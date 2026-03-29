#!/usr/bin/env python3
"""Generate cross-phase markdown report from PMC benchmark results.

Ranks all GC phases across all benchmarks by:
1. Worst IPC (p50)
2. Worst cache behaviour (LLC misses/kinst)
3. Memory boundedness (estimated stall %)
4. IPC variability (spread = p95/p5)
5. Saveable cycles (headroom if every invocation hit p95 IPC)
6. Application-level spans summary
"""

import json, sys, os
from collections import defaultdict
from datetime import datetime

# Approximate stall costs for Intel Alder Lake P-core (cycles)
L2_MISS_COST = 12
LLC_MISS_COST = 65
DTLB_MISS_COST = 20

TOP_N = 25  # rows per table


def load_spans(result_dir):
    spans = []
    for f in sorted(os.listdir(result_dir)):
        if not f.endswith('.jsonl'):
            continue
        bench = f.rsplit('_iter', 1)[0]
        with open(os.path.join(result_dir, f)) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                s = json.loads(line)
                s['benchmark'] = bench
                spans.append(s)
    return spans


def pct(vals, p):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return 0
    return vals[min(int(n * p), n - 1)]


def fmt_cy(v):
    if v >= 1e9:
        return f'{v / 1e9:.1f}B'
    if v >= 1e6:
        return f'{v / 1e6:.0f}M'
    return f'{v / 1e3:.0f}K'


def compute_rows(spans):
    """Aggregate spans per (benchmark, phase) and compute all metrics."""
    key_data = defaultdict(lambda: {
        'ipcs': [], 'cycles': [], 'instructions': [],
        'l2_misses': [], 'llc_misses': [], 'dtlb_ld': [], 'dtlb_st': [],
    })

    for s in spans:
        if s.get('cycles', 0) <= 0:
            continue
        k = (s['benchmark'], s['phase'])
        d = key_data[k]
        if s.get('ipc'):
            d['ipcs'].append(s['ipc'])
        d['cycles'].append(s['cycles'])
        d['instructions'].append(s.get('instructions', 0))
        d['l2_misses'].append(s.get('l2_misses', 0))
        d['llc_misses'].append(s.get('llc_misses', 0))
        d['dtlb_ld'].append(s.get('dtlb_load_misses', 0))
        d['dtlb_st'].append(s.get('dtlb_store_misses', 0))

    rows = []
    for (bench, phase), d in key_data.items():
        n = len(d['cycles'])
        total_cycles = sum(d['cycles'])
        total_instr = sum(d['instructions'])
        total_l2 = sum(d['l2_misses'])
        total_llc = sum(d['llc_misses'])
        total_dtlb = sum(d['dtlb_ld']) + sum(d['dtlb_st'])

        ipcs = d['ipcs']
        ipc_p5 = pct(ipcs, 0.05) if ipcs else 0
        ipc_p50 = pct(ipcs, 0.50) if ipcs else 0
        ipc_p95 = pct(ipcs, 0.95) if ipcs else 0
        spread = ipc_p95 / ipc_p5 if ipc_p5 > 0 else 0

        l2_stall = total_l2 * L2_MISS_COST
        llc_stall = total_llc * LLC_MISS_COST
        dtlb_stall = total_dtlb * DTLB_MISS_COST
        total_stall = l2_stall + llc_stall + dtlb_stall
        mem_bound_pct = total_stall / total_cycles * 100 if total_cycles else 0

        kinst = total_instr / 1000 if total_instr else 1
        l2_ki = total_l2 / kinst
        llc_ki = total_llc / kinst
        dtlb_ki = total_dtlb / kinst

        save_pct = 0
        saveable = 0
        if ipc_p95 > 0 and total_instr > 0:
            ideal = total_instr / ipc_p95
            saveable = max(0, total_cycles - ideal)
            save_pct = saveable / total_cycles * 100

        rows.append({
            'bench': bench.replace('_bench', ''), 'phase': phase, 'n': n,
            'total_cycles': total_cycles, 'total_instr': total_instr,
            'ipc_p5': ipc_p5, 'ipc_p50': ipc_p50, 'ipc_p95': ipc_p95,
            'spread': spread, 'mem_bound_pct': mem_bound_pct,
            'l2_ki': l2_ki, 'llc_ki': llc_ki, 'dtlb_ki': dtlb_ki,
            'save_pct': save_pct, 'saveable': saveable,
        })

    return rows


