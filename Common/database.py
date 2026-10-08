"""Bounded MySQL connections shared by all stores; no silent storage fallback."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from queue import Empty, LifoQueue
from threading import BoundedSemaphore

import pymysql

from Common.errors import DatabaseError

TABLES = {
    "users", "auth_sessions", "recognitions", "recognition_predictions", "conversations",
    "chat_messages", "knowledge_entries", "message_knowledge_links", "product_categories",
    "products", "product_variants", "product_images",
}


def utc_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)


def timestamp(value: datetime) -> float:
    return value.replace(tzinfo=timezone.utc).timestamp()


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def json_value(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


class Database:
    def __init__(self, settings):
        self.settings = settings
        self._idle = LifoQueue()
        self._slots = BoundedSemaphore(settings.db_pool_size)
        self._closed = False

    @staticmethod
    def _discard(connection):
        if connection is not None:
            try:
                connection.close()
            except pymysql.MySQLError:
                pass  # The driver can already have closed a stale network socket.

    def _connect(self):
        settings = self.settings
        ssl_options = None
        if settings.db_ssl:
            ssl_options = {"ca": settings.db_ssl_ca, "check_hostname": bool(settings.db_ssl_ca)}
        connection = pymysql.connect(
            host=settings.db_host, port=settings.db_port, user=settings.db_user,
            password=settings.db_password.get_secret_value(), database=settings.db_name,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=settings.db_timeout_seconds, read_timeout=settings.db_timeout_seconds,
            write_timeout=settings.db_timeout_seconds, autocommit=False, ssl=ssl_options,
            init_command="SET time_zone = '+00:00'",
        )
        if settings.db_ssl and not connection._secure:
            connection.close()
            raise DatabaseError()
        return connection

    @contextmanager
    def cursor(self):
        if self._closed or not self._slots.acquire(timeout=self.settings.db_timeout_seconds):
            raise DatabaseError()
        connection = None
        healthy = False
        try:
            try:
                connection = self._idle.get_nowait()
                connection.ping(reconnect=False)
            except Empty:
                connection = self._connect()
            except pymysql.MySQLError:
                self._discard(connection)
                connection = None
                connection = self._connect()
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
            healthy = True
        except pymysql.IntegrityError:
            if connection is not None:
                try:
                    connection.rollback()
                except pymysql.MySQLError:
                    raise DatabaseError() from None
                healthy = True
            raise
        except pymysql.MySQLError:
            raise DatabaseError() from None
        finally:
            if connection is not None:
                if healthy and not self._closed:
                    self._idle.put(connection)
                else:
                    self._discard(connection)
            self._slots.release()

    def verify_schema(self):
        with self.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
                           (self.settings.db_name,))
            present = {row["TABLE_NAME"] for row in cursor.fetchall()}
            if not TABLES.issubset(present):
                raise RuntimeError("MySQL 业务表未初始化，请先执行数据库迁移工具。")

    def check(self):
        with self.cursor() as cursor:
            cursor.execute("SELECT 1")

    def close(self):
        self._closed = True
        while True:
            try:
                self._discard(self._idle.get_nowait())
            except Empty:
                return
