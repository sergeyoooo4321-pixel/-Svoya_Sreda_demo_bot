"""Абстракция crm_client с переключением реализации по CRM_MODE (раздел т.md §15, §20).

По умолчанию — локальный SvoyaCRM. Bitrix24 оставлен как будущая опция за флагом,
чтобы код можно было снова переключить на внешнюю CRM без переписывания агента.
"""
from __future__ import annotations

from app.config import get_settings
from app.crm.service import SvoyaCRM, get_crm
from app.logger import logger


def get_crm_client() -> SvoyaCRM:
    """Возвращает активную CRM согласно CRM_MODE.

    local  → SvoyaCRM (локальный аналог Bitrix24, по умолчанию).
    bitrix → пока не активна в этом демо; падать не будем, используем local и предупредим.
    """
    settings = get_settings()
    if settings.crm_is_local:
        return get_crm()
    logger.warning(
        "CRM_MODE=bitrix запрошен, но в демо активна только локальная SvoyaCRM. "
        "Использую local. Чтобы вернуть Bitrix24 — добавьте Bitrix-адаптер с тем же интерфейсом."
    )
    return get_crm()
