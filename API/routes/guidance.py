from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from API.dependencies import get_chat_service, optional_user
from Chat.guidance_schemas import GuidanceRequest, GuidanceResponse
from Users.schemas import UserProfile

router = APIRouter()


@router.post("/api/v1/chat/guidance", response_model=GuidanceResponse,
             summary="生成引导追问或结合补充信息继续分析")
async def generate_guidance(request: Request, body: GuidanceRequest,
                            user: Annotated[UserProfile | None, Depends(optional_user)]):
    return await get_chat_service(request).guidance.generate(body, user.id if user else None)


@router.get("/api/v1/chat/guidance/{recognition_id}", response_model=GuidanceResponse,
            summary="获取已保存的引导问题、补充信息和当前会话版本")
async def get_guidance(request: Request, recognition_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
                       user: Annotated[UserProfile | None, Depends(optional_user)]):
    return await get_chat_service(request).guidance.get(recognition_id, user.id if user else None)
