from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import time

from Common.errors import ChatError


class ChatStore:
    """Persist trusted recognition context and complete user/assistant turns."""

    def __init__(self, path: Path, ttl_seconds: int, history_turns: int):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.history_turns = history_turns

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS conversations (
                recognition_id TEXT PRIMARY KEY,
                context TEXT NOT NULL,
                history TEXT NOT NULL DEFAULT '[]',
                revision INTEGER NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL
            )""")
            connection.execute("DELETE FROM conversations WHERE expires_at <= ?", (time.time(),))

    def create(self, recognition_id: str, context: dict):
        with self.connection() as connection:
            connection.execute("DELETE FROM conversations WHERE expires_at <= ?", (time.time(),))
            connection.execute(
                "INSERT INTO conversations (recognition_id, context, expires_at) VALUES (?, ?, ?)",
                (recognition_id, json.dumps(context, ensure_ascii=False), time.time() + self.ttl_seconds),
            )

    def get(self, recognition_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE recognition_id = ?", (recognition_id,)).fetchone()
        if row is None or row["expires_at"] <= time.time():
            raise ChatError(404, "conversation_not_found", "识别记录不存在或对话已过期，请重新上传叶片图片。")
        return {"context": json.loads(row["context"]), "history": json.loads(row["history"]), "revision": row["revision"]}

    def append_turn(self, recognition_id: str, revision: int, question: str, reply: str) -> int:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM conversations WHERE recognition_id = ?", (recognition_id,)).fetchone()
            if row is None or row["expires_at"] <= time.time():
                raise ChatError(404, "conversation_not_found", "对话已过期，请重新上传叶片图片。")
            if row["revision"] != revision:
                raise ChatError(409, "conversation_changed", "这段对话已更新，请稍后重新发送。")
            history = json.loads(row["history"])
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": reply}])
            history = history[-self.history_turns * 2:]
            connection.execute(
                "UPDATE conversations SET history = ?, revision = revision + 1, expires_at = ? WHERE recognition_id = ?",
                (json.dumps(history, ensure_ascii=False), time.time() + self.ttl_seconds, recognition_id),
            )
        return len(history)
