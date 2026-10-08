import time

from Common.database import json_text, json_value, utc_datetime
from Common.errors import ChatError


class MySQLChatStore:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings
        self.ttl_seconds = settings.chat_session_ttl_seconds
        self.history_turns = settings.chat_history_turns

    def initialize(self):
        self.cleanup()

    def cleanup(self):
        # Lock the same parent row as append_turn, so renewal and expiry cannot race.
        with self.database.cursor() as cursor:
            cursor.execute("SELECT id,recognition_id FROM conversations WHERE expires_at<=%s FOR UPDATE",
                           (utc_datetime(time.time()),))
            expired = cursor.fetchall()
            if not expired:
                return
            cursor.executemany("DELETE FROM conversations WHERE id=%s", [(row["id"],) for row in expired])
            identifiers = [(row["recognition_id"],) for row in expired if row["recognition_id"]]
            if identifiers:
                cursor.executemany("DELETE FROM recognitions WHERE id=%s", identifiers)

    def _create(self, cursor, identifier, context, user_id, recognition_id=None):
        now = time.time()
        kind = "general" if context.get("conversation_type") == "general" else "recognition"
        title = "智能病虫害问答" if kind == "general" else "叶片识别咨询"
        cursor.execute("""INSERT INTO conversations
            (id,user_id,recognition_id,kind,title,context_snapshot,expires_at,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (identifier, user_id, recognition_id, kind, title, json_text(context),
             utc_datetime(now + self.ttl_seconds), utc_datetime(now), utc_datetime(now)))

    def create(self, identifier, context, user_id=None):
        self.cleanup()
        with self.database.cursor() as cursor:
            self._create(cursor, identifier, context, user_id)

    def create_recognition(self, result, candidates, context, user_id=None, requested_top_k=None):
        self.cleanup()
        now = time.time()
        image_path = f"{result.request_id}.jpg" if result.result_image_url else None
        with self.database.cursor() as cursor:
            cursor.execute("""INSERT INTO recognitions
                (id,user_id,filename,image_width,image_height,model_name,leaf_check,postprocessor,
                 confidence_threshold,requested_top_k,status,inference_ms,elapsed_ms,result_image_path,
                 result_image_expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (result.request_id, user_id, result.image.filename, result.image.width, result.image.height,
                 result.model, json_text(result.leaf_check.model_dump()), result.postprocessor,
                 result.confidence_threshold, requested_top_k or len(result.predictions), result.status,
                 result.inference_ms, result.elapsed_ms, image_path,
                 utc_datetime(now + self.settings.result_ttl_seconds) if image_path else None, utc_datetime(now)))
            cursor.executemany("""INSERT INTO recognition_predictions
                (recognition_id,rank_no,class_id,class_name,display_name,crop,condition_name,is_healthy,confidence)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(result.request_id, rank, item.class_id, item.class_name, item.display_name, item.crop,
                  item.condition, item.is_healthy, item.confidence) for rank, item in enumerate(candidates, 1)])
            self._create(cursor, result.request_id, context, user_id, result.request_id)

    @staticmethod
    def _authorize(row, user_id):
        if row is None or row["expires_at"] <= utc_datetime(time.time()) or (row["user_id"] is not None and row["user_id"] != user_id):
            raise ChatError(404, "conversation_not_found", "识别记录不存在或对话已过期，请重新上传叶片图片。")

    def get(self, identifier, user_id=None):
        with self.database.cursor() as cursor:
            cursor.execute("SELECT * FROM conversations WHERE id=%s", (identifier,))
            row = cursor.fetchone()
            self._authorize(row, user_id)
            cursor.execute("""SELECT role,content FROM chat_messages WHERE conversation_id=%s
                ORDER BY turn_no DESC,id DESC LIMIT %s""", (identifier, self.history_turns * 2))
            history = list(reversed(cursor.fetchall()))
        return {"context": json_value(row["context_snapshot"]), "history": history, "revision": row["revision"]}

    def result_image_available(self, identifier, user_id=None):
        with self.database.cursor() as cursor:
            cursor.execute("""SELECT c.user_id,c.expires_at,r.result_image_path,r.result_image_expires_at
                FROM conversations c JOIN recognitions r ON r.id=c.recognition_id WHERE c.id=%s""", (identifier,))
            row = cursor.fetchone()
            self._authorize(row, user_id)
        return bool(row["result_image_path"] == f"{identifier}.jpg" and row["result_image_expires_at"]
                    and row["result_image_expires_at"] > utc_datetime(time.time()))

    def append_turn(self, identifier, revision, question, reply, user_id=None, metadata=None):
        metadata = metadata or {}
        now = utc_datetime(time.time())
        with self.database.cursor() as cursor:
            cursor.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (identifier,))
            row = cursor.fetchone()
            self._authorize(row, user_id)
            if row["revision"] != revision:
                raise ChatError(409, "conversation_changed", "这段对话已更新，请稍后重新发送。")
            turn_no = revision + 1
            cursor.execute("INSERT INTO chat_messages (conversation_id,turn_no,role,content,created_at) VALUES (%s,%s,'user',%s,%s)",
                           (identifier, turn_no, question, now))
            cursor.execute("""INSERT INTO chat_messages
                (conversation_id,turn_no,role,content,reply_source,model_name,elapsed_ms,usage_json,guard_checks,truncated,created_at)
                VALUES (%s,%s,'assistant',%s,%s,%s,%s,%s,%s,%s,%s)""",
                (identifier, turn_no, reply, metadata.get("source", "deepseek"), metadata.get("model"),
                 metadata.get("elapsed_ms"), json_text(metadata.get("usage", {})),
                 json_text(metadata.get("guard")), metadata.get("truncated", False), now))
            message_id = cursor.lastrowid
            for knowledge_id in set(metadata.get("source_ids", [])):
                relation = "direct" if metadata.get("source") == "knowledge" else "reference"
                cursor.execute("""INSERT INTO message_knowledge_links
                    (message_id,knowledge_id,relation_type,match_probability) VALUES (%s,%s,%s,%s)""",
                    (message_id, knowledge_id, relation, metadata.get("reuse_probability")))
            cursor.execute("UPDATE conversations SET revision=%s,expires_at=%s,updated_at=%s WHERE id=%s",
                           (turn_no, utc_datetime(time.time() + self.ttl_seconds), now, identifier))
            cursor.execute("SELECT COUNT(*) AS total FROM chat_messages WHERE conversation_id=%s", (identifier,))
            total = cursor.fetchone()["total"]
        return min(total, self.history_turns * 2)

    def finish_turn(self, identifier, revision, activity):
        with self.database.cursor() as cursor:
            cursor.execute("""SELECT id FROM chat_messages WHERE conversation_id=%s AND turn_no=%s
                AND role='assistant' FOR UPDATE""", (identifier, revision + 1))
            row = cursor.fetchone()
            if row is None:  # A concurrent expiry cleanup may already have removed this turn.
                return
            cursor.execute("UPDATE chat_messages SET knowledge_activity=%s WHERE id=%s",
                           (json_text(activity.model_dump()), row["id"]))
            if activity.status == "stored" and activity.entry_id:
                cursor.execute("""INSERT INTO message_knowledge_links (message_id,knowledge_id,relation_type)
                    VALUES (%s,%s,'source')""", (row["id"], activity.entry_id))
