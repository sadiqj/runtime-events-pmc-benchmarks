#!/usr/bin/env python3
"""Generate publication-quality graphs from PMC benchmark results.

Produces PNG charts in the output directory:
  1. IPC box plots per GC phase (top phases by cycle weight)
  2. Memory boundedness breakdown (stacked horizontal bars)
  3. IPC vs memory boundedness scatter (per benchmark x phase)
  4. Cache miss rate comparison across benchmarks for key phases
  5. IPC spread (variability) dot-range chart
  6. Overhead comparison bar chart (if overhead data available)

Usage:
    python3 scripts/plot_results.py <bench-results-dir> [overhead-dir] [output-dir]
"""

import json, os, sys
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# --- Style ---
COLORS = {
    'htmlStream_bench': '#4477aa',
    'network_bench':    '#44aa77',
    'gzip_bench':       '#aa7744',
    'stre_bench':       '#aa4477',
}
SHORT = {
    'htmlStream_bench': 'htmlStream',
    'network_bench':    'network',
    'gzip_bench':       'gzip',
    'stre_bench':       'stre',
}

L2_MISS_COST = 12
LLC_MISS_COST = 65
DTLB_MISS_COST = 20

plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.size': 11,
    'axes.titlesize': 13,
    'axes.titleweight': 'bold',
    'axes.labelsize': 11,
    'figure.facecolor': '#fafafa',
    'axes.facecolor': '#ffffff',
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linewidth': 0.5,
    'axes.spines.top': False,
    'axes.spines.right': False,
})


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


# ── Figure 1: IPC box plots for top GC phases ──────────────────────────

def plot_ipc_boxplots(spans, out_dir):
    """Box plots of IPC distribution for the heaviest GC phases, per benchmark."""
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    # Find top phases by total cycles across all benchmarks
    phase_cycles = defaultdict(int)
    for s in spans:
        if s.get('cycles', 0) > 0 and not s['phase'].startswith('bench:'):
            phase_cycles[s['phase']] += s['cycles']
    top_phases = [p for p, _ in sorted(phase_cycles.items(),
                                       key=lambda x: x[1], reverse=True)[:10]]

    fig, ax = plt.subplots(figsize=(14, 7))

    positions = []
    labels = []
    data = []
    colors_list = []
    pos = 0
    group_centers = []

    for phase in top_phases:
        group_start = pos
        for bench in benchmarks:
            ipcs = [s['ipc'] for s in spans
                    if s['benchmark'] == bench and s['phase'] == phase
                    and s.get('ipc') and s.get('cycles', 0) > 0]
            if not ipcs:
                continue
            data.append(ipcs)
            positions.append(pos)
            colors_list.append(COLORS.get(bench, '#888'))
            pos += 1
        group_centers.append((group_start + pos - 1) / 2)
        labels.append(phase.replace('_', '\n'))
        pos += 1.5  # gap between phases

    bp = ax.boxplot(data, positions=positions, widths=0.7, patch_artist=True,
                    showfliers=False, medianprops=dict(color='#333', linewidth=1.5),
                    whiskerprops=dict(color='#888'), capprops=dict(color='#888'))

    for patch, color in zip(bp['boxes'], colors_list):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
        patch.set_edgecolor('#555')

    ax.set_xticks(group_centers)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel('Instructions Per Cycle (IPC)')
    ax.set_title('IPC Distribution by GC Phase — Top 10 Phases by Cycle Weight')
    ax.set_ylim(bottom=0)

    # Legend
    from matplotlib.patches import Patch
    legend_patches = [Patch(facecolor=COLORS[b], alpha=0.75,
                            label=SHORT[b]) for b in benchmarks]
    ax.legend(handles=legend_patches, loc='upper right', framealpha=0.9)

    fig.tight_layout()
    path = os.path.join(out_dir, 'ipc_boxplots.png')
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Figure 2: Memory boundedness breakdown ──────────────────────────────

