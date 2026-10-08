from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from Common.errors import UserError
from Users.schemas import UserProfile

login_credentials = HTTPBearer(auto_error=False)


def get_service(request: Request):
    service = getattr(request.app.state, "recognizer", None)
    if service is None:
        raise HTTPException(503, "模型未就绪")
    return service


def get_chat_service(request: Request):
    service = getattr(request.app.state, "chat", None)
    if service is None:
        raise HTTPException(503, "对话服务未就绪")
    return service


def get_user_store(request: Request):
    store = getattr(request.app.state, "users", None)
    if store is None:
        raise HTTPException(503, "用户服务未就绪")
    return store


def optional_user(request: Request,
                  credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(login_credentials)]) -> UserProfile | None:
    if credentials is None:
        if "authorization" in request.headers:
            raise UserError(401, "invalid_session", "登录凭证格式不正确，请重新登录。")
        return None
    return get_user_store(request).authenticate(credentials.credentials)


def authenticated_user(user: Annotated[UserProfile | None, Depends(optional_user)]) -> UserProfile:
    if user is None:
        raise UserError(401, "auth_required", "请先登录。")
    return user
