from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from API.errors import register_error_handlers
from API.middleware import UploadLimitMiddleware
from API.routes import chat, health, knowledge, recognition
from Chat.deepseek import DeepSeekClient
from Chat.service import ChatService
from Common.config import Settings
from Jev.guard import JevGuard
from Visual.recognition.images import cleanup_results
from Visual.recognition.recognizer import YoloRecognizer


def create_app(settings: Settings | None = None, service_factory: Callable = YoloRecognizer,
               deepseek_factory: Callable = DeepSeekClient, jev_factory: Callable = JevGuard) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        chat_service = ChatService(settings, deepseek_factory(settings), jev_factory(settings))
        try:
            await chat_service.initialize()
            settings.result_dir.mkdir(parents=True, exist_ok=True)
            await run_in_threadpool(cleanup_results, settings.result_dir, settings.result_ttl_seconds)
            service = service_factory(settings)
            await run_in_threadpool(service.load)
            application.state.recognizer = service
            application.state.chat = chat_service
            yield
        finally:
            application.state.recognizer = None
            application.state.chat = None
            await chat_service.close()

    application = FastAPI(
        title="植物病虫害图片识别 API", version="0.5.0",
        description="先检查叶片并识别病害；Jev 检查话题和已有知识适用性，优先复用匹配问答，否则由 DeepSeek 回复，支持携带识别上下文的多轮对话。",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.add_middleware(UploadLimitMiddleware, max_body_bytes=settings.max_upload_bytes + 64 * 1024)
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"], allow_headers=["*"],
        )
    register_error_handlers(application)
    application.include_router(health.router)
    application.include_router(recognition.router)
    application.include_router(chat.router)
    application.include_router(knowledge.router)
    return application


app = create_app()
