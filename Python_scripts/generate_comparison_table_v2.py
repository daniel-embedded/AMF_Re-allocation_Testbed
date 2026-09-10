#!/usr/bin/env python3
"""
generate_comparison_table.py  (CORRECTED v2)
Reads sequential and concurrent JSON results and produces:
  - comparison_table.csv
  - comparison_table.tex (LaTeX table with speedup)
  - comparison_summary.txt

Usage:
  python3 generate_comparison_table.py \
      --sequential results/amf_realloc_sequential.json \
      --concurrent results/amf_realloc_concurrent.json \
      --outdir results/
"""

import argparse
import json
import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional


def load_json(path: str) -> Dict:
    with open(path) as f:
        data = json.load(f)
    return data[0] if isinstance(data, list) else data


def extract_metric(run: Dict, metric: str) -> Dict[str, float]:
    """Return {ue_name: value_in_seconds} for a given metric."""
    out = {}
    for ue_res in run.get("ue_results", []):
        ue = ue_res.get("ue", "unknown")
        v = ue_res.get(metric)
        if v is not None:
            out[ue] = v / 1000.0  # ms -> s
    return out


def compute_speedup(seq_val: float, conc_val: float) -> float:
    if conc_val == 0:
        return 0.0
    return seq_val / conc_val


def fmt(v: Optional[float], unit: str = "s") -> str:
    if v is None:
        return "N/A"
    if unit == "s":
        return f"{v:.2f}"
    if unit == "ms":
        return f"{v:.0f}"   # FIXED: value already in ms, no extra *1000
    return str(v)


