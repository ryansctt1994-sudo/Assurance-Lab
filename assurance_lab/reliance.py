"""Reliance receipts, descendant closure, BoundaryCompleteness (§13-15)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class Receipt:
    user: str
    dep: str
    evidence_hash: str  # H(X_d)
    scope: Optional[str]  # None = unknown scope
    dep_type: str
    v_c: str
    v_sem: int

    def digest(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()


class Impact(Enum):
    INVALIDATED = "invalidated"
    REVIEW_REQUIRED = "review-required"
    UNAFFECTED = "unaffected"


@dataclass
class RelianceLedger:
    evidence_hash: dict[str, str] = field(default_factory=dict)  # adjudication -> H(X_d)
    declared: set[tuple[str, str]] = field(default_factory=set)  # (user, dep)
    receipts: list[Receipt] = field(default_factory=list)
    uses: list[tuple[str, str]] = field(default_factory=list)  # observed (user, dep)

    def record_use(self, user: str, dep: str, receipt: Optional[Receipt]) -> None:
        """A consumption event. A receipt must bind the dep's current evidence."""
        self.uses.append((user, dep))
        if receipt is not None:
            if receipt.user != user or receipt.dep != dep:
                raise ValueError("receipt does not match the use")
            if receipt.evidence_hash != self.evidence_hash.get(dep):
                raise ValueError("receipt binds stale or unknown evidence")
            self.receipts.append(receipt)

    def audit(self) -> dict[str, list[tuple[str, str]]]:
        """Classify uses.

        * missing_receipt: declared consumer used dep without a receipt →
          internal invariant failure.
        * undeclared: use by an undeclared consumer → BoundaryCompleteness
          alarm (the model does not know this path exists).
        """
        have = {(r.user, r.dep) for r in self.receipts}
        out: dict[str, list[tuple[str, str]]] = {"missing_receipt": [], "undeclared": []}
        for u in self.uses:
            if u not in self.declared:
                out["undeclared"].append(u)
            elif u not in have:
                out["missing_receipt"].append(u)
        return out

    def descendants(self, d: str) -> dict[str, Impact]:
        """Transitive closure over receipts from invalidated adjudication d.

        Direct 'premise' dependence ⇒ INVALIDATED; anything else, including
        unknown scope, fails closed to REVIEW_REQUIRED. Nodes not reached are
        UNAFFECTED (only nodes known to the ledger are reported).
        """
        out: dict[str, Impact] = {}
        frontier = [d]
        visited = {d}
        while frontier:
            n = frontier.pop()
            for r in self.receipts:
                if r.dep != n or r.user in visited:
                    continue
                visited.add(r.user)
                if r.scope is not None and r.dep_type == "premise" and n == d:
                    out[r.user] = Impact.INVALIDATED
                else:
                    out[r.user] = Impact.REVIEW_REQUIRED
                frontier.append(r.user)
        known = {r.user for r in self.receipts} | {r.dep for r in self.receipts}
        for k in known - visited:
            out[k] = Impact.UNAFFECTED
        return out


class BoundaryVerdict(Enum):
    COVERED = "covered"
    ENLARGE_MODEL = "enlarge-model"  # in scope, legitimate external path exists
    UNAUTHORIZED = "unauthorized-effect"  # in scope, no legitimate path
    OUT_OF_SCOPE = "out-of-scope"


def classify_boundary(effect: str, modeled_paths: set[str], in_scope: set[str],
                      legitimate_external: set[str]) -> BoundaryVerdict:
    """§15 triage. Never legitimizes an effect merely by expanding the model."""
    if effect in modeled_paths:
        return BoundaryVerdict.COVERED
    if effect not in in_scope:
        return BoundaryVerdict.OUT_OF_SCOPE
    if effect in legitimate_external:
        return BoundaryVerdict.ENLARGE_MODEL
    return BoundaryVerdict.UNAUTHORIZED
