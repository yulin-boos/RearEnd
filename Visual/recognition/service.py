from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException

from Common.config import Settings
from Common.schemas import ImageInfo, RecognitionResponse
from .images import cleanup_results, decode_image, save_result_image


def perform_recognition(settings: Settings, data: bytes, filename: str, threshold: float,
                        top_k: int, save_result: bool, service, chat) -> RecognitionResponse:
    started = perf_counter()
    image = decode_image(data, settings.max_image_pixels)
    leaf_check = service.check_leaf(image)
    if not leaf_check.is_leaf:
        raise HTTPException(422, {
            "code": "not_leaf", "message": "未检测到明确的植物叶片，已拒绝识别。请上传清晰的叶片图片。",
            "leaf_check": leaf_check.model_dump(),
        })
    # Keep enough candidates for DeepSeek even when the UI requests only Top-1.
    predictions, inference_ms = service.predict(image, max(top_k, settings.chat_context_top_k))
    best = predictions[0]
    confident = best.confidence >= threshold
    request_id = uuid4().hex
    result_url = None
    if save_result:
        cleanup_results(settings.result_dir, settings.result_ttl_seconds)
        save_result_image(image, best, confident, settings.result_dir / f"{request_id}.jpg")
        result_url = f"/api/v1/results/{request_id}.jpg"
    result = RecognitionResponse(
        request_id=request_id, model=settings.model_path.name,
        image=ImageInfo(filename=filename, width=image.width, height=image.height),
        leaf_check=leaf_check,
        postprocessor=service.postprocessor, confidence_threshold=threshold,
        is_confident=confident, status="recognized" if confident else "uncertain",
        top_prediction=best, predictions=predictions[:top_k], inference_ms=inference_ms,
        elapsed_ms=round((perf_counter() - started) * 1000, 3), result_image_url=result_url,
    )
    chat.seed(result, predictions)
    return result
