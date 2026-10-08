import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Existing environment and relative paths remain based on Visual.
ROOT = PROJECT_ROOT / "Visual"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    model_path: Path = ROOT.parent / "model" / "best.pt"
    leaf_model_path: Path = ROOT.parent / "model" / "clip_leaf_gate"
    leaf_device: str = "cpu"
    leaf_min_similarity: float = Field(default=0.24, ge=0, le=1)
    leaf_min_margin: float = Field(default=0.02, ge=0, le=1)
    device: str = "cpu"
    image_size: int = Field(default=224, ge=32, le=2048)
    default_confidence: float = Field(default=0.5, ge=0, le=1)
    default_top_k: int = Field(default=5, ge=1, le=50)
    max_upload_mb: int = Field(default=10, ge=1, le=100)
    max_image_pixels: int = Field(default=20_000_000, ge=1)
    result_dir: Path = ROOT / "storage" / "results"
    result_ttl_seconds: int = Field(default=86400, ge=1)
    require_native: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = []
    product_data_dir: Path = PROJECT_ROOT / "Shop" / "data"
    database_backend: Literal["sqlite", "mysql"] = "sqlite"
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str = Field(default="plant_health", pattern=r"^[a-zA-Z0-9_]+$")
    db_user: str = "plant_health_app"
    db_password: SecretStr | None = Field(default=None, repr=False, exclude=True)
    db_pool_size: int = Field(default=6, ge=1, le=32)
    db_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_ssl: bool = True
    db_ssl_ca: str | None = None
    deepseek_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    deepseek_base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-flash", min_length=1, max_length=100)
    deepseek_timeout_seconds: float = Field(default=60, ge=1, le=300)
    deepseek_max_tokens: int = Field(default=1000, ge=64, le=8192)
    typesafe_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    typesafe_base_url: HttpUrl = HttpUrl("https://api.typesafe.ai")
    typesafe_model: str = Field(default="jev-latest", min_length=1, max_length=100)
    typesafe_timeout_seconds: float = Field(default=15, ge=1, le=120)
    jev_min_relevance: float = Field(default=0.8, ge=0.5, le=1)
    jev_max_violation: float = Field(default=0.2, ge=0, le=0.5)
    chat_db_path: Path = ROOT / "storage" / "chat.sqlite3"
    chat_session_ttl_seconds: int = Field(default=86400, ge=60)
    chat_history_turns: int = Field(default=10, ge=1, le=100)
    chat_context_top_k: int = Field(default=5, ge=1, le=50)
    auth_session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)
    knowledge_db_path: Path = ROOT / "storage" / "knowledge.sqlite3"
    knowledge_min_value: float = Field(default=0.8, ge=0.5, le=1)
    knowledge_min_match: float = Field(default=0.85, ge=0.5, le=1)
    knowledge_top_k: int = Field(default=3, ge=1, le=5)
    knowledge_candidate_limit: int = Field(default=6, ge=1, le=20)
    knowledge_direct_enabled: bool = True
    knowledge_direct_min_match: float = Field(default=0.85, ge=0.5, le=1)

    @field_validator("deepseek_api_key", "typesafe_api_key", "db_password", mode="before")
    @classmethod
    def empty_key_is_unconfigured(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def require_database_credentials(self):
        if self.database_backend == "mysql" and not self.db_password:
            raise ValueError("MySQL 模式需要配置 DB_PASSWORD")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        mapping = {
            "model_path": "YOLO_MODEL_PATH", "device": "YOLO_DEVICE",
            "leaf_model_path": "LEAF_MODEL_PATH", "leaf_device": "LEAF_DEVICE",
            "leaf_min_similarity": "LEAF_MIN_SIMILARITY", "leaf_min_margin": "LEAF_MIN_MARGIN",
            "image_size": "YOLO_IMAGE_SIZE", "default_confidence": "DEFAULT_CONFIDENCE",
            "default_top_k": "DEFAULT_TOP_K", "max_upload_mb": "MAX_UPLOAD_MB",
            "max_image_pixels": "MAX_IMAGE_PIXELS", "result_dir": "RESULT_DIR",
            "result_ttl_seconds": "RESULT_TTL_SECONDS", "require_native": "REQUIRE_NATIVE",
            "host": "HOST", "port": "PORT",
            "product_data_dir": "PRODUCT_DATA_DIR",
            "database_backend": "DATABASE_BACKEND", "db_host": "DB_HOST",
            "db_port": "DB_PORT", "db_name": "DB_NAME", "db_user": "DB_USER",
            "db_password": "DB_PASSWORD", "db_pool_size": "DB_POOL_SIZE",
            "db_timeout_seconds": "DB_TIMEOUT_SECONDS", "db_ssl": "DB_SSL",
            "db_ssl_ca": "DB_SSL_CA",
            "deepseek_api_key": "DEEPSEEK_API_KEY", "deepseek_base_url": "DEEPSEEK_BASE_URL",
            "deepseek_model": "DEEPSEEK_MODEL", "deepseek_timeout_seconds": "DEEPSEEK_TIMEOUT_SECONDS",
            "deepseek_max_tokens": "DEEPSEEK_MAX_TOKENS", "chat_db_path": "CHAT_DB_PATH",
            "typesafe_api_key": "TYPESAFE_API_KEY", "typesafe_base_url": "TYPESAFE_BASE_URL",
            "typesafe_model": "TYPESAFE_MODEL", "typesafe_timeout_seconds": "TYPESAFE_TIMEOUT_SECONDS",
            "jev_min_relevance": "JEV_MIN_RELEVANCE", "jev_max_violation": "JEV_MAX_VIOLATION",
            "chat_session_ttl_seconds": "CHAT_SESSION_TTL_SECONDS", "chat_history_turns": "CHAT_HISTORY_TURNS",
            "chat_context_top_k": "CHAT_CONTEXT_TOP_K",
            "auth_session_ttl_seconds": "AUTH_SESSION_TTL_SECONDS",
            "knowledge_db_path": "KNOWLEDGE_DB_PATH", "knowledge_min_value": "KNOWLEDGE_MIN_VALUE",
            "knowledge_min_match": "KNOWLEDGE_MIN_MATCH", "knowledge_top_k": "KNOWLEDGE_TOP_K",
            "knowledge_candidate_limit": "KNOWLEDGE_CANDIDATE_LIMIT",
            "knowledge_direct_enabled": "KNOWLEDGE_DIRECT_ENABLED",
            "knowledge_direct_min_match": "KNOWLEDGE_DIRECT_MIN_MATCH",
        }
        values = {field: os.environ[key] for field, key in mapping.items() if key in os.environ}
        if not values.get("db_ssl_ca"):
            values["db_ssl_ca"] = None
        for field in ("model_path", "leaf_model_path", "result_dir", "chat_db_path", "knowledge_db_path", "product_data_dir"):
            if field in values:
                path = Path(values[field]).expanduser()
                values[field] = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        values["cors_origins"] = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
        return cls(**values)
