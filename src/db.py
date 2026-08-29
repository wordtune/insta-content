"""حافظه‌ی محلی پست‌ها با SQLite — برای جلوگیری از تکرار و رعایت سقف روزانه."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    published_at  TEXT,
    plan_key      TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    title         TEXT,
    caption       TEXT,
    hashtags      TEXT,
    image_paths   TEXT,
    media_urls    TEXT,
    ig_media_id   TEXT,
    status        TEXT NOT NULL DEFAULT 'draft',
    error         TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_plan_index(conn: sqlite3.Connection, plan_len: int) -> int:
    """اندیس بعدی در تقویم محتوایی (چرخش گردشی)."""
    row = conn.execute("SELECT value FROM meta WHERE key='plan_index'").fetchone()
    idx = int(row["value"]) if row else 0
    return idx % max(plan_len, 1)


def advance_plan_index(conn: sqlite3.Connection, plan_len: int) -> None:
    idx = (next_plan_index(conn, plan_len) + 1) % max(plan_len, 1)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('plan_index', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(idx),),
    )
    conn.commit()


def published_last_24h(conn: sqlite3.Connection) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM posts WHERE status='published' AND published_at >= ?",
        (since,),
    ).fetchone()
    return int(row["c"])


def recent_titles(conn: sqlite3.Connection, limit: int = 30) -> list[str]:
    rows = conn.execute(
        "SELECT title FROM posts WHERE title IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["title"] for r in rows]


def create_draft(conn: sqlite3.Connection, *, plan_key: str, media_type: str,
                 content: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO posts(created_at, plan_key, media_type, title, caption, hashtags, status) "
        "VALUES(?,?,?,?,?,?, 'draft')",
        (
            _now(), plan_key, media_type,
            content.get("title"), content.get("caption"),
            json.dumps(content.get("hashtags", []), ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def attach_media(conn: sqlite3.Connection, post_id: int,
                 image_paths: list[str], media_urls: list[str]) -> None:
    conn.execute(
        "UPDATE posts SET image_paths=?, media_urls=? WHERE id=?",
        (json.dumps(image_paths, ensure_ascii=False),
         json.dumps(media_urls, ensure_ascii=False), post_id),
    )
    conn.commit()


def mark_published(conn: sqlite3.Connection, post_id: int, ig_media_id: str) -> None:
    conn.execute(
        "UPDATE posts SET status='published', published_at=?, ig_media_id=? WHERE id=?",
        (_now(), ig_media_id, post_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, post_id: int, error: str) -> None:
    conn.execute(
        "UPDATE posts SET status='failed', error=? WHERE id=?", (error[:2000], post_id)
    )
    conn.commit()