def generate_report(result_dir):
    spans = load_spans(result_dir)
    rows = compute_rows(spans)

    gc_rows = [r for r in rows if not r['phase'].startswith('bench:')]
    app_rows = [r for r in rows if r['phase'].startswith('bench:')]

    dirname = os.path.basename(result_dir.rstrip('/'))
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    out = []
    out.append('# Cross-Phase Performance Analysis')
    out.append('')
    out.append(f'Data: {len(spans):,} spans across '
               f'{len(set(s["benchmark"] for s in spans))} benchmarks.  ')
    out.append(f'Source: `{dirname}`  ')
    out.append(f'Generated: {ts}')
    out.append('')

    # --- 1. Worst IPC ---
    out.append('## 1. GC Phases by Worst IPC (p50)')
    out.append('')
    out.append('Sorted by lowest IPC p50. Phases with low IPC are spending '
               'most cycles stalled.')
    out.append('')
    out.append('| Benchmark | Phase | N | Cycles | IPC p5 | IPC p50 '
               '| IPC p95 | Spread | % Mem Bound |')
    out.append('|-----------|-------|--:|-------:|-------:|--------:'
               '|--------:|-------:|------------:|')
    for r in sorted(gc_rows, key=lambda r: r['ipc_p50'])[:TOP_N]:
        out.append(f'| {r["bench"]} | {r["phase"]} | {r["n"]} '
                   f'| {fmt_cy(r["total_cycles"])} | {r["ipc_p5"]:.2f} '
                   f'| {r["ipc_p50"]:.2f} | {r["ipc_p95"]:.2f} '
                   f'| {r["spread"]:.1f}x | {r["mem_bound_pct"]:.0f}% |')

    # --- 2. Worst Cache ---
    out.append('')
    out.append('## 2. GC Phases by Worst Cache Behaviour (LLC misses/kinst)')
    out.append('')
    out.append('Sorted by highest LLC miss rate per 1000 instructions.')
    out.append('')
    out.append('| Benchmark | Phase | N | Cycles | L2/ki | LLC/ki '
               '| dTLB/ki | IPC p50 | % Mem Bound |')
    out.append('|-----------|-------|--:|-------:|------:|-------:'
               '|--------:|--------:|------------:|')
    for r in sorted(gc_rows, key=lambda r: r['llc_ki'], reverse=True)[:TOP_N]:
        out.append(f'| {r["bench"]} | {r["phase"]} | {r["n"]} '
                   f'| {fmt_cy(r["total_cycles"])} | {r["l2_ki"]:.1f} '
                   f'| {r["llc_ki"]:.1f} | {r["dtlb_ki"]:.2f} '
                   f'| {r["ipc_p50"]:.2f} | {r["mem_bound_pct"]:.0f}% |')

    # --- 3. Memory Boundedness ---
    out.append('')
    out.append('## 3. GC Phases by Memory Boundedness')
    out.append('')
    out.append('Estimated % of cycles stalled on memory '
               f'(L2 miss ~{L2_MISS_COST}cy, LLC miss ~{LLC_MISS_COST}cy, '
               f'dTLB miss ~{DTLB_MISS_COST}cy).')
    out.append('')
    out.append('| Benchmark | Phase | N | Cycles | % Mem Bound | L2/ki '
               '| LLC/ki | dTLB/ki | IPC p50 |')
    out.append('|-----------|-------|--:|-------:|------------:|------:'
               '|-------:|--------:|--------:|')
    for r in sorted(gc_rows, key=lambda r: r['mem_bound_pct'],
                    reverse=True)[:TOP_N]:
        out.append(f'| {r["bench"]} | {r["phase"]} | {r["n"]} '
                   f'| {fmt_cy(r["total_cycles"])} '
                   f'| {r["mem_bound_pct"]:.0f}% | {r["l2_ki"]:.1f} '
                   f'| {r["llc_ki"]:.1f} | {r["dtlb_ki"]:.2f} '
                   f'| {r["ipc_p50"]:.2f} |')

    # --- 4. IPC Variability ---
    out.append('')
    out.append('## 4. GC Phases by IPC Variability (Spread)')
    out.append('')
    out.append('Spread = IPC p95 / IPC p5. High spread means some invocations '
               'are fast and some are stalled \u2014')
    out.append('cache-aware optimisations (layout, prefetching) pay off most '
               'here.')
    out.append('')
    out.append('| Benchmark | Phase | N | Cycles | IPC p5 | IPC p50 '
               '| IPC p95 | Spread | % Mem Bound |')
    out.append('|-----------|-------|--:|-------:|-------:|--------:'
               '|--------:|-------:|------------:|')
    for r in sorted(gc_rows, key=lambda r: r['spread'],
                    reverse=True)[:TOP_N]:
        out.append(f'| {r["bench"]} | {r["phase"]} | {r["n"]} '
                   f'| {fmt_cy(r["total_cycles"])} | {r["ipc_p5"]:.2f} '
                   f'| {r["ipc_p50"]:.2f} | {r["ipc_p95"]:.2f} '
                   f'| {r["spread"]:.1f}x | {r["mem_bound_pct"]:.0f}% |')

    # --- 5. Saveable Cycles ---
    out.append('')
    out.append('## 5. GC Phases by Saveable Cycles')
    out.append('')
    out.append("If every invocation ran at the phase's own p95 IPC, "
               "how much would we save?")
    out.append('')
    out.append('| Benchmark | Phase | N | Total Cycles | Saveable '
               '| Save % | IPC p50 | IPC p95 |')
    out.append('|-----------|-------|--:|-------------:|---------:'
               '|-------:|--------:|--------:|')
    for r in sorted(gc_rows, key=lambda r: r['saveable'],
                    reverse=True)[:TOP_N]:
        out.append(f'| {r["bench"]} | {r["phase"]} | {r["n"]} '
                   f'| {fmt_cy(r["total_cycles"])} '
                   f'| {fmt_cy(r["saveable"])} | {r["save_pct"]:.0f}% '
                   f'| {r["ipc_p50"]:.2f} | {r["ipc_p95"]:.2f} |')

    # --- 6. Application Spans ---
    out.append('')
    out.append('## 6. Application-Level Spans')
    out.append('')
    out.append('Custom user spans instrumented in the benchmarks.')
    out.append('')
    out.append('| Benchmark | Span | IPC p50 | L2/ki | LLC/ki '
               '| dTLB/ki | % Mem Bound |')
    out.append('|-----------|------|--------:|------:|-------:'
               '|--------:|------------:|')
    for r in sorted(app_rows, key=lambda r: (r['bench'], r['phase'])):
        out.append(f'| {r["bench"]} | {r["phase"]} | {r["ipc_p50"]:.2f} '
                   f'| {r["l2_ki"]:.2f} | {r["llc_ki"]:.3f} '
                   f'| {r["dtlb_ki"]:.4f} | {r["mem_bound_pct"]:.0f}% |')

    return '\n'.join(out)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results-dir> [output.md]",
              file=sys.stderr)
        sys.exit(1)
    result_dir = sys.argv[1]
    out_path = (sys.argv[2] if len(sys.argv) > 2
                else os.path.join(result_dir, 'cross_phase_analysis.md'))
    report = generate_report(result_dir)
    with open(out_path, 'w') as f:
        f.write(report)
        f.write('\n')
    print(f"Report written to {out_path}")
