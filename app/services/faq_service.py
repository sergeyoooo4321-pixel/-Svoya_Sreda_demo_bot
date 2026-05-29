"""Загрузка базы знаний FAQ для подачи в промт ИИ."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


FAQ_PATH = Path(__file__).resolve().parent.parent / "data" / "faq.md"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "data" / "system_prompt.md"


@lru_cache(maxsize=1)
def load_faq() -> str:
    if not FAQ_PATH.exists():
        return ""
    return FAQ_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.exists():
        return ""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
