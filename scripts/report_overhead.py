#!/usr/bin/env python3
"""Generate overhead analysis report from run_overhead.sh results.

Computes statistics (mean, stddev, 95% CI, Welch's t-test) using only
the stdlib — no scipy/numpy required. Produces an HTML report with
summary table and CSS-only bar chart.
"""

import json, sys, os, math
from datetime import datetime

BENCHMARKS = ['htmlStream_bench', 'stre_bench', 'network_bench', 'gzip_bench']
MODES = ['baseline', 'events', 'pmc']
MODE_LABELS = {'baseline': 'Baseline', 'events': 'Events Only', 'pmc': 'Events+PMC'}

# t-critical values for 95% CI (two-tailed, alpha=0.05)
# Key: degrees of freedom -> t-value. Interpolate for missing.
T_TABLE = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    25: 2.060, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}

def t_critical(df):
    """Look up t-critical for given df using table + interpolation."""
    if df <= 0: return 12.706
    df = max(1, int(round(df)))
    if df in T_TABLE: return T_TABLE[df]
    # Find bracketing entries
    keys = sorted(T_TABLE.keys())
    if df < keys[0]: return T_TABLE[keys[0]]
    if df > keys[-1]: return 1.960  # z-approx for large df
    lo = max(k for k in keys if k <= df)
    hi = min(k for k in keys if k >= df)
    if lo == hi: return T_TABLE[lo]
    frac = (df - lo) / (hi - lo)
    return T_TABLE[lo] + frac * (T_TABLE[hi] - T_TABLE[lo])


def load_times(result_dir, bench, mode):
    path = os.path.join(result_dir, f'{bench}_{mode}.times')
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [float(line.strip()) for line in f if line.strip()]


def stats(vals):
    """Return mean, stddev, n, 95% CI half-width."""
    n = len(vals)
    if n == 0: return 0, 0, 0, 0
    mean = sum(vals) / n
    if n == 1: return mean, 0, 1, 0
    var = sum((x - mean) ** 2 for x in vals) / (n - 1)
    sd = math.sqrt(var)
    se = sd / math.sqrt(n)
    tc = t_critical(n - 1)
    ci = tc * se
    return mean, sd, n, ci


def welch_t_test(vals1, vals2):
    """Welch's t-test. Returns t-statistic, degrees of freedom, and
    whether p < 0.05 (True = significant)."""
    n1, n2 = len(vals1), len(vals2)
    if n1 < 2 or n2 < 2:
        return 0, 0, False
    m1 = sum(vals1) / n1
    m2 = sum(vals2) / n2
    v1 = sum((x - m1) ** 2 for x in vals1) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in vals2) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0: return 0, max(n1, n2) - 1, False
    t = (m2 - m1) / se
    # Welch-Satterthwaite df
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else 1
    tc = t_critical(int(df))
    sig = abs(t) > tc
    return t, df, sig


def overhead_pct_ci(base_vals, mode_vals):
    """Compute overhead % and CI via delta method.
    overhead = (mean_mode - mean_base) / mean_base * 100
    """
    m_b, sd_b, n_b, _ = stats(base_vals)
    m_m, sd_m, n_m, _ = stats(mode_vals)
    if m_b == 0 or n_b < 2 or n_m < 2:
        return 0, 0, 0
    oh = (m_m - m_b) / m_b * 100

    # Delta method: Var(M/B - 1) ≈ (1/B²)(Var(M) + (M/B)²·Var(B) - 2·(M/B)·Cov(M,B))
    # Since M and B are independent (different runs): Cov = 0
    var_m = sd_m ** 2 / n_m
    var_b = sd_b ** 2 / n_b
    ratio = m_m / m_b
    var_ratio = var_m / m_b ** 2 + ratio ** 2 * var_b / m_b ** 2
    se_pct = math.sqrt(var_ratio) * 100 if var_ratio > 0 else 0

    # Use the smaller df for conservative CI
    df = min(n_b - 1, n_m - 1)
    tc = t_critical(df)
    ci = tc * se_pct
    return oh, ci, se_pct


