#!/usr/bin/bash
# =============================================================================
# run_all_experiments_v5.sh
# Automated workflow for AMF re-allocation experiments (8 modes), scale-aware.
#
# From v4:
#   - UE count selectable: ./run_all_experiments_v4.sh [label] [ue_count]
#     (default 6; any integer 1..488 matching start_network.sh's pool)
#   - PARTIAL subset = first half of pool; SPLIT-GNB movers = second half
#     (identical to v3 behavior at 6 UEs: ue1-3 / ue4-6)
#   - N14 wait-after scales with pool size
#
# The orchestrator computes new metrics (rrc_latency_ms, post_restart_latency_ms)
# automatically; this script only drives the sequencing.
# =============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ORCHESTRATOR="python3 sctp_proxy/amf_orchestrator_v7.py"
GNB_POOL_SINGLE="gnb1"
GNB_POOL_BOTH="gnb1,gnb2"
WAIT_BEFORE=10
WAIT_AFTER=60
COOLDOWN=30

# ---------------------------------------------------------------------------
# Arguments: [label] [ue_count]
# ---------------------------------------------------------------------------
CUSTOM_LABEL="${1:-}"
UE_COUNT="${2:-6}"
MAX_UES=488

if ! [[ "$UE_COUNT" =~ ^[0-9]+$ ]] || [ "$UE_COUNT" -lt 2 ] || [ "$UE_COUNT" -gt "$MAX_UES" ]; then
    echo "[ERROR] Invalid UE count '$UE_COUNT' (integer 2..$MAX_UES)"
    exit 1
fi

# UE pool: ue1..ueN
UE_POOL=$(seq -f "ue%g" -s, 1 "$UE_COUNT")

# PARTIAL subset: first half (floor) of the pool  -> 6 UEs: ue1,ue2,ue3
HALF=$(( UE_COUNT / 2 ))
PARTIAL_SUBSET=$(seq -f "ue%g" -s, 1 "$HALF")

# SPLIT-GNB movers: second half of the pool       -> 6 UEs: ue4,ue5,ue6
SPLIT_MOVERS=$(seq -f "ue%g" -s, $(( HALF + 1 )) "$UE_COUNT")

# N14 wait-after: 120s base + 2s per UE (6 UEs -> 132s, 48 UEs -> 216s, 192 UEs -> 504s)
N14_WAIT=$(( 120 + UE_COUNT * 2 ))

echo "[CONFIG] UE pool: $UE_COUNT UEs | partial subset: $PARTIAL_SUBSET | split movers: $SPLIT_MOVERS | n14 wait: ${N14_WAIT}s"

# ---------------------------------------------------------------------------
# Determine output root directory
# ---------------------------------------------------------------------------
TIMESTAMP=$(date +"%Y.%m.%d_%H-%M-%S")

if [ -n "$CUSTOM_LABEL" ]; then
    ROOT_DIR="results/${TIMESTAMP}_${CUSTOM_LABEL}_${UE_COUNT}ue"
else
    ROOT_DIR="results/${TIMESTAMP}_${UE_COUNT}ue"
fi
mkdir -p "$ROOT_DIR"

# Subdirectories for each experiment
DIR_SEQ="$ROOT_DIR/01_sequential"
DIR_CONC="$ROOT_DIR/02_concurrent"
DIR_BF="$ROOT_DIR/03_backforth"
DIR_PART="$ROOT_DIR/04_partial"
DIR_GNB="$ROOT_DIR/05_isolate_gnb"
DIR_SPLIT_SIM="$ROOT_DIR/06_split_gnb_simultaneous"
DIR_SPLIT_SEQ="$ROOT_DIR/07_split_gnb_sequential"
DIR_N14="$ROOT_DIR/08_n14-mobility"

for d in "$DIR_SEQ" "$DIR_CONC" "$DIR_BF" "$DIR_PART" "$DIR_GNB" "$DIR_SPLIT_SIM" "$DIR_SPLIT_SEQ" "$DIR_N14"; do
    mkdir -p "$d"
done

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

