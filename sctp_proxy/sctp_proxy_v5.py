#!/usr/bin/env python3
"""
sctp_proxy_v5.py
Full-featured SCTP proxy for Open5GS N2 (dual-instance capable).

Changes from v5:
  - LISTEN_PORT and CONTROL_PORT are configurable via environment variables.
  - This allows running two proxy instances (e.g. 38412/9999 and 38413/9998)
    from the same build context.

Env vars:
  PROXY_LISTEN_PORT   -- SCTP listen port (default 38412)
  PROXY_CONTROL_PORT  -- UDP control port (default 9999)
  PROXY_METRICS_PORT  -- HTTP metrics port (default 9100)
  DISABLE_AUTO_RESTART -- Set to "1" to skip auto-restart on SWITCH
"""

import socket
import threading
import select
import time
import sys
import os
from collections import defaultdict

try:
    import sctp
except ImportError:
    print("[ERROR] pysctp not installed.")
    print("Run: apt-get update && apt-get install -y libsctp-dev gcc && pip install pysctp docker")
    sys.exit(1)

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    print("[WARNING] docker SDK not installed. Auto-restart disabled.")
    print("Install with: pip install docker")

# -- Configuration ---------------------------------------------------
LISTEN_ADDR = "0.0.0.0"
LISTEN_PORT = int(os.environ.get("PROXY_LISTEN_PORT", "38412"))
CONTROL_PORT = int(os.environ.get("PROXY_CONTROL_PORT", "9999"))
METRICS_PORT = int(os.environ.get("PROXY_METRICS_PORT", "9100"))
DISABLE_AUTO_RESTART = os.environ.get("DISABLE_AUTO_RESTART", "0") == "1"

BACKENDS = {
    "amf1": ("amf1.open5gs.org", 38412),
    "amf2": ("amf2.open5gs.org", 38413),
}

BACKEND_TO_GNB_UE = {
    "amf1": {"gnb": "gnb1", "ue": "ue1"},
    "amf2": {"gnb": "gnb1", "ue": "ue1"},
}

# Global state
_current_backend = "amf1"
_active_conns = []
_state_lock = threading.Lock()

_metrics = {
    "connections_total": 0,
    "active_associations": 0,
    "switches_total": 0,
    "bytes_gnb_to_amf": 0,
    "bytes_amf_to_gnb": 0,
    "ngap_messages": defaultdict(int),
    "assoc_durations": [],
}

docker_client = docker.from_env() if DOCKER_AVAILABLE else None


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def safe_close(sock):
    try:
        sock.close()
    except Exception:
        pass


class Connection:
    def __init__(self, frontend, backend, client_addr):
        self.frontend = frontend
        self.backend = backend
        self.client_addr = client_addr
        self.stop_event = threading.Event()
        self.closed = False
        self.start_time = time.time()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        safe_close(self.frontend)
        safe_close(self.backend)
        duration = time.time() - self.start_time
        with _state_lock:
            _metrics["assoc_durations"].append(duration)
            if len(_metrics["assoc_durations"]) > 1000:
                _metrics["assoc_durations"] = _metrics["assoc_durations"][-1000:]


def _ngap_heuristic(data: bytes) -> str:
    if len(data) < 8:
        return "keepalive"
    first = data[0]
    sz = len(data)
    if sz > 400 and (first & 0xC0) == 0x00:
        return "ng_setup"
    if 80 < sz < 260 and (first & 0xC0) == 0x00:
        return "initial_ue"
    if 50 < sz < 150 and (first & 0xC0) == 0x00:
        return "uplink_nas"
    if 50 < sz < 150 and (first & 0xC0) == 0x40:
        return "downlink_nas"
    if 150 < sz < 450 and (first & 0xC0) == 0x40:
        return "initial_context_setup"
    if sz > 250 and (first & 0xC0) == 0x40:
        return "pdu_session_setup"
    if sz < 80:
        return "ue_context_release"
    return "unknown"


