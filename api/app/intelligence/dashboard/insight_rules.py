"""Regras deterministicas de insight do Novo Dashboard (plano §7 "Proxima
acao", §12; F1.6).

Tudo aqui e FUNCAO PURA sobre um `InsightContext` ja montado pelo
`overview_service` a partir dos servicos v3 (aderencia, musculos, recuperacao,
progressao em lote, corpo, qualidade). Nenhuma regra le banco, relogio ou
recalcula metrica: ela COMPARA numeros que ja existem e EXPLICA.

Regras nao negociaveis (brief original):

- Severidade e `INFO | ATTENTION | ACTION` (`contracts.Severity`). NUNCA
  existe "CRITICAL" para situacao normal de treino - o enum nem tem o valor.
- Cada insight carrega evidencia (valores e datas), comparacao (base, janela,
  chaves), confianca (`data_quality`), limitacoes, explicacoes alternativas,
  acao sugerida e data de reavaliacao. Nada muda automaticamente: a acao e
  uma SUGESTAO em texto.
- Quantidade minima de dados e condicao de comparabilidade sao pre-requisito
  de TODA regra. Sem elas a regra fica em SILENCIO - silencio nao e alerta.
- Limiares vem de `metrics_registry.THRESHOLDS`, nunca de constante solta.
  Ajustar limiar e decisao de produto (validacao contra dados reais esta na
  descricao do PR da F1.6).
- `fingerprint` e deterministico: `codigo` + chaves da comparacao. E o que a
  F2.2 vai usar para cooldown persistente (`dashboard_alert_states`); no MVP
  serve para deduplicar na propria resposta.

A regra de "queda de desempenho com evento de contexto <= 72h" esta LIGADA
desde a F2.1, junto com `TRAINING_PAUSE_DETECTED` ("voce parou de treinar").
Ela COLAPSA POR EVENTO (2026-09-25): um evento com quedas associadas e UM
cartao, com os exercicios afetados como evidencia - mesma linha do colapso
`MUSCLE_BELOW_MIN_TWO_WEEKS` -> `TRAINING_VOLUME_BROADLY_BELOW_MIN`.
As duas leem `ctx.context_events` / `ctx.days_since_last_session`, que o
`overview_service` monta com o `context_service` (manuais da tabela
`context_events`, migracao 048, unidos aos derivados de troca de ficha e de
academia).

`TRAINING_PAUSE_DETECTED` existe porque, numa pausa TOTAL de treino, as
regras de volume (`MUSCLE_BELOW_MIN_TWO_WEEKS` /
`TRAINING_VOLUME_BROADLY_BELOW_MIN`) silenciam de proposito - "semana sem
treino nenhum e ferias, nao deficit". O silencio estava certo e a resposta
estava faltando: quem parou precisa ouvir "voce parou de treinar", nao "seu
volume de biceps caiu". Decisao do usuario, 2026-09-24.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Optional, Sequence

from ...domain.deload import suppresses_below_min_volume, suppresses_regression
from ...observability import log_event
from .contracts import DataNature, DataQuality, Severity, WindowInfo
from .exposure_service import paired_top_reps
from .metrics_registry import (
    SIBLING_MUSCLE_GROUP_PAIRS,
    THRESHOLDS,
    ExerciseProgressionStatus,
    metric_version,
)

INSIGHT_RULES_VERSION = metric_version("insight_rules")

# Status de volume semanal (mesmos codigos de `classify_weekly_volume_status`).
_BELOW_MIN = "BELOW_MIN"
_ABOVE_MAX = "ABOVE_MAX"

# Prontidao "piorando" = o modelo ja nao classifica o grupo como pronto.
_STRAINED_READINESS = frozenset({"REDUCED_DOSE", "NO_DIRECT_WORK"})

# Status comparaveis (o exercicio TEM historico comparavel; o resto e
# ausencia de dado, nao resultado).
_COMPARABLE_STATUSES = frozenset(
    {
        ExerciseProgressionStatus.PROGRESSING.value,
        ExerciseProgressionStatus.STABLE.value,
        ExerciseProgressionStatus.PLATEAU_SUSPECTED.value,
        ExerciseProgressionStatus.REGRESSING.value,
    }
)
_PROGRESS_OK_STATUSES = frozenset(
    {ExerciseProgressionStatus.PROGRESSING.value, ExerciseProgressionStatus.STABLE.value}
)

_SEVERITY_RANK = {Severity.ACTION: 0, Severity.ATTENTION: 1, Severity.INFO: 2}
_QUALITY_RANK = {
    DataQuality.HIGH: 0,
    DataQuality.MEDIUM: 1,
    DataQuality.LOW: 2,
    DataQuality.INSUFFICIENT: 3,
}

# Tabela `body_measurements` (migracao 047) ausente - janela de deploy. Com a
# tabela presente, o `body_service` escreve limitacoes mais precisas
# ("nenhuma medida registrada", "uma so no protocolo"...).
WAIST_UNAVAILABLE_LIMITATION = (
    "Cintura não disponível (F1.4, `body_measurements` ainda não existe): sem ela o sinal "
    "não distingue recomposição de simples manutenção."
)
CONTEXT_EVENTS_UNAVAILABLE_LIMITATION = (
    "Eventos de contexto (álcool, viagem, doença...) indisponíveis: a tabela `context_events` "
    "(migração 048) ainda não existe neste banco."
)
CONTEXT_EVENTS_EMPTY_LIMITATION = (
    "Nenhum evento de contexto registrado nesta janela: ausência de evento não é ausência de "
    "contexto — só quer dizer que nada foi anotado em /dashboard/contexto."
)
CONTEXT_EVENTS_ASSOCIATION_LIMITATION = (
    "Eventos de contexto registrados na janela aparecem em /dashboard/contexto; a coincidência "
    "no tempo é associação, nunca causa."
)


def context_limitation(ctx: "InsightContext") -> str:
    """A frase VERDADEIRA sobre o contexto, no estado em que ele esta.

    Antes da F2.1 havia uma frase so ("a regra esta desligada"), e ela deixou
    de ser verdade no momento em que a tabela nasceu. Uma limitacao que mente
    e pior do que nenhuma: por isso o texto e escolhido pelo estado -
    indisponivel (migracao pendente), disponivel e vazio, ou disponivel com
    eventos."""
    if not ctx.context_events_available:
        return CONTEXT_EVENTS_UNAVAILABLE_LIMITATION
    if not ctx.context_events:
        return CONTEXT_EVENTS_EMPTY_LIMITATION
    return CONTEXT_EVENTS_ASSOCIATION_LIMITATION


# ---------------------------------------------------------------------------
# Contexto (montado pelo overview_service; DTO, sem I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsightContext:
    """Tudo que as regras enxergam. Cada campo e o PAYLOAD de um servico v3
    chamado UMA vez pelo `overview_service` (memo por request)."""

    window: WindowInfo
    cycle_days: int
    muscles: Mapping[str, Any]
    # Duas SEMANAS consecutivas terminando em `window.date_to` (regua da
    # regra "abaixo do minimo"; independe do ciclo do Modelo).
    muscles_current_week: Optional[Mapping[str, Any]] = None
    muscles_previous_week: Optional[Mapping[str, Any]] = None
    recovery: Optional[Mapping[str, Any]] = None
    progression: Sequence[Mapping[str, Any]] = ()
    skipped_items: Sequence[Mapping[str, Any]] = ()
    body: Optional[Mapping[str, Any]] = None
    working_sets_total: int = 0
    unmapped_working_sets: int = 0
    context_events: Sequence[Mapping[str, Any]] = ()
    # A tabela `context_events` existe neste banco? Separado de a lista estar
    # vazia: "nao da para saber" e "nada aconteceu" sao estados diferentes.
    context_events_available: bool = False
    # Pausa de treino (F2.1): dias civis entre a ultima sessao e o fim da
    # janela, e quantos dias de treino houve nos 28 dias ate aquela sessao.
    days_since_last_session: Optional[int] = None
    last_session_local_date: Optional[date] = None
    sessions_before_pause: int = 0
    # M97 D: periodos ativos + dias de sessoes de deload que tocam a janela
    # (`(inicio, fim)`, dono `domain/deload.py`). Vazio = sem deload.
    deload_ranges: Sequence[tuple[date, date]] = ()
    thresholds: Mapping[str, Any] = field(default_factory=lambda: THRESHOLDS)

    def threshold(self, name: str) -> Any:
        return self.thresholds[name]


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _value(metric: Optional[Mapping[str, Any]]) -> Optional[float]:
    if not metric:
        return None
    value = metric.get("value")
    return None if value is None else float(value)


def _quality(value: Any) -> DataQuality:
    if isinstance(value, DataQuality):
        return value
    try:
        return DataQuality(str(value))
    except ValueError:
        return DataQuality.LOW


def _worst_quality(*qualities: DataQuality) -> DataQuality:
    worst = DataQuality.HIGH
    for quality in qualities:
        if _QUALITY_RANK[quality] > _QUALITY_RANK[worst]:
            worst = quality
    return worst


def _has_real_target(item: Mapping[str, Any]) -> bool:
    source = item.get("targets", {}).get("source")
    source_value = source.value if hasattr(source, "value") else str(source)
    return source_value != "fallback_generic"


def _items_by_group(payload: Optional[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    if not payload:
        return {}
    return {int(item["muscle_group_id"]): item for item in payload.get("items", [])}


def _has_any_session(payload: Optional[Mapping[str, Any]]) -> bool:
    """O ciclo teve treino? Sem nenhuma serie fisica em nenhum grupo, "abaixo
    do minimo" e ferias, nao conclusao."""
    if not payload:
        return False
    for item in payload.get("items", []):
        if (_value(item.get("realized", {}).get("physical_sets")) or 0.0) > 0:
            return True
    return False


