#!/usr/bin/bash
# =============================================================================
# generate_all_plots_v3.sh  (v3.0)
# Batch figure generation for the 8-experiment AMF re-allocation setup.
#
# Uses the unified plotter plot_results_v4.py for everything:
#   - per-experiment figures (boxplot, bar, CDF, timeline, ip_rotation,
#     latency_breakdown, post_restart, cohort comparison for split modes)
#   - cross-experiment figures (scatter_speedup, speedup_per_ue,
#     comparison_table.csv/.tex, comparison_summary.txt, backforth_drift)
#
# Usage:
#   ./generate_all_plots_v3.sh [results_folder]
#
# Examples:
#   ./generate_all_plots_v3.sh                    # uses latest results/ subfolder
#   ./generate_all_plots_v3.sh results/2026.09.01_17-55-09_6ue
# =============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# Locate results root directory
# ---------------------------------------------------------------------------
if [ $# -ge 1 ]; then
    ROOT_DIR="$1"
else
    ROOT_DIR=$(ls -td results/*/ 2>/dev/null | head -n 1)
    if [ -z "$ROOT_DIR" ]; then
        echo "[ERROR] No results/ subfolder found."
        echo "        Run ./run_all_experiments_v4.sh first, or provide a path:"
        echo "        ./generate_all_plots_v3.sh results/<folder>"
        exit 1
    fi
fi

ROOT_DIR="${ROOT_DIR%/}"
if [ ! -d "$ROOT_DIR" ]; then
    echo "[ERROR] Directory not found: $ROOT_DIR"
    exit 1
fi

echo "======================================================================"
echo "  Batch Figure Generation (8-experiment setup, plot_results_v4)"
echo "  Source: $ROOT_DIR"
echo "======================================================================"

PLOT="Python_scripts/plot_results_v4.py"
if [ ! -f "$PLOT" ]; then
    echo "[ERROR] Required script not found: $PLOT"
    echo "        Make sure it is under Python_scripts/ (~/docker-open5gs)."
    exit 1
fi

PLOTS_DIR="$ROOT_DIR/plots"
mkdir -p "$PLOTS_DIR"

run_plot() {
    local desc="$1"
    local cmd="$2"

    echo ""
    echo "[PLOT] $desc"
    eval "$cmd"
    if [ $? -eq 0 ]; then
        echo "       [OK]"
    else
        echo "       [WARN] Something went wrong (non-fatal)."
    fi
}

# ---------------------------------------------------------------------------
# 1) Per-experiment plots — every NN_* folder with an amf_realloc.json
#    (covers all 8 experiments; cohort comparison activates automatically
#    for split modes, post_restart skips itself for n14/isolate-gnb)
# ---------------------------------------------------------------------------
for exp_dir in "$ROOT_DIR"/[0-9][0-9]_*; do
    [ -d "$exp_dir" ] || continue
    json_file="$exp_dir/amf_realloc.json"
    if [ ! -f "$json_file" ]; then
        echo "[SKIP] No amf_realloc.json in $exp_dir"
        continue
    fi

    exp_name=$(basename "$exp_dir")
    exp_plots_dir="$PLOTS_DIR/$exp_name"
    mkdir -p "$exp_plots_dir"

    run_plot "$exp_name — per-experiment plots" \
        "python3 \"$PLOT\" \"$json_file\" --outdir \"$exp_plots_dir\""
done

# ---------------------------------------------------------------------------
# 2) Cross-experiment outputs (sequential vs concurrent + back-forth drift)
# ---------------------------------------------------------------------------
SEQ_JSON="$ROOT_DIR/01_sequential/amf_realloc.json"
CONC_JSON="$ROOT_DIR/02_concurrent/amf_realloc.json"
BF_JSON="$ROOT_DIR/03_backforth/amf_realloc.json"

CROSS_ARGS=""
if [ -f "$SEQ_JSON" ] && [ -f "$CONC_JSON" ]; then
    CROSS_ARGS="--sequential \"$SEQ_JSON\" --concurrent \"$CONC_JSON\""
else
    echo "[SKIP] Cross-experiment: need both 01_sequential and 02_concurrent."
fi
if [ -f "$BF_JSON" ]; then
    CROSS_ARGS="$CROSS_ARGS --backforth \"$BF_JSON\""
else
    echo "[SKIP] Back-forth drift: 03_backforth/amf_realloc.json not found."
fi

if [ -n "$CROSS_ARGS" ]; then
    run_plot "Cross-experiment (scatter, speedup, table, drift)" \
        "python3 \"$PLOT\" --cross $CROSS_ARGS --outdir \"$PLOTS_DIR\" --label tab:comparison"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "  ALL PLOTS GENERATED"
echo "======================================================================"
echo ""
echo "Output directory: $PLOTS_DIR"
echo ""
find "$PLOTS_DIR" -type f \( -name "*.png" -o -name "*.csv" -o -name "*.tex" -o -name "*.txt" \) | sort | while read -r f; do
    rel="${f#$PLOTS_DIR/}"
    size=$(du -h "$f" | cut -f1)
    printf "  %-50s %6s\n" "$rel" "$size"
done
echo ""
echo "======================================================================"
