#!/usr/bin/env python3
"""
plot_results_v4.py  (v4.0 — 8-experiment setup, orchestrator v7.5.x)

Unified plotter for AMF re-allocation experiments. Merges:
  - plot_results_v2.py (per-experiment standard plots)
  - plot_results_v3.py (B&W cohort comparison for split modes)
  - plot_scatter_speedup.py / generate_comparison_table_v2.py (cross-experiment)

Per-experiment figures (single JSON):
  1. boxplot_comparison.png   — latency distribution per mode
  2. barplot_per_ue.png       — per-UE latency (NATURAL UE order: ue1, ue2, ...)
  3. cdf_latency.png          — CDF of reallocation latency
  4. timeline_gantt.png       — registration timeline (N14/isolate-gnb aware)
  5. ip_rotation.png          — old vs new IP per UE (adaptive sizing)
  6. latency_breakdown.png    — stacked signaling breakdown + total marker
  7. post_restart_per_ue.png  — container-start -> registration (restart modes only)
  8. cohort_comparison_N.png  — stayers vs movers (split modes only)

Cross-experiment figures (--cross, needs 01_sequential + 02_concurrent):
  - scatter_speedup.png       — per-UE sequential vs concurrent scatter
  - speedup_per_ue.png        — per-UE speedup ratio (seq/conc)
  - comparison_table.csv/.tex — per-mode statistics table
  - comparison_summary.txt    — human-readable summary
  - backforth_drift.png       — with --backforth 03_backforth/amf_realloc.json

v4.0 fixes vs v2/v3:
  * Natural UE sorting everywhere (ue1, ue2, ..., ue10, ... — not lexicographic).
  * timeline_gantt works for n14-mobility and isolate-gnb: bars anchor on the
    first post-switch event of ANY type (initial_registration, rrc_connected,
    mm_registered, registration_request), and close on registration_success OR
    mm_registered/rrc_connected fallback. N14 UEs no longer vanish.
  * Adaptive figure sizing / label density for 12/24/48-UE campaigns.
  * ip_rotation table paginates cleanly at 48 UEs.
  * All skips are explicit ([SKIP] with reason) so silent empty figures are
    impossible.

Usage:
  python3 plot_results_v4.py <exp_json> --outdir <dir>                 # per-exp
  python3 plot_results_v4.py --cross --sequential S --concurrent C \
         [--backforth B] --outdir <dir>                                # cross-exp
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Install with: pip install matplotlib numpy")
    sys.exit(1)

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "legend.fontsize": 9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "axes.grid": True,
    "grid.alpha": 0.3,
})

MODE_COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2"]
HATCHES = ["//", "\\\\", "||", "--", "xx", "oo"]
BW_COLORS = ["#ffffff", "#e0e0e0", "#cccccc", "#999999", "#666666"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> List[Dict]:
    with open(path) as f:
        data = json.load(f)
    return [data] if isinstance(data, dict) else data


def ms_to_s(val):
    return val / 1000.0 if val is not None else None


def ue_sort_key(name: str):
    """Natural sort key: ue1, ue2, ..., ue10, ue11, ... (not lexicographic)."""
    m = re.match(r"^([A-Za-z]*?)(\d+)$", str(name))
    if m:
        return (m.group(1), int(m.group(2)))
    return (str(name), 0)


def natural_sorted(names) -> List[str]:
    return sorted(names, key=ue_sort_key)


def is_mover(ue_res: Dict) -> bool:
    """Orchestrator v7.2+ stamps gnb2_selected ONLY on mover records."""
    return "gnb2_selected" in ue_res


def recovered_ues(run: Dict) -> List[Dict]:
    return [
        u for u in run.get("ue_results", [])
        if u.get("recovered") and u.get("reallocation_latency_ms") is not None
    ]


def extract_mode_data(runs: List[Dict], metric: str = "reallocation_latency_ms"):
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
    """{mode: {ue: value_s}}; multiple runs of same mode are merged (last wins
    per UE, but all UEs across runs are kept)."""
    out: Dict[str, Dict[str, float]] = {}
    for run in runs:
        mode = run.get("mode", "unknown")
        out.setdefault(mode, {})
        for ue_res in recovered_ues(run):
            v = ue_res.get(metric)
            ue = ue_res.get("ue", "unknown")
            if v is not None:
                out[mode][ue] = ms_to_s(v)
    return out


def n_ues_adaptive(n: int) -> Tuple[float, float, int]:
    """(fig_width, font_size, label_rotation) adaptive to UE count."""
    if n <= 8:
        return 7.0, 8, 0
    if n <= 16:
        return 9.0, 7, 45
    if n <= 28:
        return 12.0, 6, 60
    return 15.0, 5, 90


def _ts_to_ms(ts_str: str) -> float:
    """Parse a UE log timestamp as UTC (orchestrator switch_time_ms is UTC)."""
    s = ts_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp() * 1000
        except ValueError:
            continue
    return 0.0


# ---------------------------------------------------------------------------
# Per-experiment figures
# ---------------------------------------------------------------------------

def plot_boxplot_comparison(runs: List[Dict], outdir: Path):
    data = {m: v for m, v in extract_mode_data(runs).items() if v}
    if not data:
        print("[SKIP] boxplot_comparison: no recovered-UE latency data")
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
    for i, vals in enumerate(values, start=1):
        mean = np.mean(vals)
        ax.annotate(f"μ={mean:.1f}s", xy=(i, mean), xytext=(i + 0.25, mean),
                    fontsize=8, color="darkgreen")

    fig.tight_layout()
    path = outdir / "boxplot_comparison.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_bar_per_ue(runs: List[Dict], outdir: Path):
    per_ue = extract_per_ue(runs)
    if not per_ue:
        print("[SKIP] barplot_per_ue: no recovered-UE latency data")
        return

    modes = list(per_ue.keys())
    all_ues = natural_sorted({ue for m in per_ue.values() for ue in m.keys()})

    w, fs, rot = n_ues_adaptive(len(all_ues))
    fig, ax = plt.subplots(figsize=(w, 4))
    x = np.arange(len(all_ues))
    width = 0.8 / max(len(modes), 1)

    for i, mode in enumerate(modes):
        vals = [per_ue[mode].get(ue, 0) for ue in all_ues]
        offset = width * (i - len(modes) / 2 + 0.5)
        bars = ax.bar(x + offset, vals, width, label=mode.capitalize(),
                      color=MODE_COLORS[i % len(MODE_COLORS)], alpha=0.85)
        if len(all_ues) <= 16:  # value labels only when readable
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2,
                            bar.get_height() + 0.3, f"{v:.1f}",
                            ha="center", va="bottom", fontsize=fs - 1)

    ax.set_ylabel("Re-allocation Latency (s)")
    ax.set_title("Re-allocation Latency per UE")
    ax.set_xticks(x)
    ax.set_xticklabels(all_ues, rotation=rot, fontsize=fs)
    ax.legend(title="Mode")
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    path = outdir / "barplot_per_ue.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_cdf(runs: List[Dict], outdir: Path):
    data = {m: v for m, v in extract_mode_data(runs).items() if v}
    if not data:
        print("[SKIP] cdf_latency: no recovered-UE latency data")
        return

    fig, ax = plt.subplots(figsize=(5.5, 4))
    for i, (mode, vals) in enumerate(data.items()):
        sorted_vals = np.sort(vals)
        cdf = np.arange(1, len(sorted_vals) + 1) / len(sorted_vals)
        ax.plot(sorted_vals, cdf, marker="o", markersize=3,
                label=mode.capitalize(),
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


# Events that can OPEN a timeline bar (first post-switch activity of any kind).
START_EVENTS = {"initial_registration", "registration_request",
                "rrc_connected", "mm_registered"}
# Events that CLOSE a timeline bar (recovery confirmed).
END_EVENTS = {"registration_success", "mm_registered", "rrc_connected"}


def plot_timeline(runs: List[Dict], outdir: Path):
    """Gantt-like timeline of UE recovery per mode.

    v4: anchors each bar on the FIRST post-switch event of any type, so modes
    without post-switch initial_registration (n14-mobility: mobility update;
    isolate-gnb: RRC re-establishment) are drawn instead of silently skipped.
    Bar closes on registration_success, falling back to mm_registered /
    rrc_connected (mirrors orchestrator v7.4.10+ recovery detection).
    """
    n_runs = len(runs)
    fig, axes = plt.subplots(n_runs, 1,
                             figsize=(8, max(2.5, 0.35 * max(
                                 len(r.get("ue_results", [])) for r in runs
                             )) * n_runs),
                             squeeze=False)
    colors = plt.cm.tab20(np.linspace(0, 1, 20))
    drew_any = False

    for ax_idx, run in enumerate(runs):
        ax = axes[ax_idx, 0]
        mode = run.get("mode", "unknown")
        switch_ms = run.get("switch_time_ms", 0)
        ue_results = run.get("ue_results", [])
        n_skipped = 0

        any_mover = False
        for i, ue_res in enumerate(ue_results):
            events = ue_res.get("events", [])
            start_ts = None
            end_ts = None
            tun_ts = None
            for e in events:
                e_ms = _ts_to_ms(e.get("timestamp", ""))
                if e_ms < switch_ms:
                    continue
                ev = e.get("event", "")
                if ev in START_EVENTS:
                    if start_ts is None or e_ms < start_ts:
                        start_ts = e_ms
                if ev in END_EVENTS:
                    if end_ts is None or e_ms > end_ts:
                        end_ts = e_ms
                elif ev == "tun_up":
                    if tun_ts is None or e_ms > tun_ts:
                        tun_ts = e_ms

            if end_ts is None:
                n_skipped += 1
                continue
            if start_ts is None:
                start_ts = switch_ms  # N14 fast path: no pre-registration event

            start = (start_ts - switch_ms) / 1000.0
            end = (end_ts - switch_ms) / 1000.0

            hatch = None
            edge = None
            if is_mover(ue_res):
                any_mover = True
                hatch = "//"
                if ue_res.get("gnb2_selected") is True:
                    edge = "green"
                elif ue_res.get("gnb2_selected") is False:
                    edge = "red"

            ax.barh(i, max(end - start, 0.001), left=start, height=0.5,
                    color=colors[i % len(colors)], alpha=0.8,
                    hatch=hatch, edgecolor=edge or "none",
                    linewidth=1.5 if edge else 0.0)
            drew_any = True
            if tun_ts:
                ax.plot((tun_ts - switch_ms) / 1000.0, i, marker="D",
                        color="red", markersize=4)

        ax.set_yticks(range(len(ue_results)))
        ax.set_yticklabels([r.get("ue", f"ue{i}")
                            for i, r in enumerate(ue_results)], fontsize=7)
        ax.set_xlabel("Time since proxy switch (s)")
        title = f"Recovery Timeline — {mode.capitalize()} Mode"
        if n_skipped:
            title += f"  ({n_skipped} UE(s) without post-switch events)"
        ax.set_title(title, fontsize=10)
        ax.set_xlim(left=0)
        ax.invert_yaxis()

        if any_mover:
            legend_items = [
                mpatches.Patch(facecolor="white", edgecolor="black", hatch="//",
                               label="Mover (gnb2-first config)"),
                mpatches.Patch(facecolor="white", edgecolor="green",
                               linewidth=1.5, label="Verified via gNB2"),
                mpatches.Patch(facecolor="white", edgecolor="red",
                               linewidth=1.5, label="Fallback to gNB1"),
            ]
            ax.legend(handles=legend_items, loc="upper left", fontsize=8)

    if not drew_any:
        print("[SKIP] timeline_gantt: no post-switch events in any run")
        plt.close(fig)
        return

    fig.tight_layout()
    path = outdir / "timeline_gantt.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_ip_rotation(runs: List[Dict], outdir: Path):
    rows = []
    for run in runs:
        mode = run.get("mode", "unknown")
        for ue_res in run.get("ue_results", []):
            old = ue_res.get("old_ip") or "—"
            new = ue_res.get("new_ip") or "—"
            changed = "Yes" if ue_res.get("ip_changed") else "No"
            rows.append([mode.capitalize(), ue_res.get("ue", "?"), old, new,
                         changed])

    if not rows:
        print("[SKIP] ip_rotation: no IP data")
        return

    # Keep UE rows in natural order within each mode block
    rows.sort(key=lambda r: (r[0], ue_sort_key(r[1])))

    n = len(rows)
    row_h = 0.32 if n <= 16 else 0.22
    fs = 9 if n <= 16 else (7 if n <= 32 else 6)
    fig, ax = plt.subplots(figsize=(6, max(2.5, (n + 1) * row_h)))
    ax.axis("off")

    table = ax.table(cellText=rows,
                     colLabels=["Mode", "UE", "Old IP", "New IP", "Changed?"],
                     loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(fs)
    table.scale(1.2, 1.3)

    for i in range(5):
        table[(0, i)].set_facecolor("#4C78A8")
        table[(0, i)].set_text_props(color="white", weight="bold")

    # Highlight preserved IPs (session continuity) in green
    for r, row in enumerate(rows, start=1):
        if row[4] == "No":
            table[(r, 4)].set_facecolor("#C8E6C9")
        else:
            table[(r, 4)].set_facecolor("#FFCDD2")

    fig.tight_layout()
    path = outdir / "ip_rotation.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_latency_breakdown(runs: List[Dict], outdir: Path):
    """Stacked signaling breakdown per UE (ms) + total re-allocation (s, right).

    For n14-mobility the RRC/session components are legitimately absent (the
    session is transferred, not re-established) — the smaller stack vs the
    total marker IS the session-continuity story.
    """
    drew = False
    for run in runs:
        mode = run.get("mode", "unknown")
        ues = recovered_ues(run)
        if not ues:
            continue

        ues = sorted(ues, key=lambda u: ue_sort_key(u.get("ue", "?")))
        names = [u.get("ue", "?") for u in ues]
        rrc = [u.get("rrc_latency_ms") or 0 for u in ues]
        reg = [u.get("registration_latency_ms") or 0 for u in ues]
        sess = [u.get("session_setup_latency_ms") or 0 for u in ues]
        realloc_s = [u["reallocation_latency_ms"] / 1000.0 for u in ues]

        w, fs, rot = n_ues_adaptive(len(names))
        fig, ax = plt.subplots(figsize=(w, 4))
        x = np.arange(len(names))
        ax.bar(x, rrc, 0.5, label="RRC establishment", color="#4C78A8",
               alpha=0.85)
        ax.bar(x, reg, 0.5, bottom=rrc, label="Registration (NAS)",
               color="#F58518", alpha=0.85)
        ax.bar(x, sess, 0.5, bottom=[a + b for a, b in zip(rrc, reg)],
               label="PDU session setup", color="#54A24B", alpha=0.85)

        ax2 = ax.twinx()
        ax2.plot(x, realloc_s, marker="D", color="black", linestyle="none",
                 markersize=6, label="Total re-allocation (right axis)")
        ax2.set_ylabel("Total Re-allocation Latency (s)")
        ax2.set_ylim(bottom=0)

        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=rot, fontsize=fs)
        ax.set_ylabel("Signaling Latency (ms)")
        ax.set_title(f"Signaling Breakdown vs Total Re-allocation — "
                     f"{mode.capitalize()}")
        ax.set_ylim(bottom=0)

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)

        fig.tight_layout()
        path = outdir / "latency_breakdown.png"
        fig.savefig(path)
        print(f"[Plot] Saved: {path}")
        plt.close(fig)
        drew = True
    if not drew:
        print("[SKIP] latency_breakdown: no recovered UEs")


def plot_post_restart(runs: List[Dict], outdir: Path):
    """Container-start -> registration latency per UE (restart-based modes).

    Not applicable to n14-mobility (no restarts) or isolate-gnb — skipped
    explicitly with a reason.
    """
    drew = False
    for run in runs:
        mode = run.get("mode", "unknown")
        ues = [u for u in recovered_ues(run)
               if u.get("post_restart_latency_ms") is not None]
        if not ues:
            print(f"[SKIP] post_restart_per_ue: mode '{mode}' has no "
                  f"post_restart_latency_ms (expected for n14-mobility / "
                  f"isolate-gnb)")
            continue

        ues = sorted(ues, key=lambda u: ue_sort_key(u.get("ue", "?")))
        names = [u.get("ue", "?") for u in ues]
        vals = [u["post_restart_latency_ms"] for u in ues]

        w, fs, rot = n_ues_adaptive(len(names))
        fig, ax = plt.subplots(figsize=(w, 4))
        bars = ax.bar(names, vals, 0.5, color="#4C78A8", alpha=0.85)
        if len(names) <= 16:
            for bar, v in zip(bars, vals):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 5, f"{v:.0f}",
                        ha="center", va="bottom", fontsize=fs)

        ax.set_ylabel("Post-restart Latency (ms)")
        ax.set_title(f"Container Start → Registration — {mode.capitalize()}")
        ax.set_ylim(bottom=0)
        ax.tick_params(axis="x", rotation=rot, labelsize=fs)
        fig.tight_layout()
        path = outdir / "post_restart_per_ue.png"
        fig.savefig(path)
        print(f"[Plot] Saved: {path}")
        plt.close(fig)
        drew = True
    if not drew:
        print("[SKIP] post_restart_per_ue: no restart-anchored data in any run")


def _partition_cohorts(run: Dict):
    """Split ue_results into (stayers, movers).

    Primary: orchestrator-stamped `gnb2_selected` (present only on movers).
    Fallback (older JSONs): median-latency split.
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


