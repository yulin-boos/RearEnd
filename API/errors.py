import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from Common.errors import ChatError

logger = logging.getLogger(__name__)


def register_error_handlers(application: FastAPI):
    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        codes = {400: "bad_request", 404: "not_found", 413: "payload_too_large", 415: "unsupported_image", 503: "not_ready"}
        error = exc.detail if isinstance(exc.detail, dict) else {"code": codes.get(exc.status_code, "http_error"), "message": exc.detail}
        return JSONResponse({"error": error}, status_code=exc.status_code)


    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": list(error["loc"]), "message": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "请求参数不合法", "details": details}}, status_code=422)


    @application.exception_handler(ChatError)
    async def chat_error(request: Request, exc: ChatError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code)


    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("Request failed: %s", request.url.path, exc_info=exc)
        return JSONResponse({"error": {"code": "internal_error", "message": "图片识别失败，请检查服务日志"}}, status_code=500)
