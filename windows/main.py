"""
BlueNet Windows Application
Tkinter-based UI: Chat, Browser, Peers, and Sites tabs.
"""

import os
import sys
import time
import queue
import logging
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, font as tkfont

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.mesh    import MeshNode
from core.content import (SiteStore, default_home_site, render_site_text,
                           parse_bt_url, make_bt_url, make_site,
                           make_header_section, make_text_section,
                           make_link_section, make_divider)
from core.store   import MessageStore

log = logging.getLogger("bluenet.win")

ACCENT  = "#1e88e5"
BG      = "#1a1a2e"
BG2     = "#16213e"
FG      = "#e0e0e0"
FG_DIM  = "#9e9e9e"
GREEN   = "#4caf50"
RED     = "#f44336"
YELLOW  = "#ffc107"


def _style(root: tk.Tk):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TNotebook",      background=BG,  borderwidth=0)
    style.configure("TNotebook.Tab",  background=BG2, foreground=FG,
                    padding=[12, 6])
    style.map("TNotebook.Tab",
              background=[("selected", ACCENT)],
              foreground=[("selected", "#ffffff")])
    style.configure("TFrame",  background=BG)
    style.configure("TLabel",  background=BG,  foreground=FG)
    style.configure("TButton", background=ACCENT, foreground="#ffffff",
                    padding=[8, 4])
    style.map("TButton", background=[("active", "#1565c0")])
    style.configure("TEntry",  fieldbackground=BG2, foreground=FG,
                    insertcolor=FG)
    style.configure("TScrollbar", background=BG2)
    style.configure("Peers.TFrame", background=BG2)


