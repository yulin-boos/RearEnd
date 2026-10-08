import secrets
import time
from uuid import uuid4

import pymysql

from Common.database import timestamp, utc_datetime
from Common.errors import UserError
from Users.schemas import LoginResponse, UserProfile
from Users.security import DUMMY_PASSWORD_HASH, hash_password, token_hash, verify_password


class MySQLUserStore:
    def __init__(self, database, session_ttl_seconds):
        self.database = database
        self.session_ttl_seconds = session_ttl_seconds

    def initialize(self):
        with self.database.cursor() as cursor:
            cursor.execute("DELETE FROM auth_sessions WHERE expires_at <= %s", (utc_datetime(time.time()),))

    @staticmethod
    def profile(row):
        fields = {field: row[field] for field in UserProfile.model_fields}
        for field in ("created_at", "updated_at"):
            fields[field] = timestamp(fields[field])
        return UserProfile(**fields)

    def _session(self, cursor, user):
        now = time.time()
        expires_at = now + self.session_ttl_seconds
        token = secrets.token_urlsafe(32)
        cursor.execute("INSERT INTO auth_sessions (token_hash,user_id,created_at,expires_at) VALUES (%s,%s,%s,%s)",
                       (token_hash(token), user.id, utc_datetime(now), utc_datetime(expires_at)))
        return LoginResponse(access_token=token, expires_at=expires_at, user=user)

    def register(self, body):
        self.initialize()
        encoded = hash_password(body.password)
        now = utc_datetime(time.time())
        identifier = uuid4().hex
        try:
            with self.database.cursor() as cursor:
                cursor.execute("""INSERT INTO users
                    (id,username,password_hash,nickname,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                    (identifier, body.username, encoded, body.nickname or body.username[:20], now, now))
                cursor.execute("SELECT * FROM users WHERE id=%s", (identifier,))
                return self._session(cursor, self.profile(cursor.fetchone()))
        except pymysql.IntegrityError as error:
            if error.args[0] != 1062 or "uq_users_username" not in str(error):
                raise
            raise UserError(409, "username_taken", "这个账号已被注册，请更换账号。") from None

    def login(self, body):
        with self.database.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username=%s", (body.username,))
            row = cursor.fetchone()
        valid = verify_password(body.password, row["password_hash"] if row else DUMMY_PASSWORD_HASH)
        if row is None or not valid:
            raise UserError(401, "invalid_credentials", "账号或密码不正确。")
        self.initialize()
        with self.database.cursor() as cursor:
            return self._session(cursor, self.profile(row))

    def authenticate(self, token):
        if not 32 <= len(token) <= 128:
            raise UserError(401, "invalid_session", "登录已失效，请重新登录。")
        with self.database.cursor() as cursor:
            cursor.execute("""SELECT users.* FROM auth_sessions JOIN users ON users.id=auth_sessions.user_id
                WHERE token_hash=%s AND expires_at>%s""", (token_hash(token), utc_datetime(time.time())))
            row = cursor.fetchone()
        if row is None:
            raise UserError(401, "invalid_session", "登录已失效，请重新登录。")
        return self.profile(row)

    def logout(self, token):
        with self.database.cursor() as cursor:
            cursor.execute("DELETE FROM auth_sessions WHERE token_hash=%s", (token_hash(token),))

    def update_profile(self, identifier, body):
        changes = body.model_dump(exclude_unset=True)
        assignments = ",".join(field + "=%s" for field in changes)
        with self.database.cursor() as cursor:
            cursor.execute(f"UPDATE users SET {assignments},updated_at=%s WHERE id=%s",
                           (*changes.values(), utc_datetime(time.time()), identifier))
            cursor.execute("SELECT * FROM users WHERE id=%s", (identifier,))
            row = cursor.fetchone()
        if row is None:
            raise UserError(401, "invalid_session", "登录已失效，请重新登录。")
        return self.profile(row)
