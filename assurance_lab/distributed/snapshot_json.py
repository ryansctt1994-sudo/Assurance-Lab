"""JSON form of a frozen replica snapshot, for retained counterexamples.

Events are tagged objects and sets become sorted lists, so a snapshot is
plain data that can be stored, hashed and reviewed. ``decode`` builds the
events with the classes of a *given* replica module, so one counterexample
can be replayed against another revision's code (loaded from git).
"""

from __future__ import annotations

import dataclasses
import json


def _event(e) -> dict:
    d = {"type": type(e).__name__}
    for f in dataclasses.fields(e):
        v = getattr(e, f.name)
        d[f.name] = sorted(v) if isinstance(v, frozenset) else list(v) if isinstance(v, tuple) else v
    return d


def _key(d) -> str:
    return json.dumps(d, sort_keys=True)


def encode(frozen: tuple) -> dict:
    adm, edges, inv, res, rej, pend, seen = frozen
    return {
        "admitted": [[k, _event(v)] for k, v in adm],
        "edges": sorted(list(x) for x in edges),
        "invalidated": sorted(inv),
        "resolutions": [[k, sorted((_event(r) for r in v), key=_key)] for k, v in res],
        "rejected": [[_event(e), why] for e, why in rej],
        "pending": [_event(e) for e in pend],
        "seen": sorted((_event(e) for e in seen), key=_key),
    }


def _decode_event(d: dict, mod):
    kw = {k: v for k, v in d.items() if k != "type"}
    if d["type"] == "Resolve":
        kw["candidates"] = tuple(kw["candidates"])
        kw["approvals"] = frozenset(kw["approvals"])
    return getattr(mod, d["type"])(**kw)


def decode(obj: dict, mod) -> tuple:
    """The frozen 7-tuple, with events built from ``mod``'s classes."""
    ev = lambda d: _decode_event(d, mod)  # noqa: E731
    return (
        tuple((k, ev(v)) for k, v in obj["admitted"]),
        frozenset(tuple(x) for x in obj["edges"]),
        frozenset(obj["invalidated"]),
        tuple((k, frozenset(ev(r) for r in v)) for k, v in obj["resolutions"]),
        tuple((ev(e), why) for e, why in obj["rejected"]),
        tuple(ev(e) for e in obj["pending"]),
        frozenset(ev(e) for e in obj["seen"]),
    )


def encode_events(events) -> list:
    return [_event(e) for e in events]


def decode_events(items, mod) -> list:
    return [_decode_event(d, mod) for d in items]


def load_replica_at(rev: str, repo, tmpdir):
    """Import ``assurance_lab/distributed/replica.py`` as it was at ``rev``
    (read with ``git show``; the module uses only the standard library)."""
    import importlib.util
    import subprocess
    import sys
    from pathlib import Path

    src = subprocess.run(["git", "show", f"{rev}:assurance_lab/distributed/replica.py"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout
    path = Path(tmpdir) / f"replica_at_{rev}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"replica_at_{rev}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod
