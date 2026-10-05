from fastapi import APIRouter, Request

from API.dependencies import get_service

router = APIRouter()


@router.get("/health", summary="服务和模型状态")
def health(request: Request):
    settings = request.app.state.settings
    service = get_service(request)
    return {"status": "ok", "model_loaded": True, "model": settings.model_path.name,
            "task": service.task, "class_count": len(service.classes),
            "device": settings.device, "postprocessor": service.postprocessor,
            "leaf_check": {"enabled": True, "model": settings.leaf_model_path.name,
                           "device": settings.leaf_device,
                           "minimum_similarity": settings.leaf_min_similarity,
                           "minimum_margin": settings.leaf_min_margin},
            "deepseek": {"configured": request.app.state.chat.client.configured,
                         "model": settings.deepseek_model, "automatic_analysis": True},
            "jev": {"enabled": True, "configured": request.app.state.chat.guard.configured,
                    "model": settings.typesafe_model, "checks": ["user_question", "assistant_reply"],
                    "minimum_relevance": settings.jev_min_relevance,
                    "maximum_violation": settings.jev_max_violation},
            "knowledge": {"enabled": True, "minimum_value": settings.knowledge_min_value,
                          "minimum_match": settings.knowledge_min_match,
                          "direct_enabled": settings.knowledge_direct_enabled,
                          "direct_minimum_match": settings.knowledge_direct_min_match}}


