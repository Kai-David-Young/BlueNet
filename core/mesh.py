"""
BlueNet Mesh Node
Handles peer lifecycle, routing, packet forwarding, and message dispatch.
Platform-specific Bluetooth adapters inject themselves via the adapter interface.
"""

import time
import threading
import logging
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set

from .protocol import (
    MsgType, BROADCAST_ADDR, MAX_TTL, SEEN_ID_TTL,
    make_packet, encode_packet, decode_stream,
    hello_packet, route_update_packet,
)

log = logging.getLogger("bluenet.mesh")


class Peer:
    """Represents a directly connected Bluetooth peer."""
    def __init__(self, addr: str, name: str, send_fn: Callable[[bytes], None]):
        self.addr     = addr
        self.name     = name
        self.send     = send_fn          # callable(bytes)
        self.last_seen = time.time()
        self.alive     = True

    def touch(self):
        self.last_seen = time.time()


class RoutingTable:
    """
    Distance-vector routing table.
    Entry: dst_addr -> (next_hop_addr, hop_count, last_updated)
    """
    def __init__(self, local_addr: str):
        self.local = local_addr
        self._lock  = threading.Lock()
        # dst -> {next_hop, hops, ts}
        self._table: Dict[str, dict] = {}

    def update(self, dst: str, next_hop: str, hops: int) -> bool:
        """Return True if the table changed."""
        if dst == self.local:
            return False
        with self._lock:
            existing = self._table.get(dst)
            if existing is None or hops < existing["hops"]:
                self._table[dst] = {
                    "next_hop": next_hop,
                    "hops":     hops,
                    "ts":       time.time(),
                }
                return True
        return False

    def remove_via(self, next_hop: str):
        """Remove all routes that go through next_hop (peer disconnected)."""
        with self._lock:
            stale = [d for d, r in self._table.items()
                     if r["next_hop"] == next_hop]
            for d in stale:
                del self._table[d]

    def next_hop(self, dst: str) -> Optional[str]:
        with self._lock:
            entry = self._table.get(dst)
            return entry["next_hop"] if entry else None

    def snapshot(self) -> Dict[str, int]:
        """Return {dst: hop_count} for sharing with peers."""
        with self._lock:
            return {d: r["hops"] for d, r in self._table.items()}

    def expire(self, max_age: float = 120.0):
        """Remove stale routes."""
        now = time.time()
        with self._lock:
            stale = [d for d, r in self._table.items()
                     if now - r["ts"] > max_age]
            for d in stale:
                del self._table[d]


class SeenSet:
    """Deduplicates packets by ID with time-based expiry."""
    def __init__(self, ttl: float = SEEN_ID_TTL):
        self._ttl   = ttl
        self._seen: Dict[str, float] = {}
        self._lock  = threading.Lock()

    def add_if_new(self, pkt_id: str) -> bool:
        """Return True if we have NOT seen this ID before (and add it)."""
        now = time.time()
        with self._lock:
            if pkt_id in self._seen:
                return False
            self._seen[pkt_id] = now
            return True

    def expire(self):
        now = time.time()
        with self._lock:
            stale = [k for k, ts in self._seen.items()
                     if now - ts > self._ttl]
            for k in stale:
                del self._seen[k]