def _evidence(
    metric: str,
    from_value: Optional[float],
    to_value: Optional[float],
    *,
    unit: Optional[str],
    nature: DataNature,
    observed_at: Optional[date] = None,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "from_value": None if from_value is None else round(float(from_value), 2),
        "to_value": None if to_value is None else round(float(to_value), 2),
        "unit": unit,
        "nature": nature,
        "observed_at": observed_at,
    }


# ---------------------------------------------------------------------------
# F2.2 - chave de COOLDOWN por codigo (`dashboard_alert_states`, migracao 051)
# ---------------------------------------------------------------------------
#
# A impressao identifica a OBSERVACAO; o cooldown precisa identificar o ALVO.
# Sao coisas diferentes sempre que uma chave da comparacao muda sozinha, e a
# medicao do dataset real de DEV (usuario 1, 8 leituras de
# `/dashboard/insights` entre 2026-08-14 e 2026-09-25) mostrou tres jeitos de
# isso acontecer:
#
# - chave de CONJUNTO: `EXERCISES_REGRESSING:exercise_ids=9,14,16,...` produziu
#   QUATRO impressoes diferentes nas 8 leituras, para a mesma condicao ("varios
#   exercicios regredindo"), porque a lista muda a cada exercicio que entra ou
#   sai;
# - chave com a DATA DA JANELA: `RECOMPOSITION_COMPATIBLE` e
#   `DATA_QUALITY_UNMAPPED_SETS` levam `date_to` na chave - impressao nova todo
#   dia, por construcao;
# - chave de EVENTO: `PERFORMANCE_DROP_WITH_CONTEXT_EVENT` leva
#   `(event_type, event_date)`, que sao fatos fixos - impressao ESTAVEL (na
#   medicao, quando a chave ainda levava `exercise_id`, as mesmas 19
#   impressoes repetiram por 21 dias; o colapso por evento reduziu essas 19 a
#   3 cartoes, um por evento, sem mudar a estabilidade). Aqui a impressao
#   inteira serve como chave.
#
# Por isso cada codigo declara ABAIXO o subconjunto de chaves que identifica o
# alvo. Tupla VAZIA = o alvo e global ("o seu treino", "o seu corpo") e a chave
# de cooldown e o proprio codigo. Um codigo novo que esqueca de aparecer aqui
# NAO quebra a leitura (cai na impressao inteira, o comportamento conservador)
# mas falha em `tests/unit/test_dashboard_alert_cooldown.py`, que exige a
# decisao escrita - mesmo padrao do guard das migracoes.
COOLDOWN_KEYS_BY_CODE: Mapping[str, tuple[str, ...]] = {
    # alvo = o grupo muscular
    "MUSCLE_BELOW_MIN_TWO_WEEKS": ("muscle_group_id",),
    "MUSCLE_ABOVE_MAX_READINESS_STRAINED": ("muscle_group_id",),
    "DIRECT_FAR_BELOW_INDIRECT": ("muscle_group_id",),
    # alvo = o treino como um todo (a lista de grupos muda a cada semana)
    "TRAINING_VOLUME_BROADLY_BELOW_MIN": (),
    # alvo = o exercicio NAQUELE equipamento/convencao (a chave de
    # comparabilidade do §8.1 - estavel enquanto o exercicio for o mesmo)
    "EXERCISE_PLATEAU_SUSPECTED": ("exercise_id", "equipment_id", "load_convention"),
    # alvo = o treino como um todo (medido: 4 impressoes em 8 leituras)
    "EXERCISES_REGRESSING": (),
    # alvo = o item da ficha
    "PLAN_ITEM_SKIPPED_AT_END": ("plan_exercise_id",),
    # alvo = o par de grupos irmaos (par FIXO, vem do registry)
    "SIBLING_ASYMMETRY": ("muscle_group_ids",),
    # alvo = o corpo (a chave traz `date_to`, que muda todo dia)
    "RECOMPOSITION_COMPATIBLE": (),
    # alvo = o EVENTO de contexto (fato fixo; a impressao inteira). Desde o
    # colapso por evento, a impressao NAO leva mais `exercise_id`: os
    # exercicios afetados sao evidencia dentro do cartao.
    "PERFORMANCE_DROP_WITH_CONTEXT_EVENT": ("event_type", "event_date"),
    # alvo = a pausa. `last_session` FAZ parte da chave de proposito: uma pausa
    # NOVA (outra ultima sessao) e um alerta novo, e deve furar o cooldown da
    # anterior.
    "TRAINING_PAUSE_DETECTED": ("last_session",),
    # alvo = o cadastro do usuario (a chave traz `date_to`)
    "DATA_QUALITY_UNMAPPED_SETS": (),
}

# Codigos que uma regra pode emitir, incluindo os consolidados (colapso), que
# nao tem `RuleSpec` proprio. E a lista que o teste de cobertura compara com
# `COOLDOWN_KEYS_BY_CODE`.
COLLAPSED_CODES: tuple[str, ...] = ("TRAINING_VOLUME_BROADLY_BELOW_MIN",)


def cooldown_keys_for(code: str) -> Optional[tuple[str, ...]]:
    """Subconjunto de chaves que identifica o ALVO deste codigo, ou `None`
    quando o codigo nao tem decisao registrada."""
    return COOLDOWN_KEYS_BY_CODE.get(str(code))


def alert_key(insight: Mapping[str, Any]) -> str:
    """Identidade do alerta para o cooldown (`dashboard_alert_states.alert_key`).

    Codigo sem decisao registrada cai na IMPRESSAO inteira e avisa no log: o
    efeito e "nunca silencia", que e o lado seguro do erro (mostrar demais em
    vez de calar o que importa)."""
    code = str(insight["code"])
    selected = cooldown_keys_for(code)
    if selected is None:
        log_event("insight.cooldown_keys_missing", code=code)
        return str(insight["fingerprint"])
    keys = (insight.get("comparison") or {}).get("keys") or {}
    subset = {name: keys[name] for name in selected if name in keys}
    return build_fingerprint(code, subset)


def build_fingerprint(code: str, keys: Mapping[str, Any]) -> str:
    """`codigo:chave=valor&chave=valor` com chaves ORDENADAS - o mesmo insight,
    com as mesmas chaves, tem sempre a mesma impressao.

    A impressao identifica a OBSERVACAO (deduplicacao na resposta). Quem
    identifica o ALVO, para o cooldown da F2.2, e `alert_key` acima - ver o
    comentario de `COOLDOWN_KEYS_BY_CODE` para a diferenca medida."""
    parts = []
    for key in sorted(keys):
        value = keys[key]
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        parts.append(f"{key}={value}")
    return f"{code}:" + "&".join(parts)