class ChatTab(ttk.Frame):
    def __init__(self, parent, app: "BlueNetApp"):
        super().__init__(parent)
        self.app = app
        self._peer_var = tk.StringVar(value="*")
        self._build()

    def _build(self):
        # ── Left: conversation list ──────────────────────────────────────────
        left = tk.Frame(self, bg=BG2, width=160)
        left.pack(side="left", fill="y", padx=(0, 1))
        left.pack_propagate(False)

        tk.Label(left, text="Conversations", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 9, "bold")).pack(pady=(8, 4))

        self._conv_list = tk.Listbox(left, bg=BG2, fg=FG, selectbackground=ACCENT,
                                     borderwidth=0, highlightthickness=0,
                                     font=("Segoe UI", 9))
        self._conv_list.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._conv_list.bind("<<ListboxSelect>>", self._on_conv_select)

        tk.Button(left, text="+ Broadcast", command=self._select_broadcast,
                  bg=BG2, fg=YELLOW, activebackground=BG2, relief="flat",
                  font=("Segoe UI", 9)).pack(pady=(0, 8))

        # ── Right: message area ──────────────────────────────────────────────
        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        self._peer_label = tk.Label(right, textvariable=self._peer_var,
                                    bg=BG, fg=ACCENT,
                                    font=("Segoe UI", 11, "bold"))
        self._peer_label.pack(anchor="w", padx=8, pady=(8, 0))

        self._msg_area = scrolledtext.ScrolledText(
            right, state="disabled", bg=BG2, fg=FG, insertbackground=FG,
            font=("Consolas", 10), wrap="word",
            borderwidth=0, highlightthickness=0, padx=8, pady=8
        )
        self._msg_area.pack(fill="both", expand=True, padx=8, pady=4)
        self._msg_area.tag_config("me",   foreground=ACCENT)
        self._msg_area.tag_config("peer", foreground=GREEN)
        self._msg_area.tag_config("sys",  foreground=FG_DIM,
                                  font=("Consolas", 9, "italic"))
        self._msg_area.tag_config("bcast", foreground=YELLOW)

        entry_frame = tk.Frame(right, bg=BG)
        entry_frame.pack(fill="x", padx=8, pady=(0, 8))

        self._entry = tk.Entry(entry_frame, bg=BG2, fg=FG, insertbackground=FG,
                               font=("Segoe UI", 11), relief="flat")
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._entry.bind("<Return>", self._send)

        send_btn = tk.Button(entry_frame, text="Send", command=self._send,
                             bg=ACCENT, fg="#ffffff", activebackground="#1565c0",
                             relief="flat", font=("Segoe UI", 10))
        send_btn.pack(side="right")

        self._refresh_convs()

    def _refresh_convs(self):
        self._conv_list.delete(0, "end")
        self._conv_list.insert("end", "Broadcast (*)")
        for peer in self.app.node.connected_peers():
            self._conv_list.insert("end", f"{peer.name} ({peer.addr[:8]}...)")
        self.after(5000, self._refresh_convs)

    def _on_conv_select(self, _evt=None):
        sel = self._conv_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx == 0:
            self._select_broadcast()
            return
        peers = self.app.node.connected_peers()
        if idx - 1 < len(peers):
            peer = peers[idx - 1]
            self._peer_var.set(f"{peer.name} ({peer.addr})")
            self._load_history(peer.addr)
            self._current_peer = peer.addr

    def _select_broadcast(self):
        self._peer_var.set("Broadcast to all (*)")
        self._current_peer = "*"
        self._load_broadcast_history()
        self._conv_list.selection_clear(0, "end")
        self._conv_list.selection_set(0)

    def _load_history(self, peer_addr: str):
        msgs = self.app.msg_store.get_conversation(
            self.app.node.addr, peer_addr)
        self._msg_area.config(state="normal")
        self._msg_area.delete("1.0", "end")
        for m in msgs:
            self._render_msg(m)
        self._msg_area.config(state="disabled")
        self._msg_area.see("end")

    def _load_broadcast_history(self):
        msgs = self.app.msg_store.get_broadcast_history()
        self._msg_area.config(state="normal")
        self._msg_area.delete("1.0", "end")
        for m in msgs:
            self._render_msg(m, broadcast=True)
        self._msg_area.config(state="disabled")
        self._msg_area.see("end")

    def _render_msg(self, m: dict, broadcast: bool = False):
        ts   = time.strftime("%H:%M", time.localtime(m.get("ts", 0)))
        src  = m.get("src", "")
        text = m.get("text", "")
        tag  = "bcast" if broadcast else ("me" if src == self.app.node.addr else "peer")
        name = "Me" if src == self.app.node.addr else src[:11]
        self._msg_area.insert("end", f"[{ts}] {name}: ", tag)
        self._msg_area.insert("end", text + "\n")

    def append_message(self, src: str, text: str, broadcast: bool = False):
        """Called from app when a new message arrives for the active convo."""
        ts   = time.strftime("%H:%M")
        tag  = "bcast" if broadcast else ("me" if src == self.app.node.addr else "peer")
        name = "Me" if src == self.app.node.addr else src[:11]
        self._msg_area.config(state="normal")
        self._msg_area.insert("end", f"[{ts}] {name}: ", tag)
        self._msg_area.insert("end", text + "\n")
        self._msg_area.config(state="disabled")
        self._msg_area.see("end")

    def _send(self, _evt=None):
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        dst = getattr(self, "_current_peer", "*")
        if dst == "*":
            self.app.node.send_broadcast(text)
            self.app.msg_store.save(
                f"local-{time.time()}", self.app.node.addr, "*", text,
                sent=True)
            self.append_message(self.app.node.addr, text, broadcast=True)
        else:
            msg_id = self.app.node.send_chat(dst, text)
            self.app.msg_store.save(msg_id, self.app.node.addr, dst, text,
                                    sent=True)
            self.append_message(self.app.node.addr, text)