class MeshNode:
    """
    Core mesh networking node.

    Usage:
        node = MeshNode(local_addr="AA:BB:CC:DD:EE:FF", name="Alice")
        node.on_chat = my_chat_handler
        node.on_site_request = my_site_handler
        # call node.peer_connected(addr, name, send_fn) when BT connects
        # call node.receive_bytes(addr, data) when bytes arrive
    """

    def __init__(self, local_addr: str, name: str):
        self.addr   = local_addr.upper()
        self.name   = name
        self.routes = RoutingTable(self.addr)
        self._seen  = SeenSet()
        self._peers: Dict[str, Peer] = {}   # addr -> Peer
        self._lock  = threading.Lock()
        self._bufs: Dict[str, bytearray] = defaultdict(bytearray)

        # ── Callbacks (set by application layer) ────────────────────────────
        # fn(src_addr, src_name, text, group)
        self.on_chat:          Optional[Callable] = None
        # fn(src_addr, msg_id)
        self.on_chat_ack:      Optional[Callable] = None
        # fn(src_addr, src_name, path, reply_id) -> dict (site data)
        self.on_site_request:  Optional[Callable] = None
        # fn(path, site_data)
        self.on_site_response: Optional[Callable] = None
        # fn(addr, name, connected: bool)
        self.on_peer_change:   Optional[Callable] = None
        # fn(text)  - raw broadcast
        self.on_broadcast:     Optional[Callable] = None

        self._start_maintenance()

    # ── Peer lifecycle ───────────────────────────────────────────────────────
    def peer_connected(self, addr: str, name: str,
                       send_fn: Callable[[bytes], None]):
        addr = addr.upper()
        log.info("Peer connected: %s (%s)", name, addr)
        peer = Peer(addr, name, send_fn)
        with self._lock:
            self._peers[addr] = peer
        self.routes.update(addr, addr, 1)
        self._send_hello(peer)
        if self.on_peer_change:
            self.on_peer_change(addr, name, True)

    def peer_disconnected(self, addr: str):
        addr = addr.upper()
        with self._lock:
            peer = self._peers.pop(addr, None)
        if peer:
            peer.alive = False
            log.info("Peer disconnected: %s (%s)", peer.name, addr)
        self.routes.remove_via(addr)
        self._broadcast_route_update()
        if self.on_peer_change and peer:
            self.on_peer_change(addr, peer.name, False)

    def connected_peers(self) -> List[Peer]:
        with self._lock:
            return list(self._peers.values())

    # ── Receiving data ───────────────────────────────────────────────────────
    def receive_bytes(self, from_addr: str, data: bytes):
        from_addr = from_addr.upper()
        buf = self._bufs[from_addr]
        buf.extend(data)
        packets, remaining = decode_stream(buf)
        self._bufs[from_addr] = remaining
        for pkt in packets:
            self._handle_packet(from_addr, pkt)

    def _handle_packet(self, from_addr: str, pkt: dict):
        pkt_id = pkt.get("id", "")
        if not self._seen.add_if_new(pkt_id):
            return  # already processed

        # Update peer last-seen
        with self._lock:
            peer = self._peers.get(from_addr)
        if peer:
            peer.touch()

        dst = pkt.get("dst", "")
        msg_type = pkt.get("type", "")
        src = pkt.get("src", "")

        # ── Route learning ───────────────────────────────────────────────────
        if src and src != self.addr:
            hops = len(pkt.get("hops", [])) + 1
            self.routes.update(src, from_addr, hops)

        # ── Deliver or forward ───────────────────────────────────────────────
        if dst == self.addr or dst == BROADCAST_ADDR:
            self._dispatch(pkt)

        if dst != self.addr:  # forward (also flood broadcasts)
            self._forward(pkt, from_addr)

    def _dispatch(self, pkt: dict):
        t   = pkt.get("type")
        src = pkt.get("src", "")
        pl  = pkt.get("payload", {})

        if t == MsgType.HELLO:
            self._handle_hello(src, pl, pkt)
        elif t == MsgType.HELLO_ACK:
            self._handle_hello_ack(src, pl)
        elif t == MsgType.PING:
            self._send_to_peer(src, make_packet(
                MsgType.PONG, self.addr, src, {}, ttl=1))
        elif t == MsgType.ROUTE_UPDATE:
            self._merge_routes(src, pl.get("routes", {}))
        elif t == MsgType.CHAT:
            if self.on_chat:
                peer_name = self._peer_name(src)
                self.on_chat(src, peer_name, pl.get("text", ""),
                             pl.get("group"))
            # Send ACK
            self._send_to_peer(src, make_packet(
                MsgType.CHAT_ACK, self.addr, src,
                {"ack_id": pkt["id"]}, ttl=MAX_TTL))
        elif t == MsgType.CHAT_ACK:
            if self.on_chat_ack:
                self.on_chat_ack(src, pl.get("ack_id", ""))
        elif t == MsgType.SITE_REQUEST:
            if self.on_site_request:
                site_data = self.on_site_request(
                    src, self._peer_name(src), pl.get("path", "/"),
                    pkt.get("id"))
                if site_data:
                    from .protocol import site_response_packet
                    resp = site_response_packet(
                        self.addr, src, pl.get("path", "/"),
                        site_data, pkt["id"])
                    self._send_to_peer(src, resp)
        elif t == MsgType.SITE_RESPONSE:
            if self.on_site_response:
                self.on_site_response(pl.get("path", "/"), pl.get("site", {}))
        elif t == MsgType.BROADCAST:
            if self.on_broadcast:
                self.on_broadcast(pl.get("text", ""))
        elif t == MsgType.BYE:
            self.peer_disconnected(src)

    def _forward(self, pkt: dict, came_from: str):
        ttl = pkt.get("ttl", 0) - 1
        if ttl <= 0:
            return
        pkt = dict(pkt)
        pkt["ttl"] = ttl
        pkt["hops"] = pkt.get("hops", []) + [self.addr]

        dst = pkt.get("dst", "")
        if dst == BROADCAST_ADDR:
            # Flood to all except origin
            self._flood(encode_packet(pkt), exclude=came_from)
        else:
            next_hop = self.routes.next_hop(dst)
            if next_hop and next_hop != came_from:
                self._deliver_to(next_hop, encode_packet(pkt))
            else:
                # No known route – flood as fallback
                self._flood(encode_packet(pkt), exclude=came_from)

    # ── Sending ──────────────────────────────────────────────────────────────
    def send_chat(self, dst: str, text: str, group: Optional[str] = None):
        from .protocol import chat_packet
        pkt = chat_packet(self.addr, dst, text, group)
        self._send_to_peer(dst, pkt)
        return pkt["id"]

    def send_broadcast(self, text: str):
        pkt = make_packet(MsgType.BROADCAST, self.addr, BROADCAST_ADDR,
                          {"text": text})
        self._flood(encode_packet(pkt))

    def request_site(self, dst: str, path: str):
        from .protocol import site_request_packet
        pkt = site_request_packet(self.addr, dst, path)
        self._send_to_peer(dst, pkt)
        return pkt["id"]

    def _send_to_peer(self, dst: str, pkt: dict):
        dst = dst.upper()
        if dst == self.addr:
            return
        next_hop = self.routes.next_hop(dst) or dst
        self._deliver_to(next_hop, encode_packet(pkt))

    def _deliver_to(self, addr: str, data: bytes):
        with self._lock:
            peer = self._peers.get(addr)
        if peer and peer.alive:
            try:
                peer.send(data)
            except Exception as e:
                log.warning("Send to %s failed: %s", addr, e)
                self.peer_disconnected(addr)

    def _flood(self, data: bytes, exclude: Optional[str] = None):
        with self._lock:
            peers = list(self._peers.values())
        for peer in peers:
            if peer.addr != exclude and peer.alive:
                try:
                    peer.send(data)
                except Exception as e:
                    log.warning("Flood to %s failed: %s", peer.addr, e)

    # ── Hello / routing ──────────────────────────────────────────────────────
    def _send_hello(self, peer: Peer):
        pkt = hello_packet(self.addr, self.name, self.routes.snapshot())
        try:
            peer.send(encode_packet(pkt))
        except Exception as e:
            log.warning("Hello to %s failed: %s", peer.addr, e)

    def _handle_hello(self, src: str, pl: dict, pkt: dict):
        name = pl.get("name", src)
        with self._lock:
            peer = self._peers.get(src)
        if peer:
            peer.name = name
        self._merge_routes(src, pl.get("routes", {}))
        # Send back an ACK with our routes
        ack = make_packet(MsgType.HELLO_ACK, self.addr, src, {
            "name":   self.name,
            "routes": self.routes.snapshot(),
        }, ttl=1)
        self._send_to_peer(src, ack)

    def _handle_hello_ack(self, src: str, pl: dict):
        name = pl.get("name", src)
        with self._lock:
            peer = self._peers.get(src)
        if peer:
            peer.name = name
        self._merge_routes(src, pl.get("routes", {}))

    def _merge_routes(self, via: str, routes: dict):
        changed = False
        for dst, hops in routes.items():
            if dst != self.addr:
                if self.routes.update(dst, via, int(hops) + 1):
                    changed = True
        if changed:
            self._broadcast_route_update()

    def _broadcast_route_update(self):
        pkt = route_update_packet(self.addr, self.routes.snapshot())
        self._flood(encode_packet(pkt))

    # ── Maintenance ──────────────────────────────────────────────────────────
    def _start_maintenance(self):
        t = threading.Thread(target=self._maintenance_loop, daemon=True)
        t.start()

    def _maintenance_loop(self):
        while True:
            time.sleep(30)
            self._seen.expire()
            self.routes.expire()
            # Ping all peers
            with self._lock:
                peers = list(self._peers.values())
            for peer in peers:
                if time.time() - peer.last_seen > 60:
                    log.info("Peer %s timed out", peer.addr)
                    self.peer_disconnected(peer.addr)
                else:
                    ping = make_packet(MsgType.PING, self.addr, peer.addr, {}, ttl=1)
                    try:
                        peer.send(encode_packet(ping))
                    except Exception:
                        self.peer_disconnected(peer.addr)

    # ── Utilities ────────────────────────────────────────────────────────────
    def _peer_name(self, addr: str) -> str:
        with self._lock:
            peer = self._peers.get(addr.upper())
        return peer.name if peer else addr
