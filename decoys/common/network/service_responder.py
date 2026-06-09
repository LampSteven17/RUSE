"""Service-mix responder daemon — runs on the per-deploy neighborhood sidecar.

PHASE's service_mix_targets (v1) wants Zeek to emit conn.log rows labeled
service=smb/splunk/udp originating from each SUP. Empirically (human
CPTC9_24 Zeek data) Zeek assigns those labels via DEEP PACKET INSPECTION on
real protocol bytes, NOT by port — so a SUP SYN-ing a dead fake-infra IP
produces conn_state=S0 with no payload and gets service="-", which does
nothing for the gap. The connection must COMPLETE and exchange protocol
bytes so Zeek's analyzer fires.

This daemon is the responder. It co-lives on the neighborhood sidecar VM
(same /16 as the SUPs, intra-default-SG reachable) and answers the
service-mix generators in common/network/service_mix.py:

  TCP 445  (smb)    — accept, read the SMB negotiate request, send a
                      negotiate response (~resp bytes like the human),
                      then RST. Reproduces the human pattern (86% RSTR,
                      orig~350B / resp~400B). Zeek confirms service=smb on
                      the orig's \\xffSMB negotiate over an ESTABLISHED conn.
  TCP 9997 (splunk) — accept, read the forwarder S2S preamble, send a
                      cooked-mode ack payload, clean shutdown (SF, like the
                      human's 100% SF). Splunk has no native Zeek analyzer —
                      the label comes from a sensor-side signature we can't
                      read here, so this path is SENSOR-VALIDATED-ONLY.
  UDP ports         — echo each datagram back so the flow is bidirectional
                      (human udp rows carry resp bytes), proto=udp,
                      service-less → PHASE relabels "udp".

Idle-safe: it only listens. No SUP connects → no traffic. So it is benign
on any deploy and is installed on the sidecar unconditionally.

Shared wire constants (ports, payloads) live in service_mix.py so the two
ends can't drift; this module imports them.
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
import sys
import threading
import time
from pathlib import Path

# Importable both from the deploy tree and as a standalone script on the sidecar.
try:
    from common.network.service_mix import (
        SERVICE_MIX_UDP_PORTS, SMB_PORT, SPLUNK_PORT,
        SPLUNK_S2S_PREAMBLE, SMB_NEGOTIATE_RESPONSE, SPLUNK_ACK_PAYLOAD,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from common.network.service_mix import (
        SERVICE_MIX_UDP_PORTS, SMB_PORT, SPLUNK_PORT,
        SPLUNK_S2S_PREAMBLE, SMB_NEGOTIATE_RESPONSE, SPLUNK_ACK_PAYLOAD,
    )


LOG_PATH_DEFAULT = Path("/var/log/ruse-service-responder.jsonl")
ACCEPT_BACKLOG = 64
RECV_BUF = 8192
CLIENT_TIMEOUT = 10.0


def _rst_close(sock: socket.socket) -> None:
    """Close with SO_LINGER 0 → sends RST → orig records conn_state RSTR
    (matches the human smb pattern, 86% RSTR)."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                        struct.pack("ii", 1, 0))
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _graceful_close(sock: socket.socket) -> None:
    """Clean bidirectional shutdown → conn_state SF (matches splunk's 100% SF)."""
    try:
        sock.shutdown(socket.SHUT_WR)
        sock.settimeout(2.0)
        while True:
            if not sock.recv(RECV_BUF):
                break
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


