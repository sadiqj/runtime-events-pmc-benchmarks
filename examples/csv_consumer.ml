(*
   Simple CSV consumer — attaches to an OCaml process via runtime_events,
   computes per-span counter deltas, and emits CSV to stdout.

   Usage: csv_consumer <events_dir> <pid>

   Runs until Ctrl-C or the target process exits.
*)

open Runtime_events

external[@layout_poly] array_length :
  ('a : any mod separable). local_ 'a array -> int = "%array_length"
external[@layout_poly] array_get :
  ('a : any mod separable). local_ 'a array -> int -> 'a = "%array_safe_get"

(* Counter config codes *)
let config_instructions = 0x00c0L
let config_cycles       = 0x003cL
let config_l2_misses    = 0x3f24L
let config_llc_misses   = 0x412eL
let config_dtlb_loads   = 0x11d0L
let config_dtlb_stores  = 0x12d0L

type span_begin = {
  ts: int64;
  instructions: int64;
  cycles: int64;
  l2_misses: int64;
  llc_misses: int64;
  dtlb_load_misses: int64;
  dtlb_store_misses: int64;
}

let active_spans : (int * runtime_phase, span_begin) Hashtbl.t =
  Hashtbl.create 64

let active_user_spans : (int * string, span_begin) Hashtbl.t =
  Hashtbl.create 64

let find_counter (samples : perf_sample array) (target_config : int64) : int64 =
  let n = array_length samples in
  let result = ref 0L in
  for i = 0 to n - 1 do
    let sample = array_get samples i in
    let config = Stdlib_upstream_compatible.Int64_u.to_int64 sample.#config in
    let value = Stdlib_upstream_compatible.Int64_u.to_int64 sample.#value in
    if Int64.equal config target_config then
      result := value
  done;
  !result

let make_span_begin ts (samples : perf_sample array) = {
  ts = Timestamp.to_int64 ts;
  instructions = find_counter samples config_instructions;
  cycles = find_counter samples config_cycles;
  l2_misses = find_counter samples config_l2_misses;
  llc_misses = find_counter samples config_llc_misses;
  dtlb_load_misses = find_counter samples config_dtlb_loads;
  dtlb_store_misses = find_counter samples config_dtlb_stores;
}

let emit_csv phase_name domain_id ts begin_state
    (samples : perf_sample array) =
  let end_ts = Timestamp.to_int64 ts in
  Printf.printf "%s,%d,%Ld,%Ld,%Ld,%Ld,%Ld,%Ld,%Ld,%Ld,%Ld\n"
    phase_name domain_id
    begin_state.ts end_ts
    (Int64.sub end_ts begin_state.ts)
    (Int64.sub (find_counter samples config_instructions) begin_state.instructions)
    (Int64.sub (find_counter samples config_cycles) begin_state.cycles)
    (Int64.sub (find_counter samples config_l2_misses) begin_state.l2_misses)
    (Int64.sub (find_counter samples config_llc_misses) begin_state.llc_misses)
    (Int64.sub (find_counter samples config_dtlb_loads) begin_state.dtlb_load_misses)
    (Int64.sub (find_counter samples config_dtlb_stores) begin_state.dtlb_store_misses);
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
  (* Print CSV header *)
  Printf.printf "phase,domain,start_ns,end_ns,duration_ns,instructions,cycles,l2_misses,llc_misses,dtlb_load_misses,dtlb_store_misses\n";
  flush stdout;
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
  free_cursor cursor
