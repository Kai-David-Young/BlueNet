"""
BlueNet Protocol Layer
Defines packet structure, message types, serialization, and framing.
"""

import json
import uuid
import time
import zlib
import base64
import hashlib
import struct
from typing import Any, Dict, Optional


# ── Service Constants ────────────────────────────────────────────────────────
SERVICE_UUID   = "94f39d29-7d6d-437d-973b-fba39e49d4ef"
SERVICE_NAME   = "BlueNet"
RFCOMM_PORT    = 3
MAX_TTL        = 7
BROADCAST_ADDR = "*"
PROTOCOL_VER   = "1.0"
MAX_PACKET_SIZE = 65535  # bytes
SEEN_ID_TTL     = 120    # seconds before forgetting a seen packet ID


# ── Message Types ────────────────────────────────────────────────────────────
class MsgType:
    # Presence / discovery
    HELLO        = "HELLO"      # Announce node presence + routing table
    HELLO_ACK    = "HELLO_ACK"  # Acknowledge presence
    PING         = "PING"       # Keep-alive probe
    PONG         = "PONG"       # Keep-alive response
    BYE          = "BYE"        # Graceful disconnect
    # Routing
    ROUTE_UPDATE = "ROUTE_UPD"  # Distribute routing table
    # Messaging
    CHAT         = "CHAT"       # Chat message (DM or group)
    CHAT_ACK     = "CHAT_ACK"   # Delivery receipt
    BROADCAST    = "BROADCAST"  # Flood broadcast (no ACK)
    # Web content
    SITE_REQUEST = "SITE_REQ"   # Request a BlueWeb page
    SITE_RESPONSE= "SITE_RES"   # BlueWeb page content
    # File transfer
    FILE_OFFER   = "FILE_OFFER" # Offer a file
    FILE_ACCEPT  = "FILE_ACPT"  # Accept file offer
    FILE_CHUNK   = "FILE_CHNK"  # File data chunk
    FILE_DONE    = "FILE_DONE"  # Transfer complete


# ── Packet Factory ───────────────────────────────────────────────────────────
def make_packet(
    msg_type: str,
    src: str,
    dst: str,
    payload: Dict[str, Any],
    ttl: int = MAX_TTL,
    reply_to: Optional[str] = None,
) -> Dict:
    pkt = {
        "ver":     PROTOCOL_VER,
        "type":    msg_type,
        "id":      str(uuid.uuid4()),
        "src":     src,
        "dst":     dst,
        "ttl":     ttl,
        "ts":      time.time(),
        "hops":    [],          # list of relay MACs for debugging
        "payload": payload,
    }
    if reply_to:
        pkt["reply_to"] = reply_to
    return pkt


# ── Serialization ────────────────────────────────────────────────────────────
def encode_packet(pkt: Dict) -> bytes:
    """Serialize and length-prefix a packet for stream transmission."""
    data = json.dumps(pkt, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_PACKET_SIZE:
        raise ValueError(f"Packet too large: {len(data)} bytes")
    # 4-byte big-endian length prefix
    return struct.pack(">I", len(data)) + data


def decode_stream(buf: bytearray) -> tuple[list, bytearray]:
    """
    Extract complete packets from a byte buffer.
    Returns (list_of_packets, remaining_buffer).
    """
    packets = []
    while len(buf) >= 4:
        length = struct.unpack(">I", buf[:4])[0]
        if len(buf) < 4 + length:
            break  # incomplete packet, wait for more data
        raw = buf[4:4 + length]
        buf = buf[4 + length:]
        try:
            pkt = json.loads(raw.decode("utf-8"))
            packets.append(pkt)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # discard malformed packet
    return packets, buf


# ── Compression Helpers ──────────────────────────────────────────────────────
def compress_bytes(data: bytes) -> str:
    """Compress bytes and return base64 string."""
    return base64.b64encode(zlib.compress(data, level=9)).decode("ascii")


def decompress_bytes(data: str) -> bytes:
    """Decompress a base64+zlib string back to bytes."""
    return zlib.decompress(base64.b64decode(data))


def compress_text(text: str) -> str:
    return compress_bytes(text.encode("utf-8"))


def decompress_text(data: str) -> str:
    return decompress_bytes(data).decode("utf-8")


# ── Checksum ─────────────────────────────────────────────────────────────────
def packet_hash(pkt: Dict) -> str:
    """SHA-256 fingerprint of a packet (excluding mutable fields)."""
    copy = {k: v for k, v in pkt.items() if k not in ("hops", "ttl")}
    raw = json.dumps(copy, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ── Convenience constructors ─────────────────────────────────────────────────
def hello_packet(src: str, node_name: str, routing_table: dict) -> Dict:
    return make_packet(MsgType.HELLO, src, BROADCAST_ADDR, {
        "name":    node_name,
        "routes":  routing_table,
    }, ttl=1)  # HELLO only goes to direct peers


def chat_packet(src: str, dst: str, text: str,
                group: Optional[str] = None) -> Dict:
    payload: Dict[str, Any] = {"text": text}
    if group:
        payload["group"] = group
    return make_packet(MsgType.CHAT, src, dst, payload)


def site_request_packet(src: str, dst: str, path: str) -> Dict:
    return make_packet(MsgType.SITE_REQUEST, src, dst, {"path": path})


def site_response_packet(src: str, dst: str, path: str,
                          site_data: dict, reply_to: str) -> Dict:
    return make_packet(MsgType.SITE_RESPONSE, src, dst,
                       {"path": path, "site": site_data},
                       reply_to=reply_to)


def route_update_packet(src: str, routing_table: dict) -> Dict:
    return make_packet(MsgType.ROUTE_UPDATE, src, BROADCAST_ADDR,
                       {"routes": routing_table}, ttl=MAX_TTL)
