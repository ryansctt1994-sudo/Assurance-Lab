"""Admission causal closure (I_2, §6): admission reads only π_A(state).

``run_admission`` hands the admission function a read-logging view that
exposes only the declared projection. Reads outside it raise, and the
observed read-set is bound into the receipt ρ_A.

Strongest justified claim: *diagnostic information was excluded from the
enforced in-process input channel*. Not shown: absence of human knowledge,
training influence, or side channels (time, globals, I/O, closures). A real
deployment needs process/sandbox isolation; this is the logic, not the jail.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable


class ProjectionViolation(Exception):
    pass


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()


class ProjectedView(Mapping):
    """Read-only mapping restricted to ``allowed`` keys, logging every read."""

    def __init__(self, state: Mapping, allowed: frozenset):
        self._data = {k: state[k] for k in allowed if k in state}
        self._allowed = allowed
        self.reads: set[str] = set()

    def __getitem__(self, key):
        if key not in self._allowed:
            raise ProjectionViolation(f"admission read outside π_A: {key!r}")
        self.reads.add(key)
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


@dataclass(frozen=True)
class AdmissionReceipt:
    version: str
    input_manifest: tuple[str, ...]
    observed_reads: tuple[str, ...]
    projection_digest: str
    decision: Any
    environment_policy: str
    rho: str

    def reads_within_projection(self) -> bool:
        return set(self.observed_reads) <= set(self.input_manifest)


def run_admission(fn: Callable[[Mapping], Any], state: Mapping, allowed: frozenset, *,
                  version: str = "A-v1", environment_policy: str = "in-process-view") -> AdmissionReceipt:
    view = ProjectedView(state, allowed)
    decision = fn(view)
    projected = {k: view._data[k] for k in sorted(view._data)}
    manifest = tuple(sorted(allowed))
    reads = tuple(sorted(view.reads))
    pd = _digest(projected)
    rho = _digest([version, manifest, pd, decision, environment_policy])
    return AdmissionReceipt(version, manifest, reads, pd, decision, environment_policy, rho)
