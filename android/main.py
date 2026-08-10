"""
BlueNet Android Application (Kivy)
Full-screen app with screens: Chat, Browser, Peers, Sites.
Built with buildozer for Android deployment.
"""

import os
import sys
import time
import queue
import logging
import threading

# Support both dev (core/ in project root) and packaged APK (core/ alongside main.py)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, ".."))

# Kivy config must happen before importing kivy modules
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

from kivy.app            import App
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.boxlayout  import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label      import Label
from kivy.uix.button     import Button
from kivy.uix.textinput  import TextInput
from kivy.uix.popup      import Popup
from kivy.core.window    import Window
from kivy.metrics        import dp
from kivy.clock          import Clock
from kivy.uix.widget     import Widget
from kivy.graphics       import Color, Rectangle, RoundedRectangle

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.mesh    import MeshNode
from core.content import (SiteStore, default_home_site, render_site_text,
                           parse_bt_url, make_bt_url, make_site,
                           make_header_section, make_text_section,
                           make_divider, make_link_section)
from core.store   import MessageStore

log = logging.getLogger("bluenet.android_app")

# ── Colors ────────────────────────────────────────────────────────────────────
C_BG     = (0.10, 0.10, 0.18, 1)
C_BG2    = (0.09, 0.13, 0.24, 1)
C_ACCENT = (0.12, 0.53, 0.90, 1)
C_FG     = (0.88, 0.88, 0.88, 1)
C_GREEN  = (0.30, 0.69, 0.31, 1)
C_RED    = (0.96, 0.26, 0.21, 1)
C_YELLOW = (1.00, 0.76, 0.03, 1)
C_DIM    = (0.62, 0.62, 0.62, 1)


def _bg(widget, color=C_BG):
    with widget.canvas.before:
        Color(*color)
        rect = Rectangle(size=widget.size, pos=widget.pos)
    widget.bind(size=lambda *a: setattr(rect, "size", widget.size),
                pos=lambda *a: setattr(rect, "pos", widget.pos))


def styled_label(text="", color=C_FG, size=16, bold=False):
    return Label(text=text, color=color,
                 font_size=dp(size), bold=bold,
                 text_size=(None, None), halign="left",
                 valign="middle")


def styled_button(text, bg=C_ACCENT, fg=(1, 1, 1, 1), on_press=None,
                   size_hint_x=None, width=None):
    btn = Button(
        text=text, background_color=bg, color=fg,
        font_size=dp(14), bold=True,
        size_hint_x=size_hint_x, width=width,
        background_normal="",
    )
    if on_press:
        btn.bind(on_press=on_press)
    return btn


def styled_input(hint="", multiline=False, height=dp(44)):
    ti = TextInput(
        hint_text=hint, multiline=multiline,
        background_color=C_BG2, foreground_color=C_FG,
        cursor_color=C_FG, font_size=dp(14),
        size_hint_y=None, height=height,
        padding=[dp(8), dp(8), dp(8), dp(8)],
    )
    return ti


# ── Chat Screen ───────────────────────────────────────────────────────────────
class ChatScreen(Screen):
    def __init__(self, app: "BlueNetApp", **kw):
        super().__init__(**kw)
        self.app = app
        self._current_peer = "*"
        self._build()

    def _build(self):
        root = BoxLayout(orientation="horizontal")
        _bg(root, C_BG)

        # Left sidebar – peer list
        left = BoxLayout(orientation="vertical", size_hint_x=None,
                          width=dp(150))
        _bg(left, C_BG2)
        left.add_widget(
            styled_label("Peers", C_ACCENT, 13, bold=True)
        )
        self._peer_scroll = ScrollView()
        self._peer_list   = BoxLayout(orientation="vertical",
                                       size_hint_y=None)
        self._peer_list.bind(minimum_height=self._peer_list.setter("height"))
        self._peer_scroll.add_widget(self._peer_list)
        left.add_widget(self._peer_scroll)

        bcast_btn = styled_button("Broadcast", C_BG, C_YELLOW,
                                   on_press=lambda *_: self._select("*"))
        left.add_widget(bcast_btn)
        root.add_widget(left)

        # Right – messages + input
        right = BoxLayout(orientation="vertical")
        _bg(right, C_BG)

        self._peer_label = styled_label("Broadcast (*)", C_ACCENT, 13, bold=True)
        right.add_widget(BoxLayout(size_hint_y=None, height=dp(36),
                                    padding=[dp(8), 0]))
        right.children[0].add_widget(self._peer_label)

        self._msg_scroll = ScrollView()
        self._msg_layout = BoxLayout(orientation="vertical", size_hint_y=None,
                                      spacing=dp(4),
                                      padding=[dp(8), dp(4)])
        self._msg_layout.bind(
            minimum_height=self._msg_layout.setter("height"))
        self._msg_scroll.add_widget(self._msg_layout)
        right.add_widget(self._msg_scroll)

        # Input row
        input_row = BoxLayout(size_hint_y=None, height=dp(52),
                               spacing=dp(4), padding=[dp(4), dp(4)])
        _bg(input_row, C_BG2)
        self._text_input = styled_input("Type a message…")
        self._text_input.bind(
            on_text_validate=lambda *_: self._send())
        send_btn = styled_button("Send", on_press=lambda *_: self._send(),
                                  size_hint_x=None, width=dp(80))
        input_row.add_widget(self._text_input)
        input_row.add_widget(send_btn)
        right.add_widget(input_row)

        root.add_widget(right)
        self.add_widget(root)

        Clock.schedule_interval(self._refresh_peers, 5)

    def _refresh_peers(self, *_):
        self._peer_list.clear_widgets()
        if not self.app.node:
            return
        for peer in self.app.node.connected_peers():
            btn = styled_button(
                f"{peer.name[:12]}\n{peer.addr[:8]}…",
                bg=C_BG2, fg=C_FG,
                on_press=lambda *_, a=peer.addr, n=peer.name: self._select(a, n)
            )
            btn.size_hint_y = None
            btn.height = dp(54)
            self._peer_list.add_widget(btn)

    def _select(self, addr: str, name: str = "Broadcast"):
        self._current_peer = addr
        if addr == "*":
            self._peer_label.text = "Broadcast (*)"
            msgs = self.app.msg_store.get_broadcast_history()
        else:
            self._peer_label.text = f"{name} ({addr[:8]}…)"
            msgs = self.app.msg_store.get_conversation(
                self.app.node.addr, addr)
        self._render_msgs(msgs)

    def _render_msgs(self, msgs: list):
        self._msg_layout.clear_widgets()
        for m in msgs:
            self._add_msg_bubble(m["src"], m["text"])
        Clock.schedule_once(lambda *_: setattr(
            self._msg_scroll, "scroll_y", 0), 0.1)

    def _add_msg_bubble(self, src: str, text: str, broadcast=False):
        is_me = src == (self.app.node.addr if self.app.node else "")
        color = C_ACCENT if is_me else (C_YELLOW if broadcast else C_GREEN)
        ts    = time.strftime("%H:%M")
        lbl = Label(
            text=f"[color=#{self._hex(color)}][b]{src[:8]}[/b][/color]"
                 f"  [color=aaaaaa]{ts}[/color]\n{text}",
            markup=True, color=C_FG,
            font_size=dp(13), text_size=(dp(260), None),
            halign="left", valign="top", size_hint_y=None
        )
        lbl.bind(texture_size=lambda i, v: setattr(i, "height", v[1] + dp(8)))
        self._msg_layout.add_widget(lbl)

    @staticmethod
    def _hex(c):
        return "".join(f"{int(v*255):02x}" for v in c[:3])

    def append_message(self, src: str, text: str, broadcast: bool = False):
        """Thread-safe append called from app event queue."""
        self._add_msg_bubble(src, text, broadcast)
        Clock.schedule_once(lambda *_: setattr(
            self._msg_scroll, "scroll_y", 0), 0.1)

    def _send(self):
        text = self._text_input.text.strip()
        if not text or not self.app.node:
            return
        self._text_input.text = ""
        dst = self._current_peer
        if dst == "*":
            self.app.node.send_broadcast(text)
            self.app.msg_store.save(
                f"loc-{time.time()}", self.app.node.addr, "*", text, sent=True)
            self.append_message(self.app.node.addr, text, broadcast=True)
        else:
            msg_id = self.app.node.send_chat(dst, text)
            self.app.msg_store.save(
                msg_id, self.app.node.addr, dst, text, sent=True)
            self.append_message(self.app.node.addr, text)


