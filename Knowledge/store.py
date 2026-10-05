from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from uuid import uuid4

from Knowledge.schemas import KnowledgeDraft, KnowledgeEntry


def normalized(text: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKC", text).casefold() if character.isalnum())


def search_tokens(text: str) -> list[str]:
    """Index Chinese character pairs and Latin words with the bundled SQLite FTS5."""
    tokens = []
    for run in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", unicodedata.normalize("NFKC", text).casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", run):
            tokens.extend(run[index:index + 2] for index in range(max(1, len(run) - 1)))
        else:
            tokens.append(run)
    return list(dict.fromkeys(tokens))


class KnowledgeStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS knowledge_entries (
                row_id INTEGER PRIMARY KEY,
                id TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL UNIQUE,
                crop TEXT NOT NULL,
                condition TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                source_recognition_id TEXT NOT NULL,
                deepseek_model TEXT NOT NULL,
                jev_model TEXT NOT NULL
            )""")
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search USING fts5(tokens)")

    def save(self, draft: KnowledgeDraft, recognition_id: str, deepseek_model: str, jev_model: str) -> tuple[str, bool]:
        identity = [normalized(getattr(draft, field)) for field in ("crop", "condition", "question", "applicability")]
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT id FROM knowledge_entries WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if existing:
                return existing["id"], False
            identifier = uuid4().hex
            cursor = connection.execute("""INSERT INTO knowledge_entries
                (id, fingerprint, crop, condition, content, created_at, source_recognition_id, deepseek_model, jev_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identifier, fingerprint, draft.crop, draft.condition, draft.model_dump_json(), time.time(),
                 recognition_id, deepseek_model, jev_model))
            text = " ".join([draft.title, draft.crop, draft.condition, draft.question, draft.answer,
                             draft.applicability, *draft.keywords])
            connection.execute("INSERT INTO knowledge_search (rowid, tokens) VALUES (?, ?)",
                               (cursor.lastrowid, " ".join(search_tokens(text))))
        return identifier, True

    @staticmethod
    def entry(row) -> KnowledgeEntry:
        return KnowledgeEntry(**json.loads(row["content"]), id=row["id"], created_at=row["created_at"])

    def search(self, query: str, limit: int = 5, crop: str | None = None, offset: int = 0) -> list[KnowledgeEntry]:
        tokens = search_tokens(query)[:64]
        if not tokens:
            return []
        expression = " OR ".join('"' + token + '"' for token in tokens)
        sql = """SELECT entries.* FROM knowledge_search
            JOIN knowledge_entries AS entries ON entries.row_id = knowledge_search.rowid
            WHERE knowledge_search MATCH ?"""
        parameters = [expression]
        if crop:
            sql += " AND entries.crop = ?"
            parameters.append(crop)
        sql += " ORDER BY bm25(knowledge_search), entries.created_at DESC, entries.row_id DESC LIMIT ? OFFSET ?"
        parameters.extend([limit, offset])
        with self.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self.entry(row) for row in rows]

    def recent(self, limit: int = 20, offset: int = 0, crop: str | None = None) -> list[KnowledgeEntry]:
        sql = "SELECT * FROM knowledge_entries"
        parameters = []
        if crop:
            sql += " WHERE crop = ?"
            parameters.append(crop)
        sql += " ORDER BY created_at DESC, row_id DESC LIMIT ? OFFSET ?"
        parameters.extend([limit, offset])
        with self.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self.entry(row) for row in rows]

    def get(self, identifier: str) -> KnowledgeEntry | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM knowledge_entries WHERE id = ?", (identifier,)).fetchone()
        return self.entry(row) if row else None
