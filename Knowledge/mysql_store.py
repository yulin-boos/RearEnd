import hashlib
import json
from uuid import uuid4

import pymysql

from Common.database import json_text, json_value, timestamp
from Knowledge.schemas import KnowledgeEntry
from Knowledge.store import normalized, search_tokens


def search_text(draft):
    return " ".join([draft.title, draft.crop, draft.condition, draft.question, draft.answer,
                     draft.applicability, *draft.keywords])


def like_pattern(value):
    return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


class MySQLKnowledgeStore:
    def __init__(self, database):
        self.database = database

    def initialize(self):
        pass  # Versioned schema is provisioned by the migration tool, not by the app.

    @staticmethod
    def entry(row):
        return KnowledgeEntry(
            id=row["id"], title=row["title"], crop=row["crop"], condition=row["condition_name"],
            question=row["question"], answer=row["answer"], applicability=row["applicability"],
            uncertainty=row["uncertainty"], keywords=json_value(row["keywords"]),
            created_at=timestamp(row["created_at"]), source_type=row["source_type"],
            expert_verified=bool(row["expert_verified"]),
        )

    def save(self, draft, recognition_id, deepseek_model, jev_model):
        identity = [normalized(getattr(draft, field)) for field in ("crop", "condition", "question", "applicability")]
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        identifier = uuid4().hex
        try:
            with self.database.cursor() as cursor:
                cursor.execute("""INSERT INTO knowledge_entries
                    (id,fingerprint,title,crop,condition_name,question,answer,applicability,uncertainty,
                     keywords,search_text,source_context_id,deepseek_model,jev_model)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (identifier, fingerprint, draft.title, draft.crop, draft.condition, draft.question,
                     draft.answer, draft.applicability, draft.uncertainty, json_text(draft.keywords),
                     search_text(draft), recognition_id, deepseek_model, jev_model))
        except pymysql.IntegrityError as error:
            if error.args[0] != 1062:
                raise
            with self.database.cursor() as cursor:
                cursor.execute("SELECT id FROM knowledge_entries WHERE fingerprint=%s", (fingerprint,))
                existing = cursor.fetchone()
            if existing is None:
                raise
            return existing["id"], False
        return identifier, True

    def search(self, query, limit=5, crop=None, offset=0):
        tokens = search_tokens(query)[:64]
        if not tokens:
            return []
        crop_sql = " AND crop=%s" if crop else ""
        crop_values = [crop] if crop else []
        expression = " ".join(tokens)
        with self.database.cursor() as cursor:
            use_like = any(len(token) < 2 for token in tokens)
            if not use_like:
                cursor.execute("SELECT COUNT(*) AS total FROM knowledge_entries WHERE MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE)" + crop_sql,
                               [expression, *crop_values])
                use_like = cursor.fetchone()["total"] == 0
            if use_like:
                conditions = " OR ".join("search_text LIKE %s ESCAPE '!'" for _ in tokens)
                cursor.execute("SELECT * FROM knowledge_entries WHERE (" + conditions + ")" + crop_sql +
                               " ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",
                               [*(like_pattern(token) for token in tokens), *crop_values, limit, offset])
            else:
                cursor.execute("""SELECT *,MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score
                    FROM knowledge_entries WHERE MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE)""" + crop_sql +
                    " ORDER BY score DESC,created_at DESC,id DESC LIMIT %s OFFSET %s",
                    [expression, expression, *crop_values, limit, offset])
            rows = cursor.fetchall()
        return [self.entry(row) for row in rows]

    def recent(self, limit=20, offset=0, crop=None):
        sql = "SELECT * FROM knowledge_entries" + (" WHERE crop=%s" if crop else "")
        with self.database.cursor() as cursor:
            cursor.execute(sql + " ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",
                           ([crop] if crop else []) + [limit, offset])
            rows = cursor.fetchall()
        return [self.entry(row) for row in rows]

    def get(self, identifier):
        with self.database.cursor() as cursor:
            cursor.execute("SELECT * FROM knowledge_entries WHERE id=%s", (identifier,))
            row = cursor.fetchone()
        return self.entry(row) if row else None
