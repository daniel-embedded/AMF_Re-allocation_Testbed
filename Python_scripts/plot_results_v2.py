#!/usr/bin/env python3
"""
plot_results_v2.py  (v2.1 — adapted to the 7-experiment / orchestrator v7 setup)

Generate publication-quality figures from AMF re-allocation experiments.

Reads:
  - results/<exp>/amf_realloc.json  (output from amf_orchestrator_v7.py)
  - or any JSON list produced by the orchestrator

Produces:
  1. boxplot_comparison.png    — re-allocation latency distribution (per mode)
  2. barplot_per_ue.png        — per-UE reallocation latency (grouped by mode)
  3. cdf_latency.png           — CDF of reallocation latency
  4. timeline_gantt.png        — timeline of UE registrations (cohort-aware)
  5. ip_rotation.png           — Old IP vs. New IP per UE
  6. latency_breakdown.png     — NEW: stacked signaling breakdown per UE
                                 (RRC + registration + session setup) with the
                                 total reallocation latency as a marker; makes
                                 the "signaling vs orchestration overhead"
                                 split directly visible.
  7. post_restart_per_ue.png   — NEW: container-start -> registration latency
                                 per UE (orchestrator v7 populates this in all
                                 restart-based modes).

v2.1 fixes:
  - _ts_to_ms now parses UE log timestamps as UTC explicitly (the orchestrator
    logs in UTC and switch_time_ms is UTC epoch; the old naive parse shifted
    timeline bars by the host timezone offset, e.g. +3 h on UTC-3 hosts).
  - Records with recovered=false or missing metrics are skipped everywhere.
  - Timeline and breakdown are cohort-aware for split-gNB modes: movers carry
    the orchestrator-stamped gnb2_selected field; they are drawn hatched and
    listed in the legend.

All latencies are shown in SECONDS (not ms) for readability, except the
breakdown/post-restart plots which use milliseconds (sub-second signaling).
"""

import json
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Install with: pip install matplotlib numpy")
    sys.exit(1)

# Matplotlib style tweaks for publication quality
plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "legend.fontsize": 9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "figure.figsize": (6, 4),
    "axes.grid": True,
    "grid.alpha": 0.3,
})

MODE_COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2"]


def load_json(path: str) -> List[Dict]:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    return data


def ms_to_s(val):
    """Convert ms to seconds, handling None."""
    return val / 1000.0 if val is not None else None


def is_mover(ue_res: Dict) -> bool:
    """Orchestrator v7.2+ stamps gnb2_selected ONLY on mover records."""
    return "gnb2_selected" in ue_res


def recovered_ues(run: Dict) -> List[Dict]:
    """Only UEs that actually recovered carry meaningful metrics."""
    return [
        u for u in run.get("ue_results", [])
        if u.get("recovered") and u.get("reallocation_latency_ms") is not None
    ]


def extract_mode_data(runs: List[Dict], metric: str = "reallocation_latency_ms"):
    """Return dict: {mode: [values_in_seconds]} (recovered UEs only)."""
    out = {}
    for run in runs:
        mode = run.get("mode", "unknown")
        vals = []
        for ue_res in recovered_ues(run):
            v = ue_res.get(metric)
            if v is not None:
                vals.append(ms_to_s(v))
        out.setdefault(mode, []).extend(vals)
    return out


def extract_per_ue(runs: List[Dict], metric: str = "reallocation_latency_ms"):
    """Return dict: {mode: {ue: value_in_seconds}} (recovered UEs only)."""
    out = {}
    for run in runs:
        mode = run.get("mode", "unknown")
        out[mode] = {}
        for ue_res in recovered_ues(run):
            v = ue_res.get(metric)
            ue = ue_res.get("ue", "unknown")
            if v is not None:
                out[mode][ue] = ms_to_s(v)
    return out