def _insight(
    *,
    code: str,
    question: str,
    status: str,
    severity: Severity,
    answer: str,
    evidence: list[dict[str, Any]],
    basis: str,
    window: str,
    keys: Mapping[str, Any],
    data_quality: DataQuality,
    limitations: Sequence[str],
    alternatives: Sequence[str],
    action: Optional[str],
    reassess_after: Optional[date],
    source_screen: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "question": question,
        "status": status,
        "severity": severity,
        "answer": answer,
        "evidence": evidence,
        "comparison": {"basis": basis, "window": window, "keys": dict(keys)},
        "data_quality": data_quality,
        "limitations": list(limitations),
        "alternative_explanations": list(alternatives),
        "suggested_action": action,
        "reassess_after": reassess_after,
        "source_screen": source_screen,
        "fingerprint": build_fingerprint(code, keys),
    }


def _fmt(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


# ---------------------------------------------------------------------------
# Regras v1 (§12)
# ---------------------------------------------------------------------------


def _below_min_in_both_weeks(
    current: Mapping[str, Any], previous: Mapping[str, Any]
) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any]]], int]:
    """`([(item_semana_atual, item_semana_anterior), ...], grupos_com_meta)`:
    os grupos com META real abaixo do minimo nas DUAS semanas e o total de
    grupos com meta real na semana atual (denominador do colapso)."""
    previous_by_group = _items_by_group(previous)
    below: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    with_target = 0
    for item in current.get("items", []):
        if not _has_real_target(item):
            continue
        with_target += 1
        if item.get("status") != _BELOW_MIN:
            continue
        before = previous_by_group.get(int(item["muscle_group_id"]))
        if not before or before.get("status") != _BELOW_MIN or not _has_real_target(before):
            continue
        below.append((item, before))
    return below, with_target


def _below_min_alternatives(ctx: InsightContext) -> list[str]:
    alternatives = [
        "Deload ou semana mais leve planejada de propósito.",
        "Sessões que tocam o grupo ainda não aconteceram nestas semanas (ordem dos slots do ciclo).",
    ]
    if ctx.muscles.get("unattributed_sets"):
        alternatives.append(
            "Exercícios sem contribuição muscular cadastrada podem estar subestimando o grupo."
        )
    return alternatives


def below_min_ruler_start(date_to: date, thresholds: Mapping[str, Any] = THRESHOLDS) -> date:
    """Primeiro dia da regua da regra "abaixo do minimo": as N semanas que
    terminam em `date_to`. Quem le os deloads da regua (`overview_service`)
    usa esta data, nao uma copia da conta."""
    weeks = int(thresholds["insight_below_min_consecutive_weeks"])
    week_days = int(thresholds["insight_below_min_week_days"])
    return date_to - timedelta(days=weeks * week_days - 1)


def rule_muscle_below_min_consecutive_weeks(ctx: InsightContext) -> list[dict[str, Any]]:
    """Grupo com META abaixo do minimo em N SEMANAS consecutivas (N=2).

    A regua e SEMANAL (as metas `min_weekly_sets` sao semanais) e independe
    do ciclo do Modelo atribuido: com ciclo de 56 dias, "dois ciclos" cobria
    112 dias e todos os grupos caiam juntos depois de uma pausa (decisao do
    usuario, 2026-09-23, ao validar o F1.6). Uma semana isolada nao conclui:
    exige as duas, cada uma com ao menos uma sessao (semana sem treino
    nenhum e ferias, nao deficit). Meta generica (`fallback_generic`) nao
    dispara: nao ha objetivo do usuario contra o qual estar "abaixo".

    COLAPSO: quando >= `insight_below_min_collapse_ratio` dos grupos com
    meta disparariam no mesmo periodo, a observacao real e UMA ("houve pausa
    ou reducao geral de volume") - sai um unico insight consolidado
    (`TRAINING_VOLUME_BROADLY_BELOW_MIN`) e os individuais NAO sao emitidos.
    Abaixo da fracao, cada grupo continua individual."""
    weeks_required = int(ctx.threshold("insight_below_min_consecutive_weeks"))
    if weeks_required != 2:  # pragma: no cover - v1 so implementa duas semanas
        raise ValueError("insight_below_min_consecutive_weeks: v1 so avalia 2 semanas")
    week_days = int(ctx.threshold("insight_below_min_week_days"))
    collapse_ratio = float(ctx.threshold("insight_below_min_collapse_ratio"))
    current = ctx.muscles_current_week
    previous = ctx.muscles_previous_week
    if not current or not previous:
        return []
    # M97 D: a regua (as duas semanas) toca um deload -> reducao planejada,
    # nao deficit (dono `deload.suppresses_below_min_volume`).
    ruler_start = below_min_ruler_start(ctx.window.date_to, ctx.thresholds)
    if suppresses_below_min_volume(ctx.deload_ranges, ruler_start, ctx.window.date_to):
        return []
    if not _has_any_session(current) or not _has_any_session(previous):
        return []

    below, with_target = _below_min_in_both_weeks(current, previous)
    if not below:
        return []
    window_label = f"{week_days}d x {weeks_required}"

    if len(below) / with_target >= collapse_ratio:
        return [
            _broadly_below_min_insight(
                ctx, below, with_target, window_label=window_label, weeks=weeks_required, week_days=week_days
            )
        ]

    out: list[dict[str, Any]] = []
    for item, before in below:
        group_id = int(item["muscle_group_id"])
        weekly_now = _value(item["realized"]["weekly_effective_sets"])
        weekly_before = _value(before["realized"]["weekly_effective_sets"])
        minimum = _value(item["targets"]["min"])
        name = item["muscle_group"]
        out.append(
            _insight(
                code="MUSCLE_BELOW_MIN_TWO_WEEKS",
                question="Algum grupo com meta ficou abaixo do mínimo por duas semanas seguidas?",
                status=_BELOW_MIN,
                severity=Severity.ATTENTION,
                answer=(
                    f"{name} ficou abaixo do mínimo de {_fmt(minimum)} séries/sem nas duas últimas semanas "
                    f"({_fmt(weekly_before)} → {_fmt(weekly_now)} séries/sem)."
                ),
                evidence=[
                    _evidence(
                        "weekly_effective_sets",
                        weekly_before,
                        weekly_now,
                        unit="séries/sem",
                        nature=DataNature.DERIVED,
                        observed_at=ctx.window.date_to,
                    ),
                    _evidence(
                        "target_min_weekly_sets", minimum, minimum, unit="séries/sem", nature=DataNature.PLANNED
                    ),
                ],
                basis="weekly_target_min_two_consecutive_weeks",
                window=window_label,
                keys={"muscle_group_id": group_id},
                data_quality=_worst_quality(_quality(item["data_quality"]), _quality(before["data_quality"])),
                limitations=list(dict.fromkeys([*item.get("limitations", []), *before.get("limitations", [])])),
                alternatives=_below_min_alternatives(ctx),
                action=(
                    f"Sugestão: priorizar um exercício em que {name} seja o motor principal na próxima "
                    "sessão. A ficha não muda sozinha."
                ),
                reassess_after=ctx.window.date_to + timedelta(days=week_days),
                source_screen="/dashboard/musculos",
            )
        )
    return out


