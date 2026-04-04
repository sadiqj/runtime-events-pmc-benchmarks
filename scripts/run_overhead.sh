#!/bin/bash
set -euo pipefail

# run_overhead.sh — Measure overhead of runtime events and PMC collection.
#
# Compares 3 modes:
#   baseline  — no runtime events
#   events    — runtime events enabled, no PMC
#   events+pmc — runtime events + PMC counters
#
# Interleaved execution to avoid thermal drift.
#
# Always pins to CPU 0 (a P-core on hybrid CPUs; valid on any CPU layout).
#
# Usage: ./scripts/run_overhead.sh [iterations] [rounds]

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ITERATIONS=${1:-20}
PIN_CPU=0
ROUNDS=${2:-3}

COUNTERS="r00c0,r003c,r3f24,r412e,r11d0,r12d0"
BENCHMARKS=(htmlStream_bench stre_bench network_bench gzip_bench)
BENCH_DIR="$ROOT/oxmono/_build/default/benchmarks/ahrefs-devkit"

# Per-benchmark rounds override: stre is very short (~0.17s/round),
# so we give it more rounds to reach a duration where timing noise
# is small relative to the measurement.
declare -A BENCH_ROUNDS_OVERRIDE
BENCH_ROUNDS_OVERRIDE=(
  [htmlStream_bench]=$ROUNDS
  [stre_bench]=30
  [network_bench]=$ROUNDS
  [gzip_bench]=$ROUNDS
)

get_rounds() {
  local bench=$1
  echo "${BENCH_ROUNDS_OVERRIDE[$bench]:-$ROUNDS}"
}

RESULTS_DIR="$ROOT/results/overhead_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULTS_DIR"

CONSUMER="$ROOT/consumer/_build/default/pmc_consumer.exe"

# Verify executables
for bench in "${BENCHMARKS[@]}"; do
  if [[ ! -f "$BENCH_DIR/${bench}.exe" ]]; then
    echo "ERROR: $BENCH_DIR/${bench}.exe not found. Run scripts/setup.sh first."
    exit 1
  fi
done

if [[ ! -f "$CONSUMER" ]]; then
  echo "ERROR: Consumer not found at $CONSUMER. Run scripts/setup.sh first."
  exit 1
fi

echo "=== Overhead Measurement ==="
echo "Iterations: $ITERATIONS"
echo "Pinned to CPU: $PIN_CPU"
echo "BENCH_ROUNDS (default): $ROUNDS"
for bench in "${BENCHMARKS[@]}"; do
  echo "  ${bench}: $(get_rounds "$bench") rounds"
done
echo "Results: $RESULTS_DIR"
echo ""

# Write metadata
cat > "$RESULTS_DIR/metadata.json" <<METAEOF
{
  "iterations": $ITERATIONS,
  "cpu": $PIN_CPU,
  "rounds_default": $ROUNDS,
  "rounds_per_bench": {
    "htmlStream_bench": $(get_rounds htmlStream_bench),
    "stre_bench": $(get_rounds stre_bench),
    "network_bench": $(get_rounds network_bench),
    "gzip_bench": $(get_rounds gzip_bench)
  },
  "date": "$(date -Iseconds)",
  "benchmarks": ["htmlStream_bench", "stre_bench", "network_bench", "gzip_bench"],
  "modes": ["baseline", "events", "pmc"],
  "counters": "$COUNTERS"
}
METAEOF

run_baseline() {
  local bench=$1
  local r; r=$(get_rounds "$bench")
  BENCH_ROUNDS=$r taskset -c "$PIN_CPU" \
    /usr/bin/time -f '%e' "$BENCH_DIR/${bench}.exe" 2>&1 >/dev/null | tail -1
}

run_events() {
  local bench=$1
  local r; r=$(get_rounds "$bench")
  local tmpdir
  tmpdir=$(mktemp -d)
  BENCH_ROUNDS=$r \
  OCAML_RUNTIME_EVENTS_START=1 \
  OCAML_RUNTIME_EVENTS_PRESERVE=1 \
  OCAML_RUNTIME_EVENTS_DIR="$tmpdir" \
  taskset -c "$PIN_CPU" \
    /usr/bin/time -f '%e' "$BENCH_DIR/${bench}.exe" 2>&1 >/dev/null | tail -1
  rm -rf "$tmpdir"
}

run_pmc() {
  local bench=$1
  local r; r=$(get_rounds "$bench")
  local tmpdir
  tmpdir=$(mktemp -d)
  BENCH_ROUNDS=$r \
  OCAML_RUNTIME_EVENTS_START=1 \
  OCAML_RUNTIME_EVENTS_PRESERVE=1 \
  OCAML_RUNTIME_EVENTS_DIR="$tmpdir" \
  OCAML_RUNTIME_EVENTS_PERF_COUNTERS="$COUNTERS" \
  taskset -c "$PIN_CPU" \
    /usr/bin/time -f '%e' "$BENCH_DIR/${bench}.exe" 2>&1 >/dev/null | tail -1
  rm -rf "$tmpdir"
}