# ── Browser Screen ────────────────────────────────────────────────────────────
class BrowserScreen(Screen):
    def __init__(self, app: "BlueNetApp", **kw):
        super().__init__(**kw)
        self.app = app
        self._history: list[str] = []
        self._build()

    def _build(self):
        root = BoxLayout(orientation="vertical")
        _bg(root, C_BG)

        # Address bar
        bar = BoxLayout(size_hint_y=None, height=dp(52),
                         spacing=dp(4), padding=[dp(4), dp(4)])
        _bg(bar, C_BG2)
        back_btn = styled_button("◀", C_BG2, C_FG,
                                  on_press=lambda *_: self._back(),
                                  size_hint_x=None, width=dp(44))
        self._addr = styled_input("bt://address/path")
        go_btn = styled_button("Go", on_press=lambda *_: self._navigate(),
                                size_hint_x=None, width=dp(60))
        bar.add_widget(back_btn)
        bar.add_widget(self._addr)
        bar.add_widget(go_btn)
        root.add_widget(bar)

        # Content area
        self._scroll = ScrollView()
        self._content_box = BoxLayout(orientation="vertical",
                                       size_hint_y=None, spacing=dp(6),
                                       padding=[dp(12), dp(12)])
        self._content_box.bind(
            minimum_height=self._content_box.setter("height"))
        self._scroll.add_widget(self._content_box)
        root.add_widget(self._scroll)

        # Status bar
        self._status = styled_label("Ready", C_DIM, 11)
        self._status.size_hint_y = None
        self._status.height = dp(24)
        root.add_widget(self._status)

        self.add_widget(root)
        # Load default home
        Clock.schedule_once(lambda *_: self._load("bt://local/"), 1)

    def _navigate(self):
        url = self._addr.text.strip()
        if not url.startswith("bt://"):
            url = "bt://" + url
            self._addr.text = url
        self._load(url)

    def _back(self):
        if len(self._history) > 1:
            self._history.pop()
            url = self._history[-1]
            self._addr.text = url
            self._load(url, record=False)

    def _load(self, url: str, record: bool = True):
        if record and (not self._history or self._history[-1] != url):
            self._history.append(url)

        if url in ("bt://local/", "bt://local"):
            site = self.app.site_store.get_local("/")
            if site:
                self._render(site)
            return

        addr, path = parse_bt_url(url)
        if not addr:
            self._status.text = "Invalid URL"
            return

        cached = self.app.site_store.get_cached(addr, path)
        if cached:
            self._render(cached)
            self._status.text = f"Cached – {url}"
            return

        self._status.text = f"Loading {url}…"
        if self.app.node:
            self.app.node.request_site(addr, path)

    def show_site(self, url: str, site: dict):
        self._addr.text = url
        self._render(site)
        self._status.text = f"Loaded – {url}"

    def _render(self, site: dict):
        self._content_box.clear_widgets()
        for sec in site.get("sections", []):
            t = sec.get("type", "")
            if t == "header":
                lvl = sec.get("level", 1)
                sz  = {1: 22, 2: 18, 3: 15}.get(lvl, 15)
                lbl = styled_label(sec.get("text", ""), C_ACCENT, sz, bold=True)
                lbl.size_hint_y = None
                lbl.height = dp(sz + 12)
                self._content_box.add_widget(lbl)
            elif t == "text":
                lbl = Label(
                    text=sec.get("content", ""),
                    color=C_FG, font_size=dp(14),
                    text_size=(dp(340), None),
                    halign="left", valign="top", size_hint_y=None
                )
                lbl.bind(texture_size=lambda i, v: setattr(i, "height", v[1]))
                self._content_box.add_widget(lbl)
            elif t == "divider":
                div = Widget(size_hint_y=None, height=dp(1))
                with div.canvas:
                    Color(*C_DIM)
                    Rectangle(size=(dp(340), dp(1)))
                self._content_box.add_widget(div)
            elif t == "link":
                url_target = sec.get("url", "")
                btn = styled_button(
                    f"▸ {sec.get('text', url_target)}",
                    bg=C_BG2, fg=C_GREEN,
                    on_press=lambda *_, u=url_target: self._link_tap(u)
                )
                btn.size_hint_y = None
                btn.height = dp(40)
                self._content_box.add_widget(btn)
            elif t == "image":
                self._render_image(sec)

    def _render_image(self, sec: dict):
        try:
            from kivy.uix.image import CoreImage
            from kivy.uix.image import AsyncImage
            import io
            from core.protocol import decompress_bytes
            raw = decompress_bytes(sec["data"])
            buf = io.BytesIO(raw)
            buf.name = f"img.{sec.get('format', 'jpg')}"
            img = CoreImage(buf, ext=sec.get("format", "jpg"))
            from kivy.uix.image import Image as KivyImage
            kimg = KivyImage(
                texture=img.texture,
                size_hint_y=None,
                height=dp(200),
                allow_stretch=True,
                keep_ratio=True
            )
            self._content_box.add_widget(kimg)
        except Exception as e:
            lbl = styled_label(f"[Image: {sec.get('alt', '?')}]", C_YELLOW, 12)
            lbl.size_hint_y = None
            lbl.height = dp(28)
            self._content_box.add_widget(lbl)

    def _link_tap(self, url: str):
        self._addr.text = url
        self._load(url)


