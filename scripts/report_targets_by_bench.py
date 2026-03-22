#!/usr/bin/env python3
"""Generate per-benchmark optimization targets report from PMC results.

Shows the same 3 analysis tables as report_targets.py but broken down
per benchmark, so you can see how the same GC phase behaves differently
across workloads (e.g. major_sweep memory-bound in htmlStream but
cache-friendly in stre).

Includes a cross-benchmark comparison table at the end.
"""

import json, sys, os, html
from collections import defaultdict
from datetime import datetime

# Approximate stall costs for Intel Alder Lake P-core (cycles)
L2_MISS_COST = 12
LLC_MISS_COST = 65
DTLB_MISS_COST = 20

BENCH_COLORS = {
    'htmlStream_bench': '#4477aa',
    'network_bench': '#44aa77',
    'gzip_bench': '#aa7744',
    'stre_bench': '#aa4477',
}

def load_spans(result_dir):
    spans = []
    for f in sorted(os.listdir(result_dir)):
        if not f.endswith('.jsonl'): continue
        bench = f.rsplit('_iter', 1)[0]
        with open(os.path.join(result_dir, f)) as fh:
            for line in fh:
                line = line.strip()
                if not line: continue
                s = json.loads(line)
                s['benchmark'] = bench
                spans.append(s)
    return spans

def pct(vals, p):
    vals = sorted(vals)
    n = len(vals)
    if n == 0: return 0
    return vals[min(int(n * p), n - 1)]

def color_scale(val, lo, hi, invert=False):
    if hi == lo: return "#888"
    t = max(0, min(1, (val - lo) / (hi - lo)))
    if invert: t = 1 - t
    if t < 0.25: return "#1a7a1a"
    if t < 0.50: return "#4a9a2a"
    if t < 0.75: return "#ba6a2a"
    return "#cc3333"

def bar_html(val, max_val, color, width_px=120):
    if max_val == 0: return ""
    w = min(val / max_val, 1.0) * width_px
    return f'<div style="background:{color};width:{w:.0f}px;height:14px;display:inline-block;border-radius:2px"></div>'

def stacked_bar_html(parts, max_val, width_px=120):
    if max_val == 0: return ""
    segs = []
    for val, color, title in parts:
        w = val / max_val * width_px
        if w >= 0.5:
            segs.append(f'<div style="background:{color};width:{w:.0f}px;height:14px;display:inline-block" title="{title}"></div>')
    return ''.join(segs)

def fmt_cycles(v):
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.0f}M"
    return f"{v/1e3:.0f}K"

