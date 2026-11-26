"""SQLite persistence with migrations, audit receipts and hybrid memory search."""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MemoryCandidate:
    id: str
    scope: str
    owner_id: str
    kind: str
    content: str
    importance: float
    score: float
    created_at: int
    status: str


def _now() -> int:
    return int(time.time())


def _vector_json(vector: Sequence[float] | None) -> str | None:
    if vector is None:
        return None
    values = [float(x) for x in vector]
    if not values or len(values) > 4096 or any(not math.isfinite(x) for x in values):
        raise ValueError("invalid embedding vector")
    return json.dumps(values, separators=(",", ":"))


def _cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class AgentStore:
    """One database per bot instance; callers must provide explicit scopes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    def _migrate(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories(
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized TEXT NOT NULL,
                    importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
                    embedding TEXT,
                    source_receipt_id TEXT,
                    supersedes_id TEXT REFERENCES memories(id),
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','superseded','archived','deleted')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_recalled_at INTEGER,
                    recall_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS memory_scope_idx
                    ON memories(scope, owner_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS relationships(
                    subject_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(subject_id, scope)
                );
                CREATE TABLE IF NOT EXISTS persona_state(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    mood TEXT NOT NULL DEFAULT '平静',
                    energy REAL NOT NULL DEFAULT .7 CHECK(energy BETWEEN 0 AND 1),
                    interests_json TEXT NOT NULL DEFAULT '[]',
                    traits_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activities(
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('planned','running','succeeded','failed','cancelled','skipped')),
                    reason TEXT NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT,
                    error_type TEXT,
                    started_at INTEGER,
                    finished_at INTEGER,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_receipts(
                    id TEXT PRIMARY KEY,
                    activity_id TEXT REFERENCES activities(id),
                    tool_name TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('requested','approved','denied','running','succeeded','failed')),
                    request_hash TEXT NOT NULL,
                    result_json TEXT,
                    created_at INTEGER NOT NULL,
                    finished_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS diary_entries(
                    id TEXT PRIMARY KEY,
                    period TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    body TEXT NOT NULL,
                    receipt_ids_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(period, period_key)
                );
                CREATE TABLE IF NOT EXISTS budgets(
                    day TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    calls_used INTEGER NOT NULL DEFAULT 0,
                    cost_microunits INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, bucket)
                );
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO persona_state(id,updated_at) VALUES(1,?)", (_now(),)
            )
            db.execute(
                "INSERT INTO schema_meta(key,value) VALUES('version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.casefold().split())

    def remember(
        self,
        *,
        scope: str,
        owner_id: str,
        kind: str,
        content: str,
        importance: float = 0.5,
        embedding: Sequence[float] | None = None,
        source_receipt_id: str | None = None,
        supersedes_id: str | None = None,
    ) -> str:
        content = content.strip()
        if not content or len(content) > 8000:
            raise ValueError("memory content length")
        if not scope or not owner_id or not kind:
            raise ValueError("memory scope fields")
        if not 0 <= importance <= 1:
            raise ValueError("importance")
        normalized = self.normalize(content)
        now = _now()
        with self.connect() as db:
            same = db.execute(
                "SELECT id FROM memories WHERE scope=? AND owner_id=? AND normalized=? "
                "AND status='active' LIMIT 1",
                (scope, owner_id, normalized),
            ).fetchone()
            if same:
                db.execute(
                    "UPDATE memories SET importance=max(importance,?), updated_at=? WHERE id=?",
                    (importance, now, same["id"]),
                )
                return str(same["id"])
            if supersedes_id:
                old = db.execute(
                    "SELECT id FROM memories WHERE id=? AND scope=? AND owner_id=? AND status='active'",
                    (supersedes_id, scope, owner_id),
                ).fetchone()
                if not old:
                    raise ValueError("superseded memory not found in scope")
                db.execute(
                    "UPDATE memories SET status='superseded',updated_at=? WHERE id=?",
                    (now, supersedes_id),
                )
            memory_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO memories(id,scope,owner_id,kind,content,normalized,importance,"
                "embedding,source_receipt_id,supersedes_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    scope,
                    owner_id,
                    kind,
                    content,
                    normalized,
                    importance,
                    _vector_json(embedding),
                    source_receipt_id,
                    supersedes_id,
                    now,
                    now,
                ),
            )
            return memory_id

    def search_memories(
        self,
        *,
        scope: str,
        owner_id: str,
        query: str,
        embedding: Sequence[float] | None = None,
        limit: int = 8,
    ) -> list[MemoryCandidate]:
        if not 1 <= limit <= 50:
            raise ValueError("limit")
        terms = set(self.normalize(query).split())
        now = _now()
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM memories WHERE scope=? AND owner_id=? AND status='active' "
                "ORDER BY updated_at DESC LIMIT 1000",
                (scope, owner_id),
            ).fetchall()
            ranked = []
            for row in rows:
                words = set(str(row["normalized"]).split())
                keyword = len(terms & words) / max(1, len(terms))
                vec = json.loads(row["embedding"]) if row["embedding"] else None
                semantic = max(0.0, _cosine(embedding, vec))
                age_days = max(0.0, (now - int(row["updated_at"])) / 86400)
                recency = math.exp(-age_days / 90)
                score = 0.45 * keyword + 0.35 * semantic + 0.15 * float(row["importance"]) + 0.05 * recency
                if terms and not keyword and not semantic:
                    continue
                ranked.append((score, row))
            ranked.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
            picked = ranked[:limit]
            if picked:
                ids = [row["id"] for _, row in picked]
                db.executemany(
                    "UPDATE memories SET recall_count=recall_count+1,last_recalled_at=? WHERE id=?",
                    [(now, x) for x in ids],
                )
            return [
                MemoryCandidate(
                    id=row["id"], scope=row["scope"], owner_id=row["owner_id"],
                    kind=row["kind"], content=row["content"], importance=row["importance"],
                    score=score, created_at=row["created_at"], status=row["status"],
                )
                for score, row in picked
            ]

    def forget(self, memory_id: str, *, scope: str, owner_id: str) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE memories SET status='deleted',updated_at=? "
                "WHERE id=? AND scope=? AND owner_id=? AND status!='deleted'",
                (_now(), memory_id, scope, owner_id),
            ).rowcount
        return bool(changed)

    def create_activity(self, kind: str, reason: str, payload: dict | None = None) -> str:
        if not kind or not reason.strip():
            raise ValueError("activity fields")
        activity_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO activities(id,kind,status,reason,input_json,created_at) VALUES(?,?,?,?,?,?)",
                (activity_id, kind, "planned", reason.strip(), json.dumps(payload or {}, ensure_ascii=False), _now()),
            )
        return activity_id

    def start_activity(self, activity_id: str) -> bool:
        with self.connect() as db:
            return bool(db.execute(
                "UPDATE activities SET status='running',started_at=? WHERE id=? AND status='planned'",
                (_now(), activity_id),
            ).rowcount)

    def finish_activity(
        self, activity_id: str, *, succeeded: bool, output: dict | None = None,
        error_type: str | None = None,
    ) -> bool:
        status = "succeeded" if succeeded else "failed"
        with self.connect() as db:
            return bool(db.execute(
                "UPDATE activities SET status=?,output_json=?,error_type=?,finished_at=? "
                "WHERE id=? AND status='running'",
                (status, json.dumps(output or {}, ensure_ascii=False), error_type, _now(), activity_id),
            ).rowcount)

    def successful_activity_ids(self, start_at: int, end_at: int) -> list[str]:
        with self.connect() as db:
            return [r["id"] for r in db.execute(
                "SELECT id FROM activities WHERE status='succeeded' AND finished_at BETWEEN ? AND ? "
                "ORDER BY finished_at", (start_at, end_at)
            )]

    def save_diary(self, period: str, period_key: str, body: str, receipt_ids: Iterable[str]) -> str:
        ids = list(dict.fromkeys(receipt_ids))
        if not ids:
            raise ValueError("diary requires real activity receipts")
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as db:
            found = {r["id"] for r in db.execute(
                f"SELECT id FROM activities WHERE status='succeeded' AND id IN ({placeholders})", ids
            )}
            if found != set(ids):
                raise ValueError("diary contains unverified activity")
            entry_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO diary_entries(id,period,period_key,body,receipt_ids_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (entry_id, period, period_key, body.strip(), json.dumps(ids), _now()),
            )
        return entry_id

    def charge_budget(self, day: str, bucket: str, *, tokens: int, calls: int = 1, cost_microunits: int = 0) -> None:
        if min(tokens, calls, cost_microunits) < 0:
            raise ValueError("negative budget charge")
        with self.connect() as db:
            db.execute(
                "INSERT INTO budgets(day,bucket,tokens_used,calls_used,cost_microunits) VALUES(?,?,?,?,?) "
                "ON CONFLICT(day,bucket) DO UPDATE SET tokens_used=tokens_used+excluded.tokens_used,"
                "calls_used=calls_used+excluded.calls_used,cost_microunits=cost_microunits+excluded.cost_microunits",
                (day, bucket, tokens, calls, cost_microunits),
            )

    def budget_usage(self, day: str, bucket: str) -> dict[str, int]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM budgets WHERE day=? AND bucket=?", (day, bucket)).fetchone()
        return {"tokens": int(row["tokens_used"]), "calls": int(row["calls_used"]), "cost_microunits": int(row["cost_microunits"])} if row else {"tokens": 0, "calls": 0, "cost_microunits": 0}

    def set_setting(self, key: str, value) -> None:
        if not key or len(key) > 120:
            raise ValueError("setting key")
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), _now()),
            )

    def get_setting(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def persona_state(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM persona_state WHERE id=1").fetchone()
        return {
            "mood": row["mood"], "energy": float(row["energy"]),
            "interests": json.loads(row["interests_json"]),
            "traits": json.loads(row["traits_json"]),
            "revision": int(row["revision"]), "updated_at": int(row["updated_at"]),
        }

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {
                "active_memories": db.execute("SELECT count(*) FROM memories WHERE status='active'").fetchone()[0],
                "successful_activities": db.execute("SELECT count(*) FROM activities WHERE status='succeeded'").fetchone()[0],
                "failed_activities": db.execute("SELECT count(*) FROM activities WHERE status='failed'").fetchone()[0],
                "diary_entries": db.execute("SELECT count(*) FROM diary_entries").fetchone()[0],
            }

    def recent_activities(self, limit: int = 30) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("limit")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,kind,status,reason,created_at,started_at,finished_at,error_type "
                "FROM activities ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_memories(self, *, status: str = "active", limit: int = 50) -> list[dict]:
        if status not in {"active", "superseded", "archived", "deleted"} or not 1 <= limit <= 200:
            raise ValueError("memory list filters")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,scope,owner_id,kind,content,importance,status,created_at,updated_at,recall_count "
                "FROM memories WHERE status=? ORDER BY updated_at DESC LIMIT ?", (status, limit)
            ).fetchall()
        return [dict(row) for row in rows]
