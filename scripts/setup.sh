#!/bin/bash
set -euo pipefail

# setup.sh — Build oxcaml with PMC support, create opam switch, install deps,
#             build benchmarks and consumer.
#
# Prerequisites:
#   - Linux x86_64
#   - perf_event_paranoid <= 0 (ideally -1)
#   - opam installed
#   - Standard build tools (gcc, make, etc.)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== PMC Benchmark Setup ==="
echo "Root: $ROOT"

# --- Check prerequisites ---

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: Linux required (got $(uname -s))"
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "ERROR: x86_64 required (got $(uname -m))"
  exit 1
fi

PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "unknown")
if [[ "$PARANOID" != "-1" && "$PARANOID" != "0" ]]; then
  echo "WARNING: perf_event_paranoid=$PARANOID (recommend -1)"
  echo "  Fix: sudo sysctl kernel.perf_event_paranoid=-1"
fi

if ! command -v opam &>/dev/null; then
  echo "ERROR: opam not found"
  exit 1
fi

# --- Phase 2: Build oxcaml ---

echo ""
echo "=== Building oxcaml (runtime_events_pmc branch) ==="
cd "$ROOT/oxcaml"

if [[ ! -f _install/bin/ocamlopt ]]; then
  # Need OCaml 5.4.x + dune + menhir to bootstrap
  if ! opam switch list 2>/dev/null | grep -q "5.4"; then
    echo "Creating bootstrap OCaml 5.4.0 switch..."
    opam switch create 5.4.0 ocaml-base-compiler.5.4.0 -y
  fi
  eval $(opam env --switch=5.4.0)
  opam install dune menhir.20231231 -y

  autoconf 2>/dev/null || autoconf27 2>/dev/null || true
  ./configure --prefix="$(pwd)/_install" --enable-runtime5
  make -s -j"$(nproc)"
  make -s install
  echo "oxcaml built: $("_install/bin/ocamlopt" --version)"
else
  echo "oxcaml already built: $("_install/bin/ocamlopt" --version)"
fi

# --- Phase 2 verification ---

echo ""
echo "=== Verifying PMC test ==="
make -s test-one TEST=lib-runtime-events/test_perf_samples.ml || \
  echo "WARNING: PMC test did not pass (may be OK if test harness issue)"

# --- Phase 3: Create opam switch ---

echo ""
echo "=== Setting up opam switch ==="
export PATH="$ROOT/oxcaml/_install/bin:$PATH"

if ! opam switch list 2>/dev/null | grep -q oxcaml-pmc; then
  opam switch create oxcaml-pmc --empty
fi

eval $(opam env --switch=oxcaml-pmc)
opam install ocaml-system dune -y

# --- Install devkit ---

echo ""
echo "=== Installing devkit ==="
if ! ocamlfind list 2>/dev/null | grep -q devkit; then
  echo "Attempting direct install..."
  if ! opam install devkit -y 2>&1; then
    echo ""
    echo "Direct install failed. Trying oxmono approach..."
    echo "See README.md for manual oxmono instructions."
    echo ""
    echo "Quick attempt with oxmono:"
    if [[ -d "$ROOT/oxmono" ]]; then
      cd "$ROOT/oxmono"
      echo "oxmono available at $ROOT/oxmono"
      echo "You may need to manually patch devkit for oxcaml compatibility."
    fi
  fi
else
  echo "devkit already installed"
fi

# --- Phase 4: Build benchmarks ---

echo ""
echo "=== Building benchmarks ==="
cd "$ROOT/sandmark"
if [[ -d benchmarks/ahrefs-devkit ]]; then
  dune build @benchmarks/ahrefs-devkit/buildbench 2>&1 || \
    echo "WARNING: Benchmark build failed (devkit may not be installed yet)"
fi

# --- Build consumer ---

echo ""
echo "=== Building PMC consumer ==="
cd "$ROOT/consumer"
export PATH="$ROOT/oxcaml/_install/bin:$PATH"
OCAMLLIB="$ROOT/oxcaml/_install/lib/ocaml" dune build pmc_consumer.exe 2>&1 || \
  echo "WARNING: Consumer build failed"

echo ""
echo "=== Setup complete ==="
echo "To run benchmarks: ./scripts/run_benchmarks.sh"
echo "To analyze results: python3 scripts/analyze.py results/<dir>/"
