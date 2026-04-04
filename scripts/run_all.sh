#!/bin/bash
set -euo pipefail

# run_all.sh — Run benchmarks, overhead measurement, and generate all reports.
#
# Usage: ./scripts/run_all.sh [options]
#
# Options:
#   -i ITERATIONS   Benchmark iterations (default: 3)
#   -o OVERHEAD_ITERATIONS  Overhead iterations (default: 20)
#   -r ROUNDS       Internal rounds per benchmark (default: 1)
#   -d OUTPUT_DIR   Output directory (default: output/)
#   -s              Skip overhead measurement (much faster)
#   -h              Show this help
#
# All runs are pinned to CPU 0 (a P-core on hybrid CPUs, valid on any layout).

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ITERATIONS=3
OVERHEAD_ITERATIONS=20
ROUNDS=1
OUTPUT_DIR="$ROOT/output"
SKIP_OVERHEAD=false

usage() {
  sed -n '3,/^$/s/^# //p' "$0"
  exit 0
}

while getopts "i:o:r:d:sh" opt; do
  case $opt in
    i) ITERATIONS=$OPTARG ;;
    o) OVERHEAD_ITERATIONS=$OPTARG ;;
    r) ROUNDS=$OPTARG ;;
    d) OUTPUT_DIR=$OPTARG ;;
    s) SKIP_OVERHEAD=true ;;
    h) usage ;;
    *) usage ;;
  esac
done

echo "=== PMC Full Run ==="
echo "Benchmark iterations: $ITERATIONS"
echo "Overhead iterations:  $OVERHEAD_ITERATIONS (skip=$SKIP_OVERHEAD)"
echo "Pinned to CPU:        0"
echo "Output:               $OUTPUT_DIR"
echo ""

mkdir -p "$OUTPUT_DIR"

# --- Run benchmarks ---
echo ">>> Running benchmarks..."
"$ROOT/scripts/run_benchmarks.sh" "$ITERATIONS" "$ROUNDS"

# Find the latest benchmark results directory
BENCH_DIR=$(ls -dt "$ROOT/results"/20* 2>/dev/null | head -1)
if [[ -z "$BENCH_DIR" ]]; then
  echo "ERROR: No benchmark results found in results/"
  exit 1
fi
echo "Benchmark results: $BENCH_DIR"

# --- Run overhead measurement ---
OVERHEAD_DIR=""
if [[ "$SKIP_OVERHEAD" == "false" ]]; then
  echo ""
  echo ">>> Running overhead measurement..."
  "$ROOT/scripts/run_overhead.sh" "$OVERHEAD_ITERATIONS" "$ROUNDS"

  OVERHEAD_DIR=$(ls -dt "$ROOT/results"/overhead_* 2>/dev/null | head -1)
  echo "Overhead results: $OVERHEAD_DIR"
fi

# --- Generate reports ---
echo ""
echo ">>> Generating reports..."

python3 "$ROOT/scripts/report_cross_phase.py" "$BENCH_DIR" \
  "$OUTPUT_DIR/cross_phase_analysis.md"

python3 "$ROOT/scripts/report_by_phase.py" "$BENCH_DIR" \
  "$OUTPUT_DIR/report_by_phase.html"

python3 "$ROOT/scripts/report_targets.py" "$BENCH_DIR" \
  "$OUTPUT_DIR/report_targets.html"

python3 "$ROOT/scripts/report_targets_by_bench.py" "$BENCH_DIR" \
  "$OUTPUT_DIR/report_targets_by_bench.html"

if [[ -n "$OVERHEAD_DIR" ]]; then
  python3 "$ROOT/scripts/report_overhead.py" "$OVERHEAD_DIR" \
    "$OUTPUT_DIR/report_overhead.html"
fi

# --- Generate charts ---
VENV_PYTHON="$ROOT/.venv/bin/python3"
if [[ -x "$VENV_PYTHON" ]]; then
  echo ""
  echo ">>> Generating charts..."
  PLOT_ARGS=("$BENCH_DIR")
  if [[ -n "$OVERHEAD_DIR" ]]; then
    PLOT_ARGS+=("$OVERHEAD_DIR")
  else
    PLOT_ARGS+=("")
  fi
  PLOT_ARGS+=("$OUTPUT_DIR")
  "$VENV_PYTHON" "$ROOT/scripts/plot_results.py" "${PLOT_ARGS[@]}"
else
  echo ""
  echo ">>> Skipping charts (.venv not found — run: python3 -m venv .venv && .venv/bin/pip install matplotlib)"
fi

# --- Copy raw data ---
BENCH_BASENAME=$(basename "$BENCH_DIR")
cp -r "$BENCH_DIR" "$OUTPUT_DIR/benchmark_$BENCH_BASENAME"

if [[ -n "$OVERHEAD_DIR" ]]; then
  OVERHEAD_BASENAME=$(basename "$OVERHEAD_DIR")
  cp -r "$OVERHEAD_DIR" "$OUTPUT_DIR/$OVERHEAD_BASENAME"
fi

echo ""
echo "=== Done ==="
echo "Reports in: $OUTPUT_DIR"
echo ""
echo "  cross_phase_analysis.md        — All phases ranked by IPC, cache, memory boundedness, variability, saveable cycles"
echo "  report_by_phase.html           — Phase performance summary (HTML)"
echo "  report_targets.html            — Optimisation targets: saveable cycles, memory boundedness, variability (HTML)"
echo "  report_targets_by_bench.html   — Same, broken down per benchmark (HTML)"
if [[ -n "$OVERHEAD_DIR" ]]; then
  echo "  report_overhead.html           — Overhead statistical analysis (HTML)"
fi
if [[ -x "$VENV_PYTHON" ]]; then
  echo "  *.png                          — Charts (IPC box plots, memory boundedness, scatter, spread, app spans, overhead)"
fi
echo "  benchmark_$BENCH_BASENAME/     — Raw JSONL span data"
if [[ -n "$OVERHEAD_DIR" ]]; then
  echo "  $OVERHEAD_BASENAME/            — Raw overhead timing data"
fi
