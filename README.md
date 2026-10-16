# AMF Re-allocation Testbed — Open5GS 5G Core in Docker

Experimental testbed for studying **AMF re-allocation across network slices** in a
containerized 5G Core, built on top of
[docker-open5gs](https://github.com/Borjis131/docker-open5gs)
([Open5GS](https://github.com/open5gs/open5gs) v2.7.6) with
[UERANSIM](https://github.com/aligungr/UERANSIM) v3.2.7 gNB/UE simulation.

The testbed deploys **two slices, each served by its own AMF**, with a **dual SCTP
proxy** between the gNBs and the AMFs. The proxy can redirect UE traffic from one
AMF/slice to the other at runtime, enabling controlled experiments on UE mobility,
session continuity, and re-allocation cost **at scale — validated up to 488 UEs**
(UERANSIM attach ceiling; see *Known scaling limits*).

> [!NOTE]
> This repository extends upstream docker-open5gs with: a patched Open5GS AMF image,
> a patched UERANSIM gNB image, a Python experiment orchestrator, scaling tooling for
> hundreds of UEs (pool `ue1`–`ue784`), and a results-analysis/plotting pipeline.
> The upstream usage documentation is kept at the end of this file.

## Architecture

```
        +--------+   N2/SCTP   +------------------+   N2/SCTP   +-------+
UEs --->|  gNB1  |------------>|                  |------------>|  AMF1 | (slice 1)
(UERANSIM)       |             |   SCTP proxy     |             +-------+
        +--------+             |  (dual-homed,    |             +-------+
        |  gNB2  |------------>|   re-routable)   |------------>|  AMF2 | (slice 2)
        +--------+             +------------------+             +-------+
                                      |
                              N14 (UE Context Transfer)
                              AMF1 <----------------------> AMF2
```

- **Open5GS v2.7.6** core (AMF ×2, SMF ×2, UPF ×2, NRF, and remaining NFs), MongoDB subscriber DB.
- **UERANSIM v3.2.7**: 2 gNBs; UE pool `ue1`–`ue784` (validated attach ceiling: 488 stable / 490 flaky).
- **Dual SCTP proxy** (`sctp-proxy`, control UDP 9999; `sctp-proxy2`, control UDP 9998):
  sits on the N2 path; can switch all (or a subset of) UEs from AMF1 to AMF2 either by
  tearing down SCTP associations (restart-based mechanisms) or by redirecting them
  (N14 context-transfer mobility mechanism). Backend switching is triggered by a
  single UDP datagram to the proxy control port.
- **Patched images** (required — see *Build it* below):
  - `base-open5gs:v2.7.6` with a patch to `gmm-handler.c` tolerating non-cleartext
    IEs (`Non cleartext IEs tolerated [0x40000c]`), required for N14 UE Context
    Transfer between the AMFs. Built locally and **shadowed** as
    `ghcr.io/borjis131/amf:v2.7.6` so the compose files need no edits.
  - `local/gnb:v3.2.7-exp8`: patched UERANSIM gNB image.

## Repository structure

- `compose-files/` — docker compose deployments (two-slice/AMF deployment with proxy).
- `configs/` — Open5GS configuration files per deployment.
- `images/` — per-NF Dockerfiles (including the patched `amf` image build).
- `Python_scripts/`
  - `analyze_scaling.py` — analysis & figure pipeline (supports per-tier coloring
    across the full tier grid; see *Analyze results* below).
- `start_network.sh` — phased network startup for `ue_count` UEs (1–784, or `all`).
- `run_all_experiments_v4.sh` — runs the full 8-experiment campaign.
- `generate_ues_scale768.py` — generates UE YAML configs and the MongoDB
  subscriber-provisioning script (mongosh); extend the range for larger pools.
- `generate_analysis_plots.sh` — one-command wrapper for the analysis pipeline.
- `results/` — experiment output (see *Results layout* below).
- `sctp_proxy/`
  - `amf_orchestrator_v7.py` — experiment orchestrator (current reference: **v7.5.4**;
    hardened variants v7.5.5–v7.5.9 for Exp8 >248 UEs exist but v7.5.4 is the
    validated reference for the reference tier grid).
  - `sctp_proxy_v5` — full-featured SCTP proxy for Open5GS N2 (dual-instance capable).

## The 8 experiments

All experiments are driven by `amf_orchestrator_v7.py` and export a structured
`amf_realloc.json` per run.

| # | Mode                 | What it does                                                                                     |
|---|----------------------|--------------------------------------------------------------------------------------------------|
| 1 | `sequential`         | Restart-based re-allocation, strictly one UE at a time (restart + wait for recovery per UE)      |
| 2 | `concurrent`         | Restart-based re-allocation, all UEs at once                                                     |
| 3 | `back-forth`         | Concurrent switch AMF1→AMF2→AMF1→AMF2 (symmetry check)                                          |
| 4 | `partial`            | Only a subset (first half) of UEs is re-allocated; non-movers tracked for collateral disturbance |
| 5 | `isolate-gnb`        | gNB-level isolation; recovery via RRC re-establishment                                           |
| 6 | `split-simultaneous` | Split the UE population across both AMFs, cohorts restarted back-to-back (storms overlap)        |
| 7 | `split-sequential`   | Split the UE population across both AMFs, mover cohort starts only after stayer cohort recovers  |
| 8 | `n14-mobility`       | Proxy redirect + **N14 UE Context Transfer** (no UE restart, IP/session preserved)               |

Per-run JSON records, per UE: recovery success, recovery latency (ms), recovery type
(`re-registration`, `rrc_reestablishment`, `collateral_*`), whether the PDU session
IP was preserved, mobility-registration/session-continuity flags, and N14
context-transfer evidence (trigger-anchored scan of AMF logs, stderr-merged).

Latency convention: per-UE `reallocation_latency_ms` is anchored at the **proxy
switch time**; `post_restart_latency_ms` is anchored at each UE's own container
restart. In restart-based modes the former is dominated by the serial restart
issuance queue (~0.34 s/UE in bulk-issuance modes, ~3.4 s/UE in Exp1's strict
restart-and-wait loop); the core-side recovery itself is ~0.6 s/UE.

## Build it

The testbed requires the **patched** base image and AMF image. The patch is applied and verified at build time (the build fails if the patch does not apply):

```bash
# 1. Patched base image (applies gmm-handler.c patch, guarded by a grep check)
docker build --no-cache -t base-open5gs:v2.7.6 .

# 2. Patched AMF image, tagged to shadow the registry image used by the compose files
cd images/amf
docker build -t ghcr.io/borjis131/amf:v2.7.6 .
```

Verify the patch is inside the running container:

```bash
docker exec amf2 grep -c tolerated /usr/local/bin/open5gs-amfd   # must be > 0
```

Build the patched gNB image (see `images/` for the UERANSIM Dockerfile variant):

```bash
docker build -t local/gnb:v3.2.7-exp8 .
```

## Use it

### Provision subscribers (up to 784 UEs)

```bash
python3 generate_ues_scale768.py            # writes UE YAMLs + db_insert_*.js (extend range as needed)
mongosh <db_uri> db_insert_ue49_ue768.js     # clones the reference subscriber doc per new IMSI
```

> [!WARNING]
> Each UE YAML's `supi` must be exactly 15 digits and match the IMSI inserted into
> MongoDB. No duplicate SUPIs.

### Start the network

```bash
./start_network.sh <ue_count>     # `ue_count` = 1..784 or 'all' (default: 6)
```

Phased startup; waits until all `ue_count` UEs are registered (timeout 3 s/UE, min 60 s,
scaling with the UE count), restarting stragglers once at half-timeout. Note the
UERANSIM attach ceiling: **488 UEs start reliably; 490 is flaky** (see *Known
scaling limits*). We deliberately keep the startup script as-is so this simulator
limit is documented rather than hidden.

### Run the full experiment campaign

```bash
./run_all_experiments_v4.sh <label> <ue_count>     # `ue_count` default: 6
```

Runs all 8 experiments, passing `--ues <ue_count>` to the orchestrator, deriving the
partial subset (first half) and split movers (second half) automatically, and scaling
the N14 wait window with the UE count.

To run a single experiment manually:

```bash
python3 Python_scripts/amf_orchestrator_v7.py --mode <run_mode> --ues <ue_count>
```

Possible `--mode <run_mode>` options:
- `sequential`
- `concurrent`
- `backforth`
- `partial`
- `isolate-gnb`
- `split-gnb-simultaneous`
- `split-gnb-sequential`
- `n14-mobility`

### Analyze results and generate figures

```bash
./generate_analysis_plots.sh    # = python3 Python_scripts/analyze_scaling.py --results-root results
```

Produces into `results/scaling_analysis/`:

- `fig1_mechanism_cost.png` — per-mechanism cost, canonical 6-UE campaign (bars)
- `fig2_scaling_curves.png` + `fig2_scaling_curves_log.png` — latency vs. burst size per mechanism (linear and log Y, 95% CI across runs)
- `fig3_queueing_ramp.png` — queueing/serialization ramp, one color per tier (explicit per-tier palette + golden-ratio fallback, distinct for the full 16-tier grid)
- `fig4_latency_decomposition.png` — stacked latency decomposition
- `fig5_continuity_matrix.png` — session-continuity heatmap
- `fig6_partial_blast_radius.png` — collateral disturbance in partial mode
- `fig7_backforth_symmetry.png` — directional symmetry check
- `summary_stats.csv` — aggregate statistics

Statistics convention: mean of run means; 95% CI across runs (Student-t, n = runs);
per-UE pooled values only for distribution shapes. Experiment-8 replication runs are
merged with the campaign's N14 runs.

## Scaling: validated tiers and per-mode limits

Reference tier grid (legitimated by stress testing): **32, 64, 96, 128, 160, 192,
224, 256 UEs**. Up to 224 UEs **all 8 experiments pass at 100% recovery**; 256 is
the first tier with losses. Stress campaigns additionally covered 240, 248, 256,
264, 288, 312, 336, 384 and 488 UEs to map each mechanism's wall:

| Experiment | Behavior at scale |
|---|---|
| Exp1 `sequential` | 100% recovery even at 488 UEs (no contention — strict one-at-a-time) |
| Exp2 `concurrent` | Clean ≤216; transition at 240 |
| Exp3 `back-forth` | 10 clean cycles ≤256; last-cycle collapse at 288; progressively earlier collapse at 312–384; immediate at 488 (sustained-storm congestion collapse) |
| Exp4 `partial` / Exp5 `isolate-gnb` | Radio-path wall at 264 |
| Exp6/Exp7 `split-*` | Clean ≤224; transition at 240; wall at 312. Sequential improves recovery at saturation vs. simultaneous (e.g. 245/384 vs 202/384) |
| Exp8 `n14-mobility` | Validated flat ~2.9 s mean latency from 6 to 248 UEs; 256 is the first setup failure (Exp8's >248 setup chain — radio-visibility check wall — remains an open item; v7.5.4 is the reference at ≤248) |

## Results layout

```
results/
├── <timestamp>_<label>_<ue_count>ue/           # one full campaign at `ue_count` UEs
│   ├── 01_sequential/amf_realloc.json
│   ├── 02_concurrent/amf_realloc.json
│   ├── ...
│   └── 08_n14-mobility/amf_realloc.json
├── <timestamp>_Exp8_<ue_count>ue_<R>reps/      # Exp-8 replication runs
│   └── *.json
└── scaling_analysis/                           # figures + CSV from analyze_scaling.py
```

## Tested environment

- Host "Hindem": Intel Core i9-14900HX (32 logical CPUs), 31 GB RAM, Ubuntu (GNOME)
- Docker 28.5.2, **cgroupfs cgroup driver** (required for >500 containers)
- Open5GS v2.7.6 (patched), UERANSIM v3.2.7 (patched gNB image)
- Docker networks: RAN `10.33.32.0/22`; SMF session pool enlarged to `10.45.0.0/16`
- Validated: all 8 experiments at 100% success up to 224 UEs; reference grid
  32–256; stress tiers up to 488

## Known scaling limits

- **UERANSIM attach ceiling (simulator defect, not config)**: at ~490 UEs a
  deterministic per-UE subset (~2%) crash-loops at Security Mode Command with
  `std::runtime_error: Bad constructed NAS message` (upstream UERANSIM issue #788).
  488 UEs attach reliably, 490 is flaky. Documented as the simulator limit.
- **Exp8 >248 UEs**: the radio-visibility setup step (per-UE beacon sweep after the
  gnb2 NG Setup) does not complete reliably within practical timeouts; 248 is the
  validated N14 ceiling. Hardened orchestrator variants (v7.5.5–v7.5.9: parallel
  sweep, latched visibility, proxy-switch retries) improved robustness but were not
  adopted as reference.
- SCTP proxy CPU saturation was an early wall (~90–100 UEs) before the proxy and
  network sizing updates; host RAM/CPU were not limiting at 488 UEs.
- Exp1's absolute latencies include a per-UE blocking recovery wait (~3.4 s/UE
  pace) — treat them as an orderly-baseline upper bound, not core capacity.

---

# Upstream documentation (docker-open5gs)

This repository uses the same release version tags as Open5GS; images are based on `ubuntu` and `mongo` images per the `.env` file.

## Configure it

All the images (except `webui`) depend on the `base-open5gs` image to be built; after that each image is independent. Update the `.env` and `docker-bake.hcl` files with the desired values:

- `OPEN5GS_VERSION` — Open5GS version to use (default/tested: v2.7.6).
- `UBUNTU_VERSION` — Ubuntu base image tag (default: `jammy`).
- `MONGODB_VERSION` — MongoDB image tag (tested: 3.6.8, 4.2, 6.0, 8.0).
- `NODE_VERSION` — Node.js version for the WebUI (default: 20).
- `DOCKER_HOST_IP` — host IP, used for the `advertise` field in `upf.yaml`.

## Build it (upstream deployments)

With Bake (recommended upstream method):

```bash
docker buildx bake
```

With Make:

```bash
make all            # base-open5gs + all NF images
```

With Docker Compose:

```bash
make base-open5gs
docker compose -f compose-files/basic/docker-compose.yaml --env-file=.env up -d
```

## Use it (upstream deployments)

```bash
docker compose -f compose-files/basic/docker-compose.yaml --env-file=.env up -d
docker compose -f compose-files/basic/docker-compose.yaml --env-file=.env down
```

The `basic` deployment works with external gNBs, exposing N2 (`SCTP 38412`) on the AMF and N3 (`UDP 2152`) on the UPF, plus MongoDB on `TCP 27017`. See `docs/` for the full deployment documentation and `misc/` for diagrams.

Helm charts are also available upstream (`helm/` directory; `CHART_VERSION` 0.3.5 for the umbrella chart). Note that `<chart_version>` is not the same as `<open5gs_version>`.
