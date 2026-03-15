#!/usr/bin/env python3
"""
analyze.py — Analyze PMC benchmark results from JSONL files.

Usage: python3 scripts/analyze.py <results_dir> [--csv] [--plot]

Loads all *.jsonl files from the results directory, groups spans by
benchmark x phase, and produces summary tables for IPC, cache misses,
and TLB misses.
"""

import json
import sys
import os
import argparse
from collections import defaultdict
from pathlib import Path


def load_results(results_dir):
    """Load all JSONL files from results directory."""
    spans = []
    for jsonl_file in sorted(Path(results_dir).glob("*.jsonl")):
        bench_name = jsonl_file.stem.rsplit("_iter", 1)[0]
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    record["benchmark"] = bench_name
                    spans.append(record)
                except json.JSONDecodeError:
                    continue
    return spans


def percentile(values, p):
    """Compute p-th percentile (0-100)."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_v):
        return sorted_v[f]
    return sorted_v[f] + (k - f) * (sorted_v[c] - sorted_v[f])


def aggregate(values):
    """Compute mean, p50, p99 for a list of values."""
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p99": 0.0, "count": 0}
    mean = sum(values) / len(values)
    return {
        "mean": mean,
        "p50": percentile(values, 50),
        "p99": percentile(values, 99),
        "count": len(values),
    }


def group_spans(spans):
    """Group spans by (benchmark, phase) and collect metric lists."""
    groups = defaultdict(lambda: defaultdict(list))
    for s in spans:
        key = (s["benchmark"], s["phase"])
        groups[key]["ipc"].append(s.get("ipc", 0))
        groups[key]["duration_ns"].append(s.get("duration_ns", 0))
        groups[key]["instructions"].append(s.get("instructions", 0))
        groups[key]["cycles"].append(s.get("cycles", 0))
        groups[key]["l2_per_kinst"].append(s.get("l2_per_kinst", 0))
        groups[key]["llc_per_kinst"].append(s.get("llc_per_kinst", 0))
        groups[key]["dtlb_ld_per_kinst"].append(s.get("dtlb_ld_per_kinst", 0))
        groups[key]["dtlb_st_per_kinst"].append(s.get("dtlb_st_per_kinst", 0))
        groups[key]["l2_misses"].append(s.get("l2_misses", 0))
        groups[key]["llc_misses"].append(s.get("llc_misses", 0))
        groups[key]["dtlb_load_misses"].append(s.get("dtlb_load_misses", 0))
        groups[key]["dtlb_store_misses"].append(s.get("dtlb_store_misses", 0))
    return groups


def print_table(title, headers, rows):
    """Print a formatted ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(
        h.ljust(w) for h, w in zip(headers, col_widths)
    ) + " |"

    print(f"\n{title}")
    print(sep)
    print(header_line)
    print(sep)
    for row in rows:
        line = "| " + " | ".join(
            str(cell).ljust(w) for cell, w in zip(row, col_widths)
        ) + " |"
        print(line)
    print(sep)


def table_ipc(groups):
    """Table 1: IPC by benchmark x phase."""
    headers = ["Benchmark", "Phase", "Count", "IPC mean", "IPC p50", "IPC p99"]
    rows = []
    for (bench, phase), metrics in sorted(groups.items()):
        agg = aggregate(metrics["ipc"])
        rows.append([
            bench, phase, agg["count"],
            f"{agg['mean']:.3f}", f"{agg['p50']:.3f}", f"{agg['p99']:.3f}",
        ])
    print_table("Table 1: Instructions Per Cycle (IPC) by Phase", headers, rows)
    return rows


def table_cache(groups):
    """Table 2: Cache misses by benchmark x phase."""
    headers = [
        "Benchmark", "Phase", "Count",
        "L2/kinst mean", "L2/kinst p50",
        "LLC/kinst mean", "LLC/kinst p50",
    ]
    rows = []
    for (bench, phase), metrics in sorted(groups.items()):
        l2 = aggregate(metrics["l2_per_kinst"])
        llc = aggregate(metrics["llc_per_kinst"])
        rows.append([
            bench, phase, l2["count"],
            f"{l2['mean']:.3f}", f"{l2['p50']:.3f}",
            f"{llc['mean']:.3f}", f"{llc['p50']:.3f}",
        ])
    print_table("Table 2: Cache Miss Rates (per 1000 instructions)", headers, rows)
    return rows


def table_tlb(groups):
    """Table 3: TLB misses by benchmark x phase."""
    headers = [
        "Benchmark", "Phase", "Count",
        "dTLB-ld/kinst mean", "dTLB-ld/kinst p50",
        "dTLB-st/kinst mean", "dTLB-st/kinst p50",
    ]
    rows = []
    for (bench, phase), metrics in sorted(groups.items()):
        ld = aggregate(metrics["dtlb_ld_per_kinst"])
        st = aggregate(metrics["dtlb_st_per_kinst"])
        rows.append([
            bench, phase, ld["count"],
            f"{ld['mean']:.4f}", f"{ld['p50']:.4f}",
            f"{st['mean']:.4f}", f"{st['p50']:.4f}",
        ])
    print_table("Table 3: dTLB Miss Rates (per 1000 instructions)", headers, rows)
    return rows