def forward(source, dest, label, conn, direction):
    try:
        while not conn.stop_event.is_set():
            ready, _, _ = select.select([source], [], [], 0.5)
            if not ready:
                continue
            data = source.recv(65535)
            if not data:
                log(f"{label}: source closed connection")
                break
            if conn.stop_event.is_set():
                break
            with _state_lock:
                if direction == "gNB_to_AMF":
                    _metrics["bytes_gnb_to_amf"] += len(data)
                else:
                    _metrics["bytes_amf_to_gnb"] += len(data)
                htype = _ngap_heuristic(data)
                _metrics["ngap_messages"][(direction, htype)] += 1
            dest.send(data)
    except (OSError, ConnectionResetError, BrokenPipeError) as e:
        log(f"{label}: socket error: {e}")
    except ValueError as e:
        if not conn.stop_event.is_set():
            log(f"{label}: unexpected ValueError: {e}")
    except Exception as e:
        log(f"{label}: unexpected error: {e}")
    finally:
        conn.shutdown()
        with _state_lock:
            if conn in _active_conns:
                _active_conns.remove(conn)
                _metrics["active_associations"] = len(_active_conns)


def handle_gnb(frontend_sock, client_addr):
    global _current_backend
    backend_addr = BACKENDS[_current_backend]
    log(f"New gNB assoc from {client_addr} -> backend {_current_backend} {backend_addr}")
    backend_sock = sctp.sctpsocket(socket.AF_INET, sctp.SOCK_STREAM, 0)
    try:
        backend_sock.connect(backend_addr)
        log(f"Connected to {_current_backend} at {backend_addr}")
    except Exception as e:
        log(f"FAILED to connect to {_current_backend} {backend_addr}: {e}")
        safe_close(frontend_sock)
        return
    conn = Connection(frontend_sock, backend_sock, client_addr)
    with _state_lock:
        _active_conns.append(conn)
        _metrics["connections_total"] += 1
        _metrics["active_associations"] = len(_active_conns)
    t_up = threading.Thread(
        target=forward,
        args=(frontend_sock, backend_sock, f"gNB->AMF {client_addr}", conn, "gNB_to_AMF"),
        daemon=True
    )
    t_down = threading.Thread(
        target=forward,
        args=(backend_sock, frontend_sock, f"AMF->gNB {client_addr}", conn, "AMF_to_gNB"),
        daemon=True
    )
    t_up.start()
    t_down.start()


def restart_container(container_name: str, timeout: int = 10) -> bool:
    if not docker_client:
        log(f"[AUTO-RESTART] Docker SDK not available, cannot restart {container_name}")
        return False
    try:
        log(f"[AUTO-RESTART] Restarting container: {container_name}...")
        container = docker_client.containers.get(container_name)
        container.restart(timeout=timeout)
        log(f"[AUTO-RESTART] {container_name} restarted successfully")
        return True
    except docker.errors.NotFound:
        log(f"[AUTO-RESTART] Container {container_name} not found")
        return False
    except Exception as e:
        log(f"[AUTO-RESTART] Failed to restart {container_name}: {e}")
        return False


def switch_backend(new_backend):
    global _current_backend
    if new_backend not in BACKENDS:
        log(f"Unknown backend '{new_backend}'. Available: {list(BACKENDS.keys())}")
        return False
    old_backend = _current_backend
    _current_backend = new_backend
    _metrics["switches_total"] += 1
    log(f"*** BACKEND SWITCH: {old_backend} -> {new_backend} {BACKENDS[new_backend]} ***")
    with _state_lock:
        conns = _active_conns[:]
    for conn in conns:
        log(f"Tearing down assoc for {conn.client_addr}")
        conn.shutdown()
    log(f"Closed {len(conns)} active association(s).")

    if DISABLE_AUTO_RESTART:
        log("[AUTO-RESTART] Skipped (DISABLE_AUTO_RESTART=1)")
        log(f"*** Switch complete. New connections go to {new_backend}. ***")
        return True

    targets = BACKEND_TO_GNB_UE.get(new_backend, {})
    gnb_name = targets.get("gnb")
    ue_name = targets.get("ue")
    if gnb_name:
        restart_container(gnb_name, timeout=5)
        time.sleep(2)
    if ue_name:
        restart_container(ue_name, timeout=5)
    log(f"*** Re-allocation complete. New connections go to {new_backend}. ***")
    return True


