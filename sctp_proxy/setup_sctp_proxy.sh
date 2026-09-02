#!/bin/bash
echo "[setup] Updating package list..."
apt-get update -qq

echo "[setup] Installing libsctp-dev and build tools..."
apt-get install -y -qq libsctp-dev gcc

echo "[setup] Installing Python SCTP bindings..."
pip install --quiet pysctp

echo "[setup] Verifying installation..."
python3 -c "import sctp; print('pysctp OK')" 2>/dev/null || echo "Install check failed"

echo "[setup] Done. You can now run: python3 /app/sctp_proxy.py"
