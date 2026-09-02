#!/usr/bin/env python3
# =============================================================================
# analyze_scaling.py  (v1.0)
# Thesis figure generation for the AMF re-allocation scaling study.
#
# Reads the results/ tree directly:
#   - campaign folders:   results/*_<N>ue/            (8-experiment runs)
#       inner layout: NN_<mode>/amf_realloc.json
#   - Exp 8 repetitions:  results/Exp8_<N>ue_*reps/   (any *.json with
#       n14-mobility runs inside, nested or not)
#
# Aggregation rules:
#   - end-to-end latency: mean of RUN means; 95% CI across runs (n = runs,
#     not UEs -- per-UE samples within a run are correlated)
#   - distribution shapes (queueing ramp): per-UE data pooled across runs
#   - binary flags (continuity matrix): reported as-is, never averaged
#
# Output: <outdir>/fig1..fig7 PNG (300 dpi) + one CSV per figure + summary CSV
# =============================================================================

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Student-t critical values (two-sided 95%) for small samples
T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36,
       8: 2.31, 9: 2.26, 10: 2.23}

MODES = ["sequential", "concurrent", "backforth", "partial", "isolate-gnb",
         "split-gnb-simultaneous", "split-gnb-sequential", "n14-mobility"]
MODE_LABELS = {
    "sequential": "Sequential",
    "concurrent": "Concurrent",
    "backforth": "Back-forth",
    "partial": "Partial",
    "isolate-gnb": "Isolate-gNB",
    "split-gnb-simultaneous": "Split simult.",
    "split-gnb-sequential": "Split sequent.",
    "n14-mobility": "N14 mobility",
}
FOLDER_TO_MODE = {
    "01_sequential": "sequential",
    "02_concurrent": "concurrent",
    "03_backforth": "backforth",
    "04_partial": "partial",
    "05_isolate_gnb": "isolate-gnb",
    "06_split_gnb_simultaneous": "split-gnb-simultaneous",
    "07_split_gnb_sequential": "split-gnb-sequential",
    "08_n14-mobility": "n14-mobility",
}
TIER_COLORS = {6: "#4C78A8", 12: "#F58518", 24: "#54A24B", 48: "#B279A2"}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_json_runs(path: Path):
    """Load a JSON file containing a run dict or a list of run dicts."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    runs = data if isinstance(data, list) else [data]
    return [r for r in runs if isinstance(r, dict) and "mode" in r]


def discover_campaigns(results_root: Path):
    """{tier: {mode: [runs]}} from campaign folders results/*_<N>ue/."""
    campaigns = {}
    for d in sorted(results_root.iterdir()):
        m = re.search(r"_(\d+)ue$", d.name)
        if not d.is_dir() or not m:
            continue
        tier = int(m.group(1))
        modes = {}
        for exp_dir in sorted(d.iterdir()):
            if not exp_dir.is_dir() or exp_dir.name not in FOLDER_TO_MODE:
                continue
            jf = exp_dir / "amf_realloc.json"
            if jf.exists():
                modes[FOLDER_TO_MODE[exp_dir.name]] = load_json_runs(jf)
        if modes:
            campaigns[tier] = modes
    return campaigns


def discover_exp8_reps(results_root: Path):
    """{tier: [n14 runs]} from results/Exp8_<N>ue_*reps/ folders."""
    reps = {}
    for d in sorted(results_root.iterdir()):
        m = re.search(r"Exp8_(\d+)ue_\d+reps", d.name)
        if not d.is_dir() or not m:
            continue
        tier = int(m.group(1))
        runs = []
        for jf in sorted(d.rglob("*.json")):
            runs.extend(r for r in load_json_runs(jf)
                        if r.get("mode") == "n14-mobility")
        reps.setdefault(tier, []).extend(runs)
    return reps


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------
def run_mean_latency(run):
    vals = [u["reallocation_latency_ms"] for u in run.get("ue_results", [])
            if u.get("reallocation_latency_ms") is not None]
    return float(np.mean(vals)) if vals else None


def ci95(values):
    """(mean, ci) across runs. ci=None if n<2."""
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    m = float(np.mean(values))
    if len(values) < 2:
        return m, None
    s = float(np.std(values, ddof=1))
    t = T95.get(len(values) - 1, 1.96)
    return m, t * s / np.sqrt(len(values))


def save_csv(path, header, rows):
    with open(path, "w") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig1_mechanism_cost(campaigns, outdir):
    """Canonical 6-UE campaign: mean end-to-end latency per mode (bar)."""
    tiers = sorted(campaigns)
    canonical = min(tiers)  # 6-UE campaign
    modes = campaigns[canonical]
    labels, means, colors = [], [], []
    for mode in MODES:
        runs = modes.get(mode, [])
        ms = [run_mean_latency(r) for r in runs]
        ms = [x for x in ms if x is not None]
        if not ms:
            continue
        labels.append(MODE_LABELS[mode])
        means.append(np.mean(ms) / 1000.0)
        colors.append("#B279A2" if mode == "n14-mobility" else "#4C78A8")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    bars = ax.bar(labels, means, color=colors, edgecolor="black")
    for b, v in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}s",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Mean re-allocation latency (s)")
    ax.set_title(f"Re-allocation cost by mechanism ({canonical} UEs, canonical campaign)")
    ax.grid(True, axis="y", alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    fig.tight_layout()
    fig.savefig(outdir / "fig1_mechanism_cost.png", dpi=300)
    plt.close(fig)
    save_csv(outdir / "fig1_mechanism_cost.csv",
             ["mode", "mean_latency_s"], list(zip(labels, [f"{m:.3f}" for m in means])))
    print(f"[OK] fig1_mechanism_cost.png ({canonical}-UE canonical campaign)")


def fig2_scaling_curves(campaigns, exp8_reps, outdir):
    """Mean latency vs UE count, one line per mode; CI where runs > 1.
    Always emits both a linear-Y and a log-Y version."""
    tiers = sorted(campaigns)
    series = {}   # mode -> (xs, ys, cs)
    csv_rows = []
    for mode in MODES:
        xs, ys, cs = [], [], []
        for tier in tiers:
            runs = list(campaigns[tier].get(mode, []))
            if mode == "n14-mobility":
                runs += exp8_reps.get(tier, [])
            ms = [run_mean_latency(r) for r in runs]
            mean, ci = ci95(ms)
            if mean is None:
                continue
            xs.append(tier)
            ys.append(mean / 1000.0)
            cs.append((ci or 0) / 1000.0)
            csv_rows.append([mode, tier, len(ms), f"{mean/1000:.3f}",
                             f"{(ci or 0)/1000:.3f}"])
        if xs:
            series[mode] = (xs, ys, cs)

    for logy, fname in ((False, "fig2_scaling_curves.png"),
                        (True, "fig2_scaling_curves_log.png")):
        fig, ax = plt.subplots(figsize=(8, 5))
        for mode, (xs, ys, cs) in series.items():
            lw = 2.5 if mode == "n14-mobility" else 1.4
            ax.errorbar(xs, ys, yerr=cs, marker="o", capsize=4, linewidth=lw,
                        label=MODE_LABELS[mode])
        ax.set_xlabel("Number of UEs (correlated re-allocation burst)")
        ax.set_ylabel("Mean end-to-end latency (s)" + (", log scale" if logy else ""))
        ax.set_title("Scaling: latency vs. burst size per mechanism (95% CI across runs)")
        ax.set_xticks(tiers)
        if logy:
            ax.set_yscale("log")
            ax.set_ylim(bottom=0.8)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        fig.savefig(outdir / fname, dpi=300)
        plt.close(fig)
        print(f"[OK] {fname}")
    save_csv(outdir / "fig2_scaling_curves.csv",
             ["mode", "ues", "n_runs", "mean_s", "ci95_s"], csv_rows)


def fig3_queueing_ramp(campaigns, exp8_reps, outdir):
    """Per-UE registration-exchange latency vs finish rank (pooled per tier)."""
    fig, ax = plt.subplots(figsize=(8, 5))
    csv_rows = []
    for tier in sorted(campaigns):
        runs = list(campaigns[tier].get("n14-mobility", [])) + exp8_reps.get(tier, [])
        xs, ys = [], []
        for r in runs:
            pts = [(u["reallocation_latency_ms"], u.get("registration_latency_ms"))
                   for u in r.get("ue_results", [])
                   if u.get("reallocation_latency_ms") is not None
                   and u.get("registration_latency_ms") is not None]
            pts.sort()
            n = len(pts)
            for rank, (_, reg) in enumerate(pts, 1):
                xs.append(rank / n)
                ys.append(reg)
                csv_rows.append([tier, rank / n, f"{reg:.0f}"])
        if xs:
            xs, ys = np.array(xs), np.array(ys)
            ax.scatter(xs, ys, s=14, alpha=0.35, color=TIER_COLORS.get(tier),
                       label=f"{tier} UEs ({len(runs)} runs, pooled)")
            # median trend line per rank decile (makes the ramp readable)
            bins = np.clip((xs * 10).astype(int), 0, 9)
            mx = [np.median(xs[bins == b]) for b in range(10) if (bins == b).any()]
            my = [np.median(ys[bins == b]) for b in range(10) if (bins == b).any()]
            ax.plot(mx, my, color=TIER_COLORS.get(tier), linewidth=2.2)
    ax.set_xlabel("Finish rank within run (normalized)")
    ax.set_ylabel("Registration + N14 exchange latency (ms)")
    ax.set_title("N14 queueing ramp: per-UE core-side latency (pooled runs)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "fig3_n14_queueing_ramp.png", dpi=300)
    plt.close(fig)
    save_csv(outdir / "fig3_n14_queueing_ramp.csv",
             ["tier", "norm_rank", "reg_exchange_ms"], csv_rows)
    print("[OK] fig3_n14_queueing_ramp.png")


def fig4_decomposition(campaigns, exp8_reps, outdir):
    """Stacked decomposition: radio wait vs RRC vs registration vs session setup."""
    tiers = sorted(campaigns)
    modes_cmp = ["concurrent", "n14-mobility"]
    fig, axes = plt.subplots(1, len(modes_cmp), figsize=(11, 4.5), sharey=False)
    csv_rows = []
    for ax, mode in zip(axes, modes_cmp):
        radio, rrc, reg, sess = [], [], [], []
        for tier in tiers:
            runs = list(campaigns[tier].get(mode, []))
            if mode == "n14-mobility":
                runs += exp8_reps.get(tier, [])
            vals = []
            for r in runs:
                for u in r.get("ue_results", []):
                    if u.get("reallocation_latency_ms") is None:
                        continue
                    t = u["reallocation_latency_ms"]
                    rg = u.get("registration_latency_ms") or 0
                    rr = u.get("rrc_latency_ms") or 0
                    ss = u.get("session_setup_latency_ms") or 0
                    vals.append((max(t - rg - rr - ss, 0), rr, rg, ss))
            if not vals:
                radio.append(0); rrc.append(0); reg.append(0); sess.append(0)
                continue
            a = np.mean(vals, axis=0) / 1000.0
            radio.append(a[0]); rrc.append(a[1]); reg.append(a[2]); sess.append(a[3])
            csv_rows.append([mode, tier] + [f"{x:.3f}" for x in a])
        x = np.arange(len(tiers))
        ax.bar(x, radio, label="Radio (RLF/reselection/restart)", color="#9ECAE1")
        ax.bar(x, rrc, bottom=radio, label="RRC setup", color="#FDB863")
        ax.bar(x, reg, bottom=np.array(radio) + np.array(rrc),
               label="Registration (+N14)", color="#E08214")
        ax.bar(x, sess, bottom=np.array(radio) + np.array(rrc) + np.array(reg),
               label="PDU session setup", color="#8073AC")
        ax.set_xticks(x)
        ax.set_xticklabels([f"{t}" for t in tiers])
        ax.set_xlabel("UEs")
        ax.set_title(MODE_LABELS[mode])
        ax.grid(True, axis="y", alpha=0.3)
    axes[0].set_ylabel("Mean latency (s)")
    handles, lbls = axes[0].get_legend_handles_labels()
    fig.legend(handles, lbls, loc="upper center", ncol=4, fontsize=8,
               bbox_to_anchor=(0.5, 1.0))
    fig.suptitle("")
    fig.tight_layout(rect=[0, 0, 1, 0.90])
    fig.savefig(outdir / "fig4_latency_decomposition.png", dpi=300)
    plt.close(fig)
    save_csv(outdir / "fig4_latency_decomposition.csv",
             ["mode", "ues", "radio_s", "rrc_s", "registration_s", "session_s"],
             csv_rows)
    print("[OK] fig4_latency_decomposition.png")


def fig5_continuity_matrix(campaigns, outdir):
    """Mode x property matrix (canonical = smallest tier campaign)."""
    canonical = min(sorted(campaigns))
    modes = campaigns[canonical]
    props = ["IP preserved", "Session continuity", "GUTI preserved",
             "N14 transfer", "No collateral"]
    matrix, row_labels = [], []
    for mode in MODES:
        runs = modes.get(mode)
        if not runs:
            continue
        r = runs[0]
        agg = r.get("aggregate", {})
        urs = r.get("ue_results", [])
        n = len(urs)
        ip_ok = agg.get("ip_changed_count", n) == 0
        sess_ok = (agg.get("session_continuity_count") == n) if "session_continuity_count" in agg else False
        guti_ok = (agg.get("mobility_registration_count") == n) if "mobility_registration_count" in agg else False
        n14_ok = bool(r.get("n14_context_transfer"))
        no_collateral = agg.get("collateral_disturbed_count", 0) == 0
        matrix.append([ip_ok, sess_ok, guti_ok, n14_ok, no_collateral])
        row_labels.append(MODE_LABELS[mode])

    fig, ax = plt.subplots(figsize=(7.5, 4))
    arr = np.array(matrix, dtype=float)
    ax.imshow(arr, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(props)))
    ax.set_xticklabels(props, rotation=20, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels)
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            ax.text(j, i, "YES" if arr[i, j] else "no", ha="center", va="center",
                    fontsize=8, color="black")
    ax.set_title(f"Service continuity properties by mechanism ({canonical} UEs)")
    fig.tight_layout()
    fig.savefig(outdir / "fig5_continuity_matrix.png", dpi=300)
    plt.close(fig)
    rows = [[row_labels[i]] + [int(v) for v in matrix[i]] for i in range(len(matrix))]
    save_csv(outdir / "fig5_continuity_matrix.csv", ["mode"] + props, rows)
    print("[OK] fig5_continuity_matrix.png")


def fig6_blast_radius(campaigns, outdir):
    """Partial re-allocation: movers recovered vs collateral disturbed, per tier."""
    tiers = sorted(campaigns)
    movers, collat, kept_ip = [], [], []
    csv_rows = []
    for tier in tiers:
        runs = campaigns[tier].get("partial", [])
        if not runs:
            continue
        r = runs[0]
        agg = r.get("aggregate", {})
        mv = agg.get("recovered_count", 0)
        co = agg.get("collateral_disturbed_count", 0)
        disturbed = [u for u in r.get("ue_results", []) if u.get("disturbed")]
        kept = sum(1 for u in disturbed if not u.get("ip_changed"))
        movers.append(mv); collat.append(co); kept_ip.append(kept)
        csv_rows.append([tier, mv, co, kept])

    x = np.arange(len(tiers))
    w = 0.35
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.bar(x - w / 2, movers, w, label="Targeted movers recovered", color="#4C78A8",
           edgecolor="black")
    ax.bar(x + w / 2, collat, w, label="Collateral-disturbed non-movers",
           color="#F58518", edgecolor="black")
    for xi, (c, k) in enumerate(zip(collat, kept_ip)):
        if c:
            ax.text(xi + w / 2, c + 0.3, f"{k}/{c} kept IP", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{t}" for t in tiers])
    ax.set_xlabel("Total UEs")
    ax.set_ylabel("UE count")
    ax.set_title("Partial re-allocation blast radius (partial mode: half the pool targeted)")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "fig6_partial_blast_radius.png", dpi=300)
    plt.close(fig)
    save_csv(outdir / "fig6_partial_blast_radius.csv",
             ["tier", "movers_recovered", "collateral_disturbed", "collateral_kept_ip"],
             csv_rows)
    print("[OK] fig6_partial_blast_radius.png")


def fig7_backforth_symmetry(campaigns, outdir):
    """Back-forth legs grouped by direction, per tier (mean +/- std)."""
    tiers = sorted(campaigns)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    csv_rows = []
    x = np.arange(len(tiers))
    w = 0.35
    for k, target in enumerate(["amf2", "amf1"]):
        ys, es = [], []
        for tier in tiers:
            runs = [r for r in campaigns[tier].get("backforth", [])
                    if r.get("target_amf") == target]
            ms = [run_mean_latency(r) for r in runs]
            m, ci = ci95(ms)
            ys.append((m or 0) / 1000.0)
            es.append((ci or 0) / 1000.0)
            csv_rows.append([tier, target, len(ms), f"{(m or 0)/1000:.3f}",
                             f"{(ci or 0)/1000:.3f}"])
        ax.bar(x + (k - 0.5) * w, ys, w, yerr=es, capsize=4,
               label=f"Target {target.upper()}",
               color="#4C78A8" if target == "amf2" else "#F58518",
               edgecolor="black")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{t}" for t in tiers])
    ax.set_xlabel("UEs")
    ax.set_ylabel("Mean latency (s)")
    ax.set_title("Back-forth symmetry: switch direction vs. latency (95% CI)")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "fig7_backforth_symmetry.png", dpi=300)
    plt.close(fig)
    save_csv(outdir / "fig7_backforth_symmetry.csv",
             ["tier", "target", "n_runs", "mean_s", "ci95_s"], csv_rows)
    print("[OK] fig7_backforth_symmetry.png")


def summary_table(campaigns, exp8_reps, outdir):
    """Consolidated stats: per mode per tier, mean/CI across runs."""
    rows = []
    for mode in MODES:
        for tier in sorted(campaigns):
            runs = list(campaigns[tier].get(mode, []))
            if mode == "n14-mobility":
                runs += exp8_reps.get(tier, [])
            ms = [run_mean_latency(r) for r in runs]
            m, ci = ci95(ms)
            if m is None:
                continue
            agg0 = runs[0].get("aggregate", {}) if runs else {}
            rows.append([mode, tier, len(ms), f"{m/1000:.3f}",
                         f"{(ci or 0)/1000:.3f}",
                         agg0.get("recovered_count", ""),
                         agg0.get("ip_changed_count", ""),
                         agg0.get("session_continuity_count", "")])
    save_csv(outdir / "summary_stats.csv",
             ["mode", "ues", "n_runs", "mean_s", "ci95_s",
              "recovered", "ip_changed", "session_continuity"], rows)
    print("[OK] summary_stats.csv")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Thesis scaling-analysis figures")
    ap.add_argument("--results-root", default="results",
                    help="Root containing campaign and Exp8-rep folders")
    ap.add_argument("--outdir", default="",
                    help="Output dir (default: <results-root>/scaling_analysis)")
    args = ap.parse_args()

    results_root = Path(args.results_root)
    if not results_root.is_dir():
        print(f"[ERROR] Not a directory: {results_root}")
        sys.exit(1)
    outdir = Path(args.outdir) if args.outdir else results_root / "scaling_analysis"
    outdir.mkdir(parents=True, exist_ok=True)

    campaigns = discover_campaigns(results_root)
    exp8_reps = discover_exp8_reps(results_root)
    if not campaigns:
        print(f"[ERROR] No campaign folders (*_<N>ue) found under {results_root}")
        sys.exit(1)
    print(f"[INFO] Campaigns: " + ", ".join(f"{t} UEs" for t in sorted(campaigns)))
    print(f"[INFO] Exp8 reps: " + (", ".join(
        f"{t} UEs x{len(r)}" for t, r in sorted(exp8_reps.items())) or "none"))

    fig1_mechanism_cost(campaigns, outdir)
    fig2_scaling_curves(campaigns, exp8_reps, outdir)
    fig3_queueing_ramp(campaigns, exp8_reps, outdir)
    fig4_decomposition(campaigns, exp8_reps, outdir)
    fig5_continuity_matrix(campaigns, outdir)
    fig6_blast_radius(campaigns, outdir)
    fig7_backforth_symmetry(campaigns, outdir)
    summary_table(campaigns, exp8_reps, outdir)
    print(f"\n[DONE] All figures and CSVs in: {outdir}")


if __name__ == "__main__":
    main()