# ── Peers Screen ──────────────────────────────────────────────────────────────
class PeersScreen(Screen):
    def __init__(self, app: "BlueNetApp", **kw):
        super().__init__(**kw)
        self.app = app
        self._build()

    def _build(self):
        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
        _bg(root, C_BG)

        root.add_widget(
            styled_label("Network Peers", C_ACCENT, 17, bold=True))

        self._info = styled_label("", C_DIM, 11)
        self._info.size_hint_y = None
        self._info.height = dp(24)
        root.add_widget(self._info)

        scroll = ScrollView()
        self._peer_layout = BoxLayout(orientation="vertical",
                                       size_hint_y=None, spacing=dp(6))
        self._peer_layout.bind(
            minimum_height=self._peer_layout.setter("height"))
        scroll.add_widget(self._peer_layout)
        root.add_widget(scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        btn_row.add_widget(styled_button(
            "Connect to address…",
            on_press=self._manual_connect))
        btn_row.add_widget(styled_button(
            "Refresh", bg=C_BG2, fg=C_FG,
            on_press=lambda *_: self._refresh()))
        root.add_widget(btn_row)

        self.add_widget(root)
        Clock.schedule_interval(self._refresh, 10)

    def _refresh(self, *_):
        self._peer_layout.clear_widgets()
        if not self.app.node:
            return
        peers  = self.app.node.connected_peers()
        routes = self.app.node.routes.snapshot()
        self._info.text = (
            f"This node: {self.app.node.name}  |  {self.app.node.addr}  |  "
            f"{len(peers)} direct peers  |  {len(routes)} reachable"
        )
        for peer in peers:
            age = int(time.time() - peer.last_seen)
            box = BoxLayout(size_hint_y=None, height=dp(56),
                             padding=[dp(8), dp(4)], spacing=dp(8))
            _bg(box, C_BG2)
            box.add_widget(styled_label(
                f"[b]{peer.name}[/b]\n{peer.addr}  •  1 hop  •  {age}s ago",
                C_FG, 12))
            self._peer_layout.add_widget(box)

        for dst, hops in routes.items():
            if not any(p.addr == dst for p in peers):
                box = BoxLayout(size_hint_y=None, height=dp(44),
                                 padding=[dp(8), dp(4)])
                _bg(box, C_BG2)
                box.add_widget(styled_label(
                    f"{dst}  •  {hops} hops  •  via mesh", C_DIM, 11))
                self._peer_layout.add_widget(box)

    def _manual_connect(self, *_):
        content = BoxLayout(orientation="vertical", spacing=dp(8),
                             padding=dp(12))
        addr_input = styled_input("AA:BB:CC:DD:EE:FF")
        content.add_widget(Label(text="Bluetooth MAC address:",
                                  color=C_FG, font_size=dp(14),
                                  size_hint_y=None, height=dp(32)))
        content.add_widget(addr_input)
        popup = Popup(title="Connect", content=content,
                      size_hint=(0.85, 0.35))

        def do_connect(*_):
            addr = addr_input.text.strip()
            popup.dismiss()
            if addr and self.app.bt:
                threading.Thread(
                    target=self.app.bt.connect_to,
                    args=(addr,), daemon=True
                ).start()

        content.add_widget(styled_button("Connect", on_press=do_connect))
        popup.open()


# ── Sites Screen ──────────────────────────────────────────────────────────────
class SitesScreen(Screen):
    def __init__(self, app: "BlueNetApp", **kw):
        super().__init__(**kw)
        self.app = app
        self._build()

    def _build(self):
        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
        _bg(root, C_BG)
        root.add_widget(styled_label("My Sites", C_ACCENT, 17, bold=True))

        scroll = ScrollView()
        self._list = BoxLayout(orientation="vertical", size_hint_y=None,
                                spacing=dp(6))
        self._list.bind(minimum_height=self._list.setter("height"))
        scroll.add_widget(self._list)
        root.add_widget(scroll)

        root.add_widget(styled_button(
            "+ New Site", on_press=self._new_site))

        self.add_widget(root)
        self._refresh()

    def _refresh(self):
        self._list.clear_widgets()
        for path in self.app.site_store.list_local():
            row = BoxLayout(size_hint_y=None, height=dp(48),
                             padding=[dp(8), dp(4)], spacing=dp(8))
            _bg(row, C_BG2)
            row.add_widget(styled_label(
                f"bt://{self.app.node.addr if self.app.node else 'local'}{path}",
                C_FG, 12))
            self._list.add_widget(row)

    def _new_site(self, *_):
        content = BoxLayout(orientation="vertical", spacing=dp(8),
                             padding=dp(8))
        path_i  = styled_input("/mypage")
        title_i = styled_input("Site title")
        body_i  = styled_input("Site content…", multiline=True,
                                 height=dp(120))
        content.add_widget(
            Label(text="Path:", color=C_FG, font_size=dp(13),
                  size_hint_y=None, height=dp(28)))
        content.add_widget(path_i)
        content.add_widget(
            Label(text="Title:", color=C_FG, font_size=dp(13),
                  size_hint_y=None, height=dp(28)))
        content.add_widget(title_i)
        content.add_widget(
            Label(text="Content:", color=C_FG, font_size=dp(13),
                  size_hint_y=None, height=dp(28)))
        content.add_widget(body_i)
        popup = Popup(title="New Site", content=content,
                      size_hint=(0.9, 0.75))

        def publish(*_):
            path    = path_i.text.strip() or "/"
            title   = title_i.text.strip() or "Untitled"
            body    = body_i.text.strip()
            node_addr = self.app.node.addr if self.app.node else "local"
            site = make_site(
                title=title, author_addr=node_addr,
                sections=[
                    make_header_section(title),
                    make_text_section(body),
                ]
            )
            self.app.site_store.publish(path, site)
            popup.dismiss()
            self._refresh()

        content.add_widget(styled_button("Publish", on_press=publish))
        popup.open()


# ── Bottom Navigation ─────────────────────────────────────────────────────────
class BottomNav(BoxLayout):
    def __init__(self, sm: ScreenManager, **kw):
        super().__init__(size_hint_y=None, height=dp(56),
                          spacing=dp(2), **kw)
        _bg(self, C_BG2)
        screens = [
            ("Chat",    "chat"),
            ("Browser", "browser"),
            ("Peers",   "peers"),
            ("Sites",   "sites"),
        ]
        for label, name in screens:
            btn = Button(
                text=label, background_normal="",
                background_color=C_BG2,
                color=C_FG, font_size=dp(13),
            )
            btn.bind(on_press=lambda *_, n=name, b=btn: self._select(sm, n, b))
            self.add_widget(btn)
        self._btns = self.children[:]

    def _select(self, sm, name, btn):
        sm.current = name
        for b in self.children:
            b.background_color = C_ACCENT if b is btn else C_BG2


# ── Main App ──────────────────────────────────────────────────────────────────
class BlueNetApp(App):
    def __init__(self, node_name: str = "BlueNetNode", **kw):
        super().__init__(**kw)
        self.node_name  = node_name
        self.node: "MeshNode" = None  # type: ignore
        self.bt         = None
        self.msg_store  = MessageStore(
            os.path.join(self._data_dir(), "messages.db"))
        self.site_store = SiteStore(
            os.path.join(self._data_dir(), "sites.db"))
        self._ui_queue: queue.Queue = queue.Queue()

    def _data_dir(self):
        try:
            from android.storage import app_storage_path
            return app_storage_path()
        except ImportError:
            return "."

    def build(self):
        Window.clearcolor = C_BG

        sm = ScreenManager(transition=SlideTransition())

        self.chat_screen    = ChatScreen(self,    name="chat")
        self.browser_screen = BrowserScreen(self, name="browser")
        self.peers_screen   = PeersScreen(self,   name="peers")
        self.sites_screen   = SitesScreen(self,   name="sites")

        sm.add_widget(self.chat_screen)
        sm.add_widget(self.browser_screen)
        sm.add_widget(self.peers_screen)
        sm.add_widget(self.sites_screen)

        root = BoxLayout(orientation="vertical")
        _bg(root, C_BG)

        # Status bar
        self._status_bar = BoxLayout(size_hint_y=None, height=dp(28),
                                      padding=[dp(8), dp(4)])
        _bg(self._status_bar, C_BG2)
        self._status_lbl = styled_label("BlueNet – Starting…", C_DIM, 10)
        self._status_bar.add_widget(self._status_lbl)
        root.add_widget(self._status_bar)

        root.add_widget(sm)
        root.add_widget(BottomNav(sm))

        Clock.schedule_once(self._start_bt, 0.5)
        Clock.schedule_interval(self._process_queue, 0.1)

        return root

    def _start_bt(self, *_):
        def _launch():
            try:
                self._request_bt_permissions()
                from android.bt_adapter import BluetoothAdapterAndroid
                bt = BluetoothAdapterAndroid(
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

                if not self.site_store.get_local("/"):
                    self.site_store.publish(
                        "/", default_home_site(local_addr, self.node_name))

                self._ui_queue.put(("status", f"Online – {local_addr}"))
            except Exception as e:
                log.error("BT start failed: %s", e)
                self._ui_queue.put(("status", f"BT Error: {e}"))

        threading.Thread(target=_launch, daemon=True).start()

    def _request_bt_permissions(self):
        try:
            from android.permissions import (request_permissions,
                                              Permission)
            request_permissions([
                Permission.BLUETOOTH,
                Permission.BLUETOOTH_ADMIN,
                Permission.BLUETOOTH_CONNECT,
                Permission.BLUETOOTH_SCAN,
                Permission.ACCESS_FINE_LOCATION,
            ])
        except ImportError:
            pass

    # ── BT callbacks ─────────────────────────────────────────────────────────
    def _on_peer_connected(self, addr, name, send_fn):
        if self.node:
            self.node.peer_connected(addr, name, send_fn)

    def _on_peer_disconnected(self, addr):
        if self.node:
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

    # ── UI event queue ────────────────────────────────────────────────────────
    def _process_queue(self, *_):
        try:
            while True:
                evt = self._ui_queue.get_nowait()
                self._handle(evt)
        except queue.Empty:
            pass

    def _handle(self, evt):
        kind = evt[0]
        if kind == "status":
            self._status_lbl.text = evt[1]
        elif kind == "chat":
            _, src, name, text, group = evt
            if self.node:
                self.msg_store.save(
                    f"rx-{time.time()}-{src}", src,
                    self.node.addr, text, sent=False, group=group)
            self.chat_screen.append_message(src, text)
        elif kind == "broadcast":
            _, text = evt
            self.chat_screen.append_message("*", text, broadcast=True)
        elif kind == "peer_change":
            _, addr, name, connected = evt
            action = "joined" if connected else "left"
            self.chat_screen.append_message(
                "net", f"{name} ({addr[:8]}…) {action} the mesh",
                broadcast=True)
        elif kind == "site_response":
            _, path, site_data = evt
            self.browser_screen.show_site(path, site_data)


def main():
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(description="BlueNet Android App")
    ap.add_argument("--name", default="BlueNetNode")
    args = ap.parse_args()
    BlueNetApp(node_name=args.name).run()


if __name__ == "__main__":
    main()