def plot_boxplot_comparison(runs: List[Dict], outdir: Path):
    """Figure 1: Boxplot comparing modes."""
    data = extract_mode_data(runs, "reallocation_latency_ms")
    if not data:
        print("[SKIP] No data for boxplot comparison")
        return

    fig, ax = plt.subplots(figsize=(5, 4))
    labels = list(data.keys())
    values = [data[k] for k in labels]

    bp = ax.boxplot(values, labels=labels, patch_artist=True,
                    showmeans=True, meanline=True,
                    medianprops=dict(color="black", linewidth=1.5))

    for patch, color in zip(bp["boxes"], MODE_COLORS):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax.set_ylabel("Re-allocation Latency (s)")
    ax.set_title("AMF Re-allocation Latency by Mode")
    ax.set_ylim(bottom=0)

    # Add mean labels
    for i, (label, vals) in enumerate(data.items(), start=1):
        if vals:
            mean = np.mean(vals)
            ax.annotate(f"μ={mean:.1f}s", xy=(i, mean), xytext=(i+0.25, mean),
                        fontsize=8, color="darkgreen")

    fig.tight_layout()
    path = outdir / "boxplot_comparison.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_bar_per_ue(runs: List[Dict], outdir: Path):
    """Figure 2: Grouped bar chart per UE."""
    per_ue = extract_per_ue(runs, "reallocation_latency_ms")
    if not per_ue:
        print("[SKIP] No data for per-UE barplot")
        return

    modes = list(per_ue.keys())
    all_ues = sorted({ue for m in per_ue.values() for ue in m.keys()})

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(all_ues))
    width = 0.35

    for i, mode in enumerate(modes):
        vals = [per_ue[mode].get(ue, 0) for ue in all_ues]
        offset = width * (i - len(modes)/2 + 0.5)
        bars = ax.bar(x + offset, vals, width, label=mode.capitalize(),
                      color=MODE_COLORS[i % len(MODE_COLORS)], alpha=0.85)
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                        f"{v:.1f}", ha="center", va="bottom", fontsize=7)

    ax.set_ylabel("Re-allocation Latency (s)")
    ax.set_title("Re-allocation Latency per UE")
    ax.set_xticks(x)
    ax.set_xticklabels(all_ues)
    ax.legend(title="Mode")
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    path = outdir / "barplot_per_ue.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_cdf(runs: List[Dict], outdir: Path):
    """Figure 3: CDF of reallocation latency."""
    data = extract_mode_data(runs, "reallocation_latency_ms")
    if not data:
        print("[SKIP] No data for CDF")
        return

    fig, ax = plt.subplots(figsize=(5.5, 4))

    for i, (mode, vals) in enumerate(data.items()):
        if not vals:
            continue
        sorted_vals = np.sort(vals)
        cdf = np.arange(1, len(sorted_vals) + 1) / len(sorted_vals)
        ax.plot(sorted_vals, cdf, marker="o", markersize=3, label=mode.capitalize(),
                color=MODE_COLORS[i % len(MODE_COLORS)], linewidth=2)

    ax.set_xlabel("Re-allocation Latency (s)")
    ax.set_ylabel("CDF")
    ax.set_title("Cumulative Distribution of Re-allocation Latency")
    ax.legend(loc="lower right")
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.05)
    fig.tight_layout()
    path = outdir / "cdf_latency.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_timeline(runs: List[Dict], outdir: Path):
    """Figure 4: Gantt-like timeline of UE registrations per mode.

    Split-gNB modes are cohort-aware: mover bars (gnb2_selected present) are
    hatched; a solid green edge marks verified gNB2 re-selection, a solid red
    edge marks a fallback to gnb1.
    """
    fig, axes = plt.subplots(len(runs), 1, figsize=(8, 2.5 * len(runs)), squeeze=False)
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    for ax_idx, run in enumerate(runs):
        ax = axes[ax_idx, 0]
        mode = run.get("mode", "unknown")
        switch_ms = run.get("switch_time_ms", 0)
        ue_results = run.get("ue_results", [])

        any_mover = False
        for i, ue_res in enumerate(ue_results):
            ue = ue_res.get("ue", f"ue{i}")
            events = ue_res.get("events", [])
            init_ts = None
            reg_ts = None
            tun_ts = None
            for e in events:
                e_ms = _ts_to_ms(e["timestamp"])
                if e_ms < switch_ms:
                    continue
                if e["event"] == "initial_registration":
                    if init_ts is None or e_ms < init_ts:
                        init_ts = e_ms
                elif e["event"] == "registration_success":
                    if reg_ts is None or e_ms > reg_ts:
                        reg_ts = e_ms
                elif e["event"] == "tun_up":
                    if tun_ts is None or e_ms > tun_ts:
                        tun_ts = e_ms

            if init_ts is None or reg_ts is None:
                continue

            start = (init_ts - switch_ms) / 1000.0
            end = (reg_ts - switch_ms) / 1000.0

            hatch = None
            edge = None
            if is_mover(ue_res):
                any_mover = True
                hatch = "//"
                if ue_res.get("gnb2_selected") is True:
                    edge = "green"
                elif ue_res.get("gnb2_selected") is False:
                    edge = "red"

            bar = ax.barh(i, end - start, left=start, height=0.5,
                          color=colors[i % len(colors)], alpha=0.8,
                          hatch=hatch, edgecolor=edge or "none",
                          linewidth=1.5 if edge else 0.0)
            if tun_ts:
                tun_s = (tun_ts - switch_ms) / 1000.0
                ax.plot(tun_s, i, marker="D", color="red", markersize=5)

        ax.set_yticks(range(len(ue_results)))
        ax.set_yticklabels([r.get("ue", f"ue{i}") for i, r in enumerate(ue_results)])
        ax.set_xlabel("Time since proxy switch (s)")
        ax.set_title(f"Registration Timeline — {mode.capitalize()} Mode")
        ax.set_xlim(left=0)
        ax.invert_yaxis()

        if any_mover:
            legend_items = [
                mpatches.Patch(facecolor="white", edgecolor="black", hatch="//",
                               label="Mover (gnb2-first config)"),
                mpatches.Patch(facecolor="white", edgecolor="green", linewidth=1.5,
                               label="Verified via gNB2"),
                mpatches.Patch(facecolor="white", edgecolor="red", linewidth=1.5,
                               label="Fallback to gNB1"),
            ]
            ax.legend(handles=legend_items, loc="upper left", fontsize=8)

    fig.tight_layout()
    path = outdir / "timeline_gantt.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_ip_rotation(runs: List[Dict], outdir: Path):
    """Figure 5: Table/chart showing IP address rotation."""
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.axis("off")

    rows = []
    for run in runs:
        mode = run.get("mode", "unknown")
        for ue_res in run.get("ue_results", []):
            old = ue_res.get("old_ip") or "—"
            new = ue_res.get("new_ip") or "—"
            changed = "Yes" if ue_res.get("ip_changed") else "No"
            rows.append([mode.capitalize(), ue_res.get("ue", "?"), old, new, changed])

    if not rows:
        print("[SKIP] No IP data for rotation plot")
        return

    table = ax.table(cellText=rows,
                     colLabels=["Mode", "UE", "Old IP", "New IP", "Changed?"],
                     loc="center",
                     cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.2, 1.5)

    for i in range(5):
        table[(0, i)].set_facecolor("#4C78A8")
        table[(0, i)].set_text_props(color="white", weight="bold")

    fig.tight_layout()
    path = outdir / "ip_rotation.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_latency_breakdown(runs: List[Dict], outdir: Path):
    """Figure 6 (NEW): stacked signaling breakdown per UE.

    Stacked (in ms): RRC establishment + registration + session setup.
    Overlaid marker: total reallocation latency (switch -> reg success, in s,
    right-hand axis). The visual gap between the two is exactly the
    orchestration overhead (container restarts, stabilization sleeps) — the
    central thesis-relevant comparison of this setup.
    """
    for run in runs:
        mode = run.get("mode", "unknown")
        ues = recovered_ues(run)
        if not ues:
            continue

        names = [u.get("ue", "?") for u in ues]
        rrc = [u.get("rrc_latency_ms") or 0 for u in ues]
        reg = [u.get("registration_latency_ms") or 0 for u in ues]
        sess = [u.get("session_setup_latency_ms") or 0 for u in ues]
        realloc_s = [u["reallocation_latency_ms"] / 1000.0 for u in ues]

        fig, ax = plt.subplots(figsize=(7, 4))
        x = np.arange(len(names))
        b1 = ax.bar(x, rrc, 0.5, label="RRC establishment", color="#4C78A8", alpha=0.85)
        b2 = ax.bar(x, reg, 0.5, bottom=rrc, label="Registration (NAS)", color="#F58518", alpha=0.85)
        b3 = ax.bar(x, sess, 0.5, bottom=[a+b for a, b in zip(rrc, reg)],
                    label="PDU session setup", color="#54A24B", alpha=0.85)

        ax2 = ax.twinx()
        ax2.plot(x, realloc_s, marker="D", color="black", linestyle="none",
                 markersize=7, label="Total re-allocation (right axis)")
        ax2.set_ylabel("Total Re-allocation Latency (s)")
        ax2.set_ylim(bottom=0)

        ax.set_xticks(x)
        ax.set_xticklabels(names)
        ax.set_ylabel("Signaling Latency (ms)")
        ax.set_title(f"Signaling Breakdown vs Total Re-allocation — {mode.capitalize()}")
        ax.set_ylim(bottom=0)

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)

        fig.tight_layout()
        path = outdir / "latency_breakdown.png"
        fig.savefig(path)
        print(f"[Plot] Saved: {path}")
        plt.close(fig)