def generate_report(result_dir):
    # Load metadata
    meta_path = os.path.join(result_dir, 'metadata.json')
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    dirname = os.path.basename(result_dir.rstrip('/'))

    # Collect all data
    data = {}
    for bench in BENCHMARKS:
        data[bench] = {}
        for mode in MODES:
            data[bench][mode] = load_times(result_dir, bench, mode)

    h = []
    h.append(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Overhead Report — {dirname}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 20px; background: #fafafa; color: #222; line-height: 1.4; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 17px; margin-top: 32px; border-bottom: 2px solid #ddd; padding-bottom: 4px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  .summary-cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{ background: white; border: 1px solid #ddd; border-radius: 6px; padding: 12px 18px; min-width: 140px; }}
  .card h3 {{ margin: 0 0 4px 0; font-size: 13px; color: #666; }}
  .card .val {{ font-size: 20px; font-weight: 700; }}
  table {{ border-collapse: collapse; font-size: 13px; margin-bottom: 24px; }}
  th {{ background: #f0f0f0; text-align: left; padding: 6px 10px; border: 1px solid #ddd; font-weight: 600; white-space: nowrap; }}
  td {{ padding: 5px 10px; border: 1px solid #eee; white-space: nowrap; }}
  tr:hover td {{ background: #f5f5ff; }}
  .r {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .sig {{ color: #cc3333; font-weight: 600; }}
  .ns {{ color: #888; }}
  .bar-container {{ display: inline-flex; align-items: center; gap: 6px; }}
  .bar {{ height: 18px; border-radius: 2px; display: inline-block; min-width: 1px; }}
  .bar-events {{ background: #4477aa; }}
  .bar-pmc {{ background: #aa4477; }}
  .whisker {{ display: inline-block; width: 1px; background: #666; height: 18px; position: relative; }}
  .whisker-cap {{ display: inline-block; width: 7px; height: 1px; background: #666; position: absolute; }}
  .chart-row {{ margin: 8px 0; display: flex; align-items: center; gap: 10px; }}
  .chart-label {{ width: 120px; text-align: right; font-size: 13px; font-weight: 600; }}
  .chart-bar-area {{ position: relative; width: 400px; height: 44px; }}
  .chart-zero {{ position: absolute; left: 200px; top: 0; bottom: 0; width: 1px; background: #ccc; }}
  .chart-bar {{ position: absolute; top: 4px; height: 22px; border-radius: 2px; opacity: 0.85; }}
  .chart-ci {{ position: absolute; top: 12px; height: 1px; background: #333; }}
  .chart-ci-cap {{ position: absolute; top: 8px; width: 1px; height: 9px; background: #333; }}
  .chart-val {{ font-size: 12px; font-variant-numeric: tabular-nums; }}
  .methodology {{ background: #f8f8f0; border: 1px solid #e0e0d0; border-radius: 4px; padding: 14px 18px; font-size: 12px; color: #555; margin-top: 24px; }}
  .methodology h3 {{ margin: 0 0 8px 0; font-size: 13px; color: #333; }}
</style>
</head><body>
<h1>Runtime Events Overhead</h1>
<div class="meta">{dirname} &mdash; generated {ts}</div>
""")

    # Summary cards
    iters = meta.get('iterations', '?')
    cpu = meta.get('cpu', '?')
    rounds_per_bench = meta.get('rounds_per_bench', {})
    rounds_default = meta.get('rounds_default', meta.get('rounds', '?'))
    if rounds_per_bench:
        rounds_str = ', '.join(f'{b.replace("_bench","")}: {r}'
                               for b, r in rounds_per_bench.items())
    else:
        rounds_str = str(rounds_default)
    h.append('<div class="summary-cards">')
    h.append(f'<div class="card"><h3>Iterations</h3><div class="val">{iters}</div></div>')
    h.append(f'<div class="card"><h3>Pinned CPU</h3><div class="val">{cpu}</div></div>')
    h.append(f'<div class="card"><h3>BENCH_ROUNDS</h3><div class="val" style="font-size:14px">{rounds_str}</div></div>')
    h.append(f'<div class="card"><h3>Date</h3><div class="val">{meta.get("date", "?")[:10]}</div></div>')
    h.append('</div>')

    # Main results table
    h.append('<h2>Results</h2>')
    h.append('<table><tr>'
             '<th>Benchmark</th>'
             '<th>Baseline</th>'
             '<th>Events Only</th>'
             '<th>Events+PMC</th>'
             '<th>Events Overhead</th>'
             '<th>PMC Overhead</th>'
             '</tr>')

    chart_data = []

    for bench in BENCHMARKS:
        short = bench.replace('_bench', '')
        base = data[bench]['baseline']
        evts = data[bench]['events']
        pmc = data[bench]['pmc']

        s_base = stats(base)
        s_evts = stats(evts)
        s_pmc = stats(pmc)

        oh_evts, ci_evts, _ = overhead_pct_ci(base, evts)
        oh_pmc, ci_pmc, _ = overhead_pct_ci(base, pmc)

        _, _, sig_evts = welch_t_test(base, evts)
        _, _, sig_pmc = welch_t_test(base, pmc)

        def fmt_stat(m, ci):
            return f'{m:.2f} &pm; {ci:.2f}s'

        def fmt_oh(oh, ci, sig):
            sign = '+' if oh >= 0 else ''
            lo, hi = oh - ci, oh + ci
            cls = 'sig' if sig else 'ns'
            return f'<span class="{cls}">{sign}{oh:.1f}% [{lo:.1f}, {hi:.1f}]</span>'

        h.append(f'<tr>'
            f'<td style="font-weight:600">{short}</td>'
            f'<td class="r">{fmt_stat(s_base[0], s_base[3])}</td>'
            f'<td class="r">{fmt_stat(s_evts[0], s_evts[3])}</td>'
            f'<td class="r">{fmt_stat(s_pmc[0], s_pmc[3])}</td>'
            f'<td class="r">{fmt_oh(oh_evts, ci_evts, sig_evts)}</td>'
            f'<td class="r">{fmt_oh(oh_pmc, ci_pmc, sig_pmc)}</td>'
            f'</tr>')

        chart_data.append((short, oh_evts, ci_evts, oh_pmc, ci_pmc))

    h.append('</table>')
    h.append('<div style="font-size:11px;color:#888;margin-top:-18px;margin-bottom:16px">'
             '<span class="sig">Red</span> = statistically significant (p &lt; 0.05, Welch\'s t-test). '
             '<span class="ns">Gray</span> = not significant.</div>')

    # CSS-only bar chart
    h.append('<h2>Overhead %</h2>')
    h.append('<div style="margin-bottom:8px;font-size:12px">'
             '<span style="display:inline-block;width:14px;height:14px;background:#4477aa;border-radius:2px;vertical-align:middle"></span> Events Only &nbsp; '
             '<span style="display:inline-block;width:14px;height:14px;background:#aa4477;border-radius:2px;vertical-align:middle"></span> Events+PMC</div>')

    # Find max absolute overhead for scaling
    max_oh = max(max(abs(oh_e) + ci_e, abs(oh_p) + ci_p)
                 for _, oh_e, ci_e, oh_p, ci_p in chart_data) if chart_data else 5
    max_oh = max(max_oh, 1)
    chart_width = 400
    center = chart_width // 2
    scale = center / max_oh  # px per %

    for short, oh_e, ci_e, oh_p, ci_p in chart_data:
        h.append(f'<div class="chart-row">')
        h.append(f'<div class="chart-label">{short}</div>')
        h.append(f'<div class="chart-bar-area">')
        h.append(f'<div class="chart-zero"></div>')

        # Events bar
        bar_left = center if oh_e >= 0 else center + oh_e * scale
        bar_w = abs(oh_e) * scale
        ci_left_e = center + (oh_e - ci_e) * scale
        ci_right_e = center + (oh_e + ci_e) * scale
        h.append(f'<div class="chart-bar bar-events" style="left:{bar_left:.0f}px;width:{max(bar_w, 1):.0f}px"></div>')
        # CI whiskers for events
        h.append(f'<div class="chart-ci" style="left:{ci_left_e:.0f}px;width:{ci_right_e - ci_left_e:.0f}px;top:8px"></div>')
        h.append(f'<div class="chart-ci-cap" style="left:{ci_left_e:.0f}px;top:4px"></div>')
        h.append(f'<div class="chart-ci-cap" style="left:{ci_right_e:.0f}px;top:4px"></div>')

        # PMC bar (offset down slightly)
        bar_left_p = center if oh_p >= 0 else center + oh_p * scale
        bar_w_p = abs(oh_p) * scale
        ci_left_p = center + (oh_p - ci_p) * scale
        ci_right_p = center + (oh_p + ci_p) * scale
        h.append(f'<div class="chart-bar bar-pmc" style="left:{bar_left_p:.0f}px;width:{max(bar_w_p, 1):.0f}px;top:16px"></div>')
        h.append(f'<div class="chart-ci" style="left:{ci_left_p:.0f}px;width:{ci_right_p - ci_left_p:.0f}px;top:24px"></div>')
        h.append(f'<div class="chart-ci-cap" style="left:{ci_left_p:.0f}px;top:20px;height:9px"></div>')
        h.append(f'<div class="chart-ci-cap" style="left:{ci_right_p:.0f}px;top:20px;height:9px"></div>')

        h.append(f'</div>')
        h.append(f'<div class="chart-val">e: {oh_e:+.1f}% &nbsp; p: {oh_p:+.1f}%</div>')
        h.append(f'</div>')

    # Methodology note
    h.append("""<div class="methodology">
<h3>Methodology</h3>
<p><strong>Three modes compared:</strong></p>
<ul>
<li><strong>Baseline</strong> — no environment variables, plain execution</li>
<li><strong>Events Only</strong> — OCAML_RUNTIME_EVENTS_START=1 + PRESERVE + DIR, no PMC counters</li>
<li><strong>Events+PMC</strong> — same as Events plus OCAML_RUNTIME_EVENTS_PERF_COUNTERS (6 counters)</li>
</ul>
<p><strong>No consumer process</strong> is attached in any mode — we measure only the benchmark process wall-clock time
to isolate the overhead of enabling runtime events and PMC collection in the runtime itself.</p>
<p><strong>Interleaved execution:</strong> For each iteration, all modes run for each benchmark before moving to the next
iteration. This prevents systematic drift from thermal effects or frequency scaling.</p>
<p><strong>Statistics:</strong> 95% confidence intervals using Student's t-distribution.
Welch's t-test for significance (unequal variances assumed).
Overhead CI via delta method on the ratio of means.</p>
<p><strong>Timing:</strong> /usr/bin/time -f '%e' (wall-clock seconds). Processes pinned to a single core via taskset.</p>
</div>""")

    h.append('</body></html>')
    return '\n'.join(h)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <overhead-results-dir> [output.html]", file=sys.stderr)
        sys.exit(1)
    result_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(result_dir, 'report_overhead.html')
    report = generate_report(result_dir)
    with open(out, 'w') as f:
        f.write(report)
    print(f"Report written to {out}")
