from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    """Bound multipart bytes before parsing, including chunked HTTP requests."""

    def __init__(self, app, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        if b"content-length" in headers:
            try:
                declared = int(headers[b"content-length"])
            except ValueError:
                declared = -1
            if declared < 0:
                await JSONResponse({"error": {"code": "bad_request", "message": "Content-Length 不合法"}}, status_code=400)(scope, receive, send)
                return
            if declared > self.max_body_bytes:
                await JSONResponse({"error": {"code": "payload_too_large", "message": "上传内容超过大小限制"}}, status_code=413)(scope, receive, send)
                return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise HTTPException(413, "上传内容超过大小限制")
            return message

        await self.app(scope, limited_receive, send)