verify_modes() {
  local vbench="stre_bench"
  local vdir
  vdir=$(mktemp -d)

  echo "=== Pre-flight verification ==="

  # Verify EVENTS mode: .events file is created and non-empty
  BENCH_ROUNDS=1 \
  OCAML_RUNTIME_EVENTS_START=1 \
  OCAML_RUNTIME_EVENTS_PRESERVE=1 \
  OCAML_RUNTIME_EVENTS_DIR="$vdir" \
  taskset -c "$PIN_CPU" \
    "$BENCH_DIR/${vbench}.exe" >/dev/null 2>&1

  local events_file=""
  for f in "$vdir"/*.events; do
    [[ -f "$f" ]] && events_file="$f" && break
  done
  local events_file_size=0
  if [[ -n "$events_file" ]]; then
    events_file_size=$(stat -c '%s' "$events_file")
  fi

  if [[ -z "$events_file" ]] || [[ "$events_file_size" -eq 0 ]]; then
    echo "  events mode: FAIL — no .events file in $vdir (runtime events not enabled)"
    rm -rf "$vdir"
    exit 1
  fi
  echo "  events mode: OK (events file ${events_file_size} bytes)"
  rm -f "$vdir"/*.events

  # Verify PMC mode: consumer captures spans with non-zero counter values
  BENCH_ROUNDS=1 \
  OCAML_RUNTIME_EVENTS_START=1 \
  OCAML_RUNTIME_EVENTS_PRESERVE=1 \
  OCAML_RUNTIME_EVENTS_DIR="$vdir" \
  OCAML_RUNTIME_EVENTS_PERF_COUNTERS="$COUNTERS" \
  taskset -c "$PIN_CPU" \
    "$BENCH_DIR/${vbench}.exe" &
  local vbench_pid=$!

  # Poll for events file
  local actual_pid=""
  for _i in $(seq 1 50); do
    for f in "$vdir"/*.events; do
      if [[ -f "$f" ]]; then
        actual_pid=$(basename "$f" .events)
        break
      fi
    done
    [[ -n "$actual_pid" ]] && break
    sleep 0.05
  done

  if [[ -z "$actual_pid" ]]; then
    echo "  pmc mode: FAIL — no .events file appeared"
    kill "$vbench_pid" 2>/dev/null || true
    rm -rf "$vdir"
    exit 1
  fi

  # Attach consumer, wait for bench
  "$CONSUMER" "$vdir" "$actual_pid" > "$vdir/capture.jsonl" 2>/dev/null &
  local consumer_pid=$!
  wait "$vbench_pid" 2>/dev/null || true
  sleep 0.3
  wait "$consumer_pid" 2>/dev/null || true

  local total_lines nonzero_lines
  total_lines=$(wc -l < "$vdir/capture.jsonl" 2>/dev/null || echo 0)
  nonzero_lines=$(grep -oE '"instructions":[0-9]+' "$vdir/capture.jsonl" 2>/dev/null \
                  | awk -F: '$2 > 0' | wc -l)

  if [[ "$total_lines" -eq 0 ]]; then
    echo "  pmc mode: FAIL — consumer captured 0 spans"
    rm -rf "$vdir"
    exit 1
  fi
  if [[ "$nonzero_lines" -eq 0 ]]; then
    echo "  pmc mode: FAIL — $total_lines spans collected but all have instructions=0"
    echo ""
    echo "  PMC counters are NOT being captured. Likely causes:"
    echo "    - /proc/sys/kernel/perf_event_paranoid too restrictive (needs -1)."
    echo "    - Requested counters ($COUNTERS) exceed available hardware PMC slots."
    echo "    - On hybrid CPUs, CPU 0 must be a P-core (check /sys/devices/cpu_core/cpus)."
    rm -rf "$vdir"
    exit 1
  fi
  echo "  pmc mode: OK ($nonzero_lines/$total_lines spans with counters)"
  echo ""

  # Save verification metadata
  cat > "$RESULTS_DIR/verification.json" <<VEOF
{
  "verified_at": "$(date -Iseconds)",
  "pin_cpu": $PIN_CPU,
  "events_file_size": $events_file_size,
  "pmc_consumer_lines": $total_lines,
  "pmc_samples_with_counters": $nonzero_lines,
  "counters": "$COUNTERS"
}
VEOF

  rm -rf "$vdir"
}

verify_modes

for iter in $(seq 1 "$ITERATIONS"); do
  echo "--- Iteration $iter/$ITERATIONS ---"
  for bench in "${BENCHMARKS[@]}"; do
    short="${bench/_bench/}"

    # baseline
    t=$(run_baseline "$bench")
    echo "$t" >> "$RESULTS_DIR/${bench}_baseline.times"
    echo "  $short baseline: ${t}s"

    # events only
    t=$(run_events "$bench")
    echo "$t" >> "$RESULTS_DIR/${bench}_events.times"
    echo "  $short events:   ${t}s"

    # events + pmc
    t=$(run_pmc "$bench")
    echo "$t" >> "$RESULTS_DIR/${bench}_pmc.times"
    echo "  $short pmc:      ${t}s"
  done
done

echo ""
echo "=== Done ==="
echo "Results in: $RESULTS_DIR"
echo "Analyze with: python3 scripts/report_overhead.py $RESULTS_DIR/"