class BrowserTab(ttk.Frame):
    def __init__(self, parent, app: "BlueNetApp"):
        super().__init__(parent)
        self.app  = app
        self._history: list[str] = []
        self._build()

    def _build(self):
        # ── Address bar ──────────────────────────────────────────────────────
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=8, pady=6)

        tk.Button(bar, text="◀", command=self._back, bg=BG2, fg=FG,
                  relief="flat", font=("Segoe UI", 11)).pack(side="left")

        self._addr_var = tk.StringVar(value="bt://local/")
        addr_entry = tk.Entry(bar, textvariable=self._addr_var, bg=BG2,
                              fg=FG, insertbackground=FG,
                              font=("Segoe UI", 11), relief="flat")
        addr_entry.pack(side="left", fill="x", expand=True, padx=4)
        addr_entry.bind("<Return>", self._navigate)

        tk.Button(bar, text="Go", command=self._navigate, bg=ACCENT,
                  fg="#ffffff", relief="flat",
                  font=("Segoe UI", 10)).pack(side="right")

        tk.Label(bar, text="bt://", bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side="left")

        # ── Content area ─────────────────────────────────────────────────────
        self._content = scrolledtext.ScrolledText(
            self, bg=BG2, fg=FG, state="disabled",
            font=("Segoe UI", 11), wrap="word",
            borderwidth=0, highlightthickness=0, padx=12, pady=12
        )
        self._content.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._content.tag_config("h1", font=("Segoe UI", 18, "bold"),
                                  foreground=ACCENT)
        self._content.tag_config("h2", font=("Segoe UI", 14, "bold"),
                                  foreground=ACCENT)
        self._content.tag_config("h3", font=("Segoe UI", 12, "bold"),
                                  foreground=ACCENT)
        self._content.tag_config("divider", foreground=FG_DIM)
        self._content.tag_config("link",    foreground=GREEN,
                                  underline=True)
        self._content.tag_config("img_alt", foreground=YELLOW,
                                  font=("Segoe UI", 9, "italic"))
        self._content.tag_config("status",  foreground=FG_DIM,
                                  font=("Segoe UI", 9, "italic"))

        # Status bar
        self._status = tk.Label(self, text="Ready", bg=BG2, fg=FG_DIM,
                                 font=("Segoe UI", 9), anchor="w")
        self._status.pack(fill="x", padx=8)

    def _navigate(self, _evt=None):
        url = self._addr_var.get().strip()
        if not url.startswith("bt://"):
            url = "bt://" + url
            self._addr_var.set(url)
        self._load(url)

    def _back(self):
        if len(self._history) > 1:
            self._history.pop()
            url = self._history[-1]
            self._addr_var.set(url)
            self._load(url, record=False)

    def _load(self, url: str, record: bool = True):
        if url == "bt://local/" or url == "bt://local":
            site = self.app.site_store.get_local("/")
            if site:
                self._render(site)
                if record:
                    self._history.append(url)
            return

        addr, path = parse_bt_url(url)
        if not addr:
            self._set_status("Invalid URL", error=True)
            return

        # Check cache first
        cached = self.app.site_store.get_cached(addr, path)
        if cached:
            self._render(cached)
            if record:
                self._history.append(url)
            self._set_status(f"Cached – {url}")
            return

        self._set_status(f"Loading {url} …")
        if record:
            self._history.append(url)

        req_id = self.app.node.request_site(addr, path)

        def timeout_check():
            import time
            time.sleep(15)
            # If we still haven't navigated away, show error
            self._set_status("Request timed out", error=True)

        threading.Thread(target=timeout_check, daemon=True).start()

    def show_site(self, url: str, site: dict):
        """Called when site response arrives."""
        self._render(site)
        self._addr_var.set(url)
        self._set_status(f"Loaded – {url}")

    def _render(self, site: dict):
        self._content.config(state="normal")
        self._content.delete("1.0", "end")

        for sec in site.get("sections", []):
            t = sec.get("type", "")
            if t == "header":
                lvl = sec.get("level", 1)
                tag = f"h{min(lvl, 3)}"
                self._content.insert("end", sec.get("text", "") + "\n\n", tag)
            elif t == "text":
                self._content.insert("end", sec.get("content", "") + "\n\n")
            elif t == "divider":
                self._content.insert("end", "─" * 60 + "\n\n", "divider")
            elif t == "link":
                # Clickable link
                url = sec.get("url", "")
                txt = sec.get("text", url)
                tag_name = f"link_{id(sec)}"
                self._content.tag_config(tag_name, foreground=GREEN,
                                          underline=True)
                self._content.tag_bind(tag_name, "<Button-1>",
                                        lambda e, u=url: self._click_link(u))
                self._content.tag_bind(tag_name, "<Enter>",
                                        lambda e: self._content.config(
                                            cursor="hand2"))
                self._content.tag_bind(tag_name, "<Leave>",
                                        lambda e: self._content.config(
                                            cursor=""))
                self._content.insert("end", f"  ▸ {txt}\n", tag_name)
            elif t == "image":
                self._render_image(sec)
            elif t == "code":
                self._content.insert("end", sec.get("content", "") + "\n\n",
                                      "code")

        updated = site.get("updated")
        if updated:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
            self._content.insert("end", f"\nLast updated: {ts}\n", "status")

        self._content.config(state="disabled")

    def _render_image(self, sec: dict):
        try:
            from PIL import Image, ImageTk
            import io
            from core.protocol import decompress_bytes
            raw = decompress_bytes(sec["data"])
            img = Image.open(io.BytesIO(raw))
            photo = ImageTk.PhotoImage(img)
            self._content.image_create("end", image=photo)
            # Keep a reference to prevent GC
            if not hasattr(self, "_images"):
                self._images = []
            self._images.append(photo)
            self._content.insert("end", "\n")
        except Exception:
            alt = sec.get("alt", "(image)")
            self._content.insert("end", f"[{alt}]\n", "img_alt")

    def _click_link(self, url: str):
        self._addr_var.set(url)
        self._load(url)

    def _set_status(self, msg: str, error: bool = False):
        self._status.config(text=msg, fg=RED if error else FG_DIM)


