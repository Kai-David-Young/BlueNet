"""
Windows Bluetooth Adapter (PyBluez / RFCOMM)
Handles device discovery, RFCOMM server, and outbound connections.
"""

import bluetooth
import threading
import logging
import time
from typing import Callable, Optional

from core.protocol import SERVICE_UUID, SERVICE_NAME, RFCOMM_PORT

log = logging.getLogger("bluenet.bt_win")

RECV_CHUNK = 4096


class BluetoothAdapterWindows:
    """
    Manages:
      - RFCOMM server socket (accepting inbound connections)
      - Outbound RFCOMM connections to discovered peers
      - Continuous device scan
    """

    def __init__(
        self,
        on_peer_connected:    Callable[[str, str, Callable[[bytes], None]], None],
        on_peer_disconnected:  Callable[[str], None],
        on_data_received:      Callable[[str, bytes], None],
    ):
        self._on_connected    = on_peer_connected
        self._on_disconnected = on_peer_disconnected
        self._on_data         = on_data_received

        self._connections: dict[str, bluetooth.BluetoothSocket] = {}
        self._lock        = threading.Lock()
        self._running     = False
        self._local_addr  = ""
        self._local_name  = ""

    # ── Start / stop ─────────────────────────────────────────────────────────
    def start(self):
        self._running = True
        try:
            self._local_addr = bluetooth.read_local_bdaddr()
            self._local_name = bluetooth.read_local_name()
        except Exception as e:
            log.error("Could not read local BT address: %s", e)
            raise

        log.info("Local BT: %s  (%s)", self._local_addr, self._local_name)

        threading.Thread(target=self._server_loop, daemon=True).start()
        threading.Thread(target=self._scan_loop,   daemon=True).start()

    def stop(self):
        self._running = False
        with self._lock:
            for sock in self._connections.values():
                try:
                    sock.close()
                except Exception:
                    pass
            self._connections.clear()

    @property
    def local_addr(self) -> str:
        return self._local_addr

    @property
    def local_name(self) -> str:
        return self._local_name

    # ── RFCOMM server ─────────────────────────────────────────────────────────
    def _server_loop(self):
        server = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        try:
            server.bind(("", bluetooth.PORT_ANY))
            server.listen(5)
            port = server.getsockname()[1]
            bluetooth.advertise_service(
                server,
                SERVICE_NAME,
                service_id=SERVICE_UUID,
                service_classes=[SERVICE_UUID, bluetooth.SERIAL_PORT_CLASS],
                profiles=[bluetooth.SERIAL_PORT_PROFILE],
            )
            log.info("RFCOMM server listening on port %d", port)
        except Exception as e:
            log.error("Server start failed: %s", e)
            server.close()
            return

        while self._running:
            try:
                server.settimeout(2.0)
                client_sock, (addr, _) = server.accept()
                log.info("Inbound connection from %s", addr)
                threading.Thread(
                    target=self._handle_connection,
                    args=(addr, client_sock),
                    daemon=True,
                ).start()
            except bluetooth.btcommon.BluetoothError:
                continue
            except Exception as e:
                log.error("Accept error: %s", e)

        try:
            bluetooth.stop_advertising(server)
            server.close()
        except Exception:
            pass

    # ── Outbound connection ───────────────────────────────────────────────────
    def connect_to(self, addr: str) -> bool:
        addr = addr.upper()
        with self._lock:
            if addr in self._connections:
                return True

        log.info("Connecting to %s ...", addr)
        try:
            services = bluetooth.find_service(uuid=SERVICE_UUID, address=addr)
            if not services:
                log.warning("BlueNet service not found on %s", addr)
                return False
            port = services[0]["port"]
            sock  = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            sock.connect((addr, port))
        except Exception as e:
            log.warning("Connect to %s failed: %s", addr, e)
            return False

        log.info("Connected to %s", addr)
        threading.Thread(
            target=self._handle_connection,
            args=(addr, sock),
            daemon=True,
        ).start()
        return True

    # ── Per-connection handler ────────────────────────────────────────────────
    def _handle_connection(self, addr: str, sock: bluetooth.BluetoothSocket):
        addr = addr.upper()
        with self._lock:
            if addr in self._connections:
                sock.close()
                return
            self._connections[addr] = sock

        def send_fn(data: bytes):
            try:
                sock.sendall(data)
            except Exception as e:
                log.warning("Send to %s failed: %s", addr, e)
                self._disconnect(addr)

        # Notify mesh node – name will be learned from HELLO
        self._on_connected(addr, addr, send_fn)

        try:
            while self._running:
                try:
                    sock.settimeout(5.0)
                    chunk = sock.recv(RECV_CHUNK)
                    if not chunk:
                        break
                    self._on_data(addr, chunk)
                except bluetooth.btcommon.BluetoothError:
                    continue
        except Exception as e:
            log.info("Connection to %s closed: %s", addr, e)
        finally:
            self._disconnect(addr)

    def _disconnect(self, addr: str):
        with self._lock:
            sock = self._connections.pop(addr, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass
            self._on_disconnected(addr)

    # ── Discovery scan ────────────────────────────────────────────────────────
    def _scan_loop(self):
        while self._running:
            try:
                log.info("Scanning for nearby Bluetooth devices...")
                devices = bluetooth.discover_devices(
                    duration=8, lookup_names=True, flush_cache=True
                )
                for addr, name in devices:
                    addr = addr.upper()
                    log.info("Found device: %s (%s)", name, addr)
                    with self._lock:
                        already = addr in self._connections
                    if not already:
                        self.connect_to(addr)
            except Exception as e:
                log.warning("Scan error: %s", e)
            # Scan every 60 s
            for _ in range(60):
                if not self._running:
                    return
                time.sleep(1)

    def discovered_devices(self):
        """Return currently connected peer addresses."""
        with self._lock:
            return list(self._connections.keys())