def plot_memory_boundedness(spans, out_dir):
    """Stacked horizontal bar chart of memory stall breakdown by phase."""
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    # Aggregate per phase (across all benchmarks)
    phase_data = defaultdict(lambda: {'cycles': 0, 'l2': 0, 'llc': 0,
                                      'dtlb': 0, 'instr': 0})
    for s in spans:
        if s.get('cycles', 0) <= 0 or s['phase'].startswith('bench:'):
            continue
        d = phase_data[s['phase']]
        d['cycles'] += s['cycles']
        d['l2'] += s.get('l2_misses', 0)
        d['llc'] += s.get('llc_misses', 0)
        d['dtlb'] += s.get('dtlb_load_misses', 0) + s.get('dtlb_store_misses', 0)
        d['instr'] += s.get('instructions', 0)

    # Top 12 by total cycles
    top = sorted(phase_data.items(), key=lambda x: x[1]['cycles'],
                 reverse=True)[:12]

    phases = [p for p, _ in top]
    l2_pcts = []
    llc_pcts = []
    dtlb_pcts = []

    for _, d in top:
        l2_stall = d['l2'] * L2_MISS_COST
        llc_stall = d['llc'] * LLC_MISS_COST
        dtlb_stall = d['dtlb'] * DTLB_MISS_COST
        total = d['cycles']
        l2_pcts.append(l2_stall / total * 100)
        llc_pcts.append(llc_stall / total * 100)
        dtlb_pcts.append(dtlb_stall / total * 100)

    fig, ax = plt.subplots(figsize=(12, 7))
    y = np.arange(len(phases))

    ax.barh(y, l2_pcts, color='#cc7733', edgecolor='white',
            label=f'L2 miss (~{L2_MISS_COST}cy)')
    left = np.array(l2_pcts)
    ax.barh(y, llc_pcts, left=left, color='#cc3333', edgecolor='white',
            label=f'LLC miss (~{LLC_MISS_COST}cy)')
    left = left + np.array(llc_pcts)
    ax.barh(y, dtlb_pcts, left=left, color='#7733cc', edgecolor='white',
            label=f'dTLB miss (~{DTLB_MISS_COST}cy)')

    ax.set_yticks(y)
    ax.set_yticklabels(phases, fontsize=10)
    ax.set_xlabel('Estimated Stall Cycles as % of Total Cycles')
    ax.set_title('Memory Boundedness by GC Phase — Top 12 by Cycle Weight\n'
                 'Values >100% mean estimated stall exceeds measured cycles '
                 '(overlapping stalls)')
    ax.legend(loc='lower right', framealpha=0.9)
    ax.invert_yaxis()
    ax.axvline(x=100, color='#333', linestyle='--', alpha=0.4, linewidth=1)
    ax.text(102, len(phases) - 0.5, 'stall > compute', fontsize=8,
            color='#555', alpha=0.7)
    max_val = max(sum(x) for x in zip(l2_pcts, llc_pcts, dtlb_pcts))
    ax.set_xlim(0, max_val * 1.12)

    # Add total % labels
    for i in range(len(phases)):
        total_pct = l2_pcts[i] + llc_pcts[i] + dtlb_pcts[i]
        ax.text(total_pct + 2, i, f'{total_pct:.0f}%',
                va='center', fontsize=9, color='#555', fontweight='bold')

    fig.tight_layout()
    path = os.path.join(out_dir, 'memory_boundedness.png')
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Figure 3: IPC vs Memory Boundedness scatter ────────────────────────

