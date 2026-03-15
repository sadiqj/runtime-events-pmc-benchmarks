# PMC Benchmark Runner & Analysis

Hardware performance counter (PMC) analysis of OCaml runtime GC phases using the `runtime_events_pmc` branch of oxcaml.

Collects per-span IPC, cache misses (L2/LLC), and dTLB misses for ahrefs-devkit benchmarks from sandmark.

## Prerequisites

- Linux x86_64
- `perf_event_paranoid` set to -1: `sudo sysctl kernel.perf_event_paranoid=-1`
- opam installed
- Standard build tools (gcc, make)

## Setup

```bash
git clone --recursive <repo-url>
cd pmc
./scripts/setup.sh
```

## Usage

```bash
# Run all benchmarks (3 iterations, pinned to CPU 0)
./scripts/run_benchmarks.sh

# Custom iterations and CPU
./scripts/run_benchmarks.sh 5 2

# Analyze results
python3 scripts/analyze.py results/<dir>/
python3 scripts/analyze.py results/<dir>/ --csv --plot
```

## Counter Codes (Intel Alder Lake)

| Code | Event | Purpose |
|------|-------|---------|
| `r00c0` | INST_RETIRED.ANY | Instructions |
| `r003c` | CPU_CLK_UNHALTED.THREAD | Cycles |
| `r3f24` | L2_RQSTS.MISS | L2 cache misses |
| `r412e` | LONGEST_LAT_CACHE.MISS | LLC misses |
| `r11d0` | MEM_INST_RETIRED.STLB_MISS_LOADS | dTLB load misses |
| `r12d0` | MEM_INST_RETIRED.STLB_MISS_STORES | dTLB store misses |

## Structure

```
consumer/           PMC data collector (OCaml, attaches to benchmark process)
scripts/setup.sh    Build oxcaml, create opam switch, install deps
scripts/run_benchmarks.sh   Run benchmarks with PMC collection
scripts/analyze.py  Analyze JSONL results, produce tables & charts
oxcaml/             Submodule: sadiqj/oxcaml @ runtime_events_pmc
sandmark/           Submodule: ocaml-bench/sandmark @ pr-480
oxmono/             Submodule: avsm/oxmono (for patching packages)
```
