#!/bin/bash
# experiment_runner.sh
# One-shot automated experiment runner for AMF re-allocation
# with Open5GS / UERANSIM.
#
# Usage:
#   bash experiment_runner.sh
#   PROXY_HOST=192.168.1.10 REPEAT=3 bash experiment_runner.sh

set -euo pipefail

PROXY_HOST="${PROXY_HOST:-localhost}"
PROXY_PORT="${PROXY_PORT:-9999}"
OUTPUT_DIR="${OUTPUT_DIR:-./results/$(date +%Y%m%d_%H%M%S)}"
WAIT_BEFORE="${WAIT_BEFORE:-10}"
WAIT_AFTER="${WAIT_AFTER:-60}"
REPEAT="${REPEAT:-1}"
COOLDOWN="${COOLDOWN:-30}"

mkdir -p "$OUTPUT_DIR"

echo "=================================================="
echo "  AMF Re-allocation Experiment Runner"
echo "=================================================="
echo "Proxy:      $PROXY_HOST:$PROXY_PORT"
echo "Output:     $OUTPUT_DIR"
echo "Repeat:     $REPEAT run(s)"
echo "Wait before: ${WAIT_BEFORE}s"
echo "Wait after:  ${WAIT_AFTER}s"
echo "Cooldown:   ${COOLDOWN}s"
echo ""

# Check proxy connectivity
echo "[Check] Verifying proxy control interface..."
python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
try:
    s.sendto(b'STATUS', ('$PROXY_HOST', $PROXY_PORT))
    data, _ = s.recvfrom(1024)
    print(data.decode().strip())
except Exception as e:
    print(f'[ERROR] Proxy unreachable: {e}')
    sys.exit(1)
finally:
    s.close()
" || exit 1

# Check if orchestrator script exists
if [[ ! -f "amf_orchestrator_v2.py" ]]; then
    echo "[ERROR] amf_orchestrator_v2.py not found in current directory"
    exit 1
fi

# Run experiment
echo ""
echo "[Run] Starting experiment..."
python3 amf_orchestrator_v2.py \
    --proxy-host "$PROXY_HOST" \
    --proxy-port "$PROXY_PORT" \
    --command repeat \
    --target amf2 \
    --wait-before "$WAIT_BEFORE" \
    --wait-after "$WAIT_AFTER" \
    --repeat "$REPEAT" \
    --cooldown "$COOLDOWN" \
    --output-dir "$OUTPUT_DIR" \
    --basename "amf_realloc"

echo ""
echo "=================================================="
echo "  Experiment Complete"
echo "  Results: $OUTPUT_DIR"
echo "=================================================="
