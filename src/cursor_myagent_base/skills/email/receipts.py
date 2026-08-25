from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from cursor_myagent_base.config import project_root
from cursor_myagent_base.skills.normalize import normalize_text


def receipts_path() -> Path:
    override = (os.getenv("EMAIL_RECEIPTS_PATH") or "").strip()
    if override:
        return Path(override)
    return project_root() / ".email_receipts.json"


def receipt_key(*, thread_id: str, to: str, subject: str, body: str) -> str:
    packed = "|".join(
        [
            normalize_text(thread_id),
            normalize_text(to),
            normalize_text(subject),
            normalize_text(body),
        ]
    )
    return sha256(packed.encode("utf-8")).hexdigest()


def _load_all(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def find_receipt(key: str) -> dict[str, Any] | None:
    item = _load_all(receipts_path()).get(key)
    return item if isinstance(item, dict) else None


def delete_receipt(key: str) -> None:
    path = receipts_path()
    records = _load_all(path)
    if key not in records:
        return
    records.pop(key, None)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(records, ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def save_receipt(key: str, payload: dict[str, Any]) -> None:
    path = receipts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _load_all(path)
    records[key] = {
        **payload,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(records, ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
