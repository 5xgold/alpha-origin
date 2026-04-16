"""OpenAI 兼容 API 封装 — DeepSeek / Qwen / 本地模型通用"""

from pathlib import Path
from openai import OpenAI
from jinja2 import Environment, FileSystemLoader

from llm_digest.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
    LLM_MAX_TOKENS, LLM_TEMPERATURE, PROMPTS_DIR,
)

_client = None
_jinja_env = None


def _get_client():
    global _client
    if _client is None:
        if not LLM_API_KEY:
            raise RuntimeError("未配置 LLM_API_KEY，请在 .env 中设置")
        _client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    return _client


def _get_jinja_env():
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(PROMPTS_DIR)),
            keep_trailing_newline=True,
        )
    return _jinja_env


def chat(prompt: str, system: str = "", temperature: float = LLM_TEMPERATURE) -> str:
    """调用 LLM，返回文本回复"""
    client = _get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=LLM_MAX_TOKENS,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        error_msg = str(e)
        if "maximum context length" in error_msg or "token" in error_msg.lower():
            raise RuntimeError(f"Token 超限，请缩减输入内容: {error_msg}")
        raise RuntimeError(f"LLM 调用失败: {error_msg}")


def chat_with_template(template_name: str, context: dict, **kwargs) -> str:
    """渲染 Jinja2 模板后调用 LLM"""
    env = _get_jinja_env()
    template = env.get_template(template_name)
    prompt = template.render(**context)
    return chat(prompt, **kwargs)