def compute_phase_rows(spans):
    """Aggregate spans into per-phase rows with all computed metrics.

    Takes a list of span dicts (each must have 'benchmark' key).
    Returns list of row dicts suitable for rendering tables.
    """
    phase_data = defaultdict(lambda: {
        'ipcs': [], 'cycles': [], 'instructions': [],
        'l2_misses': [], 'llc_misses': [], 'dtlb_ld': [], 'dtlb_st': [],
        'bench_cycles': defaultdict(int), 'benchmarks': set(),
    })

    for s in spans:
        if s.get('cycles', 0) <= 0: continue
        p = phase_data[s['phase']]
        if s.get('ipc'): p['ipcs'].append(s['ipc'])
        p['cycles'].append(s['cycles'])
        p['instructions'].append(s.get('instructions', 0))
        p['l2_misses'].append(s.get('l2_misses', 0))
        p['llc_misses'].append(s.get('llc_misses', 0))
        p['dtlb_ld'].append(s.get('dtlb_load_misses', 0))
        p['dtlb_st'].append(s.get('dtlb_store_misses', 0))
        p['bench_cycles'][s['benchmark']] += s['cycles']
        p['benchmarks'].add(s['benchmark'])

    rows = []
    for phase, d in phase_data.items():
        n = len(d['cycles'])
        total_cycles = sum(d['cycles'])
        total_instr = sum(d['instructions'])
        total_l2 = sum(d['l2_misses'])
        total_llc = sum(d['llc_misses'])
        total_dtlb_ld = sum(d['dtlb_ld'])
        total_dtlb_st = sum(d['dtlb_st'])

        ipc_p5 = pct(d['ipcs'], 0.05) if d['ipcs'] else 0
        ipc_p50 = pct(d['ipcs'], 0.50) if d['ipcs'] else 0
        ipc_p95 = pct(d['ipcs'], 0.95) if d['ipcs'] else 0

        cycle_p25 = pct(d['cycles'], 0.25)
        substantial = [(c, ip) for c, ip in zip(d['cycles'], d['ipcs']) if c >= cycle_p25 and ip > 0] if d['ipcs'] else []
        sub_ipcs = [ip for _, ip in substantial]
        ipc_sub_p5 = pct(sub_ipcs, 0.05) if sub_ipcs else ipc_p5
        ipc_sub_p50 = pct(sub_ipcs, 0.50) if sub_ipcs else ipc_p50
        ipc_sub_p95 = pct(sub_ipcs, 0.95) if sub_ipcs else ipc_p95
        ipc_sub_spread = ipc_sub_p95 / ipc_sub_p5 if ipc_sub_p5 > 0 else 0

        if ipc_p95 > 0 and total_instr > 0:
            ideal_cycles = total_instr / ipc_p95
            saveable = max(0, total_cycles - ideal_cycles)
        else:
            saveable = 0

        l2_stall = total_l2 * L2_MISS_COST
        llc_stall = total_llc * LLC_MISS_COST
        dtlb_stall = (total_dtlb_ld + total_dtlb_st) * DTLB_MISS_COST
        total_stall = l2_stall + llc_stall + dtlb_stall
        mem_bound_pct = total_stall / total_cycles * 100 if total_cycles else 0

        kinst = total_instr / 1000 if total_instr else 1
        l2_per_kinst = total_l2 / kinst
        llc_per_kinst = total_llc / kinst
        dtlb_per_kinst = (total_dtlb_ld + total_dtlb_st) / kinst

        rows.append({
            'phase': phase, 'n': n,
            'total_cycles': total_cycles,
            'total_instr': total_instr,
            'ipc_p5': ipc_p5, 'ipc_p50': ipc_p50, 'ipc_p95': ipc_p95,
            'ipc_sub_p5': ipc_sub_p5, 'ipc_sub_p50': ipc_sub_p50, 'ipc_sub_p95': ipc_sub_p95,
            'ipc_sub_spread': ipc_sub_spread,
            'n_substantial': len(substantial),
            'cycles_p25': cycle_p25,
            'saveable': saveable,
            'saveable_pct': saveable / total_cycles * 100 if total_cycles else 0,
            'l2_stall': l2_stall, 'llc_stall': llc_stall, 'dtlb_stall': dtlb_stall,
            'total_stall': total_stall,
            'mem_bound_pct': mem_bound_pct,
            'l2_per_kinst': l2_per_kinst,
            'llc_per_kinst': llc_per_kinst,
            'dtlb_per_kinst': dtlb_per_kinst,
            'bench_cycles': dict(d['bench_cycles']),
            'benchmarks': sorted(d['benchmarks']),
        })

    # Top 25% by total cycles
    rows.sort(key=lambda r: r['total_cycles'], reverse=True)
    cutoff = max(len(rows) // 4, 1)
    return rows[:cutoff]


def render_saveable_table(h, rows):
    """Render the Saveable Cycles table."""
    rows_sorted = sorted(rows, key=lambda r: r['saveable'], reverse=True)
    grand_total = sum(r['total_cycles'] for r in rows) or 1
    max_saveable = max((r['saveable'] for r in rows), default=1)

    h.append('<h3>Saveable Cycles</h3>')
    h.append('<p class="desc">If every invocation ran at the phase\'s p95 IPC, '
             'how many cycles would we save?</p>')
    h.append('<table><tr>'
             '<th>Phase</th><th>N</th>'
             '<th>Total Cycles</th><th>%</th>'
             '<th>Saveable</th><th></th><th>Save %</th>'
             '<th>IPC p50</th><th>IPC p95</th>'
             '</tr>')
    for r in rows_sorted:
        save_color = color_scale(r['saveable_pct'], 0, 80)
        ipc_color = color_scale(r['ipc_p50'], 0.5, 4.0, invert=True)
        tp = r['total_cycles'] / grand_total * 100
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
            f'<td class="r">{tp:.1f}%</td>'
            f'<td class="r" style="color:{save_color};font-weight:600">{fmt_cycles(r["saveable"])}</td>'
            f'<td>{bar_html(r["saveable"], max_saveable, save_color, 80)}</td>'
            f'<td class="r" style="color:{save_color}">{r["saveable_pct"]:.0f}%</td>'
            f'<td class="r" style="color:{ipc_color}">{r["ipc_p50"]:.2f}</td>'
            f'<td class="r">{r["ipc_p95"]:.2f}</td>'
            f'</tr>')
    h.append('</table>')