def get_prometheus_text() -> str:
    lines = []
    with _state_lock:
        lines.append("# HELP sctp_proxy_connections_total Total accepted SCTP associations")
        lines.append("# TYPE sctp_proxy_connections_total counter")
        lines.append(f"sctp_proxy_connections_total {_metrics['connections_total']}")
        lines.append("# HELP sctp_proxy_active_associations Current active associations")
        lines.append("# TYPE sctp_proxy_active_associations gauge")
        lines.append(f"sctp_proxy_active_associations {_metrics['active_associations']}")
        lines.append("# HELP sctp_proxy_switches_total Total backend switches")
        lines.append("# TYPE sctp_proxy_switches_total counter")
        lines.append(f"sctp_proxy_switches_total {_metrics['switches_total']}")
        lines.append("# HELP sctp_proxy_bytes_transferred_total Bytes transferred")
        lines.append("# TYPE sctp_proxy_bytes_transferred_total counter")
        lines.append(f'sctp_proxy_bytes_transferred_total{{direction="gNB_to_AMF"}} {_metrics["bytes_gnb_to_amf"]}')
        lines.append(f'sctp_proxy_bytes_transferred_total{{direction="AMF_to_gNB"}} {_metrics["bytes_amf_to_gnb"]}')
        lines.append("# HELP sctp_proxy_ngap_messages_total NGAP messages by direction and heuristic type")
        lines.append("# TYPE sctp_proxy_ngap_messages_total counter")
        for (direction, msg_type), count in _metrics["ngap_messages"].items():
            lines.append(f'sctp_proxy_ngap_messages_total{{direction="{direction}",type="{msg_type}"}} {count}')
        lines.append("# HELP sctp_proxy_backend_current Current backend (1=active)")
        lines.append("# TYPE sctp_proxy_backend_current gauge")
        for name in BACKENDS:
            val = 1 if name == _current_backend else 0
            lines.append(f'sctp_proxy_backend_current{{backend="{name}"}} {val}')
        if _metrics["assoc_durations"]:
            durations = _metrics["assoc_durations"]
            lines.append("# HELP sctp_proxy_association_duration_seconds Association lifetime")
            lines.append("# TYPE sctp_proxy_association_duration_seconds summary")
            lines.append(f"sctp_proxy_association_duration_seconds_sum {sum(durations):.3f}")
            lines.append(f"sctp_proxy_association_duration_seconds_count {len(durations)}")
    return "\n".join(lines) + "\n"


