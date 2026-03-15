(*
   PMC Consumer — out-of-process runtime events consumer that collects
   per-span hardware performance counter data and emits JSONL.

   Usage: pmc_consumer <events_dir> <pid>

   Attaches to an external OCaml process via runtime_events, records
   perf_sample values at span begin/end, computes deltas and derived
   metrics, and writes one JSONL line per completed span to stdout.

   Expected env var on the monitored process:
     OCAML_RUNTIME_EVENTS_PERF_COUNTERS=r00c0,r003c,r3f24,r412e,r11d0,r12d0

   Counter order (by config code):
     0: r00c0 = INST_RETIRED.ANY      (instructions)
     1: r003c = CPU_CLK_UNHALTED.THREAD (cycles)
     2: r3f24 = L2_RQSTS.MISS         (L2 cache misses)
     3: r412e = LONGEST_LAT_CACHE.MISS (LLC misses)
     4: r11d0 = MEM_INST_RETIRED.STLB_MISS_LOADS  (dTLB load misses)
     5: r12d0 = MEM_INST_RETIRED.STLB_MISS_STORES (dTLB store misses)
*)

open Runtime_events

(* Unboxed array access for perf_sample arrays *)
external[@layout_poly] array_length :
  ('a : any mod separable). 'a array -> int = "%array_length"
external[@layout_poly] array_get :
  ('a : any mod separable). 'a array -> int -> 'a = "%array_safe_get"

(* Known counter config codes (as set via env var) *)
let config_instructions = 0x00c0L
let config_cycles       = 0x003cL
let config_l2_misses    = 0x3f24L
let config_llc_misses   = 0x412eL
let config_dtlb_loads   = 0x11d0L
let config_dtlb_stores  = 0x12d0L

(* Per-span begin state: (timestamp_ns, instructions, cycles, l2, llc, dtlb_ld, dtlb_st) *)
type span_begin = {
  ts: int64;
  instructions: int64;
  cycles: int64;
  l2_misses: int64;
  llc_misses: int64;
  dtlb_load_misses: int64;
  dtlb_store_misses: int64;
}

(* Key: (domain_id, phase) *)
let active_spans : (int * runtime_phase, span_begin) Hashtbl.t =
  Hashtbl.create 64

(* Extract counter value by config code from perf_sample array *)
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

let runtime_begin domain_id ts phase (samples : perf_sample array) =
  if array_length samples > 0 then begin
    let ts_ns = Timestamp.to_int64 ts in
    let entry = {
      ts = ts_ns;
      instructions = find_counter samples config_instructions;
      cycles = find_counter samples config_cycles;
      l2_misses = find_counter samples config_l2_misses;
      llc_misses = find_counter samples config_llc_misses;
      dtlb_load_misses = find_counter samples config_dtlb_loads;
      dtlb_store_misses = find_counter samples config_dtlb_stores;
    } in
    Hashtbl.replace active_spans (domain_id, phase) entry
  end

let runtime_end domain_id ts phase (samples : perf_sample array) =
  if array_length samples > 0 then begin
    match Hashtbl.find_opt active_spans (domain_id, phase) with
    | None -> ()
    | Some begin_state ->
      Hashtbl.remove active_spans (domain_id, phase);
      let end_ts = Timestamp.to_int64 ts in
      let duration_ns = Int64.sub end_ts begin_state.ts in
      let d_instr = Int64.sub (find_counter samples config_instructions)
                      begin_state.instructions in
      let d_cycles = Int64.sub (find_counter samples config_cycles)
                       begin_state.cycles in
      let d_l2 = Int64.sub (find_counter samples config_l2_misses)
                   begin_state.l2_misses in
      let d_llc = Int64.sub (find_counter samples config_llc_misses)
                    begin_state.llc_misses in
      let d_dtlb_ld = Int64.sub (find_counter samples config_dtlb_loads)
                        begin_state.dtlb_load_misses in
      let d_dtlb_st = Int64.sub (find_counter samples config_dtlb_stores)
                        begin_state.dtlb_store_misses in
      (* Derived metrics *)
      let instr_f = Int64.to_float d_instr in
      let cycles_f = Int64.to_float d_cycles in
      let ipc = if cycles_f > 0.0 then instr_f /. cycles_f else 0.0 in
      let kinstr = instr_f /. 1000.0 in
      let l2_per_ki = if kinstr > 0.0 then Int64.to_float d_l2 /. kinstr
                      else 0.0 in
      let llc_per_ki = if kinstr > 0.0 then Int64.to_float d_llc /. kinstr
                       else 0.0 in
      let dtlb_ld_per_ki = if kinstr > 0.0
                           then Int64.to_float d_dtlb_ld /. kinstr
                           else 0.0 in
      let dtlb_st_per_ki = if kinstr > 0.0
                           then Int64.to_float d_dtlb_st /. kinstr
                           else 0.0 in
      let phase_name = runtime_phase_name phase in
      Printf.printf
        "{\"phase\":\"%s\",\"domain\":%d,\"duration_ns\":%Ld,\
         \"instructions\":%Ld,\"cycles\":%Ld,\
         \"ipc\":%.4f,\
         \"l2_misses\":%Ld,\"llc_misses\":%Ld,\
         \"dtlb_load_misses\":%Ld,\"dtlb_store_misses\":%Ld,\
         \"l2_per_kinst\":%.4f,\"llc_per_kinst\":%.4f,\
         \"dtlb_ld_per_kinst\":%.4f,\"dtlb_st_per_kinst\":%.4f}\n"
        phase_name domain_id duration_ns
        d_instr d_cycles
        ipc
        d_l2 d_llc
        d_dtlb_ld d_dtlb_st
        l2_per_ki llc_per_ki
        dtlb_ld_per_ki dtlb_st_per_ki;
      flush stdout
  end

let () =
  if Array.length Sys.argv < 3 then begin
    Printf.eprintf "Usage: %s <events_dir> <pid>\n" Sys.argv.(0);
    exit 1
  end;
  let path = Sys.argv.(1) in
  let pid = int_of_string Sys.argv.(2) in
  let cursor = create_cursor (Some (path, pid)) in
  let callbacks = Callbacks.create ~runtime_begin ~runtime_end () in
  (* Poll until the target process exits *)
  let running = ref true in
  while !running do
    let _n = read_poll cursor callbacks (Some 1024) in
    (* Check if process is still alive *)
    begin try
      Unix.kill pid 0  (* signal 0 = just check existence *)
    with Unix.Unix_error (Unix.ESRCH, _, _) ->
      (* Process gone — do one final poll to drain remaining events *)
      ignore (read_poll cursor callbacks None);
      running := false
    end;
    if !running then
      Unix.sleepf 0.001  (* 1ms poll interval *)
  done;
  free_cursor cursor