class ServiceResponder:
    """Multi-listener responder: one thread per TCP port + one per UDP port."""

    def __init__(self, logger: logging.Logger,
                 smb_port: int = SMB_PORT, splunk_port: int = SPLUNK_PORT,
                 udp_ports=SERVICE_MIX_UDP_PORTS):
        self.logger = logger
        self.smb_port = smb_port
        self.splunk_port = splunk_port
        self.udp_ports = list(udp_ports)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._counts = {"smb": 0, "splunk": 0, "udp": 0}
        self._counts_lock = threading.Lock()

    # ── per-service handlers ─────────────────────────────────────────────

    def _bump(self, service: str) -> None:
        with self._counts_lock:
            self._counts[service] += 1

    def _handle_smb(self, conn: socket.socket, peer) -> None:
        try:
            conn.settimeout(CLIENT_TIMEOUT)
            conn.recv(RECV_BUF)                  # SMB negotiate request
            conn.sendall(SMB_NEGOTIATE_RESPONSE)  # ~400B negotiate response
        except OSError:
            pass
        finally:
            _rst_close(conn)                      # RST → RSTR
        self._bump("smb")

    def _handle_splunk(self, conn: socket.socket, peer) -> None:
        try:
            conn.settimeout(CLIENT_TIMEOUT)
            conn.recv(RECV_BUF)                  # forwarder S2S preamble
            conn.sendall(SPLUNK_ACK_PAYLOAD)     # cooked-mode ack
        except OSError:
            pass
        finally:
            _graceful_close(conn)                 # SF
        self._bump("splunk")

    # ── listener loops ───────────────────────────────────────────────────

    def _tcp_listener(self, port: int, handler) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(ACCEPT_BACKLOG)
            srv.settimeout(1.0)
        except OSError as e:
            self.logger.error(json.dumps({
                "event": "listen_error", "proto": "tcp", "port": port,
                "error": repr(e)[:160]}))
            return
        self.logger.info(json.dumps({"event": "listening", "proto": "tcp", "port": port}))
        while not self._stop.is_set():
            try:
                conn, peer = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # Handle inline (handlers are short); a slow client only blocks its
            # own port thread, and the daemon's volume is modest.
            try:
                handler(conn, peer)
            except Exception as e:
                self.logger.warning(json.dumps({
                    "event": "handler_error", "port": port,
                    "error": repr(e)[:120]}))
        try:
            srv.close()
        except OSError:
            pass

    def _udp_listener(self, port: int) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.settimeout(1.0)
        except OSError as e:
            self.logger.error(json.dumps({
                "event": "listen_error", "proto": "udp", "port": port,
                "error": repr(e)[:160]}))
            return
        self.logger.info(json.dumps({"event": "listening", "proto": "udp", "port": port}))
        while not self._stop.is_set():
            try:
                data, peer = srv.recvfrom(RECV_BUF)
            except socket.timeout:
                continue
            except OSError:
                break
            # Echo back so the flow is bidirectional (human udp carries resp bytes).
            try:
                srv.sendto(data or b"\x00", peer)
            except OSError:
                pass
            self._bump("udp")
        try:
            srv.close()
        except OSError:
            pass

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        specs = [("tcp", self.smb_port, self._handle_smb),
                 ("tcp", self.splunk_port, self._handle_splunk)]
        for _, port, handler in specs:
            t = threading.Thread(target=self._tcp_listener, args=(port, handler),
                                 name=f"tcp-{port}", daemon=True)
            t.start()
            self._threads.append(t)
        for port in self.udp_ports:
            t = threading.Thread(target=self._udp_listener, args=(port,),
                                 name=f"udp-{port}", daemon=True)
            t.start()
            self._threads.append(t)
        self.logger.info(json.dumps({
            "event": "daemon_start",
            "tcp_ports": [self.smb_port, self.splunk_port],
            "udp_ports": self.udp_ports}))

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        self.start()
        last = 0.0
        try:
            while not self._stop.is_set():
                time.sleep(1.0)
                now = time.monotonic()
                if now - last >= 60.0:        # heartbeat so audit can tell idle≠dead
                    last = now
                    with self._counts_lock:
                        snap = dict(self._counts)
                    self.logger.info(json.dumps({"event": "heartbeat", "served": snap}))
        finally:
            self.stop()
            self.logger.info(json.dumps({"event": "daemon_stop"}))


def _build_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ruse-service-responder")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    logger.addHandler(sh)
    return logger


def main() -> int:
    p = argparse.ArgumentParser(description="RUSE service-mix responder daemon")
    p.add_argument("--log", default=str(LOG_PATH_DEFAULT))
    p.add_argument("--smb-port", type=int, default=SMB_PORT)
    p.add_argument("--splunk-port", type=int, default=SPLUNK_PORT)
    args = p.parse_args()
    logger = _build_logger(Path(args.log))
    responder = ServiceResponder(logger, smb_port=args.smb_port,
                                 splunk_port=args.splunk_port)

    import signal
    signal.signal(signal.SIGTERM, lambda *_: responder.stop())
    signal.signal(signal.SIGINT, lambda *_: responder.stop())

    responder.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
