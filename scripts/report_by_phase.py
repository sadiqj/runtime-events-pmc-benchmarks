#!/usr/bin/env python3
"""Generate HTML report aggregated by GC phase across all benchmarks."""

import json, sys, os, html
from collections import defaultdict
from datetime import datetime

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

def color_ipc(v):
    if v >= 3.0: return "#1a7a1a"
    if v >= 2.0: return "#4a9a2a"
    if v >= 1.0: return "#8a8a2a"
    if v >= 0.5: return "#ba6a2a"
    return "#cc3333"

def color_cache(v, lo=5, hi=100):
    if v <= lo: return "#1a7a1a"
    if v <= lo*3: return "#4a9a2a"
    if v <= hi*0.5: return "#8a8a2a"
    if v <= hi: return "#ba6a2a"
    return "#cc3333"

def color_tlb(v):
    return color_cache(v, lo=0.1, hi=2.0)

def bar_html(val, max_val, color, width_px=120):
    if max_val == 0: return ""
    w = min(val / max_val, 1.0) * width_px
    return f'<div style="background:{color};width:{w:.0f}px;height:14px;display:inline-block;border-radius:2px"></div>'

def stacked_bar(parts, max_val, width_px=120):
    """parts: list of (value, color). Renders a stacked bar."""
    if max_val == 0: return ""
    segs = []
    for val, color in parts:
        w = val / max_val * width_px
        if w >= 0.5:
            segs.append(f'<div style="background:{color};width:{w:.0f}px;height:14px;display:inline-block" title="{val/1e9:.2f}B"></div>')
    return ''.join(segs)

BENCH_COLORS = {
    'htmlStream_bench': '#4477aa',
    'network_bench': '#44aa77',
    'gzip_bench': '#aa7744',
    'stre_bench': '#aa4477',
}

