#!/usr/bin/env python3
"""
amf_orchestrator_v7.py  (v7.4)

v7.4 adds Experiment 8 (mode: n14-mobility) — N14-enabled mobility
re-allocation WITHOUT UE container restart:
  - The proxy teardown used in exps 1-7 forces Initial Registration (SUCI),
    which tears down the PDU session and rotates the IP. Exp8 instead keeps
    the UE's 5G-GUTI alive, kills gnb1 (TAC1), and lets the UE reselect
    gnb2 (TAC2). Because the UE's TAI list is {1}, TAC2 triggers a Mobility
    Registration Update carrying the old GUTI; AMF2 then pulls the UE context
    (incl. SM contexts) from AMF1 over N14 (UEContextTransfer,
    reason=MOBI_REG). Expected outcome: session continuity, SAME IP.
  - TAI-list blocker: both AMFs normally advertise tac1+tac2, so the UE's
    TAI list would be {1,2} and no mobility registration would fire. Exp8
    temporarily swaps amf1.yaml to tac1-only and recreates amf1 via
    `docker compose up -d amf1` (the compose stack uses `configs:`, which are
    immutable content snapshots — a plain file edit + `docker restart` would
    NOT pick the change up; compose recreate does). The on-disk file is
    restored immediately afterwards; amf1 keeps the tac1-only snapshot for
    the duration of the experiment and is recreated from the restored file
    during cleanup. Requires --compose-dir pointing at the compose project
    (default: current directory) and Docker Compose v2 (`docker compose`).
    v7.4.1: if the compose file has a non-standard name/location (e.g. nested
    under a subdirectory, per the '../../configs/...' volume references),
    pass it explicitly via --compose-file; a generic "no configuration file
    provided" failure now prints a hint to locate it via start_network.sh.
    v7.4.3 (after the 2026-08-25 pilot): `docker compose up -d amf1` was a
    silent NO-OP with bind-mounted amf1.yaml (amf1 kept advertising tac1+tac2,
    so no TAI-list trigger could ever fire) — the config load now falls back
    to `docker restart amf1` and is VERIFIED by reading the config from inside
    the container. The UE baseline now runs with gnb2 STOPPED: the pilot showed
    all 6 UEs camping on the freshly-restarted gnb2/AMF2 BEFORE the trigger
    (cell selection is a race that gnb1-first config ordering does not win),
    which made the trigger a no-op. Baseline time anchors are captured BEFORE
    the UE restart loop and stray-cell detection is anchored on each UE's
    container StartedAt (a late anchor previously hid both).
    v7.4.4 (pilot 2): `docker stop gnb2` removes its DNS record and UERANSIM
    CRASHES at boot ('Bad Inet address: gnb2.ueransim.org' — this is why the
    static configs keep gnb2 commented out). gnb2 is now PAUSED instead:
    DNS stays resolvable, radio stays silent, baseline camping on gnb1 is
    deterministic. Pilot-2 UE logs also confirmed the trigger mechanics:
    same-TAC radio recovery (RLF -> reselect tac1) triggers NO registration,
    so the TAI change is indeed the required lever.
  - UE configs are rewritten to list BOTH gNBs (gnb1 first). This is only
    safe because gnb2 is started BEFORE the UE restart (UERANSIM cannot
    resolve gnb2.ueransim.org while the container is down), and a retry loop
    re-boots any UE that camps on tac2 at baseline.
  - AMF-side N14 evidence is parsed from amf1/amf2 logs of the CURRENT
    container lifetime (docker logs --since StartedAt). Visibility requires
    `logger: sbi: level: debug` in amf2.yaml (recommended); without it the
    n14_context_transfer flag may stay false even if the transfer happened.
  - TBD after the pilot run: the exact UERANSIM wording for mobility
    registration. UE_MOBILITY_PATTERNS below are tolerant placeholders
    ("Mobility Registration" / "Registration Request"); lock them to the
    observed log lines before the measurement campaign.
  - Run exp8 LAST in run_all_experiments_v2.sh (it recreates amf1 and stops
    gnb1; cleanup restores the homogeneous end state for the next suite run).

v7.3 fixes (after the 2026-08-18 v7.2 run):
  1. Split modes now restart movers onto gnb1 after config restore
     (_return_movers_to_gnb1). With exclusive gnb2-only configs, movers kept a
     gnb2-only IN-MEMORY gnbSearchList after the on-disk restore; when gnb2's
     association was later torn down (proxy switch-back) and gnb2 was stopped
     by run_all_experiments_v2.sh, the movers entered a permanent
     'no cells in coverage' loop. Metrics are unaffected — the cleanup runs
     after results are collected.
Multi-UE, multi-mode automated orchestrator for AMF re-allocation experiments.

v7.2 fixes (after the 2026-08-18 v7.1 run):
  1. Split modes now write gnb2-ONLY mover configs (exclusive=True). With both
     gNBs listed, UERANSIM's cell selection among equal simulated signals is a
     timing race (v7.1 run: ue4 fell back to tac[1] in BOTH split modes, ue6
     in exp7 — AMF logs confirm TAC[1]/CellID[0x10]). Exclusive mode makes
     re-selection deterministic; a mover that cannot reach gnb2 now fails
     loudly (timeout WARN) instead of silently registering via gnb1.
  2. Restored two v7.1 edits that were missing from the delivered file:
     restart-time tracking in run_concurrent (hence also back-forth), and the
     gnb2_selected stamping call in run_split_gnb_sequential.

v7.1 fixes (after the 2026-08-18 run):
  1. rrc_latency_ms boundary comparison relaxed from <= to <: UERANSIM logs
     the cycle's MM-DEREGISTERED, Initial Registration and RRC-CONNECTED in the
     SAME millisecond, so the strict <= discarded the legitimate same-ms RRC
     event (observed: rrc null in healthy lifetimes). Cross-lifetime stale
     events remain excluded (they precede the boundary by seconds).
  2. Legacy modes (sequential/concurrent/backforth/partial) now track restart
     times via _restart_container_tracked, so post_restart_latency_ms is
     populated in ALL experiments, not just split modes.
  3. run_split_gnb_sequential: 3 s stabilization wait after gnb2 NG Setup
     before restarting movers (in the 08-18 run, movers booted ~3 s after
     gnb2's restart and all selected tac[1]; in the simultaneous mode, where
     ~6.5 s elapsed, all selected tac[2]).
  4. Split modes now verify post-hoc that each mover's CURRENT lifetime shows
     "Selected cell ... tac[2]" and stamp gnb2_selected true/false into the
     JSON — a mover that silently falls back to gnb1 no longer looks like a
     successful re-selection.

Fixes over v6:
  1. _set_gnb_preference replaces _swap_gnb_order: rewrites the whole
     gnbSearchList block (tolerant of COMMENTED gnb2 entries, which defeated
     the v6 insert-check and swap regex), writes IN PLACE (single-file bind
     mounts follow the inode; write-temp-then-rename would silently break
     them), and re-reads the file to VERIFY the first entry — aborting the
     experiment loudly instead of producing results with no gNB re-selection.
  2. post_restart_latency_ms now anchors on docker inspect StartedAt parsed
     as UTC (consistent with _ts_to_ms), captured WITHOUT the v6 0.5 s sleep
     that systematically placed the "restart" timestamp AFTER the UE had
     already registered (observed artifact: post_restart ~= -490 ms).
  3. rrc_latency_ms is bounded to the current registration cycle: the search
     never crosses the last MM-DEREGISTERED before Initial Registration, and
     a sanity cap discards stale cross-lifetime RRC-CONNECTED events
     (observed artifact: rrc values of 1-13 s in split-gnb experiments).
  4. wait_for_gnb_ng_setup only matches "NG Setup procedure is successful"
     lines logged AFTER the container's current StartedAt — docker restart
     does not clear logs, so the v6 tail-grep could confirm a stale success
     line from a previous lifetime.

Retained from v6 (dual-proxy):
  - _ts_to_ms parses UE log timestamps as UTC explicitly.
  - gNB stabilization delay (3 s) after every gNB restart.
  - Split-gnb-simultaneous restarts stayers too (keeping gnb1 preference).
  - Split-gnb-sequential restarts stayers immediately after gnb1 restart.
  - Experiment 5 (isolate-gnb) semantics preserved: ONLY gnb1 restarted.
"""

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# -- Configuration ---------------------------------------------------
DEFAULT_PROXY_HOST = "localhost"
DEFAULT_PROXY_PORT = 9999
DEFAULT_PROXY2_HOST = "localhost"
DEFAULT_PROXY2_PORT = 9998
DEFAULT_OUTPUT_DIR = "./results"
DEFAULT_CONFIG_DIR = "configs/network-slicing"

DEFAULT_UE_POOL = [f"ue{i}" for i in range(1, 49)]  # ue1..ue48 (v7.5: scaled)
DEFAULT_GNB_POOL = ["gnb1"]
LEGACY_GNB = "gnb1"

CONTAINERS = {"proxy": "sctp-proxy"}

# -- Proxy Communication --------------------------------------------
def send_proxy_cmd(cmd: str, host: str, port: int, timeout: float = 5.0) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(cmd.encode(), (host, port))
        data, _ = sock.recvfrom(4096)
        return data.decode().strip()
    except socket.timeout:
        return "ERROR: Timeout"
    finally:
        sock.close()

def get_proxy_status(host: str, port: int) -> str:
    return send_proxy_cmd("STATUS", host, port)

def switch_proxy_backend(target: str, host: str, port: int) -> bool:
    resp = send_proxy_cmd(f"SWITCH {target}", host, port)
    return resp.startswith("OK")

def switch_both_backends(target: str, host1: str, port1: int, host2: str, port2: int) -> bool:
    """Switch both proxies to the same target AMF."""
    ok1 = switch_proxy_backend(target, host1, port1)
    ok2 = switch_proxy_backend(target, host2, port2)
    if ok1 and ok2:
        return True
    if not ok1:
        print(f"[ERROR] Primary proxy switch failed ({host1}:{port1})")
    if not ok2:
        print(f"[ERROR] Secondary proxy switch failed ({host2}:{port2})")
    return False

