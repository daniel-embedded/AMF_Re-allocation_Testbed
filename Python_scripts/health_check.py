#!/usr/bin/env python3
"""
health_check.py
Inspect the Open5GS stack: which containers are running, their IPs,
and whether key ports (SCTP 38412, MongoDB 27017) are reachable.
"""

import subprocess
import json
import socket


CONTAINERS = [
    "amf1", "amf2", "smf1", "smf2", "upf1", "upf2",
    "gnb1", "gnb2", "ue1", "ue2", "nrf", "db", "pcf", "udm", "ausf", "udr", "nssf", "bsf"
]


def inspect_container(name: str) -> dict:
    """Get JSON metadata about a container."""
    result = subprocess.run(
        ["sudo", "docker", "inspect", "--format", "{{json .}}", name],
        capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    return {}


def get_container_ip(name: str, network: str = "open5gs") -> str:
    """Extract the IP address of a container on a specific Docker network."""
    data = inspect_container(name)
    networks = data.get("NetworkSettings", {}).get("Networks", {})
    net_info = networks.get(network, {})
    return net_info.get("IPAddress", "N/A")


def check_tcp_port(host: str, port: int, timeout: int = 2) -> bool:
    """Try to connect to a TCP port. Returns True if open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_stack_health():
    """Print a formatted health table of the Open5GS stack."""
    print(f"{'Container':<12} {'Status':<20} {'IP (open5gs)':<18} {'Port Check'}")
    print("-" * 70)

    for name in CONTAINERS:
        data = inspect_container(name)
        if not data:
            print(f"{name:<12} {'NOT FOUND':<20} {'N/A':<18} {'-'}")
            continue

        state = data.get("State", {})
        status = state.get("Status", "unknown")
        health = state.get("Health", {}).get("Status", "")
        display_status = f"{status} ({health})" if health else status

        ip = get_container_ip(name)

        # Port check logic
        port_check = "-"
        if name in ("amf1", "amf2"):
            # AMF listens on SCTP 38412; TCP check won't work for SCTP,
            # but we can check if the container is at least running.
            port_check = "SCTP (check manually)"
        elif name == "db":
            reachable = check_tcp_port(ip, 27017) if ip != "N/A" else False
            port_check = f"TCP 27017: {'OK' if reachable else 'FAIL'}"
        elif name == "nrf":
            reachable = check_tcp_port(ip, 80) if ip != "N/A" else False
            port_check = f"HTTP 80: {'OK' if reachable else 'FAIL'}"

        print(f"{name:<12} {display_status:<20} {ip:<18} {port_check}")


def show_network_topology():
    """Display the Docker network bridge info."""
    result = subprocess.run(
        ["sudo", "docker", "network", "inspect", "open5gs"],
        capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        net = json.loads(result.stdout)[0]
        print("\n=== Docker Network: open5gs ===")
        print(f"Subnet: {net.get('IPAM', {}).get('Config', [{}])[0].get('Subnet', 'N/A')}")
        print(f"Gateway: {net.get('IPAM', {}).get('Config', [{}])[0].get('Gateway', 'N/A')}")
        print("\nConnected containers:")
        for name, info in net.get("Containers", {}).items():
            print(f"  - {info.get('Name', 'unknown')}: {info.get('IPv4Address', 'N/A')}")
    else:
        print("[ERROR] Could not inspect network 'open5gs'")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Open5GS Health Checker")
    parser.add_argument("--topology", action="store_true", help="Show network topology")
    args = parser.parse_args()

    check_stack_health()
    if args.topology:
        show_network_topology()