def plot_post_restart(runs: List[Dict], outdir: Path):
    """Figure 7 (NEW): container-start -> registration latency per UE.

    Orchestrator v7 anchors restart_time_ms on docker StartedAt, so this is a
    meaningful per-UE metric in every restart-based mode. Modes without
    tracked restarts (e.g. isolate-gnb) are skipped automatically.
    """
    for run in runs:
        mode = run.get("mode", "unknown")
        ues = [
            u for u in recovered_ues(run)
            if u.get("post_restart_latency_ms") is not None
        ]
        if not ues:
            continue

        names = [u.get("ue", "?") for u in ues]
        vals = [u["post_restart_latency_ms"] for u in ues]

        fig, ax = plt.subplots(figsize=(6, 4))
        bars = ax.bar(names, vals, 0.5, color="#4C78A8", alpha=0.85)
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 5,
                    f"{v:.0f}", ha="center", va="bottom", fontsize=8)

        ax.set_ylabel("Post-restart Latency (ms)")
        ax.set_title(f"Container Start → Registration — {mode.capitalize()}")
        ax.set_ylim(bottom=0)
        fig.tight_layout()
        path = outdir / "post_restart_per_ue.png"
        fig.savefig(path)
        print(f"[Plot] Saved: {path}")
        plt.close(fig)


