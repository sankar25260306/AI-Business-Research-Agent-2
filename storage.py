"""
core/storage.py
Lightweight SQLite storage that doubles as the caching layer required by
Phase 10 ("Cache previously researched businesses to avoid duplicate work").
No external DB service required - keeps the project easy to reproduce.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from core.config import STORAGE


SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT, category TEXT, location TEXT, created_at REAL
);

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER,
    cache_key TEXT,            -- normalized name+location, used for cache hits
    profile_json TEXT,
    confidence REAL,
    created_at REAL,
    FOREIGN KEY(search_id) REFERENCES searches(id)
);

CREATE INDEX IF NOT EXISTS idx_business_cache_key ON businesses(cache_key);

CREATE TABLE IF NOT EXISTS profile_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    location TEXT,
    cache_key TEXT,
    profile_json TEXT,
    confidence REAL,
    updated_at REAL,
    UNIQUE(category, location, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_profile_cache_scope ON profile_cache(category, location, updated_at);
"""


class Storage:
    def __init__(self):
        Path(STORAGE.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(STORAGE.sqlite_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def save_search(self, query: str, category: str, location: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO searches (query, category, location, created_at) VALUES (?,?,?,?)",
            (query, category, location, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def cache_lookup(self, cache_key: str, max_age_days: int = 30) -> Optional[dict]:
        cutoff = time.time() - max_age_days * 86400
        row = self.conn.execute(
            "SELECT profile_json FROM businesses WHERE cache_key=? AND created_at>? "
            "ORDER BY created_at DESC LIMIT 1",
            (cache_key, cutoff),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def cached_profiles(self, category: str, location: str, max_age_days: int = 30) -> List[dict]:
        cutoff = time.time() - max_age_days * 86400
        rows = self.conn.execute(
            "SELECT profile_json FROM profile_cache "
            "WHERE category=? AND location=? AND updated_at>? "
            "ORDER BY confidence DESC, updated_at DESC",
            (self._norm(category), self._norm(location), cutoff),
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_business(self, search_id: int, cache_key: str, profile: dict, confidence: float):
        self.conn.execute(
            "INSERT INTO businesses (search_id, cache_key, profile_json, confidence, created_at) "
            "VALUES (?,?,?,?,?)",
            (search_id, cache_key, json.dumps(profile), confidence, time.time()),
        )
        self.conn.commit()

    def save_cached_profile(self, category: str, location: str, cache_key: str, profile: dict, confidence: float):
        self.conn.execute(
            "INSERT INTO profile_cache (category, location, cache_key, profile_json, confidence, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(category, location, cache_key) DO UPDATE SET "
            "profile_json=excluded.profile_json, confidence=excluded.confidence, updated_at=excluded.updated_at",
            (
                self._norm(category),
                self._norm(location),
                cache_key,
                json.dumps(profile),
                confidence,
                time.time(),
            ),
        )
        self.conn.commit()

    def all_for_search(self, search_id: int) -> List[dict]:
        rows = self.conn.execute(
            "SELECT profile_json FROM businesses WHERE search_id=?", (search_id,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _norm(self, value: str) -> str:
        return (value or "").strip().lower()