def plot_cohort_comparison(runs: List[Dict], outdir: Path):
    """Stayers vs movers for split modes (B&W-friendly, print-safe)."""
    split_runs = [r for r in runs if "split" in r.get("mode", "")]
    if not split_runs:
        print("[SKIP] cohort_comparison: no split-mode runs in this JSON")
        return

    for idx, run in enumerate(split_runs):
        mode = run.get("mode")
        fig, ax = plt.subplots(figsize=(6, 4))

        stayers, movers, method = _partition_cohorts(run)
        print(f"  [cohort] {mode}: stayers n={len(stayers)}, "
              f"movers n={len(movers)} ({method})")
        if not movers:
            print(f"  [WARN] {mode}: no mover cohort identified; skipping")
            plt.close(fig)
            continue

        bp = ax.boxplot(
            [stayers, movers],
            labels=[f"Cohort A\n(Stayers, gNB1)\nn={len(stayers)}",
                    f"Cohort B\n(Movers, gNB2)\nn={len(movers)}"],
            patch_artist=True,
        )
        for patch, hatch, color in zip(bp["boxes"], HATCHES[:2], BW_COLORS[:2]):
            patch.set_facecolor(color)
            patch.set_edgecolor("black")
            patch.set_hatch(hatch)

        mover_ues = [u for u in run.get("ue_results", [])
                     if "gnb2_selected" in u]
        if mover_ues:
            n_ok = sum(1 for u in mover_ues if u.get("gnb2_selected"))
            ax.set_title(f"Cross-gNB Registration Offset ({mode})\n"
                         f"verified via gNB2: {n_ok}/{len(mover_ues)} movers")
        else:
            ax.set_title(f"Cross-gNB Registration Offset ({mode})")

        ax.set_ylabel("Re-allocation Latency (s)")
        fig.tight_layout()
        path = outdir / f"cohort_comparison_{idx}.png"
        fig.savefig(path)
        print(f"[Plot] Saved: {path}")
        plt.close(fig)


