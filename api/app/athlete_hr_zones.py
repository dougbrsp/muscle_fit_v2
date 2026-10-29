"""Zonas de FC de UM usuário, lidas do banco e resolvidas pelo dono (M98).

Duas responsabilidades, nenhuma delas numérica:

1. `load_athlete_zones`: lê o que o usuário tem gravado (FCmáx manual e as
   zonas do Intervals do cache do sync) e entrega ao dono
   (`domain/hr_zones.resolve_athlete_zones`), que decide a precedência e a
   faixa de cada zona. Quem exibe zonas (perfil, editor do Modelo) passa por
   aqui; ninguém lê a FCmáx e calcula zona por conta própria.
2. `resolve_template_zones_subject`: DE QUEM são as zonas que o editor do
   Modelo mostra. É regra de AUTORIZAÇÃO e mora só aqui (decisão do usuário,
   2026-10-26, correção da C1):

   - admin: as zonas do usuário LOGADO;
   - aluno (`regular`): as próprias, sempre; nunca as de terceiros, mesmo que
     peça um Modelo de outra pessoa;
   - personal trainer: as do ALUNO a quem o Modelo está atribuído (vínculo
     vigente em `workout_plan_template_assignments`). O PT só enxerga aluno
     vinculado ao Modelo pedido: sem Modelo, sem atribuição vigente ou com
     mais de um aluno vigente, não há zonas (nunca as do próprio PT, que
     enganariam a prescrição do aluno).

   O frontend não decide nada disso: só pede as zonas do Modelo aberto.

Sem regra de cálculo (nada de limite de zona aqui). Mapa:
`docs/motores/FONTES_DA_VERDADE.md` §69.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import intervals_store
from .domain import hr_zones

MAX_HR_FIELD = "max_hr_bpm_manual"

ROLE_ADMIN = "admin"
ROLE_PERSONAL_TRAINER = "personal_trainer"

SUBJECT_SELF = "self"
SUBJECT_ASSIGNEE = "assignee"
SUBJECT_NONE = "none"

REASON_NO_TEMPLATE = "no_template"
REASON_NO_ASSIGNEE = "no_assignee"
REASON_MULTIPLE_ASSIGNEES = "multiple_assignees"

_ACTIVE_ASSIGNEES_SQL = """
    SELECT a.user_id, u.full_name, u.username
    FROM workout_plan_template_assignments a
    JOIN users u ON u.id = a.user_id
    WHERE a.template_id = %s AND a.unassigned_at IS NULL
    ORDER BY a.user_id ASC
"""


@dataclass(frozen=True)
class ZonesSubject:
    """De quem são as zonas. `user_id` None = ninguém (sem zonas)."""

    kind: str
    user_id: Optional[int] = None
    name: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "user_id": self.user_id, "name": self.name, "reason": self.reason}


def load_athlete_zones(cur: Any, user_id: int) -> Optional[hr_zones.AthleteZones]:
    """Zonas resolvidas do usuário; None se o usuário não existe.

    `SELECT *`: antes da 096 a coluna da FCmáx não existe e o resultado é
    "sem zonas", nunca erro."""
    cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
    row = cur.fetchone()
    if not row:
        return None
    # M98 C2: zonas do Intervals (cache do sync, migração 097). Qual esporte
    # representa o atleta e o mapeamento 7 -> 5 são do dono.
    reference = hr_zones.pick_reference_sport(intervals_store.read_athlete_hr_zones(cur, user_id))
    intervals_zones = hr_zones.zones_from_intervals(reference["hr_zones_bpm"]) if reference else ()
    return hr_zones.resolve_athlete_zones(
        intervals_zones=intervals_zones,
        manual_max_hr_bpm=row.get(MAX_HR_FIELD),
        intervals_max_hr_bpm=reference.get("max_hr_bpm") if reference else None,
    )


def zones_payload(athlete: Optional[hr_zones.AthleteZones]) -> dict[str, Any]:
    """Forma da API (do dono); sem usuário = sem zonas."""
    resolved = athlete or hr_zones.resolve_athlete_zones()
    return hr_zones.athlete_zones_payload(resolved)


def resolve_template_zones_subject(cur: Any, identity: Any, template_id: Optional[int]) -> ZonesSubject:
    """De quem são as zonas que o editor do Modelo `template_id` mostra para
    `identity` (regra no docstring do módulo)."""
    if identity.role != ROLE_PERSONAL_TRAINER:
        # Admin e aluno: sempre o usuário logado (aluno nunca vê terceiros).
        return ZonesSubject(kind=SUBJECT_SELF, user_id=int(identity.user_id))
    if template_id is None:
        return ZonesSubject(kind=SUBJECT_NONE, reason=REASON_NO_TEMPLATE)
    cur.execute(_ACTIVE_ASSIGNEES_SQL, (int(template_id),))
    assignees = list(cur.fetchall() or [])
    if not assignees:
        return ZonesSubject(kind=SUBJECT_NONE, reason=REASON_NO_ASSIGNEE)
    if len(assignees) > 1:
        return ZonesSubject(kind=SUBJECT_NONE, reason=REASON_MULTIPLE_ASSIGNEES)
    row = assignees[0]
    return ZonesSubject(
        kind=SUBJECT_ASSIGNEE,
        user_id=int(row["user_id"]),
        name=row.get("full_name") or row.get("username"),
    )


__all__ = [
    "REASON_MULTIPLE_ASSIGNEES",
    "REASON_NO_ASSIGNEE",
    "REASON_NO_TEMPLATE",
    "SUBJECT_ASSIGNEE",
    "SUBJECT_NONE",
    "SUBJECT_SELF",
    "ZonesSubject",
    "load_athlete_zones",
    "resolve_template_zones_subject",
    "zones_payload",
]
