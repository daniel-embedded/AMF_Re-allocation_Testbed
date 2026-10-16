#!/usr/bin/env python3
"""
config_inspector.py
Read the Open5GS YAML configs and extract key parameters:
- AMF: GUAMI, TAC, PLMN, NRF URI, S-NSSAI
- SMF: DNN, UPF association, S-NSSAI
- UPF: subnet, NAT

This helps you understand how the NFs are wired together before you write
an orchestrator that manipulates them.
"""

import yaml
from pathlib import Path
from typing import Any

# Adjust this base path to match your repository layout
CONFIG_BASE = Path("configs/network-slicing")


def load_yaml(filename: str) -> dict:
    """Safely load a YAML config file."""
    path = CONFIG_BASE / filename
    if not path.exists():
        print(f"[WARN] {path} not found. Skipping.")
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def summarize_amf(cfg: dict, name: str):
    """Extract AMF key parameters."""
    print(f"\n=== {name} ===")
    amf = cfg.get("amf", {})
    ngap = amf.get("ngap", {}).get("server", [{}])[0]
    sbi = amf.get("sbi", {}).get("server", [{}])[0]
    guami = amf.get("guami", [{}])[0]
    tai = amf.get("tai", [{}])[0]
    plmn = amf.get("plmn_support", [{}])[0]
    nssai = amf.get("network_name", {})

    print(f"  NGAP: {ngap.get('address', 'N/A')}:{ngap.get('port', 38412)}")
    print(f"  SBI:  {sbi.get('address', 'N/A')}:{sbi.get('port', 80)}")
    print(f"  GUAMI: PLMN={guami.get('plmn_id', {})}, AMF ID={guami.get('amf_id', {})}")
    print(f"  TAI:   PLMN={tai.get('plmn_id', {})}, TAC={tai.get('tac', 'N/A')}")
    print(f"  PLMN Support: {plmn.get('plmn_id', {})}")
    print(f"  S-NSSAI: {plmn.get('s_nssai', [])}")
    print(f"  NRF URI: {amf.get('nrf', {}).get('uri', 'N/A')}")


def summarize_smf(cfg: dict, name: str):
    """Extract SMF key parameters."""
    print(f"\n=== {name} ===")
    smf = cfg.get("smf", {})
    sbi = smf.get("sbi", {}).get("server", [{}])[0]
    pfcp = smf.get("pfcp", {}).get("server", [{}])[0]
    gtpc = smf.get("gtpc", {}).get("server", [{}])[0]
    session = smf.get("session", [{}])[0]

    print(f"  SBI:   {sbi.get('address', 'N/A')}:{sbi.get('port', 80)}")
    print(f"  PFCP:  {pfcp.get('address', 'N/A')}:{pfcp.get('port', 8805)}")
    print(f"  GTPC:  {gtpc.get('address', 'N/A')}:{gtpc.get('port', 2123)}")
    print(f"  DNN:   {session.get('dnn', 'N/A')}")
    print(f"  S-NSSAI: {session.get('s_nssai', {})}")

    upfs = smf.get("upf", {}).get("pfcp", [])
    print(f"  UPFs:  {len(upfs)} configured")
    for upf in upfs:
        print(f"    - {upf.get('address', 'N/A')} (DNN: {upf.get('dnn', 'N/A')})")


def summarize_upf(cfg: dict, name: str):
    """Extract UPF key parameters."""
    print(f"\n=== {name} ===")
    upf = cfg.get("upf", {})
    pfcp = upf.get("pfcp", {}).get("server", [{}])[0]
    gtpu = upf.get("gtpu", {}).get("server", [{}])[0]
    session = upf.get("session", [{}])[0]

    print(f"  PFCP: {pfcp.get('address', 'N/A')}:{pfcp.get('port', 8805)}")
    print(f"  GTPU: {gtpu.get('address', 'N/A')}:{gtpu.get('port', 2152)}")
    print(f"  Subnet: {session.get('subnet', 'N/A')}")
    print(f"  NAT: {session.get('nat', 'N/A')}")


def main():
    print("=== Open5GS Configuration Inspector ===")
    print(f"Reading configs from: {CONFIG_BASE.resolve()}")

    for i in (1, 2):
        amf_cfg = load_yaml(f"amf{i}.yaml")
        if amf_cfg:
            summarize_amf(amf_cfg, f"AMF{i}")

    for i in (1, 2):
        smf_cfg = load_yaml(f"smf{i}.yaml")
        if smf_cfg:
            summarize_smf(smf_cfg, f"SMF{i}")

    for i in (1, 2):
        upf_cfg = load_yaml(f"upf{i}.yaml")
        if upf_cfg:
            summarize_upf(upf_cfg, f"UPF{i}")

    print("\n=== Summary ===")
    print("Use this to verify that AMF1/AMF2 serve different TACs or S-NSSAIs,")
    print("and that SMF1/UPF1 and SMF2/UPF2 are paired correctly.")


if __name__ == "__main__":
    main()
