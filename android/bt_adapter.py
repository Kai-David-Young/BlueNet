"""
Android Bluetooth Adapter (via jnius + Android Java API)
Handles RFCOMM server and client connections using Android's BluetoothAdapter.
This module is only imported when running on Android (inside Kivy/buildozer).
"""

import threading
import logging
import time
from typing import Callable

log = logging.getLogger("bluenet.bt_android")

from core.protocol import SERVICE_UUID, SERVICE_NAME

RECV_CHUNK = 4096


def _get_java():
    """Lazy-import jnius classes (only available on Android)."""
    from jnius import autoclass, cast
    BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
    BluetoothDevice  = autoclass("android.bluetooth.BluetoothDevice")
    UUID             = autoclass("java.util.UUID")
    return BluetoothAdapter, BluetoothDevice, UUID


class BluetoothAdapterAndroid:
    """
    Android Bluetooth adapter wrapping the Java BluetoothAdapter API.
    Provides the same interface as BluetoothAdapterWindows.
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

        self._connections: dict = {}
        self._lock = threading.Lock()
        self._running = False
        self._local_addr = ""
        self._local_name = ""
        self._adapter    = None

    def start(self):
        self._running = True
        BluetoothAdapter, _, _ = _get_java()
        self._adapter = BluetoothAdapter.getDefaultAdapter()

        if self._adapter is None:
            raise RuntimeError("No Bluetooth adapter found")
        if not self._adapter.isEnabled():
            raise RuntimeError("Bluetooth is disabled – please enable it")

        self._local_addr = self._adapter.getAddress()
        self._local_name = self._adapter.getName()
        log.info("Android BT: %s (%s)", self._local_addr, self._local_name)

        # Make discoverable for 300 s (triggered by activity intent separately)
        threading.Thread(target=self._server_loop,   daemon=True).start()
        threading.Thread(target=self._scan_loop,     daemon=True).start()

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
        _, _, UUID = _get_java()
        uuid_obj = UUID.fromString(SERVICE_UUID)
        server_socket = self._adapter.listenUsingRfcommWithServiceRecord(
            SERVICE_NAME, uuid_obj
        )
        log.info("Android RFCOMM server listening")

        while self._running:
            try:
                client_socket = server_socket.accept(2000)  # 2 s timeout
                if client_socket:
                    device = client_socket.getRemoteDevice()
                    addr   = device.getAddress().upper()
                    log.info("Inbound connection from %s", addr)
                    threading.Thread(
                        target=self._handle_connection,
                        args=(addr, client_socket),
                        daemon=True,
                    ).start()
            except Exception as e:
                if self._running:
                    log.debug("Server accept timeout: %s", e)

        try:
            server_socket.close()
        except Exception:
            pass

    # ── Outbound connection ───────────────────────────────────────────────────
    def connect_to(self, addr: str) -> bool:
        addr = addr.upper()
        with self._lock:
            if addr in self._connections:
                return True
        try:
            _, BluetoothDevice, UUID = _get_java()
            device   = self._adapter.getRemoteDevice(addr)
            uuid_obj = UUID.fromString(SERVICE_UUID)
            sock     = device.createRfcommSocketToServiceRecord(uuid_obj)
            # Cancel discovery before connecting
            self._adapter.cancelDiscovery()
            sock.connect()
            log.info("Connected to %s", addr)
            threading.Thread(
                target=self._handle_connection,
                args=(addr, sock),
                daemon=True,
            ).start()
            return True
        except Exception as e:
            log.warning("Connect to %s failed: %s", addr, e)
            return False

    # ── Per-connection handler ────────────────────────────────────────────────
    def _handle_connection(self, addr: str, sock):
        with self._lock:
            if addr in self._connections:
                sock.close()
                return
            self._connections[addr] = sock

        output_stream = sock.getOutputStream()

        def send_fn(data: bytes):
            try:
                output_stream.write(data)
                output_stream.flush()
            except Exception as e:
                log.warning("Send to %s failed: %s", addr, e)
                self._disconnect(addr)

        self._on_connected(addr, addr, send_fn)

        input_stream = sock.getInputStream()
        buf = bytearray(RECV_CHUNK)

        try:
            while self._running:
                try:
                    n = input_stream.read(buf)
                    if n < 0:
                        break
                    self._on_data(addr, bytes(buf[:n]))
                except Exception as e:
                    log.debug("Recv error on %s: %s", addr, e)
                    break
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
        """
        Start discovery and attempt to connect to found devices.
        Uses BroadcastReceiver via PythonActivity to receive ACTION_FOUND.
        """
        while self._running:
            self._do_scan()
            for _ in range(60):
                if not self._running:
                    return
                time.sleep(1)

    def _do_scan(self):
        try:
            # Get paired devices first
            paired = self._adapter.getBondedDevices()
            iterator = paired.iterator()
            while iterator.hasNext():
                device = iterator.next()
                addr = device.getAddress().upper()
                with self._lock:
                    already = addr in self._connections
                if not already and addr != self._local_addr:
                    self.connect_to(addr)

            # Start discovery for new devices
            if self._adapter.isDiscovering():
                self._adapter.cancelDiscovery()
            self._adapter.startDiscovery()

            # Listen for ACTION_FOUND via android broadcast
            self._register_discovery_receiver()
            time.sleep(12)  # discovery takes ~12 s
            self._adapter.cancelDiscovery()
        except Exception as e:
            log.warning("Scan failed: %s", e)

    def _register_discovery_receiver(self):
        """Register a BroadcastReceiver for BluetoothDevice.ACTION_FOUND."""
        try:
            from android.broadcast import BroadcastReceiver
            from jnius import autoclass
            BluetoothDevice = autoclass("android.bluetooth.BluetoothDevice")

            def on_found(context, intent):
                device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                if device:
                    addr = device.getAddress().upper()
                    with self._lock:
                        already = addr in self._connections
                    if not already and addr != self._local_addr:
                        threading.Thread(
                            target=self.connect_to, args=(addr,),
                            daemon=True
                        ).start()

            br = BroadcastReceiver(on_found,
                                   actions=[BluetoothDevice.ACTION_FOUND])
            br.start()
            # Stop after 15 s
            threading.Timer(15, br.stop).start()
        except Exception as e:
            log.debug("BroadcastReceiver setup failed: %s", e)
