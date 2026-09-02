#!/usr/bin/env python3
"""
plot_results_v3.py  (v3.1)
Produces publication-ready charts optimized for B&W printing (textures/hatches).
Adds cohort-comparison and jitter (CV) plotting capabilities.

v3.1 fixes (for the 7-experiment setup with orchestrator v7.x):
  1. Cohort partitioning in split-gNB experiments no longer relies on the
     `restart_time_ms - switch_time_ms > 100` heuristic. With orchestrator
     v7's accurate restart anchors, EVERY UE (stayers included) restarts
     seconds after the switch, so the old heuristic classified everyone as a
     mover and the cohort plot was meaningless.
     Primary signal is now the orchestrator-stamped `gnb2_selected` field
     (present only on mover records, true/false). Fallback when the field is
     absent (older JSONs): movers are the cohort with the LARGER mean
     reallocation latency — in split modes movers are always restarted after
     stayers by design.
  2. Recovered-only filtering is explicit (ue.get("recovered")) and records
     without reallocation latency are skipped in both cohort and box plots.
  3. Cohort plot is annotated with per-cohort n and with the gnb2_selected
     tally (verified re-selections) when available.
"""

import json
import sys
import argparse
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

# Print-friendly publication styling
plt.rcParams.update({
    "figure.dpi": 300, "savefig.dpi": 300,
    "font.size": 10, "axes.grid": True,
    "grid.alpha": 0.3, "hatch.linewidth": 1.5
})

HATCHES = ['//', '\\\\', '||', '--', 'xx', 'oo']
COLORS = ['#ffffff', '#e0e0e0', '#cccccc', '#999999', '#666666']

def load_json(path):
    with open(path) as f: d = json.load(f)
    return [d] if isinstance(d, dict) else d

def _partition_cohorts(run):
    """Split ue_results into (stayers, movers).

    Primary: orchestrator v7.2+ stamps `gnb2_selected` ONLY on mover records.
    Fallback (older JSONs without the field): split at the median latency —
    movers are restarted strictly after stayers, so they form the high-latency
    cohort. Returns two lists of reallocation latencies in seconds.
    """
    ues = [
        u for u in run.get("ue_results", [])
        if u.get("recovered") and u.get("reallocation_latency_ms") is not None
    ]
    if not ues:
        return [], [], "none"

    if any("gnb2_selected" in u for u in ues):
        movers = [u for u in ues if "gnb2_selected" in u]
        stayers = [u for u in ues if "gnb2_selected" not in u]
        method = "gnb2_selected field"
    else:
        lats = sorted(u["reallocation_latency_ms"] for u in ues)
        median = lats[len(lats) // 2]
        movers = [u for u in ues if u["reallocation_latency_ms"] >= median]
        stayers = [u for u in ues if u["reallocation_latency_ms"] < median]
        method = "latency median fallback (no gnb2_selected field)"

    s = [u["reallocation_latency_ms"] / 1000.0 for u in stayers]
    m = [u["reallocation_latency_ms"] / 1000.0 for u in movers]
    return s, m, method

def plot_cohort_comparison(runs, outdir):
    """Compares the Stayers vs Movers for Split-gNB experiments."""
    split_runs = [r for r in runs if "split" in r.get("mode", "")]
    if not split_runs: return

    for idx, run in enumerate(split_runs):
        mode = run.get("mode")
        fig, ax = plt.subplots(figsize=(6, 4))

        stayers, movers, method = _partition_cohorts(run)
        print(f"  [cohort] {mode}: stayers n={len(stayers)}, movers n={len(movers)} ({method})")
        if not movers:
            print(f"  [WARN] {mode}: no mover cohort identified; skipping plot")
            plt.close(fig)
            continue

        data = [stayers, movers]
        bp = ax.boxplot(
            data,
            labels=[f"Cohort A\n(Stayers, gNB1)\nn={len(stayers)}",
                    f"Cohort B\n(Movers, gNB2)\nn={len(movers)}"],
            patch_artist=True,
        )

        for patch, hatch, color in zip(bp["boxes"], HATCHES[:2], COLORS[:2]):
            patch.set_facecolor(color)
            patch.set_edgecolor('black')
            patch.set_hatch(hatch)

        # Annotate verified re-selections when the field exists
        mover_ues = [u for u in run.get("ue_results", []) if "gnb2_selected" in u]
        if mover_ues:
            n_ok = sum(1 for u in mover_ues if u.get("gnb2_selected"))
            ax.set_title(
                f"Cross-gNB Registration Offset ({mode})\n"
                f"verified via gNB2: {n_ok}/{len(mover_ues)} movers"
            )
        else:
            ax.set_title(f"Cross-gNB Registration Offset ({mode})")

        ax.set_ylabel("Re-allocation Latency (s)")
        fig.tight_layout()
        fig.savefig(outdir / f"cohort_comparison_{idx}.png")
        plt.close(fig)

def plot_boxplot_comparison(runs, outdir):
    data = {}
    for r in runs:
        mode = r.get("mode", "unknown")
        vals = [
            u["reallocation_latency_ms"] / 1000.0
            for u in r.get("ue_results", [])
            if u.get("recovered") and u.get("reallocation_latency_ms") is not None
        ]
        if vals: data.setdefault(mode, []).extend(vals)

    if not data: return
    fig, ax = plt.subplots(figsize=(6, 4))
    bp = ax.boxplot(list(data.values()), labels=list(data.keys()), patch_artist=True)

    for i, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(COLORS[i % len(COLORS)])
        patch.set_edgecolor('black')
        patch.set_hatch(HATCHES[i % len(HATCHES)])

    ax.set_ylabel("Re-allocation Latency (s)")
    ax.set_title("AMF Re-allocation Latency by Mode")
    fig.tight_layout()
    fig.savefig(outdir / "boxplot_comparison.png")
    plt.close(fig)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", default="results/amf_realloc.json", nargs="?")
    parser.add_argument("--outdir", default="results")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    runs = load_json(args.json_path)

    plot_boxplot_comparison(runs, outdir)
    plot_cohort_comparison(runs, outdir)
    print(f"[DONE] Plots generated with print-friendly textures in {outdir}")

if __name__ == "__main__": main()
