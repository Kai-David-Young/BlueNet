"""
BlueNet Message Store
Persists chat history in SQLite.
"""

import sqlite3
import time
import logging
from typing import List, Dict, Optional

log = logging.getLogger("bluenet.store")


class MessageStore:
    def __init__(self, db_path: str = "bluenet_messages.db"):
        self._db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id        TEXT PRIMARY KEY,
                    src       TEXT NOT NULL,
                    dst       TEXT NOT NULL,
                    grp       TEXT,
                    text      TEXT NOT NULL,
                    ts        REAL NOT NULL,
                    sent      INTEGER NOT NULL DEFAULT 0,
                    acked     INTEGER NOT NULL DEFAULT 0
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_ts ON messages(ts)
            """)

    def save(self, msg_id: str, src: str, dst: str, text: str,
             sent: bool = False, group: Optional[str] = None):
        with self._conn() as c:
            c.execute("""
                INSERT OR IGNORE INTO messages
                    (id, src, dst, grp, text, ts, sent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (msg_id, src, dst, group, text, time.time(), int(sent)))

    def mark_acked(self, msg_id: str):
        with self._conn() as c:
            c.execute("UPDATE messages SET acked=1 WHERE id=?", (msg_id,))

    def get_conversation(self, local_addr: str, peer_addr: str,
                         limit: int = 100) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT * FROM messages
                WHERE (src=? AND dst=?) OR (src=? AND dst=?)
                ORDER BY ts DESC LIMIT ?
            """, (local_addr, peer_addr, peer_addr, local_addr, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_broadcast_history(self, limit: int = 50) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT * FROM messages WHERE dst='*'
                ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_known_peers(self, local_addr: str) -> List[str]:
        """Return all addresses we've exchanged messages with."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT DISTINCT
                    CASE WHEN src=? THEN dst ELSE src END AS peer
                FROM messages
                WHERE (src=? OR dst=?) AND dst != '*'
                ORDER BY ts DESC
            """, (local_addr, local_addr, local_addr)).fetchall()
        return [r["peer"] for r in rows if r["peer"] != local_addr]