def render_memory_table(h, rows):
    """Render the Memory Boundedness table."""
    rows_sorted = sorted(rows, key=lambda r: r['mem_bound_pct'], reverse=True)
    max_stall = max((r['total_stall'] for r in rows), default=1)

    h.append('<h3>Memory Boundedness</h3>')
    h.append(f'<p class="desc">Estimated cycles stalled on memory. '
             f'L2 ≈ {L2_MISS_COST}cy, LLC ≈ {LLC_MISS_COST}cy, dTLB ≈ {DTLB_MISS_COST}cy. '
             f'Bar: <span style="color:#cc7733">L2</span> / '
             f'<span style="color:#cc3333">LLC</span> / '
             f'<span style="color:#7733cc">dTLB</span>.</p>')
    h.append('<table><tr>'
             '<th>Phase</th><th>N</th>'
             '<th>Total Cycles</th>'
             '<th>% Mem Bound</th>'
             '<th>Stall</th><th></th>'
             '<th>L2/ki</th><th>LLC/ki</th><th>dTLB/ki</th>'
             '</tr>')
    for r in rows_sorted:
        mb_color = color_scale(r['mem_bound_pct'], 0, 80)
        stall_parts = [
            (r['l2_stall'], '#cc7733', f"L2: {r['l2_stall']/1e9:.2f}B cy"),
            (r['llc_stall'], '#cc3333', f"LLC: {r['llc_stall']/1e9:.2f}B cy"),
            (r['dtlb_stall'], '#7733cc', f"dTLB: {r['dtlb_stall']/1e9:.2f}B cy"),
        ]
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
            f'<td class="r" style="color:{mb_color};font-weight:600">{r["mem_bound_pct"]:.0f}%</td>'
            f'<td class="r" style="font-size:11px">{fmt_cycles(r["total_stall"])}</td>'
            f'<td>{stacked_bar_html(stall_parts, max_stall, 100)}</td>'
            f'<td class="r">{r["l2_per_kinst"]:.1f}</td>'
            f'<td class="r">{r["llc_per_kinst"]:.1f}</td>'
            f'<td class="r">{r["dtlb_per_kinst"]:.2f}</td>'
            f'</tr>')
    h.append('</table>')


def render_variability_table(h, rows):
    """Render the IPC Variability table."""
    rows_sorted = sorted(rows, key=lambda r: r['ipc_sub_spread'], reverse=True)
    max_spread = max((r['ipc_sub_spread'] for r in rows), default=1)

    h.append('<h3>IPC Variability (substantial spans only)</h3>')
    h.append('<p class="desc">p95/p5 IPC ratio on spans above each phase\'s p25 cycle count. '
             'High spread = cache/TLB sensitive.</p>')
    h.append('<table><tr>'
             '<th>Phase</th><th>N (subst.)</th>'
             '<th>Total Cycles</th>'
             '<th>Cycle floor</th>'
             '<th>IPC p5</th><th>IPC p50</th><th>IPC p95</th>'
             '<th>Spread</th><th></th>'
             '</tr>')
    for r in rows_sorted:
        sp_color = color_scale(r['ipc_sub_spread'], 1, 10)
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n_substantial"]:,}</td>'
            f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
            f'<td class="r">{r["cycles_p25"]:,}</td>'
            f'<td class="r">{r["ipc_sub_p5"]:.3f}</td>'
            f'<td class="r">{r["ipc_sub_p50"]:.2f}</td>'
            f'<td class="r">{r["ipc_sub_p95"]:.2f}</td>'
            f'<td class="r" style="color:{sp_color};font-weight:600">{r["ipc_sub_spread"]:.1f}&times;</td>'
            f'<td>{bar_html(r["ipc_sub_spread"], max_spread, sp_color, 60)}</td>'
            f'</tr>')
    h.append('</table>')


