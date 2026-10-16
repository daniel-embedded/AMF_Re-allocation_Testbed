#!/usr/bin/env python3
"""
log_parser.py
Parse Docker logs from AMF and UE containers to extract 5G events.
This is a foundational skill for your orchestrator: detecting registration,
PDU session establishment, and CM state changes.
"""

import subprocess
import re
from datetime import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass
class LogEvent:
    timestamp: str
    container: str
    module: str      # e.g., nas, rrc, app
    level: str       # info, debug, error
    message: str


# Regex to parse UERANSIM log lines:
# [2026-06-22 13:25:09.447] [nas] [info] UE switches to state [MM-DEREGISTERED/PLMN-SEARCH]
UERANSIM_LOG_PATTERN = re.compile(
    r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\]\s+\[(\w+)\]\s+\[(\w+)\]\s+(.*)"
)

# Regex to parse Open5GS AMF logs (simplified)
# Example: 06/22 13:25:09.447: [amf] INFO: [Added] Number of AMF-UEs is now 1
OPEN5GS_LOG_PATTERN = re.compile(
    r"(\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}):\s+\[(\w+)\]\s+(\w+):\s+(.*)"
)


def fetch_logs(container: str, lines: int = 50) -> str:
    """Fetch the last N lines from a container."""
    result = subprocess.run(
        ["sudo", "docker", "logs", "--tail", str(lines), container],
        capture_output=True, text=True, check=False
    )
    return result.stdout


def parse_ue_logs(container: str, lines: int = 50) -> list[LogEvent]:
    """Parse UERANSIM UE logs into structured events."""
    raw = fetch_logs(container, lines)
    events = []
    for line in raw.strip().split("\n"):
        match = UERANSIM_LOG_PATTERN.match(line)
        if match:
            ts, module, level, msg = match.groups()
            events.append(LogEvent(ts, container, module, level, msg))
    return events


def parse_amf_logs(container: str, lines: int = 50) -> list[LogEvent]:
    """Parse Open5GS AMF logs into structured events."""
    raw = fetch_logs(container, lines)
    events = []
    for line in raw.strip().split("\n"):
        match = OPEN5GS_LOG_PATTERN.match(line)
        if match:
            ts, module, level, msg = match.groups()
            # Normalize timestamp to ISO-like format
            ts = ts.replace("/", "-").split()[0] + " " + ts.split()[1]
            events.append(LogEvent(ts, container, module, level.upper(), msg))
    return events


def find_registration_events(events: list[LogEvent]) -> list[LogEvent]:
    """Filter events related to registration."""
    keywords = ["Registration", "MM-REGISTERED", "MM-DEREGISTERED", "Authentication"]
    return [e for e in events if any(k in e.message for k in keywords)]


def find_pdu_session_events(events: list[LogEvent]) -> list[LogEvent]:
    """Filter events related to PDU session establishment."""
    keywords = ["PDU Session", "PSI", "uesimtun", "Establishment"]
    return [e for e in events if any(k in e.message for k in keywords)]


def summarize_ue_state(container: str = "ue1"):
    """Print a human-readable summary of the UE's last known states."""
    events = parse_ue_logs(container, lines=100)
    print(f"\n=== {container} State Summary ===")
    for ev in events:
        if "switches to state" in ev.message or "Registration" in ev.message or "PDU Session" in ev.message:
            print(f"[{ev.timestamp}] [{ev.module}] {ev.message}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Open5GS Log Parser")
    parser.add_argument("--container", default="ue1", help="Container to parse")
    parser.add_argument("--lines", type=int, default=50, help="Lines to fetch")
    parser.add_argument("--filter", choices=["registration", "pdu", "all"], default="all")
    args = parser.parse_args()

    if args.container.startswith("ue"):
        events = parse_ue_logs(args.container, args.lines)
    else:
        events = parse_amf_logs(args.container, args.lines)

    if args.filter == "registration":
        events = find_registration_events(events)
    elif args.filter == "pdu":
        events = find_pdu_session_events(events)

    print(f"\n=== Parsed {len(events)} events from {args.container} ===")
    for ev in events[-20:]:  # Show last 20
        print(f"[{ev.timestamp}] [{ev.module}] {ev.level}: {ev.message}")
