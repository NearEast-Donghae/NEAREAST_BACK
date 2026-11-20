"""What changes in recommendations depending on who the user travels with.

Every change is a rule written here, with the sentence shown to the user when it applies:
nothing is adjusted silently. A companion without a rule changes nothing.
"""

from dataclasses import dataclass
from typing import Literal

Companion = Literal["alone", "couple", "child", "parents"]


@dataclass(frozen=True)
class CompanionRule:
    label: str
    maxWalkingMinutes: int | None = None  # one way; None: the service default
    indoorFirst: bool = False  # cultural (indoor) places are listed before the others
    reason: str | None = None  # shown whenever the rule changed something


RULES: dict[str, CompanionRule] = {
    "alone": CompanionRule("혼자"),
    "couple": CompanionRule("연인"),
    "child": CompanionRule(
        "어린이", maxWalkingMinutes=15, indoorFirst=True,
        reason="어린이와 함께라 걷는 시간을 편도 15분 이내로 잡고 실내 시설을 먼저 보여드려요."),
    "parents": CompanionRule(
        "부모님", maxWalkingMinutes=20,
        reason="부모님과 함께라 걷는 시간을 편도 20분 이내로 잡았어요."),
}
NO_RULE = CompanionRule("")


def rule(companions: str | None) -> CompanionRule:
    return RULES.get(companions or "", NO_RULE)
