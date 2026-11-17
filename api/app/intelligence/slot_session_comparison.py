"""Comparativo T-0..T-3 de `/volume-grupamentos` pelo MESMO slot do Modelo.

Dono da regra (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.5, "Comparativo
T-0..T-3 por slot"). Python puro: nao le banco, nao conhece rota.

Pedido do usuario (2026-10-23): o T-0 de um musculo pode estar no slot 1 da
ficha e o T-1 no slot 5; comparar os dois mistura dias diferentes do Modelo. A
comparacao passa a ser entre treinos do grupo NA MESMA POSICAO da ficha, e a
posicao e identificada por ID, nunca pelo nome escrito no slot.

## Regra

- T-0 = a sessao mais recente em que a chave (grupo, grupo pai ou macro-grupo)
  aparece - a mesma de antes.
- T-1..T-3 = as sessoes ANTERIORES da mesma chave cuja ficha estava no MESMO
  slot do T-0, da mais recente para a mais antiga, ate completar
  `SLOT_COMPARISON_SESSIONS`. Sessao de outro slot nunca completa a coluna:
  com menos sessoes no slot, as colunas que faltam ficam vazias (`None`).
- T-0 sem slot (treino livre, ficha sem Modelo, sessao sem ficha): so o T-0,
  status `STATUS_NO_SLOT`; nenhuma sessao entra na comparacao. Sessao sem slot
  tambem nunca entra como T-1..T-3 de um T-0 com slot.

## Identidade do slot

`SlotKey = (template_id, slot_sequence)`: o `workout_plans.template_id` +
`template_slot_sequence` gravados na ficha da sessao (026 + 009), o MESMO
vinculo que a aderencia usa (`dashboard/adherence_service.py`, "slot do
template, nao string de nome"). O `workout_plan_template_slots.id` NAO serve de
chave: cada versao publicada do Modelo grava slots com ids novos (065,
`template_versions.insert_version_slots`), entao o T-1 de antes de uma edicao
teria outro id. `template_id` e estavel entre versoes (065) e a `sequence`
e a posicao do slot, unica por versao (`uq_wpts_version_sequence`). O nome do
slot e so rotulo de exibicao.

## Linhagem do Modelo (pedido do usuario 2026-10-24)

Duplicar um Modelo (`POST /workout-plan-templates/{id}/duplicate`) cria OUTRO
`template_id` com a mesma grade; a copia grava a origem em
`workout_plan_templates.source_template_id` (090). A identidade do slot passa a
ser o Modelo RAIZ da linhagem + a posicao: `SessionSlot.key =
(lineage_template_id, slot_sequence)`. Assim o 36 (copia do 34) e o 34 contam
como o mesmo Modelo no comparativo T-0..T-3 e nos grupos de `/dashboard/treinos`
(uma regra so para "mesmo slot"). A raiz e resolvida aqui
(`lineage_root_of`/`resolve_lineage_roots`) a partir do mapa `copia -> origem`
que o SQL traz pronto (`template_lineage_reader`); o SQL nao percorre a cadeia.
Protecoes: ciclo (dado corrompido) = o menor id do ciclo, para todos os membros
concordarem; cadeia mais longa que `MAX_TEMPLATE_LINEAGE_DEPTH` = o proprio
Modelo (sem linhagem, o comportamento anterior). Limite conhecido: a copia
editada depois (slots reordenados) segue casando pela `sequence`.

## Agrupar por (macro-grupo, grupo pai, grupo)

A mesma regra vale em todo nivel: as linhas da sessao que caem na mesma chave
sao somadas ANTES da escolha das sessoes (volume, series e series equivalentes
somam; RPE medio ponderado pelas series). O T-0 de um macro-grupo e a sessao
mais recente com qualquer grupo dele, e o slot e o dessa sessao.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Mapping, Optional

from .volume_comparison import (
    RPE_STABLE_TOLERANCE,
    TREND_NO_TOLERANCE,
    VOLUME_STABLE_TOLERANCE,
    granularity_windows,
    period_delta,
    period_direction,
    week_start_of,
)

# Quantas sessoes do mesmo slot o comparativo mostra (T-0..T-3).
SLOT_COMPARISON_SESSIONS = 4

# Chaves das colunas, do T-0 (mais recente) para o T-3.
PERIOD_KEYS: tuple[str, ...] = tuple(f"t{index}" for index in range(SLOT_COMPARISON_SESSIONS))

# Status da linha: T-0 com slot (as colunas anteriores sao do mesmo slot) ou
# T-0 sem slot (nao ha com o que comparar).
STATUS_SAME_SLOT = "same_slot"
STATUS_NO_SLOT = "no_slot"

# Niveis do seletor "Agrupar por" (#566), com a mesma chave que a tela usa.
LEVEL_VOLUME_CLASS = "volume_class"
LEVEL_PARENT_GROUP = "parent_group"
LEVEL_MUSCLE_GROUP = "muscle_group"
GROUPING_LEVELS: tuple[str, ...] = (LEVEL_VOLUME_CLASS, LEVEL_PARENT_GROUP, LEVEL_MUSCLE_GROUP)

# Grupo sem nome na linha (mesmo fallback da leitura de volume e da tela).
UNNAMED_GROUP_LABEL = "Outros"
NO_EQUIPMENT_LABEL = "Sem equipamento"  # desempate da ordem; o payload segue com `equipment_name: null`

SlotKey = tuple[int, int]

# Teto de saltos ao subir a linhagem `copia -> origem` (cadeias reais tem 1-2
# saltos; o teto so protege contra dado corrompido).
MAX_TEMPLATE_LINEAGE_DEPTH = 32


def lineage_root_of(template_id: int, parents: Mapping[int, Optional[int]]) -> int:
    """Modelo raiz da linhagem de `template_id`. `parents`: `id -> origem`
    (so os Modelos com origem precisam estar no mapa; origem fora do mapa =
    raiz, inclusive quando ela foi apagada fisicamente)."""
    current = int(template_id)
    seen = [current]
    for _ in range(MAX_TEMPLATE_LINEAGE_DEPTH):
        parent = parents.get(current)
        if parent is None:
            return current
        parent = int(parent)
        if parent in seen:
            # Ciclo: todos os membros respondem a mesma raiz.
            return min(seen[seen.index(parent):])
        seen.append(parent)
        current = parent
    # Ate `MAX_TEMPLATE_LINEAGE_DEPTH` saltos; alem disso, sem linhagem.
    return current if parents.get(current) is None else int(template_id)


def resolve_lineage_roots(
    template_ids: Iterable[Optional[int]], parents: Mapping[int, Optional[int]]
) -> dict[int, int]:
    """`template_id -> raiz da linhagem` de cada Modelo pedido (None ignorado)."""
    return {
        int(template_id): lineage_root_of(int(template_id), parents)
        for template_id in template_ids
        if template_id is not None
    }


@dataclass(frozen=True)
class SessionSlot:
    """Slot da ficha de uma sessao. `slot_id`/`slot_name`/`template_name` sao
    da versao que governa a ficha e servem so para exibir. `lineage_template_id`
    e a raiz da linhagem do Modelo (`lineage_root_of`); sem ela, o proprio
    Modelo."""

    template_id: int
    slot_sequence: int
    slot_id: Optional[int] = None
    slot_name: Optional[str] = None
    template_name: Optional[str] = None
    lineage_template_id: Optional[int] = None

    @property
    def lineage_id(self) -> int:
        return self.lineage_template_id if self.lineage_template_id is not None else self.template_id

    @property
    def key(self) -> SlotKey:
        return (self.lineage_id, self.slot_sequence)

    @property
    def label(self) -> str:
        name = (self.slot_name or "").strip()
        return name or f"Slot {self.slot_sequence}"

    def as_payload(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "slot_sequence": self.slot_sequence,
            "slot_id": self.slot_id,
            "slot_name": self.slot_name,
            "template_name": self.template_name,
            "lineage_template_id": self.lineage_id,
            "label": self.label,
        }


def session_slot_from_row(
    row: Mapping[str, Any], lineage_roots: Optional[Mapping[int, int]] = None
) -> Optional[SessionSlot]:
    """Slot de uma linha `{template_id, slot_sequence, slot_id, slot_name,
    template_name}`; `None` quando a ficha nao tem Modelo ou posicao.
    `lineage_roots` (`resolve_lineage_roots`) da a raiz da linhagem; Modelo
    fora do mapa = raiz de si mesmo."""
    template_id = row.get("template_id")
    slot_sequence = row.get("slot_sequence")
    if template_id is None or slot_sequence is None:
        return None
    slot_id = row.get("slot_id")
    lineage = (lineage_roots or {}).get(int(template_id))
    return SessionSlot(
        template_id=int(template_id),
        slot_sequence=int(slot_sequence),
        slot_id=int(slot_id) if slot_id is not None else None,
        slot_name=row.get("slot_name"),
        template_name=row.get("template_name"),
        lineage_template_id=int(lineage) if lineage is not None else None,
    )


# ---------------------------------------------------------------------------
# Grupo da sessao em `/dashboard/treinos` (pedido do usuario 2026-10-24)
# ---------------------------------------------------------------------------
#
# A lista de sessoes agrupa pela MESMA identidade de slot do comparativo
# (`SessionSlot.key`): renomear o Modelo ou o titulo do slot nao parte o grupo.
# Fallback de quem nao tem slot:
# - ficha salva sem Modelo (legado anterior ao vinculo 026, ficha avulsa) ou
#   ficha de Modelo sem posicao: UM grupo so, "Fichas sem Modelo". Agrupar por
#   `workout_plan_id` dava um grupo de 1 sessao por ficha, com rotulos
#   repetidos; agrupar pelo nome traria o defeito antigo de volta;
# - sessao sem ficha nenhuma: UM grupo so, "Treinos sem ficha".
GROUP_KIND_SLOT = "slot"
GROUP_KIND_NO_TEMPLATE = "no_template"
GROUP_KIND_UNLINKED = "unlinked"

NO_TEMPLATE_GROUP_KEY = "no_template"
UNLINKED_GROUP_KEY = "unlinked"
NO_TEMPLATE_GROUP_LABEL = "Fichas sem Modelo"
UNLINKED_GROUP_LABEL = "Treinos sem ficha"


def slot_group_key(slot: SessionSlot) -> str:
    """Chave textual de `SessionSlot.key` (Modelo raiz da linhagem + posicao)
    para o payload."""
    lineage_id, slot_sequence = slot.key
    return f"slot:{lineage_id}:{slot_sequence}"


def session_group_of(
    *,
    workout_plan_id: Optional[int],
    template_id: Optional[int],
    slot_sequence: Optional[int],
    slot_name: Optional[str] = None,
    template_name: Optional[str] = None,
    lineage_roots: Optional[Mapping[int, int]] = None,
) -> dict[str, Any]:
    """Grupo de UMA sessao na lista de `/dashboard/treinos`.

    `slot_name` e `template_name` sao so rotulo (os da versao/cadastro que a
    leitura resolveu); a chave nunca depende deles. `lineage_roots`
    (`resolve_lineage_roots`): a copia de um Modelo cai no grupo da origem;
    `lineage_template_id` diz a linhagem (a tela ordena os grupos por ela) e
    `template_id`/`template_name` sao os do Modelo DESTA sessao (a tela exibe
    os da sessao mais recente do grupo)."""
    slot = session_slot_from_row(
        {
            "template_id": template_id,
            "slot_sequence": slot_sequence,
            "slot_name": slot_name,
            "template_name": template_name,
        },
        lineage_roots,
    )
    if slot is not None:
        return {
            "key": slot_group_key(slot),
            "kind": GROUP_KIND_SLOT,
            "label": slot.label,
            "template_id": slot.template_id,
            "template_name": slot.template_name,
            "lineage_template_id": slot.lineage_id,
            "slot_sequence": slot.slot_sequence,
        }
    if workout_plan_id is not None:
        return {
            "key": NO_TEMPLATE_GROUP_KEY,
            "kind": GROUP_KIND_NO_TEMPLATE,
            "label": NO_TEMPLATE_GROUP_LABEL,
            "template_id": None,
            "template_name": None,
            "lineage_template_id": None,
            "slot_sequence": None,
        }
    return {
        "key": UNLINKED_GROUP_KEY,
        "kind": GROUP_KIND_UNLINKED,
        "label": UNLINKED_GROUP_LABEL,
        "template_id": None,
        "template_name": None,
        "lineage_template_id": None,
        "slot_sequence": None,
    }


def grouping_key_of(row: Mapping[str, Any], level: str) -> dict[str, Any]:
    """Chave da linha no nivel: `{key, label, order}`, com os campos de
    hierarquia ja resolvidos pelo dono (`methodology_volume.resolve_group_hierarchy`).
    Sem o campo do nivel, a linha fica no proprio grupo."""
    if level == LEVEL_PARENT_GROUP and row.get("parent_group_name"):
        parent_id = row.get("parent_group_id")
        key = f"pai:{parent_id}" if parent_id is not None else f"pai:{row['parent_group_name']}"
        return {"key": key, "label": row["parent_group_name"], "order": None}
    if level == LEVEL_VOLUME_CLASS and row.get("volume_class_name"):
        class_id = row.get("volume_class_id")
        key = f"macro:{class_id}" if class_id is not None else "macro:sem-classe"
        return {"key": key, "label": row["volume_class_name"], "order": row.get("volume_class_order")}
    name = row.get("muscle_group") or UNNAMED_GROUP_LABEL
    return {"key": f"grupo:{name}", "label": name, "order": None}


def _sort_key_group(group: Mapping[str, Any]) -> tuple:
    order = group.get("order")
    return (order is None, order if order is not None else 0, str(group.get("label") or ""))


def select_slot_sessions(
    sessions: Iterable[Mapping[str, Any]],
    slot_of: Callable[[int], Optional[SessionSlot]],
) -> tuple[Optional[SessionSlot], list[Optional[Mapping[str, Any]]]]:
    """Escolhe T-0..T-3 de UMA chave. `sessions`: um item por sessao, com
    `activity_id` e `local_date`. Devolve o slot do T-0 (ou `None`) e a lista
    de `SLOT_COMPARISON_SESSIONS` posicoes (`None` = sem sessao no slot)."""
    ordered = sorted(sessions, key=lambda s: (str(s["local_date"]), int(s["activity_id"])), reverse=True)
    picked: list[Optional[Mapping[str, Any]]] = [None] * SLOT_COMPARISON_SESSIONS
    if not ordered:
        return None, picked
    latest = ordered[0]
    picked[0] = latest
    slot = slot_of(int(latest["activity_id"]))
    if slot is None:
        return None, picked
    index = 1
    for session in ordered[1:]:
        if index >= SLOT_COMPARISON_SESSIONS:
            break
        other = slot_of(int(session["activity_id"]))
        if other is not None and other.key == slot.key:
            picked[index] = session
            index += 1
    return slot, picked


def _comparison_rows(
    rows: Iterable[Mapping[str, Any]],
    level: str,
    slots_by_activity: Mapping[int, SessionSlot],
    accumulate: Callable[[dict[str, Any], Mapping[str, Any]], None],
    finish: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    by_group: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = grouping_key_of(row, level)
        entry = by_group.setdefault(group["key"], {"group": group, "sessions": {}})
        activity_id = int(row["activity_id"])
        session = entry["sessions"].get(activity_id)
        if session is None:
            session = {"activity_id": activity_id, "local_date": _iso(row.get("local_date"))}
            entry["sessions"][activity_id] = session
        accumulate(session, row)

    result = []
    for entry in by_group.values():
        slot, picked = select_slot_sessions(entry["sessions"].values(), slots_by_activity.get)
        result.append(
            {
                "group": entry["group"],
                "comparison_status": STATUS_SAME_SLOT if slot is not None else STATUS_NO_SLOT,
                "slot": slot.as_payload() if slot is not None else None,
                "by_period": {
                    key: (finish(dict(session)) if session is not None else None)
                    for key, session in zip(PERIOD_KEYS, picked)
                },
            }
        )
    return sorted(result, key=lambda item: _sort_key_group(item["group"]))


def _iso(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _accumulate_volume(session: dict[str, Any], row: Mapping[str, Any]) -> None:
    sets_count = int(row.get("sets_count") or 0)
    session["volume_kg"] = session.get("volume_kg", 0.0) + float(row.get("volume_kg") or 0.0)
    session["sets_count"] = session.get("sets_count", 0) + sets_count
    if row.get("avg_rpe") is not None:
        session["_rpe_sum"] = session.get("_rpe_sum", 0.0) + float(row["avg_rpe"]) * sets_count
        session["_rpe_sets"] = session.get("_rpe_sets", 0) + sets_count


def _finish_volume(session: dict[str, Any]) -> dict[str, Any]:
    rpe_sets = session.pop("_rpe_sets", 0)
    rpe_sum = session.pop("_rpe_sum", 0.0)
    session.setdefault("volume_kg", 0.0)
    session.setdefault("sets_count", 0)
    session["avg_rpe"] = round(rpe_sum / rpe_sets, 2) if rpe_sets > 0 else None
    return session


def _accumulate_equivalent_sets(session: dict[str, Any], row: Mapping[str, Any]) -> None:
    session["equivalent_sets"] = session.get("equivalent_sets", 0.0) + float(row.get("equivalent_sets") or 0.0)


def _finish_equivalent_sets(session: dict[str, Any]) -> dict[str, Any]:
    session["equivalent_sets"] = round(session.get("equivalent_sets", 0.0), 2)
    return session


# Grafico "T-0 vs T-1": os grupos mais treinados em T-0 (cabe no mobile).
TOP_VOLUME_T0_LIMIT = 10


def _value(period: Optional[Mapping[str, Any]], field: str) -> Optional[float]:
    return None if period is None else period.get(field)


def _annotate_periods(rows: list[dict[str, Any]], annotate: Callable[[dict, Mapping], None]) -> list[dict[str, Any]]:
    """Cada periodo `t<k>` contra o imediatamente mais antigo `t<k+1>`
    (`t3` e periodo sem vizinho ficam `None` nos campos novos)."""
    for row in rows:
        periods = row["by_period"]
        for index, key in enumerate(PERIOD_KEYS):
            current = periods.get(key)
            if current is None:
                continue
            older_key = PERIOD_KEYS[index + 1] if index + 1 < len(PERIOD_KEYS) else None
            annotate(current, periods.get(older_key) if older_key else None)
    return rows


def _annotate_volume(current: dict[str, Any], previous: Optional[Mapping[str, Any]]) -> None:
    current["volume_delta_kg"] = period_delta(current.get("volume_kg"), _value(previous, "volume_kg"))
    current["volume_direction"] = period_direction(
        current.get("volume_kg"), _value(previous, "volume_kg"), VOLUME_STABLE_TOLERANCE
    )
    current["rpe_direction"] = period_direction(
        current.get("avg_rpe"), _value(previous, "avg_rpe"), RPE_STABLE_TOLERANCE
    )


def _annotate_equivalent_sets(current: dict[str, Any], previous: Optional[Mapping[str, Any]]) -> None:
    current["equivalent_sets_delta"] = period_delta(
        current.get("equivalent_sets"), _value(previous, "equivalent_sets")
    )
    current["sets_direction"] = period_direction(
        current.get("equivalent_sets"), _value(previous, "equivalent_sets"), VOLUME_STABLE_TOLERANCE
    )


def _volume_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo da pagina (antes calculado na tela de volume por grupamento)."""
    t0_rows = [row["by_period"]["t0"] for row in rows if row["by_period"].get("t0") is not None]
    # Decisao do usuario (17/11/2026): RPE medio de T-0 PONDERADO pelas series de
    # cada grupo; grupo sem RPE fica fora, mesmo arredondamento de `_finish_volume`.
    rated = [(p["avg_rpe"], p.get("sets_count") or 0) for p in t0_rows if p.get("avg_rpe") is not None]
    rated_sets = sum(sets for _, sets in rated)
    return {
        "by_period": {
            key: sum((_value(row["by_period"].get(key), "volume_kg") or 0.0) for row in rows)
            for key in PERIOD_KEYS
        },
        "groups_with_t0": len(t0_rows),
        "groups_tracked": len(rows),
        "avg_rpe_t0": round(sum(rpe * sets for rpe, sets in rated) / rated_sets, 2) if rated_sets > 0 else None,
    }


