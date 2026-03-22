#!/usr/bin/env python3
"""Generate optimization targets report from PMC benchmark results.

Answers: "Which GC phases should I optimize, and why?"

Three views of the same data:
1. Saveable cycles — where is the biggest headroom?
2. Memory boundedness — what's causing the stalls?
3. Variability — which phases are cache-sensitive?
"""

import json, sys, os, html
from collections import defaultdict
from datetime import datetime

# Approximate stall costs for Intel Alder Lake P-core (cycles)
L2_MISS_COST = 12      # L2 miss, hit LLC
LLC_MISS_COST = 65      # LLC miss, go to DRAM
DTLB_MISS_COST = 20    # dTLB miss, page walk

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
    """Green at good end, red at bad end."""
    if hi == lo: return "#888"
    t = max(0, min(1, (val - lo) / (hi - lo)))
    if invert: t = 1 - t
    # t=0 → green, t=1 → red
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

BENCH_COLORS = {
    'htmlStream_bench': '#4477aa',
    'network_bench': '#44aa77',
    'gzip_bench': '#aa7744',
    'stre_bench': '#aa4477',
}

def generate_report(result_dir):
    spans = load_spans(result_dir)
    total_spans = len(spans)
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    # Aggregate by phase across all benchmarks
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

        # IPC stats on "substantial" spans only (above p25 cycles for this phase)
        # This filters out short interrupted invocations that add noise
        cycle_p25 = pct(d['cycles'], 0.25)
        substantial = [(c, ip) for c, ip in zip(d['cycles'], d['ipcs']) if c >= cycle_p25 and ip > 0] if d['ipcs'] else []
        sub_ipcs = [ip for _, ip in substantial]
        ipc_sub_p5 = pct(sub_ipcs, 0.05) if sub_ipcs else ipc_p5
        ipc_sub_p50 = pct(sub_ipcs, 0.50) if sub_ipcs else ipc_p50
        ipc_sub_p95 = pct(sub_ipcs, 0.95) if sub_ipcs else ipc_p95
        ipc_sub_spread = ipc_sub_p95 / ipc_sub_p5 if ipc_sub_p5 > 0 else 0

        # Saveable cycles: if every span ran at the phase's p95 IPC
        if ipc_p95 > 0 and total_instr > 0:
            ideal_cycles = total_instr / ipc_p95
            saveable = max(0, total_cycles - ideal_cycles)
        else:
            saveable = 0

        # Memory stall estimates
        l2_stall = total_l2 * L2_MISS_COST
        llc_stall = total_llc * LLC_MISS_COST
        dtlb_stall = (total_dtlb_ld + total_dtlb_st) * DTLB_MISS_COST
        total_stall = l2_stall + llc_stall + dtlb_stall
        mem_bound_pct = total_stall / total_cycles * 100 if total_cycles else 0

        # Per-kinst rates (using totals for stable estimate)
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
            'bench_cycles': d['bench_cycles'],
            'benchmarks': sorted(d['benchmarks']),
        })

    # Top 25% by total cycles
    rows.sort(key=lambda r: r['total_cycles'], reverse=True)
    cutoff = max(len(rows) // 4, 1)
    rows = rows[:cutoff]

    grand_total = sum(r['total_cycles'] for r in rows)
    max_total = rows[0]['total_cycles'] if rows else 1
    max_saveable = max(r['saveable'] for r in rows) if rows else 1

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    dirname = os.path.basename(result_dir.rstrip('/'))

    def fmt_cycles(v):
        if v >= 1e9: return f"{v/1e9:.2f}B"
        if v >= 1e6: return f"{v/1e6:.0f}M"
        return f"{v/1e3:.0f}K"

    def total_pct(r):
        return r['total_cycles'] / grand_total * 100 if grand_total else 0

    def bench_bar(r):
        parts = [(r['bench_cycles'].get(b, 0), BENCH_COLORS.get(b, '#888'),
                   f"{b}: {r['bench_cycles'].get(b,0)/1e9:.2f}B") for b in benchmarks]
        return stacked_bar_html(parts, max_total)

    def bench_tags(r):
        tags = []
        for b in r['benchmarks']:
            c = BENCH_COLORS.get(b, '#888')
            short = b.replace('_bench', '')
            v = r['bench_cycles'].get(b, 0)
            p = v / r['total_cycles'] * 100 if r['total_cycles'] else 0
            tags.append(f'<span style="background:{c};color:white;padding:1px 5px;border-radius:3px;font-size:11px;margin-right:2px" title="{b}: {v/1e9:.2f}B ({p:.0f}%)">{short} {p:.0f}%</span>')
        return ' '.join(tags)

    h = []
    h.append(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Optimization Targets — {dirname}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 20px; background: #fafafa; color: #222; line-height: 1.4; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 17px; margin-top: 32px; border-bottom: 2px solid #ddd; padding-bottom: 4px; }}
  p.desc {{ color: #555; font-size: 13px; margin: 4px 0 12px 0; max-width: 800px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; font-size: 13px; margin-bottom: 24px; }}
  th {{ background: #f0f0f0; text-align: left; padding: 6px 10px; border: 1px solid #ddd; font-weight: 600; white-space: nowrap; }}
  td {{ padding: 5px 10px; border: 1px solid #eee; white-space: nowrap; }}
  tr:hover td {{ background: #f5f5ff; }}
  .r {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .phase {{ font-weight: 600; }}
  .summary-card {{ display: inline-block; background: white; border: 1px solid #ddd; border-radius: 6px; padding: 14px 20px; margin: 6px; min-width: 180px; }}
  .summary-card h3 {{ margin: 0 0 6px 0; font-size: 14px; }}
  .summary-card .big {{ font-size: 22px; font-weight: 700; }}
  .summary-card .sub {{ font-size: 12px; color: #888; }}
  .legend {{ margin: 10px 0 16px 0; }}
  .legend span {{ padding: 2px 8px; border-radius: 3px; color: white; font-size: 12px; margin-right: 6px; }}
  .note {{ font-size: 11px; color: #999; margin-top: -18px; margin-bottom: 16px; }}
</style>
</head><body>
<h1>GC Phase Optimization Targets</h1>
<div class="meta">{dirname} &mdash; {total_spans:,} spans, {len(phase_data)} phases &mdash; generated {ts}</div>
""")

    # Legend
    h.append('<div class="legend">')
    for b in benchmarks:
        c = BENCH_COLORS.get(b, '#888')
        h.append(f'<span style="background:{c}">{b.replace("_bench","")}</span>')
    h.append('</div>')

    # ===== Section 1: Saveable Cycles =====
    rows_by_saveable = sorted(rows, key=lambda r: r['saveable'], reverse=True)

    h.append('<h2>1. Saveable Cycles</h2>')
    h.append('<p class="desc">If every invocation of a phase ran at that phase\'s p95 IPC (its own best-case), '
             'how many cycles would we save? Phases at the top have the most headroom &times; volume.</p>')
    h.append('<table><tr>'
             '<th>Phase</th><th>N</th>'
             '<th>Total Cycles</th><th></th><th>%</th>'
             '<th>Saveable</th><th></th><th>Save %</th>'
             '<th>IPC p50</th><th>IPC p95</th>'
             '<th>Benchmarks</th>'
             '</tr>')
    for r in rows_by_saveable:
        save_color = color_scale(r['saveable_pct'], 0, 80, invert=False)
        ipc_color = color_scale(r['ipc_p50'], 0.5, 4.0, invert=True)
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
            f'<td>{bench_bar(r)}</td>'
            f'<td class="r">{total_pct(r):.1f}%</td>'
            f'<td class="r" style="color:{save_color};font-weight:600">{fmt_cycles(r["saveable"])}</td>'
            f'<td>{bar_html(r["saveable"], max_saveable, save_color, 80)}</td>'
            f'<td class="r" style="color:{save_color}">{r["saveable_pct"]:.0f}%</td>'
            f'<td class="r" style="color:{ipc_color}">{r["ipc_p50"]:.2f}</td>'
            f'<td class="r">{r["ipc_p95"]:.2f}</td>'
            f'<td>{bench_tags(r)}</td>'
            f'</tr>')
    h.append('</table>')
    h.append(f'<div class="note">Total saveable across top phases: {sum(r["saveable"] for r in rows)/1e9:.1f}B cycles '
             f'({sum(r["saveable"] for r in rows)/grand_total*100:.0f}% of top-25% phase cycles)</div>')

    # ===== Section 2: Memory Boundedness =====
    rows_by_mem = sorted(rows, key=lambda r: r['mem_bound_pct'], reverse=True)
    max_stall = max(r['total_stall'] for r in rows) if rows else 1

    h.append('<h2>2. Memory Boundedness</h2>')
    h.append(f'<p class="desc">Estimated fraction of cycles stalled on memory, using approximate Alder Lake costs: '
             f'L2 miss ≈ {L2_MISS_COST}cy, LLC miss ≈ {LLC_MISS_COST}cy, dTLB miss ≈ {DTLB_MISS_COST}cy. '
             f'Stacked bar: <span style="color:#cc7733">L2</span> / '
             f'<span style="color:#cc3333">LLC</span> / '
             f'<span style="color:#7733cc">dTLB</span>.</p>')
    h.append('<table><tr>'
             '<th>Phase</th><th>N</th>'
             '<th>Total Cycles</th><th>%</th>'
             '<th>% Mem Bound</th>'
             '<th>Stall Breakdown</th><th></th>'
             '<th>L2/ki</th><th>LLC/ki</th><th>dTLB/ki</th>'
             '<th>Benchmarks</th>'
             '</tr>')
    for r in rows_by_mem:
        mb_color = color_scale(r['mem_bound_pct'], 0, 80, invert=False)
        stall_parts = [
            (r['l2_stall'], '#cc7733', f"L2: {r['l2_stall']/1e9:.2f}B cy"),
            (r['llc_stall'], '#cc3333', f"LLC: {r['llc_stall']/1e9:.2f}B cy"),
            (r['dtlb_stall'], '#7733cc', f"dTLB: {r['dtlb_stall']/1e9:.2f}B cy"),
        ]
        stall_bar = stacked_bar_html(stall_parts, max_stall, 100)
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
            f'<td class="r">{total_pct(r):.1f}%</td>'
            f'<td class="r" style="color:{mb_color};font-weight:600">{r["mem_bound_pct"]:.0f}%</td>'
            f'<td class="r" style="font-size:11px">{fmt_cycles(r["total_stall"])}</td>'
            f'<td>{stall_bar}</td>'
            f'<td class="r">{r["l2_per_kinst"]:.1f}</td>'
            f'<td class="r">{r["llc_per_kinst"]:.1f}</td>'
            f'<td class="r">{r["dtlb_per_kinst"]:.2f}</td>'
            f'<td>{bench_tags(r)}</td>'
            f'</tr>')
    h.append('</table>')

    # ===== Section 3: IPC Variability (substantial spans only) =====
    rows_by_spread = sorted(rows, key=lambda r: r['ipc_sub_spread'], reverse=True)
    max_spread = max(r['ipc_sub_spread'] for r in rows) if rows else 1

    h.append('<h2>3. IPC Variability (substantial spans only)</h2>')
    h.append('<p class="desc">Ratio of p95 to p5 IPC, computed only on spans above each phase\'s p25 cycle count '
             '(filters out short interrupted invocations that add noise). '
             'High spread means the phase is sensitive to cache/TLB state — '
             'prefetching, data layout, or working set changes can shift the distribution toward the fast end.</p>')
    h.append('<table><tr>'
             '<th>Phase</th><th>N (subst.)</th>'
             '<th>Total Cycles</th><th>%</th>'
             '<th>Cycle floor</th>'
             '<th>IPC p5</th><th>IPC p50</th><th>IPC p95</th>'
             '<th>Spread</th><th></th>'
             '<th>Benchmarks</th>'
             '</tr>')
    for r in rows_by_spread:
        sp_color = color_scale(r['ipc_sub_spread'], 1, 10, invert=False)
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n_substantial"]:,}</td>'
            f'<td class="r">{fmt_cycles(r["total_cycles"])}</td>'
            f'<td class="r">{total_pct(r):.1f}%</td>'
            f'<td class="r">{r["cycles_p25"]:,}</td>'
            f'<td class="r">{r["ipc_sub_p5"]:.3f}</td>'
            f'<td class="r">{r["ipc_sub_p50"]:.2f}</td>'
            f'<td class="r">{r["ipc_sub_p95"]:.2f}</td>'
            f'<td class="r" style="color:{sp_color};font-weight:600">{r["ipc_sub_spread"]:.1f}&times;</td>'
            f'<td>{bar_html(r["ipc_sub_spread"], max_spread, sp_color, 60)}</td>'
            f'<td>{bench_tags(r)}</td>'
            f'</tr>')
    h.append('</table>')

    h.append('</body></html>')
    return '\n'.join(h)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results-dir> [output.html]", file=sys.stderr)
        sys.exit(1)
    result_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(result_dir, 'report_targets.html')
    report = generate_report(result_dir)
    with open(out, 'w') as f:
        f.write(report)
    print(f"Report written to {out}")
