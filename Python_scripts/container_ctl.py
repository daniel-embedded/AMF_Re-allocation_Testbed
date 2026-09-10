#!/usr/bin/env python3
"""
container_ctl.py
A safer, more flexible way to manage your Open5GS Docker containers.
Replaces os.system() with subprocess.run() for better error handling.
"""

import subprocess
import sys
from pathlib import Path

# Adjust these paths to match your setup
COMPOSE_FILE = Path("compose-files/network-slicing/docker-compose.yaml")
ENV_FILE = Path(".env")


def run_command(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command safely and optionally capture output."""
    print(f"[CMD] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False  # We handle errors manually
    )
    if result.returncode != 0:
        print(f"[ERROR] Exit code {result.returncode}")
        if result.stderr:
            print(f"[STDERR] {result.stderr.strip()}")
    elif capture and result.stdout:
        print(result.stdout.strip())
    return result


def compose_up():
    """Start the full Open5GS stack in detached mode."""
    cmd = [
        "sudo", "docker", "compose",
        "-f", str(COMPOSE_FILE),
        "--env-file", str(ENV_FILE),
        "up", "-d"
    ]
    run_command(cmd)


def compose_down():
    """Stop the full Open5GS stack and remove orphans."""
    cmd = [
        "sudo", "docker", "compose",
        "-f", str(COMPOSE_FILE),
        "--env-file", str(ENV_FILE),
        "down", "--remove-orphans"
    ]
    run_command(cmd)


def restart_container(name: str):
    """Restart a single container (e.g., 'amf1', 'ue1')."""
    run_command(["sudo", "docker", "restart", name])


def container_status():
    """Show status of all Open5GS-related containers."""
    result = run_command(
        ["sudo", "docker", "ps", "-a", "--filter", "name=amf|smf|upf|gnb|ue|nrf|db", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        capture=True
    )
    return result.stdout


def logs_tail(container: str, lines: int = 30):
    """Tail logs from a specific container."""
    run_command(["sudo", "docker", "logs", "--tail", str(lines), container])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Open5GS Container Controller")
    parser.add_argument("action", choices=["up", "down", "restart", "status", "logs"])
    parser.add_argument("--target", help="Container name for restart/logs", default="amf1")
    parser.add_argument("--lines", type=int, default=30, help="Number of log lines")
    args = parser.parse_args()

    if args.action == "up":
        compose_up()
    elif args.action == "down":
        compose_down()
    elif args.action == "restart":
        restart_container(args.target)
    elif args.action == "status":
        container_status()
    elif args.action == "logs":
        logs_tail(args.target, args.lines)
