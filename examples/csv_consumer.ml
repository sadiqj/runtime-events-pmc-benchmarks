(*
   Simple CSV consumer — attaches to an OCaml process via runtime_events,
   computes per-span counter deltas, and emits CSV to stdout.

   Counter columns are named by their hex config codes (e.g. 0x00c0)
   so this works with arbitrary perf counters, not just a fixed set.

   Usage: csv_consumer <events_dir> <pid>

   Runs until Ctrl-C or the target process exits.
*)

open Runtime_events

external[@layout_poly] array_length :
  ('a : any mod separable). local_ 'a array -> int = "%array_length"
external[@layout_poly] array_get :
  ('a : any mod separable). local_ 'a array -> int -> 'a = "%array_safe_get"

(* Extract (config, value) pairs from a perf_sample array *)
let samples_to_list (samples : perf_sample array) : (int64 * int64) list =
  let n = array_length samples in
  let acc = ref [] in
  for i = n - 1 downto 0 do
    let sample = array_get samples i in
    let config = Stdlib_upstream_compatible.Int64_u.to_int64 sample.#config in
    let value = Stdlib_upstream_compatible.Int64_u.to_int64 sample.#value in
    acc := (config, value) :: !acc
  done;
  !acc

type span_begin = {
  ts: int64;
  counters: (int64 * int64) list;  (* (config, value) pairs *)
}

let active_spans : (int * runtime_phase, span_begin) Hashtbl.t =
  Hashtbl.create 64

let active_user_spans : (int * string, span_begin) Hashtbl.t =
  Hashtbl.create 64

(* Discover counter config codes from the first samples we see.
   Once set, this determines the CSV column order. *)
let counter_configs : int64 list ref = ref []
let header_printed = ref false

let discover_configs (samples : perf_sample array) =
  if !counter_configs = [] && array_length samples > 0 then begin
    let n = array_length samples in
    let configs = ref [] in
    for i = n - 1 downto 0 do
      let sample = array_get samples i in
      let config = Stdlib_upstream_compatible.Int64_u.to_int64 sample.#config in
      configs := config :: !configs
    done;
    counter_configs := !configs
  end

let print_header () =
  if not !header_printed then begin
    Printf.printf "phase,domain,start_ns,end_ns,duration_ns";
    List.iter (fun c -> Printf.printf ",0x%Lx" c) !counter_configs;
    Printf.printf "\n";
    flush stdout;
    header_printed := true
  end

let find_value config (pairs : (int64 * int64) list) : int64 =
  match List.assoc_opt config pairs with
  | Some v -> v
  | None -> 0L

let make_span_begin ts (samples : perf_sample array) =
  discover_configs samples;
  { ts = Timestamp.to_int64 ts; counters = samples_to_list samples }

let emit_csv phase_name domain_id ts begin_state
    (samples : perf_sample array) =
  print_header ();
  let end_ts = Timestamp.to_int64 ts in
  let end_counters = samples_to_list samples in
  Printf.printf "%s,%d,%Ld,%Ld,%Ld"
    phase_name domain_id
    begin_state.ts end_ts
    (Int64.sub end_ts begin_state.ts);
  List.iter (fun config ->
    let delta = Int64.sub
      (find_value config end_counters)
      (find_value config begin_state.counters)
    in
    Printf.printf ",%Ld" delta
  ) !counter_configs;
  Printf.printf "\n";
  flush stdout

let runtime_begin domain_id ts phase (local_ samples : perf_sample array) =
  Hashtbl.replace active_spans (domain_id, phase)
    (make_span_begin ts samples)

let runtime_end domain_id ts phase (local_ samples : perf_sample array) =
  match Hashtbl.find_opt active_spans (domain_id, phase) with
    | None -> ()
    | Some begin_state ->
      Hashtbl.remove active_spans (domain_id, phase);
      emit_csv (runtime_phase_name phase) domain_id ts begin_state samples

let user_span domain_id ts (event : Type.span User.t) (span : Type.span)
    (local_ samples : perf_sample array) =
  let event_name = User.name event in
  match span with
  | Begin ->
    Hashtbl.replace active_user_spans (domain_id, event_name)
      (make_span_begin ts samples)
  | End ->
    match Hashtbl.find_opt active_user_spans (domain_id, event_name) with
    | None -> ()
    | Some begin_state ->
      Hashtbl.remove active_user_spans (domain_id, event_name);
      emit_csv event_name domain_id ts begin_state samples

let () =
  if Array.length Sys.argv < 3 then begin
    Printf.eprintf "Usage: %s <events_dir> <pid>\n" Sys.argv.(0);
    exit 1
  end;
  let path = Sys.argv.(1) in
  let pid = int_of_string Sys.argv.(2) in
  let cursor = create_cursor (Some (path, pid)) in
  let callbacks =
    Callbacks.create ~runtime_begin ~runtime_end ()
    |> Callbacks.add_user_event Type.span user_span
  in
  (* Handle Ctrl-C gracefully *)
  let running = Atomic.make true in
  Sys.Safe.set_signal Sys.sigint (Sys.Signal_handle (fun _ ->
    Atomic.set running false));
  while Atomic.get running do
    let _n = read_poll cursor callbacks (Some 1024) in
    (* Check if process is still alive *)
    begin try
      Unix.kill pid 0
    with Unix.Unix_error (Unix.ESRCH, _, _) ->
      ignore (read_poll cursor callbacks None);
      Atomic.set running false
    end;
    if Atomic.get running then
      Unix.sleepf 0.001
  done;
  (* Print header even if no counters were seen (no PMC runtime) *)
  print_header ();
  free_cursor cursor
