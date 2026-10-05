from fastapi import HTTPException, Request


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