# ---------------------------------------------------------------------------
# Cross-experiment figures
# ---------------------------------------------------------------------------

def _per_ue_map(json_path: str) -> Dict[str, float]:
    """{ue: latency_s} from a single-mode JSON (last run wins per UE)."""
    out = {}
    for run in load_json(json_path):
        for u in recovered_ues(run):
            out[u.get("ue", "?")] = u["reallocation_latency_ms"] / 1000.0
    return out


def plot_scatter_speedup(seq_json: str, conc_json: str, outdir: Path):
    seq = _per_ue_map(seq_json)
    conc = _per_ue_map(conc_json)
    common = natural_sorted(set(seq) & set(conc))
    if not common:
        print("[SKIP] scatter_speedup: no common UEs between sequential and "
              "concurrent")
        return

    xs = [seq[u] for u in common]
    ys = [conc[u] for u in common]
    lim = max(max(xs), max(ys)) * 1.15

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, lim], [0, lim], linestyle="--", color="gray",
            label="Equal latency (speedup = 1×)")
    ax.scatter(xs, ys, s=50, color="#4C78A8", edgecolors="black", zorder=5)
    if len(common) <= 16:
        for u, x, y in zip(common, xs, ys):
            ax.annotate(u, (x, y), textcoords="offset points",
                        xytext=(5, 5), fontsize=7)

    speedups = [x / y for x, y in zip(xs, ys) if y > 0]
    if speedups:
        ax.set_title(f"Sequential vs Concurrent per UE\n"
                     f"mean speedup (seq/conc): {np.mean(speedups):.2f}×  "
                     f"(n={len(speedups)})")
    ax.set_xlabel("Sequential latency (s)")
    ax.set_ylabel("Concurrent latency (s)")
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    path = outdir / "scatter_speedup.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def plot_speedup_per_ue(seq_json: str, conc_json: str, outdir: Path):
    seq = _per_ue_map(seq_json)
    conc = _per_ue_map(conc_json)
    common = natural_sorted(set(seq) & set(conc))
    if not common:
        print("[SKIP] speedup_per_ue: no common UEs")
        return

    ratios = [seq[u] / conc[u] if conc[u] > 0 else 0 for u in common]
    w, fs, rot = n_ues_adaptive(len(common))

    fig, ax = plt.subplots(figsize=(w, 4))
    bars = ax.bar(common, ratios, 0.6, color="#54A24B", alpha=0.85)
    ax.axhline(1.0, linestyle="--", color="gray", linewidth=1)
    if len(common) <= 16:
        for bar, v in zip(bars, ratios):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                    f"{v:.2f}", ha="center", va="bottom", fontsize=fs)
    ax.set_ylabel("Speedup (sequential / concurrent)")
    ax.set_title("Per-UE Speedup of Concurrent over Sequential Re-allocation")
    ax.tick_params(axis="x", rotation=rot, labelsize=fs)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    path = outdir / "speedup_per_ue.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