ensure_amf1() {
    echo "[CHECK] Ensuring proxy is on AMF1..."
    STATUS=$($ORCHESTRATOR --command status --ues "$UE_COUNT" 2>/dev/null || echo "ERROR")
    if echo "$STATUS" | grep -q "Backend: amf1"; then
        echo "[OK] Proxy already on AMF1"
        return 0
    fi
    echo "[INFO] Switching proxy back to AMF1..."
    $ORCHESTRATOR --command switch --target amf1 --ues "$UE_COUNT"
    sleep 3
}

ensure_gnb2_stopped() {
    echo "[CHECK] Ensuring gnb2 is stopped for clean baseline..."
    if sudo docker ps --format "{{.Names}}" | grep -q "^gnb2$"; then
        echo "[INFO] Stopping gnb2..."
        sudo docker stop gnb2 >/dev/null 2>&1
        sleep 2
    fi
    echo "[OK] gnb2 is not running"
}

run_experiment() {
    local name="$1"
    local cmd="$2"
    local outdir="$3"
    local extra_args="${4:-}"

    echo ""
    echo "======================================================================"
    echo "  EXPERIMENT: $name"
    echo "  Output: $outdir"
    echo "======================================================================"

    # Experiments 1–7 all need AMF1 baseline, except back-forth
    if [ "$name" != "backforth" ]; then
        ensure_amf1
    fi

    # Split-gNB experiments need a clean gnb2 state before they begin
    if [[ "$name" == split_gnb* ]]; then
        ensure_gnb2_stopped
    fi

    echo "[WAIT] Stabilizing for ${WAIT_BEFORE}s before $name..."
    sleep "$WAIT_BEFORE"

    eval "$cmd $extra_args --output-dir \"$outdir\" --basename amf_realloc"

    if [ $? -eq 0 ]; then
        echo "[DONE] $name completed. Results in: $outdir"
    else
        echo "[WARN] $name may have encountered an error. Check logs above."
    fi

    echo "[WAIT] Cooldown ${COOLDOWN}s before next experiment..."
    sleep "$COOLDOWN"
}

# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

echo "======================================================================"
echo "  AMF Re-allocation Experiment Workflow v5 (${UE_COUNT} UEs)"
echo "  Root output: $ROOT_DIR"
echo "======================================================================"

# Verify orchestrator exists
if [ ! -f "sctp_proxy/amf_orchestrator_v7.py" ]; then
    echo "[ERROR] sctp_proxy/amf_orchestrator_v7.py not found!"
    echo "        Please ensure the updated orchestrator is in place."
    exit 1
fi

# Verify proxy is reachable
#STATUS=$($ORCHESTRATOR --command status --ues "$UE_COUNT" 2>/dev/null || echo "ERROR")
#if echo "$STATUS" | grep -q "ERROR"; then
#    echo "[ERROR] Cannot reach sctp-proxy. Is it running?"
#    exit 1
#fi

# ---------------------------------------------------------------------------
# 1) SEQUENTIAL (stagger = 1 s)
# ---------------------------------------------------------------------------
run_experiment "SEQUENTIAL (stagger=1s)" \
    "$ORCHESTRATOR --command sequential --target amf2 --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --stagger 1 --ues $UE_COUNT --gnb-pool $GNB_POOL_SINGLE" \
    "$DIR_SEQ"

# ---------------------------------------------------------------------------
# 2) CONCURRENT
# ---------------------------------------------------------------------------
run_experiment "CONCURRENT" \
    "$ORCHESTRATOR --command concurrent --target amf2 --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --ues $UE_COUNT --gnb-pool $GNB_POOL_SINGLE" \
    "$DIR_CONC"

# ---------------------------------------------------------------------------
# 3) BACK-FORTH (4 cycles: AMF2 -> AMF1 -> AMF2 -> AMF1)
# ---------------------------------------------------------------------------
run_experiment "BACK-FORTH (4 cycles)" \
    "$ORCHESTRATOR --command backforth --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --cooldown $COOLDOWN --repeat 10 --ues $UE_COUNT --gnb-pool $GNB_POOL_SINGLE" \
    "$DIR_BF"

