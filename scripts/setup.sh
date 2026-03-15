#!/bin/bash
set -euo pipefail

# setup.sh — Set up oxcaml with PMC support and build benchmarks.
#
# Prerequisites:
#   - Linux x86_64, perf_event_paranoid <= 0
#   - opam, gcc, make, autoconf, zlib-dev, libpcre-dev

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Prerequisites ---

[[ "$(uname -s)" == "Linux" ]] || { echo "ERROR: Linux required"; exit 1; }
[[ "$(uname -m)" == "x86_64" ]] || { echo "ERROR: x86_64 required"; exit 1; }
command -v opam &>/dev/null || { echo "ERROR: opam not found"; exit 1; }

PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "unknown")
[[ "$PARANOID" == "-1" || "$PARANOID" == "0" ]] || \
  echo "WARNING: perf_event_paranoid=$PARANOID (recommend: sudo sysctl kernel.perf_event_paranoid=-1)"

# --- Create oxcaml switch with PMC support ---

if ! opam switch list 2>/dev/null | grep -q oxcaml-pmc; then
  echo "Creating oxcaml-pmc switch (~15 min)..."
  opam update --all
  opam switch create oxcaml-pmc \
    --repos ox=git+https://github.com/oxcaml/opam-repository.git,default \
    ocaml-variants.5.2.0+ox
fi
eval $(opam env --switch=oxcaml-pmc)

if ! opam pin list 2>/dev/null | grep -q runtime_events_pmc; then
  echo "Pinning to runtime_events_pmc for perf counter support..."
  opam pin ocaml-variants \
    git+https://github.com/sadiqj/oxcaml.git#runtime_events_pmc_rebased --yes
  eval $(opam env --switch=oxcaml-pmc)
fi

command -v dune &>/dev/null || opam install dune --yes

echo "Switch: ocamlopt $(ocamlopt -version), dune $(dune --version)"

# --- Build ---

echo "Building benchmarks..."
cd "$ROOT/oxmono"
dune build @benchmarks/ahrefs-devkit/buildbench

echo "Building consumer..."
cd "$ROOT/consumer"
dune build pmc_consumer.exe

echo ""
echo "Setup complete. Run: ./scripts/run_benchmarks.sh"