def control_interface():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", CONTROL_PORT))
    log(f"Control interface ready on UDP port {CONTROL_PORT}")
    log("Commands: SWITCH amf1 | SWITCH amf2 | STATUS | METRICS | RESTART gnb1|ue1")
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            cmd = data.decode().strip().upper()
            log(f"Control from {addr}: {cmd}")
            if cmd.startswith("SWITCH "):
                parts = cmd.split()
                if len(parts) == 2:
                    success = switch_backend(parts[1].lower())
                    if success:
                        status_msg = f"OK: switched to {_current_backend}"
                        if DISABLE_AUTO_RESTART:
                            status_msg += " (auto-restart disabled)"
                        sock.sendto((status_msg + "\n").encode(), addr)
                    else:
                        sock.sendto(b"ERROR: switch failed\n", addr)
                else:
                    sock.sendto(b"ERROR: usage: SWITCH amf1|amf2\n", addr)
            elif cmd == "STATUS":
                with _state_lock:
                    status = (
                        f"Backend: {_current_backend}\n"
                        f"Active associations: {len(_active_conns)}\n"
                        f"Connections total: {_metrics['connections_total']}\n"
                        f"Switches total: {_metrics['switches_total']}\n"
                        f"Auto-restart: {'disabled' if DISABLE_AUTO_RESTART else 'enabled'}\n"
                        f"Docker SDK: {'yes' if DOCKER_AVAILABLE else 'no'}\n"
                        f"Targets: {BACKENDS}\n"
                    )
                sock.sendto(status.encode(), addr)
            elif cmd == "METRICS":
                body = get_prometheus_text()
                if len(body) > 60000:
                    body = body[:60000] + "\n# ...truncated\n"
                sock.sendto(body.encode(), addr)
            elif cmd.startswith("RESTART "):
                parts = cmd.split()
                if len(parts) == 2:
                    name = parts[1].lower()
                    success = restart_container(name)
                    sock.sendto(f"{'OK' if success else 'FAIL'}: restart {name}\n".encode(), addr)
                else:
                    sock.sendto(b"ERROR: usage: RESTART <container_name>\n", addr)
            else:
                sock.sendto(b"ERROR: Unknown command. Use SWITCH, STATUS, METRICS, or RESTART\n", addr)
        except Exception as e:
            log(f"Control interface error: {e}")


class MetricsHandler:
    def __init__(self, request, client_address, server):
        self.request = request
        self.client_address = client_address
        self.server = server
        self.handle()

    def handle(self):
        try:
            req = self.request.recv(4096).decode()
            path = req.split()[1] if len(req.split()) > 1 else "/"
            if path == "/metrics":
                body = get_prometheus_text()
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/plain; version=0.0.4\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "\r\n"
                    + body
                )
            elif path == "/":
                html = "<h1>SCTP Proxy v5 Metrics</h1><a href='/metrics'>/metrics</a>"
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html\r\n"
                    f"Content-Length: {len(html)}\r\n"
                    "\r\n" + html
                )
            else:
                response = "HTTP/1.1 404 Not Found\r\n\r\n"
            self.request.sendall(response.encode())
        except Exception as e:
            log(f"Metrics handler error: {e}")
        finally:
            self.request.close()


def metrics_server():
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", METRICS_PORT))
        srv.listen(5)
        log(f"Metrics server ready on http://0.0.0.0:{METRICS_PORT}/metrics")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=MetricsHandler, args=(conn, addr, None), daemon=True).start()
    except Exception as e:
        log(f"Metrics server error: {e}")


def main():
    log("=" * 60)
    log("Open5GS SCTP Proxy v5 (Auto-Restart + Metrics)")
    log("=" * 60)
    log(f"[CONFIG] Listen port: {LISTEN_PORT}, Control port: {CONTROL_PORT}, Metrics port: {METRICS_PORT}")
    if DISABLE_AUTO_RESTART:
        log("[CONFIG] Auto-restart is DISABLED (DISABLE_AUTO_RESTART=1)")
    server = sctp.sctpsocket(socket.AF_INET, sctp.SOCK_STREAM, 0)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_ADDR, LISTEN_PORT))
    server.listen(5)
    log(f"Listening on SCTP {LISTEN_ADDR}:{LISTEN_PORT}")
    log(f"Default backend: {_current_backend} {BACKENDS[_current_backend]}")
    log(f"Prometheus: http://0.0.0.0:{METRICS_PORT}/metrics")
    log("Press Ctrl+C to stop.\n")
    threading.Thread(target=control_interface, daemon=True).start()
    threading.Thread(target=metrics_server, daemon=True).start()
    try:
        while True:
            client_sock, client_addr = server.accept()
            log(f"Accepted SCTP association from {client_addr}")
            threading.Thread(
                target=handle_gnb,
                args=(client_sock, client_addr),
                daemon=True
            ).start()
    except KeyboardInterrupt:
        log("\nShutdown requested. Closing server...")
        with _state_lock:
            for conn in _active_conns:
                conn.shutdown()
        safe_close(server)
        log("Goodbye.")


if __name__ == "__main__":
    main()