# ---------------------------------------------------------------------------
# 4) PARTIAL (first half of the pool restarted)
# ---------------------------------------------------------------------------
run_experiment "PARTIAL (subset: $PARTIAL_SUBSET)" \
    "$ORCHESTRATOR --command partial --target amf2 --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --ue-subset $PARTIAL_SUBSET --ues $UE_COUNT --gnb-pool $GNB_POOL_SINGLE" \
    "$DIR_PART"

# ---------------------------------------------------------------------------
# 5) ISOLATE-GNB (only gNB restarted)
# ---------------------------------------------------------------------------
run_experiment "ISOLATE-GNB" \
    "$ORCHESTRATOR --command isolate-gnb --target amf2 --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --ues $UE_COUNT --gnb-pool $GNB_POOL_SINGLE" \
    "$DIR_GNB"

# ---------------------------------------------------------------------------
# 6) SPLIT-GNB SIMULTANEOUS
#    AMF switch + gNB2 start + restart second-half UEs all together
# ---------------------------------------------------------------------------
run_experiment "SPLIT-GNB SIMULTANEOUS ($SPLIT_MOVERS -> gnb2)" \
    "$ORCHESTRATOR --command split-gnb-simultaneous --target amf2 --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --ues $UE_COUNT --gnb-pool $GNB_POOL_BOTH --ue-subset $SPLIT_MOVERS" \
    "$DIR_SPLIT_SIM"

# ---------------------------------------------------------------------------
# 7) SPLIT-GNB SEQUENTIAL
#    AMF switch first, wait for recovery, then gNB2 phase
# ---------------------------------------------------------------------------
run_experiment "SPLIT-GNB SEQUENTIAL ($SPLIT_MOVERS -> gnb2)" \
    "$ORCHESTRATOR --command split-gnb-sequential --target amf2 --wait-before $WAIT_BEFORE --wait-after $WAIT_AFTER --ues $UE_COUNT --gnb-pool $GNB_POOL_BOTH --ue-subset $SPLIT_MOVERS" \
    "$DIR_SPLIT_SEQ"

# ---------------------------------------------------------------------------
# 8) N14-MOBILITY
#    re-allocation WITHOUT UE container restart through N14 interface
# ---------------------------------------------------------------------------
run_experiment "N14-MOBILITY" \
    "$ORCHESTRATOR --command n14-mobility --target amf2 --wait-after $N14_WAIT --ues $UE_COUNT" \
    "$DIR_N14"

# ---------------------------------------------------------------------------
# Final cleanup
# ---------------------------------------------------------------------------
ensure_amf1
ensure_gnb2_stopped

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "  WORKFLOW COMPLETE (${UE_COUNT} UEs)"
echo "======================================================================"
echo ""
echo "All results saved under: $ROOT_DIR"
echo ""
echo "Subdirectories:"
echo "  01_sequential/              → Sequential mode (stagger=1s)"
echo "  02_concurrent/              → Concurrent mode (all $UE_COUNT UEs)"
echo "  03_backforth/               → Back-forth consistency (4 cycles)"
echo "  04_partial/                 → Partial re-allocation ($PARTIAL_SUBSET)"
echo "  05_isolate_gnb/             → gNB-only restart"
echo "  06_split_gnb_simultaneous/  → Split-gNB simultaneous ($SPLIT_MOVERS)"
echo "  07_split_gnb_sequential/    → Split-gNB sequential ($SPLIT_MOVERS)"
echo "  08_n14-mobility/            → N14-mobility"
echo ""
echo "Each folder contains:"
echo "  amf_realloc.json          (raw data, now with rrc_latency_ms &"
echo "                             post_restart_latency_ms where applicable)"
echo "  amf_realloc.csv           (spreadsheet)"
echo "  amf_realloc.tex           (LaTeX table)"
echo "  amf_realloc_summary.txt   (human-readable summary)"
echo ""
echo "Next step:"
echo "  ./generate_all_plots_v2.sh $ROOT_DIR"
echo ""
echo "======================================================================"