def _broadly_below_min_insight(
    ctx: InsightContext,
    below: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    with_target: int,
    *,
    window_label: str,
    weeks: int,
    week_days: int,
) -> dict[str, Any]:
    """UM insight para a maioria dos grupos abaixo do minimo: a frase diz a
    observacao (N de M), a lista completa vai na evidencia. E informacao
    (ATTENTION), nao urgencia: pausa/viagem/doenca e a explicacao mais
    provavel e so a regra de contexto (F2.1) vai confirmar. Fingerprint =
    codigo + ids ordenados dos grupos."""
    ordered = sorted(below, key=lambda pair: int(pair[0]["muscle_group_id"]))
    group_ids = [int(item["muscle_group_id"]) for item, _ in ordered]
    evidence = [
        _evidence(
            "muscle_groups_below_min",
            None,
            len(ordered),
            unit="grupos",
            nature=DataNature.DERIVED,
            observed_at=ctx.window.date_to,
        ),
        _evidence("muscle_groups_with_target", None, with_target, unit="grupos", nature=DataNature.PLANNED),
    ]
    limitations: list[str] = []
    qualities: list[DataQuality] = []
    for item, before in ordered:
        evidence.append(
            _evidence(
                f"weekly_effective_sets[{item['muscle_group']}]",
                _value(before["realized"]["weekly_effective_sets"]),
                _value(item["realized"]["weekly_effective_sets"]),
                unit="séries/sem",
                nature=DataNature.DERIVED,
            )
        )
        limitations.extend([*item.get("limitations", []), *before.get("limitations", [])])
        qualities.extend([_quality(item["data_quality"]), _quality(before["data_quality"])])
    limitations = list(dict.fromkeys(limitations))
    limitations.append(
        "Leitura agregada: não distingue pausa deliberada de queda de volume. "
        + context_limitation(ctx)
    )
    return _insight(
        code="TRAINING_VOLUME_BROADLY_BELOW_MIN",
        question="O volume ficou abaixo do mínimo de forma generalizada?",
        status=_BELOW_MIN,
        severity=Severity.ATTENTION,
        answer=(
            f"{len(ordered)} de {with_target} grupos com meta ficaram abaixo do mínimo nas últimas "
            f"{weeks} semanas — compatível com pausa ou redução geral de volume."
        ),
        evidence=evidence,
        basis="weekly_target_min_two_consecutive_weeks_majority",
        window=window_label,
        keys={"muscle_group_ids": group_ids},
        data_quality=_worst_quality(*qualities),
        limitations=limitations,
        alternatives=[
            "Pausa no treino (viagem, doença, férias): confira a timeline em /dashboard/contexto.",
            "Deload ou período mais leve planejado de propósito.",
            "Redução geral de volume por mudança de ficha ou de rotina.",
        ],
        action=(
            "Sugestão: retomar o plano conforme a prontidão; reavaliar após 2 semanas de treino regular. "
            "Nada muda sem sua decisão."
        ),
        reassess_after=ctx.window.date_to + timedelta(days=weeks * week_days),
        source_screen="/dashboard/musculos",
    )


