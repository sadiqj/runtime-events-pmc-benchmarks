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
# Usage: ./scripts/run_overhead.sh [iterations] [cpu] [rounds]

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ITERATIONS=${1:-20}
PIN_CPU=${2:-0}
ROUNDS=${3:-3}

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

# Verify executables
for bench in "${BENCHMARKS[@]}"; do
  if [[ ! -f "$BENCH_DIR/${bench}.exe" ]]; then
    echo "ERROR: $BENCH_DIR/${bench}.exe not found. Run scripts/setup.sh first."
    exit 1
  fi
done

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
