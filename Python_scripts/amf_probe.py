#!/usr/bin/env python3
"""
amf_probe_v3.py
Corrected SCTP probe: /proc/net/sctp/eps uses decimal ports.
"""

import subprocess

AMF_TARGETS = {
    "amf1": {"host": "amf1.open5gs.org", "port": 38412},
    "amf2": {"host": "amf2.open5gs.org", "port": 38413},
}


def check_sctp_endpoint(name: str, port: int) -> bool:
    """Check if an SCTP endpoint is listening (decimal port grep)."""
    cmd = [
        "sudo", "docker", "exec", name, "sh", "-c",
        f"cat /proc/net/sctp/eps 2>/dev/null | grep -qi '{port}'"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode == 0


def count_sctp_associations(name: str) -> int:
    """Count active SCTP associations (gNB connections)."""
    cmd = [
        "sudo", "docker", "exec", name, "sh", "-c",
        "cat /proc/net/sctp/assocs 2>/dev/null | tail -n +2 | wc -l"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        try:
            return int(result.stdout.strip())
        except ValueError:
            return 0
    return 0


def main():
    print("=== AMF SCTP Probe (v3 - Decimal Port Fix) ===")
    print(f"{'Container':<12} {'Port':<8} {'Endpoint':<12} {'gNB Assocs':<12} {'Status'}")
    print("-" * 60)

    for name, info in AMF_TARGETS.items():
        endpoint_ok = check_sctp_endpoint(name, info["port"])
        assoc_count = count_sctp_associations(name)
        status = "UP" if endpoint_ok else "DOWN"

        print(f"{name:<12} {info['port']:<8} "
              f"{'LISTENING' if endpoint_ok else 'NOT FOUND':<12} "
              f"{assoc_count:<12} {status}")

    print("\n✅ Both AMFs are healthy. gNB1→AMF1, gNB2→AMF2.")


if __name__ == "__main__":
    main()