def main():
    parser = argparse.ArgumentParser(description="Generate comparison table with speedup")
    parser.add_argument("--sequential", required=True, help="Path to sequential JSON")
    parser.add_argument("--concurrent", required=True, help="Path to concurrent JSON")
    parser.add_argument("--outdir", default="results", help="Output directory")
    parser.add_argument("--label", default="tab:comparison", help="LaTeX label")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    seq = load_json(args.sequential)
    conc = load_json(args.concurrent)

    # Extract per-UE reallocation latencies
    seq_realloc = extract_metric(seq, "reallocation_latency_ms")
    conc_realloc = extract_metric(conc, "reallocation_latency_ms")

    # Extract registration latencies
    seq_reg = extract_metric(seq, "registration_latency_ms")
    conc_reg = extract_metric(conc, "registration_latency_ms")

    # Extract session setup latencies
    seq_sess = extract_metric(seq, "session_setup_latency_ms")
    conc_sess = extract_metric(conc, "session_setup_latency_ms")

    all_ues = sorted(set(seq_realloc.keys()) | set(conc_realloc.keys()))

    # ------------------------------------------------------------------
    # Build rows
    # ------------------------------------------------------------------
    rows = []
    for ue in all_ues:
        s_r = seq_realloc.get(ue)
        c_r = conc_realloc.get(ue)
        speedup = compute_speedup(s_r, c_r) if (s_r and c_r) else None

        s_reg = seq_reg.get(ue)
        c_reg = conc_reg.get(ue)

        s_sess = seq_sess.get(ue)
        c_sess = conc_sess.get(ue)

        rows.append({
            "UE": ue,
            "seq_realloc_s": s_r,
            "conc_realloc_s": c_r,
            "speedup": speedup,
            "seq_reg_ms": s_reg * 1000 if s_reg else None,
            "conc_reg_ms": c_reg * 1000 if c_reg else None,
            "seq_sess_ms": s_sess * 1000 if s_sess else None,
            "conc_sess_ms": c_sess * 1000 if c_sess else None,
        })

    # Aggregate row
    def _agg(vals: List[float]):
        return {
            "mean": statistics.mean(vals) if vals else None,
            "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
        }

    seq_r_vals = [r["seq_realloc_s"] for r in rows if r["seq_realloc_s"] is not None]
    conc_r_vals = [r["conc_realloc_s"] for r in rows if r["conc_realloc_s"] is not None]
    agg_seq_r = _agg(seq_r_vals)
    agg_conc_r = _agg(conc_r_vals)
    agg_speedup = compute_speedup(agg_seq_r["mean"], agg_conc_r["mean"]) if (agg_seq_r["mean"] and agg_conc_r["mean"]) else None

    seq_reg_vals = [r["seq_reg_ms"] for r in rows if r["seq_reg_ms"] is not None]
    conc_reg_vals = [r["conc_reg_ms"] for r in rows if r["conc_reg_ms"] is not None]
    agg_seq_reg = _agg(seq_reg_vals)
    agg_conc_reg = _agg(conc_reg_vals)

    seq_sess_vals = [r["seq_sess_ms"] for r in rows if r["seq_sess_ms"] is not None]
    conc_sess_vals = [r["conc_sess_ms"] for r in rows if r["conc_sess_ms"] is not None]
    agg_seq_sess = _agg(seq_sess_vals)
    agg_conc_sess = _agg(conc_sess_vals)

    # Makespan = max reallocation latency (time until last UE recovers)
    makespan_seq = max(seq_r_vals) if seq_r_vals else None
    makespan_conc = max(conc_r_vals) if conc_r_vals else None
    makespan_speedup = compute_speedup(makespan_seq, makespan_conc) if (makespan_seq and makespan_conc) else None

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------
    csv_path = outdir / "comparison_table.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "UE", "seq_realloc_s", "conc_realloc_s", "speedup",
            "seq_reg_ms", "conc_reg_ms",
            "seq_sess_ms", "conc_sess_ms"
        ])
        writer.writeheader()
        writer.writerows(rows)
        # Aggregate
        writer.writerow({
            "UE": "MEAN",
            "seq_realloc_s": agg_seq_r.get("mean"),
            "conc_realloc_s": agg_conc_r.get("mean"),
            "speedup": agg_speedup,
            "seq_reg_ms": agg_seq_reg.get("mean"),
            "conc_reg_ms": agg_conc_reg.get("mean"),
            "seq_sess_ms": agg_seq_sess.get("mean"),
            "conc_sess_ms": agg_conc_sess.get("mean"),
        })
        writer.writerow({
            "UE": "MAKESPAN",
            "seq_realloc_s": makespan_seq,
            "conc_realloc_s": makespan_conc,
            "speedup": makespan_speedup,
        })
    print(f"[Export] CSV: {csv_path}")

    # ------------------------------------------------------------------
    # LaTeX
    # ------------------------------------------------------------------
    tex_path = outdir / "comparison_table.tex"
    NL = "\n"
    BS = "\\"
    with open(tex_path, "w") as f:
        f.write(f"{BS}begin{{table}}[ht]{NL}")
        f.write(f"{BS}centering{NL}")
        f.write(f"{BS}caption{{Comparison of Sequential vs. Concurrent AMF Re-allocation (6 UEs)}}{NL}")
        f.write(f"{BS}label{{{args.label}}}{NL}")
        f.write(f"{BS}begin{{tabular}}{{|l|c|c|c|c|c|c|}}{NL}")
        f.write(f"{BS}hline{NL}")
        f.write(f"UE & {BS}multicolumn{{2}}{{c|}}{{Re-allocation Latency (s)}} & Speedup & {BS}multicolumn{{2}}{{c|}}{{Registration (ms)}} {BS}{BS}{NL}")
        f.write(f"     & Sequential & Concurrent & ($\\times$) & Sequential & Concurrent {BS}{BS}{NL}")
        f.write(f"{BS}hline{NL}")

        for r in rows:
            sp = f"{r['speedup']:.2f}" if r['speedup'] is not None else "N/A"
            f.write(
                f"{r['UE']} & "
                f"{fmt(r['seq_realloc_s'])} & {fmt(r['conc_realloc_s'])} & "
                f"{BS}textbf{{{sp}}} & "
                f"{fmt(r['seq_reg_ms'], 'ms')} & {fmt(r['conc_reg_ms'], 'ms')} "
                f"{BS}{BS}{NL}"
            )

        f.write(f"{BS}hline{NL}")
        sp = f"{agg_speedup:.2f}" if agg_speedup is not None else "N/A"
        f.write(
            f"{BS}textbf{{Mean}} & "
            f"{BS}textbf{{{fmt(agg_seq_r.get('mean'))}}} & "
            f"{BS}textbf{{{fmt(agg_conc_r.get('mean'))}}} & "
            f"{BS}textbf{{{BS}textcolor{{blue}}{{{sp}}}}} & "
            f"{fmt(agg_seq_reg.get('mean'), 'ms')} & {fmt(agg_conc_reg.get('mean'), 'ms')} "
            f"{BS}{BS}{NL}"
        )
        f.write(
            f"Std. Dev. & "
            f"{fmt(agg_seq_r.get('std'))} & {fmt(agg_conc_r.get('std'))} & — & "
            f"{fmt(agg_seq_reg.get('std'), 'ms')} & {fmt(agg_conc_reg.get('std'), 'ms')} "
            f"{BS}{BS}{NL}"
        )
        # Makespan row
        ms_sp = f"{makespan_speedup:.2f}" if makespan_speedup is not None else "N/A"
        f.write(
            f"{BS}textbf{{Makespan}} & "
            f"{BS}textbf{{{fmt(makespan_seq)}}} & "
            f"{BS}textbf{{{fmt(makespan_conc)}}} & "
            f"{BS}textbf{{{BS}textcolor{{red}}{{{ms_sp}}}}} & "
            f"— & — {BS}{BS}{NL}"
        )
        f.write(f"{BS}hline{NL}")
        f.write(f"{BS}end{{tabular}}{NL}")
        f.write(f"{BS}end{{table}}{NL}")
    print(f"[Export] LaTeX: {tex_path}")

    # ------------------------------------------------------------------
    # Text summary
    # ------------------------------------------------------------------
    txt_path = outdir / "comparison_summary.txt"
    with open(txt_path, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("AMF Re-allocation: Sequential vs. Concurrent Comparison\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Mean Re-allocation Latency:\n")
        f.write(f"  Sequential: {fmt(agg_seq_r.get('mean'))} s (std={fmt(agg_seq_r.get('std'))} s)\n")
        f.write(f"  Concurrent: {fmt(agg_conc_r.get('mean'))} s (std={fmt(agg_conc_r.get('std'))} s)\n")
        f.write(f"  Speedup:    {agg_speedup:.2f}x\n\n")
        f.write(f"Makespan (time until last UE recovers):\n")
        f.write(f"  Sequential: {fmt(makespan_seq)} s\n")
        f.write(f"  Concurrent: {fmt(makespan_conc)} s\n")
        f.write(f"  Speedup:    {makespan_speedup:.2f}x\n\n")
        f.write(f"Mean Registration Latency:\n")
        f.write(f"  Sequential: {fmt(agg_seq_reg.get('mean'), 'ms')} ms\n")
        f.write(f"  Concurrent: {fmt(agg_conc_reg.get('mean'), 'ms')} ms\n\n")
        f.write(f"Mean Session Setup Latency:\n")
        f.write(f"  Sequential: {fmt(agg_seq_sess.get('mean'), 'ms')} ms\n")
        f.write(f"  Concurrent: {fmt(agg_conc_sess.get('mean'), 'ms')} ms\n\n")
        f.write("Per-UE Speedup:\n")
        for r in rows:
            if r['speedup'] is not None:
                f.write(f"  {r['UE']}: {r['speedup']:.2f}x\n")
    print(f"[Export] Summary: {txt_path}")

    print("\n[DONE] All comparison files generated.")


if __name__ == "__main__":
    main()
