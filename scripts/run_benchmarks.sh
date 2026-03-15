#!/bin/bash
set -euo pipefail

# run_benchmarks.sh — Run ahrefs-devkit benchmarks with PMC collection.
#
# Pins to P-core CPU 0 for consistent PMC on Alder Lake hybrid CPUs.
# Collects 6 counters in a single batch (well within P-core limit of ~8).
#
# Usage: ./scripts/run_benchmarks.sh [iterations] [cpu]
#   iterations: number of runs per benchmark (default: 3)
#   cpu: CPU core to pin to (default: 0, should be a P-core)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

BENCHMARKS=(htmlStream_bench stre_bench network_bench gzip_bench)
COUNTERS="r00c0,r003c,r3f24,r412e,r11d0,r12d0"
ITERATIONS=${1:-3}
PIN_CPU=${2:-0}

RESULTS_DIR="$ROOT/results/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULTS_DIR"

BENCH_DIR="$ROOT/oxmono/_build/default/benchmarks/ahrefs-devkit"
CONSUMER="$ROOT/consumer/_build/default/pmc_consumer.exe"

# Verify executables exist
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

echo "=== PMC Benchmark Runner ==="
echo "Iterations: $ITERATIONS"
echo "Pinned to CPU: $PIN_CPU"
echo "Counters: $COUNTERS"
echo "Results: $RESULTS_DIR"
echo ""

for bench in "${BENCHMARKS[@]}"; do
  for iter in $(seq 1 "$ITERATIONS"); do
    OUTFILE="$RESULTS_DIR/${bench}_iter${iter}.jsonl"
    echo "Running $bench (iteration $iter/$ITERATIONS)..."

    # Clean up old events files
    rm -f "$RESULTS_DIR"/*.events

    # Start benchmark in background with runtime events + PMC enabled
    OCAML_RUNTIME_EVENTS_START=1 \
    OCAML_RUNTIME_EVENTS_PRESERVE=1 \
    OCAML_RUNTIME_EVENTS_DIR="$RESULTS_DIR" \
    OCAML_RUNTIME_EVENTS_PERF_COUNTERS="$COUNTERS" \
    taskset -c "$PIN_CPU" \
      "$BENCH_DIR/${bench}.exe" &
    BENCH_PID=$!

    # Wait for events file to appear
    EVENTS_FILE=""
    for _i in $(seq 1 50); do
      for f in "$RESULTS_DIR"/*.events; do
        if [[ -f "$f" ]]; then EVENTS_FILE="$f"; break; fi
      done
      if [[ -n "$EVENTS_FILE" ]]; then break; fi
      sleep 0.05
    done
    ACTUAL_PID=$(basename "${EVENTS_FILE:-.events}" .events)

    # Attach consumer in background to collect PMC data
    "$CONSUMER" "$RESULTS_DIR" "$ACTUAL_PID" > "$OUTFILE" 2>/dev/null &
    CONSUMER_PID=$!

    # Wait for benchmark to finish
    wait "$BENCH_PID" 2>/dev/null || true

    # Give consumer a moment to drain final events, then wait for it
    sleep 0.5
    wait "$CONSUMER_PID" 2>/dev/null || true

    # Clean up events file
    rm -f "$RESULTS_DIR"/*.events

    LINES=$(wc -l < "$OUTFILE" 2>/dev/null || echo 0)
    echo "  -> $LINES spans collected"
  done
done

echo ""
echo "=== Done ==="
echo "Results in: $RESULTS_DIR"
echo "Analyze with: python3 scripts/analyze.py $RESULTS_DIR/"
