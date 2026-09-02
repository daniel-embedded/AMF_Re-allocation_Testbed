#!/usr/bin/env python3
# generate_ues.py  --  scale-up edition (ue7..ue48)
#
# Generates, for each new UE:
#   1. configs/network-slicing/ueN.yaml            (UERANSIM UE config)
#   2. docker-compose snippets + config entries     (printed, paste into compose)
#   3. db_insert_ue7_ue48.js                        (MongoDB subscriber cloning script)
#
# DB insertion clones the EXISTING ue6 subscriber document (guaranteed schema
# match with the running Open5GS/WebUI version), replacing only the IMSI and
# resetting SQN. No manual WebUI work needed.

from pathlib import Path

N_UE_START = 7       # first new UE number (ue7)
N_UE_COUNT = 42      # ue7 .. ue48
IMSI_START = 7897    # last 3 IMSI digits; ue6 = ...7896
IMEI_START = 809     # last 3 IMEI digits; ue6 = ...43808
IMESV_START = 57     # last 2 IMEISV digits; ue6 = ...16156

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

# MongoDB script: clone ue6's subscriber doc for each new IMSI.
# Run with:  sudo docker exec -i db mongosh --quiet open5gs < db_insert_ue7_ue48.js
MONGO_TEMPLATE = """// Auto-generated: clone ue6 subscriber for ue7..ue48
var tpl = db.subscribers.findOne({{imsi: "001011234567896"}});
if (!tpl) {{ throw "template subscriber (ue6) not found"; }}
delete tpl._id;
var imsis = [{imsi_list}];
var inserted = 0;
imsis.forEach(function(imsi) {{
  if (db.subscribers.findOne({{imsi: imsi}})) {{ print("SKIP (exists): " + imsi); return; }}
  var doc = Object.assign({{}}, tpl);
  doc.imsi = imsi;
  if (doc.security) doc.security.sqn = "000000000000";
  db.subscribers.insertOne(doc);
  inserted++;
}});
print("Inserted " + inserted + " subscribers (ue7..ue48).");
printjson(db.subscribers.findOne({{imsi: imsis[0]}}));  // verify one
"""


def main():
    compose_snippets = []
    config_entries = []
    imsi_list = []

    for i in range(N_UE_COUNT):
        ue_num = N_UE_START + i
        name = f"ue{ue_num}"
        imsi = IMSI_START + i
        imei = IMEI_START + i
        imei_sv = IMESV_START + i

        config_path = Path(f"configs/network-slicing/{name}.yaml")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(UE_TEMPLATE.format(
            imsi_suffix=imsi,
            imei_suffix=imei,
            imei_sv_suffix=f"{imei_sv:02d}"
        ))
        imsi_list.append(f'"001011234567{imsi}"')

        compose_snippets.append(COMPOSE_TEMPLATE.format(name=name))
        config_entries.append(CONFIG_ENTRY_TEMPLATE.format(name=name))

    print(f"[+] Created {N_UE_COUNT} UE configs (ue{N_UE_START}..ue{N_UE_START+N_UE_COUNT-1})")

    Path("db_insert_ue7_ue48.js").write_text(
        MONGO_TEMPLATE.format(imsi_list=", ".join(imsi_list))
    )
    print("[+] Created db_insert_ue7_ue48.js")

    Path("compose_snippets_ue7_ue48.txt").write_text(
        "--- Add under 'services:' ---\n" + "".join(compose_snippets)
        + "\n\n--- Add under 'configs:' ---\n" + "".join(config_entries) + "\n"
    )
    print("[+] Created compose_snippets_ue7_ue48.txt")

    print("\nNext steps:")
    print("  1. Paste compose_snippets_ue7_ue48.txt contents into docker-compose.yaml")
    print("  2. sudo docker exec -i db mongosh --quiet open5gs < db_insert_ue7_ue48.js")
    print("     (if 'mongosh' is missing in the db image, use 'mongo' instead)")
    print("  3. Update the orchestrator UE pool:")
    pool = ",".join(f"ue{n}" for n in range(1, N_UE_START + N_UE_COUNT))
    print(f"       --ue-pool {pool}")


if __name__ == "__main__":
    main()
