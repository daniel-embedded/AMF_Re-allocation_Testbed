# AMF Re-allocation Measurement Framework (Option 1)

Automated orchestrator and measurement framework for AMF re-allocation experiments
in an Open5GS / UERANSIM containerized setup.

## What This Measures

| Metric | Definition | Source |
|--------|-----------|--------|
| **Re-allocation latency** | Time from `SWITCH` command to UE `Initial Registration is successful` | UE logs |
| **Registration latency** | Time from AMF `InitialUEMessage` to `Registration complete` | AMF logs |
| **Session setup latency** | Time from `SWITCH` to UE `TUN interface up` | UE logs |
| **Signaling overhead** | Count of NGAP + NAS messages during re-registration | gNB/AMF/UE logs |
| **IP continuity** | Whether UE keeps the same tunnel IP | UE logs |
| **AMF load** | Active sessions and UEs per AMF | AMF logs + proxy metrics |

## Files

- `sctp_proxy_v4.py` — Enhanced SCTP proxy with Prometheus metrics
- `amf_orchestrator_v2.py` — Experiment runner, log parser, and result exporter
- `experiment_runner.sh` — One-shot convenience wrapper

## Requirements

- Python 3.10+
- `pysctp` (for the proxy; install with `apt-get install libsctp-dev && pip install pysctp`)
- `sudo` access for `docker` commands
- No extra Python packages needed for the orchestrator (stdlib only)

## Quick Start

### 1. Start the enhanced proxy (inside sctp-proxy container or host)

```bash
# If running inside the sctp-proxy container:
python3 /app/sctp_proxy_v4.py

# Or from host with the script copied in:
sudo docker cp sctp_proxy_v4.py sctp-proxy:/app/
sudo docker exec -it sctp-proxy python3 /app/sctp_proxy_v4.py
```

The proxy exposes:
- **SCTP N2**: `0.0.0.0:38412`
- **Control UDP**: `0.0.0.0:9999`
- **Prometheus**: `http://0.0.0.0:9100/metrics`

### 2. Run a single experiment

```bash
python3 amf_orchestrator_v2.py --command experiment --target amf2 --wait-before 10 --wait-after 60
```

This will:
1. Check baseline
2. Wait 10s
3. Send `SWITCH amf2` to the proxy
4. Restart `gnb1` then `ue1`
5. Monitor logs for up to 60s
6. Export results to `./results/`

### 3. Run repeated back-and-forth switches

```bash
python3 amf_orchestrator_v2.py \
  --command repeat \
  --repeat 4 \
  --wait-before 10 \
  --wait-after 60 \
  --cooldown 30 \
  --output-dir ./results/batch_1
```

This runs: `amf2 -> amf1 -> amf2 -> amf1`

### 4. One-shot with the shell script

```bash
REPEAT=3 WAIT_AFTER=90 bash experiment_runner.sh
```

## Output Formats

For each experiment run, four files are produced in the output directory:

- `amf_realloc.json` — Raw structured data
- `amf_realloc.csv` — Spreadsheet-ready (Excel, R, pandas)
- `amf_realloc.tex` — LaTeX table for your thesis
- `amf_realloc_summary.txt` — Human-readable text report

## Prometheus Metrics

Scrape `http://<proxy-host>:9100/metrics` for:

```
sctp_proxy_connections_total
sctp_proxy_active_associations
sctp_proxy_switches_total
sctp_proxy_bytes_transferred_total{direction="gNB_to_AMF|AMF_to_gNB"}
sctp_proxy_ngap_messages_total{direction="...",type="..."}
sctp_proxy_backend_current{backend="amf1|amf2"}
```

You can point a Prometheus server to this endpoint, or just curl it:

```bash
curl http://localhost:9100/metrics
```

## Known Limitations (Documented for Your Thesis)

1. **No N14 interface**: Open5GS does not implement inter-AMF UE context transfer. AMF2
cannot inherit AMF1's security context or PDU session bindings.
2. **IP changes on re-registration**: Because the old SM context is explicitly released
and a new session created, the UE receives a new IP from the UPF pool.
3. **Full re-authentication**: When moving to a different AMF with a different GUAMI,
the UE is treated as unknown and must perform full Initial Registration.

These limitations are **expected** and should be framed in your research as:
> "This work quantifies the re-establishment latency and signaling overhead of AMF
> re-allocation in the absence of standardized N14 context transfer, providing a
> baseline for future seamless handover implementations."

## Troubleshooting

### Proxy not reachable
Ensure the proxy control interface is listening on UDP 9999:
```bash
python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); \
  s.sendto(b'STATUS',('localhost',9999)); print(s.recvfrom(1024)[0].decode())"
```

### UE does not re-register
- Check that `gnb1` points to `proxy.open5gs.org:38412` in its config
- Verify the proxy switched correctly (`STATUS` command)
- Check `ue1` and `gnb1` logs: `sudo docker logs --tail 50 ue1`

### Metrics server port conflict
Change the metrics port:
```bash
PROXY_METRICS_PORT=9101 python3 sctp_proxy_v4.py
```
