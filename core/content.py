"""
BlueWeb Content System
Defines the BlueWeb site format, rendering helpers, and local site store.
Sites are small JSON documents with compressed content.
URL scheme: bt://DEVICE_ADDR/path
"""

import os
import json
import time
import zlib
import base64
import sqlite3
import logging
from typing import Optional, List, Dict, Any

from .protocol import compress_bytes, decompress_bytes

log = logging.getLogger("bluenet.content")

BLUEWEB_VERSION = "1.0"

# Section types
class SectionType:
    HEADER  = "header"
    TEXT    = "text"
    IMAGE   = "image"
    LINK    = "link"
    DIVIDER = "divider"
    CODE    = "code"


def make_site(title: str, author_addr: str,
              sections: List[Dict]) -> Dict:
    """Construct a BlueWeb site document."""
    return {
        "blueweb": BLUEWEB_VERSION,
        "title":   title,
        "author":  author_addr,
        "updated": time.time(),
        "sections": sections,
    }


def make_text_section(content: str) -> Dict:
    return {"type": SectionType.TEXT, "content": content}


def make_header_section(text: str, level: int = 1) -> Dict:
    return {"type": SectionType.HEADER, "text": text, "level": level}


def make_link_section(text: str, url: str) -> Dict:
    return {"type": SectionType.LINK, "text": text, "url": url}


def make_divider() -> Dict:
    return {"type": SectionType.DIVIDER}


def make_image_section(image_bytes: bytes, fmt: str = "jpeg",
                        alt: str = "", max_size: tuple = (320, 240)) -> Dict:
    """
    Compress and embed an image into a site section.
    Accepts raw image bytes (JPEG/PNG). Returns section dict.
    """
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail(max_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50, optimize=True)
        image_bytes = buf.getvalue()
        fmt = "jpeg"
    except ImportError:
        pass  # Pillow not available, store raw

    compressed = compress_bytes(image_bytes)
    return {
        "type":   SectionType.IMAGE,
        "alt":    alt,
        "format": fmt,
        "data":   compressed,
    }


def render_site_text(site: Dict) -> str:
    """Render a site to plain text (for terminals / basic display)."""
    lines = [f"=== {site.get('title', 'Untitled')} ===", ""]
    for sec in site.get("sections", []):
        t = sec.get("type")
        if t == SectionType.HEADER:
            prefix = "#" * sec.get("level", 1)
            lines.append(f"{prefix} {sec.get('text', '')}")
        elif t == SectionType.TEXT:
            lines.append(sec.get("content", ""))
        elif t == SectionType.LINK:
            lines.append(f"[{sec.get('text', '')}] -> {sec.get('url', '')}")
        elif t == SectionType.DIVIDER:
            lines.append("─" * 40)
        elif t == SectionType.IMAGE:
            lines.append(f"[Image: {sec.get('alt', '(no alt)')}]")
        elif t == SectionType.CODE:
            lines.append(f"```\n{sec.get('content', '')}\n```")
        lines.append("")
    updated = site.get("updated")
    if updated:
        lines.append(f"Last updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(updated))}")
    return "\n".join(lines)


def parse_bt_url(url: str) -> tuple[str, str]:
    """
    Parse bt://ADDR/path  -> (addr, path)
    Returns ("", "") on parse failure.
    """
    if not url.startswith("bt://"):
        return "", ""
    rest = url[5:]
    parts = rest.split("/", 1)
    addr = parts[0].upper()
    path = "/" + parts[1] if len(parts) > 1 else "/"
    return addr, path


def make_bt_url(addr: str, path: str = "/") -> str:
    path = path if path.startswith("/") else "/" + path
    return f"bt://{addr}{path}"


# ── Local Site Store ─────────────────────────────────────────────────────────
class SiteStore:
    """
    Stores local sites (pages hosted by this device) and a cache
    of pages fetched from remote nodes.
    Uses SQLite for persistence.
    """

    def __init__(self, db_path: str = "bluenet_sites.db"):
        self._db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS local_sites (
                    path     TEXT PRIMARY KEY,
                    data     TEXT NOT NULL,
                    updated  REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS site_cache (
                    addr     TEXT NOT NULL,
                    path     TEXT NOT NULL,
                    data     TEXT NOT NULL,
                    cached   REAL NOT NULL,
                    PRIMARY KEY (addr, path)
                )
            """)

    # ── Local sites (served by this node) ────────────────────────────────────
    def publish(self, path: str, site: Dict):
        """Publish or update a local site page."""
        if not path.startswith("/"):
            path = "/" + path
        with self._conn() as c:
            c.execute("""
                INSERT INTO local_sites (path, data, updated)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET data=excluded.data,
                                                updated=excluded.updated
            """, (path, json.dumps(site), time.time()))
        log.info("Published site at %s", path)

    def get_local(self, path: str) -> Optional[Dict]:
        if not path.startswith("/"):
            path = "/" + path
        with self._conn() as c:
            row = c.execute(
                "SELECT data FROM local_sites WHERE path=?", (path,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_local(self) -> List[str]:
        with self._conn() as c:
            rows = c.execute("SELECT path FROM local_sites ORDER BY path").fetchall()
        return [r["path"] for r in rows]

    # ── Remote cache ─────────────────────────────────────────────────────────
    def cache_remote(self, addr: str, path: str, site: Dict):
        with self._conn() as c:
            c.execute("""
                INSERT INTO site_cache (addr, path, data, cached)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(addr, path) DO UPDATE SET data=excluded.data,
                                                      cached=excluded.cached
            """, (addr.upper(), path, json.dumps(site), time.time()))

    def get_cached(self, addr: str, path: str,
                   max_age: float = 3600.0) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT data, cached FROM site_cache WHERE addr=? AND path=?",
                (addr.upper(), path)
            ).fetchone()
        if row and (time.time() - row["cached"]) < max_age:
            return json.loads(row["data"])
        return None

    def purge_cache(self, max_age: float = 86400.0):
        cutoff = time.time() - max_age
        with self._conn() as c:
            c.execute("DELETE FROM site_cache WHERE cached < ?", (cutoff,))


# ── Default home site ─────────────────────────────────────────────────────────
def default_home_site(node_addr: str, node_name: str) -> Dict:
    return make_site(
        title=f"{node_name}'s BlueNet Node",
        author_addr=node_addr,
        sections=[
            make_header_section(f"Welcome to {node_name}'s node!"),
            make_text_section(
                "This is a BlueNet node — part of a decentralised "
                "Bluetooth mesh network. You can browse sites hosted "
                "by peers, chat, and share content without the internet."
            ),
            make_divider(),
            make_header_section("About BlueNet", level=2),
            make_text_section(
                "BlueNet creates a peer-to-peer mesh network over Bluetooth. "
                "Nodes relay messages automatically, so you can communicate "
                "even when not in direct range of your destination."
            ),
            make_divider(),
            make_link_section("BlueNet Home", "bt://local/"),
        ]
    )