def plot_ipc_vs_membound(spans, out_dir):
    """Scatter plot: IPC p50 vs % memory bound, sized by total cycles."""
    key_data = defaultdict(lambda: {
        'ipcs': [], 'cycles': 0, 'l2': 0, 'llc': 0, 'dtlb': 0, 'instr': 0
    })
    for s in spans:
        if s.get('cycles', 0) <= 0 or s['phase'].startswith('bench:'):
            continue
        k = (s['benchmark'], s['phase'])
        d = key_data[k]
        if s.get('ipc'):
            d['ipcs'].append(s['ipc'])
        d['cycles'] += s['cycles']
        d['l2'] += s.get('l2_misses', 0)
        d['llc'] += s.get('llc_misses', 0)
        d['dtlb'] += s.get('dtlb_load_misses', 0) + s.get('dtlb_store_misses', 0)
        d['instr'] += s.get('instructions', 0)

    fig, ax = plt.subplots(figsize=(11, 8))

    max_cycles = max(d['cycles'] for d in key_data.values()) if key_data else 1

    all_points = []
    for bench in sorted(COLORS):
        xs, ys, sizes, labels = [], [], [], []
        for (b, phase), d in key_data.items():
            if b != bench or not d['ipcs']:
                continue
            ipc_p50 = pct(d['ipcs'], 0.5)
            stall = (d['l2'] * L2_MISS_COST + d['llc'] * LLC_MISS_COST +
                     d['dtlb'] * DTLB_MISS_COST)
            mem_pct = stall / d['cycles'] * 100 if d['cycles'] else 0
            size = max(15, d['cycles'] / max_cycles * 800)
            xs.append(ipc_p50)
            ys.append(mem_pct)
            sizes.append(size)
            labels.append(phase)
            all_points.append((ipc_p50, mem_pct, size, phase, bench))

        ax.scatter(xs, ys, s=sizes, c=COLORS[bench], alpha=0.6,
                   edgecolors='white', linewidth=0.5,
                   label=SHORT[bench])

    # Label only the most extreme points across all benchmarks
    # Pick top 5 by mem_bound, top 3 by lowest IPC, top 3 by size
    labeled = set()
    by_mem = sorted(all_points, key=lambda p: p[1], reverse=True)[:5]
    by_ipc = sorted(all_points, key=lambda p: p[0])[:3]
    by_size = sorted(all_points, key=lambda p: p[2], reverse=True)[:3]
    for pt in by_mem + by_ipc + by_size:
        key = (pt[3], pt[4])
        if key in labeled:
            continue
        labeled.add(key)
        ax.annotate(pt[3], (pt[0], pt[1]),
                    fontsize=7, color='#333', alpha=0.85,
                    xytext=(8, 6), textcoords='offset points',
                    arrowprops=dict(arrowstyle='-', color='#aaa',
                                   lw=0.5))

    ax.set_xlabel('IPC (p50)')
    ax.set_ylabel('Estimated % Memory Bound')
    ax.set_title('IPC vs Memory Boundedness — GC Phases\n'
                 '(bubble size = total cycle weight)')
    ax.legend(framealpha=0.9)
    ax.axhline(y=100, color='#cc3333', linestyle='--', alpha=0.4, linewidth=1)
    ax.text(ax.get_xlim()[1] * 0.98, 103, 'stall > compute', ha='right',
            fontsize=8, color='#cc3333', alpha=0.6)

    fig.tight_layout()
    path = os.path.join(out_dir, 'ipc_vs_membound.png')
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Figure 4: Cross-benchmark phase comparison ─────────────────────────