def _top_volume_t0(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top `TOP_VOLUME_T0_LIMIT` por volume T-0 (desc, ordem estavel); periodo
    ausente conta 0, como na tela."""
    items = [
        {
            "group": row["group"],
            "t0": _value(row["by_period"].get("t0"), "volume_kg") or 0.0,
            "t1": _value(row["by_period"].get("t1"), "volume_kg") or 0.0,
        }
        for row in rows
    ]
    return sorted(items, key=lambda item: -item["t0"])[:TOP_VOLUME_T0_LIMIT]


def _level_payload(volume: list[dict[str, Any]], equivalent_sets: list[dict[str, Any]]) -> dict[str, Any]:
    volume = _annotate_periods(volume, _annotate_volume)
    return {
        "volume": volume,
        "equivalent_sets": _annotate_periods(equivalent_sets, _annotate_equivalent_sets),
        "totals": _volume_totals(volume),
        "top_volume_t0": _top_volume_t0(volume),
    }


def build_slot_comparison(
    volume_rows: Iterable[Mapping[str, Any]],
    equivalent_sets_rows: Iterable[Mapping[str, Any]],
    slots_by_activity: Mapping[int, SessionSlot],
) -> dict[str, Any]:
    """Comparativo pronto para a tela, nos tres niveis do seletor.

    - `volume_rows`: linhas de `VolumeService.get_recent_exercise_volume`
      (volume bruto, series, RPE por sessao e grupo) com a hierarquia anotada;
    - `equivalent_sets_rows`: linhas de `get_equivalent_sets_by_activity`
      (series equivalentes por sessao e grupo) com a hierarquia anotada;
    - `slots_by_activity`: slot de cada sessao (sessao ausente = sem slot).

    Cada fonte escolhe as proprias sessoes (um grupo pode ter series
    equivalentes numa sessao em que so foi secundario), com a mesma regra."""
    volume_rows = list(volume_rows)
    equivalent_sets_rows = list(equivalent_sets_rows)
    return {
        "sessions_per_slot": SLOT_COMPARISON_SESSIONS,
        "period_keys": list(PERIOD_KEYS),
        "levels": {
            level: _level_payload(
                _comparison_rows(volume_rows, level, slots_by_activity, _accumulate_volume, _finish_volume),
                _comparison_rows(
                    equivalent_sets_rows,
                    level,
                    slots_by_activity,
                    _accumulate_equivalent_sets,
                    _finish_equivalent_sets,
                ),
            )
            for level in GROUPING_LEVELS
        },
    }


# ---------------------------------------------------------------------------
# Comparativo semanal e tendencia atual x anterior (#188, etapa 3)
# ---------------------------------------------------------------------------
#
# Antes somados na tela (`comparativeWeekRows` e `repsComparison` de
# `VolumeGrupamentosClient.tsx`); mesma base, mesma conta, mesmos limiares.


def _day_of(value: Any) -> date:
    return date.fromisoformat(_iso(value)[:10])


def _finish_week(week: dict[str, Any]) -> dict[str, Any]:
    # Sem arredondar o RPE: a seta usa o valor cru, como a tela fazia.
    rpe_sets = week.pop("_rpe_sets", 0)
    rpe_sum = week.pop("_rpe_sum", 0.0)
    week.setdefault("volume_kg", 0.0)
    week.setdefault("sets_count", 0)
    week["avg_rpe"] = rpe_sum / rpe_sets if rpe_sets > 0 else None
    return week


def _weekly_rows(rows: list[Mapping[str, Any]], level: str) -> list[dict[str, Any]]:
    by_group: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = grouping_key_of(row, level)
        entry = by_group.setdefault(group["key"], {"group": group, "weeks": {}})
        week_start = week_start_of(_day_of(row.get("local_date"))).isoformat()
        week = entry["weeks"].setdefault(week_start, {"week_start": week_start})
        _accumulate_volume(week, row)

    result = []
    for entry in by_group.values():
        # As semanas mais recentes EM QUE O GRUPO APARECE (semana sem treino e pulada).
        recent = sorted(entry["weeks"].values(), key=lambda week: week["week_start"], reverse=True)
        picked: list[Optional[dict[str, Any]]] = [_finish_week(week) for week in recent[: len(PERIOD_KEYS)]]
        picked += [None] * (len(PERIOD_KEYS) - len(picked))
        result.append({"group": entry["group"], "by_period": dict(zip(PERIOD_KEYS, picked))})
    return _annotate_periods(sorted(result, key=lambda item: _sort_key_group(item["group"])), _annotate_volume)


def build_weekly_comparison(volume_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Comparativo das ultimas semanas por nivel: volume somado por grupo x
    semana (segunda), RPE ponderado por `sets_count` (RPE nulo nao entra), as
    `len(PERIOD_KEYS)` semanas mais recentes do grupo em `t0..t3`, cada uma
    contra a imediatamente mais antiga. `volume_rows`: as linhas de
    `VolumeService.get_recent_exercise_volume` com a hierarquia anotada."""
    rows = list(volume_rows)
    return {
        "period_keys": list(PERIOD_KEYS),
        "levels": {level: _weekly_rows(rows, level) for level in GROUPING_LEVELS},
    }


def _annotate_equipment(current: dict[str, Any], previous: Optional[Mapping[str, Any]]) -> None:
    # Setas da tela: series e ultimo peso, tolerancia de volume (epsilon 0.0001).
    current["sets_delta"] = period_delta(current.get("sets_count"), _value(previous, "sets_count"))
    current["sets_direction"] = period_direction(
        current.get("sets_count"), _value(previous, "sets_count"), VOLUME_STABLE_TOLERANCE
    )
    current["last_weight_delta_kg"] = period_delta(current.get("last_weight_kg"), _value(previous, "last_weight_kg"))
    current["last_weight_direction"] = period_direction(
        current.get("last_weight_kg"), _value(previous, "last_weight_kg"), VOLUME_STABLE_TOLERANCE
    )


def _equipment_total(equipments: list[dict[str, Any]]) -> dict[str, Any]:
    by_period: dict[str, Optional[dict[str, Any]]] = {}
    for key in PERIOD_KEYS:
        present = [eq["by_period"][key] for eq in equipments if eq["by_period"][key] is not None]
        by_period[key] = (
            {
                "week_start": present[0]["week_start"],
                "sets_count": sum(p["sets_count"] for p in present),
                "volume_kg": sum(p["volume_kg"] for p in present),
                # Decisao do usuario (17/11/2026): a MAIOR ultima carga entre os
                # aparelhos na semana (nenhuma carga -> None), nunca a soma.
                "last_weight_kg": max(
                    (p["last_weight_kg"] for p in present if p["last_weight_kg"] is not None), default=None
                ),
            }
            if present
            else None
        )
    return {"equipment_name": None, "is_total": True, "by_period": by_period}


def _equipment_rows(rows: list[Mapping[str, Any]], level: str) -> list[dict[str, Any]]:
    by_group: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = grouping_key_of(row, level)
        entry = by_group.setdefault(group["key"], {"group": group, "weeks": set(), "equipments": {}})
        week_start = week_start_of(_day_of(row.get("local_date"))).isoformat()
        entry["weeks"].add(week_start)
        # Sem equipamento ("" ou nulo) e uma linha so; o rotulo e da apresentacao.
        weeks = entry["equipments"].setdefault(row.get("equipment_name") or None, {})
        agg = weeks.setdefault(
            week_start,
            {"week_start": week_start, "sets_count": 0, "volume_kg": 0.0, "last_weight_kg": None, "_order": None},
        )
        agg["sets_count"] += int(row.get("sets_count") or 0)
        agg["volume_kg"] += float(row.get("volume_kg") or 0.0)
        order = (_iso(row.get("local_date")), int(row["activity_id"]))
        if agg["_order"] is None or order >= agg["_order"]:  # ultima atividade da semana
            agg["_order"] = order
            agg["last_weight_kg"] = row.get("last_weight_kg")

    result = []
    for entry in by_group.values():
        # As 4 semanas mais recentes EM QUE O GRUPO APARECE (qualquer equipamento).
        top_weeks = sorted(entry["weeks"], reverse=True)[: len(PERIOD_KEYS)]
        equipments = []
        for name, weeks in entry["equipments"].items():
            by_period = {}
            for index, key in enumerate(PERIOD_KEYS):
                week = weeks.get(top_weeks[index]) if index < len(top_weeks) else None
                by_period[key] = None if week is None else {k: v for k, v in week.items() if k != "_order"}
            if any(by_period.values()):
                equipments.append({"equipment_name": name, "is_total": False, "by_period": by_period})
        # Mais series nas 4 semanas primeiro; empate pelo rotulo (ausente = "Sem equipamento"), sem caixa.
        equipments.sort(
            key=lambda eq: (
                -sum((p or {}).get("sets_count", 0) for p in eq["by_period"].values()),
                (eq["equipment_name"] or NO_EQUIPMENT_LABEL).casefold(),
            )
        )
        lines = _annotate_periods([_equipment_total(equipments), *equipments], _annotate_equipment)
        result.append({"group": entry["group"], "equipments": lines})
    return sorted(result, key=lambda item: _sort_key_group(item["group"]))


def build_equipment_weekly_comparison(volume_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Comparativo por equipamento nas 4 semanas mais recentes de cada grupo:
    series e volume somados por grupo x equipamento x semana, ultimo peso da
    atividade mais recente da semana, linha total por grupo (a primeira).
    Antes somado na tela (`equipmentComparativeRows`)."""
    rows = list(volume_rows)
    return {
        "period_keys": list(PERIOD_KEYS),
        "levels": {level: _equipment_rows(rows, level) for level in GROUPING_LEVELS},
    }


def _heatmap_level(rows: list[Mapping[str, Any]], level: str) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = grouping_key_of(row, level)
        cell = cells.setdefault(group["key"], {"group": group, "sets_count": 0, "volume_kg": 0.0})
        cell["sets_count"] += int(row.get("sets_count") or 0)
        cell["volume_kg"] += float(row.get("volume_kg") or 0.0)
    ordered = sorted(cells.values(), key=lambda cell: -cell["sets_count"])
    volumes = [cell["volume_kg"] for cell in ordered]
    low, high = (min(volumes), max(volumes)) if volumes else (0.0, 0.0)
    for cell in ordered:
        cell["avg_weight_kg"] = cell["volume_kg"] / cell["sets_count"] if cell["sets_count"] > 0 else None
        # Posicao min-max do volume (0 a 1); todos iguais = 0, como a tela.
        cell["volume_ratio"] = min(1.0, max(0.0, (cell["volume_kg"] - low) / (high - low))) if high > low else 0.0
    return {"min_volume_kg": low, "max_volume_kg": high, "cells": ordered}


def build_heatmap(volume_rows: Iterable[Mapping[str, Any]], activity_id: Optional[int] = None) -> dict[str, Any]:
    """Mapa de calor por nivel: series e volume por grupo, peso medio
    aproximado (volume/series), ordem por series e posicao normalizada do
    volume. `activity_id`: so aquela sessao. Antes calculado na tela
    (`heatmapCells`/`heatmapVolumeRange`)."""
    rows = [row for row in volume_rows if activity_id is None or int(row["activity_id"]) == int(activity_id)]
    return {"levels": {level: _heatmap_level(rows, level) for level in GROUPING_LEVELS}}


def _period_rows(items: list[Mapping[str, Any]], level: str, windows: Mapping[str, Mapping[str, date]]) -> list[dict]:
    current, previous = windows["current"], windows["previous"]
    by_group: dict[str, dict[str, Any]] = {}
    for item in items:
        group = grouping_key_of(item, level)
        # O grupo entra mesmo sem item nas janelas (zeros), como na tela.
        row = by_group.setdefault(
            group["key"],
            {"group": group, "current_reps": 0, "previous_reps": 0, "current_volume_kg": 0.0, "previous_volume_kg": 0.0},
        )
        day = _day_of(item.get("period"))
        if current["from"] <= day <= current["to"]:
            side = "current"
        elif previous["from"] <= day <= previous["to"]:
            side = "previous"
        else:
            continue
        row[f"{side}_reps"] += int(float(item.get("reps_total") or 0))
        row[f"{side}_volume_kg"] += float(item.get("volume_kg") or 0.0)

    result = []
    for row in by_group.values():
        row["reps_delta"] = row["current_reps"] - row["previous_reps"]
        row["volume_delta_kg"] = period_delta(row["current_volume_kg"], row["previous_volume_kg"])
        row["volume_direction"] = period_direction(
            row["current_volume_kg"], row["previous_volume_kg"], TREND_NO_TOLERANCE
        )
        result.append(row)
    if level == LEVEL_VOLUME_CLASS:
        return sorted(result, key=lambda row: _sort_key_group(row["group"]))
    # Grupo e grupo pai: ranking por repeticoes (desempate por volume), como sempre.
    return sorted(result, key=lambda row: (-row["current_reps"], -row["current_volume_kg"]))


def build_period_comparison(
    trend_items: Iterable[Mapping[str, Any]], granularity: str, today: date
) -> dict[str, Any]:
    """Aba de tendencia: reps e volume por grupo na janela atual x anterior
    (`granularity_windows`), por nivel. `trend_items`: `VolumeService.get_volume_trend`
    (tonelagem bruta por dia x grupo) com a hierarquia anotada; `today`: hoje local."""
    windows = granularity_windows(today, granularity)
    items = list(trend_items)
    return {
        "granularity": granularity,
        "current": {key: value.isoformat() for key, value in windows["current"].items()},
        "previous": {key: value.isoformat() for key, value in windows["previous"].items()},
        "levels": {level: _period_rows(items, level, windows) for level in GROUPING_LEVELS},
    }
