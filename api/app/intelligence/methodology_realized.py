"""Metodologia do REALIZADO (M76 Lote 4b, E6).

A tabela "Ativação muscular (últimos 7 dias)" de `/dashboard/musculos`
reavaliava a metodologia no frontend (`muscle-activation-7d.ts`): faixa por
treino do macro-grupo na frequência real da janela, meta semanal e fatia ->
bloco subindo só um nível. Este módulo devolve tudo pronto, com as regras do
dono (`methodology_volume.py`):

- fatia -> bloco pela cadeia inteira de `methodology_parent_group_id`
  (`block_of_group`, a mesma do motor do Modelo);
- semana: regua do REALIZADO, `calendar_weekly_rate` (Lote 4a) sobre a janela
  de calendario - nunca `weekly_normalization`, que e a regua do planejado;
- frequência -> coluna 1x/2x/3x+ por `frequency_bucket_for`;
- comparação seca com a faixa (`_compare`, limites inclusivos, sem tolerância);
- faixa por sessão e meta semanal do macro-grupo do BLOCO (`VolumeClassSpec`).

Três leituras por bloco:
- `days[].status`: as séries diretas DO DIA contra a faixa por treino (o
  desenho da célula);
- `session_status`: a MÉDIA por treino da janela contra a faixa por treino -
  a leitura da metodologia (o motor do Modelo avalia a média, não cada dia);
- `weekly_status`: soma da janela contra a meta semanal.

Diferença deliberada do motor do Modelo: grupo com meta semanal e nenhuma
série na janela aparece com `weekly_status = BELOW_RANGE` ("não treinei" é o
estímulo insuficiente que a tabela quer mostrar), não `NOT_DIRECTLY_TRAINED`.

Unidade: séries DIRETAS no MODO DE CONTAGEM da janela (Lote 4), a de
`VolumeService.get_daily_muscle_activation(counting_mode=...)`, com a fatia de
técnica: em `primary_only` a contribuição PRIMARY sem `stimulus_weight`; em
`primary_secondary` PRIMARY + SECONDARY ponderados pelo motor
(`counted_contributions`). A janela inteira usa UM modo
(`counting_mode_for_window`), resolvido por quem chama; as faixas recebidas já
são as desse modo. O dia conta na frequência pela regra do motor
(`counts_as_frequency_session`: PRIMARY no dia, ou frações de SECONDARY >= 1
série); em `primary_only` isso é exatamente "dia com série direta". A régua
equivalente (`effective_sets`, P+S+E) não muda com o modo. Puro: sem I/O.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from .methodology_volume import (
    SETS_DECIMAL_PLACES,
    MethodologyGroupInput,
    MethodologyVolumeStatus,
    VolumeClassSpec,
    _compare,
    block_of_group,
    calendar_weekly_rate,
    counts_as_frequency_session,
    frequency_bucket_for,
    group_name_key,
)

# v2 (Lote 4 do modo de contagem): o payload ganha `counting_mode` (a régua da
# janela). Em `primary_only` os itens são os da v1.
METRIC_VERSION = "realized-v2"


def _round2(value: float) -> float:
    return round(float(value), 2)


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def block_map(groups: Sequence[MethodologyGroupInput]) -> dict[int, int]:
    """`grupo -> bloco` (fatia -> pai pela cadeia inteira, `block_of_group`)
    para a ativacao diaria juntar fatia + pai do MESMO exercicio como o motor
    do Modelo (`VolumeService.get_daily_muscle_activation(block_of=...)`)."""
    by_id = {g.id: g for g in groups}
    return {group_id: block_of_group(group_id, by_id) for group_id in by_id}


def _status(value: float, low: Optional[float], high: Optional[float]) -> str:
    if low is None or high is None:
        return MethodologyVolumeStatus.NOT_EVALUABLE.value
    return _compare(value, low, high).value  # type: ignore[arg-type]


def evaluate_realized_window(
    daily_items: Iterable[Mapping[str, Any]],
    days: Sequence[str],
    groups: Sequence[MethodologyGroupInput],
    volume_classes: Mapping[int, VolumeClassSpec],
) -> list[dict[str, Any]]:
    """Uma linha por BLOCO com trabalho na janela (ou com meta semanal).

    `daily_items`: linhas de `get_daily_muscle_activation` (`local_date`,
    `muscle_group`, `muscle_group_id`, `direct_sets`, `sets_count` e, fora de
    `primary_only`, `direct_secondary_sets`).
    `days`: as datas ISO da janela, da mais antiga para a mais recente.
    Grupo que não está no cadastro vira bloco próprio, sem macro-grupo."""
    by_id: dict[int, MethodologyGroupInput] = {g.id: g for g in groups}
    by_name = {group_name_key(g.name): g for g in groups}
    day_index = {str(day)[:10]: index for index, day in enumerate(days)}

    blocks: dict[Any, dict[str, Any]] = {}

    def _ensure(key: Any, block_id: Optional[int], name: str) -> dict[str, Any]:
        entry = blocks.get(key)
        if entry is None:
            entry = {
                "block_id": block_id,
                "name": name,
                "direct": [0.0] * len(days),
                "secondary": [0.0] * len(days),
                "effective": [0.0] * len(days),
                "members": [],
            }
            blocks[key] = entry
        return entry

    def _block_for(group_id: Any, group_name: str) -> tuple[Any, Optional[int], str, Optional[int]]:
        group = by_id.get(int(group_id)) if group_id is not None else None
        if group is None:
            group = by_name.get(group_name_key(group_name))
        if group is None:
            return ("name", group_name), None, group_name, None
        block_id = block_of_group(group.id, by_id)
        return block_id, block_id, by_id[block_id].name, group.id

    for item in daily_items:
        index = day_index.get(str(item.get("local_date"))[:10])
        if index is None:
            continue
        group_name = str(item.get("muscle_group") or "")
        key, block_id, block_name, member_id = _block_for(item.get("muscle_group_id"), group_name)
        entry = _ensure(key, block_id, block_name)
        entry["direct"][index] += _num(item.get("direct_sets"))
        entry["secondary"][index] += _num(item.get("direct_secondary_sets"))
        entry["effective"][index] += _num(item.get("sets_count"))
        member = {"muscle_group_id": member_id, "muscle_group": group_name}
        if member not in entry["members"]:
            entry["members"].append(member)

    # Bloco com meta semanal que não treinou na janela também aparece.
    for group in groups:
        if group.methodology_parent_group_id is not None:
            continue
        spec = volume_classes.get(group.volume_class_id) if group.volume_class_id is not None else None
        if spec is None or not spec.has_weekly_target:
            continue
        _ensure(group.id, group.id, group.name)

    rows: list[dict[str, Any]] = []
    for entry in blocks.values():
        block_id = entry["block_id"]
        group = by_id.get(block_id) if block_id is not None else None
        spec = (
            volume_classes.get(group.volume_class_id)
            if group is not None and group.volume_class_id is not None
            else None
        )
        direct: list[float] = entry["direct"]
        secondary: list[float] = entry["secondary"]
        # Dia que conta na frequência: regra do motor (PRIMARY no dia, ou
        # frações de SECONDARY >= 1 série). Sem secundário (`primary_only`):
        # dia com série direta, como na v1.
        counted = [
            counts_as_frequency_session(round(value - extra, SETS_DECIMAL_PLACES) > 0, extra)
            for value, extra in zip(direct, secondary)
        ]
        days_trained = sum(1 for value in counted if value)
        bucket = frequency_bucket_for(float(days_trained))
        rng = spec.range_for(bucket) if spec is not None else None
        session_min = float(rng.sets_min) if rng is not None else None
        session_max = float(rng.sets_max) if rng is not None else None
        weekly_min = float(spec.weekly_sets_min) if spec is not None and spec.has_weekly_target else None  # type: ignore[arg-type]
        weekly_max = float(spec.weekly_sets_max) if spec is not None and spec.has_weekly_target else None  # type: ignore[arg-type]
        weekly_total = _round2(sum(direct))
        # Regua do REALIZADO (Lote 4a): janela de calendario -> taxa semanal
        # pelo dono (`calendar_weekly_rate`); com 7 dias e o proprio total.
        weekly_rate = calendar_weekly_rate(weekly_total, len(days)) or 0.0
        per_session = _round2(weekly_total / days_trained) if days_trained else None

        if block_id is not None and not any(m["muscle_group_id"] == block_id for m in entry["members"]):
            entry["members"].insert(0, {"muscle_group_id": block_id, "muscle_group": entry["name"]})

        rows.append(
            {
                "muscle_group_id": block_id,
                "muscle_group": entry["name"],
                "member_groups": entry["members"],
                "volume_class_name": spec.name if spec is not None else None,
                "days": [
                    {
                        "date": str(day)[:10],
                        "direct_sets": _round2(direct[index]),
                        "effective_sets": _round2(entry["effective"][index]),
                        "status": _status(direct[index], session_min, session_max) if counted[index] else None,
                    }
                    for index, day in enumerate(days)
                ],
                "days_trained": days_trained,
                "frequency_bucket": bucket,
                "session_range": (
                    {"min": session_min, "max": session_max} if session_min is not None else None
                ),
                "direct_sets_per_session": per_session,
                "session_status": (
                    _status(per_session, session_min, session_max)
                    if per_session is not None
                    else MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED.value
                ),
                "weekly_total": weekly_total,
                "weekly_range": {"min": weekly_min, "max": weekly_max} if weekly_min is not None else None,
                "weekly_status": _status(weekly_rate, weekly_min, weekly_max),
            }
        )
    return rows
