"""Qualidade dos dados do usuario (plano §8.3, §10, §15).

A pergunta da tela `/dashboard/data-quality` nao e "quantos erros eu tenho",
e **"da para concluir alguma coisa com o que esta registrado?"**. Por isso
cada item carrega a frase do que fica IMPOSSIVEL de afirmar enquanto o
problema existir, e um `fix_link` para a tela que resolve - um numero sem
saida nao ajuda ninguem.

Decisoes deste modulo:

- **Leitura pura**: nenhum INSERT/UPDATE, nenhum DDL. O servico nunca conserta
  nada - so aponta onde esta e para onde ir.
- **Uma query por regra, no maximo** (§15): contagem e exemplos saem do MESMO
  SELECT, via `COUNT(*) OVER ()` / `SUM(n) OVER ()` (janela avaliada antes do
  `LIMIT`). Nada de "conta primeiro, busca exemplos depois" - seriam 20
  queries.
- **`user_id` obrigatorio** em todo WHERE, por `require_user_id` (§15). Um
  exemplo de outro usuario na tela de qualidade seria IDOR.
- **Exemplos limitados** (`dq_max_examples`, 5) e reduzidos a `id` + rotulo
  legivel: a resposta tem que caber com folga no teto de 1 MB da RDS Data API
  (`db_dataapi.py:86-95`) mesmo com 10 regras.
- **Severidade vem do registry**, fixa por regra: `SETS_WITHOUT_RPE` e
  informativo com 1 ou com 10.000 series. A contagem mede o tamanho do
  problema, nao muda a natureza dele.
- **Ausencia de dado nao e zero**: regra que nao POde ser avaliada (a coluna
  `load_convention` ainda nao existe no schema, janela de deploy) devolve
  `evaluated=False` e vira uma limitacao declarada na resposta - nunca um
  "0 problemas" que a pessoa leria como "esta tudo certo".

Escopo (`DataQualityScope`): regras de CADASTRO (contribuicao muscular,
convencao do par, meta do grupo) ignoram a janela de proposito e olham TODO o
historico do usuario - uma lacuna de cadastro nao deixa de existir porque o
periodo selecionado e curto. As demais contam ocorrencias dentro da janela.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from ...exercise_mapping_canonical import UNRESOLVED_LINK_CONDITION, UNRESOLVED_LINK_JOINS
from ...load_conventions import COLUMN as LOAD_CONVENTION_COLUMN
from ...load_conventions import has_load_convention_column
from ...observability import log_event
from ...set_types import sql_is_unknown_set_type, sql_is_working, sql_set_confirmed, sql_set_type_expr
from .contracts import DataQuality, DataQualityScope, Severity, WindowInfo
from .identity import require_user_id
from .metrics_registry import DATA_QUALITY_RULES, DataQualityRule, data_quality_rule_meta, metric_version, threshold

MAX_EXAMPLES = int(threshold("dq_max_examples"))
STALE_IN_PROGRESS_HOURS = int(threshold("dq_in_progress_stale_hours"))
WEIGHT_DIVERGENCE_KG = float(threshold("dq_weight_divergence_kg"))

# Id canonico do exercicio: o mesmo COALESCE de `exposure_service`/`analytics`
# (um exercicio absorvido pela consolidacao do catalogo continua no historico).
_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
_SESSION_HAS_EXERCISES = "EXISTS (SELECT 1 FROM exercises x WHERE x.activity_id = a.id)"


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityContext:
    """Tudo que as regras compartilham. Resolvido UMA vez por requisicao."""

    user_id: int
    window: WindowInfo
    now_local: datetime
    training_type_id: Optional[int] = None
    max_examples: int = MAX_EXAMPLES


@dataclass
class RuleResult:
    """Resultado de UMA regra. `evaluated=False` = a regra nao pode rodar
    neste schema; `count` fica 0 mas o item NAO e apresentado como "sem
    problema" (vira limitacao declarada na resposta)."""

    rule: DataQualityRule
    count: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    evaluated: bool = True


