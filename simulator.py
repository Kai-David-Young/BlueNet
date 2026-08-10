"""
BlueNet network simulator for local testing.
Runs multiple mesh nodes in a single process connected via in-memory pipes,
simulating a Bluetooth mesh without actual Bluetooth hardware.

Usage:
    python simulator.py --nodes 3

Each node opens a Tkinter window. Messages route through the simulated mesh.
"""

import sys
import os
import time
import queue
import threading
import logging
import argparse

sys.path.insert(0, os.path.dirname(__file__))

from core.mesh     import MeshNode
from core.protocol import encode_packet, decode_stream
from core.content  import SiteStore, default_home_site

log = logging.getLogger("bluenet.sim")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# ── Simulated in-memory link ──────────────────────────────────────────────────
class SimLink:
    """Bidirectional pipe between two SimNode instances."""
    def __init__(self, a: "SimNode", b: "SimNode", latency_ms: float = 5):
        self.a = a
        self.b = b
        self.latency = latency_ms / 1000

    def deliver_to_a(self, data: bytes):
        threading.Timer(self.latency,
                        lambda: self.a.receive(self.b.addr, data)).start()

    def deliver_to_b(self, data: bytes):
        threading.Timer(self.latency,
                        lambda: self.b.receive(self.a.addr, data)).start()


class SimNode:
    """A simulated Bluetooth node with a MeshNode attached."""

    def __init__(self, addr: str, name: str, data_dir: str = "."):
        self.addr      = addr
        self.name      = name
        self._links: dict[str, SimLink] = {}
        self._bufs:  dict[str, bytearray] = {}

        db_prefix = os.path.join(data_dir, f"sim_{addr.replace(':', '')}")
        self.site_store = SiteStore(db_prefix + "_sites.db")
        if not self.site_store.get_local("/"):
            self.site_store.publish(
                "/", default_home_site(addr, name))

        self.node = MeshNode(local_addr=addr, name=name)
        self.node.on_site_request = lambda src, sname, path, rid: \
            self.site_store.get_local(path)

    def connect_to(self, other: "SimNode"):
        if other.addr in self._links:
            return
        link = SimLink(self, other)
        self._links[other.addr]       = link
        other._links[self.addr]       = link

        def send_to_other(data: bytes, _link=link, _other=other):
            _link.deliver_to_b(data) if self.addr == _link.a.addr \
                else _link.deliver_to_a(data)

        def send_to_self(data: bytes, _link=link, _self=self):
            _link.deliver_to_a(data) if self.addr == _link.a.addr \
                else _link.deliver_to_b(data)

        self.node.peer_connected(other.addr, other.name, send_to_other)
        other.node.peer_connected(self.addr, self.name, send_to_self)

    def receive(self, from_addr: str, data: bytes):
        self.node.receive_bytes(from_addr, data)

    def disconnect_from(self, other: "SimNode"):
        self._links.pop(other.addr, None)
        other._links.pop(self.addr, None)
        self.node.peer_disconnected(other.addr)
        other.node.peer_disconnected(self.addr)


# ── Interactive CLI simulator ─────────────────────────────────────────────────
def run_cli(n_nodes: int):
    import tempfile
    data_dir = tempfile.mkdtemp(prefix="bluenet_sim_")

    nodes = []
    for i in range(n_nodes):
        addr = f"AA:BB:CC:DD:EE:{i:02X}"
        node = SimNode(addr, f"Node-{i}", data_dir)
        nodes.append(node)

    # Connect in a line topology (A-B-C-D…)
    for i in range(len(nodes) - 1):
        nodes[i].connect_to(nodes[i + 1])
        print(f"Connected: {nodes[i].name} <-> {nodes[i+1].name}")

    # Add chat callbacks
    for node in nodes:
        n = node  # capture
        def _chat(src, sname, text, grp, _n=n):
            print(f"\n[{_n.name}] MSG FROM {sname}: {text}")
        node.node.on_chat = _chat
        def _bcast(text, _n=n):
            print(f"\n[{_n.name}] BROADCAST: {text}")
        node.node.on_broadcast = _bcast

    print(f"\nSimulator running with {n_nodes} nodes in a chain topology.")
    print("Commands: chat <from_idx> <to_idx> <msg>")
    print("          bcast <from_idx> <msg>")
    print("          connect <a_idx> <b_idx>")
    print("          disconnect <a_idx> <b_idx>")
    print("          quit\n")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down simulator.")
            break

        if not line:
            continue
        parts = line.split(maxsplit=3)
        cmd   = parts[0].lower()

        if cmd == "quit":
            break
        elif cmd == "chat" and len(parts) >= 4:
            fi, ti = int(parts[1]), int(parts[2])
            msg = parts[3]
            nodes[fi].node.send_chat(nodes[ti].addr, msg)
            print(f"Sent from {nodes[fi].name} to {nodes[ti].name}: {msg}")
        elif cmd == "bcast" and len(parts) >= 3:
            fi = int(parts[1])
            msg = " ".join(parts[2:])
            nodes[fi].node.send_broadcast(msg)
            print(f"Broadcast from {nodes[fi].name}: {msg}")
        elif cmd == "connect" and len(parts) == 3:
            a, b = int(parts[1]), int(parts[2])
            nodes[a].connect_to(nodes[b])
            print(f"Connected {nodes[a].name} <-> {nodes[b].name}")
        elif cmd == "disconnect" and len(parts) == 3:
            a, b = int(parts[1]), int(parts[2])
            nodes[a].disconnect_from(nodes[b])
            print(f"Disconnected {nodes[a].name} <-> {nodes[b].name}")
        else:
            print("Unknown command")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="BlueNet Mesh Simulator")
    ap.add_argument("--nodes", type=int, default=3,
                    help="Number of simulated nodes")
    args = ap.parse_args()
    run_cli(args.nodes)