def _ts_to_ms(ts_str: str) -> float:
    """Parse a UE log timestamp as UTC (v2.1 fix).

    UERANSIM logs in UTC and the orchestrator's switch_time_ms is UTC epoch.
    The old naive parse interpreted timestamps in the HOST timezone, shifting
    every timeline bar by the host offset (e.g. +3 h on UTC-3 hosts).
    """
    s = ts_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp() * 1000
        except ValueError:
            continue
    return 0.0


def main():
    parser = argparse.ArgumentParser(description="Plot AMF re-allocation results")
    parser.add_argument("json_path", nargs="?", default="results/amf_realloc.json",
                        help="Path to JSON output from orchestrator")
    parser.add_argument("--outdir", default="results",
                        help="Directory to save plots")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Loading {args.json_path}...")
    runs = load_json(args.json_path)
    print(f"[INFO] Loaded {len(runs)} experiment run(s)")

    plot_boxplot_comparison(runs, outdir)
    plot_bar_per_ue(runs, outdir)
    plot_cdf(runs, outdir)
    plot_timeline(runs, outdir)
    plot_ip_rotation(runs, outdir)
    plot_latency_breakdown(runs, outdir)
    plot_post_restart(runs, outdir)

    print("\n[DONE] All plots generated in:", outdir.resolve())


if __name__ == "__main__":
    main()
