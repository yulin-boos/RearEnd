from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from API.dependencies import get_chat_service
from Common.schemas import ChatReply, ChatRequest, ChatSessionResponse

router = APIRouter()


@router.post("/api/v1/chat/sessions", response_model=ChatSessionResponse, summary="创建无需图片识别的植物咨询会话")
async def create_chat_session(request: Request):
    service = get_chat_service(request)
    identifier = await run_in_threadpool(service.create_general)
    return ChatSessionResponse(session_id=identifier)


@router.post("/api/v1/chat", response_model=ChatReply, summary="植物咨询或结合图片识别结果与 DeepSeek 对话")
async def chat(request: Request, body: ChatRequest):
    service = get_chat_service(request)
    return await service.reply(body.recognition_id, body.message)