def rule_muscle_above_max_readiness_strained(ctx: InsightContext) -> list[dict[str, Any]]:
    """Grupo ACIMA do maximo E prontidao piorando (modelo ja nao o classifica
    como `READY`). Um dos dois sozinho nao dispara: volume alto com prontidao
    boa e sobrecarga tolerada; prontidao ruim com volume normal e assunto da
    tela de recuperacao."""
    if not ctx.recovery:
        return []
    readiness_by_group = {
        int(group["muscle_group_id"]): group
        for group in ctx.recovery.get("groups", [])
        if group.get("muscle_group_id") is not None
    }
    out: list[dict[str, Any]] = []
    for item in ctx.muscles.get("items", []):
        if item.get("status") != _ABOVE_MAX or not _has_real_target(item):
            continue
        group_id = int(item["muscle_group_id"])
        readiness = readiness_by_group.get(group_id)
        if not readiness or readiness.get("readiness_status") not in _STRAINED_READINESS:
            continue

        weekly = _value(item["realized"]["weekly_effective_sets"])
        maximum = _value(item["targets"]["max"])
        recovery_pct = _value(readiness.get("recovery_pct"))
        remaining_hours = _value(readiness.get("remaining_hours"))
        name = item["muscle_group"]
        days_to_reassess = max(1, int(((remaining_hours or 72.0) + 23.0) // 24.0))
        out.append(
            _insight(
                code="MUSCLE_ABOVE_MAX_READINESS_STRAINED",
                question="Algum grupo está acima do máximo enquanto a prontidão piora?",
                status=_ABOVE_MAX,
                severity=Severity.ATTENTION,
                answer=(
                    f"{name} está em {_fmt(weekly)} séries/sem (máximo {_fmt(maximum)}) e o modelo de "
                    f"prontidão o classifica como {readiness.get('readiness_status')}"
                    + (f" ({_fmt(recovery_pct, 0)}% recuperado)." if recovery_pct is not None else ".")
                ),
                evidence=[
                    _evidence(
                        "weekly_effective_sets",
                        maximum,
                        weekly,
                        unit="séries/sem",
                        nature=DataNature.DERIVED,
                        observed_at=ctx.window.date_to,
                    ),
                    _evidence("recovery_pct", None, recovery_pct, unit="%", nature=DataNature.INFERRED),
                ],
                basis="weekly_target_max_and_readiness_model",
                window=ctx.window.kind.value,
                keys={"muscle_group_id": group_id},
                data_quality=_worst_quality(
                    _quality(item["data_quality"]), _quality(ctx.recovery.get("data_quality", "MEDIUM"))
                ),
                limitations=[
                    *item.get("limitations", []),
                    "Prontidão é um modelo (INFERRED), não uma medição; sinais objetivos podem divergir.",
                ],
                alternatives=[
                    "Fase de sobrecarga planejada, com deload previsto.",
                    "Estímulo recente concentrado em uma sessão (a média semanal exagera o excesso).",
                ],
                action=(
                    f"Sugestão: reduzir 1–2 séries de {name} ou espaçar o próximo estímulo até a "
                    "prontidão voltar a READY. Nada muda sem sua decisão."
                ),
                reassess_after=ctx.window.date_to + timedelta(days=days_to_reassess),
                source_screen="/dashboard/recovery",
            )
        )
    return out


def rule_exercise_plateau_suspected(ctx: InsightContext) -> list[dict[str, Any]]:
    """Exercicio `PLATEAU_SUSPECTED` com >= N exposicoes comparaveis (N=4; o
    status ja exige 3, o insight exige mais uma para nao alarmar cedo)."""
    minimum = int(ctx.threshold("insight_plateau_min_exposures"))
    out: list[dict[str, Any]] = []
    for item in ctx.progression:
        if item.get("status") != ExerciseProgressionStatus.PLATEAU_SUSPECTED.value:
            continue
        if int(item.get("comparable_exposures") or 0) < minimum:
            continue
        # Run comparavel do status (mesmo contexto de fadiga), nao as ultimas N.
        run = list(item.get("_comparable_run") or [])
        first, last = (run[0], run[-1]) if run else ({}, {})
        paired = paired_top_reps(first, last) if len(run) >= 2 else None
        e1rm_missing = any(exposure.get("e1rm") is None for exposure in run)
        key = last.get("comparability_key") or {}
        name = item.get("name") or f"exercício {item['exercise_id']}"
        out.append(
            _insight(
                code="EXERCISE_PLATEAU_SUSPECTED",
                question="Algum exercício parou de evoluir em condições comparáveis?",
                status=ExerciseProgressionStatus.PLATEAU_SUSPECTED.value,
                severity=Severity.INFO,
                answer=(
                    f"{name}: {item['comparable_exposures']} exposições comparáveis sem progresso nem "
                    f"regressão (carga {_fmt(first.get('best_load_kg'))} → {_fmt(last.get('best_load_kg'))} kg)."
                ),
                evidence=[
                    _evidence(
                        "best_load_kg",
                        first.get("best_load_kg"),
                        last.get("best_load_kg"),
                        unit="kg",
                        nature=DataNature.OBSERVED,
                        observed_at=item.get("last_local_date"),
                    ),
                    # Reps POR SERIE emparelhada nas cargas de topo (o que a
                    # regra compara), nunca a soma de todas as series; com RPE
                    # em todas, `reps + RIR` (M78) - a unidade diz qual.
                    _evidence(
                        "paired_top_reps",
                        (paired or {}).get("previous_sum"),
                        (paired or {}).get("current_sum"),
                        unit="reps + RIR" if (paired or {}).get("effort_adjusted") else "reps",
                        nature=DataNature.OBSERVED,
                    ),
                    _evidence(
                        "e1rm", first.get("e1rm"), last.get("e1rm"), unit="kg", nature=DataNature.ESTIMATED
                    ),
                ],
                basis="trailing_comparable_run",
                window=ctx.window.kind.value,
                keys={
                    "exercise_id": int(item["exercise_id"]),
                    "equipment_id": key.get("equipment_id"),
                    "load_convention": key.get("load_convention"),
                },
                data_quality=DataQuality.MEDIUM if e1rm_missing else DataQuality.HIGH,
                limitations=[
                    "Suspeita de platô não é diagnóstico: é ausência de progresso nas últimas exposições.",
                    *(["e1RM ausente em alguma exposição (série sem RPE)."] if e1rm_missing else []),
                ],
                alternatives=[
                    "Fase de manutenção deliberada.",
                    "Mudança de técnica ou amplitude não registrada nas séries.",
                    "Descanso entre séries diferente entre as exposições.",
                ],
                action=(
                    "Sugestão: variar um estímulo na próxima exposição (repetições, carga ou descanso) "
                    "e comparar. Você decide se muda a prescrição."
                ),
                reassess_after=ctx.window.date_to + timedelta(days=14),
                source_screen=f"/dashboard/exercicios/{int(item['exercise_id'])}",
            )
        )
    return out


def rule_exercises_regressing(ctx: InsightContext) -> list[dict[str, Any]]:
    """>= N exercicios `REGRESSING` cuja ultima exposicao caiu nos ultimos
    `insight_regressing_lookback_days` dias (28)."""
    if suppresses_regression(ctx.deload_ranges, ctx.window.date_to):
        return []  # M97 D: deload em curso (dono `deload.suppresses_regression`)
    minimum = int(ctx.threshold("insight_regressing_min_exercises"))
    lookback = int(ctx.threshold("insight_regressing_lookback_days"))
    since = ctx.window.date_to - timedelta(days=lookback - 1)
    regressing = []
    for item in ctx.progression:
        if item.get("status") != ExerciseProgressionStatus.REGRESSING.value:
            continue
        last = item.get("last_local_date")
        if isinstance(last, str):
            last = date.fromisoformat(last[:10])
        if last is None or last < since:
            continue
        regressing.append(item)
    if len(regressing) < minimum:
        return []

    names = ", ".join(str(item.get("name") or item["exercise_id"]) for item in regressing[:5])
    ids = sorted(int(item["exercise_id"]) for item in regressing)
    return [
        _insight(
            code="EXERCISES_REGRESSING",
            question="Vários exercícios regrediram ao mesmo tempo?",
            status=ExerciseProgressionStatus.REGRESSING.value,
            severity=Severity.ATTENTION,
            answer=(
                f"{len(regressing)} exercícios regrediram nos últimos {lookback} dias em condições "
                f"comparáveis ({names}{'…' if len(regressing) > 5 else ''})."
            ),
            evidence=[
                _evidence(
                    "exercises_regressing",
                    None,
                    len(regressing),
                    unit="exercícios",
                    nature=DataNature.DERIVED,
                    observed_at=ctx.window.date_to,
                )
            ],
            basis="exercise_progression_status_last_exposure",
            window=f"{lookback}d",
            keys={"exercise_ids": ids},
            data_quality=DataQuality.HIGH if len(regressing) >= minimum + 1 else DataQuality.MEDIUM,
            limitations=[
                "Regressão é lida exposição a exposição, na mesma chave de comparabilidade; "
                "não separa causa de treino de causa externa.",
                context_limitation(ctx),
            ],
            alternatives=[
                "Sono, estresse ou doença na semana — a timeline de /dashboard/contexto mostra o que "
                "foi anotado nesses dias.",
                "Deload planejado.",
                "Troca de equipamento não cadastrada, lida como a mesma máquina.",
            ],
            action=(
                "Sugestão: revisar recuperação e sinais objetivos antes de subir qualquer prescrição "
                "nesta semana."
            ),
            reassess_after=ctx.window.date_to + timedelta(days=7),
            source_screen="/dashboard/recovery",
        )
    ]


def rule_plan_item_skipped_at_end(ctx: InsightContext) -> list[dict[str, Any]]:
    """Item da ficha PULADO >= N vezes (3) quando esta no FIM da sessao
    (ultimas `insight_end_of_session_positions` posicoes da ficha)."""
    minimum = int(ctx.threshold("insight_skipped_min_count"))
    end_positions = int(ctx.threshold("insight_end_of_session_positions"))
    out: list[dict[str, Any]] = []
    for item in ctx.skipped_items:
        skipped = int(item.get("skipped") or 0)
        sessions = int(item.get("sessions") or 0)
        total = int(item.get("plan_items_total") or 0)
        sequence = item.get("plan_sequence")
        if skipped < minimum or sequence is None or total <= 0:
            continue
        if int(sequence) <= total - end_positions:
            continue
        name = item.get("name") or f"item {item.get('plan_exercise_id')}"
        out.append(
            _insight(
                code="PLAN_ITEM_SKIPPED_AT_END",
                question="Algum item do fim da ficha vem sendo pulado com frequência?",
                status="SKIPPED",
                severity=Severity.INFO,
                answer=(
                    f"{name} (posição {sequence} de {total}) foi pulado em {skipped} de {sessions} sessões "
                    "com esta ficha."
                ),
                evidence=[
                    _evidence(
                        "skipped_count",
                        None,
                        skipped,
                        unit="sessões",
                        nature=DataNature.DERIVED,
                        observed_at=item.get("last_local_date"),
                    ),
                    _evidence("sessions_with_plan", None, sessions, unit="sessões", nature=DataNature.OBSERVED),
                ],
                basis="plan_item_status_by_session",
                window=ctx.window.kind.value,
                keys={"plan_exercise_id": int(item["plan_exercise_id"])},
                data_quality=DataQuality.HIGH if sessions >= minimum + 1 else DataQuality.MEDIUM,
                limitations=[
                    "Status derivado do vínculo sessão-ficha (§5): item executado sem vínculo aparece como "
                    "extra, não como cumprido."
                ],
                alternatives=[
                    "Falta de tempo no fim da sessão.",
                    "Exercício de baixa prioridade para você.",
                    "Substituído por outro exercício registrado sem vínculo com a ficha.",
                ],
                action=(
                    "Sugestão: mover o item para o início da sessão ou trocá-lo por um mais curto. "
                    "A ficha só muda se você editar."
                ),
                reassess_after=ctx.window.date_to + timedelta(days=ctx.cycle_days),
                source_screen="/dashboard/treinos",
            )
        )
    return out


def rule_direct_far_below_indirect(ctx: InsightContext) -> list[dict[str, Any]]:
    """Direto << indireto (razao < `direct_indirect_min_ratio`) em grupo COM
    meta e com >= `insight_min_indirect_sets` series indiretas na janela."""
    ratio_min = float(ctx.threshold("direct_indirect_min_ratio"))
    min_indirect = float(ctx.threshold("insight_min_indirect_sets"))
    out: list[dict[str, Any]] = []
    for item in ctx.muscles.get("items", []):
        if not _has_real_target(item):
            continue
        direct = _value(item["realized"]["direct_sets"]) or 0.0
        indirect = _value(item["realized"]["indirect_sets"]) or 0.0
        if indirect < min_indirect:
            continue
        ratio = direct / indirect
        if ratio >= ratio_min:
            continue
        name = item["muscle_group"]
        out.append(
            _insight(
                code="DIRECT_FAR_BELOW_INDIRECT",
                question="Algum grupo com meta recebe quase só estímulo indireto?",
                status="DIRECT_BELOW_INDIRECT",
                severity=Severity.INFO,
                answer=(
                    f"{name}: {_fmt(direct, 0)} séries diretas contra {_fmt(indirect, 0)} indiretas "
                    f"(razão {_fmt(ratio, 2)}, abaixo de {_fmt(ratio_min, 2)})."
                ),
                evidence=[
                    _evidence(
                        "direct_sets", None, direct, unit="séries", nature=DataNature.OBSERVED,
                        observed_at=ctx.window.date_to,
                    ),
                    _evidence("indirect_sets", None, indirect, unit="séries", nature=DataNature.OBSERVED),
                ],
                basis="direct_to_indirect_ratio",
                window=ctx.window.kind.value,
                keys={"muscle_group_id": int(item["muscle_group_id"])},
                data_quality=_quality(item["data_quality"]),
                limitations=list(item.get("limitations", [])),
                alternatives=[
                    "Grupo tratado como secundário de propósito nesta fase.",
                    "Contribuições cadastradas como SECONDARY onde deveriam ser PRIMARY.",
                ],
                action=(
                    f"Se {name} é prioridade, sugestão: incluir um exercício em que ele seja o motor "
                    "principal. Nada muda automaticamente."
                ),
                reassess_after=ctx.window.date_to + timedelta(days=ctx.cycle_days),
                source_screen="/dashboard/musculos",
            )
        )
    return out


def rule_sibling_asymmetry(ctx: InsightContext) -> list[dict[str, Any]]:
    """Assimetria entre grupos irmaos (`SIBLING_MUSCLE_GROUP_PAIRS`): diferenca
    de atingimento do ALVO semanal >= `insight_sibling_asymmetry_min_diff`.
    Os dois grupos precisam de meta real e ao menos um precisa ter sido
    treinado (nenhum treinado = dado insuficiente, nao assimetria)."""
    min_diff = float(ctx.threshold("insight_sibling_asymmetry_min_diff"))
    by_group = _items_by_group(ctx.muscles)
    out: list[dict[str, Any]] = []
    for group_a, group_b in SIBLING_MUSCLE_GROUP_PAIRS:
        item_a, item_b = by_group.get(int(group_a)), by_group.get(int(group_b))
        if not item_a or not item_b or not _has_real_target(item_a) or not _has_real_target(item_b):
            continue
        weekly_a = _value(item_a["realized"]["weekly_effective_sets"]) or 0.0
        weekly_b = _value(item_b["realized"]["weekly_effective_sets"]) or 0.0
        if weekly_a <= 0 and weekly_b <= 0:
            continue
        target_a = _value(item_a["targets"]["target"]) or 0.0
        target_b = _value(item_b["targets"]["target"]) or 0.0
        if target_a <= 0 or target_b <= 0:
            continue
        pct_a, pct_b = weekly_a / target_a, weekly_b / target_b
        diff = abs(pct_a - pct_b)
        if diff < min_diff:
            continue
        lagging = item_a if pct_a < pct_b else item_b
        out.append(
            _insight(
                code="SIBLING_ASYMMETRY",
                question="Grupos irmãos estão sendo treinados de forma desigual?",
                status="ASYMMETRY",
                severity=Severity.INFO,
                answer=(
                    f"{item_a['muscle_group']} está em {_fmt(pct_a * 100, 0)}% do alvo e "
                    f"{item_b['muscle_group']} em {_fmt(pct_b * 100, 0)}% — {lagging['muscle_group']} "
                    "fica para trás."
                ),
                evidence=[
                    _evidence(
                        f"target_achievement_pct[{item_a['muscle_group_id']}]",
                        None,
                        pct_a * 100,
                        unit="%",
                        nature=DataNature.DERIVED,
                        observed_at=ctx.window.date_to,
                    ),
                    _evidence(
                        f"target_achievement_pct[{item_b['muscle_group_id']}]",
                        None,
                        pct_b * 100,
                        unit="%",
                        nature=DataNature.DERIVED,
                    ),
                ],
                basis="weekly_target_achievement_between_sibling_groups",
                window=ctx.window.kind.value,
                keys={"muscle_group_ids": [int(group_a), int(group_b)]},
                data_quality=_worst_quality(_quality(item_a["data_quality"]), _quality(item_b["data_quality"])),
                limitations=list(dict.fromkeys([*item_a.get("limitations", []), *item_b.get("limitations", [])])),
                alternatives=[
                    "Metas dos dois grupos calibradas de forma diferente de propósito.",
                    "Exercícios de um dos grupos sem contribuição cadastrada.",
                ],
                action=(
                    f"Sugestão: redistribuir 1–2 séries para {lagging['muscle_group']} no próximo ciclo."
                ),
                reassess_after=ctx.window.date_to + timedelta(days=ctx.cycle_days),
                source_screen="/dashboard/musculos",
            )
        )
    return out


def rule_recomposition_compatible(ctx: InsightContext) -> list[dict[str, Any]]:
    """Peso estavel (media movel 7d variando <= `weight_stable_pct`) + >= 50%
    dos exercicios comparaveis PROGRESSING/STABLE -> "compativel com
    recomposicao". INFERRED.

    Cintura (F1.4, `ctx.body["waist"]`, vinda de `body_service.waist_summary`):
    - variacao de cintura no protocolo DISPONIVEL e caindo >= `waist_drop_cm`
      -> confianca MEDIUM, cintura entra como evidencia;
    - variacao disponivel e NAO caindo -> silencio: peso estavel + progressao
      sem cintura caindo e manutencao, nao recomposicao;
    - sem variacao avaliavel (tabela ausente, nenhuma medida, uma so no
      protocolo) -> comportamento da F1.6: confianca LOW com a limitacao
      declarada (a que o `body_service` escreveu, ou a generica)."""
    body = ctx.body or {}
    change_pct = _value(body.get("weight_ma7_change_pct"))
    span_days = int(body.get("span_days") or 0)
    if change_pct is None or span_days < int(ctx.threshold("insight_weight_min_span_days")):
        return []
    if abs(change_pct) > float(ctx.threshold("weight_stable_pct")) * 100.0:
        return []

    comparable = [item for item in ctx.progression if item.get("status") in _COMPARABLE_STATUSES]
    min_comparable = int(ctx.threshold("insight_recomp_min_comparable_exercises"))
    if len(comparable) < min_comparable:
        return []
    ok = [item for item in comparable if item.get("status") in _PROGRESS_OK_STATUSES]
    share = len(ok) / len(comparable)
    if share < float(ctx.threshold("insight_recomp_progress_share")):
        return []

    waist = body.get("waist") or {}
    waist_change = _value(waist.get("change"))
    waist_known = waist_change is not None
    if waist_known and (-waist_change) < float(ctx.threshold("waist_drop_cm")):
        return []

    first = _value(body.get("weight_ma7_first"))
    last = _value(body.get("weight_ma7_latest"))
    evidence = [
        _evidence(
            "body_weight_ma7", first, last, unit="kg", nature=DataNature.DERIVED,
            observed_at=ctx.window.date_to,
        ),
        _evidence("progress_share", None, share * 100, unit="%", nature=DataNature.DERIVED),
    ]
    answer = (
        f"Peso estável ({_fmt(first)} → {_fmt(last)} kg na média de 7 dias, {_fmt(change_pct, 1)}%) "
        f"com {len(ok)} de {len(comparable)} exercícios comparáveis progredindo ou estáveis"
    )
    if waist_known:
        waist_first = _value({"value": waist.get("first")})
        waist_last = _value({"value": waist.get("last")})
        evidence.insert(
            1,
            _evidence("waist_cm", waist_first, waist_last, unit="cm", nature=DataNature.OBSERVED),
        )
        answer += f" e cintura caindo ({_fmt(waist_first)} → {_fmt(waist_last)} cm, no protocolo)."
        quality = DataQuality.MEDIUM
        waist_limitations = list(waist.get("limitations", []))
        action = "Nenhuma ação: continuar o plano e manter as medidas de cintura no protocolo."
    else:
        answer += "."
        quality = DataQuality.LOW
        waist_limitations = list(waist.get("limitations") or [WAIST_UNAVAILABLE_LIMITATION])
        action = "Nenhuma ação: continuar o plano e registrar a cintura em /dashboard/corpo."

    return [
        _insight(
            code="RECOMPOSITION_COMPATIBLE",
            question="O conjunto peso + progressão é compatível com recomposição?",
            status="COMPATIBLE",
            severity=Severity.INFO,
            answer=answer,
            evidence=evidence,
            basis=(
                "weight_ma7_stability_waist_drop_and_progression_share"
                if waist_known
                else "weight_ma7_stability_and_progression_share"
            ),
            window=ctx.window.kind.value,
            keys={"window": ctx.window.kind.value, "date_to": ctx.window.date_to.isoformat()},
            data_quality=quality,
            limitations=[
                *waist_limitations,
                "Sinal INFERIDO: nunca é prova de recomposição.",
                *list(body.get("limitations", [])),
            ],
            alternatives=[
                "Manutenção simples de peso e força.",
                "Variação de peso mascarada por hidratação ou horário das pesagens.",
            ],
            action=action,
            reassess_after=ctx.window.date_to + timedelta(days=28),
            source_screen="/dashboard/corpo",
        )
    ]


def _event_label(event: Mapping[str, Any]) -> str:
    return str(event.get("event_type_label") or event.get("event_type") or "evento")


def _event_span(event: Mapping[str, Any]) -> tuple[Optional[date], Optional[date]]:
    """`(inicio, fim)` do evento. Evento de um dia so tem fim = inicio; evento
    com duracao (viagem, fase de dieta) tem o fim declarado."""
    start = event.get("local_date")
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    end = event.get("end_local_date")
    if isinstance(end, str):
        end = date.fromisoformat(end[:10])
    if start is None:
        return None, None
    return start, (end if end is not None and end >= start else start)


def _event_period(start: date, end: date) -> str:
    return start.isoformat() + (f" a {end.isoformat()}" if end != start else "")


def _association_phrase(during: int, after: int) -> str:
    """Como a frase liga a queda ao evento, SEM afirmar causa: "durante",
    "até 72h depois" ou as duas, quando as exposicoes se dividem."""
    if during and after:
        return "durante ou até 72h depois de"
    if during:
        return "durante"
    return "até 72h depois de"


def rule_performance_drop_with_context_event(ctx: InsightContext) -> list[dict[str, Any]]:
    """Queda de desempenho (exercicio REGRESSING) cuja ultima exposicao caiu
    DENTRO do evento ou ate 72h depois do fim dele -> ASSOCIACAO (nunca
    causa), INFO, `data_quality=LOW`.

    LIGADA na F2.1. Para evento com duracao (viagem de 10 dias), a janela
    conta a partir do FIM: treinar no terceiro dia de uma viagem e tao
    "durante o evento" quanto no primeiro.

    UM insight por EVENTO (decisao do usuario, 2026-09-25), nao por
    (exercicio, evento). Medido no dataset de DEV (usuario 1, janela 90d):
    3 eventos de contexto produziam 19 cartoes - um por exercicio afetado -
    e, com `overview_max_next_actions = 2`, essas associacoes ocupavam as duas
    vagas da home e repetiam por 21 dias. Um evento com queda associada e UMA
    observacao ("o que caiu em volta deste evento"), e os exercicios afetados
    sao a EVIDENCIA dentro do cartao (numero + nome), nao cartoes separados.
    Mesmo colapso que `TRAINING_VOLUME_BROADLY_BELOW_MIN` fez com
    `MUSCLE_BELOW_MIN_TWO_WEEKS` ("e pausa no treino, nao 16 problemas").

    O que o colapso NAO muda: a janela de associacao (72h contadas do FIM do
    evento), a linguagem de associacao com `data_quality=LOW`, e o fato de o
    mesmo exercicio poder aparecer em mais de um evento - escolher um deles
    seria inventar a hierarquia de causa que a regra se recusa a afirmar. A
    ordem e deterministica: a lista de eventos ja vem ordenada do
    `context_service` e os exercicios sao ordenados por `exercise_id`."""
    if not ctx.context_events:
        return []
    if suppresses_regression(ctx.deload_ranges, ctx.window.date_to):
        return []  # M97 D: deload em curso (dono `deload.suppresses_regression`)
    horizon = timedelta(days=3)
    regressing: list[tuple[Mapping[str, Any], date]] = []
    for item in ctx.progression:
        if item.get("status") != ExerciseProgressionStatus.REGRESSING.value:
            continue
        last = item.get("last_local_date")
        if isinstance(last, str):
            last = date.fromisoformat(last[:10])
        if last is None:
            continue
        regressing.append((item, last))
    if not regressing:
        return []

    out: list[dict[str, Any]] = []
    for event in ctx.context_events:
        start, end = _event_span(event)
        if start is None or end is None:
            continue
        affected = sorted(
            (
                (item, last)
                for item, last in regressing
                if start <= last <= end + horizon
            ),
            key=lambda pair: int(pair[0]["exercise_id"]),
        )
        if not affected:
            continue
        out.append(_context_event_drop_insight(ctx, event, start, end, affected))
    return out


def _context_event_drop_insight(
    ctx: InsightContext,
    event: Mapping[str, Any],
    start: date,
    end: date,
    affected: Sequence[tuple[Mapping[str, Any], date]],
) -> dict[str, Any]:
    """UM cartao para UM evento: a frase diz quantos exercicios cairam em volta
    dele e nomeia os primeiros; a lista completa, com o intervalo de cada
    exposicao, vai na evidencia (`event_to_exposure_days[<exercicio>]`).
    Fingerprint = codigo + evento (tipo + data de inicio), sem `exercise_id`:
    o ALVO do cartao passou a ser o evento."""
    label = _event_label(event)
    names = [str(item.get("name") or item["exercise_id"]) for item, _ in affected]
    during = sum(1 for _, last in affected if last <= end)
    phrase = _association_phrase(during, len(affected) - during)
    shown = ", ".join(names[:5])
    if len(names) > 5:
        shown += f" e outros {len(names) - 5}"
    if len(affected) == 1:
        answer = f"{names[0]} regrediu {phrase} '{label}' ({_event_period(start, end)})."
    else:
        answer = (
            f"{len(affected)} exercícios regrediram {phrase} '{label}' "
            f"({_event_period(start, end)}): {shown}."
        )

    evidence = [
        _evidence(
            "exercises_with_drop",
            None,
            len(affected),
            unit="exercícios",
            nature=DataNature.DERIVED,
            observed_at=max(last for _, last in affected),
        )
    ]
    for item, last in affected:
        evidence.append(
            _evidence(
                f"event_to_exposure_days[{item.get('name') or item['exercise_id']}]",
                None,
                (last - end).days,
                unit="dias",
                nature=DataNature.OBSERVED,
                observed_at=last,
            )
        )

    return _insight(
        code="PERFORMANCE_DROP_WITH_CONTEXT_EVENT",
        question="A queda de desempenho coincide com um evento de contexto?",
        status=ExerciseProgressionStatus.REGRESSING.value,
        severity=Severity.INFO,
        answer=answer,
        evidence=evidence,
        basis="context_event_within_72h",
        window="72h",
        keys={
            "event_type": str(event.get("event_type")),
            "event_date": start.isoformat(),
        },
        data_quality=DataQuality.LOW,
        limitations=[
            "Associação temporal, não causa.",
            "O evento é um registro do usuário (ou derivado do histórico); a regra "
            "não pesa gravidade nem duração.",
            "Leitura agregada por evento: os exercícios são evidência da coincidência, "
            "não um ranking — a regra não ordena por gravidade da queda.",
        ],
        alternatives=[
            "Coincidência com deload ou troca de equipamento.",
            "Outro evento não registrado no mesmo período.",
        ],
        action=None,
        reassess_after=max(last for _, last in affected) + timedelta(days=7),
        source_screen="/dashboard/contexto",
    )


def rule_training_pause_detected(ctx: InsightContext) -> list[dict[str, Any]]:
    """"Voce parou de treinar": N dias civis (limiar
    `insight_training_pause_min_days`) sem NENHUMA sessao ate o fim da janela.

    Por que esta regra existe e por que ela vive AQUI (decisao do usuario,
    2026-09-24): numa pausa TOTAL, as regras de volume silenciam de proposito
    (`_has_any_session`: "semana sem treino nenhum e ferias, nao deficit") e o
    painel ficava mudo justamente no caso mais obvio. Dizer "seu volume de
    biceps caiu" para quem parou ha tres semanas responde a pergunta errada; a
    observacao verdadeira e uma so, e e sobre contexto.

    Pre-requisitos (silencio quando faltam, porque silencio nao e alerta):
    - havia ROTINA antes do silencio: >= `insight_training_pause_min_sessions_before`
      dias de treino nos 28 dias que terminam na ultima sessao. Sem isso,
      "voce parou" seria dito a quem ainda nao comecou;
    - existe ao menos uma sessao (sem historico nenhum a regra nao fala).

    Severidade ATTENTION, nunca ACTION: parar de treinar pode ser exatamente a
    decisao certa (lesao, ferias, vida). O insight OBSERVA; a retomada e
    decisao do usuario. Quando ha evento de contexto cobrindo a pausa (viagem,
    doenca, lesao), ele entra na frase - continua sendo associacao, mas deixa
    de parecer que o sistema nao sabe de nada."""
    days = ctx.days_since_last_session
    if days is None or ctx.last_session_local_date is None:
        return []
    minimum = int(ctx.threshold("insight_training_pause_min_days"))
    if days < minimum:
        return []
    if int(ctx.sessions_before_pause or 0) < int(
        ctx.threshold("insight_training_pause_min_sessions_before")
    ):
        return []

    last = ctx.last_session_local_date
    # Eventos que TOCAM a pausa: entre a ultima sessao e o fim da janela.
    covering: list[Mapping[str, Any]] = []
    for event in ctx.context_events:
        start, end = _event_span(event)
        if start is None or end is None:
            continue
        if end >= last and start <= ctx.window.date_to:
            covering.append(event)

    evidence = [
        _evidence(
            "days_since_last_session",
            None,
            float(days),
            unit="dias",
            nature=DataNature.OBSERVED,
            observed_at=ctx.window.date_to,
        ),
        _evidence(
            "training_days_before_pause",
            None,
            float(int(ctx.sessions_before_pause or 0)),
            unit="dias com treino",
            nature=DataNature.OBSERVED,
            observed_at=last,
        ),
    ]

    if covering:
        labels = ", ".join(dict.fromkeys(_event_label(event) for event in covering))
        answer = (
            f"Nenhuma sessão registrada há {days} dias (última em {last.isoformat()}). "
            f"No período há evento de contexto: {labels}."
        )
    else:
        answer = (
            f"Nenhuma sessão registrada há {days} dias (última em {last.isoformat()}). "
            "Nenhum evento de contexto anotado no período."
        )

    return [
        _insight(
            code="TRAINING_PAUSE_DETECTED",
            question="Houve uma pausa no treino?",
            status="PAUSED",
            severity=Severity.ATTENTION,
            answer=answer,
            evidence=evidence,
            basis="days_since_last_session_over_threshold",
            window=f"{days}d",
            keys={"last_session": last.isoformat()},
            data_quality=DataQuality.HIGH,
            limitations=[
                "Sessão não registrada é indistinguível de sessão que não aconteceu: treino fora "
                "do app não aparece aqui.",
                context_limitation(ctx),
            ],
            alternatives=[
                "Treinos aconteceram e não foram registrados.",
                "Pausa deliberada (férias, recuperação de lesão, vida fora do treino).",
            ],
            action=(
                "Sugestão: retomar pela prontidão, não pela carga anterior — as metas semanais só "
                "voltam a ser comparáveis depois de duas semanas de treino regular. Se a pausa "
                "teve motivo, registre-o em /dashboard/contexto."
            ),
            reassess_after=ctx.window.date_to + timedelta(days=7),
            source_screen="/dashboard/contexto",
        )
    ]


def rule_data_quality_unmapped_sets(ctx: InsightContext) -> list[dict[str, Any]]:
    """Qualidade comprometendo a leitura: > 20% das series de trabalho da
    janela pertencem a exercicios sem vinculo canonico. So avalia com
    >= `insight_dq_min_sets` series (base pequena nao sustenta percentual)."""
    total = int(ctx.working_sets_total or 0)
    unmapped = int(ctx.unmapped_working_sets or 0)
    if total < int(ctx.threshold("insight_dq_min_sets")):
        return []
    ratio = unmapped / total
    if ratio <= float(ctx.threshold("insight_dq_unmapped_sets_max_ratio")):
        return []
    return [
        _insight(
            code="DATA_QUALITY_UNMAPPED_SETS",
            question="A qualidade dos dados está comprometendo as conclusões?",
            status="COMPROMISED",
            severity=Severity.ACTION,
            answer=(
                f"{unmapped} de {total} séries de trabalho ({_fmt(ratio * 100, 0)}%) são de exercícios sem "
                "vínculo canônico: volume por grupo e progressão ficam subestimados."
            ),
            evidence=[
                _evidence(
                    "unmapped_working_sets_ratio", None, ratio * 100, unit="%", nature=DataNature.DERIVED,
                    observed_at=ctx.window.date_to,
                ),
                _evidence("working_sets_total", None, total, unit="séries", nature=DataNature.OBSERVED),
            ],
            basis="working_sets_without_canonical_mapping",
            window=ctx.window.kind.value,
            keys={"window": ctx.window.kind.value, "date_to": ctx.window.date_to.isoformat()},
            data_quality=DataQuality.HIGH,
            limitations=[],
            alternatives=["Importação recente ainda não vinculada."],
            action="Vincular os exercícios sem mapping em /dominio-exercicios (fila de não mapeados).",
            reassess_after=ctx.window.date_to + timedelta(days=7),
            source_screen="/dashboard/data-quality",
        )
    ]


# ---------------------------------------------------------------------------
# Registro das regras e avaliacao
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleSpec:
    code: str
    fn: Callable[[InsightContext], list[dict[str, Any]]]
    enabled: bool = True


RULES: tuple[RuleSpec, ...] = (
    # Emite `MUSCLE_BELOW_MIN_TWO_WEEKS` por grupo ou, quando a maioria dos
    # grupos com meta dispara, UM `TRAINING_VOLUME_BROADLY_BELOW_MIN`.
    RuleSpec("MUSCLE_BELOW_MIN_TWO_WEEKS", rule_muscle_below_min_consecutive_weeks),
    RuleSpec("MUSCLE_ABOVE_MAX_READINESS_STRAINED", rule_muscle_above_max_readiness_strained),
    RuleSpec("EXERCISE_PLATEAU_SUSPECTED", rule_exercise_plateau_suspected),
    RuleSpec("EXERCISES_REGRESSING", rule_exercises_regressing),
    RuleSpec("PLAN_ITEM_SKIPPED_AT_END", rule_plan_item_skipped_at_end),
    RuleSpec("DIRECT_FAR_BELOW_INDIRECT", rule_direct_far_below_indirect),
    RuleSpec("SIBLING_ASYMMETRY", rule_sibling_asymmetry),
    RuleSpec("RECOMPOSITION_COMPATIBLE", rule_recomposition_compatible),
    # F2.1: `context_events` (migracao 048) existe - regra LIGADA.
    RuleSpec("PERFORMANCE_DROP_WITH_CONTEXT_EVENT", rule_performance_drop_with_context_event),
    # F2.1: a pausa TOTAL e assunto de contexto, nao da regra de volume (que
    # silencia de proposito quando nao houve sessao nenhuma na semana).
    RuleSpec("TRAINING_PAUSE_DETECTED", rule_training_pause_detected),
    RuleSpec("DATA_QUALITY_UNMAPPED_SETS", rule_data_quality_unmapped_sets),
)


def enabled_rule_codes() -> list[str]:
    return [rule.code for rule in RULES if rule.enabled]


def disabled_rule_codes() -> list[str]:
    return [rule.code for rule in RULES if not rule.enabled]


def sort_insights(insights: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Severidade (ACTION > ATTENTION > INFO), depois confianca (HIGH > ... >
    INSUFFICIENT), depois codigo e impressao - ordem total e deterministica."""
    return sorted(
        (dict(item) for item in insights),
        key=lambda item: (
            _SEVERITY_RANK[Severity(item["severity"])],
            _QUALITY_RANK[_quality(item["data_quality"])],
            item["code"],
            item["fingerprint"],
        ),
    )


def dedupe_by_fingerprint(insights: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Primeira ocorrencia de cada `fingerprint` vence (a lista ja vem
    ordenada por severidade/confianca)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in insights:
        if item["fingerprint"] in seen:
            continue
        seen.add(item["fingerprint"])
        out.append(dict(item))
    return out


def evaluate_insights(ctx: InsightContext) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Roda as regras LIGADAS; devolve `(insights ordenados e deduplicados,
    contagem de disparos por CODIGO emitido)` - toda regra ligada aparece
    com zero; um codigo consolidado (colapso) so aparece quando dispara. Uma
    regra que lance excecao e reportada como zero disparos - nunca derruba a
    visao geral - e o erro vai para o log de quem chama (o overview_service
    loga `insight.rule_failed`)."""
    triggered: list[dict[str, Any]] = []
    counts: dict[str, int] = {rule.code: 0 for rule in RULES if rule.enabled}
    for rule in RULES:
        if not rule.enabled:
            continue
        produced = rule.fn(ctx)
        for item in produced:
            counts[item["code"]] = counts.get(item["code"], 0) + 1
        triggered.extend(produced)
    ordered = dedupe_by_fingerprint(sort_insights(triggered))
    return ordered, counts


def select_next_actions(insights: Sequence[Mapping[str, Any]], max_items: int) -> list[dict[str, Any]]:
    """As `max_items` primeiras (a lista ja esta ordenada). Nunca inventa
    sugestao para preencher: lista vazia e resposta valida."""
    return [dict(item) for item in list(insights)[: max(0, int(max_items))]]


__all__ = [
    "COLLAPSED_CODES",
    "COOLDOWN_KEYS_BY_CODE",
    "alert_key",
    "cooldown_keys_for",
    "CONTEXT_EVENTS_ASSOCIATION_LIMITATION",
    "CONTEXT_EVENTS_EMPTY_LIMITATION",
    "CONTEXT_EVENTS_UNAVAILABLE_LIMITATION",
    "context_limitation",
    "INSIGHT_RULES_VERSION",
    "InsightContext",
    "RULES",
    "RuleSpec",
    "WAIST_UNAVAILABLE_LIMITATION",
    "build_fingerprint",
    "dedupe_by_fingerprint",
    "disabled_rule_codes",
    "enabled_rule_codes",
    "evaluate_insights",
    "select_next_actions",
    "sort_insights",
]