class PeersTab(ttk.Frame):
    def __init__(self, parent, app: "BlueNetApp"):
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self):
        tk.Label(self, text="Network Peers", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w",
                                                      padx=12, pady=8)

        self._tree = ttk.Treeview(
            self,
            columns=("addr", "name", "hops", "last_seen"),
            show="headings", height=15
        )
        self._tree.heading("addr",      text="Address")
        self._tree.heading("name",      text="Name")
        self._tree.heading("hops",      text="Hops")
        self._tree.heading("last_seen", text="Status")
        self._tree.column("addr",      width=160)
        self._tree.column("name",      width=140)
        self._tree.column("hops",      width=60)
        self._tree.column("last_seen", width=100)
        self._tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=12, pady=(0, 8))

        tk.Button(btn_frame, text="Connect to Address…",
                  command=self._manual_connect,
                  bg=ACCENT, fg="#ffffff", relief="flat",
                  font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
        tk.Button(btn_frame, text="Refresh",
                  command=self._refresh,
                  bg=BG2, fg=FG, relief="flat",
                  font=("Segoe UI", 10)).pack(side="left")

        self._node_info = tk.Label(
            self, text="", bg=BG, fg=FG_DIM, font=("Segoe UI", 9))
        self._node_info.pack(anchor="w", padx=12, pady=(0, 8))

        self._refresh()

    def _refresh(self):
        for row in self._tree.get_children():
            self._tree.delete(row)

        peers = self.app.node.connected_peers()
        routes = self.app.node.routes.snapshot()

        for peer in peers:
            age = int(time.time() - peer.last_seen)
            status = f"Active ({age}s ago)" if age < 30 else f"Idle ({age}s)"
            self._tree.insert("", "end", values=(
                peer.addr, peer.name, 1, status
            ))

        for dst, hops in routes.items():
            # Only show non-direct routes
            if not any(p.addr == dst for p in peers):
                self._tree.insert("", "end", values=(
                    dst, "(relay)", hops, "Via mesh"
                ))

        self._node_info.config(
            text=f"This node: {self.app.node.name}  •  {self.app.node.addr}  "
                 f"•  {len(peers)} direct peers  "
                 f"•  {len(routes)} reachable nodes"
        )
        self.after(10000, self._refresh)

    def _manual_connect(self):
        addr = tk.simpledialog.askstring(
            "Connect", "Enter Bluetooth MAC address (XX:XX:XX:XX:XX:XX):",
            parent=self
        )
        if addr:
            threading.Thread(
                target=self.app.bt.connect_to,
                args=(addr,), daemon=True
            ).start()


class SitesTab(ttk.Frame):
    def __init__(self, parent, app: "BlueNetApp"):
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self):
        tk.Label(self, text="My Sites", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w",
                                                      padx=12, pady=8)
        self._list = tk.Listbox(self, bg=BG2, fg=FG, selectbackground=ACCENT,
                                borderwidth=0, highlightthickness=0,
                                font=("Segoe UI", 11))
        self._list.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=12, pady=(0, 8))
        tk.Button(btn_frame, text="New Site…", command=self._new_site,
                  bg=ACCENT, fg="#ffffff", relief="flat",
                  font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
        tk.Button(btn_frame, text="Edit…", command=self._edit_site,
                  bg=BG2, fg=FG, relief="flat",
                  font=("Segoe UI", 10)).pack(side="left")

        self._refresh()

    def _refresh(self):
        self._list.delete(0, "end")
        for path in self.app.site_store.list_local():
            self._list.insert("end", path)

    def _new_site(self):
        win = tk.Toplevel(self.app.root)
        win.title("New Site")
        win.config(bg=BG)
        win.geometry("500x400")

        tk.Label(win, text="Path (e.g. /mypage):", bg=BG, fg=FG).grid(
            row=0, column=0, padx=8, pady=8, sticky="w")
        path_e = tk.Entry(win, bg=BG2, fg=FG, insertbackground=FG, width=30)
        path_e.grid(row=0, column=1, padx=8, pady=8)

        tk.Label(win, text="Title:", bg=BG, fg=FG).grid(
            row=1, column=0, padx=8, pady=4, sticky="w")
        title_e = tk.Entry(win, bg=BG2, fg=FG, insertbackground=FG, width=30)
        title_e.grid(row=1, column=1, padx=8, pady=4)

        tk.Label(win, text="Content:", bg=BG, fg=FG).grid(
            row=2, column=0, padx=8, pady=4, sticky="nw")
        content_t = scrolledtext.ScrolledText(
            win, bg=BG2, fg=FG, insertbackground=FG,
            font=("Consolas", 10), width=38, height=12)
        content_t.grid(row=2, column=1, padx=8, pady=4)

        def save():
            path    = path_e.get().strip() or "/"
            title   = title_e.get().strip() or "Untitled"
            content = content_t.get("1.0", "end").strip()
            site = make_site(
                title=title,
                author_addr=self.app.node.addr,
                sections=[
                    make_header_section(title),
                    make_text_section(content),
                ]
            )
            self.app.site_store.publish(path, site)
            self._refresh()
            win.destroy()

        tk.Button(win, text="Publish", command=save,
                  bg=ACCENT, fg="#ffffff", relief="flat").grid(
            row=3, column=1, sticky="e", padx=8, pady=8)

    def _edit_site(self):
        sel = self._list.curselection()
        if not sel:
            return
        path = self._list.get(sel[0])
        site = self.app.site_store.get_local(path)
        if not site:
            return
        # Simple JSON editor
        win = tk.Toplevel(self.app.root)
        win.title(f"Edit {path}")
        win.config(bg=BG)
        win.geometry("600x450")

        editor = scrolledtext.ScrolledText(
            win, bg=BG2, fg=FG, insertbackground=FG,
            font=("Consolas", 10))
        editor.pack(fill="both", expand=True, padx=8, pady=8)

        import json
        editor.insert("end", json.dumps(site, indent=2))

        def save():
            try:
                updated = json.loads(editor.get("1.0", "end"))
                self.app.site_store.publish(path, updated)
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        tk.Button(win, text="Save", command=save,
                  bg=ACCENT, fg="#ffffff", relief="flat").pack(pady=(0, 8))


class BlueNetApp:
    def __init__(self, node_name: str = "BlueNetNode"):
        self.root = tk.Tk()
        self.root.title("BlueNet – Bluetooth Mesh Platform")
        self.root.geometry("900x650")
        self.root.config(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        _style(self.root)

        self.node_name = node_name
        self.node: "MeshNode" = None  # type: ignore
        self.bt   = None
        self.msg_store  = MessageStore()
        self.site_store = SiteStore()

        self._ui_queue: queue.Queue = queue.Queue()
        self._build_ui()
        self._start_bt()
        self._poll_queue()

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG, height=48)
        hdr.pack(fill="x")
        tk.Label(hdr, text="BlueNet", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=12)
        self._status_dot = tk.Label(hdr, text="●", bg=BG, fg=RED,
                                     font=("Segoe UI", 14))
        self._status_dot.pack(side="left")
        self._status_lbl = tk.Label(hdr, text="Starting…", bg=BG, fg=FG_DIM,
                                     font=("Segoe UI", 9))
        self._status_lbl.pack(side="left", padx=4)

        self._addr_lbl = tk.Label(hdr, text="", bg=BG, fg=FG_DIM,
                                   font=("Consolas", 9))
        self._addr_lbl.pack(side="right", padx=12)

        # Tabs
        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.chat_tab    = ChatTab(self._nb, self)
        self.browser_tab = BrowserTab(self._nb, self)
        self.peers_tab   = PeersTab(self._nb, self)
        self.sites_tab   = SitesTab(self._nb, self)

        self._nb.add(self.chat_tab,    text="  Chat  ")
        self._nb.add(self.browser_tab, text="  Browser  ")
        self._nb.add(self.peers_tab,   text="  Peers  ")
        self._nb.add(self.sites_tab,   text="  My Sites  ")

    # ── Bluetooth startup ────────────────────────────────────────────────────
    def _start_bt(self):
        def _launch():
            try:
                from windows.bt_adapter import BluetoothAdapterWindows
                bt = BluetoothAdapterWindows(
                    on_peer_connected    = self._on_peer_connected,
                    on_peer_disconnected = self._on_peer_disconnected,
                    on_data_received     = self._on_data,
                )
                bt.start()
                self.bt = bt
                local_addr = bt.local_addr

                node = MeshNode(local_addr=local_addr, name=self.node_name)
                node.on_chat          = self._on_chat
                node.on_chat_ack      = self._on_chat_ack
                node.on_site_request  = self._on_site_request
                node.on_site_response = self._on_site_response
                node.on_peer_change   = self._on_peer_change
                node.on_broadcast     = self._on_broadcast
                self.node = node

                # Publish default home site
                if not self.site_store.get_local("/"):
                    self.site_store.publish(
                        "/", default_home_site(local_addr, self.node_name))

                self._ui_queue.put(("status", "Online", GREEN, local_addr))
            except Exception as e:
                log.error("BT start failed: %s", e)
                self._ui_queue.put(("status", f"BT Error: {e}", RED, ""))

        threading.Thread(target=_launch, daemon=True).start()

    # ── BT callbacks (called from worker threads) ────────────────────────────
    def _on_peer_connected(self, addr, name, send_fn):
        self.node.peer_connected(addr, name, send_fn)

    def _on_peer_disconnected(self, addr):
        self.node.peer_disconnected(addr)

    def _on_data(self, addr, data):
        if self.node:
            self.node.receive_bytes(addr, data)

    def _on_peer_change(self, addr, name, connected):
        self._ui_queue.put(("peer_change", addr, name, connected))

    def _on_chat(self, src, name, text, group):
        self._ui_queue.put(("chat", src, name, text, group))

    def _on_chat_ack(self, src, msg_id):
        self.msg_store.mark_acked(msg_id)

    def _on_broadcast(self, text):
        self._ui_queue.put(("broadcast", text))

    def _on_site_request(self, src, name, path, reply_id):
        return self.site_store.get_local(path)

    def _on_site_response(self, path, site_data):
        self._ui_queue.put(("site_response", path, site_data))

    # ── UI event queue ───────────────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                evt = self._ui_queue.get_nowait()
                self._handle_ui_event(evt)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_ui_event(self, evt):
        kind = evt[0]
        if kind == "status":
            _, msg, color, addr = evt
            self._status_lbl.config(text=msg)
            self._status_dot.config(fg=color)
            if addr:
                self._addr_lbl.config(text=addr)
        elif kind == "chat":
            _, src, name, text, group = evt
            self.msg_store.save(
                f"rx-{time.time()}-{src}", src,
                self.node.addr if self.node else "*",
                text, sent=False, group=group)
            self.chat_tab.append_message(src, text)
        elif kind == "broadcast":
            _, text = evt
            self.chat_tab.append_message("*", text, broadcast=True)
        elif kind == "peer_change":
            _, addr, name, connected = evt
            color = GREEN if connected else RED
            action = "connected" if connected else "disconnected"
            self.chat_tab.append_message(
                "sys", f"Peer {name} ({addr}) {action}",
                broadcast=False)
        elif kind == "site_response":
            _, path, site_data = evt
            # Find which peer/addr this came from – use path context
            self.browser_tab.show_site(path, site_data)

    def _on_close(self):
        if self.bt:
            self.bt.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    ap = argparse.ArgumentParser(description="BlueNet Windows App")
    ap.add_argument("--name", default="BlueNetNode",
                    help="Display name for this node")
    args = ap.parse_args()

    import tkinter.simpledialog
    app = BlueNetApp(node_name=args.name)
    app.run()


if __name__ == "__main__":
    main()
