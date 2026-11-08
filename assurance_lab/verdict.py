"""Three-valued verification verdicts with mandatory reporting discipline.

Handoff §16-17: PASS / FAIL / UNKNOWN, non-compensating aggregation, and every
positive result carries its domain, mechanism, assumptions, bounds and
residual risks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Verdict(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Result:
    obligation: str
    verdict: Verdict
    mechanism: str
    domain: str
    bounds: str = ""
    assumptions: tuple[str, ...] = ()
    residual_risks: tuple[str, ...] = ()
    detail: str = ""
    counterexample: Any = field(default=None, compare=False)

    def label(self) -> str:
        parts = [self.mechanism, self.domain]
        if self.bounds:
            parts.append(self.bounds)
        return f"{self.verdict.value}[{'; '.join(parts)}]"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.obligation}: {self.label()}"


def authorize(results: Iterable[Result], required: Iterable[str]) -> bool:
    """Non-compensating gate: every required obligation must be PASS.

    A required obligation that is missing from ``results`` is treated as
    UNKNOWN, and UNKNOWN never authorizes. A strong result elsewhere cannot
    compensate for a FAIL/UNKNOWN on a required obligation.
    """
    by_name: dict[str, list[Result]] = {}
    for r in results:
        by_name.setdefault(r.obligation, []).append(r)
    for name in required:
        rs = by_name.get(name)
        if not rs:
            return False
        if any(r.verdict is not Verdict.PASS for r in rs):
            return False
    return True
