import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if not value.strip():
            raise ValueError("密码不能全为空格")
        return value


class RegisterRequest(LoginRequest):
    nickname: str | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("nickname", mode="before")
    @classmethod
    def strip_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, min_length=1, max_length=20)
    phone: str | None = Field(default=None, max_length=30)
    bio: str | None = Field(default=None, max_length=80)

    @field_validator("nickname", "phone", "bio", mode="before")
    @classmethod
    def strip_fields(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value):
        if value and not re.fullmatch(r"\+?[0-9 -]{7,30}", value):
            raise ValueError("请填写有效的联系电话，或留空")
        return value

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个资料字段")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("资料字段不能为 null；清空电话或简介请传空字符串")
        return self


class UserProfile(BaseModel):
    id: str
    username: str
    nickname: str
    phone: str
    bio: str
    created_at: float
    updated_at: float


class LoginResponse(BaseModel):
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_at: float
    user: UserProfile
