#!/bin/bash
# start_network.sh - Phased startup for dual-gNB Open5GS (scalable UE edition)
#
# Usage:
#   ./start_network.sh          # 6 UEs (default, original behavior)
#   ./start_network.sh 12       # any integer 1..7
#   ./start_network.sh all      # all 768 UEs

set -e
cd "$HOME/docker-open5gs"

COMPOSE="docker compose -f compose-files/network-slicing/docker-compose.yaml --env-file=.env"

# ---- UE pool selection ------------------------------------------------------
MAX_UES=768
ARG="${1:-6}"
if [ "$ARG" = "all" ]; then
  UE_COUNT=$MAX_UES
elif [[ "$ARG" =~ ^[0-9]+$ ]] && [ "$ARG" -ge 1 ] && [ "$ARG" -le "$MAX_UES" ]; then
  UE_COUNT=$ARG
else
  echo "[ERROR] Invalid UE count '$ARG' (use an integer 1..$MAX_UES, or 'all')"
  exit 1
fi

UE_LIST=""
for n in $(seq 1 "$UE_COUNT"); do UE_LIST="$UE_LIST ue$n"; done
UE_LIST="${UE_LIST# }"
echo "[CONFIG] UE pool: $UE_COUNT UEs (ue1..ue$UE_COUNT)"

# ---- Phase 1: core network --------------------------------------------------
echo "[Phase 1] Starting core network..."
$COMPOSE up -d amf1 amf2 smf1 smf2 upf1 upf2 nrf pcf udr sctp-proxy sctp-proxy2

echo "[Phase 1a] Waiting for SMF registration (90s)..."
sleep 90

# ---- Phase 2: gNBs ----------------------------------------------------------
echo "[Phase 2] Start BOTH gNBs..."
$COMPOSE up -d gnb1 gnb2

echo "[Phase 2a] Waiting for gNBs to be fully ready..."
for i in {1..60}; do
  if docker logs gnb1 --tail 5 | grep -q "NG Setup procedure is successful" && \
     docker logs gnb2 --tail 5 | grep -q "NG Setup procedure is successful"; then
    echo "  ✓ Both gNBs report NG Setup success"
    break
  fi
  sleep 2
done
sleep 10  # Extra buffer for RRC layer stabilization

# ---- Phase 3: UEs ------------------------------------------------------------
echo "[Phase 3] Starting $UE_COUNT UEs (gnb2 is NOT in depends_on, so it won't auto-start)..."
$COMPOSE up -d $UE_LIST

# ---- Phase 4: registration check (poll until all registered) ----------------
# Timeout scales with pool size: ~3s per UE, min 60s, max 1200s (20 min).
# The old 300s cap silently froze the timeout above 100 UEs — e.g. 236 UEs
# got only ~1.3s/UE and the script gave up while late UEs were still
# registering. 3s/UE held empirically up to 236 UEs; the 1200s cap covers
# 400 UEs and only guards against a truly hung startup.
TIMEOUT=$(( UE_COUNT * 3 ))
[ "$TIMEOUT" -lt 60 ] && TIMEOUT=60
[ "$TIMEOUT" -gt 1200 ] && TIMEOUT=1200

echo "[Phase 4] Checking UE registration (timeout ${TIMEOUT}s)..."
SECONDS=0   # bash builtin: real wall-clock seconds — a poll cycle at 200+ UEs
            # (one docker logs call per UE) takes far longer than the old
            # fixed +5s increment assumed, desyncing the timeout logic.
retried=0
while [ "$SECONDS" -lt "$TIMEOUT" ]; do
  elapsed=$SECONDS
  pending=""
  for ue in $UE_LIST; do
    if ! docker logs "$ue" --tail 20 2>/dev/null | grep -q "Initial Registration is successful"; then
      pending="$pending $ue"
    fi
  done
  pending="${pending# }"
  if [ -z "$pending" ]; then
    echo "  ✓ All $UE_COUNT UEs registered (${elapsed}s)"
    break
  fi
  # After half the timeout, restart stragglers once
  if [ "$elapsed" -ge $(( TIMEOUT / 2 )) ] && [ "$retried" -eq 0 ]; then
    echo "  … restarting stragglers:$pending"
    for ue in $pending; do docker restart "$ue" >/dev/null; done
    retried=1
  fi
  sleep 5
done

if [ -n "$pending" ]; then
  echo "  ✗ Still NOT registered:$pending — check logs manually"
else
  echo "[DONE] Network should be stable ($UE_COUNT UEs registered)."
fi

