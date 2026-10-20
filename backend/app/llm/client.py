"""LLM provider 抽象。

两个实现：
- OpenAICompat：DeepSeek / 通义 / OpenAI 同一套 chat completions 接口，
  用裸 httpx 调，不引 SDK——少一个依赖，少一处版本坑。
- OfflineClient：无 API Key 时的全链路兜底。追问用模板、抽取用正则词典
  （复用 pricing_agent 的拆解能力）、解读用结构化模板。话术平实但流程完整，
  这是演示环境的底线，不是摆设。

环境变量：
    SDLC_LLM_KEY   API Key（不设则离线）
    SDLC_LLM_BASE  默认 https://api.deepseek.com
    SDLC_LLM_MODEL 默认 deepseek-flash
"""
from __future__ import annotations

import json
import os
import time
from typing import Iterator

import httpx


class LLMError(Exception):
    """调用失败（网络 / 超时 / 非 200）。调用方捕获后降级离线。"""


class SchemaError(Exception):
    """模型返回的 JSON 不合 schema。调用方捕获后降级离线。"""


def _assert_ascii(name: str, value: str) -> None:
    """HTTP header 与 URL 只吃 ASCII。

    中文、全角空格、零宽字符（复制 key 时极常见）混进 header，
    httpx 底层会抛 `'ascii' codec can't encode characters in position 7-10`——
    这话对使用者毫无信息量。这里提前拦下来，直接说是第几位、什么字符。
    """
    if not value:
        return
    bad = [(i, ch) for i, ch in enumerate(value) if ord(ch) > 127]
    if not bad:
        return
    i, ch = bad[0]
    sample = "、".join(f"第 {j + 1} 位 U+{ord(c):04X}" for j, c in bad[:3])
    raise LLMError(
        f"{name} 里有 {len(bad)} 个非 ASCII 字符（{sample}），"
        f"只能是英文字母、数字和 - _ 这类符号。"
        f"多半是从聊天窗口或文档里复制时带上了中文/不可见字符，请重新复制纯英文的那串。")


class ChatResult:
    __slots__ = ("data", "text", "tokens_in", "tokens_out", "latency_ms", "model")

    def __init__(self, data=None, text="", tokens_in=0, tokens_out=0,
                 latency_ms=0.0, model=""):
        self.data = data
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.latency_ms = latency_ms
        self.model = model


def _extract_json(text: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象/数组。模型偶尔会包一层 ```json。"""
    text = (text or "").strip()
    if not text:
        raise SchemaError("模型返回为空")
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i >= 0 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except ValueError:
                continue
    raise SchemaError(f"无法解析 JSON：{text[:120]}")


class OpenAICompat:
    online = True

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0,
                 temperature: float | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = 0.2 if temperature is None else float(temperature)

    def _headers(self) -> dict:
        _assert_ascii("API Key", self.api_key)
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _check_base(self) -> None:
        _assert_ascii("Base URL", self.base_url)

    def list_models(self, timeout: float = 15.0) -> list[str]:
        """问服务端有哪些模型名可用。

        模型名是厂商说了算的活物：deepseek-chat 现在已经不在列表里，
        只认 deepseek-flash / deepseek-v4-pro。硬编码迟早过期，所以让页面能直接问。
        """
        self._check_base()
        try:
            resp = httpx.get(f"{self.base_url}/models",
                             headers=self._headers(), timeout=timeout)
        except (httpx.HTTPError, OSError) as e:
            raise LLMError(f"网络错误：{e}") from e
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except ValueError as e:
            raise LLMError(f"返回不是 JSON：{resp.text[:120]}") from e
        items = body.get("data") if isinstance(body, dict) else body
        out: list[str] = []
        for it in items or []:
            mid = it.get("id") if isinstance(it, dict) else None
            if mid:
                out.append(str(mid))
        return sorted(set(out))

    def chat_json(self, system: str, user: str, temperature: float | None = None,
                  max_tokens: int = 4000) -> ChatResult:
        """JSON mode：要求模型只回 JSON，由调用方做 schema 校验。"""
        t = time.perf_counter()
        self._check_base()
        _assert_ascii("模型名", self.model)
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "temperature": self.temperature if temperature is None else temperature,
                    "response_format": {"type": "json_object"},
                    "max_tokens": max_tokens,
                },
                timeout=self.timeout,
            )
        except (httpx.HTTPError, OSError) as e:
            raise LLMError(f"网络错误：{e}") from e
        latency = (time.perf_counter() - t) * 1000
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"响应结构异常：{str(body)[:200]}") from e
        usage = body.get("usage") or {}
        data = _extract_json(content)
        return ChatResult(data=data, text=content,
                          tokens_in=usage.get("prompt_tokens", 0),
                          tokens_out=usage.get("completion_tokens", 0),
                          latency_ms=latency, model=self.model)

    def stream(self, system: str, user: str, temperature: float | None = None) -> Iterator[str]:
        """流式输出纯文本（EXPLAIN 用）。产出的是逐段文本增量。"""
        self._check_base()
        _assert_ascii("模型名", self.model)
        try:
            with httpx.stream(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "temperature": self.temperature if temperature is None else temperature,
                    "stream": True,
                    "max_tokens": 600,
                },
                timeout=self.timeout,
            ) as resp:
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except (httpx.HTTPError, OSError) as e:
            raise LLMError(f"网络错误：{e}") from e


class OfflineClient:
    """离线兜底。不装样子：它产出的每句话都明确标注来自模板。"""

    online = False
    model = "offline"

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> ChatResult:
        raise LLMError("未配置 SDLC_LLM_KEY，离线模式不支持自由生成")

    def stream(self, system: str, user: str, temperature: float | None = None) -> Iterator[str]:
        raise LLMError("未配置 SDLC_LLM_KEY，离线模式不支持自由生成")


# 默认模型 = DeepSeek（deepseek-flash）。
# 选它：便宜、快、中文理解强、OpenAI 协议兼容。
# 本项目从不接 Kimi / moonshot——同量级能力下成本差一个数量级。
# 想换任意 OpenAI 兼容模型，只改 SDLC_LLM_BASE / SDLC_LLM_MODEL 两个环境变量，代码零改动。
DEFAULTS = {
    "base": "https://api.deepseek.com",
    "model": "deepseek-flash",
}


def get_client(conn=None):
    """有 key 用线上，没 key 用离线。每次调用新建，避免配置变更后残留。

    传 conn 时优先用「页面里保存的配置」（llm_configs 表），没配再回落到环境变量。
    不传 conn 的老调用（报价对话等）行为完全不变。
    """
    if conn is not None:
        try:
            from .config_store import effective
            e = effective(conn)
            if e.get("api_key"):
                return OpenAICompat(
                    base_url=e["base_url"], api_key=e["api_key"], model=e["model"],
                    temperature=e.get("temperature", 0.2))
        except Exception:                        # noqa: BLE001  配置读不到不该影响主流程
            pass
    key = os.environ.get("SDLC_LLM_KEY")
    if key:
        return OpenAICompat(
            base_url=os.environ.get("SDLC_LLM_BASE", DEFAULTS["base"]),
            api_key=key,
            model=os.environ.get("SDLC_LLM_MODEL", DEFAULTS["model"]),
        )
    return OfflineClient()


def llm_status() -> dict:
    """给前端横幅用：当前到底在线还是离线、用的哪个模型。"""
    c = get_client()
    return {"online": c.online,
            "model": getattr(c, "model", "offline"),
            "base": os.environ.get("SDLC_LLM_BASE", DEFAULTS["base"]) if c.online else None}
