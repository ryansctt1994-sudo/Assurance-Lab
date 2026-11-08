"""Negative controls (§22): each mutant removes or corrupts exactly one guard.

A mutant is *detected* if bounded exploration from Init finds a violation of
Inv, Safety, or a transition property. A clean model that passes while every
mutant is detected shows the properties are not vacuous: the dangerous
transitions are reachable in principle and the checks would see them.
"""

from __future__ import annotations

from .model import CoreModel, State, _set, no_current, superseded


class SupersedeWithoutFreshAdmission(CoreModel):
    """Handoff failure #1: historical admission accepted as successor eligibility."""

    name = "supersede_without_fresh_admission"

    def fresh_admission_ok(self, s: State, y: int) -> bool:
        return True


class ResurrectOnRetract(CoreModel):
    """Retracting a successor restores its predecessor (rollback resurrection)."""

    name = "resurrect_on_retract"

    def retract(self, s: State):
        for lbl, t in super().retract(s):
            y = lbl[1]
            preds = [a for (a, b) in s.edges if b == y and y in s.current]
            yield lbl, t._replace(current=t.current | set(preds))


class SemRewriteOnRoleRevise(CoreModel):
    """RoleRevise silently rewrites the meaning of historical records."""

    name = "sem_rewrite_on_role_revise"

    def role_revise(self, s: State):
        for lbl, t in super().role_revise(s):
            recs = tuple(r._replace(sem=t.sem_now) if r.admitted else r for r in t.recs)
            yield lbl, t._replace(recs=recs)


class BeneficiarySelfApproval(CoreModel):
    """Admission approved by the principal that benefits from it."""

    name = "beneficiary_self_approval"

    def approval_ok(self, s: State, owner: int, approver: int) -> bool:
        return s.levels[approver] >= 1


class SelfGrant(CoreModel):
    """A principal raises its own authority level."""

    name = "self_grant"

    def grant(self, s: State):
        yield from super().grant(s)
        for p in range(self.cfg.n_principals):
            if 1 <= s.levels[p] < self.cfg.max_level:
                yield ("Grant", p, p), s._replace(levels=_set(s.levels, p, s.levels[p] + 1))


class AdjudicateUnadmitted(CoreModel):
    name = "adjudicate_unadmitted"

    def adjudicate(self, s: State):
        yield from super().adjudicate(s)
        for x, r in enumerate(s.recs):
            if not r.admitted and r.adj_sem is None:
                yield ("Adjudicate", x), s._replace(recs=_set(s.recs, x, r._replace(adj_sem=s.sem_now)))


class LineageRewrite(CoreModel):
    name = "lineage_rewrite"

    def extra(self, s: State):
        for x, r in enumerate(s.recs):
            if r.admitted:
                nl = (r.lineage + 1) % self.cfg.n_lineages
                if nl != r.lineage:
                    yield ("Relineage", x, nl), s._replace(recs=_set(s.recs, x, r._replace(lineage=nl)))


class CrossLineageSupersede(CoreModel):
    name = "cross_lineage_supersede"

    def successor_lineage(self, s: State, x: int):
        return list(range(self.cfg.n_lineages))


class StaleSuccessor(CoreModel):
    """Supersede from a record that is no longer current (fork / double head)."""

    name = "stale_successor"

    def supersede_source_ok(self, s: State, x: int) -> bool:
        return s.recs[x].admitted


class AdmitIntoOccupiedLineage(CoreModel):
    """Handoff failure #2: plain admission while the lineage already has a head."""

    name = "admit_into_occupied_lineage"

    def admit_lineage_ok(self, s: State, lin: int) -> bool:
        return True


class InvalidateFallsBack(CoreModel):
    """Invalidation promotes 'some other available candidate' (§3 violation)."""

    name = "invalidate_falls_back"

    def invalidate(self, s: State):
        for lbl, t in super().invalidate(s):
            x = lbl[1]
            preds = [a for (a, b) in s.edges if b == x and x in s.current]
            yield lbl, t._replace(current=t.current | set(preds))


class DiagnosisRewritesAdjudication(CoreModel):
    name = "diagnosis_rewrites_adjudication"

    def diagnose(self, s: State):
        for x, r in enumerate(s.recs):
            if r.admitted:
                if r.adj_sem is not None and self.cfg.n_sem > 1:
                    nr = r._replace(adj_sem=(r.adj_sem + 1) % self.cfg.n_sem)
                    yield ("Diagnose", x), s._replace(recs=_set(s.recs, x, nr))
                else:
                    yield ("Diagnose", x), s