# -- Docker Helpers -------------------------------------------------
def docker_logs(container: str, tail: int = 500) -> str:
    # v7.4.8: merge stderr — Open5GS daemons log to STDERR, so stdout-only
    # capture silently returned "" for amf1/amf2 and N14 evidence was never
    # found even when the transfers were in the logs.
    result = subprocess.run(
        ["sudo", "docker", "logs", "--tail", str(tail), container],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return result.stdout

def restart_container(container: str) -> bool:
    result = subprocess.run(
        ["sudo", "docker", "restart", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] Failed to restart {container}: {result.stderr.strip()}")
        return False
    print(f"[Docker] Restarted {container}")
    return True

def container_running(container: str) -> bool:
    result = subprocess.run(
        ["sudo", "docker", "ps", "--format", "{{.Names}}",
         "--filter", f"name={container}"],
        capture_output=True, text=True
    )
    return container in result.stdout

def _parse_docker_ts_ms(raw: str) -> Optional[float]:
    """Parse a docker inspect timestamp (RFC3339 UTC, ns precision) to epoch ms."""
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z?", raw.strip())
    if not m:
        return None
    frac = (m.group(2) or ".0")[:7]  # truncate nanoseconds to microseconds
    try:
        dt = datetime.fromisoformat(m.group(1) + frac).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp() * 1000

def stop_container_tracked(container: str) -> Optional[float]:
    """Stop a container; return its FinishedAt in epoch ms (UTC).

    Exp8's trigger instant: the SCTP association dies while `docker stop` runs,
    so FinishedAt (process actually dead) is a far better t0 than the time the
    command was issued (the default stop grace period can be up to 10 s).
    Falls back to the pre-stop wall clock if FinishedAt is unavailable.
    """
    fallback_ms = time.time() * 1000
    result = subprocess.run(
        ["sudo", "docker", "stop", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] Failed to stop {container}: {result.stderr.strip()}")
        return None
    print(f"[Docker] Stopped {container}")
    r = subprocess.run(
        ["sudo", "docker", "inspect", "-f", "{{.State.FinishedAt}}", container],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        ms = _parse_docker_ts_ms(r.stdout)
        if ms is not None:
            return ms
    return fallback_ms

def pause_container(container: str) -> bool:
    """Freeze a container (docker pause). Unlike docker stop, the container
    KEEPS its network identity — Docker's embedded DNS still resolves its name
    (UERANSIM throws LibError on unresolvable gnbSearchList entries, observed
    2026-08-25: 'Bad Inet address: gnb2.ueransim.org'), but its processes are
    frozen so it emits no RLS beacons and UEs cannot camp on it."""
    result = subprocess.run(
        ["sudo", "docker", "pause", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] Failed to pause {container}: {result.stderr.strip()}")
        return False
    print(f"[Docker] Paused {container}")
    return True

def start_container(container: str) -> bool:
    result = subprocess.run(
        ["sudo", "docker", "start", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] Failed to start {container}: {result.stderr.strip()}")
        return False
    print(f"[Docker] Started {container}")
    return True

def _detect_compose_labels(container: str) -> Optional[Dict[str, str]]:
    """Recover the compose project name, config file path and working dir from
    the LABELS Docker Compose stamps on every container it creates. This makes
    compose calls work regardless of the caller's cwd or the file's location."""
    r = subprocess.run(
        ["sudo", "docker", "inspect", "-f",
         '{{index .Config.Labels "com.docker.compose.project"}}|'
         '{{index .Config.Labels "com.docker.compose.project.config_files"}}|'
         '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
         container],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    parts = r.stdout.strip().split("|")
    if len(parts) != 3 or not parts[0]:
        return None
    return {"project": parts[0], "config_files": parts[1], "working_dir": parts[2]}

def _find_env_file(*start_dirs: str) -> Optional[str]:
    """Search for a .env file in the given dirs and up to 2 parent levels."""
    seen = set()
    for start in start_dirs:
        if not start:
            continue
        d = Path(start).resolve()
        for _ in range(3):
            cand = d / ".env"
            if cand.is_file() and str(cand) not in seen:
                return str(cand)
            seen.add(str(cand))
            if d.parent == d:
                break
            d = d.parent
    return None

def compose_up_service(service: str, compose_dir: str = ".", compose_file: str = "",
                       env_file: str = "") -> bool:
    """Recreate a compose service so it picks up changed `configs:` content.

    The stack mounts amfN.yaml via compose `configs:` (immutable content
    snapshots), so editing the file on disk + `docker restart` is NOT enough —
    only `docker compose up -d` detects the config drift and recreates the
    container with the new snapshot. Requires Docker Compose v2.

    Self-configuring (v7.4.2): the compose project name, config file and
    working dir are auto-detected from the labels of the RUNNING container,
    and a .env file (OPEN5GS_VERSION etc.) is searched upward from the working
    dir and the compose file's dir. Explicit --compose-dir / --compose-file /
    --env-file arguments override the detected values.
    """
    det = _detect_compose_labels(service)
    if det:
        print(f"[Docker] compose project '{det['project']}' auto-detected from {service} labels")

    cwd = compose_dir if compose_dir != "." else (det["working_dir"] if det else ".")
    cfg = compose_file or (det["config_files"] if det else "")
    project = det["project"] if det else ""
    env = env_file or _find_env_file(cwd, str(Path(cfg).parent) if cfg else "")

    cmd = ["sudo", "docker", "compose"]
    if project:
        cmd += ["-p", project]
    if cfg:
        cmd += ["-f", cfg]
    if env:
        cmd += ["--env-file", env]
        print(f"[Docker] using env file: {env}")
    cmd += ["up", "-d", service]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr.strip()
        print(f"[ERROR] docker compose up -d {service} failed: {err}")
        if "no configuration file" in err:
            print("[HINT] compose could not find its project file. Locate it with "
                  "`grep -n \"docker compose\" start_network.sh`, then pass its directory "
                  "via --compose-dir or the file itself via --compose-file.")
        elif "variable is not set" in err or "invalid reference format" in err:
            print("[HINT] compose variables (e.g. OPEN5GS_VERSION) are unset. Either run "
                  "from a shell that has them exported (start_network.sh does), or pass "
                  "--env-file pointing at the .env your stack uses.")
        return False
    print(f"[Docker] compose service {service} (re)created")
    return True

def docker_logs_since_started(container: str, tail: int = 2000) -> str:
    """Container logs bounded to the CURRENT lifetime (docker logs --since StartedAt).

    Used for AMF-side N14 evidence: Open5GS log lines carry no year, so
    per-line timestamp parsing is unreliable; --since does the bounding
    server-side instead.
    """
    r = subprocess.run(
        ["sudo", "docker", "inspect", "-f", "{{.State.StartedAt}}", container],
        capture_output=True, text=True
    )
    started = r.stdout.strip()
    if r.returncode != 0 or not started:
        return docker_logs(container, tail=tail)
    # docker logs --since interprets naive timestamps in HOST LOCAL time, but
    # StartedAt is UTC (e.g. UTC-3 hosts get an empty window). A Unix epoch
    # is timezone-unambiguous, so convert before passing it.
    since = started
    ms = container_started_ms(container)
    if ms is not None:
        # subtract 1s of slack to not lose first-lifetime lines
        since = str(int(ms // 1000) - 1)
    result = subprocess.run(
        ["sudo", "docker", "logs", "--since", since, "--tail", str(tail), container],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return result.stdout

def get_container_start_time_ms(container: str) -> Optional[float]:
    """Backward-compatible alias for container_started_ms() (defined below)."""
    return container_started_ms(container)

def container_started_ms(container: str) -> Optional[float]:
    """Current container-lifetime start time in epoch ms (UTC).

    docker inspect reports StartedAt as an ISO-8601 UTC string, e.g.
    '2026-08-17T19:34:55.123456789Z'. Parsed timezone-aware so it is directly
    comparable with MetricComputer._ts_to_ms() output (UE/proxy logs in UTC).
    Returns None if unavailable/unparseable.
    """
    result = subprocess.run(
        ["sudo", "docker", "inspect", "-f", "{{.State.StartedAt}}", container],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z?", raw)
    if not m:
        return None
    frac = (m.group(2) or ".0")[:7]  # truncate nanoseconds to microseconds
    try:
        dt = datetime.fromisoformat(m.group(1) + frac).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp() * 1000


def wait_for_gnb_ng_setup(gnb_name: str, timeout: float = 15.0, interval: float = 1.0) -> bool:
    """Poll gNB logs until NG Setup succeeds IN THE CURRENT CONTAINER LIFETIME.

    docker restart does not clear container logs, so a plain pattern search can
    confirm a stale success line from a previous lifetime. We anchor on the
    container's StartedAt and only accept log lines timestamped after it.
    """
    started_ms = container_started_ms(gnb_name)
    pat = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\].*NG Setup procedure is successful"
    )
    elapsed = 0.0
    while elapsed < timeout:
        logs = docker_logs(gnb_name, tail=50)
        for line in logs.splitlines():
            m = pat.search(line)
            if not m:
                continue
            if started_ms is None:
                # Fallback (no StartedAt): accept the match, as v6 did.
                print(f"[Docker] {gnb_name} NG Setup confirmed (lifetime anchor unavailable)")
                return True
            try:
                line_ms = (
                    datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
                    .replace(tzinfo=timezone.utc)
                    .timestamp() * 1000
                )
            except ValueError:
                continue
            if line_ms >= started_ms:
                print(f"[Docker] {gnb_name} NG Setup confirmed")
                return True
        time.sleep(interval)
        elapsed += interval
    print(f"[WARN] {gnb_name} NG Setup not confirmed within {timeout}s")
    return False

# -- Log Parsers ----------------------------------------------------
class LogParser:
    UE_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
    UE_PATTERNS = {
        "mm_deregistered": re.compile(r"UE switches to state \[MM-DEREGISTERED"),
        "mm_registered": re.compile(r"UE switches to state \[MM-REGISTERED/NORMAL-SERVICE\]"),
        "initial_registration": re.compile(r"Sending Initial Registration"),
        "registration_success": re.compile(r"Initial Registration is successful"),
        "pdu_session_success": re.compile(r"PDU Session establishment is successful"),
        "tun_up": re.compile(r"Connection setup for PDU session\[.*\] is successful, TUN interface\[.*, (\d+\.\d+\.\d+\.\d+)\]"),
        "rrc_connected": re.compile(r"UE switches to state \[RRC-CONNECTED\]"),
    }
    # v7.4 (Exp8): mobility-registration events. TBD after the pilot run —
    # these are TOLERANT PLACEHOLDERS; lock them to the exact UERANSIM wording
    # observed in the UE logs before the measurement campaign. Checked AFTER
    # UE_PATTERNS so that e.g. "Initial Registration is successful" (which also
    # matches the mobility success regex below) still classifies as an initial
    # registration.
    UE_MOBILITY_PATTERNS = {
        "mobility_registration": re.compile(
            r"Sending (Mobility Registration|Registration Request)"
        ),
        "mobility_registration_success": re.compile(
            r"(Mobility Registration|Registration) (is successful|accepted)"
        ),
    }
    # v7.4 (Exp8): AMF-side evidence of the N14 UEContextTransfer, searched in
    # the current-lifetime logs of BOTH AMFs. With `logger: sbi: level: debug`
    # the SBI HTTP request/response lines (namf-comm/v1/ue-contexts/.../transfer)
    # appear; the OpenAPI model names may appear in parser traces.
    # NOTE: a bare "namf-comm" pattern would FALSE-POSITIVE on the startup line
    # "[sbi] INFO: NF Service [namf-comm]" — keep patterns transfer-specific.
    N14_PATTERNS = [
        re.compile(r"ue-contexts/\S+/transfer", re.IGNORECASE),
        re.compile(r"UEContextTransfer", re.IGNORECASE),
        re.compile(r"UeContextTransferRequest"),
        re.compile(r"ue.?context.?transfer", re.IGNORECASE),
    ]
    PROXY_SWITCH_RE = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*BACKEND SWITCH: .* -> (\w+)"
    )
    GNB_NG_SETUP_RE = re.compile(r"NG Setup procedure is successful")

    # Base patterns first, then mobility patterns (see note above).
    PATTERN_ORDER = list(UE_PATTERNS.items()) + list(UE_MOBILITY_PATTERNS.items())

    def parse_ue(self, text: str, ue_name: str = "ue") -> List[Dict]:
        events = []
        for line in text.splitlines():
            m = self.UE_TS_RE.search(line)
            if not m:
                continue
            ts = m.group(1)
            for name, pat in self.PATTERN_ORDER:
                mm = pat.search(line)
                if mm:
                    ev = {
                        "timestamp": ts,
                        "source": ue_name,
                        "event": name,
                        "line": line.strip(),
                    }
                    if name == "tun_up" and mm.lastindex:
                        ev["ip"] = mm.group(1)
                    events.append(ev)
                    break
        return events

    def parse_proxy_switch_time(self, text: str, target_amf: str) -> Optional[str]:
        matches = self.PROXY_SWITCH_RE.findall(text)
        for ts_str, backend in reversed(matches):
            if backend == target_amf:
                return ts_str
        return None

# -- Metric Computer ------------------------------------------------
class MetricComputer:
    @staticmethod
    def _ts_to_ms(ts_str: str) -> float:
        """Parse UE log timestamp to milliseconds since epoch.
        v6 FIX: Explicitly treat as UTC to avoid local-timezone skew."""
        s = ts_str.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                return dt.timestamp() * 1000
            except ValueError:
                continue
        return 0.0

    @classmethod
    def compute_ue_metrics(
        cls,
        ue_events: List[Dict],
        switch_time_ms: float,
        ue_name: str,
        restart_time_ms: Optional[float] = None,
    ) -> Dict:
        m = {
            "ue": ue_name,
            "switch_time_ms": switch_time_ms,
            "restart_time_ms": restart_time_ms,
            "reallocation_latency_ms": None,
            "registration_latency_ms": None,
            "session_setup_latency_ms": None,
            "rrc_latency_ms": None,
            "post_restart_latency_ms": None,
            "ip_changed": False,
            "old_ip": None,
            "new_ip": None,
            "recovered": False,
            "events": ue_events,
        }

        sorted_events = sorted(ue_events, key=lambda e: cls._ts_to_ms(e["timestamp"]))
        post_switch = [e for e in sorted_events if cls._ts_to_ms(e["timestamp"]) >= switch_time_ms]

        reg_success_idx = None
        for i in range(len(post_switch) - 1, -1, -1):
            if post_switch[i]["event"] == "registration_success":
                reg_success_idx = i
                break

        if reg_success_idx is None:
            # v7.4.10: RRC-only recovery (isolate-gnb). A gNB restart drops
            # only the RRC connection; the NAS context survives, so UEs come
            # back with an RRC re-establishment and NO new registration.
            # Previously such runs reported recovered=False and null latency
            # even though service was restored. Detect the first post-switch
            # service-restoration signal instead.
            for e in post_switch:
                if e["event"] in ("mm_registered", "rrc_connected"):
                    m["recovered"] = True
                    m["recovery_type"] = "rrc_reestablishment"
                    m["reallocation_latency_ms"] = (
                        cls._ts_to_ms(e["timestamp"]) - switch_time_ms
                    )
                    break
            return m

        reg_success_event = post_switch[reg_success_idx]
        reg_success_ts = cls._ts_to_ms(reg_success_event["timestamp"])

        init_reg_event = None
        for i in range(reg_success_idx - 1, -1, -1):
            if post_switch[i]["event"] == "initial_registration":
                init_reg_event = post_switch[i]
                break

        tun_up_event = None
        for i in range(reg_success_idx + 1, len(post_switch)):
            if post_switch[i]["event"] == "tun_up":
                tun_up_event = post_switch[i]
                break

        # v7 FIX: RRC must come from the CURRENT registration cycle.
        # v6 searched backwards for any rrc_connected before initial_registration
        # with no lower bound, so failed Service-Request cycles from a previous
        # container lifetime supplied stale RRC-CONNECTED events (observed
        # artifact: rrc_latency_ms of 1-13 s). Rules now:
        #   - never cross the last MM-DEREGISTERED before Initial Registration
        #     (a deregistration ends the cycle any stale RRC belongs to);
        #   - UERANSIM often logs RRC-CONNECTED in the same millisecond as (or
        #     just after) "Sending Initial Registration", so accept candidates
        #     within a small forward skew too;
        #   - discard anything implying a latency above RRC_MAX_LATENCY_MS.
        RRC_FORWARD_SKEW_MS = 500
        RRC_MAX_LATENCY_MS = 2000
        rrc_event = None
        if init_reg_event is not None:
            init_ts = cls._ts_to_ms(init_reg_event["timestamp"])
            boundary_ts = None
            for i in range(reg_success_idx - 1, -1, -1):
                if post_switch[i]["event"] == "mm_deregistered":
                    boundary_ts = cls._ts_to_ms(post_switch[i]["timestamp"])
                    break
            best = None
            for e in post_switch:
                if e["event"] != "rrc_connected":
                    continue
                rrc_ts = cls._ts_to_ms(e["timestamp"])
                if boundary_ts is not None and rrc_ts < boundary_ts:
                    continue
                if rrc_ts > init_ts + RRC_FORWARD_SKEW_MS:
                    continue
                if init_ts - rrc_ts > RRC_MAX_LATENCY_MS:
                    continue
                if best is None or abs(rrc_ts - init_ts) < abs(
                    cls._ts_to_ms(best["timestamp"]) - init_ts
                ):
                    best = e
            rrc_event = best

        m["recovered"] = True
        m["recovery_type"] = "re-registration"
        m["reallocation_latency_ms"] = reg_success_ts - switch_time_ms

        if restart_time_ms is not None:
            m["post_restart_latency_ms"] = reg_success_ts - restart_time_ms

        if init_reg_event is not None:
            init_ts = cls._ts_to_ms(init_reg_event["timestamp"])
            m["registration_latency_ms"] = reg_success_ts - init_ts
            if rrc_event is not None:
                # RRC latency = RRC-CONNECTED -> Initial Registration send.
                # Clamped at 0: UERANSIM may log both in the same millisecond.
                m["rrc_latency_ms"] = max(
                    0.0, init_ts - cls._ts_to_ms(rrc_event["timestamp"])
                )

        if tun_up_event is not None:
            m["session_setup_latency_ms"] = cls._ts_to_ms(tun_up_event["timestamp"]) - reg_success_ts
            m["new_ip"] = tun_up_event.get("ip")

        pre_switch = [e for e in sorted_events if cls._ts_to_ms(e["timestamp"]) < switch_time_ms]
        old_tuns = [e for e in pre_switch if e["event"] == "tun_up"]
        if old_tuns:
            m["old_ip"] = old_tuns[-1].get("ip")

        if m["old_ip"] and m["new_ip"]:
            m["ip_changed"] = m["old_ip"] != m["new_ip"]

        return m

    @classmethod
    def compute_ue_mobility_metrics(
        cls,
        ue_events: List[Dict],
        trigger_time_ms: float,
        ue_name: str,
    ) -> Dict:
        """Exp8 (n14-mobility) per-UE metrics.

        Unlike compute_ue_metrics, the recovery event may be EITHER a mobility
        registration (N14 path, GUTI preserved — the thesis result) OR a fall-
        back initial registration (N14 failed — same outcome as exps 1-7). The
        first post-trigger success of either kind defines the outcome.
        Session continuity: NO new tun_up after the trigger means the PDU
        session survived the AMF re-allocation (no re-establishment).
        """
        m = {
            "ue": ue_name,
            "switch_time_ms": trigger_time_ms,  # trigger = gnb1 stop (kept key
                                                # name for exporter/plotter compat)
            "restart_time_ms": None,
            "reallocation_latency_ms": None,
            "registration_latency_ms": None,
            "session_setup_latency_ms": None,
            "rrc_latency_ms": None,
            "post_restart_latency_ms": None,
            "ip_changed": False,
            "old_ip": None,
            "new_ip": None,
            "recovered": False,
            # exp8-specific
            "mobility_registration": False,
            "fell_back_to_initial": False,
            "session_continuity": None,
            "events": ue_events,
        }

        sorted_events = sorted(ue_events, key=lambda e: cls._ts_to_ms(e["timestamp"]))
        post = [e for e in sorted_events if cls._ts_to_ms(e["timestamp"]) >= trigger_time_ms]
        pre = [e for e in sorted_events if cls._ts_to_ms(e["timestamp"]) < trigger_time_ms]

        # Baseline IP: last TUN setup BEFORE the trigger.
        pre_tuns = [e for e in pre if e["event"] == "tun_up"]
        if pre_tuns:
            m["old_ip"] = pre_tuns[-1].get("ip")

        # Recovery = earliest post-trigger registration success of either kind.
        recovery_event = None
        for e in post:
            if e["event"] in ("mobility_registration_success", "registration_success"):
                recovery_event = e
                break
        if recovery_event is None:
            return m

        recovery_ts = cls._ts_to_ms(recovery_event["timestamp"])
        m["recovered"] = True
        m["reallocation_latency_ms"] = recovery_ts - trigger_time_ms
        m["mobility_registration"] = recovery_event["event"] == "mobility_registration_success"
        m["fell_back_to_initial"] = recovery_event["event"] == "registration_success"

        # Registration send event that started this cycle.
        send_names = ("mobility_registration",) if m["mobility_registration"] else ("initial_registration",)
        send_event = None
        for e in post:
            if e["event"] in send_names and cls._ts_to_ms(e["timestamp"]) <= recovery_ts:
                send_event = e  # keep scanning: last send before success
            elif recovery_ts < cls._ts_to_ms(e["timestamp"]):
                break
        if send_event is not None:
            send_ts = cls._ts_to_ms(send_event["timestamp"])
            m["registration_latency_ms"] = recovery_ts - send_ts
            # RRC: nearest RRC-CONNECTED before/around the send, bounded.
            RRC_FORWARD_SKEW_MS = 500
            RRC_MAX_LATENCY_MS = 2000
            best = None
            for e in post:
                if e["event"] != "rrc_connected":
                    continue
                rrc_ts = cls._ts_to_ms(e["timestamp"])
                if rrc_ts > send_ts + RRC_FORWARD_SKEW_MS:
                    continue
                if send_ts - rrc_ts > RRC_MAX_LATENCY_MS:
                    continue
                if best is None or abs(rrc_ts - send_ts) < abs(
                    cls._ts_to_ms(best["timestamp"]) - send_ts
                ):
                    best = e
            if best is not None:
                m["rrc_latency_ms"] = max(0.0, send_ts - cls._ts_to_ms(best["timestamp"]))

        # Session continuity: a NEW tun_up post-trigger means the PDU session
        # was re-established (same failure mode as exps 1-7). No tun_up => the
        # session survived (N14 transferred the SM context).
        post_tuns = [e for e in post if e["event"] == "tun_up"]
        if post_tuns:
            m["new_ip"] = post_tuns[-1].get("ip")
            m["session_continuity"] = False
            m["session_setup_latency_ms"] = (
                cls._ts_to_ms(post_tuns[-1]["timestamp"]) - recovery_ts
            )
            if m["old_ip"]:
                m["ip_changed"] = m["old_ip"] != m["new_ip"]
        else:
            m["new_ip"] = m["old_ip"]  # unchanged by definition
            m["session_continuity"] = m["old_ip"] is not None
            m["ip_changed"] = False

        return m

    @classmethod
    def compute_aggregate(cls, ue_results: List[Dict]) -> Dict:
        def _extract(key: str) -> List[float]:
            return [r[key] for r in ue_results if r.get(key) is not None]

        agg = {}
        for key in (
            "reallocation_latency_ms",
            "registration_latency_ms",
            "session_setup_latency_ms",
            "rrc_latency_ms",
            "post_restart_latency_ms",
        ):
            vals = sorted(_extract(key))
            if not vals:
                agg[key] = {
                    "mean": None,
                    "std": None,
                    "min": None,
                    "max": None,
                    "p95": None,
                    "cv": None,
                    "count": 0,
                }
                continue
            mean_val = statistics.mean(vals)
            std_val = statistics.stdev(vals) if len(vals) > 1 else 0.0
            p95_idx = int(len(vals) * 0.95)
            p95 = vals[min(p95_idx, len(vals) - 1)]
            agg[key] = {
                "mean": mean_val,
                "std": std_val,
                "min": min(vals),
                "max": max(vals),
                "p95": p95,
                "cv": (std_val / mean_val) if mean_val > 0 else 0.0,
                "count": len(vals),
            }
        agg["recovered_count"] = sum(1 for r in ue_results if r.get("recovered"))
        agg["ip_changed_count"] = sum(1 for r in ue_results if r.get("ip_changed"))
        return agg

# -- Experiment Runner ----------------------------------------------
class ExperimentRunner:
    def __init__(
        self,
        proxy_host: str,
        proxy_port: int,
        proxy2_host: str,
        proxy2_port: int,
        output_dir: str,
        ue_pool: List[str],
        gnb_pool: List[str],
        config_dir: str = DEFAULT_CONFIG_DIR,
        compose_dir: str = ".",
        compose_file: str = "",
        env_file: str = "",
    ):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.proxy2_host = proxy2_host
        self.proxy2_port = proxy2_port
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.parser = LogParser()
        self.computer = MetricComputer()
        self.ue_pool = ue_pool
        self.gnb_pool = gnb_pool
        self.config_dir = Path(config_dir)
        self.compose_dir = compose_dir
        self.compose_file = compose_file
        self.env_file = env_file
        self._config_backups: Dict[str, str] = {}

    def _ue_config_path(self, ue_name: str) -> Path:
        return self.config_dir / f"{ue_name}.yaml"

    def _backup_config(self, ue_name: str) -> str:
        return self._ue_config_path(ue_name).read_text()

    def _restore_config(self, ue_name: str, content: str):
        self._ue_config_path(ue_name).write_text(content)

    def _set_gnb_preference(self, ue_name: str, gnb2_first: bool = True, exclusive: bool = False):
        """Rewrite the gnbSearchList block of ueN.yaml so the preferred gNB is
        first, then VERIFY by re-reading the file.

        Why this replaces v6's _swap_gnb_order:
          - The static ueN.yaml files keep gnb2 COMMENTED OUT so UEs boot
            cleanly while gnb2 is not running (Docker DNS only resolves
            gnb2.ueransim.org when the container exists). v6's insert-check
            (`if "gnb2.ueransim.org" not in text`) was defeated by the
            commented occurrence, and its swap regex required two consecutive
            uncommented lines — a guaranteed silent no-op.
          - Single-file bind mounts follow the INODE: the rewrite must modify
            the file in place (Path.write_text truncates and rewrites the
            same inode). Never write-temp-then-rename here.
          - Failure must be loud: raise before the proxy switch instead of
            producing 6/6 "recovered" results that never touched gnb2.
        """
        path = self._ue_config_path(ue_name)
        text = path.read_text()

        preferred = "gnb2.ueransim.org" if gnb2_first else "gnb1.ueransim.org"
        fallback = "gnb1.ueransim.org" if gnb2_first else "gnb2.ueransim.org"
        # v7.2: exclusive mode writes ONLY the preferred gNB. With both gNBs
        # listed, UERANSIM's cell selection among equal-strength simulated
        # signals is a timing race — observed 2026-08-18: ue4 fell back to
        # tac[1] in BOTH split modes despite gnb2-first configs (confirmed by
        # AMF-side TAC/CellID). Exclusive mode makes re-selection
        # deterministic: the mover either attaches via gnb2 or fails loudly.
        if exclusive:
            new_block = f"gnbSearchList:\n  - {preferred}\n"
        else:
            new_block = f"gnbSearchList:\n  - {preferred}\n  - {fallback}\n"

        # Replace the whole gnbSearchList block, tolerant of any mix of
        # commented/uncommented gnb1/gnb2 entries and indentation.
        new_text, n = re.subn(
            r"gnbSearchList:\s*\n(?:\s*#?\s*-\s*gnb[12]\.ueransim\.org[^\n]*\n?)+",
            new_block,
            text,
        )
        if n == 0:
            raise RuntimeError(f"{ue_name}: gnbSearchList block not found in {path}")

        path.write_text(new_text)

        # Post-write verification: the first ACTIVE entry must be `preferred`.
        verify = path.read_text()
        m = re.search(r"gnbSearchList:\s*\n\s*-\s*(\S+)", verify)
        if not m or m.group(1) != preferred:
            raise RuntimeError(
                f"{ue_name}: config rewrite failed verification "
                f"(first gnbSearchList entry is {m.group(1) if m else 'none'}, "
                f"expected {preferred}); aborting"
            )
        print(f"[CONFIG] {ue_name}: gnbSearchList now starts with {preferred}")

    def _restart_container_tracked(self, container: str) -> Tuple[bool, Optional[float]]:
        """Restart a container and return the restart anchor time in epoch ms.

        v7 FIX: v6 slept 0.5 s BEFORE capturing time.time(), so the captured
        "restart" timestamp systematically landed AFTER the UE had already
        registered (UERANSIM registers in ~0.1-0.5 s; observed artifact:
        post_restart_latency_ms ~= -490 ms on every run).

        Primary anchor: docker inspect StartedAt (UTC, timezone-aware parsed —
        consistent with _ts_to_ms). Fallback: time.time() captured IMMEDIATELY
        after `docker restart` returns (no sleep).
        """
        ok = restart_container(container)
        fallback_ms = time.time() * 1000  # captured right after restart returns
        if not ok:
            return False, None
        start_ms = container_started_ms(container)
        if start_ms is None:
            start_ms = fallback_ms
        return ok, start_ms

    def _restart_gnb_tracked(self, gnb: str) -> bool:
        """Restart a gNB and wait for internal AMF context to stabilize.
        v6 FIX: Adds 3-second delay to avoid UERANSIM race
        'AMF context not found with id: 0'."""
        ok = restart_container(gnb)
        if ok:
            time.sleep(3)
        return ok

    def _get_proxy_switch_time_ms(self, target_amf: str, proxy_container: str = "sctp-proxy") -> Optional[float]:
        for _ in range(5):
            raw = docker_logs(proxy_container, tail=100)
            ts_str = self.parser.parse_proxy_switch_time(raw, target_amf)
            if ts_str:
                return self.computer._ts_to_ms(ts_str)
            time.sleep(0.5)
        return None

    def _switch_backend(self, target_amf: str) -> bool:
        return switch_both_backends(
            target_amf,
            self.proxy_host, self.proxy_port,
            self.proxy2_host, self.proxy2_port,
        )

    def run_baseline_check(self) -> bool:
        print("[1/5] Baseline check...")
        if not container_running(CONTAINERS["proxy"]):
            print("[ERROR] sctp-proxy container not running!")
            return False
        for ue in self.ue_pool:
            logs = self.parser.parse_ue(docker_logs(ue, tail=100), ue)
            if not any(e["event"] == "mm_registered" for e in logs):
                print(f"[WARN] {ue} not currently registered. Proceeding anyway...")
        return True

    def wait_for_ue_recovery(
        self,
        ue_name: str,
        switch_time_ms: float,
        timeout: float,
        interval: float = 2.0,
        restart_time_ms: Optional[float] = None,
    ) -> Dict:
        elapsed = 0.0
        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            ue_logs = self.parser.parse_ue(docker_logs(ue_name, tail=500), ue_name)
            metrics = self.computer.compute_ue_metrics(ue_logs, switch_time_ms, ue_name, restart_time_ms)
            if metrics.get("recovered"):
                return metrics
        return self.computer.compute_ue_metrics(
            self.parser.parse_ue(docker_logs(ue_name, tail=500), ue_name),
            switch_time_ms,
            ue_name,
            restart_time_ms,
        )

    def wait_for_all_recovery(
        self,
        switch_time_ms: float,
        timeout: float,
        target_pool: Optional[List[str]] = None,
        restart_times: Optional[Dict[str, float]] = None,
        interval: float = 2.0,
    ) -> List[Dict]:
        pool = target_pool if target_pool is not None else self.ue_pool
        pending = set(pool)
        results: Dict[str, Dict] = {}
        elapsed = 0.0
        while elapsed < timeout and pending:
            time.sleep(interval)
            elapsed += interval
            for ue in list(pending):
                ue_logs = self.parser.parse_ue(docker_logs(ue, tail=500), ue)
                rt = restart_times.get(ue) if restart_times else None
                metrics = self.computer.compute_ue_metrics(ue_logs, switch_time_ms, ue, rt)
                if metrics.get("recovered"):
                    results[ue] = metrics
                    pending.remove(ue)
                    lat = metrics.get("reallocation_latency_ms")
                    lat_str = f"{lat:.0f} ms" if lat is not None else "N/A"
                    print(f"  -> {ue} re-registered (realloc latency: {lat_str})")
        for ue in pending:
            print(f"  [WARN] {ue} did not re-register within {timeout}s")
            ue_logs = self.parser.parse_ue(docker_logs(ue, tail=500), ue)
            rt = restart_times.get(ue) if restart_times else None
            results[ue] = self.computer.compute_ue_metrics(ue_logs, switch_time_ms, ue, rt)
        return [results[ue] for ue in pool]

    # ------------------------------------------------------------------
    # Legacy Modes (1-5)  --  ONLY gnb1 is ever restarted
    # ------------------------------------------------------------------
    def run_sequential(
        self, target_amf: str, wait_before: float, wait_after: float, stagger: float = 0.0
    ) -> Dict:
        print(f"\n[Mode: SEQUENTIAL] Target: {target_amf} | UEs: {self.ue_pool}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}
        time.sleep(wait_before)

        print(f"[3/5] Switching both proxies to {target_amf}...")
        if not self._switch_backend(target_amf):
            return {"error": "switch failed"}
        time.sleep(1.0)
        t0 = self._get_proxy_switch_time_ms(target_amf) or (time.time() * 1000)
        print(f"[INFO] Switch time: {datetime.fromtimestamp(t0/1000).isoformat()}")

        restart_container(LEGACY_GNB)
        if stagger > 0:
            time.sleep(2)

        results = []
        for ue in self.ue_pool:
            _, rt = self._restart_container_tracked(ue)
            if stagger > 0:
                time.sleep(stagger)
            results.append(
                self.wait_for_ue_recovery(ue, t0, timeout=wait_after, restart_time_ms=rt)
            )

        return {
            "mode": "sequential",
            "target_amf": target_amf,
            "switch_time_ms": t0,
            "ue_results": results,
            "aggregate": self.computer.compute_aggregate(results),
            "experiment_timestamp": datetime.now().isoformat(),
        }

    def run_concurrent(self, target_amf: str, wait_before: float, wait_after: float) -> Dict:
        print(f"\n[Mode: CONCURRENT] Target: {target_amf} | UEs: {self.ue_pool}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}
        time.sleep(wait_before)

        print(f"[3/5] Switching both proxies to {target_amf}...")
        if not self._switch_backend(target_amf):
            return {"error": "switch failed"}
        time.sleep(1.0)
        t0 = self._get_proxy_switch_time_ms(target_amf) or (time.time() * 1000)
        print(f"[INFO] Switch time: {datetime.fromtimestamp(t0/1000).isoformat()}")

        restart_container(LEGACY_GNB)
        time.sleep(2)
        restart_times: Dict[str, Optional[float]] = {}
        for ue in self.ue_pool:
            _, rt = self._restart_container_tracked(ue)
            restart_times[ue] = rt

        results = self.wait_for_all_recovery(t0, timeout=wait_after, restart_times=restart_times)
        return {
            "mode": "concurrent",
            "target_amf": target_amf,
            "switch_time_ms": t0,
            "ue_results": results,
            "aggregate": self.computer.compute_aggregate(results),
            "experiment_timestamp": datetime.now().isoformat(),
        }

    def run_backforth(self, repeat: int, wait_before: float, wait_after: float, cooldown: float) -> List[Dict]:
        targets = ["amf2" if i % 2 == 0 else "amf1" for i in range(repeat)]
        all_results = []
        for i, target in enumerate(targets):
            print(f"\n### Back-forth run {i+1}/{repeat} -> {target} ###")
            all_results.append(self.run_concurrent(target, wait_before, wait_after))
            if i < len(targets) - 1:
                print(f"Cooling down for {cooldown}s...")
                time.sleep(cooldown)
        return all_results

    def run_partial(
        self, target_amf: str, wait_before: float, wait_after: float, ue_subset: List[str]
    ) -> Dict:
        print(f"\n[Mode: PARTIAL] Target: {target_amf} | Subset: {ue_subset}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}
        time.sleep(wait_before)

        print(f"[3/5] Switching both proxies to {target_amf}...")
        if not self._switch_backend(target_amf):
            return {"error": "switch failed"}
        time.sleep(1.0)
        t0 = self._get_proxy_switch_time_ms(target_amf) or (time.time() * 1000)
        print(f"[INFO] Switch time: {datetime.fromtimestamp(t0/1000).isoformat()}")

        restart_container(LEGACY_GNB)
        time.sleep(2)

        restart_times: Dict[str, Optional[float]] = {}
        for ue in ue_subset:
            _, rt = self._restart_container_tracked(ue)
            restart_times[ue] = rt

        results = []
        for ue in self.ue_pool:
            if ue in ue_subset:
                results.append(
                    self.wait_for_ue_recovery(
                        ue, t0, timeout=wait_after,
                        restart_time_ms=restart_times.get(ue),
                    )
                )
            else:
                results.append(
                    self.computer.compute_ue_metrics(
                        self.parser.parse_ue(docker_logs(ue, tail=500), ue), t0, ue
                    )
                )

        # v7.5.1: collateral-disturbance semantics. gnb1 is restarted as part of
        # this mode, so NON-subset UEs can be bounced into re-registration even
        # though they were never meant to move (observed at 12 UEs: ue7-ue12
        # re-registered post-switch, inflating recovered_count to 12/12).
        # Reclassify: non-subset UEs are never "recovered"; post-switch activity
        # on them is reported separately as collateral disturbance.
        for r in results:
            if r["ue"] in ue_subset:
                continue
            if r.get("recovered"):
                r["disturbed"] = True
                r["recovery_type"] = (
                    "collateral_reregistration"
                    if r.get("recovery_type") == "re-registration"
                    else "collateral_rrc_activity"
                )
                r["recovered"] = False
                print(f"[WARN] {r['ue']}: collateral disturbance (non-subset UE "
                      f"re-registered after switch; kept IP: {not r.get('ip_changed')})")
            else:
                r["disturbed"] = False

        agg = self.computer.compute_aggregate(results)
        agg["collateral_disturbed_count"] = sum(
            1 for r in results if r.get("disturbed")
        )

        return {
            "mode": "partial",
            "target_amf": target_amf,
            "ue_subset": ue_subset,
            "switch_time_ms": t0,
            "ue_results": results,
            "aggregate": agg,
            "experiment_timestamp": datetime.now().isoformat(),
        }

    def run_isolate_gnb(self, target_amf: str, wait_before: float, wait_after: float) -> Dict:
        """Experiment 5: ISOLATE-GNB semantics preserved from v5.
        ONLY gnb1 is restarted; UEs are left running to test auto-recovery."""
        print(f"\n[Mode: ISOLATE-GNB] Target: {target_amf}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}
        time.sleep(wait_before)

        print(f"[3/5] Switching both proxies to {target_amf}...")
        if not self._switch_backend(target_amf):
            return {"error": "switch failed"}
        time.sleep(1.0)
        t0 = self._get_proxy_switch_time_ms(target_amf) or (time.time() * 1000)
        print(f"[INFO] Switch time: {datetime.fromtimestamp(t0/1000).isoformat()}")

        restart_container(LEGACY_GNB)
        results = self.wait_for_all_recovery(t0, timeout=wait_after)
        return {
            "mode": "isolate-gnb",
            "target_amf": target_amf,
            "switch_time_ms": t0,
            "ue_results": results,
            "aggregate": self.computer.compute_aggregate(results),
            "experiment_timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # New Split-gNB Modes (6-7)  --  v6 FIXES applied here
    # ------------------------------------------------------------------
    def _validate_subset(self, ue_subset: List[str]) -> Tuple[bool, str]:
        if not ue_subset:
            return False, "ue_subset cannot be empty"
        invalid = [u for u in ue_subset if u not in self.ue_pool]
        if invalid:
            return False, f"Invalid UE names in subset: {invalid}"
        if set(ue_subset) == set(self.ue_pool):
            return False, "ue_subset cannot contain all UEs (nothing to compare)"
        return True, ""

    def _verify_gnb2_selected(self, ue_name: str) -> bool:
        """Post-hoc check: does the UE's CURRENT container lifetime contain a
        'Selected cell ... tac[2]' line? Stamped into results so a mover that
        silently fell back to gnb1 cannot masquerade as a re-selection."""
        started_ms = container_started_ms(ue_name)
        logs = docker_logs(ue_name, tail=200)
        pat = re.compile(
            r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\].*Selected cell .*tac\[2\]"
        )
        for line in logs.splitlines():
            m = pat.search(line)
            if not m:
                continue
            try:
                line_ms = (
                    datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
                    .replace(tzinfo=timezone.utc)
                    .timestamp() * 1000
                )
            except ValueError:
                continue
            if started_ms is None or line_ms >= started_ms:
                return True
        return False

    def _stamp_gnb2_selection(self, results: List[Dict], movers: List[str]):
        for r in results:
            if r.get("ue") in movers and r.get("recovered"):
                r["gnb2_selected"] = self._verify_gnb2_selected(r["ue"])
                if not r["gnb2_selected"]:
                    print(
                        f"[WARN] {r['ue']}: no tac[2] cell selection in current "
                        f"lifetime — registered via gnb1; NOT a gNB re-selection"
                    )

    def _return_movers_to_gnb1(self, movers: List[str], settle_s: float = 8.0):
        """Restart movers AFTER config restore so they re-register via gnb1.

        v7.3: with exclusive (gnb2-only) configs, a running mover's IN-MEMORY
        gnbSearchList still contains only gnb2 after the on-disk config is
        restored. When gnb2 later goes away (proxy switch-back tears down its
        AMF association; run_all_experiments_v2.sh stops gnb2 for the clean
        baseline), those movers enter a permanent 'no cells in coverage' loop
        — they cannot see gnb1 even though it is up. Restarting them with the
        restored gnb1-first config returns the network to a homogeneous end
        state. Runs AFTER results are collected, so metrics are unaffected.
        """
        print("[CLEANUP] Restarting movers onto gnb1 (restored configs)...")
        for ue in movers:
            restart_container(ue)
        time.sleep(settle_s)

    def run_split_gnb_simultaneous(
        self, target_amf: str, wait_before: float, wait_after: float, ue_subset: List[str]
    ) -> Dict:
        ok, msg = self._validate_subset(ue_subset)
        if not ok:
            return {"error": msg}

        stayers = [u for u in self.ue_pool if u not in ue_subset]
        print(f"\n[Mode: SPLIT-GNB SIMULTANEOUS] Target: {target_amf} | Movers: {ue_subset} | Stayers: {stayers}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}

        # v7: rewrite mover configs (gnb2 first). A verification failure aborts
        # BEFORE the proxy switch, restoring any configs already rewritten.
        try:
            for ue in ue_subset:
                self._config_backups[ue] = self._backup_config(ue)
                self._set_gnb_preference(ue, gnb2_first=True, exclusive=True)
        except RuntimeError as exc:
            print(f"[ERROR] {exc}")
            for ue, content in self._config_backups.items():
                self._restore_config(ue, content)
            return {"error": f"config rewrite failed: {exc}"}

        time.sleep(wait_before)

        print(f"[3/5] Switching both proxies to {target_amf}...")
        if not self._switch_backend(target_amf):
            for ue in ue_subset:
                self._restore_config(ue, self._config_backups[ue])
            return {"error": "switch failed"}
        time.sleep(1.0)
        t0 = self._get_proxy_switch_time_ms(target_amf) or (time.time() * 1000)
        print(f"[INFO] Switch time: {datetime.fromtimestamp(t0/1000).isoformat()}")

        print("[4/5] Restarting gNB1 and gNB2...")
        self._restart_gnb_tracked(LEGACY_GNB)
        self._restart_gnb_tracked("gnb2")
        if not wait_for_gnb_ng_setup("gnb2", timeout=10.0):
            print("[WARN] gnb2 NG Setup not confirmed; proceeding anyway...")

        # v6 FIX: Restart stayers too (keeping gnb1 preference)
        restart_times: Dict[str, Optional[float]] = {}
        print("[4.5/5] Restarting stayer UEs (keeping gnb1 preference)...")
        for ue in stayers:
            _, rt = self._restart_container_tracked(ue)
            restart_times[ue] = rt
        time.sleep(2)

        print("[5/5] Restarting mover UEs...")
        for ue in ue_subset:
            _, rt = self._restart_container_tracked(ue)
            restart_times[ue] = rt

        results = self.wait_for_all_recovery(t0, timeout=wait_after, restart_times=restart_times)
        self._stamp_gnb2_selection(results, ue_subset)

        for ue in ue_subset:
            self._restore_config(ue, self._config_backups[ue])

        return {
            "mode": "split-gnb-simultaneous",
            "target_amf": target_amf,
            "switch_time_ms": t0,
            "ue_results": results,
            "aggregate": self.computer.compute_aggregate(results),
            "experiment_timestamp": datetime.now().isoformat(),
        }

    def run_split_gnb_sequential(
        self, target_amf: str, wait_before: float, wait_after: float, ue_subset: List[str]
    ) -> Dict:
        ok, msg = self._validate_subset(ue_subset)
        if not ok:
            return {"error": msg}

        stayers = [u for u in self.ue_pool if u not in ue_subset]
        print(f"\n[Mode: SPLIT-GNB SEQUENTIAL] Target: {target_amf} | Movers: {ue_subset} | Stayers: {stayers}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}

        # v7: rewrite mover configs (gnb2 first). A verification failure aborts
        # BEFORE the proxy switch, restoring any configs already rewritten.
        try:
            for ue in ue_subset:
                self._config_backups[ue] = self._backup_config(ue)
                self._set_gnb_preference(ue, gnb2_first=True, exclusive=True)
        except RuntimeError as exc:
            print(f"[ERROR] {exc}")
            for ue, content in self._config_backups.items():
                self._restore_config(ue, content)
            return {"error": f"config rewrite failed: {exc}"}

        time.sleep(wait_before)

        print(f"[3/6] Switching both proxies to {target_amf}...")
        if not self._switch_backend(target_amf):
            for ue in ue_subset:
                self._restore_config(ue, self._config_backups[ue])
            return {"error": "switch failed"}
        time.sleep(1.0)
        t0 = self._get_proxy_switch_time_ms(target_amf) or (time.time() * 1000)
        print(f"[INFO] Switch time: {datetime.fromtimestamp(t0/1000).isoformat()}")

        # v6 FIX: Restart gNB1 AND stayers immediately, then wait for recovery.
        # This eliminates the 60-second dead wait where stayers would never recover.
        print("[4/6] Restarting gNB1 and Stayers...")
        self._restart_gnb_tracked(LEGACY_GNB)

        restart_times: Dict[str, Optional[float]] = {}
        for ue in stayers:
            _, rt = self._restart_container_tracked(ue)
            restart_times[ue] = rt
        time.sleep(2)

        stayer_results = self.wait_for_all_recovery(
            t0, timeout=wait_after, target_pool=stayers, restart_times=restart_times
        )

        print("[5/6] Starting gNB2 and restarting Movers...")
        self._restart_gnb_tracked("gnb2")
        if not wait_for_gnb_ng_setup("gnb2", timeout=10.0):
            print("[WARN] gnb2 NG Setup not confirmed; proceeding anyway...")
        # v7.1: NG Setup confirms SCTP/NGAP, not radio readiness. On 2026-08-18,
        # movers booted ~3 s after gnb2's restart and ALL fell back to tac[1];
        # in the simultaneous mode (~6.5 s gap) all selected tac[2]. Give the
        # gnb2 radio (RLS) time to beacon before the movers scan.
        time.sleep(3)

        for ue in ue_subset:
            _, rt = self._restart_container_tracked(ue)
            restart_times[ue] = rt

        mover_results = self.wait_for_all_recovery(
            t0, timeout=wait_after, target_pool=ue_subset, restart_times=restart_times
        )
        self._stamp_gnb2_selection(mover_results, ue_subset)

        for ue in ue_subset:
            self._restore_config(ue, self._config_backups[ue])
        self._return_movers_to_gnb1(ue_subset)

        all_results = stayer_results + mover_results
        return {
            "mode": "split-gnb-sequential",
            "target_amf": target_amf,
            "switch_time_ms": t0,
            "ue_results": all_results,
            "aggregate": self.computer.compute_aggregate(all_results),
            "experiment_timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Experiment 8: N14-enabled mobility re-allocation  (v7.4)
    # ------------------------------------------------------------------
    def _amf_config_path(self, amf_name: str) -> Path:
        return self.config_dir / f"{amf_name}.yaml"

    @staticmethod
    def _tai_block_text(text: str) -> Optional[str]:
        """Return the YAML `tai:` block (header line + indented entries)."""
        lines = text.splitlines(keepends=True)
        for i, ln in enumerate(lines):
            m = re.match(r"^(\s*)tai:\s*$", ln)
            if not m:
                continue
            indent = len(m.group(1))
            j = i + 1
            while j < len(lines):
                l2 = lines[j]
                if l2.strip() and (len(l2) - len(l2.lstrip())) <= indent:
                    break
                j += 1
            return "".join(lines[i:j])
        return None

    def _strip_tac_from_amf_tai(self, text: str, tac: int) -> str:
        """Remove the tai list entry whose tac is `tac` (line-based, tolerant
        of indentation). Raises RuntimeError if the entry is not found."""
        lines = text.splitlines(keepends=True)
        tai_idx = None
        indent = 0
        for i, ln in enumerate(lines):
            m = re.match(r"^(\s*)tai:\s*$", ln)
            if m:
                tai_idx, indent = i, len(m.group(1))
                break
        if tai_idx is None:
            raise RuntimeError("tai block not found in amf config")
        j = tai_idx + 1
        while j < len(lines):
            l2 = lines[j]
            if l2.strip() and (len(l2) - len(l2.lstrip())) <= indent:
                break
            j += 1
        block = lines[tai_idx + 1:j]
        # Split block into list entries (each starts at a '- ' line).
        entries: List[List[str]] = []
        cur: List[str] = []
        for ln in block:
            if re.match(r"^\s*-\s", ln) and cur:
                entries.append(cur)
                cur = [ln]
            else:
                cur.append(ln)
        if cur:
            entries.append(cur)
        kept = [e for e in entries if not re.search(rf"tac:\s*{tac}\b", "".join(e))]
        if len(kept) == len(entries):
            raise RuntimeError(f"tac: {tac} entry not found in tai block")
        return "".join(lines[:tai_idx + 1] + [ln for e in kept for ln in e] + lines[j:])

    def _last_selected_tac(self, ue_name: str, since_ms: Optional[float] = None) -> Optional[int]:
        """TAC of the LAST 'Selected cell ... tac[N]' line in the UE log
        (optionally only lines timestamped >= since_ms). None if no match."""
        logs = docker_logs(ue_name, tail=300)
        pat = re.compile(
            r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\].*Selected cell .*tac\[(\d+)\]"
        )
        last_tac = None
        for line in logs.splitlines():
            m = pat.search(line)
            if not m:
                continue
            if since_ms is not None:
                try:
                    line_ms = (
                        datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
                        .replace(tzinfo=timezone.utc)
                        .timestamp() * 1000
                    )
                except ValueError:
                    continue
                if line_ms < since_ms:
                    continue
            last_tac = int(m.group(2))
        return last_tac

    def _gnb2_radio_visible(self, since_ms: Optional[float]) -> int:
        """How many UEs have logged gnb2's RLS beacon (">=2 cells in coverage")
        since `since_ms`. gnb2's radio has repeatedly been observed silent
        after container (re)start; this detects it."""
        pat = re.compile(
            r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\].*total \[(\d+)\] cells in coverage"
        )
        count = 0
        for ue in self.ue_pool:
            logs = docker_logs(ue, tail=100)
            visible = False
            for line in logs.splitlines():
                m = pat.search(line)
                if not m or int(m.group(2)) < 2:
                    continue
                if since_ms is not None:
                    try:
                        line_ms = (
                            datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
                            .replace(tzinfo=timezone.utc)
                            .timestamp() * 1000
                        )
                    except ValueError:
                        continue
                    if line_ms < since_ms:
                        continue
                visible = True
            if visible:
                count += 1
        return count

    def _wait_for_gnb2_radio(self, timeout: float = 45.0) -> bool:
        """Wait until all UEs see gnb2's RLS beacon; one restart retry.
        Aborts the experiment instead of wasting the trigger on a dead radio."""
        anchor = container_started_ms("gnb2")
        for attempt in (1, 2):
            deadline = time.time() + timeout
            while time.time() < deadline:
                n = self._gnb2_radio_visible(anchor)
                if n == len(self.ue_pool):
                    print(f"[2/6] gnb2 radio visible to all {n} UEs")
                    return True
                time.sleep(2.0)
            if attempt == 1:
                print("[WARN] gnb2 radio (RLS) not visible to all UEs after "
                      f"{timeout:.0f}s; restarting gnb2 once more...")
                self._restart_gnb_tracked("gnb2")
                if not wait_for_gnb_ng_setup("gnb2", timeout=20.0):
                    print("[ERROR] gnb2 NG Setup not confirmed after retry")
                    return False
                anchor = container_started_ms("gnb2")
        print("[ERROR] gnb2 radio never became visible to the UEs "
              "(RLS beacon issue); aborting BEFORE the trigger so the "
              "run is not wasted. Try `sudo docker restart gnb2`, verify with "
              "`sudo docker logs ue1 | grep 'cells in coverage' | tail`, "
              "then re-run.")
        return False

    def wait_for_mobility_recovery(
        self, ue_name: str, trigger_ms: float, timeout: float, interval: float = 2.0
    ) -> Dict:
        elapsed = 0.0
        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            events = self.parser.parse_ue(docker_logs(ue_name, tail=500), ue_name)
            metrics = self.computer.compute_ue_mobility_metrics(events, trigger_ms, ue_name)
            if metrics.get("recovered"):
                return metrics
        return self.computer.compute_ue_mobility_metrics(
            self.parser.parse_ue(docker_logs(ue_name, tail=500), ue_name),
            trigger_ms,
            ue_name,
        )

    def wait_for_all_mobility(
        self, trigger_ms: float, timeout: float, interval: float = 2.0
    ) -> List[Dict]:
        pending = set(self.ue_pool)
        results: Dict[str, Dict] = {}
        elapsed = 0.0
        while elapsed < timeout and pending:
            time.sleep(interval)
            elapsed += interval
            for ue in list(pending):
                events = self.parser.parse_ue(docker_logs(ue, tail=500), ue)
                metrics = self.computer.compute_ue_mobility_metrics(events, trigger_ms, ue)
                if metrics.get("recovered"):
                    results[ue] = metrics
                    pending.remove(ue)
                    lat = metrics.get("reallocation_latency_ms")
                    lat_str = f"{lat:.0f} ms" if lat is not None else "N/A"
                    kind = "mobility" if metrics.get("mobility_registration") else "INITIAL (fallback)"
                    print(f"  -> {ue} re-registered via {kind} registration (latency: {lat_str})")
        for ue in pending:
            print(f"  [WARN] {ue} did not re-register within {timeout}s")
            events = self.parser.parse_ue(docker_logs(ue, tail=500), ue)
            results[ue] = self.computer.compute_ue_mobility_metrics(events, trigger_ms, ue)
        return [results[ue] for ue in self.ue_pool]

    # Open5GS log lines start with 'MM/DD HH:MM:SS.mmm:' (UTC, no year).
    OGS_TS_RE = re.compile(r"^(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3}):")

    @staticmethod
    def _ogs_line_ms(line: str, now_ms: float) -> Optional[float]:
        """Epoch ms (UTC) of an Open5GS log line, year inferred from now."""
        m = ExperimentRunner.OGS_TS_RE.match(line)
        if not m:
            return None
        mon, day, hh, mm, ss, ms = (int(g) for g in m.groups())
        year = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).year
        try:
            dt = datetime(year, mon, day, hh, mm, ss, ms * 1000, tzinfo=timezone.utc)
        except ValueError:
            return None
        ts = dt.timestamp() * 1000
        # year-boundary guard: if the line lands >12h in the future, it was
        # logged last year (e.g. run over New Year) — not our concern, drop it.
        if ts - now_ms > 12 * 3600 * 1000:
            ts -= 365 * 24 * 3600 * 1000
        return ts

    def _check_n14_evidence(self, trigger_ms: Optional[float] = None) -> Dict:
        """Search BOTH AMFs' logs for N14 UEContextTransfer evidence, anchored
        on the trigger time (v7.4.7).

        v7.4.6 bounded the window with `docker logs --since <StartedAt>`, but
        --since interprets naive timestamps in HOST LOCAL time while StartedAt
        is UTC — on non-UTC hosts the window came out empty. Instead we fetch
        the full logs and anchor on the Open5GS per-line timestamps (UTC)."""
        anchor_ms = trigger_ms if trigger_ms is not None else 0.0
        evidence = {"n14_context_transfer": False, "n14_matches": []}
        now_ms = time.time() * 1000
        for amf in ("amf2", "amf1"):
            logs = docker_logs(amf, tail=200000)
            scanned = matched = 0
            for line in logs.splitlines():
                hit = None
                for pat in LogParser.N14_PATTERNS:
                    if pat.search(line):
                        hit = line
                        break
                if hit is None:
                    continue
                matched += 1
                ts = self._ogs_line_ms(line, now_ms)
                # accept undated matches only if no trigger anchor is given
                if ts is not None and ts < anchor_ms - 5000:
                    continue  # from a previous run / before the trigger
                scanned += 1
                evidence["n14_context_transfer"] = True
                evidence["n14_matches"].append(f"[{amf}] {line.strip()[:200]}")
            print(f"[N14] {amf}: {matched} transfer-like line(s), "
                  f"{scanned} after trigger")
        evidence["n14_matches"] = evidence["n14_matches"][:10]
        if evidence["n14_context_transfer"]:
            print(f"[N14] UEContextTransfer evidence found ({len(evidence['n14_matches'])} line(s))")
        else:
            print("[N14] No UEContextTransfer evidence in AMF logs "
                  "(expected if sbi debug logging is off)")
        return evidence

    def _amf_config_in_container(self, amf_name: str = "amf1") -> Optional[str]:
        """Content of the amf config AS SEEN INSIDE the container (decisive for
        both bind mounts and compose-config mounts)."""
        r = subprocess.run(
            ["sudo", "docker", "exec", amf_name, "cat",
             f"/etc/open5gs/custom/{amf_name}.yaml"],
            capture_output=True, text=True
        )
        return r.stdout if r.returncode == 0 else None

    def _load_amf1_tac1_only(self, swapped_text: str) -> Tuple[bool, str]:
        """Make the RUNNING amf1 load the tac1-only config, with proof.

        v7.4.3: the 2026-08-25 pilot showed `docker compose up -d amf1` can be
        a silent NO-OP (exit 0, container untouched — happens when amf1.yaml is
        a bind-mount volume: compose detects no definition drift). Strategy:
          1. write the swapped file on the host;
          2. try compose recreate (covers compose-`configs:` deployments);
          3. if the container's StartedAt did not change, fall back to a plain
             `docker restart amf1` (covers bind-mount deployments — the process
             re-reads the file at startup);
          4. VERIFY by reading the config from INSIDE the container: tac: 2
             must be gone. Only then restore the host file.
        Returns (ok, error_message). On failure the host file is restored by
        the caller (we do it here too) and the experiment must abort.
        """
        amf_path = self._amf_config_path("amf1")
        started_before = container_started_ms("amf1")
        amf_path.write_text(swapped_text)

        compose_up_service("amf1", self.compose_dir, self.compose_file, self.env_file)
        time.sleep(3)
        started_after = container_started_ms("amf1")
        if started_after is not None and started_after == started_before:
            print("[CONFIG] compose recreate was a no-op (bind-mount deployment?) "
                  "— falling back to docker restart amf1")
            if not restart_container("amf1"):
                return False, "amf1 restart failed"
            time.sleep(3)

        in_container = self._amf_config_in_container("amf1")
        if in_container is None:
            return False, "cannot read amf1 config inside container"
        if re.search(r"tac:\s*2\b", in_container) or not re.search(r"tac:\s*1\b", in_container):
            return False, (
                "amf1 still sees tac: 2 in its config after restart — the container's "
                "config mount is not following the host file. Switch amf1 to a bind "
                "mount in the compose file:\n"
                "    volumes:\n"
                "      - ../../configs/network-slicing/amf1.yaml:/etc/open5gs/custom/amf1.yaml\n"
                "(comment out its `configs:` entry), then `docker compose up -d amf1` once."
            )
        print("[CONFIG] verified: running amf1 loaded tac1-only config")
        return True, ""

    def _n14_cleanup(self, ue_backups: Dict[str, str]):
        """Return the network to the homogeneous end state of exps 1-7:
        amf1 restarted on the RESTORED (tac1+tac2) config, gnb1 up, UE
        configs gnb1-only, UEs restarted, proxy2 back on amf1, gnb2 up."""
        print("[CLEANUP] Restoring amf1 (full TAI), gnb1, gnb2, UE configs, proxy2...")
        # Host file was already restored right after step 0; make the running
        # amf1 reload it (restart is correct for bind mounts and harmless —
        # though insufficient — for compose configs, which is why cleanup
        # tolerates either deployment: exps 1-7 only need amf1 UP).
        compose_up_service("amf1", self.compose_dir, self.compose_file, self.env_file)
        time.sleep(3)
        restart_container("amf1")
        time.sleep(4)
        # amf1 restart drops gnb1's association; restart gnb1 regardless of
        # whether the trigger stopped it (docker restart starts stopped containers).
        restart_container(LEGACY_GNB)
        time.sleep(3)
        wait_for_gnb_ng_setup(LEGACY_GNB, timeout=15.0)
        for ue, content in ue_backups.items():
            self._restore_config(ue, content)
        for ue in ue_backups:
            restart_container(ue)
        switch_proxy_backend("amf1", self.proxy2_host, self.proxy2_port)
        restart_container("gnb2")  # restore the start_network.sh end state
        time.sleep(8)

    def run_n14_mobility(self, target_amf: str, wait_before: float, wait_after: float) -> Dict:
        """Experiment 8: N14-enabled mobility re-allocation (no UE restart).

        v7.4.3 ordering (after the 2026-08-25 pilot):
        [0] amf1.yaml -> tac1-only; load it into the RUNNING amf1 (compose
            recreate if that actually recreates, else docker restart) with
            IN-CONTAINER verification; restore the host file; restart gnb1
        [1] PAUSE gnb2 (DNS stays alive — UERANSIM crashes on unresolvable
            gnbSearchList entries — but its radio goes silent), rewrite UE
            configs -> [gnb1, gnb2], restart UEs => baseline on gnb1/AMF1 with
            TAI list {tac1}; with gnb2 frozen the cell-selection race cannot
            fire (pilot 1: 6/6 UEs camped on gnb2/AMF2 BEFORE the trigger)
        [2] proxy2 -> amf2, start gnb2, verify NG Setup, let RLS beacon
        [3] TRIGGER: docker stop gnb1 (UEs keep their 5G-GUTI)
        [4] UEs reselect gnb2 (tac2 not in TAI list) => Mobility Registration
            with old GUTI => AMF2 pulls context from AMF1 via N14
        [5] metrics (session continuity / same IP expected) + N14 evidence
        [6] cleanup to the homogeneous end state
        """
        print(f"\n[Mode: N14-MOBILITY] Target: {target_amf} | UEs: {self.ue_pool}")
        if not self.run_baseline_check():
            return {"error": "proxy not running"}

        # ---- Step 0: amf1 tac1-only, verified -----------------------------
        print("[0/6] Swapping amf1.yaml to tac1-only and loading it into amf1...")
        amf_path = self._amf_config_path("amf1")
        if not amf_path.exists():
            return {"error": f"amf1 config not found: {amf_path} (check --config-dir)"}
        amf_backup = amf_path.read_text()
        try:
            swapped = self._strip_tac_from_amf_tai(amf_backup, tac=2)
            blk = self._tai_block_text(swapped)
            if not blk or re.search(r"tac:\s*2\b", blk) or not re.search(r"tac:\s*1\b", blk):
                raise RuntimeError("tac1-only rewrite failed verification")
        except RuntimeError as exc:
            return {"error": f"amf1 config swap failed: {exc}"}

        ue_backups: Dict[str, str] = {}
        try:
            ok, err = self._load_amf1_tac1_only(swapped)
            # Host file restored immediately in ALL cases (the running process
            # has already read whatever it was going to read).
            amf_path.write_text(amf_backup)
            if not ok:
                return {"error": err}
            print("[CONFIG] amf1.yaml restored on disk (running amf1 keeps tac1-only)")

            print("[0/6] Restarting gnb1 (association dropped by amf1 reload)...")
            self._restart_gnb_tracked(LEGACY_GNB)
            if not wait_for_gnb_ng_setup(LEGACY_GNB, timeout=15.0):
                print("[WARN] gnb1 NG Setup not confirmed; proceeding anyway...")

            # ---- Step 1: gnb2 PAUSED + UE configs + deterministic baseline -
            # gnb2 must be silent while UEs boot: with both gNBs beaconing,
            # cell selection is a timing race (pilot 1: 6/6 UEs camped on
            # gnb2/AMF2 BEFORE the trigger, making the trigger a no-op).
            # gnb1-first ordering alone does NOT prevent this. But `docker
            # stop` removes the DNS entry and UERANSIM CRASHES on unresolvable
            # gnbSearchList entries (pilot 2: 'Bad Inet address'). `docker
            # pause` is the middle ground: DNS stays alive, radio stays silent.
            print("[1/6] Pausing gnb2, rewriting UE configs ([gnb1, gnb2]), restarting UEs...")
            if not pause_container("gnb2"):
                print("[WARN] docker pause failed — falling back to both-gNBs-up "
                      "baseline (cell-selection race possible; stray-retry loop active)")
            try:
                for ue in self.ue_pool:
                    ue_backups[ue] = self._backup_config(ue)
                    self._set_gnb_preference(ue, gnb2_first=False, exclusive=False)
            except RuntimeError as exc:
                print(f"[ERROR] {exc}")
                return {"error": f"config rewrite failed: {exc}"}

            restart_times: Dict[str, Optional[float]] = {}
            t_base = time.time() * 1000  # BEFORE the restart loop (v7.4.3 fix:
            for ue in self.ue_pool:      # capturing it after filtered out the
                _, rt = self._restart_container_tracked(ue)  # early boots and
                restart_times[ue] = rt   # blinded the stray-cell detection)
            baseline = self.wait_for_all_recovery(t_base, timeout=wait_after, restart_times=restart_times)
            n_base = sum(1 for r in baseline if r.get("recovered"))
            print(f"[1/6] Baseline: {n_base}/{len(self.ue_pool)} UEs registered")
            if n_base == 0:
                print("[ERROR] No UE registered at baseline. If UE logs show DNS "
                      "errors for gnb2.ueransim.org, this UERANSIM build cannot "
                      "tolerate unresolvable gnbSearchList entries — report back "
                      "and the retry-strategy fallback will be re-enabled.")
                return {"error": "baseline registration failed (gnb2-down intolerance?)"}

            # Verify everyone camped on gnb1/tac1, anchored on each UE's
            # container StartedAt (NOT t_base — late anchors hid strays).
            for attempt in range(3):
                strays = [
                    ue for ue in self.ue_pool
                    if self._last_selected_tac(ue, container_started_ms(ue)) == 2
                ]
                if not strays:
                    break
                print(f"[WARN] {strays} camped on gnb2 at baseline; "
                      f"restarting them (attempt {attempt+1}/3)...")
                for ue in strays:
                    self._restart_container_tracked(ue)
                time.sleep(6)
            strays = [
                ue for ue in self.ue_pool
                if self._last_selected_tac(ue, container_started_ms(ue)) == 2
            ]
            if strays:
                print(f"[WARN] {strays} still on gnb2 at baseline; their mobility "
                      f"trigger is compromised (TAI list from AMF2 includes tac2)")

            # ---- Step 2: proxy2 -> amf2, bring up gnb2 -------------------
            print(f"[2/6] Switching proxy2 to {target_amf} and starting gnb2...")
            if not switch_proxy_backend(target_amf, self.proxy2_host, self.proxy2_port):
                return {"error": "proxy2 switch failed"}
            # docker restart both unpauses and reconnects gnb2 via proxy2->amf2
            self._restart_gnb_tracked("gnb2")
            if not wait_for_gnb_ng_setup("gnb2", timeout=20.0):
                print("[ERROR] gnb2 NG Setup not confirmed; aborting exp8")
                return {"error": "gnb2 NG Setup failed"}
            time.sleep(3)  # let gnb2's radio (RLS) beacon before the trigger
            if not self._wait_for_gnb2_radio(timeout=45.0):
                return {"error": "gnb2 radio (RLS) not visible to UEs"}

            time.sleep(wait_before)

            # ---- Step 3: TRIGGER — stop gnb1, UEs keep their GUTI --------
            print("[3/6] TRIGGER: stopping gnb1 (NO UE restart — GUTI preserved)...")
            t0 = stop_container_tracked(LEGACY_GNB) or (time.time() * 1000)
            print(f"[INFO] Trigger time (gnb1 stop): {datetime.fromtimestamp(t0/1000).isoformat()}")

            # ---- Step 4: mobility registrations via gnb2 -> amf2 ---------
            print("[4/6] Waiting for mobility registrations via gnb2/AMF2...")
            results = self.wait_for_all_mobility(t0, timeout=wait_after)

            # ---- Step 5: gnb2-selection stamp + N14 evidence -------------
            for r in results:
                if r.get("recovered"):
                    r["gnb2_selected"] = (self._last_selected_tac(r["ue"], t0) == 2)
                    if not r["gnb2_selected"]:
                        print(f"[WARN] {r['ue']}: no tac[2] selection after trigger")
            n14 = self._check_n14_evidence(trigger_ms=t0)

            agg = self.computer.compute_aggregate(results)
            agg["mobility_registration_count"] = sum(
                1 for r in results if r.get("mobility_registration")
            )
            agg["initial_fallback_count"] = sum(
                1 for r in results if r.get("fell_back_to_initial")
            )
            agg["session_continuity_count"] = sum(
                1 for r in results if r.get("session_continuity")
            )

            return {
                "mode": "n14-mobility",
                "target_amf": target_amf,
                "trigger": "gnb1-stop (TAI change -> mobility registration)",
                "switch_time_ms": t0,
                "n14_context_transfer": n14["n14_context_transfer"],
                "n14_matches": n14["n14_matches"],
                "ue_results": results,
                "aggregate": agg,
                "experiment_timestamp": datetime.now().isoformat(),
            }
        finally:
            # ---- Step 6: cleanup (runs even on error) --------------------
            self._n14_cleanup(ue_backups)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export(self, results, basename: str):
        """Export one or multiple experiment runs."""
        if isinstance(results, dict):
            results = [results]
        if not results:
            print("[Export] No results to export")
            return

        json_path = self.output_dir / f"{basename}.json"
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"[Export] JSON: {json_path}")

        csv_path = self.output_dir / f"{basename}.csv"
        flat_rows = []
        for run in results:
            agg = run.get("aggregate", {})
            for ue_res in run.get("ue_results", []):
                flat = {
                    "run_mode": run.get("mode", ""),
                    "target_amf": run.get("target_amf", ""),
                    "ue": ue_res.get("ue", ""),
                    "recovered": ue_res.get("recovered", False),
                    "realloc_lat_ms": ue_res.get("reallocation_latency_ms"),
                    "reg_lat_ms": ue_res.get("registration_latency_ms"),
                    "sess_lat_ms": ue_res.get("session_setup_latency_ms"),
                    "rrc_lat_ms": ue_res.get("rrc_latency_ms"),
                    "post_restart_lat_ms": ue_res.get("post_restart_latency_ms"),
                    "ip_changed": ue_res.get("ip_changed", False),
                    "old_ip": ue_res.get("old_ip", ""),
                    "new_ip": ue_res.get("new_ip", ""),
                    "gnb2_selected": ue_res.get("gnb2_selected"),
                    "mobility_reg": ue_res.get("mobility_registration"),
                    "initial_fallback": ue_res.get("fell_back_to_initial"),
                    "session_continuity": ue_res.get("session_continuity"),
                    "n14_context_transfer": run.get("n14_context_transfer"),
                    "agg_realloc_mean": agg.get("reallocation_latency_ms", {}).get("mean"),
                    "agg_realloc_std": agg.get("reallocation_latency_ms", {}).get("std"),
                    "agg_realloc_p95": agg.get("reallocation_latency_ms", {}).get("p95"),
                    "agg_recovered": agg.get("recovered_count", 0),
                    "timestamp": run.get("experiment_timestamp", ""),
                }
                flat_rows.append(flat)

        if flat_rows:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=flat_rows[0].keys())
                writer.writeheader()
                writer.writerows(flat_rows)
            print(f"[Export] CSV: {csv_path}")

        latex_path = self.output_dir / f"{basename}.tex"
        NL = "\n"
        BS = "\\"
        with open(latex_path, "w") as f:
            f.write(f"{BS}begin{{table}}[ht]{NL}")
            f.write(f"{BS}centering{NL}")
            f.write(f"{BS}begin{{tabular}}{{|c|c|c|c|c|c|c|c|c|}}{NL}")
            f.write(f"{BS}hline{NL}")
            f.write(
                f"Run & Mode & UE & Realloc (ms) & Reg (ms) & Sess (ms) & "
                f"RRC (ms) & IP Chg & Recov {BS}{BS}{NL}"
            )
            f.write(f"{BS}hline{NL}")
            for run_idx, run in enumerate(results):
                mode = run.get("mode", "")
                for ue_res in run.get("ue_results", []):
                    def fmt(v):
                        return f"{v:.1f}" if isinstance(v, (int, float)) and v is not None else str(v)
                    row = (
                        f"{run_idx+1} & {mode} & {ue_res.get('ue','')} & "
                        f"{fmt(ue_res.get('reallocation_latency_ms'))} & "
                        f"{fmt(ue_res.get('registration_latency_ms'))} & "
                        f"{fmt(ue_res.get('session_setup_latency_ms'))} & "
                        f"{fmt(ue_res.get('rrc_latency_ms'))} & "
                        f"{'Y' if ue_res.get('ip_changed') else 'N'} & "
                        f"{'Y' if ue_res.get('recovered') else 'N'} {BS}{BS}{NL}"
                    )
                    f.write(row)
                agg = run.get("aggregate", {})
                for key in ("reallocation_latency_ms",):
                    stats = agg.get(key, {})
                    if stats.get("mean") is not None:
                        mean_val = fmt(stats.get('mean'))
                        std_val = fmt(stats.get('std'))
                        f.write(
                            f"{BS}multicolumn{{2}}{{|c|}}{{{BS}textbf{{Aggregate}}}} & "
                            f"n={stats.get('count',0)} & "
                            + mean_val + " $" + BS + "pm$ " + std_val + " & "
                            + "-- & -- & -- & -- & "
                            + f"{agg.get('recovered_count',0)}/{len(run.get('ue_results',[]))} "
                            + f"{BS}{BS}{NL}"
                        )
                f.write(f"{BS}hline{NL}")
            f.write(f"{BS}end{{tabular}}{NL}")
            f.write(f"{BS}caption{{AMF Re-allocation Experiment Results}}{NL}")
            f.write(f"{BS}label{{tab:amf_realloc}}{NL}")
            f.write(f"{BS}end{{table}}{NL}")
        print(f"[Export] LaTeX: {latex_path}")

        summary_path = self.output_dir / f"{basename}_summary.txt"
        with open(summary_path, "w") as f:
            f.write("AMF Re-allocation Experiment Summary\n")
            f.write("=" * 70 + "\n\n")
            for run_idx, run in enumerate(results):
                f.write(f"Run {run_idx+1}: mode={run.get('mode')}, target={run.get('target_amf')}\n")
                agg = run.get("aggregate", {})
                for key, stats in agg.items():
                    if isinstance(stats, dict) and stats.get("mean") is not None:
                        f.write(
                            f"  {key}: mean={stats['mean']:.1f}ms, std={stats['std']:.1f}ms, "
                            f"min={stats['min']:.1f}ms, max={stats['max']:.1f}ms, "
                            f"p95={stats['p95']:.1f}ms, cv={stats.get('cv',0):.3f} (n={stats['count']})\n"
                        )
                f.write(f"  Recovered: {agg.get('recovered_count',0)}/{len(run.get('ue_results',[]))}\n")
                f.write(f"  IP changed: {agg.get('ip_changed_count',0)}\n")
                if "mobility_registration_count" in agg:
                    f.write(
                        f"  Mobility registrations (N14 path): {agg['mobility_registration_count']}\n"
                        f"  Initial-registration fallbacks: {agg.get('initial_fallback_count',0)}\n"
                        f"  Session continuity (no PDU re-establishment): "
                        f"{agg.get('session_continuity_count',0)}\n"
                        f"  N14 UEContextTransfer evidence: "
                        f"{run.get('n14_context_transfer')}\n"
                    )
                f.write("\n")
                for ue_res in run.get("ue_results", []):
                    f.write(
                        f"    {ue_res['ue']}: realloc={ue_res.get('reallocation_latency_ms')}ms, "
                        f"reg={ue_res.get('registration_latency_ms')}ms, "
                        f"sess={ue_res.get('session_setup_latency_ms')}ms, "
                        f"rrc={ue_res.get('rrc_latency_ms')}ms, "
                        f"post_restart={ue_res.get('post_restart_latency_ms')}ms, "
                        f"recovered={ue_res.get('recovered')}, ip_changed={ue_res.get('ip_changed')}\n"
                    )
                f.write("\n")
        print(f"[Export] Summary: {summary_path}")


# -- CLI -----------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="AMF Re-allocation Orchestrator v7 (Dual Proxy + Split-gNB Fixes + N14 Mobility)")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST)
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    parser.add_argument("--proxy2-host", default=DEFAULT_PROXY2_HOST)
    parser.add_argument("--proxy2-port", type=int, default=DEFAULT_PROXY2_PORT)
    parser.add_argument(
        "--command",
        choices=[
            "status", "switch", "sequential", "concurrent",
            "backforth", "partial", "isolate-gnb",
            "split-gnb-simultaneous", "split-gnb-sequential",
            "n14-mobility",
        ],
        required=True,
    )
    parser.add_argument("--target", default="amf2")
    parser.add_argument("--wait-before", type=float, default=10.0)
    parser.add_argument("--wait-after", type=float, default=60.0)
    parser.add_argument("--cooldown", type=float, default=30.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--stagger", type=float, default=0.0)
    parser.add_argument("--ue-pool", default=",".join(DEFAULT_UE_POOL))
    parser.add_argument(
        "--ues", type=int, default=0,
        help="Convenience: use the first N UEs (ue1..ueN). Overrides --ue-pool. "
             "E.g. --ues 12 for the scaling tiers 6/12/24/48.",
    )
    parser.add_argument("--gnb-pool", default=",".join(DEFAULT_GNB_POOL))
    parser.add_argument("--ue-subset", default="")
    parser.add_argument(
        "--compose-dir", default=".",
        help="Docker Compose project dir (needed by n14-mobility to recreate amf1 "
             "after config edits; the stack uses compose `configs:`)",
    )
    parser.add_argument(
        "--compose-file", default="",
        help="Explicit compose file path (absolute, or relative to --compose-dir). "
             "Use when the compose file is not named compose.yaml/docker-compose.yml "
             "or not in --compose-dir. Check `grep 'docker compose' start_network.sh`.",
    )
    parser.add_argument(
        "--env-file", default="",
        help="Explicit .env file for compose variable interpolation "
             "(OPEN5GS_VERSION etc.). If omitted, .env is searched upward from "
             "the compose working dir and the compose file's directory.",
    )
    parser.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--basename", default="amf_realloc")
    args = parser.parse_args()

    print("[Orchestrator] version 7.5.1 (partial: collateral-disturbance classification)")

    ue_pool = [u.strip() for u in args.ue_pool.split(",") if u.strip()]
    if args.ues > 0:
        if args.ues > len(DEFAULT_UE_POOL):
            parser.error(f"--ues {args.ues} exceeds available pool ({len(DEFAULT_UE_POOL)})")
        ue_pool = DEFAULT_UE_POOL[: args.ues]
    gnb_pool = [g.strip() for g in args.gnb_pool.split(",") if g.strip()]

    runner = ExperimentRunner(
        args.proxy_host,
        args.proxy_port,
        args.proxy2_host,
        args.proxy2_port,
        args.output_dir,
        ue_pool=ue_pool,
        gnb_pool=gnb_pool,
        config_dir=args.config_dir,
        compose_dir=args.compose_dir,
        compose_file=args.compose_file,
        env_file=args.env_file,
    )

    if args.command == "status":
        print(get_proxy_status(args.proxy_host, args.proxy_port))

    elif args.command == "switch":
        ok = switch_both_backends(
            args.target,
            args.proxy_host, args.proxy_port,
            args.proxy2_host, args.proxy2_port,
        )
        sys.exit(0 if ok else 1)

    elif args.command == "sequential":
        res = runner.run_sequential(args.target, args.wait_before, args.wait_after, args.stagger)
        runner.export([res], args.basename)

    elif args.command == "concurrent":
        res = runner.run_concurrent(args.target, args.wait_before, args.wait_after)
        runner.export([res], args.basename)

    elif args.command == "backforth":
        res = runner.run_backforth(args.repeat, args.wait_before, args.wait_after, args.cooldown)
        runner.export(res, args.basename)

    elif args.command == "partial":
        subset = [u.strip() for u in args.ue_subset.split(",") if u.strip()]
        if not subset:
            print("[ERROR] --ue-subset required for partial mode")
            sys.exit(1)
        res = runner.run_partial(args.target, args.wait_before, args.wait_after, subset)
        runner.export([res], args.basename)

    elif args.command == "isolate-gnb":
        res = runner.run_isolate_gnb(args.target, args.wait_before, args.wait_after)
        runner.export([res], args.basename)

    elif args.command == "split-gnb-simultaneous":
        subset = [u.strip() for u in args.ue_subset.split(",") if u.strip()]
        if not subset:
            print("[ERROR] --ue-subset required for split-gnb-simultaneous mode")
            sys.exit(1)
        res = runner.run_split_gnb_simultaneous(
            args.target, args.wait_before, args.wait_after, subset
        )
        runner.export([res], args.basename)

    elif args.command == "split-gnb-sequential":
        subset = [u.strip() for u in args.ue_subset.split(",") if u.strip()]
        if not subset:
            print("[ERROR] --ue-subset required for split-gnb-sequential mode")
            sys.exit(1)
        res = runner.run_split_gnb_sequential(
            args.target, args.wait_before, args.wait_after, subset
        )
        runner.export([res], args.basename)

    elif args.command == "n14-mobility":
        res = runner.run_n14_mobility(args.target, args.wait_before, args.wait_after)
        runner.export([res], args.basename)


if __name__ == "__main__":
    main()