# ---------------------------------------------------------------------------
# Helpers de leitura
# ---------------------------------------------------------------------------


def _int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value)
    return int(value)


def _text(value: Any, fallback: str) -> str:
    text = "" if value is None else str(value).strip()
    return text or fallback


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return None if value is None else str(value)


def _run(cursor: Any, sql: str, params: Sequence[Any]) -> tuple[int, list[dict[str, Any]]]:
    """Executa a query da regra e devolve `(total, linhas_de_exemplo)`.

    O total vem da coluna `total_count` (funcao de janela) da PRIMEIRA linha:
    sem linha nenhuma, nao ha ocorrencia."""
    cursor.execute(sql, tuple(params))
    rows = cursor.fetchall() or []
    if not rows:
        return 0, []
    return _int(rows[0].get("total_count")), list(rows)


# ---------------------------------------------------------------------------
# Regras (§8.3) - uma funcao, uma query
# ---------------------------------------------------------------------------

_SQL_EXERCISES_WITHOUT_CANONICAL_MAPPING = f"""
SELECT COUNT(*) OVER () AS total_count,
       e.id           AS example_id,
       e.original_name AS example_label,
       a.local_date   AS example_date
FROM `exercises` e
JOIN activities a ON a.id = e.activity_id
{UNRESOLVED_LINK_JOINS}
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {UNRESOLVED_LINK_CONDITION}
ORDER BY a.local_date DESC, e.id DESC
LIMIT %s
"""


def rule_exercises_without_canonical_mapping(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Exercicio registrado que nao resolve para `exercise_catalog` ativo.

    Mesma condicao da fila de `GET /mapping-canonical/unmapped`
    (`UNRESOLVED_LINK_JOINS`/`UNRESOLVED_LINK_CONDITION`, importadas dali):
    duas definicoes de "nao mapeado" nao podem existir."""
    total, rows = _run(
        cursor,
        _SQL_EXERCISES_WITHOUT_CANONICAL_MAPPING,
        (ctx.user_id, ctx.window.date_from, ctx.window.date_to, ctx.max_examples),
    )
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Exercicio sem nome"),
            "detail": _iso(row.get("example_date")),
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING, total, exemplos)


_SQL_MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION = f"""
SELECT COUNT(*) OVER () AS total_count,
       t.canonical_id AS example_id,
       t.name         AS example_label
FROM (
  SELECT {_CANONICAL} AS canonical_id, MIN(ec.name) AS name
  FROM `exercises` e
  JOIN activities a ON a.id = e.activity_id
  LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
  JOIN exercise_catalog ec ON ec.id = {_CANONICAL} AND ec.is_active = 1
  WHERE a.user_id = %s
  GROUP BY canonical_id
) t
LEFT JOIN exercise_muscle_contributions emc
  ON emc.exercise_id = t.canonical_id AND emc.is_active = 1
WHERE emc.id IS NULL
ORDER BY t.name ASC
LIMIT %s
"""


def rule_mappings_without_muscle_contribution(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Exercicio canonico que o usuario JA TREINOU e que nao distribui
    estimulo para nenhum grupo (`exercise_muscle_contributions` sem linha
    ativa). Cadastro: olha todo o historico, nao a janela."""
    total, rows = _run(cursor, _SQL_MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION, (ctx.user_id, ctx.max_examples))
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Exercicio do catalogo"),
            "detail": None,
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION, total, exemplos)


_SQL_SETS_WITH_UNKNOWN_SET_TYPE = f"""
SELECT SUM(t.n) OVER () AS total_count,
       t.set_type_value AS example_label,
       t.n              AS example_count
FROM (
  SELECT {sql_set_type_expr("es.set_type")} AS set_type_value, COUNT(*) AS n
  FROM exercise_sets es
  JOIN `exercises` e ON e.id = es.exercise_id
  JOIN activities a ON a.id = e.activity_id
  WHERE a.user_id = %s
    AND a.local_date BETWEEN %s AND %s
    AND {sql_is_unknown_set_type("es.set_type")}
    AND {sql_set_confirmed("es.reps_pending")}
  GROUP BY set_type_value
) t
ORDER BY t.n DESC, t.set_type_value ASC
LIMIT %s
"""


