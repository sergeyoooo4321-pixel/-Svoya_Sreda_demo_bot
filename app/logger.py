"""Настройка логгера. Маскируем секреты и телефоны."""
from __future__ import annotations

import re
import sys

from loguru import logger

from app.config import get_settings


_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b")
_BITRIX_WEBHOOK_RE = re.compile(r"https?://[^/]+/rest/\d+/[A-Za-z0-9]+/?")
_API_KEY_RE = re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-:.]+[\"']?")
_PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")


def _mask_phone_in_text(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 10:
        return match.group(0)
    return f"+{digits[0]}{digits[1:4]}******{digits[-2:]}"


def _sanitize(message: str) -> str:
    message = _TELEGRAM_TOKEN_RE.sub("***TELEGRAM_TOKEN***", message)
    message = _BITRIX_WEBHOOK_RE.sub("***BITRIX_WEBHOOK***", message)
    message = _API_KEY_RE.sub(r"\1=***", message)
    message = _PHONE_RE.sub(_mask_phone_in_text, message)
    return message


def _patch_record(record: dict) -> None:
    try:
        record["message"] = _sanitize(record["message"])
    except Exception:
        pass


def setup_logger() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        backtrace=False,
        diagnose=False,
        enqueue=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logger.configure(patcher=_patch_record)


__all__ = ["logger", "setup_logger"]
