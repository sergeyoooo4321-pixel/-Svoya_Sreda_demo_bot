"""Состояния FSM.

Жёсткое пошаговое оформление убрано — заявку собирает ИИ-агент в свободном диалоге.
Остался только живой чат с менеджером (короткая форма + режим переписки).
"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ManagerChat(StatesGroup):
    awaiting_contact = State()   # просим имя + телефон
    active = State()             # идёт живая переписка клиент ↔ менеджер
