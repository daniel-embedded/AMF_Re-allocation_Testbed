#!/usr/bin/bash
# =============================================================================
# generate_analysis_plots.sh
# Thesis scaling-analysis figures (7 figures + CSVs) from the results/ tree.
#
# Usage:
#   ./generate_analysis_plots.sh                  # scans ./results
#   ./generate_analysis_plots.sh /path/to/results # custom results root
#
# Expects:
#   results/*_<N>ue/NN_<mode>/amf_realloc.json    (campaigns)
#   results/Exp8_<N>ue_*reps/*.json               (Exp 8 repetitions)
# Output:
#   <results-root>/scaling_analysis/fig1..fig7 + CSVs + summary_stats.csv
# =============================================================================

set -uo pipefail

RESULTS_ROOT="${1:-results}"
SCRIPT="Python_scripts/analyze_scaling.py"

if [ ! -f "$SCRIPT" ]; then
    echo "[ERROR] $SCRIPT not found. Place analyze_scaling.py under Python_scripts/."
    exit 1
fi
if [ ! -d "$RESULTS_ROOT" ]; then
    echo "[ERROR] Results root not found: $RESULTS_ROOT"
    exit 1
fi

echo "======================================================================"
echo "  Scaling Analysis Figure Generation"
echo "  Results root: $RESULTS_ROOT"
echo "======================================================================"

python3 "$SCRIPT" --results-root "$RESULTS_ROOT"

echo ""
echo "======================================================================"
echo "  ANALYSIS COMPLETE"
echo "======================================================================"
