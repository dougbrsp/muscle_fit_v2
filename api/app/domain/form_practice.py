"""Aprimoramento de tecnica por execucao (issue #196, ADR 0017, mapa §105).

DONO da regra "esta execucao conta para a progressao?". Python puro, sem
banco. A marca e por linha de `exercises` (coluna `form_practice`, migracao
120): no Modo Treino a marca pode ser feita ou desfeita ate a 1a serie de
TRABALHO do exercicio e trava ali (D2; `form_practice_locked`,
`resolve_form_practice_before_work`, `sql_working_sets_recorded`); pelo
Registro de exercicios ela pode ser corrigida depois (#205), com a progressao
reavaliada (`resolve_form_practice`).

A execucao marcada (D1):

- EXCLUI: progressao (avanco, regressao, plato, sequencias de sessoes
  qualificadas, mudanca de fase, ajustes de carga/reps/series/tecnicas),
  calibracao de maquina, recorde, e1RM e referencia de carga (carga de
  partida, sessao anterior, desempenho recente, exposicoes/veredito).
- MANTEM: volume semanal, recuperacao/fadiga (inclusive a fadiga previa dos
  exercicios seguintes e o teto de ativacao), tonelagem, tempo de treino e
  aderencia.

Consumidores so usam o que esta aqui: `sql_counts_for_progression(alias)` no
SQL, `counts_for_progression(valor)` em linhas ja carregadas e
`is_form_practice(valor)` para ler a coluna; `neutral_set_outcome` (leitura
neutra + sem carga da proxima serie) na rota da serie e no job `evaluate_set`;
`neutral_engine_context` (sem decisao/deload, sem conjunto de regras) e
`neutral_ai_suggestion` (sem carga/reps sugeridas, categoria de "manter") na
avaliacao por IA; `sql_plan_item_marked(alias)` +
`inherit_form_practice(enviado, herdado)` na criacao da continuacao. No historico do motor a visao
pronta e `SessionRecord.progression_view` /
`SessionRecord.progression_view_all_goals` (`domain/training_history.py`).
"""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # import real criaria ciclo: effective_intensity -> training_history -> este modulo
    from ..engine_decision_context import PairEngineContext
    from .effective_intensity import SetIntensityReading

FORM_PRACTICE_COLUMN: Final[str] = "form_practice"

# Motivo da avaliacao PULADA no `finish` quando toda execucao da sessao esta
# marcada (decisao neutra, nada gravado) - `GoalEvaluation.skipped_reason`.
SKIP_REASON_FORM_PRACTICE: Final[str] = "form_practice"

_TRUE_TEXT = frozenset({"1", "true"})


