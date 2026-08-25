from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

_FALLBACK_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """仓库根目录。Docker 里包安装在 site-packages，需设 PROJECT_ROOT=/app。"""
    override = (os.getenv("PROJECT_ROOT") or "").strip()
    if override:
        return Path(override)
    if (_FALLBACK_ROOT / "pyproject.toml").is_file():
        return _FALLBACK_ROOT
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").is_file() or (cwd / "contacts.json").is_file() or (cwd / ".env").is_file():
        return cwd
    return _FALLBACK_ROOT


load_dotenv(project_root() / ".env")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


def get_dashscope_api_key() -> str:
    return (os.getenv("DASHSCOPE_API_KEY") or "").strip()


def get_openweather_api_key() -> str:
    return (os.getenv("OPENWEATHER_API_KEY") or "").strip()


def get_amap_api_key() -> str:
    return (os.getenv("AMAP_API_KEY") or os.getenv("GAODE_API_KEY") or "").strip()


def get_amap_secret() -> str:
    """数字签名私钥；控制台未开启签名时留空。"""
    return (os.getenv("AMAP_SECRET") or "").strip()


def get_smtp_user() -> str:
    return (os.getenv("SMTP_USER") or "").strip()


def get_smtp_auth_code() -> str:
    return (os.getenv("SMTP_AUTH_CODE") or "").strip()


def get_smtp_host() -> str:
    return (os.getenv("SMTP_HOST") or "smtp.sina.com").strip()


def get_smtp_port() -> int:
    raw = (os.getenv("SMTP_PORT") or "465").strip()
    try:
        return int(raw)
    except ValueError:
        return 465


def load_contacts() -> dict[str, str]:
    path = project_root() / "contacts.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    contacts: dict[str, str] = {}
    for name, email in data.items():
        key = str(name or "").strip()
        value = str(email or "").strip()
        if key and value:
            contacts[key] = value
    return contacts


def format_contact_names() -> str:
    names = list(load_contacts())
    return "、".join(names) if names else "（通讯录为空）"


def list_contacts() -> list[dict[str, str]]:
    return [{"name": name, "email": email} for name, email in load_contacts().items()]


def get_agent_thread_id() -> str:
    return (os.getenv("AGENT_THREAD_ID") or "cli-local").strip() or "cli-local"


def get_api_token() -> str:
    return (os.getenv("API_TOKEN") or "").strip()


def get_max_concurrent_runs() -> int:
    raw = (os.getenv("MAX_CONCURRENT_RUNS") or "8").strip()
    try:
        return max(1, min(int(raw), 64))
    except ValueError:
        return 8


def get_postgres_uri() -> str:
    """优先用 POSTGRES_URI；否则用 HOST/USER/PASSWORD/DB 拼连接串。"""
    uri = (os.getenv("POSTGRES_URI") or "").strip()
    if uri:
        return uri
    user = (os.getenv("POSTGRES_USER") or "postgres").strip() or "postgres"
    password = os.getenv("POSTGRES_PASSWORD") or ""
    host = (os.getenv("POSTGRES_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.getenv("POSTGRES_PORT") or "5432").strip() or "5432"
    database = (os.getenv("POSTGRES_DB") or "postgres").strip() or "postgres"
    return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"


def require_postgres_uri() -> str:
    if not (os.getenv("POSTGRES_URI") or "").strip() and not (os.getenv("POSTGRES_PASSWORD") or "").strip():
        raise RuntimeError(
            "未配置 PostgreSQL 连接。请在 .env 中填写 POSTGRES_PASSWORD（安装时设置的 postgres 用户密码），"
            "或直接填写 POSTGRES_URI=postgresql://postgres:密码@127.0.0.1:5432/postgres"
        )
    return get_postgres_uri()


def require_dashscope_api_key() -> str:
    api_key = get_dashscope_api_key()
    if not api_key:
        raise RuntimeError(
            "未配置 DASHSCOPE_API_KEY。请复制 .env.example 为 .env 并填入通义千问密钥。"
        )
    return api_key


@lru_cache(maxsize=8)
def get_llm(*, temperature: float = 0.2) -> ChatOpenAI:
    """通过 DashScope 的 OpenAI 兼容接口调用通义千问（同步 invoke / 异步 ainvoke 都可用）。"""
    return ChatOpenAI(
        model=os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL),
        api_key=require_dashscope_api_key(),
        base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
        temperature=temperature,
        streaming=True,
        max_retries=2,
    )
