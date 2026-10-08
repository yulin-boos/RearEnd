from contextlib import closing, contextmanager
from pathlib import Path
import secrets
import sqlite3
import time
from uuid import uuid4

from Common.errors import UserError
from Users.schemas import LoginRequest, LoginResponse, ProfileUpdate, RegisterRequest, UserProfile
from Users.security import DUMMY_PASSWORD_HASH, hash_password, token_hash, verify_password


class UserStore:
    """Share CHAT_DB_PATH with conversations; store only password and token hashes."""

    def __init__(self, path: Path, session_ttl_seconds: int):
        self.path = path
        self.session_ttl_seconds = session_ttl_seconds

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id)")
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (time.time(),))

    @staticmethod
    def profile(row) -> UserProfile:
        return UserProfile(**{field: row[field] for field in UserProfile.model_fields})

    def _session(self, connection, user: UserProfile) -> LoginResponse:
        now = time.time()
        expires_at = now + self.session_ttl_seconds
        token = secrets.token_urlsafe(32)
        connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        connection.execute("INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                           (token_hash(token), user.id, now, expires_at))
        return LoginResponse(access_token=token, expires_at=expires_at, user=user)

    def register(self, body: RegisterRequest) -> LoginResponse:
        encoded = hash_password(body.password)
        now = time.time()
        identifier = uuid4().hex
        try:
            with self.connection() as connection:
                connection.execute("""INSERT INTO users
                    (id, username, password_hash, nickname, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
                    (identifier, body.username, encoded, body.nickname or body.username[:20], now, now))
                row = connection.execute("SELECT * FROM users WHERE id = ?", (identifier,)).fetchone()
                return self._session(connection, self.profile(row))
        except sqlite3.IntegrityError as error:
            if "users.username" not in str(error):
                raise
            raise UserError(409, "username_taken", "这个账号已被注册，请更换账号。") from None

    def login(self, body: LoginRequest) -> LoginResponse:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
        valid = verify_password(body.password, row["password_hash"] if row else DUMMY_PASSWORD_HASH)
        if row is None or not valid:
            raise UserError(401, "invalid_credentials", "账号或密码不正确。")
        with self.connection() as connection:
            return self._session(connection, self.profile(row))

    def authenticate(self, token: str) -> UserProfile:
        if not 32 <= len(token) <= 128:
            raise UserError(401, "invalid_session", "登录已失效，请重新登录。")
        with self.connection() as connection:
            row = connection.execute("""SELECT users.* FROM auth_sessions
                JOIN users ON users.id = auth_sessions.user_id
                WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?""",
                (token_hash(token), time.time())).fetchone()
        if row is None:
            raise UserError(401, "invalid_session", "登录已失效，请重新登录。")
        return self.profile(row)

    def logout(self, token: str):
        with self.connection() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),))

    def update_profile(self, identifier: str, body: ProfileUpdate) -> UserProfile:
        changes = body.model_dump(exclude_unset=True)
        # The schema permits only these profile fields, never an account ID or role.
        assignments = ", ".join(field + " = ?" for field in changes)
        with self.connection() as connection:
            connection.execute(f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?",
                               (*changes.values(), time.time(), identifier))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise UserError(401, "invalid_session", "登录已失效，请重新登录。")
        return self.profile(row)