def rule_sets_with_unknown_set_type(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Series cujo `set_type` nao esta no vocabulario de `set_types.py` (nem
    canonico, nem alias legado). A contagem e de SERIES; os exemplos sao os
    valores encontrados, com quantas series cada um tem."""
    total, rows = _run(
        cursor,
        _SQL_SETS_WITH_UNKNOWN_SET_TYPE,
        (ctx.user_id, ctx.window.date_from, ctx.window.date_to, ctx.max_examples),
    )
    exemplos = [
        {
            "id": None,
            "label": _text(row.get("example_label"), "(vazio)"),
            "detail": f"{_int(row.get('example_count'))} serie(s)",
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.SETS_WITH_UNKNOWN_SET_TYPE, total, exemplos)


_SQL_EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION = f"""
SELECT COUNT(*) OVER () AS total_count,
       eqex.id   AS example_id,
       eq.name   AS example_equipment,
       ec.name   AS example_exercise
FROM (
  SELECT DISTINCT ge.equipment_id AS equipment_id, {_CANONICAL} AS canonical_id
  FROM `exercises` e
  JOIN activities a ON a.id = e.activity_id
  LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
  JOIN gym_equipment ge ON ge.id = e.equipment_id
  WHERE a.user_id = %s AND ge.equipment_id IS NOT NULL
) u
JOIN equipment_exercises eqex
  ON eqex.equipment_id = u.equipment_id AND eqex.exercise_id = u.canonical_id AND eqex.is_active = 1
JOIN equipment eq ON eq.id = u.equipment_id
JOIN exercise_catalog ec ON ec.id = u.canonical_id
WHERE eqex.{LOAD_CONVENTION_COLUMN} IS NULL
ORDER BY eq.name ASC, ec.name ASC
LIMIT %s
"""


def rule_equipment_pairs_without_load_convention(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Pares (equipamento, exercicio) que o historico do usuario usa e que
    continuam sem `load_convention` (migracao 045).

    Tolerancia a janela de deploy: se a coluna ainda nao existe NESTE schema
    (`has_load_convention_column`, cache por schema), a regra devolve
    `evaluated=False` em vez de estourar 500 - e a limitacao aparece na
    resposta, para ninguem ler 0 como "tudo cadastrado"."""
    if not has_load_convention_column(cursor):
        return RuleResult(
            DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION,
            count=0,
            evaluated=False,
            limitations=[
                "A convencao de carga por par (migracao 045) ainda nao existe neste banco: "
                "a regra nao pode ser avaliada e todos os pares seguem lidos como 'Carga total'."
            ],
        )

    total, rows = _run(cursor, _SQL_EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION, (ctx.user_id, ctx.max_examples))
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": f"{_text(row.get('example_equipment'), 'Equipamento')} - "
            f"{_text(row.get('example_exercise'), 'Exercicio')}",
            "detail": None,
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION, total, exemplos)


_SQL_SETS_WITHOUT_RPE = f"""
SELECT SUM(t.n) OVER () AS total_count,
       t.activity_id   AS example_id,
       t.activity_name AS example_label,
       t.local_date    AS example_date,
       t.n             AS example_count
FROM (
  SELECT a.id AS activity_id, a.name AS activity_name, a.local_date AS local_date, COUNT(*) AS n
  FROM exercise_sets es
  JOIN `exercises` e ON e.id = es.exercise_id
  JOIN activities a ON a.id = e.activity_id
  WHERE a.user_id = %s
    AND a.local_date BETWEEN %s AND %s
    AND es.rpe IS NULL
    AND {sql_is_working("es.set_type")}
    AND {sql_set_confirmed("es.reps_pending")}
  GROUP BY a.id, a.name, a.local_date
) t
ORDER BY t.local_date DESC, t.activity_id DESC
LIMIT %s
"""


