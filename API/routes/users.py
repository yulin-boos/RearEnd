from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from API.dependencies import authenticated_user, get_user_store, login_credentials
from Users.schemas import LoginRequest, LoginResponse, ProfileUpdate, RegisterRequest, UserProfile

router = APIRouter(tags=["用户账号"])


@router.post("/api/v1/auth/register", response_model=LoginResponse, status_code=201, summary="注册账号并登录")
def register(request: Request, response: Response, body: RegisterRequest):
    response.headers["Cache-Control"] = "no-store"
    return get_user_store(request).register(body)


@router.post("/api/v1/auth/login", response_model=LoginResponse, summary="使用账号和密码登录")
def login(request: Request, response: Response, body: LoginRequest):
    response.headers["Cache-Control"] = "no-store"
    return get_user_store(request).login(body)


@router.post("/api/v1/auth/logout", status_code=204, summary="退出当前登录会话")
def logout(request: Request, user: Annotated[UserProfile, Depends(authenticated_user)],
           credentials: Annotated[HTTPAuthorizationCredentials, Depends(login_credentials)]):
    get_user_store(request).logout(credentials.credentials)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/users/me", response_model=UserProfile, summary="读取当前用户资料")
def profile(response: Response, user: Annotated[UserProfile, Depends(authenticated_user)]):
    response.headers["Cache-Control"] = "no-store"
    return user


@router.patch("/api/v1/users/me", response_model=UserProfile, summary="修改当前用户昵称、电话和简介")
def update_profile(request: Request, response: Response, body: ProfileUpdate,
                   user: Annotated[UserProfile, Depends(authenticated_user)]):
    response.headers["Cache-Control"] = "no-store"
    return get_user_store(request).update_profile(user.id, body)