def generate_report(result_dir):
    spans = load_spans(result_dir)
    total = len(spans)
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    # Group by phase only, but track per-benchmark breakdown
    phase_spans = defaultdict(list)
    phase_bench_cycles = defaultdict(lambda: defaultdict(int))
    for s in spans:
        if s.get('cycles', 0) > 0:
            phase_spans[s['phase']].append(s)
            phase_bench_cycles[s['phase']][s['benchmark']] += s['cycles']

    all_rows = []
    for phase, ss in phase_spans.items():
        ipcs = [s['ipc'] for s in ss if s.get('ipc')]
        cycles = [s['cycles'] for s in ss]
        l2 = [s['l2_per_kinst'] for s in ss if s.get('l2_per_kinst') is not None]
        llc = [s['llc_per_kinst'] for s in ss if s.get('llc_per_kinst') is not None]
        dtlb_ld = [s['dtlb_ld_per_kinst'] for s in ss if s.get('dtlb_ld_per_kinst') is not None]
        dtlb_st = [s['dtlb_st_per_kinst'] for s in ss if s.get('dtlb_st_per_kinst') is not None]
        dur = [s['duration_ns'] for s in ss if s.get('duration_ns')]
        bench_breakdown = phase_bench_cycles[phase]
        all_rows.append({
            'phase': phase, 'n': len(ss),
            'cycles_total': sum(cycles),
            'cycles_p50': pct(cycles, 0.5),
            'dur_p50': pct(dur, 0.5) if dur else 0,
            'ipc_p5': pct(ipcs, 0.05) if ipcs else 0,
            'ipc_p50': pct(ipcs, 0.5) if ipcs else 0,
            'ipc_p95': pct(ipcs, 0.95) if ipcs else 0,
            'l2_p50': pct(l2, 0.5) if l2 else 0,
            'l2_p95': pct(l2, 0.95) if l2 else 0,
            'llc_p50': pct(llc, 0.5) if llc else 0,
            'llc_p95': pct(llc, 0.95) if llc else 0,
            'dtlb_ld_p50': pct(dtlb_ld, 0.5) if dtlb_ld else 0,
            'dtlb_ld_p95': pct(dtlb_ld, 0.95) if dtlb_ld else 0,
            'dtlb_st_p50': pct(dtlb_st, 0.5) if dtlb_st else 0,
            'bench_cycles': bench_breakdown,
            'benchmarks': sorted(set(s['benchmark'] for s in ss)),
        })

    # Top 25% of phases by total cycles
    all_rows.sort(key=lambda r: r['cycles_total'], reverse=True)
    cutoff = max(len(all_rows) // 4, 1)
    rows = all_rows[:cutoff]

    grand_total = sum(r['cycles_total'] for r in rows)
    max_total = rows[0]['cycles_total'] if rows else 1

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    dirname = os.path.basename(result_dir.rstrip('/'))

    def fmt_total(r):
        v = r['cycles_total']
        if v >= 1e9: return f"{v/1e9:.2f}B"
        if v >= 1e6: return f"{v/1e6:.0f}M"
        return f"{v/1e3:.0f}K"

    def total_pct(r):
        return r['cycles_total'] / grand_total * 100 if grand_total else 0

    def bench_bar(r):
        parts = [(r['bench_cycles'].get(b, 0), BENCH_COLORS.get(b, '#888')) for b in benchmarks]
        return stacked_bar(parts, max_total)

    def bench_tags(r):
        tags = []
        for b in r['benchmarks']:
            c = BENCH_COLORS.get(b, '#888')
            short = b.replace('_bench', '')
            v = r['bench_cycles'].get(b, 0)
            pct_v = v / r['cycles_total'] * 100 if r['cycles_total'] else 0
            tags.append(f'<span style="background:{c};color:white;padding:1px 5px;border-radius:3px;font-size:11px;margin-right:3px" title="{b}: {v/1e9:.2f}B cycles ({pct_v:.0f}%)">{short} {pct_v:.0f}%</span>')
        return ' '.join(tags)

    h = []
    h.append(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>PMC Phase Report — {dirname}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 20px; background: #fafafa; color: #222; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 17px; margin-top: 30px; border-bottom: 2px solid #ddd; padding-bottom: 4px; }}
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
</style>
</head><body>
<h1>PMC Phase Report (aggregated across benchmarks)</h1>
<div class="meta">{dirname} &mdash; generated {ts}</div>
""")

    # Summary
    h.append('<div>')
    h.append(f'<div class="summary-card"><h3>Total Spans</h3><div class="big">{total:,}</div><div class="sub">{len(all_rows)} phases, top {len(rows)} shown</div></div>')
    h.append(f'<div class="summary-card"><h3>Top 25% Cycles</h3><div class="big">{grand_total/1e9:.1f}B</div><div class="sub">across {len(rows)} phases</div></div>')
    h.append('</div>')

    # Legend
    h.append('<div class="legend">')
    for b in benchmarks:
        c = BENCH_COLORS.get(b, '#888')
        short = b.replace('_bench', '')
        h.append(f'<span style="background:{c}">{short}</span>')
    h.append('</div>')

    # --- IPC Table ---
    rows_by_ipc = sorted(rows, key=lambda r: r['ipc_p50'])
    h.append('<h2>IPC &mdash; worst efficiency first</h2>')
    h.append('<table><tr><th>Phase</th><th>N</th><th>Total Cycles</th><th></th><th>%</th><th>IPC p5</th><th>IPC p50</th><th>IPC p95</th><th>Benchmarks</th></tr>')
    for r in rows_by_ipc:
        c = color_ipc(r['ipc_p50'])
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_total(r)}</td>'
            f'<td>{bench_bar(r)}</td>'
            f'<td class="r">{total_pct(r):.1f}%</td>'
            f'<td class="r" style="color:{c}">{r["ipc_p5"]:.3f}</td>'
            f'<td class="r" style="color:{c};font-weight:600">{r["ipc_p50"]:.3f}</td>'
            f'<td class="r" style="color:{c}">{r["ipc_p95"]:.3f}</td>'
            f'<td>{bench_tags(r)}</td>'
            f'</tr>')
    h.append('</table>')

    # --- Cache Table ---
    rows_by_l2 = sorted(rows, key=lambda r: r['l2_p50'], reverse=True)
    max_l2 = max((r['l2_p50'] for r in rows), default=1)
    h.append('<h2>Cache Misses /kinst &mdash; worst first</h2>')
    h.append('<table><tr><th>Phase</th><th>N</th><th>Total Cycles</th><th></th><th>%</th><th>L2 p50</th><th></th><th>L2 p95</th><th>LLC p50</th><th></th><th>LLC p95</th><th>Benchmarks</th></tr>')
    for r in rows_by_l2:
        cl2 = color_cache(r['l2_p50'])
        cllc = color_cache(r['llc_p50'])
        bar_l2 = bar_html(r['l2_p50'], max_l2, cl2, 50)
        bar_llc = bar_html(r['llc_p50'], max_l2, cllc, 50)
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_total(r)}</td>'
            f'<td>{bench_bar(r)}</td>'
            f'<td class="r">{total_pct(r):.1f}%</td>'
            f'<td class="r" style="color:{cl2};font-weight:600">{r["l2_p50"]:.1f}</td>'
            f'<td>{bar_l2}</td>'
            f'<td class="r" style="color:{cl2}">{r["l2_p95"]:.1f}</td>'
            f'<td class="r" style="color:{cllc};font-weight:600">{r["llc_p50"]:.1f}</td>'
            f'<td>{bar_llc}</td>'
            f'<td class="r" style="color:{cllc}">{r["llc_p95"]:.1f}</td>'
            f'<td>{bench_tags(r)}</td>'
            f'</tr>')
    h.append('</table>')

    # --- TLB Table ---
    rows_by_tlb = sorted(rows, key=lambda r: r['dtlb_ld_p50'], reverse=True)
    max_tlb = max((r['dtlb_ld_p50'] for r in rows), default=1)
    h.append('<h2>dTLB Misses /kinst &mdash; worst first</h2>')
    h.append('<table><tr><th>Phase</th><th>N</th><th>Total Cycles</th><th></th><th>%</th><th>dTLB-ld p50</th><th></th><th>dTLB-ld p95</th><th>dTLB-st p50</th><th>Benchmarks</th></tr>')
    for r in rows_by_tlb:
        ct = color_tlb(r['dtlb_ld_p50'])
        bar_t = bar_html(r['dtlb_ld_p50'], max_tlb, ct, 50)
        h.append(f'<tr>'
            f'<td class="phase">{html.escape(r["phase"])}</td>'
            f'<td class="r">{r["n"]:,}</td>'
            f'<td class="r">{fmt_total(r)}</td>'
            f'<td>{bench_bar(r)}</td>'
            f'<td class="r">{total_pct(r):.1f}%</td>'
            f'<td class="r" style="color:{ct};font-weight:600">{r["dtlb_ld_p50"]:.4f}</td>'
            f'<td>{bar_t}</td>'
            f'<td class="r" style="color:{ct}">{r["dtlb_ld_p95"]:.4f}</td>'
            f'<td class="r">{r["dtlb_st_p50"]:.4f}</td>'
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
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(result_dir, 'report_by_phase.html')
    report = generate_report(result_dir)
    with open(out, 'w') as f:
        f.write(report)
    print(f"Report written to {out}")