def rule_sets_without_rpe(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Series de TRABALHO sem RPE (aquecimento nao entra: nao ter RPE ali e o
    esperado). Informativo: sem RPE nao ha RIR e o e1RM nao e estimado, mas
    carga e repeticoes continuam comparaveis."""
    total, rows = _run(
        cursor,
        _SQL_SETS_WITHOUT_RPE,
        (ctx.user_id, ctx.window.date_from, ctx.window.date_to, ctx.max_examples),
    )
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Sessao"),
            "detail": f"{_iso(row.get('example_date'))} - {_int(row.get('example_count'))} serie(s)",
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.SETS_WITHOUT_RPE, total, exemplos)


_SQL_SESSIONS_WITHOUT_FINAL_STATUS = f"""
SELECT COUNT(*) OVER () AS total_count,
       a.id             AS example_id,
       a.name           AS example_label,
       a.local_date     AS example_date,
       a.session_status AS example_status
FROM activities a
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {_SESSION_HAS_EXERCISES}
  AND (
    a.session_status IS NULL
    OR (a.session_status = 'in_progress' AND COALESCE(a.started_at, a.created_at) < %s)
  )
ORDER BY a.local_date DESC, a.id DESC
LIMIT %s
"""


def rule_sessions_without_final_status(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Sessao com `session_status` nulo (importacao legada) ou parada em
    `in_progress` ha mais de 24h - "sessao ao vivo que ninguem finalizou".

    O corte usa a hora do USUARIO (`now_local`, fuso do perfil), nunca
    `NOW()` do servidor (Lambda em UTC)."""
    limite = ctx.now_local - timedelta(hours=STALE_IN_PROGRESS_HOURS)
    total, rows = _run(
        cursor,
        _SQL_SESSIONS_WITHOUT_FINAL_STATUS,
        (ctx.user_id, ctx.window.date_from, ctx.window.date_to, limite, ctx.max_examples),
    )
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Sessao"),
            "detail": f"{_iso(row.get('example_date'))} - "
            f"{_text(row.get('example_status'), 'sem status')}",
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.SESSIONS_WITHOUT_FINAL_STATUS, total, exemplos)


_SQL_SESSIONS_NOT_LINKED_TO_PLAN = f"""
SELECT COUNT(*) OVER () AS total_count,
       a.id         AS example_id,
       a.name       AS example_label,
       a.local_date AS example_date
FROM activities a
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND a.workout_plan_id IS NULL
  AND {_SESSION_HAS_EXERCISES}
  AND EXISTS (
    SELECT 1
    FROM workout_plan_template_assignments wta
    JOIN workout_plan_templates wt ON wt.id = wta.template_id
    WHERE wta.user_id = a.user_id AND wta.unassigned_at IS NULL AND wt.is_active = 1
  )
ORDER BY a.local_date DESC, a.id DESC
LIMIT %s
"""


