from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from API.dependencies import get_service
from Common.schemas import ClassInfo, RecognitionResponse
from Visual.recognition.images import clean_filename
from Visual.recognition.service import perform_recognition

router = APIRouter()


@router.get("/api/v1/classes", response_model=list[ClassInfo], summary="模型支持的类别")
def classes(request: Request):
    return get_service(request).classes


@router.post("/api/v1/recognize", response_model=RecognitionResponse, summary="上传叶片图片并识别病虫害",
                  responses={422: {"description": "请求参数不合法，或图片未通过叶片检查（error.code=not_leaf）"}})
async def recognize(
    request: Request,
    file: Annotated[UploadFile, File(description="JPEG / PNG / WebP / BMP 图片")],
    confidence: Annotated[float | None, Form(ge=0, le=1, description="低于此阈值时标记为 uncertain")] = None,
    top_k: Annotated[int | None, Form(ge=1, le=50, description="返回候选数量，最多为实际类别数")] = None,
    save_result: Annotated[bool, Form(description="是否保存带识别文字的结果图片")] = True,
    auto_analyze: Annotated[bool, Form(description="识别后是否自动分析（优先复用知识，否则调用 DeepSeek）；识别上下文始终保留供后续对话")] = True,
):
    settings = request.app.state.settings
    service = get_service(request)
    try:
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"图片不能超过 {settings.max_upload_mb} MB")
        result = await run_in_threadpool(
            perform_recognition, settings, data, clean_filename(file.filename),
            settings.default_confidence if confidence is None else confidence,
            settings.default_top_k if top_k is None else top_k, save_result, service, request.app.state.chat,
        )
        result.chat = await request.app.state.chat.analyze(result.request_id, auto_analyze)
        return result
    finally:
        await file.close()


@router.get("/api/v1/results/{request_id}.jpg", summary="获取识别结果图片", response_class=FileResponse)
def result_image(request: Request, request_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]):
    settings = request.app.state.settings
    path = settings.result_dir / f"{request_id}.jpg"
    if not path.is_file():
        raise HTTPException(404, "结果图片不存在或已过期")
    import time
    if path.stat().st_mtime < time.time() - settings.result_ttl_seconds:
        raise HTTPException(404, "结果图片已过期")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