def export_csv(groups, results_dir):
    """Export full aggregated data to CSV."""
    csv_path = os.path.join(results_dir, "summary.csv")
    with open(csv_path, "w") as f:
        f.write(
            "benchmark,phase,count,"
            "ipc_mean,ipc_p50,ipc_p99,"
            "l2_per_kinst_mean,l2_per_kinst_p50,"
            "llc_per_kinst_mean,llc_per_kinst_p50,"
            "dtlb_ld_per_kinst_mean,dtlb_ld_per_kinst_p50,"
            "dtlb_st_per_kinst_mean,dtlb_st_per_kinst_p50,"
            "duration_ns_mean,duration_ns_p50,"
            "instructions_mean,cycles_mean\n"
        )
        for (bench, phase), metrics in sorted(groups.items()):
            ipc = aggregate(metrics["ipc"])
            l2 = aggregate(metrics["l2_per_kinst"])
            llc = aggregate(metrics["llc_per_kinst"])
            dtlb_ld = aggregate(metrics["dtlb_ld_per_kinst"])
            dtlb_st = aggregate(metrics["dtlb_st_per_kinst"])
            dur = aggregate(metrics["duration_ns"])
            inst = aggregate(metrics["instructions"])
            cyc = aggregate(metrics["cycles"])
            f.write(
                f"{bench},{phase},{ipc['count']},"
                f"{ipc['mean']:.4f},{ipc['p50']:.4f},{ipc['p99']:.4f},"
                f"{l2['mean']:.4f},{l2['p50']:.4f},"
                f"{llc['mean']:.4f},{llc['p50']:.4f},"
                f"{dtlb_ld['mean']:.4f},{dtlb_ld['p50']:.4f},"
                f"{dtlb_st['mean']:.4f},{dtlb_st['p50']:.4f},"
                f"{dur['mean']:.0f},{dur['p50']:.0f},"
                f"{inst['mean']:.0f},{cyc['mean']:.0f}\n"
            )
    print(f"\nCSV exported to: {csv_path}")


def plot_results(groups, results_dir):
    """Generate matplotlib charts if available."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available, skipping plots")
        return

    # Bar chart: IPC by phase, grouped by benchmark
    benchmarks = sorted(set(b for b, _ in groups.keys()))
    phases = sorted(set(p for _, p in groups.keys()))

    fig, ax = plt.subplots(figsize=(14, 6))
    x_positions = range(len(phases))
    width = 0.8 / max(len(benchmarks), 1)

    for i, bench in enumerate(benchmarks):
        ipcs = []
        for phase in phases:
            key = (bench, phase)
            if key in groups:
                ipcs.append(aggregate(groups[key]["ipc"])["mean"])
            else:
                ipcs.append(0)
        offset = (i - len(benchmarks) / 2 + 0.5) * width
        ax.bar([x + offset for x in x_positions], ipcs, width, label=bench)

    ax.set_xlabel("GC Phase")
    ax.set_ylabel("IPC (mean)")
    ax.set_title("Instructions Per Cycle by GC Phase")
    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(phases, rotation=45, ha="right", fontsize=8)
    ax.legend(fontsize=8)
    plt.tight_layout()
    chart_path = os.path.join(results_dir, "ipc_by_phase.png")
    plt.savefig(chart_path, dpi=150)
    print(f"Chart saved: {chart_path}")
    plt.close()

    # Scatter: duration vs LLC misses
    fig, ax = plt.subplots(figsize=(10, 6))
    for bench in benchmarks:
        durations = []
        llc_misses = []
        for (b, p), metrics in groups.items():
            if b == bench:
                for d, m in zip(metrics["duration_ns"], metrics["llc_misses"]):
                    durations.append(d / 1e6)  # convert to ms
                    llc_misses.append(m)
        if durations:
            ax.scatter(durations, llc_misses, label=bench, alpha=0.5, s=10)

    ax.set_xlabel("Span duration (ms)")
    ax.set_ylabel("LLC misses")
    ax.set_title("Span Duration vs LLC Misses")
    ax.legend(fontsize=8)
    plt.tight_layout()
    chart_path = os.path.join(results_dir, "duration_vs_llc.png")
    plt.savefig(chart_path, dpi=150)
    print(f"Chart saved: {chart_path}")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Analyze PMC benchmark results")
    parser.add_argument("results_dir", help="Directory containing JSONL result files")
    parser.add_argument("--csv", action="store_true", help="Export summary CSV")
    parser.add_argument("--plot", action="store_true", help="Generate matplotlib charts")
    args = parser.parse_args()

    if not os.path.isdir(args.results_dir):
        print(f"ERROR: {args.results_dir} is not a directory")
        sys.exit(1)

    spans = load_results(args.results_dir)
    if not spans:
        print(f"No data found in {args.results_dir}")
        sys.exit(1)

    print(f"Loaded {len(spans)} spans from {args.results_dir}")

    groups = group_spans(spans)

    # Sanity check IPC values
    all_ipc = [s.get("ipc", 0) for s in spans if s.get("ipc", 0) > 0]
    if all_ipc:
        mean_ipc = sum(all_ipc) / len(all_ipc)
        if mean_ipc < 0.1 or mean_ipc > 5.0:
            print(f"WARNING: Mean IPC = {mean_ipc:.3f} — outside expected range [0.1, 5.0]")
            print("         This may indicate counter misconfiguration.")

    # Check for expected phases
    phases_seen = set(s["phase"] for s in spans)
    expected = {"minor", "major", "major_mark", "major_sweep"}
    missing = expected - phases_seen
    if missing:
        print(f"NOTE: Expected phases not seen: {missing}")

    table_ipc(groups)
    table_cache(groups)
    table_tlb(groups)

    if args.csv:
        export_csv(groups, args.results_dir)

    if args.plot:
        plot_results(groups, args.results_dir)


if __name__ == "__main__":
    main()