def rule_sessions_not_linked_to_plan(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Sessao sem `workout_plan_id` QUANDO o usuario tem Modelo atribuido.

    A condicao do Modelo esta no proprio SQL (`EXISTS` correlacionado por
    `a.user_id`): sem Modelo atribuido nao ha o que aderir, e a regra devolve
    zero em vez de acusar todas as sessoes."""
    total, rows = _run(
        cursor,
        _SQL_SESSIONS_NOT_LINKED_TO_PLAN,
        (ctx.user_id, ctx.window.date_from, ctx.window.date_to, ctx.max_examples),
    )
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Sessao"),
            "detail": _iso(row.get("example_date")),
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.SESSIONS_NOT_LINKED_TO_PLAN, total, exemplos)


_SQL_DUPLICATE_WEIGHT_SAME_DAY = """
SELECT COUNT(*) OVER () AS total_count,
       t.dia       AS example_date,
       t.min_kg    AS example_min,
       t.max_kg    AS example_max,
       t.n_sources AS example_sources
FROM (
  SELECT d.dia AS dia,
         MIN(d.weight_kg) AS min_kg,
         MAX(d.weight_kg) AS max_kg,
         COUNT(DISTINCT d.fonte) AS n_sources
  FROM (
    SELECT bc.local_date AS dia, bc.weight_kg AS weight_kg,
           COALESCE(bc.source, 'body_composition') AS fonte
    FROM body_composition bc
    WHERE bc.user_id = %s AND bc.weight_kg IS NOT NULL AND bc.local_date BETWEEN %s AND %s
    UNION ALL
    SELECT db.local_date, db.weight_kg, 'daily_biometrics'
    FROM daily_biometrics db
    WHERE db.user_id = %s AND db.weight_kg IS NOT NULL AND db.local_date BETWEEN %s AND %s
    UNION ALL
    SELECT iw.wellness_date, iw.weight_kg, 'intervals_wellness'
    FROM intervals_wellness iw
    WHERE iw.user_id = %s AND iw.weight_kg IS NOT NULL AND iw.wellness_date BETWEEN %s AND %s
  ) d
  GROUP BY d.dia
  HAVING COUNT(DISTINCT d.fonte) > 1 AND (MAX(d.weight_kg) - MIN(d.weight_kg)) > %s
) t
ORDER BY t.dia DESC
LIMIT %s
"""


def rule_duplicate_weight_same_day(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Pesagens do mesmo dia, de fontes diferentes, divergindo mais de 1 kg.

    As tres fontes de peso do sistema entram na MESMA query por `UNION ALL`
    (`body_composition`, `daily_biometrics`, `intervals_wellness`). Fontes
    diferentes com valores parecidos nao sao problema - o limiar existe para
    nao acusar arredondamento de balanca."""
    janela = (ctx.user_id, ctx.window.date_from, ctx.window.date_to)
    total, rows = _run(
        cursor,
        _SQL_DUPLICATE_WEIGHT_SAME_DAY,
        (*janela, *janela, *janela, WEIGHT_DIVERGENCE_KG, ctx.max_examples),
    )
    exemplos = []
    for row in rows:
        menor = float(row.get("example_min") or 0)
        maior = float(row.get("example_max") or 0)
        exemplos.append(
            {
                "id": None,
                "label": _iso(row.get("example_date")) or "Dia",
                "detail": f"{menor:.1f} kg vs {maior:.1f} kg em "
                f"{_int(row.get('example_sources'))} fontes",
            }
        )
    return RuleResult(DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY, total, exemplos)


_SQL_ACTIVITIES_WITHOUT_STARTED_AT = f"""
SELECT COUNT(*) OVER () AS total_count,
       a.id         AS example_id,
       a.name       AS example_label,
       a.local_date AS example_date
FROM activities a
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND a.started_at IS NULL
  AND {_SESSION_HAS_EXERCISES}
ORDER BY a.local_date DESC, a.id DESC
LIMIT %s
"""


def rule_activities_without_started_at(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Sessao sem `started_at`: nao da para ordenar as exposicoes dentro do
    dia nem medir o intervalo entre estimulos do mesmo grupo."""
    total, rows = _run(
        cursor,
        _SQL_ACTIVITIES_WITHOUT_STARTED_AT,
        (ctx.user_id, ctx.window.date_from, ctx.window.date_to, ctx.max_examples),
    )
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Sessao"),
            "detail": _iso(row.get("example_date")),
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.ACTIVITIES_WITHOUT_STARTED_AT, total, exemplos)


_SQL_MUSCLE_GROUPS_WITHOUT_TARGET = f"""
SELECT COUNT(*) OVER () AS total_count,
       t.muscle_group_id AS example_id,
       t.group_name      AS example_label
FROM (
  SELECT mg.id AS muscle_group_id, mg.name AS group_name
  FROM `exercises` e
  JOIN activities a ON a.id = e.activity_id
  LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
  JOIN exercise_muscle_contributions emc
    ON emc.exercise_id = {_CANONICAL} AND emc.is_active = 1
  JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
  WHERE a.user_id = %s
  GROUP BY mg.id, mg.name
) t
LEFT JOIN training_type_muscle_group_targets tgt
  ON tgt.muscle_group_id = t.muscle_group_id AND tgt.training_type_id = %s
 AND tgt.superseded_at IS NULL
WHERE tgt.id IS NULL
ORDER BY t.group_name ASC
LIMIT %s
"""


def rule_muscle_groups_without_target(cursor: Any, ctx: QualityContext) -> RuleResult:
    """Grupos que o usuario treina e que caem no FALLBACK generico de meta.

    A resolucao e a que ja existe (explicita -> Modelo atribuido -> fallback):
    `ctx.training_type_id` vem de `resolve_active_training_type_for_user`.
    Com `None` (nenhum Modelo com tipo de treino), o `LEFT JOIN` nao casa com
    nada e TODOS os grupos treinados entram - que e justamente o caso em que
    toda leitura de "abaixo/acima da meta" e generica."""
    total, rows = _run(
        cursor,
        _SQL_MUSCLE_GROUPS_WITHOUT_TARGET,
        (ctx.user_id, ctx.training_type_id, ctx.max_examples),
    )
    exemplos = [
        {
            "id": _int(row.get("example_id")) or None,
            "label": _text(row.get("example_label"), "Grupo muscular"),
            "detail": None,
        }
        for row in rows
    ]
    return RuleResult(DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET, total, exemplos)


# Ordem de apresentacao: a mesma do §8.3.
RULE_FUNCTIONS: tuple[tuple[DataQualityRule, Callable[[Any, QualityContext], RuleResult]], ...] = (
    (DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING, rule_exercises_without_canonical_mapping),
    (DataQualityRule.MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION, rule_mappings_without_muscle_contribution),
    (DataQualityRule.SETS_WITH_UNKNOWN_SET_TYPE, rule_sets_with_unknown_set_type),
    (DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION, rule_equipment_pairs_without_load_convention),
    (DataQualityRule.SETS_WITHOUT_RPE, rule_sets_without_rpe),
    (DataQualityRule.SESSIONS_WITHOUT_FINAL_STATUS, rule_sessions_without_final_status),
    (DataQualityRule.SESSIONS_NOT_LINKED_TO_PLAN, rule_sessions_not_linked_to_plan),
    (DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY, rule_duplicate_weight_same_day),
    (DataQualityRule.ACTIVITIES_WITHOUT_STARTED_AT, rule_activities_without_started_at),
    (DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET, rule_muscle_groups_without_target),
)


# ---------------------------------------------------------------------------
# Derivacoes puras
# ---------------------------------------------------------------------------


def derive_overall(severidades: Sequence[Severity]) -> DataQuality:
    """Veredito sobre os DADOS (plano §8.3/§8.4).

    Qualquer `ACTION` -> `LOW`; so `ATTENTION` -> `MEDIUM`; nada ou so `INFO`
    -> `HIGH`. `INFO` NUNCA escala: series sem RPE nao derrubam a leitura do
    resto."""
    if any(item is Severity.ACTION for item in severidades):
        return DataQuality.LOW
    if any(item is Severity.ATTENTION for item in severidades):
        return DataQuality.MEDIUM
    return DataQuality.HIGH


def build_items(resultados: Sequence[RuleResult]) -> list[dict[str, Any]]:
    """Resultados -> itens da resposta, SO os que encontraram ocorrencia.

    Regra com `count = 0` nao vira item (a tela nao lista "problemas que voce
    nao tem"); regra NAO avaliada tambem nao - ela aparece como limitacao,
    porque 0 ali significaria "verificado e limpo", que e falso."""
    itens: list[dict[str, Any]] = []
    for resultado in resultados:
        if not resultado.evaluated or resultado.count <= 0:
            continue
        meta = data_quality_rule_meta(resultado.rule)
        itens.append(
            {
                "code": resultado.rule.value,
                "label": meta.label,
                "description": meta.description,
                "count": int(resultado.count),
                "severity": meta.severity,
                "scope": meta.scope,
                "fix_link": meta.fix_link,
                "examples": resultado.examples,
                "limitations": list(resultado.limitations),
            }
        )
    return itens


def _window_limitations(resultados: Sequence[RuleResult], window: WindowInfo) -> list[str]:
    limitacoes: list[str] = []
    for resultado in resultados:
        limitacoes.extend(resultado.limitations)
    if any(
        data_quality_rule_meta(item.rule).scope is DataQualityScope.CATALOG
        for item in resultados
        if item.evaluated and item.count > 0
    ):
        limitacoes.append(
            "Itens de cadastro (escopo 'catalog') consideram todo o historico e ignoram o periodo "
            f"selecionado ({window.date_from.isoformat()} a {window.date_to.isoformat()})."
        )
    return limitacoes


# ---------------------------------------------------------------------------
# Leitura completa
# ---------------------------------------------------------------------------


def collect_rule_results(conn: Any, ctx: QualityContext) -> list[RuleResult]:
    """Roda as 10 regras do §8.3 numa unica conexao (um cursor, uma query por
    regra, sem N+1)."""
    resultados: list[RuleResult] = []
    with conn.cursor() as cursor:
        for _, funcao in RULE_FUNCTIONS:
            resultados.append(funcao(cursor, ctx))
    return resultados


def build_data_quality(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    now_local: datetime,
    training_type_id: Optional[int] = None,
    max_examples: int = MAX_EXAMPLES,
) -> dict[str, Any]:
    """Resposta completa de `GET /analytics/dashboard/data-quality`.

    Emite `dq.snapshot` (plano §15) com a contagem de CADA regra - so codigos
    e numeros, nenhum nome de exercicio, sessao ou nota: o log serve para
    medir a evolucao da base, nao para reconstruir o treino de ninguem."""
    owner = require_user_id(user_id)
    ctx = QualityContext(
        user_id=owner,
        window=window,
        now_local=now_local,
        training_type_id=training_type_id,
        max_examples=max(1, min(int(max_examples), MAX_EXAMPLES)),
    )

    resultados = collect_rule_results(conn, ctx)
    itens = build_items(resultados)
    overall = derive_overall([item["severity"] for item in itens])

    nao_avaliadas = [item for item in resultados if not item.evaluated]
    limitacoes = _window_limitations(resultados, window)

    log_event(
        "dq.snapshot",
        user_id=owner,
        window=window.kind.value,
        days=window.days,
        overall=overall.value,
        rules_not_evaluated=len(nao_avaliadas) or None,
        counts={item.rule.value: int(item.count) for item in resultados if item.evaluated},
    )

    return {
        "window": window,
        "items": itens,
        "overall": overall,
        "checked_rules": len(resultados) - len(nao_avaliadas),
        # Confianca na PROPRIA resposta (diferente de `overall`, que fala dos
        # dados do usuario): uma regra que nao pode rodar derruba para MEDIUM.
        "data_quality": DataQuality.MEDIUM if nao_avaliadas else DataQuality.HIGH,
        "limitations": limitacoes,
        "metric_versions": {"data_quality": metric_version("data_quality")},
    }


__all__ = [
    "DATA_QUALITY_RULES",
    "MAX_EXAMPLES",
    "QualityContext",
    "RULE_FUNCTIONS",
    "RuleResult",
    "build_data_quality",
    "build_items",
    "collect_rule_results",
    "derive_overall",
]
