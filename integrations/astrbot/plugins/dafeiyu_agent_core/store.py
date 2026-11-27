"""SQLite persistence with migrations, audit receipts and hybrid memory search."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


SCHEMA_VERSION = 3
HAN = re.compile(r"[\u3400-\u9fff]")
LATIN_WORD = re.compile(r"[a-z0-9_]+")


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


class BudgetExceeded(RuntimeError):
    pass


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


def _text_terms(text: str) -> list[str]:
    """Dependency-free Chinese character n-grams plus Latin word tokens."""
    normalized = "".join(text.casefold().split())
    terms = LATIN_WORD.findall(normalized)
    chinese = "".join(x for x in normalized if HAN.fullmatch(x))
    terms.extend(chinese)
    for size in (2, 3):
        terms.extend(chinese[i:i + size] for i in range(max(0, len(chinese) - size + 1)))
    return terms


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
                    platform_id TEXT NOT NULL DEFAULT 'qq',
                    bot_id TEXT NOT NULL DEFAULT 'default',
                    conversation_type TEXT NOT NULL DEFAULT 'private',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    access_scope TEXT NOT NULL DEFAULT 'private',
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'explicit_user',
                    confidence REAL NOT NULL DEFAULT 1 CHECK(confidence BETWEEN 0 AND 1),
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
                CREATE TABLE IF NOT EXISTS memory_versions(
                    memory_id TEXT NOT NULL REFERENCES memories(id),
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    changed_at INTEGER NOT NULL,
                    change_reason TEXT NOT NULL,
                    PRIMARY KEY(memory_id, version)
                );
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
                CREATE TABLE IF NOT EXISTS persona_versions(
                    revision INTEGER PRIMARY KEY,
                    mood TEXT NOT NULL,
                    energy REAL NOT NULL CHECK(energy BETWEEN 0 AND 1),
                    interests_json TEXT NOT NULL,
                    traits_json TEXT NOT NULL,
                    changed_at INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    source_receipt_id TEXT
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
                CREATE TABLE IF NOT EXISTS budget_reservations(
                    id TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    tokens_reserved INTEGER NOT NULL,
                    calls_reserved INTEGER NOT NULL,
                    cost_reserved INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','settled','released')),
                    created_at INTEGER NOT NULL,
                    finished_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS budget_reservation_idx
                    ON budget_reservations(day,bucket,status);
                CREATE TABLE IF NOT EXISTS embedding_cache(
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(provider,model,text_hash)
                );
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memories)")}
            additions = {
                "platform_id": "TEXT NOT NULL DEFAULT 'qq'",
                "bot_id": "TEXT NOT NULL DEFAULT 'default'",
                "conversation_type": "TEXT NOT NULL DEFAULT 'private'",
                "conversation_id": "TEXT NOT NULL DEFAULT ''",
                "user_id": "TEXT NOT NULL DEFAULT ''",
                "access_scope": "TEXT NOT NULL DEFAULT 'private'",
                "source": "TEXT NOT NULL DEFAULT 'explicit_user'",
                "confidence": "REAL NOT NULL DEFAULT 1",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE memories ADD COLUMN {name} {declaration}")
            db.execute(
                "UPDATE memories SET "
                "conversation_id=CASE WHEN conversation_id='' THEN scope ELSE conversation_id END,"
                "user_id=CASE WHEN user_id='' THEN owner_id ELSE user_id END "
                "WHERE conversation_id='' OR user_id=''"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS memory_isolation_idx ON memories("
                "platform_id,bot_id,conversation_type,conversation_id,user_id,access_scope,status)"
            )
            db.execute(
                "INSERT OR IGNORE INTO persona_state(id,updated_at) VALUES(1,?)", (_now(),)
            )
            current = db.execute("SELECT * FROM persona_state WHERE id=1").fetchone()
            db.execute(
                "INSERT OR IGNORE INTO persona_versions("
                "revision,mood,energy,interests_json,traits_json,changed_at,reason) "
                "VALUES(?,?,?,?,?,?,?)",
                (current["revision"], current["mood"], current["energy"],
                 current["interests_json"], current["traits_json"],
                 current["updated_at"], "initial state"),
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
        platform_id: str = "qq",
        bot_id: str = "default",
        conversation_type: str | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
        access_scope: str = "private",
        source: str = "explicit_user",
        confidence: float = 1.0,
    ) -> str:
        content = content.strip()
        if not content or len(content) > 8000:
            raise ValueError("memory content length")
        if not scope or not owner_id or not kind:
            raise ValueError("memory scope fields")
        if not 0 <= importance <= 1:
            raise ValueError("importance")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence")
        conversation_type = conversation_type or ("group" if scope.startswith("group:") else "private")
        conversation_id = conversation_id or scope
        user_id = user_id or owner_id
        if not all((platform_id, bot_id, conversation_type, conversation_id, user_id, access_scope, source)):
            raise ValueError("memory isolation fields")
        normalized = self.normalize(content)
        now = _now()
        with self.connect() as db:
            same = db.execute(
                "SELECT id FROM memories WHERE scope=? AND owner_id=? AND normalized=? "
                "AND platform_id=? AND bot_id=? AND conversation_type=? AND conversation_id=? "
                "AND user_id=? AND access_scope=? AND status='active' LIMIT 1",
                (scope, owner_id, normalized, platform_id, bot_id, conversation_type,
                 conversation_id, user_id, access_scope),
            ).fetchone()
            if same:
                db.execute(
                    "UPDATE memories SET importance=max(importance,?), updated_at=? WHERE id=?",
                    (importance, now, same["id"]),
                )
                return str(same["id"])
            if supersedes_id:
                old = db.execute(
                    "SELECT * FROM memories WHERE id=? AND scope=? AND owner_id=? "
                    "AND platform_id=? AND bot_id=? AND conversation_type=? AND conversation_id=? "
                    "AND user_id=? AND status='active'",
                    (supersedes_id, scope, owner_id, platform_id, bot_id, conversation_type,
                     conversation_id, user_id),
                ).fetchone()
                if not old:
                    raise ValueError("superseded memory not found in scope")
                db.execute(
                    "UPDATE memories SET status='superseded',updated_at=? WHERE id=?",
                    (now, supersedes_id),
                )
                version = db.execute(
                    "SELECT coalesce(max(version),0)+1 FROM memory_versions WHERE memory_id=?",
                    (supersedes_id,),
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO memory_versions(memory_id,version,content,kind,importance,confidence,"
                    "status,changed_at,change_reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (supersedes_id, version, old["content"], old["kind"], old["importance"],
                     old["confidence"], "superseded", now, "superseded"),
                )
            memory_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO memories(id,scope,owner_id,platform_id,bot_id,conversation_type,"
                "conversation_id,user_id,access_scope,kind,content,normalized,source,confidence,importance,"
                "embedding,source_receipt_id,supersedes_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    memory_id, scope, owner_id, platform_id, bot_id, conversation_type,
                    conversation_id, user_id, access_scope, kind, content, normalized,
                    source, confidence, importance,
                    _vector_json(embedding),
                    source_receipt_id,
                    supersedes_id,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO memory_versions(memory_id,version,content,kind,importance,confidence,"
                "status,changed_at,change_reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (memory_id, 1, content, kind, importance, confidence, "active", now, "created"),
            )
            return memory_id

    def update_memory(
        self, memory_id: str, *, scope: str, owner_id: str, content: str,
        importance: float | None = None, confidence: float | None = None,
        reason: str = "explicit_update",
    ) -> bool:
        content = content.strip()
        if not content or len(content) > 8000 or not reason.strip():
            raise ValueError("memory update fields")
        if importance is not None and not 0 <= importance <= 1:
            raise ValueError("importance")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence")
        now = _now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM memories WHERE id=? AND scope=? AND owner_id=? AND status='active'",
                (memory_id, scope, owner_id),
            ).fetchone()
            if not row:
                return False
            version = db.execute(
                "SELECT coalesce(max(version),0)+1 FROM memory_versions WHERE memory_id=?",
                (memory_id,),
            ).fetchone()[0]
            new_importance = float(row["importance"] if importance is None else importance)
            new_confidence = float(row["confidence"] if confidence is None else confidence)
            db.execute(
                "UPDATE memories SET content=?,normalized=?,importance=?,confidence=?,updated_at=? WHERE id=?",
                (content, self.normalize(content), new_importance, new_confidence, now, memory_id),
            )
            db.execute(
                "INSERT INTO memory_versions(memory_id,version,content,kind,importance,confidence,"
                "status,changed_at,change_reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (memory_id, version, content, row["kind"], new_importance, new_confidence,
                 "active", now, reason.strip()),
            )
        return True

    def memory_history(self, memory_id: str, *, scope: str, owner_id: str) -> list[dict]:
        with self.connect() as db:
            allowed = db.execute(
                "SELECT 1 FROM memories WHERE id=? AND scope=? AND owner_id=?",
                (memory_id, scope, owner_id),
            ).fetchone()
            if not allowed:
                return []
            rows = db.execute(
                "SELECT version,content,kind,importance,confidence,status,changed_at,change_reason "
                "FROM memory_versions WHERE memory_id=? ORDER BY version", (memory_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def search_memories(
        self,
        *,
        scope: str,
        owner_id: str,
        query: str,
        embedding: Sequence[float] | None = None,
        limit: int = 8,
        platform_id: str = "qq",
        bot_id: str = "default",
        conversation_type: str | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> list[MemoryCandidate]:
        if not 1 <= limit <= 50:
            raise ValueError("limit")
        query_terms = _text_terms(query)
        conversation_type = conversation_type or ("group" if scope.startswith("group:") else "private")
        conversation_id = conversation_id or scope
        user_id = user_id or owner_id
        now = _now()
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM memories WHERE scope=? AND owner_id=? AND platform_id=? AND bot_id=? "
                "AND conversation_type=? AND conversation_id=? AND user_id=? AND status='active' "
                "ORDER BY updated_at DESC LIMIT 1000",
                (scope, owner_id, platform_id, bot_id, conversation_type, conversation_id, user_id),
            ).fetchall()
            documents = [_text_terms(str(row["content"])) for row in rows]
            average_length = sum(map(len, documents)) / max(1, len(documents))
            document_frequency = Counter(
                term for document in documents for term in set(document)
            )
            ranked = []
            for row, document in zip(rows, documents):
                counts = Counter(document)
                bm25 = 0.0
                for term in set(query_terms):
                    frequency = counts.get(term, 0)
                    if not frequency:
                        continue
                    inverse = math.log(
                        1 + (len(rows) - document_frequency[term] + 0.5) /
                        (document_frequency[term] + 0.5)
                    )
                    denominator = frequency + 1.2 * (
                        0.25 + 0.75 * len(document) / max(1.0, average_length)
                    )
                    bm25 += inverse * frequency * 2.2 / denominator
                keyword = bm25 / (bm25 + 3.0) if bm25 else 0.0
                vec = json.loads(row["embedding"]) if row["embedding"] else None
                semantic = max(0.0, _cosine(embedding, vec))
                age_days = max(0.0, (now - int(row["updated_at"])) / 86400)
                recency = math.exp(-age_days / 90)
                score = (0.42 * keyword + 0.35 * semantic + 0.13 * float(row["importance"]) +
                         0.05 * float(row["confidence"]) + 0.05 * recency)
                if query_terms and not keyword and not semantic:
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

    def _set_memory_status(
        self, memory_id: str, *, scope: str, owner_id: str, status: str, reason: str,
        from_statuses: tuple[str, ...],
    ) -> bool:
        if status not in {"active", "archived", "deleted"} or not reason.strip():
            raise ValueError("memory status")
        placeholders = ",".join("?" for _ in from_statuses)
        now = _now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT * FROM memories WHERE id=? AND scope=? AND owner_id=? "
                f"AND status IN ({placeholders})",
                (memory_id, scope, owner_id, *from_statuses),
            ).fetchone()
            if not row:
                return False
            version = db.execute(
                "SELECT coalesce(max(version),0)+1 FROM memory_versions WHERE memory_id=?",
                (memory_id,),
            ).fetchone()[0]
            db.execute(
                "UPDATE memories SET status=?,updated_at=? WHERE id=?",
                (status, now, memory_id),
            )
            db.execute(
                "INSERT INTO memory_versions(memory_id,version,content,kind,importance,confidence,"
                "status,changed_at,change_reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (memory_id, version, row["content"], row["kind"], row["importance"],
                 row["confidence"], status, now, reason.strip()),
            )
        return True

    def forget(self, memory_id: str, *, scope: str, owner_id: str) -> bool:
        return self._set_memory_status(
            memory_id, scope=scope, owner_id=owner_id, status="deleted",
            reason="logical_delete", from_statuses=("active", "archived", "superseded"),
        )

    def archive_memory(self, memory_id: str, *, scope: str, owner_id: str) -> bool:
        return self._set_memory_status(
            memory_id, scope=scope, owner_id=owner_id, status="archived",
            reason="archived", from_statuses=("active",),
        )

    def restore_memory(self, memory_id: str, *, scope: str, owner_id: str) -> bool:
        return self._set_memory_status(
            memory_id, scope=scope, owner_id=owner_id, status="active",
            reason="restored", from_statuses=("archived", "deleted"),
        )

    def export_memories(self, *, scope: str, owner_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,platform_id,bot_id,conversation_type,conversation_id,user_id,"
                "access_scope,kind,content,source,confidence,importance,status,created_at,updated_at "
                "FROM memories WHERE scope=? AND owner_id=? ORDER BY created_at",
                (scope, owner_id),
            ).fetchall()
        exported = []
        for row in rows:
            item = dict(row)
            item["history"] = self.memory_history(row["id"], scope=scope, owner_id=owner_id)
            exported.append(item)
        return exported

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

    def create_tool_receipt(
        self, *, tool_name: str, principal_id: str, risk: str,
        request: dict, activity_id: str | None = None,
    ) -> str:
        if not tool_name or not principal_id or risk not in {"read", "write", "high"}:
            raise ValueError("tool receipt fields")
        canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        receipt_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO tool_receipts(id,activity_id,tool_name,principal_id,risk,status,"
                "request_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (receipt_id, activity_id, tool_name, principal_id, risk, "requested",
                 hashlib.sha256(canonical.encode("utf-8")).hexdigest(), _now()),
            )
        return receipt_id

    def set_tool_receipt_status(
        self, receipt_id: str, status: str, *, result: dict | None = None,
    ) -> bool:
        transitions = {
            "approved": {"requested"}, "denied": {"requested"},
            "running": {"approved"}, "succeeded": {"running"}, "failed": {"running"},
        }
        if status not in transitions:
            raise ValueError("invalid receipt status")
        placeholders = ",".join("?" for _ in transitions[status])
        finished = _now() if status in {"denied", "succeeded", "failed"} else None
        with self.connect() as db:
            changed = db.execute(
                f"UPDATE tool_receipts SET status=?,result_json=?,finished_at=? "
                f"WHERE id=? AND status IN ({placeholders})",
                (status, json.dumps(result or {}, ensure_ascii=False), finished, receipt_id,
                 *transitions[status]),
            ).rowcount
        return bool(changed)

    def recent_tool_receipts(self, limit: int = 30) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("limit")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,activity_id,tool_name,principal_id,risk,status,request_hash,"
                "created_at,finished_at FROM tool_receipts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def recent_activities(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("limit")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,kind,status,reason,input_json,output_json,error_type,"
                "started_at,finished_at,created_at FROM activities "
                "ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def active_memory_count(self) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) AS n FROM memories WHERE status='active'"
            ).fetchone()
        return int(row["n"])

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
            existing = db.execute(
                "SELECT id,receipt_ids_json FROM diary_entries WHERE period=? AND period_key=? "
                "ORDER BY created_at DESC LIMIT 1", (period, period_key)
            ).fetchone()
            if existing:
                old_ids = json.loads(existing["receipt_ids_json"] or "[]")
                merged_ids = list(dict.fromkeys([*old_ids, *ids]))
                db.execute(
                    "UPDATE diary_entries SET body=?,receipt_ids_json=?,created_at=? WHERE id=?",
                    (body.strip(), json.dumps(merged_ids), _now(), existing["id"]),
                )
                return existing["id"]
            entry_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO diary_entries(id,period,period_key,body,receipt_ids_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (entry_id, period, period_key, body.strip(), json.dumps(ids), _now()),
            )
        return entry_id

    def recent_diaries(self, period: str | None = None, limit: int = 7) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit")
        with self.connect() as db:
            if period:
                rows = db.execute(
                    "SELECT id,period,period_key,body,receipt_ids_json,created_at FROM diary_entries "
                    "WHERE period=? ORDER BY created_at DESC LIMIT ?", (period, limit)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,period,period_key,body,receipt_ids_json,created_at FROM diary_entries "
                    "ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

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

    def reserve_budget(
        self, day: str, bucket: str, *, tokens: int, calls: int = 1,
        cost_microunits: int = 0, token_limit: int, call_limit: int,
        cost_limit_microunits: int,
    ) -> str:
        if min(tokens, calls, cost_microunits, token_limit, call_limit, cost_limit_microunits) < 0:
            raise ValueError("negative budget value")
        reservation_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute(
                "SELECT tokens_used,calls_used,cost_microunits FROM budgets WHERE day=? AND bucket=?",
                (day, bucket),
            ).fetchone()
            reserved = db.execute(
                "SELECT coalesce(sum(tokens_reserved),0),coalesce(sum(calls_reserved),0),"
                "coalesce(sum(cost_reserved),0) FROM budget_reservations "
                "WHERE day=? AND bucket=? AND status='active'",
                (day, bucket),
            ).fetchone()
            totals = (
                (int(used[0]) if used else 0) + int(reserved[0]) + tokens,
                (int(used[1]) if used else 0) + int(reserved[1]) + calls,
                (int(used[2]) if used else 0) + int(reserved[2]) + cost_microunits,
            )
            if totals[0] > token_limit or totals[1] > call_limit or totals[2] > cost_limit_microunits:
                raise BudgetExceeded("budget limit would be exceeded")
            db.execute(
                "INSERT INTO budget_reservations(id,day,bucket,tokens_reserved,calls_reserved,"
                "cost_reserved,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (reservation_id, day, bucket, tokens, calls, cost_microunits, "active", _now()),
            )
        return reservation_id

    def settle_budget(
        self, reservation_id: str, *, tokens: int, calls: int = 1,
        cost_microunits: int = 0,
    ) -> bool:
        if min(tokens, calls, cost_microunits) < 0:
            raise ValueError("negative budget settlement")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM budget_reservations WHERE id=? AND status='active'",
                (reservation_id,),
            ).fetchone()
            if not row:
                return False
            # Provider-reported usage is the source of truth. A provider may
            # exceed the pre-call estimate; record that real overrun so future
            # reservations fail closed instead of understating spend.
            db.execute(
                "INSERT INTO budgets(day,bucket,tokens_used,calls_used,cost_microunits) "
                "VALUES(?,?,?,?,?) ON CONFLICT(day,bucket) DO UPDATE SET "
                "tokens_used=tokens_used+excluded.tokens_used,calls_used=calls_used+excluded.calls_used,"
                "cost_microunits=cost_microunits+excluded.cost_microunits",
                (row["day"], row["bucket"], tokens, calls, cost_microunits),
            )
            db.execute(
                "UPDATE budget_reservations SET status='settled',finished_at=? WHERE id=?",
                (_now(), reservation_id),
            )
        return True

    def release_budget(self, reservation_id: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE budget_reservations SET status='released',finished_at=? "
                "WHERE id=? AND status='active'",
                (_now(), reservation_id),
            ).rowcount
        return bool(changed)

    def budget_usage(self, day: str, bucket: str) -> dict[str, int]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM budgets WHERE day=? AND bucket=?", (day, bucket)).fetchone()
        return {"tokens": int(row["tokens_used"]), "calls": int(row["calls_used"]), "cost_microunits": int(row["cost_microunits"])} if row else {"tokens": 0, "calls": 0, "cost_microunits": 0}

    def get_cached_embedding(self, provider: str, model: str, text_hash: str) -> list[float] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT vector_json FROM embedding_cache WHERE provider=? AND model=? AND text_hash=?",
                (provider, model, text_hash),
            ).fetchone()
        return json.loads(row["vector_json"]) if row else None

    def cache_embedding(
        self, provider: str, model: str, text_hash: str, vector: Sequence[float],
    ) -> None:
        encoded = _vector_json(vector)
        assert encoded is not None
        with self.connect() as db:
            db.execute(
                "INSERT INTO embedding_cache(provider,model,text_hash,dimension,vector_json,created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(provider,model,text_hash) DO UPDATE SET "
                "dimension=excluded.dimension,vector_json=excluded.vector_json,created_at=excluded.created_at",
                (provider, model, text_hash, len(vector), encoded, _now()),
            )

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

    def update_persona_state(
        self, *, mood: str | None = None, energy_delta: float = 0,
        interests: Sequence[str] | None = None, traits: dict | None = None,
        reason: str, source_receipt_id: str | None = None,
    ) -> dict:
        """Apply a bounded, versioned persona update backed by real evidence."""
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise ValueError("persona update requires a concise reason")
        if not -0.2 <= energy_delta <= 0.2:
            raise ValueError("energy delta exceeds one-update safety bound")
        if source_receipt_id:
            with self.connect() as check:
                receipt = check.execute(
                    "SELECT id FROM activities WHERE id=? AND status='succeeded'",
                    (source_receipt_id,),
                ).fetchone()
            if not receipt:
                raise ValueError("persona evidence receipt is not successful")
        clean_mood = mood.strip() if mood is not None else None
        if clean_mood is not None and (not clean_mood or len(clean_mood) > 40):
            raise ValueError("invalid mood")
        clean_interests = None
        if interests is not None:
            clean_interests = list(dict.fromkeys(x.strip() for x in interests if x.strip()))
            if len(clean_interests) > 30 or any(len(x) > 80 for x in clean_interests):
                raise ValueError("invalid interests")
        if traits is not None and (not isinstance(traits, dict) or len(traits) > 30):
            raise ValueError("invalid traits")
        now = _now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM persona_state WHERE id=1").fetchone()
            next_revision = int(row["revision"]) + 1
            next_mood = clean_mood if clean_mood is not None else row["mood"]
            next_energy = min(1.0, max(0.0, float(row["energy"]) + energy_delta))
            next_interests = clean_interests if clean_interests is not None else json.loads(row["interests_json"])
            next_traits = traits if traits is not None else json.loads(row["traits_json"])
            interests_json = json.dumps(next_interests, ensure_ascii=False, separators=(",", ":"))
            traits_json = json.dumps(next_traits, ensure_ascii=False, separators=(",", ":"))
            db.execute(
                "UPDATE persona_state SET mood=?,energy=?,interests_json=?,traits_json=?,"
                "revision=?,updated_at=? WHERE id=1",
                (next_mood, next_energy, interests_json, traits_json, next_revision, now),
            )
            db.execute(
                "INSERT INTO persona_versions(revision,mood,energy,interests_json,traits_json,"
                "changed_at,reason,source_receipt_id) VALUES(?,?,?,?,?,?,?,?)",
                (next_revision, next_mood, next_energy, interests_json, traits_json,
                 now, reason, source_receipt_id),
            )
        return self.persona_state()

    def persona_history(self, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("limit")
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM persona_versions ORDER BY revision DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def restore_persona(self, revision: int, *, reason: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT mood,energy,interests_json,traits_json FROM persona_versions WHERE revision=?",
                (revision,),
            ).fetchone()
        if not row:
            raise ValueError("persona revision not found")
        return self.update_persona_state(
            mood=row["mood"], energy_delta=float(row["energy"]) - self.persona_state()["energy"],
            interests=json.loads(row["interests_json"]), traits=json.loads(row["traits_json"]),
            reason=reason,
        )

    def relationship_profile(self, *, subject_id: str, scope: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT profile_json,updated_at FROM relationships WHERE subject_id=? AND scope=?",
                (subject_id, scope),
            ).fetchone()
        if not row:
            return {"subject_id": subject_id, "scope": scope, "profile": {}, "updated_at": None}
        return {"subject_id": subject_id, "scope": scope,
                "profile": json.loads(row["profile_json"]), "updated_at": row["updated_at"]}

    def update_relationship_profile(
        self, *, subject_id: str, scope: str, profile: dict,
    ) -> dict:
        if not subject_id or not scope or not isinstance(profile, dict) or len(profile) > 50:
            raise ValueError("invalid relationship profile")
        encoded = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 20000:
            raise ValueError("relationship profile too large")
        with self.connect() as db:
            db.execute(
                "INSERT INTO relationships(subject_id,scope,profile_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(subject_id,scope) DO UPDATE SET "
                "profile_json=excluded.profile_json,updated_at=excluded.updated_at",
                (subject_id, scope, encoded, _now()),
            )
        return self.relationship_profile(subject_id=subject_id, scope=scope)

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {
                "active_memories": db.execute("SELECT count(*) FROM memories WHERE status='active'").fetchone()[0],
                "successful_activities": db.execute("SELECT count(*) FROM activities WHERE status='succeeded'").fetchone()[0],
                "failed_activities": db.execute("SELECT count(*) FROM activities WHERE status='failed'").fetchone()[0],
                "diary_entries": db.execute("SELECT count(*) FROM diary_entries").fetchone()[0],
                "successful_tool_receipts": db.execute(
                    "SELECT count(*) FROM tool_receipts WHERE status='succeeded'"
                ).fetchone()[0],
                "failed_tool_receipts": db.execute(
                    "SELECT count(*) FROM tool_receipts WHERE status='failed'"
                ).fetchone()[0],
                "denied_tool_receipts": db.execute(
                    "SELECT count(*) FROM tool_receipts WHERE status='denied'"
                ).fetchone()[0],
            }

    def recent_activities(self, limit: int = 30) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("limit")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,kind,status,reason,input_json,output_json,created_at,started_at,finished_at,error_type "
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