def generate_report(result_dir):
    spans = load_spans(result_dir)
    total_spans = len(spans)
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    dirname = os.path.basename(result_dir.rstrip('/'))

    h = []
    h.append(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Per-Benchmark Optimization Targets — {dirname}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 20px; background: #fafafa; color: #222; line-height: 1.4; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 18px; margin-top: 36px; padding: 8px 12px; border-radius: 4px; }}
  h3 {{ font-size: 15px; margin-top: 24px; border-bottom: 2px solid #ddd; padding-bottom: 4px; }}
  p.desc {{ color: #555; font-size: 13px; margin: 4px 0 12px 0; max-width: 800px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; font-size: 13px; margin-bottom: 24px; }}
  th {{ background: #f0f0f0; text-align: left; padding: 6px 10px; border: 1px solid #ddd; font-weight: 600; white-space: nowrap; }}
  td {{ padding: 5px 10px; border: 1px solid #eee; white-space: nowrap; }}
  tr:hover td {{ background: #f5f5ff; }}
  .r {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .phase {{ font-weight: 600; }}
  .bench-section {{ border-left: 4px solid; padding-left: 12px; margin-top: 32px; }}
  .note {{ font-size: 11px; color: #999; margin-top: -18px; margin-bottom: 16px; }}
  .cross-table th {{ position: sticky; top: 0; }}
  .cross-table td.bench-tag {{ font-weight: 600; }}
</style>
</head><body>
<h1>Per-Benchmark Optimization Targets</h1>
<div class="meta">{dirname} &mdash; {total_spans:,} spans, {len(benchmarks)} benchmarks &mdash; generated {ts}</div>
""")

    # Per-benchmark sections
    bench_rows = {}
    for bench in benchmarks:
        bench_spans = [s for s in spans if s['benchmark'] == bench]
        rows = compute_phase_rows(bench_spans)
        bench_rows[bench] = rows

        color = BENCH_COLORS.get(bench, '#888')
        short = bench.replace('_bench', '')
        n_spans = len(bench_spans)

        h.append(f'<div class="bench-section" style="border-color:{color}">')
        h.append(f'<h2 style="background:{color}11;color:{color}">{short}</h2>')
        h.append(f'<div class="meta">{n_spans:,} spans, top {len(rows)} phases by cycle count</div>')

        if rows:
            render_saveable_table(h, rows)
            render_memory_table(h, rows)
            render_variability_table(h, rows)
        else:
            h.append('<p>No data.</p>')

        h.append('</div>')

    # Cross-benchmark comparison table
    h.append('<h2 style="margin-top:48px;border-bottom:2px solid #333;padding-bottom:4px">'
             'Cross-Benchmark Comparison</h2>')
    h.append('<p class="desc">Same phase side-by-side across benchmarks. '
             'Shows how cache behavior and IPC differ by workload. '
             'Only phases appearing in at least 2 benchmarks\' top-25% are shown.</p>')

    # Collect all phases that appear in top rows of at least 2 benchmarks
    phase_bench_map = defaultdict(dict)  # phase -> {bench: row}
    for bench, rows in bench_rows.items():
        for r in rows:
            phase_bench_map[r['phase']][bench] = r

    cross_phases = {p: bm for p, bm in phase_bench_map.items() if len(bm) >= 2}

    if cross_phases:
        # Sort by total cycles across all benchmarks
        cross_sorted = sorted(cross_phases.keys(),
            key=lambda p: sum(r['total_cycles'] for r in cross_phases[p].values()),
            reverse=True)

        h.append('<table class="cross-table"><tr>'
                 '<th>Phase</th><th>Benchmark</th>'
                 '<th>Total Cycles</th>'
                 '<th>IPC p50</th><th>IPC p95</th><th>Spread</th>'
                 '<th>% Mem Bound</th>'
                 '<th>L2/ki</th><th>LLC/ki</th><th>dTLB/ki</th>'
                 '<th>Save %</th>'
                 '</tr>')

        for phase in cross_sorted:
            bm = cross_phases[phase]
            first = True
            for bench in benchmarks:
                if bench not in bm: continue
                r = bm[bench]
                color = BENCH_COLORS.get(bench, '#888')
                short = bench.replace('_bench', '')
                mb_color = color_scale(r['mem_bound_pct'], 0, 80)
                sp_color = color_scale(r['ipc_sub_spread'], 1, 10)
                save_color = color_scale(r['saveable_pct'], 0, 80)
                ipc_color = color_scale(r['ipc_p50'], 0.5, 4.0, invert=True)

                phase_cell = f'<td class="phase" rowspan="{len(bm)}">{html.escape(phase)}</td>' if first else ''
                h.append(f'<tr>'
                    f'{phase_cell}'
                    f'<td class="bench-tag" style="color:{color}">{short}</td>'
                    f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
                    f'<td class="r" style="color:{ipc_color}">{r["ipc_p50"]:.2f}</td>'
                    f'<td class="r">{r["ipc_p95"]:.2f}</td>'
                    f'<td class="r" style="color:{sp_color}">{r["ipc_sub_spread"]:.1f}&times;</td>'
                    f'<td class="r" style="color:{mb_color};font-weight:600">{r["mem_bound_pct"]:.0f}%</td>'
                    f'<td class="r">{r["l2_per_kinst"]:.1f}</td>'
                    f'<td class="r">{r["llc_per_kinst"]:.1f}</td>'
                    f'<td class="r">{r["dtlb_per_kinst"]:.2f}</td>'
                    f'<td class="r" style="color:{save_color}">{r["saveable_pct"]:.0f}%</td>'
                    f'</tr>')
                first = False
            # Visual separator between phases
            h.append('<tr><td colspan="11" style="border:none;height:4px;padding:0"></td></tr>')

        h.append('</table>')
    else:
        h.append('<p>No phases appear in the top-25% of multiple benchmarks.</p>')

    h.append('</body></html>')
    return '\n'.join(h)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results-dir> [output.html]", file=sys.stderr)
        sys.exit(1)
    result_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(result_dir, 'report_targets_by_bench.html')
    report = generate_report(result_dir)
    with open(out, 'w') as f:
        f.write(report)
    print(f"Report written to {out}")