def generate_comparison_table(seq_json: str, conc_json: str, outdir: Path,
                              label: str = "tab:comparison"):
    """comparison_table.csv/.tex + comparison_summary.txt."""
    datasets = {"sequential": _per_ue_map(seq_json),
                "concurrent": _per_ue_map(conc_json)}
    rows = []
    for mode, per_ue in datasets.items():
        vals = list(per_ue.values())
        if not vals:
            continue
        rows.append({
            "mode": mode, "n": len(vals),
            "mean": float(np.mean(vals)), "median": float(np.median(vals)),
            "std": float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0,
            "min": float(np.min(vals)), "max": float(np.max(vals)),
        })
    if len(rows) < 2:
        print("[SKIP] comparison_table: need data for both modes")
        return

    speedup = rows[0]["mean"] / rows[1]["mean"] if rows[1]["mean"] > 0 else 0

    # CSV
    csv_path = outdir / "comparison_table.csv"
    with open(csv_path, "w") as f:
        f.write("mode,n,mean_s,median_s,std_s,min_s,max_s\n")
        for r in rows:
            f.write(f"{r['mode']},{r['n']},{r['mean']:.3f},{r['median']:.3f},"
                    f"{r['std']:.3f},{r['min']:.3f},{r['max']:.3f}\n")
    print(f"[Table] Saved: {csv_path}")

    # LaTeX
    tex_path = outdir / "comparison_table.tex"
    with open(tex_path, "w") as f:
        f.write("\\begin{table}[htbp]\n\\centering\n")
        f.write("\\caption{AMF re-allocation latency: sequential vs "
                "concurrent (seconds). Mean speedup of concurrent over "
                f"sequential: {speedup:.2f}$\\\\times$.}}\n")
        f.write(f"\\label{{{label}}}\n")
        f.write("\\begin{tabular}{lrrrrrr}\n\\hline\n")
        f.write("Mode & $n$ & Mean & Median & Std & Min & Max \\\\\n\\hline\n")
        for r in rows:
            f.write(f"{r['mode'].capitalize()} & {r['n']} & {r['mean']:.2f} & "
                    f"{r['median']:.2f} & {r['std']:.2f} & {r['min']:.2f} & "
                    f"{r['max']:.2f} \\\\\n")
        f.write("\\hline\n\\end{tabular}\n\\end{table}\n")
    print(f"[Table] Saved: {tex_path}")

    # TXT summary
    txt_path = outdir / "comparison_summary.txt"
    with open(txt_path, "w") as f:
        f.write("AMF RE-ALLOCATION — SEQUENTIAL vs CONCURRENT SUMMARY\n")
        f.write("=" * 55 + "\n\n")
        for r in rows:
            f.write(f"{r['mode'].capitalize()}:\n")
            f.write(f"  n      = {r['n']} UEs\n")
            f.write(f"  mean   = {r['mean']:.3f} s\n")
            f.write(f"  median = {r['median']:.3f} s\n")
            f.write(f"  std    = {r['std']:.3f} s\n")
            f.write(f"  range  = [{r['min']:.3f}, {r['max']:.3f}] s\n\n")
        f.write(f"Mean speedup (sequential / concurrent): {speedup:.2f}x\n")
    print(f"[Table] Saved: {txt_path}")


