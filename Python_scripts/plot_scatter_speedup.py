#!/usr/bin/env python3
"""
plot_scatter_speedup.py
Publication-quality scatter plot: Sequential vs. Concurrent re-allocation latency.

Reads two JSON outputs from amf_orchestrator_v3_corrected.py:
  --sequential results/amf_realloc_sequential.json
  --concurrent results/amf_realloc_concurrent.json

Produces:
  - scatter_speedup.png       : Scatter plot with y=x reference line
  - speedup_per_ue.png        : Bar chart of per-UE speedup

Usage:
  python3 plot_scatter_speedup.py \
      --sequential results/amf_realloc_sequential.json \
      --concurrent results/amf_realloc_concurrent.json \
      --outdir results/
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

try:
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Install with: pip install matplotlib numpy")
    sys.exit(1)

# Publication-quality style
plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 13,
    "legend.fontsize": 10,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.figsize": (6.5, 6),
})


def load_json(path: str) -> Dict:
    with open(path) as f:
        data = json.load(f)
    return data[0] if isinstance(data, list) else data


def extract_realloc_per_ue(run: Dict) -> Dict[str, float]:
    """Return {ue_name: latency_in_seconds}."""
    out = {}
    for ue_res in run.get("ue_results", []):
        ue = ue_res.get("ue", "unknown")
        v = ue_res.get("reallocation_latency_ms")
        if v is not None:
            out[ue] = v / 1000.0
    return out


def plot_scatter(seq_data: Dict[str, float], conc_data: Dict[str, float], outdir: Path):
    """Figure 1: Scatter plot Sequential vs Concurrent with y=x line."""
    all_ues = sorted(set(seq_data.keys()) & set(conc_data.keys()))
    if not all_ues:
        print("[SKIP] No common UEs between sequential and concurrent data")
        return

    seq_vals = [seq_data[ue] for ue in all_ues]
    conc_vals = [conc_data[ue] for ue in all_ues]

    fig, ax = plt.subplots(figsize=(6.5, 6))

    # Determine axis limits (square plot, same scale)
    max_val = max(max(seq_vals), max(conc_vals)) * 1.15
    ax.set_xlim(0, max_val)
    ax.set_ylim(0, max_val)

    # y = x reference line (speedup = 1)
    ax.plot([0, max_val], [0, max_val], "k--", linewidth=1.2, alpha=0.6, label="Speedup = 1 ($y=x$)")

    # Shade region where concurrent wins (below y=x)
    ax.fill_between([0, max_val], [0, 0], [0, max_val], alpha=0.05, color="green", label="Concurrent faster")
    ax.fill_between([0, max_val], [0, max_val], [max_val, max_val], alpha=0.05, color="red", label="Sequential faster")

    # Color map for UEs
    colors = plt.cm.tab10(np.linspace(0, 1, len(all_ues)))

    # Scatter points
    for i, ue in enumerate(all_ues):
        x = seq_data[ue]
        y = conc_data[ue]
        ax.scatter(x, y, s=120, c=[colors[i]], zorder=5, edgecolors="black", linewidths=0.8)
        # Annotate UE name slightly offset
        offset_x = max_val * 0.02
        offset_y = max_val * 0.02
        ax.annotate(ue.upper(), (x, y), xytext=(x + offset_x, y + offset_y),
                    fontsize=10, fontweight="bold", color=colors[i])

    # Annotations: speedup values
    for ue in all_ues:
        x = seq_data[ue]
        y = conc_data[ue]
        speedup = x / y if y != 0 else 0
        # Place speedup text near point, below-left
        ax.annotate(f"{speedup:.2f}×", (x, y),
                    xytext=(x - max_val*0.06, y - max_val*0.06),
                    fontsize=8, color="darkslategray", ha="right", va="top",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.7, edgecolor="none"))

    ax.set_xlabel("Sequential Re-allocation Latency (s)")
    ax.set_ylabel("Concurrent Re-allocation Latency (s)")
    ax.set_title("Sequential vs. Concurrent AMF Re-allocation\n(6 UEs, stagger = 5 s)")
    ax.set_aspect("equal", adjustable="box")
    ax.legend(loc="upper left", framealpha=0.9)

    fig.tight_layout()
    path = outdir / "scatter_speedup.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_speedup_bar(seq_data: Dict[str, float], conc_data: Dict[str, float], outdir: Path):
    """Figure 2: Bar chart of per-UE speedup."""
    all_ues = sorted(set(seq_data.keys()) & set(conc_data.keys()))
    if not all_ues:
        return

    speedups = []
    colors = []
    for ue in all_ues:
        s = seq_data[ue]
        c = conc_data[ue]
        sp = s / c if c != 0 else 0
        speedups.append(sp)
        colors.append("#2ca02c" if sp > 1 else "#d62728")  # green if concurrent wins, red otherwise

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(all_ues))
    bars = ax.bar(x, speedups, color=colors, alpha=0.85, edgecolor="black", linewidth=0.6)

    # Horizontal line at speedup = 1
    ax.axhline(y=1.0, color="black", linestyle="--", linewidth=1.0, label="Speedup = 1")

    # Annotate values on bars
    for bar, sp in zip(bars, speedups):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                f"{sp:.2f}×", ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([u.upper() for u in all_ues])
    ax.set_ylabel("Speedup (Sequential / Concurrent)")
    ax.set_title("Per-UE Speedup: Concurrent over Sequential")
    ax.set_ylim(bottom=0)
    ax.legend()

    fig.tight_layout()
    path = outdir / "speedup_per_ue.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Plot scatter speedup comparison")
    parser.add_argument("--sequential", required=True, help="Path to sequential JSON")
    parser.add_argument("--concurrent", required=True, help="Path to concurrent JSON")
    parser.add_argument("--outdir", default="results", help="Output directory")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    seq = load_json(args.sequential)
    conc = load_json(args.concurrent)

    seq_data = extract_realloc_per_ue(seq)
    conc_data = extract_realloc_per_ue(conc)

    print(f"[INFO] Loaded {len(seq_data)} UEs from sequential, {len(conc_data)} from concurrent")
    print(f"[INFO] Common UEs: {sorted(set(seq_data.keys()) & set(conc_data.keys()))}")

    plot_scatter(seq_data, conc_data, outdir)
    plot_speedup_bar(seq_data, conc_data, outdir)

    print("\n[DONE] Scatter speedup plots generated.")


if __name__ == "__main__":
    main()
