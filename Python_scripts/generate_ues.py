#!/usr/bin/env python3
# generate_ues.py
import os
from pathlib import Path

N_UE_START = 3      # First new UE number
N_UE_COUNT = 4      # How many to add (creates ue3, ue4, ue5, ue6)
BASE_IMSI = 891     # Last 3 digits of IMSI: 892 was ue2, so start at 893
BASE_IMEI = 804     # Last digit of IMEI

UE_TEMPLATE = """supi: 'imsi-001011234567{imsi_suffix}'
mcc: '001'
mnc: '01'
key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEBA952E4283B54E88E6183CA'
opType: 'OPC'
amf: '8000'
imei: '356938035643{imei_suffix}'
imeiSv: '43708161258161{imei_sv_suffix}'
gnbSearchList:
  - gnb1.ueransim.org
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: 1
      sd: 0x000001
configured-nssai:
  - sst: 1
    sd: 0x000001
  - sst: 1
    sd: 0x000002
default-nssai:
  - sst: 1
    sd: 0x000001
integrity:
  IA1: true
  IA2: true
  IA3: true
ciphering:
  EA1: true
  EA2: true
  EA3: true
integrityMaxRate:
  uplink: 'full'
  downlink: 'full'
"""

COMPOSE_TEMPLATE = """
  {name}:
    container_name: {name}
    image: "ghcr.io/borjis131/ue:v3.2.7"
    command: "-c /UERANSIM/config/{name}.yaml"
    networks:
      open5gs:
        aliases:
          - {name}.ueransim.org
    configs:
      - source: {name}_config
        target: /UERANSIM/config/{name}.yaml
    privileged: true
    cap_add:
      - NET_ADMIN
    depends_on:
      - gnb1
"""

CONFIG_ENTRY_TEMPLATE = """
  {name}_config:
    file: ../../configs/network-slicing/{name}.yaml"""


def main():
    compose_snippets = []
    config_entries = []

    for i in range(N_UE_COUNT):
        ue_num = N_UE_START + i
        name = f"ue{ue_num}"
        imsi = BASE_IMSI + 1 + i  # 893, 894, 895...
        imei = BASE_IMEI + i      # 803, 804, 805...
        imei_sv = 52 + i          # 52, 53, 54...

        # Write YAML config
        config_path = Path(f"configs/network-slicing/{name}.yaml")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(UE_TEMPLATE.format(
            imsi_suffix=imsi,
            imei_suffix=imei,
            imei_sv_suffix=f"{imei_sv:02d}"  # zero-pad if needed
        ))
        print(f"[+] Created {config_path}")

        # Generate compose snippet
        compose_snippets.append(COMPOSE_TEMPLATE.format(name=name))
        config_entries.append(CONFIG_ENTRY_TEMPLATE.format(name=name))

    print("\n--- Add these to docker-compose.yaml under 'services:' ---")
    print("".join(compose_snippets))
    print("\n--- Add these to docker-compose.yaml under 'configs:' ---")
    print("".join(config_entries))

    print("\n[!] Don't forget to add subscribers via WebUI for each new IMSI!")


if __name__ == "__main__":
    main()
