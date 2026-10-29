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
from typing import Any, Callable, Iterable, Mapping, Optional

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
            level: {
                "volume": _comparison_rows(volume_rows, level, slots_by_activity, _accumulate_volume, _finish_volume),
                "equivalent_sets": _comparison_rows(
                    equivalent_sets_rows,
                    level,
                    slots_by_activity,
                    _accumulate_equivalent_sets,
                    _finish_equivalent_sets,
                ),
            }
            for level in GROUPING_LEVELS
        },
    }
