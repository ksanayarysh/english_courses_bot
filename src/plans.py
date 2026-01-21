from __future__ import annotations

from enum import Enum


class Plan(str, Enum):
    LIVE_ONLY = "live_only"
    MIXED = "mixed"


def get_plan_label(plan: str) -> str:
    return "Все занятия вживую" if plan == Plan.LIVE_ONLY.value else "Вживую + видео"