class LatchedLive(CoreModel):
    """Supersede leaves the predecessor's head flag latched (Live not re-derived)."""

    name = "latched_live"

    def supersede(self, s: State):
        for lbl, t in super().supersede(s):
            yield lbl, t._replace(current=t.current | {lbl[1]})


# --- negative controls added from the property-coverage matrix -----------
# Each targets a property that no earlier mutant showed to be load-bearing.


class UnboundedGrant(CoreModel):
    """Authority can be granted one step past the top level by an equal peer
    (TypeOK). Bounded at max_level+1 so the mutant's graph stays finite."""

    name = "unbounded_grant"

    def grant(self, s: State):
        for p in range(self.cfg.n_principals):
            for q in range(self.cfg.n_principals):
                if q != p and s.levels[q] >= s.levels[p] and s.levels[p] <= self.cfg.max_level:
                    yield ("Grant", p, q), s._replace(levels=_set(s.levels, p, s.levels[p] + 1))


class AdmitUnauthorizedApprover(CoreModel):
    """An independent but unauthorized principal approves (ApproverAuthorized)."""

    name = "admit_unauthorized_approver"

    def approval_ok(self, s: State, owner: int, approver: int) -> bool:
        return approver != owner


class RoleReviseRollback(CoreModel):
    """The semantic version can move backwards (SemBound; I_5)."""

    name = "role_revise_rollback"

    def role_revise(self, s: State):
        yield from super().role_revise(s)
        if s.sem_now > 0:
            yield ("RoleRevise", s.sem_now - 1), s._replace(sem_now=s.sem_now - 1)


class AdmitWithoutHead(CoreModel):
    """Admission leaves a live record that is not current: an ambiguous
    lineage that a later admission can silently fill (NoOrphanLive)."""

    name = "admit_without_head"

    def admit(self, s: State):
        for lbl, t in super().admit(s):
            yield lbl, t._replace(current=s.current)


class DiagnosisRevisesRoles(CoreModel):
    """Diagnosis silently bumps the semantic version: a meaning change
    without an explicit ROLE_REVISION (DiagnosisInert; I_5)."""

    name = "diagnosis_revises_roles"

    def diagnose(self, s: State):
        for lbl, t in super().diagnose(s):
            if s.sem_now + 1 < self.cfg.n_sem:
                yield lbl, t._replace(sem_now=s.sem_now + 1)
            else:
                yield lbl, t


class Unretract(CoreModel):
    """Retraction is rolled back by clearing the flag; the record is current
    again. Every *state* invariant still holds afterwards: only the step
    properties can see it (rollback resurrection, §10)."""

    name = "unretract"

    def extra(self, s: State):
        for x, r in enumerate(s.recs):
            if r.retracted and not r.invalidated and not superseded(s, x) and no_current(s, r.lineage):
                yield ("Unretract", x), s._replace(recs=_set(s.recs, x, r._replace(retracted=False)),
                                                   current=s.current | {x})


class CompactionDropsEdge(CoreModel):
    """Compaction discards the supersession edge out of an invalidated
    record: "dead" history, so no state invariant notices (§25)."""

    name = "compaction_drops_edge"

    def extra(self, s: State):
        for a, b in sorted(s.edges):
            if s.recs[a].invalidated:
                yield ("Compact", a, b), s._replace(edges=s.edges - {(a, b)})


ALL_MUTANTS = [
    SupersedeWithoutFreshAdmission,
    ResurrectOnRetract,
    SemRewriteOnRoleRevise,
    BeneficiarySelfApproval,
    SelfGrant,
    AdjudicateUnadmitted,
    LineageRewrite,
    CrossLineageSupersede,
    StaleSuccessor,
    AdmitIntoOccupiedLineage,
    InvalidateFallsBack,
    DiagnosisRewritesAdjudication,
    LatchedLive,
    UnboundedGrant,
    AdmitUnauthorizedApprover,
    RoleReviseRollback,
    AdmitWithoutHead,
    DiagnosisRevisesRoles,
    Unretract,
    CompactionDropsEdge,
]