def is_form_practice(value: Any) -> bool:
    """Leitura tolerante da coluna (pymysql, Data API, JSON): None/ausente,
    0, False, "0" e qualquer valor ilegivel = nao marcada (o comportamento de
    todo dado anterior a migracao 120)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_TEXT
    return False


def counts_for_progression(value: Any) -> bool:
    """A execucao com este valor de `form_practice` conta para progressao,
    calibracao, recorde, e1RM e referencia de carga?"""
    return not is_form_practice(value)


_SQL_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _alias(alias: str) -> str:
    if not _SQL_ALIAS_RE.match(alias or ""):
        raise ValueError(f"Alias SQL invalido para form_practice: {alias!r}")
    return alias


def sql_counts_for_progression(alias: str) -> str:
    """A linha `alias` de `exercises` conta para progressao, calibracao,
    recorde, e1RM e referencia de carga (NAO e aprimoramento de tecnica)."""
    return f"{_alias(alias)}.{FORM_PRACTICE_COLUMN} = 0"


def sql_plan_item_marked(alias: str) -> str:
    """Expressao SQL agregada: alguma linha `alias` de `exercises` do grupo
    (a rota agrupa/filtra por `activity_id` + `workout_plan_exercise_id`) esta
    marcada? Vale 1 se sim, 0 se nao e NULL sem linha (`is_form_practice`
    le NULL como nao marcada). Uso: `SELECT {sql_plan_item_marked('e')} AS
    marked FROM exercises e WHERE e.activity_id = %s AND
    e.workout_plan_exercise_id = %s`; o resultado vai para `inherit_form_practice`."""
    return f"MAX({_alias(alias)}.{FORM_PRACTICE_COLUMN})"


def inherit_form_practice(sent: Any, inherited: Any = None) -> bool:
    """Marca da nova linha de `exercises` da sessao. O valor ENVIADO pelo
    cliente (True ou False) vale, inclusive para desmarcar a continuacao por
    troca de equipamento ou a 2a execucao do mesmo item (#213). `sent` None =
    campo nao enviado (fila offline antiga, relogio): vale a heranca, isto e,
    marcada qualquer linha anterior do MESMO item da ficha na sessao, a nova
    nasce marcada. Treino livre (sem item): a rota passa `inherited=None`."""
    return is_form_practice(inherited) if sent is None else is_form_practice(sent)


def form_practice_locked(working_sets_recorded: Any) -> bool:
    """A marca do exercicio esta travada? Ela pode ser feita ou desfeita ate a
    1a serie de TRABALHO gravada (#213/#211); aquecimento, ajuste e demais
    preparatorias nao travam. `working_sets_recorded` = quantas series de
    trabalho o exercicio ja tem (`sql_working_sets_recorded`). Depois de travada
    so o Registro muda a marca (`resolve_form_practice`)."""
    try:
        return int(working_sets_recorded or 0) > 0
    except (TypeError, ValueError):
        return False


def resolve_form_practice_before_work(requested: Any, stored: Any, working_sets_recorded: Any) -> bool:
    """Marca vigente apos um pedido de mudanca no registro de serie: `requested`
    None (nao enviado) ou exercicio travado = a gravada vale, sem erro (a fila
    offline pode reenviar o pedido tarde); senao vale o pedido."""
    if requested is None or form_practice_locked(working_sets_recorded):
        return is_form_practice(stored)
    return is_form_practice(requested)


def sql_working_sets_recorded(alias: str = "es", *, locking: bool = False) -> str:
    """SQL com `exercise_id = %s`: quantas series de trabalho (qualquer estado,
    pendente ou nao) o exercicio ja tem, coluna `n`. `locking=True`: leitura
    corrente (`FOR SHARE`), que enxerga o que outra transacao ja confirmou
    (a leitura comum ficaria no snapshot do REPEATABLE READ)."""
    from ..set_types import sql_is_working

    return (
        f"SELECT COUNT(*) AS n FROM exercise_sets {_alias(alias)} "
        f"WHERE {alias}.exercise_id = %s AND {sql_is_working(alias + '.set_type')}"
        + (" FOR SHARE" if locking else "")
    )


def resolve_form_practice(sent: Any, stored: Any) -> bool:
    """Marca resultante da edicao pelo Registro (#205): `sent` None (campo
    omitido: Telegram/MCP, clientes antigos) preserva a gravada; True/False
    substitui. Quem chama compara com `is_form_practice(stored)` para saber se
    mudou."""
    return is_form_practice(stored) if sent is None else is_form_practice(sent)


def neutral_set_reading(reading: "SetIntensityReading") -> "SetIntensityReading":
    """Leitura da serie de uma execucao marcada (D1), para a rota de registro
    da serie e para a avaliacao por IA (job `evaluate_set`).

    Efeito para o usuario: a serie de aprimoramento de tecnica nao recebe
    veredito de esforco (status) nem posicao de reps na faixa, e nao ha carga
    sugerida para a proxima serie (quem chama devolve a carga da proxima serie
    como `None`). O evento de seguranca (RPE contra o teto), o RPE, o alvo, o
    teto e a permissao de falha ficam como vieram de
    `effective_intensity.set_intensity_reading`."""
    return replace(reading, status=None, reps=None, reps_min=None, reps_max=None, reps_position=None)


def neutral_set_outcome(reading: "SetIntensityReading") -> "tuple[SetIntensityReading, None]":
    """Desfecho da serie de uma execucao marcada (D1): `(leitura neutra,
    carga da proxima serie ausente)`. Fonte unica para a rota de registro da
    serie (`workout_sessions._evaluate_set`) e para o job `evaluate_set`;
    nenhum consumidor escreve o `None` da proxima carga por conta propria."""
    return neutral_set_reading(reading), None


def neutral_engine_context(engine: "PairEngineContext") -> "tuple[PairEngineContext, None]":
    """Fatos do motor entregues a avaliacao por IA de uma execucao marcada
    (D1): `(copia do contexto sem decisao nem prescricao de deload, conjunto
    de regras da proxima serie ausente)`. O conjunto publicado nao e lido nem
    enviado; o original fica intacto."""
    return replace(engine, decision=None, deload=None), None


def neutral_ai_suggestion(result: dict[str, Any]) -> dict[str, Any]:
    """Saida da avaliacao por IA de uma execucao marcada (D1): a IA comenta a
    serie, mas nao sugere carga nem reps para a proxima e nao classifica como
    aumentar/reduzir. A categoria vira a de "manter" do dono das categorias
    (`set_evaluation_categories.HOLD`, rotulo de `category_label`); a categoria
    original da IA fica em `category_from_ai` (campo ja existente). `load_adjustments`
    sai vazio: todas as suas entradas (`field: load_kg`, de `bound_ai_load`) sao
    sobre a carga sugerida, que aqui nao existe; `clamps` e `suggested_next_rpe`
    ficam como vieram. Idempotente;
    devolve copia."""
    # import local: set_evaluation_categories -> exercise_briefing -> training_history -> este modulo
    from .set_evaluation_categories import HOLD, SET_EVALUATION_CATEGORIES, category_label

    out = dict(result)
    out["suggested_next_load_kg"] = None
    out["suggested_next_reps"] = None
    out["load_adjustments"] = []
    category = result.get("category")
    if category in SET_EVALUATION_CATEGORIES and category != HOLD and "category_from_ai" not in out:
        out["category_from_ai"] = category
    out["category"] = HOLD
    out["category_label"] = category_label(HOLD)
    return out


__all__ = [
    "FORM_PRACTICE_COLUMN",
    "SKIP_REASON_FORM_PRACTICE",
    "counts_for_progression",
    "form_practice_locked",
    "inherit_form_practice",
    "resolve_form_practice_before_work",
    "sql_working_sets_recorded",
    "is_form_practice",
    "neutral_ai_suggestion",
    "neutral_engine_context",
    "neutral_set_outcome",
    "neutral_set_reading",
    "resolve_form_practice",
    "sql_counts_for_progression",
    "sql_plan_item_marked",
]