def plot_backforth_drift(bf_json: str, outdir: Path):
    runs = load_json(bf_json)
    if not runs or not isinstance(runs, list):
        print("[SKIP] backforth_drift: invalid back-forth JSON")
        return

    x = list(range(1, len(runs) + 1))
    means, makespans = [], []
    for run in runs:
        agg = run.get("aggregate", {})
        rl = agg.get("reallocation_latency_ms", {})
        means.append((rl.get("mean") or 0) / 1000.0)
        max_val = max(
            (r.get("reallocation_latency_ms") or 0)
            for r in run.get("ue_results", [])
        ) / 1000.0
        makespans.append(max_val)

    targets = [run.get("target_amf", "?") for run in runs]
    colors = ["#4C78A8" if t == "amf2" else "#F58518" for t in targets]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(x, means, marker="o", label="Mean latency", color="black",
            linewidth=1.5)
    ax.scatter(x, means, c=colors, s=100, zorder=5, edgecolors="black")
    ax.plot(x, makespans, marker="s", label="Makespan (last UE)", color="gray",
            linestyle="--", linewidth=1.5)

    legend_elements = [
        mpatches.Patch(facecolor="#4C78A8", edgecolor="black",
                       label="Target: AMF2"),
        mpatches.Patch(facecolor="#F58518", edgecolor="black",
                       label="Target: AMF1"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", title="Switch target")
    ax.set_xlabel("Cycle number")
    ax.set_ylabel("Latency (s)")
    ax.set_title("Back-and-Forth Consistency: Latency Drift Across Cycles")
    ax.set_xticks(x)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = outdir / "backforth_drift.png"
    fig.savefig(path)
    print(f"[Plot] Saved: {path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_per_experiment(json_path: str, outdir: Path):
    runs = load_json(json_path)
    print(f"[INFO] Loaded {len(runs)} run(s) from {json_path}")
    plot_boxplot_comparison(runs, outdir)
    plot_bar_per_ue(runs, outdir)
    plot_cdf(runs, outdir)
    plot_timeline(runs, outdir)
    plot_ip_rotation(runs, outdir)
    plot_latency_breakdown(runs, outdir)
    plot_post_restart(runs, outdir)
    plot_cohort_comparison(runs, outdir)
    print(f"[DONE] Per-experiment plots in: {outdir.resolve()}")


def run_cross(args, outdir: Path):
    if args.sequential and args.concurrent:
        plot_scatter_speedup(args.sequential, args.concurrent, outdir)
        plot_speedup_per_ue(args.sequential, args.concurrent, outdir)
        generate_comparison_table(args.sequential, args.concurrent, outdir,
                                  label=args.label)
    else:
        print("[SKIP] cross-experiment: need --sequential and --concurrent")
    if args.backforth:
        plot_backforth_drift(args.backforth, outdir)
    print(f"[DONE] Cross-experiment outputs in: {outdir.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Plot AMF re-allocation results (v4.0)")
    parser.add_argument("json_path", nargs="?", default=None,
                        help="Path to a single-experiment amf_realloc.json")
    parser.add_argument("--outdir", default="results")
    parser.add_argument("--cross", action="store_true",
                        help="Cross-experiment mode (scatter/speedup/table)")
    parser.add_argument("--sequential", default=None)
    parser.add_argument("--concurrent", default=None)
    parser.add_argument("--backforth", default=None)
    parser.add_argument("--label", default="tab:comparison")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.cross:
        run_cross(args, outdir)
    elif args.json_path:
        run_per_experiment(args.json_path, outdir)
    else:
        parser.error("provide a JSON path, or use --cross with "
                     "--sequential/--concurrent")


if __name__ == "__main__":
    main()
