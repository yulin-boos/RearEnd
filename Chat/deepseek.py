import httpx

from Common.errors import ChatError
from Common.config import Settings


class DeepSeekClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.configured = settings.deepseek_api_key is not None
        self.client = httpx.AsyncClient(timeout=settings.deepseek_timeout_seconds, transport=transport, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def complete(self, messages: list[dict], *, json_mode: bool = False) -> dict:
        if not self.configured:
            raise ChatError(503, "deepseek_not_configured", "尚未配置 DeepSeek 密钥，请在后端配置后重试。")
        key = self.settings.deepseek_api_key.get_secret_value()
        payload = {"model": self.settings.deepseek_model, "messages": messages, "stream": False,
                   "thinking": {"type": "disabled"}, "temperature": 0.3,
                   "max_tokens": self.settings.deepseek_max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.post(
                str(self.settings.deepseek_base_url).rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        except httpx.TimeoutException:
            raise ChatError(504, "deepseek_timeout", "DeepSeek 回复超时，请稍后重试。") from None
        except httpx.RequestError:
            raise ChatError(502, "deepseek_connection_failed", "无法连接 DeepSeek，请检查后端网络后重试。") from None
        failures = {
            401: (503, "deepseek_auth_failed", "DeepSeek 密钥无效或没有权限，请检查后端配置。"),
            403: (503, "deepseek_auth_failed", "DeepSeek 密钥无效或没有权限，请检查后端配置。"),
            402: (503, "deepseek_insufficient_balance", "DeepSeek 账户余额不足，请充值后重试。"),
            429: (429, "deepseek_rate_limited", "DeepSeek 请求过于频繁，请稍后重试。"),
        }
        if response.status_code in failures:
            raise ChatError(*failures[response.status_code])
        if not response.is_success:
            # Never forward upstream error bodies, which may contain sensitive values.
            raise ChatError(502, "deepseek_unavailable", "DeepSeek 请求失败，请检查模型配置或稍后重试。")
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
                raise ValueError("Invalid completion structure")
            choice = data["choices"][0]
            if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                raise ValueError("Invalid message structure")
            reply = choice["message"]["content"]
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("Empty answer")
            if choice.get("finish_reason") not in ("stop", "length"):
                raise ValueError("Incomplete answer")
            raw_usage = data.get("usage")
            if raw_usage is None:
                raw_usage = {}
            if not isinstance(raw_usage, dict):
                raise ValueError("Invalid usage structure")
            usage = {name: raw_usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                     if type(raw_usage.get(name)) is int and raw_usage[name] >= 0}
        except (ValueError, KeyError, IndexError, TypeError):
            raise ChatError(502, "deepseek_invalid_response", "DeepSeek 返回了无效回复，请稍后重试。") from None
        return {"reply": reply.strip(), "usage": usage, "truncated": choice.get("finish_reason") == "length"}