def plot_cross_benchmark_phases(spans, out_dir):
    """Grouped bar chart comparing key metrics across benchmarks for
    the same GC phases."""
    key_phases = ['major_slice', 'major_sweep', 'major_mark', 'minor',
                  'stw_leader', 'major']
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    # Aggregate
    data = defaultdict(lambda: defaultdict(lambda: {
        'ipcs': [], 'cycles': 0, 'llc': 0, 'instr': 0
    }))
    for s in spans:
        if s.get('cycles', 0) <= 0 or s['phase'] not in key_phases:
            continue
        d = data[s['phase']][s['benchmark']]
        if s.get('ipc'):
            d['ipcs'].append(s['ipc'])
        d['cycles'] += s['cycles']
        d['llc'] += s.get('llc_misses', 0)
        d['instr'] += s.get('instructions', 0)

    fig, axes = plt.subplots(1, 2, figsize=(15, 7))
    x = np.arange(len(key_phases))
    width = 0.18
    offsets = np.array([-1.5, -0.5, 0.5, 1.5]) * width

    # Panel A: IPC p50
    ax = axes[0]
    all_ipc_vals = {}
    for i, bench in enumerate(benchmarks):
        vals = []
        for phase in key_phases:
            d = data[phase][bench]
            vals.append(pct(d['ipcs'], 0.5) if d['ipcs'] else 0)
        bars = ax.bar(x + offsets[i], vals, width * 0.9,
                      color=COLORS[bench], alpha=0.8, label=SHORT[bench])
        for j, (bar, val) in enumerate(zip(bars, vals)):
            all_ipc_vals.setdefault(j, []).append((val, bar))
    # Label only the tallest bar in each group
    for j in range(len(key_phases)):
        pairs = all_ipc_vals.get(j, [])
        if pairs:
            max_val, max_bar = max(pairs, key=lambda p: p[0])
            min_val, min_bar = min(pairs, key=lambda p: p[0])
            if max_val > 0:
                ax.text(max_bar.get_x() + max_bar.get_width() / 2,
                        max_bar.get_height() + 0.05, f'{max_val:.1f}',
                        ha='center', va='bottom', fontsize=7.5, color='#333',
                        fontweight='bold')
            if min_val > 0 and min_val != max_val:
                ax.text(min_bar.get_x() + min_bar.get_width() / 2,
                        min_bar.get_height() + 0.05, f'{min_val:.1f}',
                        ha='center', va='bottom', fontsize=7.5, color='#888')
    ax.set_xticks(x)
    ax.set_xticklabels([p.replace('_', '\n') for p in key_phases], fontsize=9)
    ax.set_ylabel('IPC (p50)')
    ax.set_title('IPC by Phase')
    ax.legend(fontsize=9, framealpha=0.9)
    ax.set_ylim(bottom=0)

    # Panel B: LLC misses per kinst
    ax = axes[1]
    all_llc_vals = {}
    for i, bench in enumerate(benchmarks):
        vals = []
        for phase in key_phases:
            d = data[phase][bench]
            kinst = d['instr'] / 1000 if d['instr'] else 1
            vals.append(d['llc'] / kinst)
        bars = ax.bar(x + offsets[i], vals, width * 0.9,
                      color=COLORS[bench], alpha=0.8, label=SHORT[bench])
        for j, (bar, val) in enumerate(zip(bars, vals)):
            all_llc_vals.setdefault(j, []).append((val, bar))
    # Label only the tallest bar in each group
    for j in range(len(key_phases)):
        pairs = all_llc_vals.get(j, [])
        if pairs:
            max_val, max_bar = max(pairs, key=lambda p: p[0])
            if max_val > 0.5:
                ax.text(max_bar.get_x() + max_bar.get_width() / 2,
                        max_bar.get_height() + 0.2, f'{max_val:.1f}',
                        ha='center', va='bottom', fontsize=7.5, color='#333',
                        fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([p.replace('_', '\n') for p in key_phases], fontsize=9)
    ax.set_ylabel('LLC Misses / 1000 instructions')
    ax.set_title('LLC Miss Rate by Phase')
    ax.legend(fontsize=9, framealpha=0.9)
    ax.set_ylim(bottom=0)

    fig.suptitle('Cross-Benchmark Comparison — Key GC Phases',
                 fontsize=14, fontweight='bold', y=1.01)
    fig.tight_layout()
    path = os.path.join(out_dir, 'cross_benchmark_phases.png')
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Figure 5: IPC spread (variability) dot-range chart ─────────────────

def plot_ipc_spread(spans, out_dir):
    """Dot-range chart showing IPC p5 → p50 → p95 for top phases."""
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    key_data = defaultdict(lambda: {'ipcs': [], 'cycles': 0})
    for s in spans:
        if s.get('cycles', 0) <= 0 or s['phase'].startswith('bench:'):
            continue
        k = (s['benchmark'], s['phase'])
        d = key_data[k]
        if s.get('ipc'):
            d['ipcs'].append(s['ipc'])
        d['cycles'] += s['cycles']

    # Top 20 by spread (with enough data)
    rows = []
    for (bench, phase), d in key_data.items():
        if len(d['ipcs']) < 20:
            continue
        p5 = pct(d['ipcs'], 0.05)
        p50 = pct(d['ipcs'], 0.50)
        p95 = pct(d['ipcs'], 0.95)
        spread = p95 / p5 if p5 > 0 else 0
        rows.append({'bench': bench, 'phase': phase,
                     'p5': p5, 'p50': p50, 'p95': p95,
                     'spread': spread, 'cycles': d['cycles']})

    rows.sort(key=lambda r: r['spread'], reverse=True)
    rows = rows[:20]

    fig, ax = plt.subplots(figsize=(12, 8))
    y = np.arange(len(rows))

    for i, r in enumerate(rows):
        color = COLORS.get(r['bench'], '#888')
        # Range line p5 → p95
        ax.plot([r['p5'], r['p95']], [i, i], color=color, linewidth=2,
                alpha=0.4, solid_capstyle='round')
        # p5 and p95 dots
        ax.scatter([r['p5']], [i], color=color, s=30, zorder=5, alpha=0.7)
        ax.scatter([r['p95']], [i], color=color, s=30, zorder=5, alpha=0.7)
        # p50 diamond
        ax.scatter([r['p50']], [i], color=color, s=80, zorder=6, marker='D',
                   edgecolors='white', linewidth=0.8)
        # Spread label
        ax.text(r['p95'] + 0.08, i, f'{r["spread"]:.1f}x',
                va='center', fontsize=8, color='#555')

    labels = [f'{SHORT.get(r["bench"], r["bench"])}  {r["phase"]}'
              for r in rows]
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel('Instructions Per Cycle')
    ax.set_title('IPC Variability — Top 20 Most Variable (benchmark, phase) Pairs\n'
                 'Diamond = p50, endpoints = p5 / p95, label = spread ratio')
    ax.invert_yaxis()
    ax.set_xlim(left=0)

    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    legend_elems = [Patch(facecolor=COLORS[b], label=SHORT[b])
                    for b in benchmarks]
    legend_elems.append(Line2D([0], [0], marker='D', color='#888',
                               markersize=7, linestyle='None',
                               label='p50 (median)'))
    ax.legend(handles=legend_elems, loc='lower right', framealpha=0.9,
              fontsize=9)

    fig.tight_layout()
    path = os.path.join(out_dir, 'ipc_spread.png')
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Figure 6: Application spans comparison ─────────────────────────────

def plot_app_spans(spans, out_dir):
    """Grouped bar chart of application-level span IPC and memory boundedness."""
    benchmarks = sorted(set(s['benchmark'] for s in spans))

    app_data = defaultdict(lambda: {
        'ipcs': [], 'cycles': 0, 'l2': 0, 'llc': 0, 'dtlb': 0, 'instr': 0
    })
    for s in spans:
        if s.get('cycles', 0) <= 0 or not s['phase'].startswith('bench:'):
            continue
        k = (s['benchmark'], s['phase'])
        d = app_data[k]
        if s.get('ipc'):
            d['ipcs'].append(s['ipc'])
        d['cycles'] += s['cycles']
        d['l2'] += s.get('l2_misses', 0)
        d['llc'] += s.get('llc_misses', 0)
        d['dtlb'] += s.get('dtlb_load_misses', 0) + s.get('dtlb_store_misses', 0)
        d['instr'] += s.get('instructions', 0)

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    for idx, bench in enumerate(benchmarks):
        ax = axes[idx // 2][idx % 2]
        bench_items = [(phase, d) for (b, phase), d in app_data.items()
                       if b == bench]
        bench_items.sort(key=lambda x: pct(x[1]['ipcs'], 0.5) if x[1]['ipcs'] else 0)
        phase_labels = [p.replace('bench:', '') for p, _ in bench_items]
        ipcs = [pct(d['ipcs'], 0.5) if d['ipcs'] else 0 for _, d in bench_items]
        mem_pcts = []
        for _, d in bench_items:
            stall = (d['l2'] * L2_MISS_COST + d['llc'] * LLC_MISS_COST +
                     d['dtlb'] * DTLB_MISS_COST)
            mem_pcts.append(stall / d['cycles'] * 100 if d['cycles'] else 0)

        y = np.arange(len(phase_labels))
        color = COLORS.get(bench, '#888')

        bars = ax.barh(y, ipcs, color=color, alpha=0.8, edgecolor='white')

        # Annotate with mem bound %
        for i, (ipc, mem) in enumerate(zip(ipcs, mem_pcts)):
            ax.text(ipc + 0.05, i, f'{mem:.0f}% mem',
                    va='center', fontsize=8, color='#666')

        ax.set_yticks(y)
        ax.set_yticklabels(phase_labels, fontsize=9)
        ax.set_xlabel('IPC (p50)')
        ax.set_title(SHORT[bench], color=color, fontweight='bold')
        ax.set_xlim(0, max(ipcs) * 1.35 if ipcs else 1)

    fig.suptitle('Application-Level Spans — IPC with Memory Boundedness',
                 fontsize=14, fontweight='bold')
    fig.tight_layout()
    path = os.path.join(out_dir, 'app_spans.png')
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Figure 7: Overhead comparison ──────────────────────────────────────

def plot_overhead(overhead_dir, out_dir):
    """Bar chart with error bars showing overhead measurements."""
    benchmarks = ['htmlStream_bench', 'stre_bench', 'network_bench',
                  'gzip_bench']

    def load_times(bench, mode):
        path = os.path.join(overhead_dir, f'{bench}_{mode}.times')
        if not os.path.exists(path):
            return []
        return [float(x) for x in open(path).read().strip().split('\n')]

    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(benchmarks))
    width = 0.25

    mode_colors = {'baseline': '#888888', 'events': '#4477aa', 'pmc': '#cc5533'}
    mode_labels = {'baseline': 'Baseline', 'events': 'Events Only',
                   'pmc': 'Events + PMC'}

    for i, mode in enumerate(['baseline', 'events', 'pmc']):
        means = []
        stds = []
        for bench in benchmarks:
            times = load_times(bench, mode)
            means.append(np.mean(times) if times else 0)
            stds.append(np.std(times, ddof=1) if len(times) > 1 else 0)
        bars = ax.bar(x + (i - 1) * width, means, width * 0.85,
                      yerr=stds, capsize=3,
                      color=mode_colors[mode], alpha=0.8,
                      label=mode_labels[mode],
                      error_kw={'linewidth': 1, 'color': '#555'})

    ax.set_xticks(x)
    ax.set_xticklabels([SHORT[b] for b in benchmarks])
    ax.set_ylabel('Wall-clock Time (seconds)')
    ax.set_title('Runtime Overhead — Baseline vs Events vs Events+PMC\n'
                 '(20 iterations, interleaved, error bars = 1 stddev)')
    ax.legend(framealpha=0.9)
    ax.set_ylim(bottom=0)

    # Add overhead % annotations
    for bench_idx, bench in enumerate(benchmarks):
        base_times = load_times(bench, 'baseline')
        pmc_times = load_times(bench, 'pmc')
        if base_times and pmc_times:
            base_mean = np.mean(base_times)
            pmc_mean = np.mean(pmc_times)
            pct_diff = (pmc_mean - base_mean) / base_mean * 100
            ax.text(bench_idx + width, max(pmc_mean, base_mean) * 1.02,
                    f'{pct_diff:+.1f}%', ha='center', fontsize=9,
                    color='#cc5533', fontweight='bold')

    fig.tight_layout()
    path = os.path.join(out_dir, 'overhead.png')
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")
    return path


# ── Main ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <bench-results-dir> "
              f"[overhead-dir] [output-dir]", file=sys.stderr)
        sys.exit(1)

    bench_dir = sys.argv[1]
    overhead_dir = sys.argv[2] if len(sys.argv) > 2 else None
    out_dir = sys.argv[3] if len(sys.argv) > 3 else 'output'

    os.makedirs(out_dir, exist_ok=True)
    print(f"Loading spans from {bench_dir}...")
    spans = load_spans(bench_dir)
    print(f"  {len(spans):,} spans loaded")
    print()
    print("Generating charts:")

    plot_ipc_boxplots(spans, out_dir)
    plot_memory_boundedness(spans, out_dir)
    plot_ipc_vs_membound(spans, out_dir)
    plot_cross_benchmark_phases(spans, out_dir)
    plot_ipc_spread(spans, out_dir)
    plot_app_spans(spans, out_dir)

    if overhead_dir and os.path.isdir(overhead_dir):
        plot_overhead(overhead_dir, out_dir)

    print()
    print(f"Done — {len(os.listdir(out_dir))} files in {out_dir}/")


if __name__ == '__main__':
    main()
