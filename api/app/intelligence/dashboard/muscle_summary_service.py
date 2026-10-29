"""Resumo por grupo muscular do Novo Dashboard (plano §6, §7 "Volume chegou
ao musculo certo?", §10, §11, §15).

A pergunta desta tela e "quais grupos estao atrasados ou em excesso, e o
volume chegou no musculo certo?". Tres numeros respondem, e eles NUNCA sao
somados na mesma metrica exibida:

- REALIZADO (`OBSERVED`/`DERIVED`): o que as series registradas na janela de
  fato geraram - fisicas, efetivas de estimulo, efetivas de fadiga e a
  separacao direto (`PRIMARY`) / indireto (`SECONDARY`) / estabilizador
  (`STABILIZER`), tres papeis SEPARADOS;
- PLANEJADO (`PLANNED`): as series alvo das fichas salvas na janela;
- PROJETADO (`PROJECTED`): o que ainda falta rodar no ciclo corrente.

`weekly_volume_status` classifica SO o realizado. A tela v2
(`dashboard-cycle`) classificava `realizado + projetado` de proposito, para
responder "se eu completar o ciclo, onde fico?"; aqui a pergunta e outra -
"onde eu ESTOU" - e somar uma projecao ao fato faria um grupo sem nenhuma
serie na janela aparecer `ON_TARGET` porque o modelo promete roda-lo depois.

## Por que este servico nao usa `get_muscle_group_overview`

`VolumeService.get_muscle_group_overview` le o historico COMPLETO do usuario
a cada chamada (`_fetch_full_history_sets`) para alimentar o modelo de
recuperacao. Medicao no dataset de 3 anos (400 sessoes / 4.000 exercicios /
16.000 series), no shim da RDS Data API: **8,3s so nessa chamada**, e o
drill-down v2 inteiro em 13,4s - acima do teto de 5s deste PR e perto do
timeout de 29s do API Gateway que ja derrubou a tela em producao (#188).

Aqui a agregacao acontece no BANCO, dentro da janela, em consultas que
devolvem dezenas a poucas centenas de linhas curtas: sem N+1, sem subconsulta
correlacionada por serie e sem ler nada fora do periodo. A prontidao continua
vindo do servico existente (nao ha um segundo modelo de recuperacao), agora
sem limite de historico: desde a M20 (F1.5) a leitura do modelo custa 3
statements, entao `include_readiness` deixou de ser opt-in e passou a vir
ligado por padrao.

## Diferenca deliberada em relacao as rotas v2

As rotas v2 somam TODA serie nao-preparatoria ao estimulo, inclusive
aquecimento, porque `_fetch_window_sets` nao filtra `set_type`. O plano §6 e
explicito: `effective_sets_stimulus = Sigma working x stimulus_weight`, com
aquecimento e serie de ajuste FORA. Este servico aplica o filtro
(`sql_not_preparatory`), entao o numero pode ficar menor que o da tela
antiga - e o certo.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Optional

from ... import request_memo
from ...domain.technique_accounting import planned_equivalent, realized_set_units
from ..methodology_volume import sql_hypertrophy_volume_weight
from ...load_conventions import equipment_exercises_join_sql, load_convention_select_expr, sql_counts_in_tonnage
from ...observability import log_event
from ...set_types import normalize_set_type, sql_not_preparatory, sql_set_confirmed
from ..methodology_volume import calendar_weekly_rate
from ..technique_volume import TechniqueSets, free_exercise_primary_sets, new_catalog, sql_application_rows, sql_work_unit, technique_sets_sql
from ..volume_service import (
    DASHBOARD_FALLBACK_MAX_SETS,
    DASHBOARD_FALLBACK_MIN_SETS,
    DASHBOARD_FALLBACK_TARGET_SETS,
    classify_planned_volume_status,
    classify_weekly_volume_status,
    template_cycle_length_days,
)
from .coercion import round_half_up, to_date, to_datetime, to_float
from .contracts import DataNature, DataQuality, TargetSource, WindowInfo
from .identity import require_user_id
from .metrics_registry import classify_heatmap_band, metric_version
from .time_windows import with_trained_at
from ...db_helpers import rows as _rows

# Exercicios por grupo no "quem mais contribuiu".
MAX_TOP_EXERCISES = 5

# Teto de series devolvidas pelo drill-down. A resposta da RDS Data API tem
# limite de 1 MB (`db_dataapi.py`, `UnsupportedResultException`): 600 series
# com ~12 campos curtos ficam na casa de 150 KB serializados, com folga larga.
# O corte e pelas sessoes MAIS RECENTES e e DECLARADO no payload
# (`sets_limit`/`sets_truncated`) - a tela precisa poder dizer "faltam
# sessoes mais antigas" em vez de exibir um total errado.
MAX_DRILLDOWN_SETS = 600

# Historico lido para a prontidao muscular: HOJE, NENHUM limite.
#
# Ate a F1.2 este servico pedia so os ultimos 60 dias (`history_days`) para o
# bloco de prontidao caber no orcamento de latencia. A M20 tornou a leitura do
# modelo de recuperacao barata (3 statements, ver
# `intelligence/recovery_read_model.py`), entao o limite saiu: a prontidao
# desta tela enxerga exatamente o mesmo historico que a geracao de ficha e o
# Modo Treino enxergam. (A constante morta `READINESS_HISTORY_DAYS = 60` saiu
# no M76 Lote 2c.)

_NOT_PREPARATORY = sql_not_preparatory("es.set_type")

# Id canonico do exercicio, mesmo padrao de `exposure_service`/`analytics.py`:
# um exercicio renomeado/absorvido continua no MESMO historico.
_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"

# Cadeia de JOIN compartilhada por todas as consultas de realizado. O filtro
# de `set_type` mora aqui: aquecimento e serie de ajuste nao geram estimulo
# (plano §6).
#
# Ela vem partida em JOINs e WHERE porque o bloco por sessao (M34) precisa
# encaixar mais um LEFT JOIN (equipamento) ANTES do WHERE. `_FROM_REALIZED`
# continua sendo a cadeia inteira, byte a byte igual ao que era - nenhuma
# consulta existente muda.
_JOINS_REALIZED = f"""
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = {_CANONICAL}
      AND emc.is_active = 1
"""

_WHERE_REALIZED = f"""WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {_NOT_PREPARATORY}
"""

_FROM_REALIZED = _JOINS_REALIZED + _WHERE_REALIZED

# Fase 5 (PR 4): series EQUIVALENTES (diretas/indiretas/estabilizadoras,
# estimulo, fadiga) somam so as series que NAO sao aplicacao de tecnica
# (`_WORK`); cada aplicacao entra depois, em Python, com a sua fatia do bloco
# da ancora (`technique_volume.TechniqueSets`, regra unica de
# `domain/technique_accounting.py`) - nunca uma segunda formula em SQL. As
# contagens FISICAS (`physical_sets`, `sets_count` do top exercicios) seguem
# contando uma linha por serie. `technique_rows` avisa se a janela tem
# aplicacao: sem ela, nenhuma leitura extra acontece.
_WORK = sql_work_unit("es.set_type")

# M97 F4: no volume HIPERTROFICO (series diretas/indiretas/estabilizadoras e de
# estimulo) a serie de forca vale o peso do dono
# (`methodology_volume.sql_hypertrophy_volume_weight`, o mesmo de
# `hypertrophy_volume_units`); fadiga e series fisicas seguem cheias. Toda
# linha de hipertrofia multiplica por 1.
_HYP = sql_hypertrophy_volume_weight("e")

# 1) Totais por (grupo, papel). Uma linha por papel de cada grupo: o resultado
#    tem dezenas de linhas, nunca uma por serie.
ROLE_TOTALS_SQL = f"""
SELECT
  emc.muscle_group_id                       AS muscle_group_id,
  UPPER(TRIM(emc.role))                     AS role,
  SUM({_WORK} * {_HYP})                     AS sets_count,
  SUM({_WORK} * emc.stimulus_weight * {_HYP}) AS stimulus_sets,
  SUM({_WORK} * emc.fatigue_weight)         AS fatigue_sets,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN es.rpe ELSE 0 END)  AS rpe_sum,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN 1 ELSE 0 END)       AS rpe_n,
  {sql_application_rows("es.set_type")}     AS technique_rows
{_FROM_REALIZED}
GROUP BY emc.muscle_group_id, UPPER(TRIM(emc.role))
"""

# 1b) Relatorio em Markdown (detalhamento diario): os MESMOS agregados de
#     `ROLE_TOTALS_SQL` (estimulo sem aplicacao de tecnica, RPE) e de
#     `GROUP_SESSIONS_SQL` (series fisicas), so que cortados por (dia local,
#     grupo). Mesma cadeia `_FROM_REALIZED`, mesmo `_WORK`: somar os dias da
#     o total do periodo do grupo. As aplicacoes de tecnica entram depois, em
#     Python, pela mesma `TECHNIQUE_SETS_SQL`.
DAILY_GROUP_TOTALS_SQL = f"""
SELECT
  a.local_date                              AS local_date,
  emc.muscle_group_id                       AS muscle_group_id,
  COUNT(DISTINCT es.id)                     AS physical_sets,
  SUM({_WORK} * emc.stimulus_weight * {_HYP}) AS stimulus_sets,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN es.rpe ELSE 0 END)  AS rpe_sum,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN 1 ELSE 0 END)       AS rpe_n,
  COUNT(DISTINCT {_CANONICAL})              AS exercises,
  COUNT(DISTINCT a.id)                      AS sessions,
  {sql_application_rows("es.set_type")}     AS technique_rows
{_FROM_REALIZED}
GROUP BY a.local_date, emc.muscle_group_id
"""

# Series dos exercicios COM aplicacao de tecnica na janela (Fase 5, PR 4) -
# lida uma vez por requisicao, e so quando `technique_rows` > 0.
TECHNIQUE_SETS_SQL = technique_sets_sql("a.local_date BETWEEN %s AND %s")

# 2) Uma linha por (grupo, sessao): base da frequencia (`freq-v1`), do
#    "ultima vez treinado" e das series fisicas do grupo. O intervalo entre
#    estimulos precisa das sessoes individuais - nao da para mediana de
#    intervalo a partir de um total agregado.
GROUP_SESSIONS_SQL = f"""
SELECT
  emc.muscle_group_id                       AS muscle_group_id,
  a.id                                      AS activity_id,
  a.name                                    AS activity_name,
  a.local_date                              AS local_date,
  {{trained_at}}                            AS trained_at,
  COUNT(DISTINCT es.id)                     AS physical_sets,
  SUM({_WORK} * emc.stimulus_weight * {_HYP}) AS stimulus_sets,
  MAX(CASE WHEN UPPER(TRIM(emc.role)) = 'PRIMARY' THEN 1 ELSE 0 END) AS has_direct
{_FROM_REALIZED}
GROUP BY emc.muscle_group_id, a.id, a.name, a.local_date,
         {{trained_at}}
ORDER BY emc.muscle_group_id ASC, {{trained_at}} ASC
"""

# 3) Contribuicao por exercicio canonico dentro de cada grupo.
GROUP_EXERCISES_SQL = f"""
SELECT
  emc.muscle_group_id                       AS muscle_group_id,
  {_CANONICAL}                              AS exercise_id,
  MAX(e.original_name)                      AS original_name,
  UPPER(TRIM(emc.role))                     AS role,
  COUNT(*)                                  AS sets_count,
  SUM({_WORK} * emc.stimulus_weight * {_HYP}) AS stimulus_sets,
  MAX(a.local_date)                         AS last_local_date
{_FROM_REALIZED}
GROUP BY emc.muscle_group_id, {_CANONICAL}, UPPER(TRIM(emc.role))
"""

# 4) Series que NAO chegaram a grupo nenhum: o exercicio nao tem contribuicao
#    ativa cadastrada. Vira limitacao declarada (o volume do grupo aparece
#    menor do que foi treinado) - nunca um grupo "Outros" inventado.
UNATTRIBUTED_SQL = f"""
SELECT
  {_CANONICAL}            AS exercise_id,
  MAX(e.original_name)    AS original_name,
  COUNT(*)                AS sets_count
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = {_CANONICAL}
      AND emc.is_active = 1
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {_NOT_PREPARATORY}
  AND emc.id IS NULL
GROUP BY {_CANONICAL}
ORDER BY sets_count DESC
"""

# 5) Drill-down: SERIES de origem de UM grupo, das sessoes mais recentes para
#    as mais antigas (o corte por `LIMIT` fica nas mais antigas).
DRILLDOWN_SETS_SQL = f"""
SELECT
  a.id                                      AS activity_id,
  a.name                                    AS activity_name,
  a.local_date                              AS local_date,
  {{trained_at}}                            AS trained_at,
  e.id                                      AS exercise_row_id,
  e.sequence                                AS position_in_session,
  e.original_name                           AS original_name,
  {_CANONICAL}                              AS exercise_id,
  UPPER(TRIM(emc.role))                     AS role,
  emc.stimulus_weight                       AS stimulus_weight,
  emc.fatigue_weight                        AS fatigue_weight,
  es.id                                     AS set_id,
  es.set_number                             AS set_number,
  es.set_type                               AS set_type,
  es.reps                                   AS reps,
  es.weight_kg                              AS weight_kg,
  es.rpe                                    AS rpe,
  es.technique_code                         AS technique_code,
  es.anchor_set_number                      AS anchor_set_number
{_FROM_REALIZED}
  AND emc.muscle_group_id = %s
ORDER BY {{trained_at}} DESC, a.id DESC,
         e.sequence ASC, es.set_number ASC
LIMIT %s
"""

MUSCLE_GROUP_NAMES_SQL = "SELECT id, name FROM muscle_groups"


def _with_trained_at(sql: str, window: WindowInfo) -> str:
    """`{trained_at}` do SQL resolvido por `time_windows.trained_at_sql` no
    fuso da janela (o do usuario) - M76 Lote 2b."""
    return with_trained_at(sql, window.timezone)

# 6) Modelo VIGENTE do usuario, com o `volume_mode` e o numero de slots -
#    uma linha. Depois da migracao 049 (M23) "vigente" e um fato do banco
#    (`unassigned_at IS NULL`, UNIQUE por usuario) e nao mais o desempate
#    silencioso por `ORDER BY assigned_at DESC LIMIT 1` entre varias
#    atribuicoes simultaneas. Mesmo criterio de
#    `template_assignments.ACTIVE_ASSIGNMENT_SQL`; a query fica aqui porque
#    ela roda na MESMA conexao da requisicao (o resolvedor abre a propria) e
#    traz o `slots_count` agregado, que o resolvedor nao tem.
ASSIGNED_TEMPLATE_HEAD_SQL = """
SELECT
  t.id                                      AS template_id,
  t.name                                    AS template_name,
  t.volume_mode                             AS volume_mode,
  v.id                                      AS version_id,
  (SELECT COUNT(*) FROM workout_plan_template_slots s
    WHERE s.version_id = v.id) AS slots_count
FROM workout_plan_template_assignments a
JOIN workout_plan_templates t ON t.id = a.template_id
LEFT JOIN workout_plan_template_versions v ON v.current_template_id = t.id
WHERE a.user_id = %s AND a.unassigned_at IS NULL AND t.is_active = 1
LIMIT 1
"""

# 6b) Mesma coisa, mas do Modelo vigente EM UMA DATA - a fronteira de versao
#     do plano (§4). Usada quando ha janela (`date_to`); sem janela a tela
#     le 6). PR3: tambem a VERSAO do Modelo naquele instante (modo,
#     numero de slots e, em 7), a ativacao) - JOIN no mesmo statement; uma
#     janela que atravessa duas versoes e lida pela do FIM (regra do M12).
ASSIGNED_TEMPLATE_HEAD_AT_SQL = """
SELECT
  t.id                                      AS template_id,
  t.name                                    AS template_name,
  CASE WHEN v.id IS NULL THEN t.volume_mode ELSE v.volume_mode END AS volume_mode,
  v.id                                      AS version_id,
  (SELECT COUNT(*) FROM workout_plan_template_slots s
    WHERE s.version_id = v.id) AS slots_count
FROM workout_plan_template_assignments a
JOIN workout_plan_templates t ON t.id = a.template_id
LEFT JOIN workout_plan_template_versions v
       ON v.template_id = t.id
      AND (v.effective_from IS NULL OR v.effective_from <= %s)
      AND (v.superseded_at IS NULL OR v.superseded_at > %s)
WHERE a.user_id = %s
  AND a.assigned_at <= %s
  AND (a.unassigned_at IS NULL OR a.unassigned_at > %s)
ORDER BY a.assigned_at DESC, a.id DESC
LIMIT 1
"""

# 7) Ativacao PLANEJADA por grupo de um Modelo `equipment_series`: para cada
#    item `equipment` de cada slot de treino, `sets_count x stimulus_weight`
#    por contribuicao ativa - a MESMA conta de
#    `VolumeService.get_muscle_activation_for_exercises` (usada pelo preview
#    do builder, `_compute_slot_activation_equipment_series`) e a mesma
#    unidade de `ROLE_TOTALS_SQL` acima (`SUM(emc.stimulus_weight)` por
#    serie realizada). Planejado e realizado ficam comparaveis porque saem
#    da mesma tabela de contribuicoes, com o mesmo peso. Slot de descanso
#    fica fora (o preview tambem devolve ativacao zero para ele). Uma linha
#    por (slot, grupo), nunca por item: o total do ciclo e a soma dos slots, e
#    o PROJETADO do Modelo por equipamento soma so os slots pendentes.
#    Fase 5 (PR 5): uma linha por grupo E por (tecnica, aplicacoes) do item -
#    `planned_weight` = soma dos pesos desses itens (com series), para o
#    Python somar `planned_equivalent(tecnica, aplicacoes) x peso` pela regra
#    unica (`technique_accounting`), sem formula em SQL. Modelo sem tecnica:
#    a mesma linha por grupo de antes.
#    M101c: o segundo ramo (`UNION ALL`, `row_kind = 'unpaired'`) traz os
#    itens `equipment` SEM exercicio da mesma versao, um por linha (candidatos
#    a "exercicio livre", M101b). A SQL so filtra: QUEM e livre e quanto ele
#    soma no grupo primario decide o dono em Python
#    (`domain/free_exercise_item` + `technique_volume.free_exercise_primary_sets`),
#    a mesma regra da previa do editor e da metodologia. Continua UM statement.
TEMPLATE_PLAN_ROW_PAIR = "pair"
TEMPLATE_PLAN_ROW_UNPAIRED = "unpaired"
TEMPLATE_PLAN_ACTIVATION_SQL = f"""
SELECT
  '{TEMPLATE_PLAN_ROW_PAIR}'                AS row_kind,
  s.sequence                                AS slot_sequence,
  emc.muscle_group_id                       AS muscle_group_id,
  i.advanced_technique                      AS advanced_technique,
  i.advanced_technique_applications         AS advanced_technique_applications,
  SUM(i.sets_count * emc.stimulus_weight)   AS planned_sets,
  SUM(CASE WHEN i.sets_count > 0 THEN emc.stimulus_weight ELSE 0 END) AS planned_weight,
  NULL                                      AS item_kind,
  NULL                                      AS mapping_id,
  NULL                                      AS equipment_id,
  NULL                                      AS sets_count
FROM workout_plan_template_slots s
JOIN workout_plan_template_slot_items i ON i.slot_id = s.id
JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = i.mapping_id
      AND emc.is_active = 1
-- PR3: os slots da VERSAO resolvida no cabecalho (a vigente, ou a do fim da
-- janela com a flag ligada).
WHERE s.template_id = %s AND s.version_id = %s
  AND s.slot_type <> 'descanso'
  AND i.item_kind = 'equipment'
  AND i.mapping_id IS NOT NULL
GROUP BY s.sequence, emc.muscle_group_id, i.advanced_technique, i.advanced_technique_applications
UNION ALL
SELECT
  '{TEMPLATE_PLAN_ROW_UNPAIRED}'            AS row_kind,
  s.sequence                                AS slot_sequence,
  i.muscle_group_id                         AS muscle_group_id,
  i.advanced_technique                      AS advanced_technique,
  i.advanced_technique_applications         AS advanced_technique_applications,
  NULL                                      AS planned_sets,
  NULL                                      AS planned_weight,
  i.item_kind                               AS item_kind,
  i.mapping_id                              AS mapping_id,
  i.equipment_id                            AS equipment_id,
  i.sets_count                              AS sets_count
FROM workout_plan_template_slots s
JOIN workout_plan_template_slot_items i ON i.slot_id = s.id
WHERE s.template_id = %s AND s.version_id = %s
  AND s.slot_type <> 'descanso'
  AND i.item_kind = 'equipment'
  AND i.mapping_id IS NULL
"""

TEMPLATE_VOLUME_MODE_EQUIPMENT_SERIES = "equipment_series"

EXERCISE_NAMES_SQL = """
SELECT ec.id AS id, ec.name AS name
FROM exercise_catalog ec
WHERE ec.id IN ({placeholders})
"""

# 8) F2.5 / M34 - uma linha por (grupo, sessao, equipamento). UMA consulta
#    alimenta os TRES blocos por sessao (comparativo por sessao, comparativo
#    por equipamento e mapa de calor), porque os tres sao recortes da mesma
#    agregacao: somar sobre equipamento da o comparativo por sessao, somar
#    sobre sessao da o do equipamento, e fatiar por sessao da o mapa de calor.
#    Tres consultas separadas custariam tres statements para reler as mesmas
#    series.
#
#    Cardinalidade: sessoes da janela x grupos tocados por sessao x
#    equipamentos por grupo. Numa janela de 90 dias com 40 sessoes isso fica
#    na casa de poucas centenas de linhas curtas - a mesma ordem de
#    `GROUP_SESSIONS_SQL`, que ja roda aqui. NAO ha `LIMIT` por grupo: MySQL
#    nao recorta "as 4 ultimas por grupo" sem funcao de janela, e o recorte
#    em memoria custa menos que arriscar SQL dependente de versao do motor.
#
#    `emc` tem UNIQUE (exercicio, grupo), entao dentro de um `muscle_group_id`
#    cada serie aparece UMA vez: as somas de tonelagem e de RPE nao inflam
#    pelo JOIN (o mesmo motivo pelo qual `GROUP_SESSIONS_SQL` pode usar
#    `SUM(emc.stimulus_weight)` ao lado de `COUNT(DISTINCT es.id)`).
#
#    M58 - tonelagem ciente da convencao de carga: o par equipamento<->
#    exercicio (`equipment_exercises`, UNIQUE (equipment_id, exercise_id) -
#    nao duplica serie) entra no JOIN e as series `assisted`/`bodyweight` ficam
#    FORA da tonelagem (o numero gravado nao e a carga movida), pela MESMA
#    regra do relatorio Markdown (`load_conventions.sql_counts_in_tonnage`).
#    Series fisicas, estimulo e RPE continuam contando TODAS as series - so a
#    tonelagem muda. `tonnage_excluded_sets` declara quantas sairam. Continua
#    UM statement: a convencao vem da propria linha.
def session_equipment_sql(convention_expr: str = "NULL") -> str:
    """SQL do bloco por sessao. `convention_expr` = saida de
    `load_convention_select_expr` (a coluna da 045 ou `NULL` = `total`)."""
    counts = sql_counts_in_tonnage(convention_expr)
    return f"""
SELECT
  emc.muscle_group_id                       AS muscle_group_id,
  a.id                                      AS activity_id,
  a.name                                    AS activity_name,
  a.local_date                              AS local_date,
  eq.name                                   AS equipment_name,
  COUNT(DISTINCT es.id)                     AS physical_sets,
  SUM({_WORK} * emc.stimulus_weight * {_HYP}) AS stimulus_sets,
  {sql_application_rows("es.set_type")}     AS technique_rows,
  SUM(CASE WHEN {counts} THEN COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0) ELSE 0 END) AS tonnage_kg,
  SUM(CASE WHEN {counts} AND es.reps IS NOT NULL AND es.weight_kg IS NOT NULL THEN 1 ELSE 0 END) AS tonnage_n,
  COUNT(DISTINCT CASE WHEN NOT ({counts}) THEN es.id END) AS tonnage_excluded_sets,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN es.rpe ELSE 0 END) AS rpe_sum,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN 1 ELSE 0 END)      AS rpe_n,
  MAX(CASE WHEN UPPER(TRIM(emc.role)) = 'PRIMARY' THEN 1 ELSE 0 END) AS has_direct
{_JOINS_REALIZED}
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN equipment eq ON eq.id = ge.equipment_id
{equipment_exercises_join_sql(canonical_expr=_CANONICAL)}{_WHERE_REALIZED}
GROUP BY emc.muscle_group_id, a.id, a.name, a.local_date, eq.name
ORDER BY a.local_date DESC, a.id DESC
"""


# Forma sem a coluna da 045 (convencao NULL = `total` em toda serie). Mantida
# para leitura/documentacao; a rota usa `session_equipment_sql(<deteccao>)`.
SESSION_EQUIPMENT_SQL = session_equipment_sql("NULL")

_METRIC_VERSIONS: Mapping[str, str] = {
    key: metric_version(key)
    for key in (
        "physical_sets",
        "effective_sets_stimulus",
        "effective_sets_fatigue",
        "role_sets",
        "weekly_volume_status",
        "muscle_frequency",
        "readiness",
    )
}

# Versoes so do bloco por SESSAO (M34). Ficam num mapa PROPRIO e sao unidas a
# `_METRIC_VERSIONS` apenas quando o bloco e pedido: sem isso a resposta
# declararia uma formula que ela nao usou.
_COMPARATIVE_METRIC_VERSIONS: Mapping[str, str] = {
    key: metric_version(key)
    for key in (
        "muscle_session_comparative",
        "muscle_equipment_comparative",
        "muscle_heatmap",
        "tonnage",
    )
}

# Quantas sessoes o comparativo por sessao mostra: T-0, T-1, T-2 e T-3, como
# na tela legada `/volume-grupamentos`.
SESSION_COMPARATIVE_POINTS = 4

# Equipamentos listados por grupo. O corte e DECLARADO
# (`equipment_truncated`): uma lista cortada em silencio faria a soma dos
# equipamentos parecer menor que o total do grupo.
MAX_EQUIPMENTS_PER_GROUP = 6

# Sessoes trazidas no mapa de calor (as mais recentes da janela). A tela
# oferece um seletor de sessao sem ir ao servidor de novo, e o payload fica
# limitado a ~8 x nº de grupos celulas curtas.
MAX_HEATMAP_SESSIONS = 8

SESSION_UNIT_NOTE = (
    "Comparativo por SESSÃO: T-0 é a última sessão em que o grupo apareceu, "
    "T-1 a anterior, e assim por diante. Não é semana nem mês — duas sessões "
    "seguidas podem cair no mesmo dia."
)

HEATMAP_SCOPE_NOTE = (
    "No mapa de calor a intensidade é relativa ao MAIOR grupo da mesma sessão: "
    "ela compara grupos dentro de uma sessão, nunca sessões entre si."
)

_ROLE_DIRECT = "PRIMARY"
_ROLE_INDIRECT = "SECONDARY"
_ROLE_STABILIZER = "STABILIZER"


# ---------------------------------------------------------------------------
# Helpers puros (testados sem banco)
# ---------------------------------------------------------------------------


# As tres coercoes abaixo passaram a viver em `coercion.py` (F1.1), para o
# `adherence_service` nao nascer com uma segunda copia delas. Os nomes locais
# continuam existindo porque sao usados em todo este modulo e nos testes.
_num = to_float
_as_date = to_date
_as_datetime = to_datetime


def metric(
    value: Optional[float],
    *,
    unit: Optional[str],
    nature: DataNature,
    version_key: str,
    quality: DataQuality = DataQuality.HIGH,
    limitations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Envelope `Metric` (§8.4). `value=None` continua `None`: ausencia NUNCA
    vira zero aqui - quem le precisa distinguir "nao deu para calcular" de
    "foi zero de fato"."""
    return {
        # Arredondamento UNICO para todo numero nao inteiro do envelope
        # (M42/M59): `Decimal` do PyMySQL, `str` do shim Data API e `float`
        # calculado em Python saem pela mesma regra (`ROUND_HALF_UP`), entao
        # os dois backends exibem o mesmo centesimo. Inteiro fica inteiro.
        "value": round_half_up(value, 2) if isinstance(value, (float, Decimal)) else value,
        "unit": unit,
        "nature": nature,
        "metric_version": _METRIC_VERSIONS[version_key],
        "data_quality": quality,
        "limitations": list(limitations or []),
    }


def median_interval_hours(timestamps: Iterable[datetime]) -> Optional[float]:
    """Mediana do intervalo, em horas, entre estimulos consecutivos
    (`freq-v1`). `None` - nunca `0` - com menos de dois estimulos: um unico
    treino nao tem intervalo, e chamar isso de "0 horas" diria que o grupo foi
    treinado duas vezes no mesmo instante.

    Mediana, e nao media, de proposito: uma pausa de ferias no meio da janela
    deslocaria a media inteira e faria parecer que a frequencia caiu."""
    ordered = sorted(t for t in timestamps if t is not None)
    if len(ordered) < 2:
        return None
    gaps = [
        (later - earlier).total_seconds() / 3600.0
        for earlier, later in zip(ordered, ordered[1:])
    ]
    return round_half_up(statistics.median(gaps), 2)


def weekly_rate(value: Optional[float], window_days: int) -> Optional[float]:
    """Total acumulado na janela -> taxa SEMANAL equivalente, arredondada.

    Existe porque `min/target/max_weekly_sets` sao metas por SEMANA: comparar
    28 dias de volume contra uma meta de 7 dias marcaria todo grupo como
    `ABOVE_MAX`. A conta e do dono (`methodology_volume.calendar_weekly_rate`,
    M76 Lote 4a); aqui so o arredondamento da tela."""
    rate = calendar_weekly_rate(value, window_days)
    return round(rate, 2) if rate is not None else None


def resolve_targets(
    muscle_group_id: int,
    targets_by_group: Mapping[int, Mapping[str, float]],
    source: TargetSource,
) -> tuple[float, float, float, TargetSource]:
    """min/target/max do grupo + a origem EFETIVA da meta daquele grupo.

    Cuidado que a v2 nao tinha: ter um `training_type` resolvido nao garante
    que ELE tenha meta para ESTE grupo. Sem linha em
    `training_type_muscle_group_targets` o item cai em `fallback_generic`
    individualmente, mesmo quando a resposta como um todo tem uma Meta de
    Ativacao atribuida."""
    target = targets_by_group.get(muscle_group_id)
    if target:
        return float(target["min"]), float(target["target"]), float(target["max"]), source
    return (
        DASHBOARD_FALLBACK_MIN_SETS,
        DASHBOARD_FALLBACK_TARGET_SETS,
        DASHBOARD_FALLBACK_MAX_SETS,
        TargetSource.FALLBACK_GENERIC,
    )


FALLBACK_TARGET_LIMITATION = (
    "Sem meta cadastrada para este grupo: a comparacao usa os limiares genericos, "
    "entao 'abaixo' ou 'acima' nao reflete o seu objetivo."
)

OUTSIDE_TEMPLATE_PLAN_LIMITATION = (
    "Grupo fora do Modelo atual: nenhum equipamento do seu Modelo trabalha este grupo, "
    "entao o volume realizado aqui e trabalho extra em relacao ao plano, nao deficit."
)


def template_plan_cadence_limitation(cycle_length_days: int, slots_count: int) -> str:
    return (
        f"Meta semanal derivada do proprio Modelo (equipamento + series fixas): ativacao "
        f"planejada do ciclo de {slots_count} slot(s) no ritmo do calendario, um slot por dia "
        f"(ciclo de {cycle_length_days} dias, descanso incluso) - a mesma unidade do realizado, "
        "que e uma taxa semanal de dias corridos. Nao e a semana do editor de Modelo (7 dias a "
        "partir do 1º treino), que avalia a metodologia. Nao ha minimo nem maximo: o plano e fixo."
    )


@dataclass(frozen=True)
class PlanSlot:
    """Um slot do Modelo so com o que a janela semanal precisa."""

    sequence: int
    slot_type: Optional[str]


@dataclass(frozen=True)
class TemplatePlan:
    """Meta que vem do PROPRIO Modelo `equipment_series` atribuido.

    `activation_by_group` e a ativacao planejada de UM ciclo inteiro (soma
    de todos os slots de treino), em series efetivas; `weekly_targets` e o
    RITMO SEMANAL desse ciclo no calendario
    (`methodology_volume.calendar_weekly_rate(ciclo, dias do ciclo)`) - a
    unidade em que `realized.weekly_effective_sets` (taxa de dias corridos) e
    comparado. Ver `template_plan_weekly_targets`."""

    template_id: int
    template_name: Optional[str]
    slots_count: int
    cycle_length_days: int
    activation_by_group: Mapping[int, float] = field(default_factory=dict)
    # Mesma ativacao, aberta por slot (`sequence -> grupo -> series
    # efetivas`). Alimenta o PROJETADO: um Modelo por equipamento nao tem item
    # `muscle_group`, entao a soma dos itens de grupo (regra antiga)
    # dava sempre vazio para ele.
    activation_by_slot: Mapping[int, Mapping[int, float]] = field(default_factory=dict)
    # PR3: versao do Modelo de que esta meta saiu (a vigente, ou a do fim da
    # janela). `_projected_by_group` so reaproveita a ativacao por slot quando
    # e a MESMA versao que ele projeta (a vigente).
    version_id: Optional[int] = None

    @property
    def weekly_targets(self) -> dict[int, float]:
        return template_plan_weekly_targets(self.activation_by_group, self.cycle_length_days)

    def contains(self, muscle_group_id: int) -> bool:
        """O Modelo trabalha este grupo? Ativacao planejada zero conta como
        "fora do Modelo" - realizado nele e trabalho extra, nao deficit."""
        return float(self.activation_by_group.get(muscle_group_id) or 0.0) > 0.0


def template_plan_weekly_targets(
    activation_by_group: Mapping[int, float], cycle_length_days: int
) -> dict[int, float]:
    """Ativacao planejada do CICLO -> meta SEMANAL por grupo para comparar com
    o REALIZADO: o ritmo do ciclo no calendario, pelo dono
    (`methodology_volume.calendar_weekly_rate(ativacao, dias do ciclo)`).

    Por que nao a semana do editor (`weekly_normalization`, 7 dias a partir do
    1o slot de treino)? O realizado e uma taxa de dias CORRIDOS. Quem segue um
    Modelo de 8 slots a risca (um slot por dia) faz o ciclo a cada 8 dias, ou
    seja `ciclo x 7/8` por semana; a semana do editor corta o 8o dia (descanso)
    e conta o ciclo inteiro em 7 dias. Comparar o realizado com ela deixaria a
    aderencia perfeita 12,5% ABAIXO do planejado - fora da tolerancia de 5% -
    para sempre (medido em producao, M76 Lote 4a). A semana do editor continua
    sendo a regua da METODOLOGIA (builder, preview, `/dashboard/ciclo`)."""
    if not cycle_length_days or cycle_length_days <= 0:
        return {}
    return {
        int(group_id): calendar_weekly_rate(float(value), cycle_length_days)
        for group_id, value in activation_by_group.items()
        if group_id is not None and float(value or 0.0) > 0.0
    }

NO_DIRECT_WORK_LIMITATION = (
    "Nenhuma serie com trabalho DIRETO neste grupo na janela: o volume veio so de "
    "participacao indireta ou de estabilizacao."
)


def item_data_quality(
    *,
    target_source: TargetSource,
    direct_sets: float,
    unattributed_sets_in_window: int,
    outside_template_plan: bool = False,
) -> tuple[DataQuality, list[str]]:
    """Qualidade do ITEM e o que ela impede de concluir.

    - meta generica -> `LOW`: o item existe, mas o veredito "abaixo/acima" nao
      se sustenta contra um objetivo que ninguem definiu;
    - meta do proprio Modelo (`template_plan`) -> continua `HIGH`: e o plano
      do usuario, nao um numero generico. Grupo que o Modelo NAO trabalha
      ganha so a limitacao "fora do Modelo" (o numero realizado e real);
    - sem trabalho direto -> no maximo `MEDIUM`: o numero e real, a leitura de
      "grupo treinado" e que fica fraca;
    - series sem contribuicao cadastrada na janela -> no maximo `MEDIUM`: o
      volume do grupo pode estar subestimado."""
    limitations: list[str] = []
    quality = DataQuality.HIGH

    if target_source is TargetSource.FALLBACK_GENERIC:
        quality = DataQuality.LOW
        limitations.append(FALLBACK_TARGET_LIMITATION)

    if target_source is TargetSource.TEMPLATE_PLAN and outside_template_plan:
        limitations.append(OUTSIDE_TEMPLATE_PLAN_LIMITATION)

    if direct_sets <= 0:
        if quality is DataQuality.HIGH:
            quality = DataQuality.MEDIUM
        limitations.append(NO_DIRECT_WORK_LIMITATION)

    if unattributed_sets_in_window > 0:
        if quality is DataQuality.HIGH:
            quality = DataQuality.MEDIUM
        limitations.append(
            f"{unattributed_sets_in_window} serie(s) da janela pertencem a exercicios sem "
            "contribuicao muscular cadastrada e nao entraram em nenhum grupo."
        )

    return quality, limitations


def envelope_quality(items: list[dict[str, Any]]) -> DataQuality:
    """Confianca da RESPOSTA: a pior qualidade entre os itens. Sem item
    nenhum a resposta e `INSUFFICIENT` - "nao ha o que concluir" nao e o
    mesmo que "esta tudo bem"."""
    if not items:
        return DataQuality.INSUFFICIENT
    order = [DataQuality.HIGH, DataQuality.MEDIUM, DataQuality.LOW, DataQuality.INSUFFICIENT]
    worst = DataQuality.HIGH
    for item in items:
        quality = item["data_quality"]
        if order.index(quality) > order.index(worst):
            worst = quality
    return worst


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def muscle_group_names(conn: Any) -> dict[int, str]:
    """`{id: nome}` de TODOS os grupos musculares - tabela de referencia, uma
    leitura so.

    Publica desde o M29 para quem chama `build_muscle_summary` mais de uma vez
    na MESMA requisicao (a visao geral le tres janelas) poder resolver os nomes
    UMA vez e repassar em `group_names=`. Sao 3 leituras identicas de uma
    tabela que nao muda dentro de uma requisicao; no caminho da RDS Data API
    cada uma e uma ida ao servico."""
    return {
        int(row["id"]): row["name"]
        for row in request_memo.memoize(("muscle_groups", "id_name"), lambda: _rows(conn, MUSCLE_GROUP_NAMES_SQL, ()))
    }


# Nome antigo, mantido para quem ja importava (uso interno deste modulo).
_muscle_group_names = muscle_group_names


def _exercise_names(conn: Any, exercise_ids: set[int]) -> dict[int, str]:
    if not exercise_ids:
        return {}
    ordered = sorted(exercise_ids)
    placeholders = ",".join(["%s"] * len(ordered))
    sql = EXERCISE_NAMES_SQL.format(placeholders=placeholders)
    return {
        int(row["id"]): row["name"]
        for row in _rows(conn, sql, tuple(ordered))
        if row.get("name")
    }


def _readiness_by_group(user_id: int) -> dict[int, dict[str, Any]]:
    """Prontidao por grupo, do servico existente (nao existe um segundo
    modelo de recuperacao no projeto).

    Desde a M20 (`intelligence/recovery_read_model.py`) a prontidao custa 3
    statements, e nao mais ~16: por isso o `history_days` que limitava o
    historico a 60 dias foi REMOVIDO daqui. O ganho nao e so
    de tempo - com o historico inteiro, o "exercicio novo" do modelo volta a
    ser o mesmo que a geracao de ficha e o Modo Treino enxergam, e a tela
    deixa de ter uma limitacao propria a declarar.

    Falha aqui NAO derruba a tela: o resto da resposta e leitura direta de
    series e continua valendo.

    M76 B3: janela e limite são os do modelo (`RECOVERY_LOOKBACK_DAYS`,
    `RECOVERY_GROUP_LIMIT`, defaults de `get_recovery_overview`) - os mesmos da
    geração de ficha. O período escolhido na tela não muda a prontidão."""
    from ..muscle_recovery_service import MuscleRecoveryService

    try:
        items = MuscleRecoveryService().get_recovery_overview(user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - prontidao e um bloco opcional da tela
        log_event("dashboard.readiness_unavailable", user_id=user_id, error=str(exc))
        return {}

    by_group: dict[int, dict[str, Any]] = {}
    for item in items:
        group_id = item.get("muscle_group_id")
        if group_id is not None:
            by_group[int(group_id)] = item
    return by_group


def _planned_by_group(user_id: int, window: WindowInfo) -> dict[int, dict[str, Any]]:
    """PLANEJADO reusando `VolumeService.get_planned_effective_sets`: a
    prescricao das fichas salvas na janela (`workout_plan_exercises` x
    contribuicao muscular, mesma regra de ativacao do realizado), com
    `template_slot_targets` como alternativa para ficha antiga sem prescricao
    aproveitavel. Nenhuma leitura de serie."""
    from ..volume_service import VolumeService

    planned: dict[int, dict[str, Any]] = {}
    for row in VolumeService().get_planned_effective_sets(user_id, window.date_from, window.date_to):
        group_id = row.get("muscle_group_id")
        if group_id is None:
            continue
        planned[int(group_id)] = row
    return planned


def _add_planned(
    activation: dict[int, float],
    by_slot: dict[int, dict[int, float]],
    sequence: Any,
    group_id: int,
    planned: float,
) -> None:
    """Soma `planned` no ciclo (`activation`) e no slot (`by_slot`)."""
    activation[group_id] = activation.get(group_id, 0.0) + planned
    if sequence is not None:
        slot_bucket = by_slot.setdefault(int(sequence), {})
        slot_bucket[group_id] = slot_bucket.get(group_id, 0.0) + planned


def resolve_template_plan(conn: Any, user_id: int, *, date_to: Optional[date] = None) -> Optional[TemplatePlan]:
    """Meta vinda do PROPRIO Modelo, quando o Modelo do usuario e
    `equipment_series`. `None` em qualquer outro caso (sem Modelo, Modelo
    `percent_budget`, Modelo sem slot) - e ai a cadeia de metas segue
    exatamente como antes (`explicit` > `assigned_template` >
    `fallback_generic`).

    QUAL Modelo (M23): o vigente no FIM da janela (`date_to`), para um ciclo
    antigo ser medido contra a meta do Modelo daquela epoca e nao contra a do
    Modelo de hoje. `date_to=None` (chamador que nao tem janela) le o vigente
    hoje.

    Duas consultas curtas (cabecalho + ativacao agregada por grupo, com os
    itens sem exercicio no mesmo statement, M101c), na MESMA
    conexao da requisicao. Falha aqui nao derruba a tela: cai para a cadeia
    antiga, com o erro registrado."""
    from ...template_assignments import _as_moment

    try:
        if date_to is not None:
            momento = _as_moment(date_to)
            head_rows = _rows(
                conn, ASSIGNED_TEMPLATE_HEAD_AT_SQL, (momento, momento, int(user_id), momento, momento)
            )
        else:
            head_rows = _rows(conn, ASSIGNED_TEMPLATE_HEAD_SQL, (int(user_id),))
        if not head_rows:
            return None
        head = head_rows[0]
        if str(head.get("volume_mode") or "") != TEMPLATE_VOLUME_MODE_EQUIPMENT_SERIES:
            return None
        slots_count = int(_num(head.get("slots_count")) or 0)
        if slots_count <= 0:
            return None
        template_id = int(head["template_id"])
        version_id = head.get("version_id")
        version_id = int(version_id) if version_id is not None else None

        activation: dict[int, float] = {}
        by_slot: dict[int, dict[int, float]] = {}
        catalog = new_catalog()
        for row in _rows(conn, TEMPLATE_PLAN_ACTIVATION_SQL, (template_id, version_id, template_id, version_id)):
            group_id = row.get("muscle_group_id")
            if group_id is None:
                continue
            if row.get("row_kind") == TEMPLATE_PLAN_ROW_UNPAIRED:
                # M101c: item de exercicio livre = series planejadas no grupo
                # primario (regra do dono, a mesma da previa e da metodologia);
                # item sem exercicio que nao e livre soma 0.
                free = free_exercise_primary_sets(row, catalog)
                if free > 0.0:
                    _add_planned(activation, by_slot, row.get("slot_sequence"), int(group_id), free)
                continue
            # Fase 5 (PR 5): equivalente PLANEJADO da tecnica pelo catalogo
            # (estimativa); o realizado conta as reps reais - a diferenca e
            # informativa. Sem tecnica, `planned_equivalent` = 0 sem ler nada.
            technique = planned_equivalent(
                row.get("advanced_technique"), row.get("advanced_technique_applications"), catalog
            )
            planned = (_num(row.get("planned_sets")) or 0.0) + technique * (_num(row.get("planned_weight")) or 0.0)
            _add_planned(activation, by_slot, row.get("slot_sequence"), int(group_id), planned)

        return TemplatePlan(
            template_id=template_id,
            template_name=head.get("template_name"),
            slots_count=slots_count,
            # Um slot = um dia (motor de geracao). Era `7 x slots`, o que
            # deixava a meta semanal 7x menor que o planejado.
            cycle_length_days=template_cycle_length_days(slots_count),
            activation_by_group=activation,
            activation_by_slot=by_slot,
            version_id=version_id,
        )
    except Exception as exc:  # noqa: BLE001 - meta do Modelo e um bloco opcional da tela
        log_event("dashboard.template_plan_unavailable", user_id=user_id, error=str(exc))
        return None


def sum_projected_activation_from_pending_slots(
    activation_by_slot: Mapping[int, Mapping[int, float]], pending_sequences: Iterable[Any]
) -> dict[int, float]:
    """PROJETADO de um Modelo por equipamento: soma, por grupo, a ativacao
    planejada (series efetivas, `TEMPLATE_PLAN_ACTIVATION_SQL`) dos slots
    ainda pendentes no ciclo. Funcao pura. Slot sem ativacao (descanso) soma
    zero; grupo com soma zero nao aparece."""
    totals: dict[int, float] = {}
    for sequence in pending_sequences:
        for group_id, value in (activation_by_slot.get(sequence) or {}).items():
            totals[int(group_id)] = totals.get(int(group_id), 0.0) + float(value or 0.0)
    return {group_id: value for group_id, value in totals.items() if value > 0.0}


def template_activation_by_slot(
    template: Mapping[str, Any],
    template_plan: Optional[TemplatePlan] = None,
    targets_by_group: Optional[Mapping[int, Mapping[str, float]]] = None,
) -> dict[int, dict[int, float]]:
    """PLANEJADO de UMA ocorrencia de cada slot do Modelo, por grupo
    (`sequence -> muscle_group_id -> series`). Fonte unica do "valor por
    slot" que `/dashboard/musculos` (projetado) e `/dashboard/ciclo`
    (planejado da semana, via `methodology_volume.weekly_normalization`)
    consomem (M76 Lote 4a, E2).

    - Modelo por equipamento (`template_plan` do MESMO Modelo e da versao
      vigente): a ativacao ponderada por slot de `resolve_template_plan`
      (`sets x stimulus_weight` + tecnica) - a mesma unidade do realizado.
    - Itens `muscle_group` (Modo A): o ponto medio do orcamento do item
      (`project_muscle_item_budgets`: faixa % x meta semanal do grupo), a
      mesma regra do preview do editor; sem meta para o grupo, `sets_count`
      (ou 0). Antes so `sets_count` entrava, e um Modelo `percent_budget` sem
      series digitadas projetava zero.
    Slot de descanso nao tem valor."""
    from ...template_versions import current_version_id_of
    from ..template_volume_planner import project_muscle_item_budgets

    if (
        template_plan is not None
        and template_plan.template_id == template.get("id")
        and (template_plan.version_id is None or template_plan.version_id == current_version_id_of(template))
    ):
        return {int(seq): dict(groups) for seq, groups in template_plan.activation_by_slot.items()}

    weekly_targets = {
        int(group_id): float(values["target"])
        for group_id, values in (targets_by_group or {}).items()
        if values and values.get("target") is not None
    }
    by_slot: dict[int, dict[int, float]] = {}
    for slot in template.get("slots") or []:
        if str(slot.get("slot_type") or "").strip().lower() == "descanso":
            continue
        items = list(slot.get("items") or [])
        mids = {
            projection.index: projection.sets_mid
            for projection in project_muscle_item_budgets(items, weekly_targets)
            if projection.sets_mid is not None
        }
        bucket: dict[int, float] = {}
        for index, item in enumerate(items):
            if item.get("item_kind") != "muscle_group" or item.get("muscle_group_id") is None:
                continue
            value = float(mids[index]) if index in mids else float(item.get("sets_count") or 0)
            group_id = int(item["muscle_group_id"])
            bucket[group_id] = bucket.get(group_id, 0.0) + value
        if bucket:
            by_slot[int(slot["sequence"])] = bucket
    return by_slot


def _projected_by_group(
    user_id: int,
    template_plan: Optional[TemplatePlan] = None,
    targets_by_group: Optional[Mapping[int, Mapping[str, float]]] = None,
) -> tuple[dict[int, float], int, Optional[int]]:
    """PROJETADO: series que ainda faltam no ciclo corrente do Modelo
    atribuido - a soma, nos slots pendentes, do valor por slot de
    `template_activation_by_slot` (a mesma fonte do planejado de
    `/dashboard/ciclo`, M76 Lote 4a), com a mesma leitura de
    `done_sequences` de `GET /analytics/dashboard-cycle`.

    Devolve `({}, 0, None)` quando o usuario nao tem Modelo atribuido: sem
    ciclo nao existe projecao, e inventar zero diria que nao falta nada."""
    from ...analytics import _resolve_latest_assigned_template_id
    from ...db import transaction
    from ...workout_plan_templates_engine import get_template_if_assigned

    try:
        template_id = _resolve_latest_assigned_template_id(user_id)
        if template_id is None:
            return {}, 0, None
        template = get_template_if_assigned(template_id, user_id)
        if template is None:
            return {}, 0, None

        slots = sorted(template.get("slots") or [], key=lambda slot: slot["sequence"])
        if not slots:
            return {}, 0, None

        def read_plan_rows() -> list[dict[str, Any]]:
            with transaction() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT template_slot_sequence
                        FROM workout_plans
                        WHERE user_id = %s AND template_id = %s AND template_slot_sequence IS NOT NULL
                        ORDER BY id DESC
                        """,
                        (user_id, template_id),
                    )
                    return list(cursor.fetchall() or [])

        # Mesma leitura de `adherence_service` (mesma chamada GET): uma ida ao
        # banco por requisicao (PERF-01, `request_memo`).
        plan_rows = request_memo.memoize(("workout_plan_slot_sequences", user_id, template_id), read_plan_rows)

        done: set[Any] = set()
        for row in plan_rows:
            sequence = row.get("template_slot_sequence")
            if sequence in done:
                break
            done.add(sequence)
            if len(done) >= len(slots):
                break

        pending = {slot["sequence"] for slot in slots} - done
        # PR3: a ativacao por slot do Modelo por equipamento so serve se for da
        # MESMA versao que este projetado olha (a vigente) - a regra mora em
        # `template_activation_by_slot`, a mesma que `/dashboard/ciclo` usa.
        by_slot = template_activation_by_slot(template, template_plan, targets_by_group)
        totals = sum_projected_activation_from_pending_slots(by_slot, pending)
        return (
            {int(gid): float(value) for gid, value in totals.items()},
            len(pending),
            template_cycle_length_days(len(slots)),
        )
    except Exception as exc:  # noqa: BLE001 - projecao e bloco opcional da tela
        log_event("dashboard.projection_unavailable", user_id=user_id, error=str(exc))
        return {}, 0, None


# ---------------------------------------------------------------------------
# Montagem dos itens
# ---------------------------------------------------------------------------


class _WindowTechniqueSets:
    """`TECHNIQUE_SETS_SQL` da janela, lida no maximo UMA vez por requisicao
    e so quando algum agregado acusou aplicacao de tecnica - os totais por
    grupo e o bloco por sessao (M34) dividem a mesma leitura."""

    def __init__(self, conn: Any, user_id: int, window: WindowInfo) -> None:
        self._conn = conn
        self._params = (user_id, window.date_from, window.date_to)
        self._loaded: Optional[TechniqueSets] = None

    def get(self) -> TechniqueSets:
        if self._loaded is None:
            self._loaded = TechniqueSets(_rows(self._conn, TECHNIQUE_SETS_SQL, self._params), new_catalog())
        return self._loaded


def _has_technique_rows(rows: Iterable[Mapping[str, Any]]) -> bool:
    return any((_num(row.get("technique_rows")) or 0) > 0 for row in rows)


def _build_items(
    conn: Any,
    *,
    user_id: int,
    window: WindowInfo,
    targets_by_group: Mapping[int, Mapping[str, float]],
    target_source: TargetSource,
    include_readiness: bool,
    now_local: datetime,
    status_only: bool = False,
    template_plan: Optional[TemplatePlan] = None,
    group_names: Optional[Mapping[int, str]] = None,
    technique_sets: Optional[_WindowTechniqueSets] = None,
) -> tuple[list[dict[str, Any]], list[str], int]:
    params = (user_id, window.date_from, window.date_to)
    plan_weekly_targets = template_plan.weekly_targets if template_plan is not None else {}

    role_rows = _rows(conn, ROLE_TOTALS_SQL, params)
    session_rows = _rows(conn, _with_trained_at(GROUP_SESSIONS_SQL, window), params)
    # `status_only` (F1.6): a visao geral reusa este servico para os dois
    # ultimos ciclos SO para o status do realizado - top exercicios,
    # planejado e projetado nao mudam a classificacao (§7) e custariam
    # consultas a mais.
    exercise_rows = [] if status_only else _rows(conn, GROUP_EXERCISES_SQL, params)
    unattributed_rows = _rows(conn, UNATTRIBUTED_SQL, params)

    names_by_group = dict(group_names) if group_names is not None else muscle_group_names(conn)
    unattributed_sets = sum(int(row.get("sets_count") or 0) for row in unattributed_rows)

    readiness_by_group = _readiness_by_group(user_id) if include_readiness else {}
    if status_only:
        planned_by_group, projected_by_group, pending_slots, cycle_length_days = {}, {}, 0, None
    else:
        planned_by_group = _planned_by_group(user_id, window)
        projected_by_group, pending_slots, cycle_length_days = _projected_by_group(
            user_id, template_plan=template_plan, targets_by_group=targets_by_group
        )

    # --- agregacao por grupo ------------------------------------------------
    buckets: dict[int, dict[str, Any]] = {}

    def _bucket(group_id: int) -> dict[str, Any]:
        return buckets.setdefault(
            group_id,
            {
                "stimulus": 0.0,
                "fatigue": 0.0,
                "direct": 0.0,
                "indirect": 0.0,
                "stabilizer": 0.0,
                "rpe_sum": 0.0,
                "rpe_n": 0,
                "physical_sets": 0,
                "sessions": [],
                "direct_sessions": [],
                "exercises": [],
            },
        )

    for row in role_rows:
        group_id = row.get("muscle_group_id")
        if group_id is None:
            continue
        bucket = _bucket(int(group_id))
        role = (row.get("role") or "").strip().upper()
        sets_count = _num(row.get("sets_count")) or 0.0
        bucket["stimulus"] += _num(row.get("stimulus_sets")) or 0.0
        bucket["fatigue"] += _num(row.get("fatigue_sets")) or 0.0
        bucket["rpe_sum"] += _num(row.get("rpe_sum")) or 0.0
        bucket["rpe_n"] += int(_num(row.get("rpe_n")) or 0)
        if role == _ROLE_DIRECT:
            bucket["direct"] += sets_count
        elif role == _ROLE_INDIRECT:
            bucket["indirect"] += sets_count
        elif role == _ROLE_STABILIZER:
            bucket["stabilizer"] += sets_count

    for row in session_rows:
        group_id = row.get("muscle_group_id")
        if group_id is None:
            continue
        bucket = _bucket(int(group_id))
        trained_at = _as_datetime(row.get("trained_at"))
        entry = {
            "activity_id": int(row["activity_id"]),
            "activity_name": row.get("activity_name"),
            "local_date": _as_date(row.get("local_date")),
            "trained_at": trained_at,
            "physical_sets": int(_num(row.get("physical_sets")) or 0),
            "stimulus_sets": _num(row.get("stimulus_sets")) or 0.0,
            "has_direct": bool(_num(row.get("has_direct")) or 0),
        }
        bucket["sessions"].append(entry)
        bucket["physical_sets"] += entry["physical_sets"]
        if entry["has_direct"]:
            bucket["direct_sessions"].append(entry)

    for row in exercise_rows:
        group_id = row.get("muscle_group_id")
        if group_id is None:
            continue
        _bucket(int(group_id))["exercises"].append(row)

    # Aplicacoes de tecnica (Fase 5, PR 4): as consultas acima nao as somaram
    # nas series equivalentes; cada uma entra aqui com a sua fatia do bloco,
    # ponderada pelo peso da contribuicao - diretas, estimulo e fadiga do
    # grupo, estimulo da sessao e do exercicio.
    if _has_technique_rows(role_rows):
        loader = technique_sets or _WindowTechniqueSets(conn, user_id, window)
        sessions_by_key = {
            (group_id, entry["activity_id"]): entry
            for group_id, bucket in buckets.items()
            for entry in bucket["sessions"]
        }
        for row, units in loader.get().applications():
            group_id = int(row["muscle_group_id"])
            bucket = _bucket(group_id)
            stimulus = (_num(row.get("stimulus_weight")) or 0.0) * units
            bucket["stimulus"] += stimulus
            bucket["fatigue"] += (_num(row.get("fatigue_weight")) or 0.0) * units
            role = (row.get("role") or "").strip().upper()
            if role == _ROLE_DIRECT:
                bucket["direct"] += units
            elif role == _ROLE_INDIRECT:
                bucket["indirect"] += units
            elif role == _ROLE_STABILIZER:
                bucket["stabilizer"] += units
            entry = sessions_by_key.get((group_id, int(row["activity_id"])))
            if entry is not None:
                entry["stimulus_sets"] += stimulus
            if not status_only:
                bucket["exercises"].append(
                    {
                        "muscle_group_id": group_id,
                        "exercise_id": row.get("exercise_id"),
                        "original_name": row.get("original_name"),
                        "role": role,
                        "sets_count": 0,
                        "stimulus_sets": stimulus,
                        "last_local_date": row.get("local_date"),
                    }
                )

    # Grupos que so tem meta ou projecao (nenhuma serie na janela) tambem
    # aparecem: "nao treinei nada" e a resposta mais importante desta tela.
    for group_id in (
        list(targets_by_group.keys())
        + list(plan_weekly_targets.keys())
        + list(projected_by_group.keys())
        + list(planned_by_group.keys())
    ):
        _bucket(int(group_id))

    exercise_ids = {
        int(row["exercise_id"])
        for bucket in buckets.values()
        for row in bucket["exercises"]
        if row.get("exercise_id") is not None
    }
    names_by_exercise = _exercise_names(conn, exercise_ids)

    items: list[dict[str, Any]] = []
    for group_id, bucket in buckets.items():
        stimulus = bucket["stimulus"]
        weekly_stimulus = weekly_rate(stimulus, window.days) or 0.0

        min_sets: Optional[float]
        max_sets: Optional[float]
        outside_plan = False
        if template_plan is not None:
            # Meta do PROPRIO Modelo `equipment_series`: alvo = ativacao
            # planejada semanal; min/max NAO existem (plano fixo) e ficam
            # `None`, nunca zero nem +-X% inventado. Grupo que o Modelo nao
            # trabalha entra com alvo 0 e a limitacao "fora do Modelo".
            target_sets = plan_weekly_targets.get(group_id, 0.0)
            min_sets, max_sets = None, None
            item_target_source = TargetSource.TEMPLATE_PLAN
            outside_plan = not template_plan.contains(group_id)
            status = classify_planned_volume_status(weekly_stimulus, target_sets)
        else:
            min_sets, target_sets, max_sets, item_target_source = resolve_targets(
                group_id, targets_by_group, target_source
            )
            # SO o realizado entra na classificacao (§7). Projetado e planejado
            # ficam em campos proprios, ao lado.
            status = classify_weekly_volume_status(weekly_stimulus, min_sets, target_sets, max_sets)

        quality, limitations = item_data_quality(
            target_source=item_target_source,
            direct_sets=bucket["direct"],
            unattributed_sets_in_window=unattributed_sets,
            outside_template_plan=outside_plan,
        )

        sessions = bucket["sessions"]
        direct_sessions = bucket["direct_sessions"]
        last_session = max(sessions, key=lambda s: (s["trained_at"] or datetime.min)) if sessions else None
        last_direct = (
            max(direct_sessions, key=lambda s: (s["trained_at"] or datetime.min)) if direct_sessions else None
        )
        # Horas desde o ultimo estimulo medidas contra o relogio DO USUARIO
        # (`now_local`, fuso de `users.timezone`) - nao contra o relogio do
        # servidor, que roda em UTC e faria um treino da noite anterior
        # parecer 3h mais antigo do que foi.
        hours_since = None
        if last_session and last_session["trained_at"]:
            delta = (now_local - last_session["trained_at"]).total_seconds() / 3600.0
            hours_since = round_half_up(max(delta, 0.0), 2)

        planned_row = planned_by_group.get(group_id) or {}
        planned_sets = _num(planned_row.get("planned_effective_sets"))
        projected_sets = projected_by_group.get(group_id)

        item = {
            "muscle_group_id": group_id,
            "muscle_group": names_by_group.get(group_id, f"Grupo {group_id}"),
            "targets": {
                "min": metric(
                    min_sets, unit="séries/sem", nature=DataNature.PLANNED, version_key="weekly_volume_status"
                ),
                "target": metric(
                    target_sets, unit="séries/sem", nature=DataNature.PLANNED, version_key="weekly_volume_status"
                ),
                "max": metric(
                    max_sets, unit="séries/sem", nature=DataNature.PLANNED, version_key="weekly_volume_status"
                ),
                "source": item_target_source,
                "training_type_id": None,
            },
            "realized": {
                "physical_sets": metric(
                    float(bucket["physical_sets"]),
                    unit="séries",
                    nature=DataNature.OBSERVED,
                    version_key="physical_sets",
                ),
                "effective_sets_stimulus": metric(
                    stimulus, unit="séries", nature=DataNature.DERIVED, version_key="effective_sets_stimulus"
                ),
                "effective_sets_fatigue": metric(
                    bucket["fatigue"],
                    unit="séries",
                    nature=DataNature.DERIVED,
                    version_key="effective_sets_fatigue",
                ),
                "direct_sets": metric(
                    bucket["direct"], unit="séries", nature=DataNature.OBSERVED, version_key="role_sets"
                ),
                "indirect_sets": metric(
                    bucket["indirect"], unit="séries", nature=DataNature.OBSERVED, version_key="role_sets"
                ),
                "stabilizer_sets": metric(
                    bucket["stabilizer"], unit="séries", nature=DataNature.OBSERVED, version_key="role_sets"
                ),
                "weekly_effective_sets": metric(
                    weekly_stimulus,
                    unit="séries/sem",
                    nature=DataNature.DERIVED,
                    version_key="weekly_volume_status",
                ),
                "avg_rpe": metric(
                    (bucket["rpe_sum"] / bucket["rpe_n"]) if bucket["rpe_n"] else None,
                    unit=None,
                    nature=DataNature.OBSERVED,
                    version_key="physical_sets",
                ),
            },
            "planned": {
                "effective_sets": metric(
                    planned_sets, unit="séries", nature=DataNature.PLANNED, version_key="effective_sets_stimulus"
                ),
                "weekly_effective_sets": metric(
                    weekly_rate(planned_sets, window.days),
                    unit="séries/sem",
                    nature=DataNature.PLANNED,
                    version_key="weekly_volume_status",
                ),
                "plans_count": int(planned_row.get("plans_count") or 0),
            },
            "projected": {
                "remaining_sets": metric(
                    projected_sets,
                    unit="séries no ciclo",
                    nature=DataNature.PROJECTED,
                    version_key="effective_sets_stimulus",
                    limitations=[
                        "Projeção do que ainda falta no ciclo do Modelo atribuído — não entra no status "
                        "e não é uma taxa semanal."
                    ],
                ),
                "pending_slots": pending_slots,
                "cycle_length_days": cycle_length_days,
            },
            "frequency": _frequency_block(sessions, direct_sessions),
            "readiness": _readiness_block(readiness_by_group.get(group_id)) if include_readiness else None,
            "last_stimulus_local_date": last_session["local_date"] if last_session else None,
            "last_direct_local_date": last_direct["local_date"] if last_direct else None,
            "hours_since_last_stimulus": metric(
                hours_since, unit="h", nature=DataNature.DERIVED, version_key="muscle_frequency"
            ),
            "status": status,
            "status_basis": "realized_only",
            "top_exercises": _top_exercises(bucket["exercises"], stimulus, names_by_exercise),
            "data_quality": quality,
            "limitations": limitations,
        }
        items.append(item)

    items.sort(
        key=lambda i: (i["realized"]["effective_sets_stimulus"]["value"] or 0.0, i["muscle_group"]),
        reverse=True,
    )

    envelope_limitations: list[str] = []
    if template_plan is not None:
        envelope_limitations.append(
            template_plan_cadence_limitation(template_plan.cycle_length_days, template_plan.slots_count)
        )
    if unattributed_rows:
        nomes = ", ".join(
            str(row.get("original_name") or f"exercicio {row.get('exercise_id')}")
            for row in unattributed_rows[:3]
        )
        envelope_limitations.append(
            f"{unattributed_sets} serie(s) de {len(unattributed_rows)} exercicio(s) sem contribuicao "
            f"muscular cadastrada ficaram fora de todo grupo ({nomes})."
        )
    if not status_only and not planned_by_group:
        envelope_limitations.append(
            "Nenhuma ficha salva na janela tem exercicio com contribuicao muscular cadastrada: "
            "nao ha volume planejado para comparar."
        )

    return items, envelope_limitations, unattributed_sets


def _frequency_block(
    sessions: list[dict[str, Any]], direct_sessions: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "direct_sessions": metric(
            float(len(direct_sessions)),
            unit="sessões",
            nature=DataNature.OBSERVED,
            version_key="muscle_frequency",
        ),
        "participation_sessions": metric(
            float(len(sessions)),
            unit="sessões",
            nature=DataNature.OBSERVED,
            version_key="muscle_frequency",
        ),
        "median_interval_hours": metric(
            median_interval_hours(s["trained_at"] for s in direct_sessions),
            unit="h",
            nature=DataNature.DERIVED,
            version_key="muscle_frequency",
            limitations=(
                []
                if len(direct_sessions) >= 2
                else ["Menos de dois estímulos diretos na janela: não há intervalo a medir."]
            ),
        ),
    }


def _readiness_block(row: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        # Codigo CANONICO da prontidao (`READY|REDUCED_DOSE|NO_DIRECT_WORK`),
        # o mesmo enum de `/recovery-signals` e do dominio `readiness` do
        # frontend. O `status` do servico de recuperacao e a COR
        # (`green|yellow|red`): repassa-lo aqui deixava todo selo da tela
        # cinza, como "estado nao previsto no contrato".
        "status": row.get("readiness_status"),
        "guidance": row.get("guidance"),
        "estimated_recovery_hours": metric(
            _num(row.get("estimated_recovery_hours")),
            unit="h",
            nature=DataNature.INFERRED,
            version_key="readiness",
        ),
        "hours_since_last_stimulus": metric(
            _num(row.get("hours_since_trained")),
            unit="h",
            nature=DataNature.DERIVED,
            version_key="muscle_frequency",
        ),
        "explanation": list(row.get("explanation") or []),
    }


def _top_exercises(
    rows: list[Mapping[str, Any]],
    group_stimulus: float,
    names_by_exercise: Mapping[int, str],
) -> list[dict[str, Any]]:
    """Exercicios que mais contribuiram, com o PESO da contribuicao
    (`contribution_share`): "quanto deste grupo veio deste exercicio". Sem o
    share, uma lista de nomes nao responde "o volume chegou no musculo certo?"."""
    merged: dict[Any, dict[str, Any]] = {}
    for row in rows:
        exercise_id = row.get("exercise_id")
        key = int(exercise_id) if exercise_id is not None else ("name", row.get("original_name"))
        bucket = merged.setdefault(
            key,
            {
                "exercise_id": int(exercise_id) if exercise_id is not None else None,
                "original_name": row.get("original_name"),
                "sets_count": 0,
                "effective_sets_stimulus": 0.0,
                "roles": set(),
                "last_local_date": None,
            },
        )
        bucket["sets_count"] += int(_num(row.get("sets_count")) or 0)
        bucket["effective_sets_stimulus"] += _num(row.get("stimulus_sets")) or 0.0
        role = (row.get("role") or "").strip().upper()
        if role:
            bucket["roles"].add(role)
        last = _as_date(row.get("last_local_date"))
        if last and (bucket["last_local_date"] is None or last > bucket["last_local_date"]):
            bucket["last_local_date"] = last

    results = []
    for bucket in merged.values():
        exercise_id = bucket["exercise_id"]
        results.append(
            {
                "exercise_id": exercise_id,
                "name": (
                    names_by_exercise.get(exercise_id)
                    if exercise_id is not None
                    else None
                )
                or bucket["original_name"]
                or "Exercício sem nome",
                "sets_count": bucket["sets_count"],
                "effective_sets_stimulus": round(bucket["effective_sets_stimulus"], 2),
                "contribution_share": (
                    round(bucket["effective_sets_stimulus"] / group_stimulus, 3)
                    if group_stimulus
                    else None
                ),
                "roles": sorted(bucket["roles"]),
                "last_local_date": bucket["last_local_date"],
            }
        )
    results.sort(key=lambda r: r["effective_sets_stimulus"], reverse=True)
    return results[:MAX_TOP_EXERCISES]


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def build_muscle_summary(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    training_type_id: Optional[int] = None,
    training_type_source: TargetSource = TargetSource.ASSIGNED_TEMPLATE,
    include_readiness: bool = True,
    now_local: Optional[datetime] = None,
    status_only: bool = False,
    targets_by_group: Optional[Mapping[int, Mapping[str, float]]] = None,
    template_plan: Optional[TemplatePlan] = None,
    template_plan_resolved: bool = False,
    group_names: Optional[Mapping[int, str]] = None,
    include_comparatives: bool = False,
) -> dict[str, Any]:
    """Resposta de `GET /analytics/dashboard/muscles`.

    `status_only=True` devolve so o que o `status` do realizado precisa
    (sem top exercicios, planejado nem projetado - campos zerados com
    `plans_count=0`/`pending_slots=0`); `targets_by_group` reaproveita as
    metas ja resolvidas por quem chama. Os dois sao uso exclusivo da visao
    geral (F1.6), que le os dois ultimos ciclos; a rota `/muscles` sempre
    chama com os defaults.

    Meta do proprio Modelo (`template_plan`): quando o Modelo atribuido mais
    recente e `equipment_series`, a meta de cada grupo e a ativacao
    planejada dele (`resolve_template_plan`) e `target_source` vira
    `template_plan` - a nao ser que um `training_type_id` EXPLICITO tenha
    sido pedido, que continua vencendo. Quem chama varias vezes na mesma
    requisicao (visao geral) resolve o plano UMA vez e passa
    `template_plan=`/`template_plan_resolved=True`, para nao repetir as duas
    consultas por chamada.

    `group_names` segue a mesma ideia (M29): quem ja tem `{id: nome}` dos
    grupos em maos passa adiante em vez de fazer este servico reler
    `muscle_groups` a cada chamada. Sem o parametro, a leitura acontece como
    sempre - a rota `/muscles` nao muda.

    `include_comparatives` (M34) acrescenta o bloco POR SESSAO (T-0..T-3, por
    equipamento e mapa de calor) ao custo de UM statement. Nasce DESLIGADO de
    proposito: a visao geral e a segunda chamada da tela (a da prontidao) nao
    precisam dele, e ligar por padrao faria as duas pagarem a consulta sem
    usar o resultado."""
    owner = require_user_id(user_id)
    now_local = now_local or datetime.combine(window.date_to, datetime.max.time())

    explicit = training_type_id is not None and training_type_source is TargetSource.EXPLICIT
    if explicit:
        template_plan = None
    elif not template_plan_resolved:
        template_plan = resolve_template_plan(conn, owner, date_to=window.date_to)

    resolved_targets: dict[int, dict[str, float]] = {}
    if template_plan is not None:
        source = TargetSource.TEMPLATE_PLAN
        training_type_id = None
    else:
        if targets_by_group is not None:
            resolved_targets = {int(k): dict(v) for k, v in targets_by_group.items()}
        elif training_type_id is not None:
            # M12 (migracao 052): a meta tem vigencia. Uma janela que terminou
            # em julho e lida contra a meta de julho, e nao contra a de hoje -
            # pela mesma regra de fronteira de versao do M23.
            from ...training_type_targets import resolve_targets_for_window

            resolved_targets = resolve_targets_for_window(training_type_id, window.date_to)
        source = training_type_source if training_type_id is not None else TargetSource.FALLBACK_GENERIC
    targets_by_group = resolved_targets

    # Nomes dos grupos resolvidos UMA vez quando o bloco por sessao entra
    # junto (M34): `_build_items` e `build_muscle_comparatives` precisam do
    # mesmo `{id: nome}`, e deixar cada um ler `muscle_groups` faria o bloco
    # custar DOIS statements em vez de um - foi exatamente o que o teto de
    # medicao pegou. Sem `include_comparatives` nada muda: a leitura continua
    # acontecendo dentro de `_build_items`, como sempre.
    if include_comparatives and not status_only and group_names is None:
        group_names = muscle_group_names(conn)

    technique_sets = _WindowTechniqueSets(conn, owner, window)
    items, limitations, unattributed = _build_items(
        conn,
        user_id=owner,
        window=window,
        targets_by_group=targets_by_group,
        target_source=source,
        include_readiness=include_readiness,
        now_local=now_local,
        status_only=status_only,
        template_plan=template_plan,
        group_names=group_names,
        technique_sets=technique_sets,
    )
    for item in items:
        item["targets"]["training_type_id"] = (
            training_type_id if item["targets"]["source"] is not TargetSource.FALLBACK_GENERIC else None
        )

    comparatives = None
    metric_versions = dict(_METRIC_VERSIONS)
    if include_comparatives and not status_only:
        comparatives = build_muscle_comparatives(
            conn, user_id=owner, window=window, group_names=group_names, technique_sets=technique_sets
        )
        metric_versions.update(_COMPARATIVE_METRIC_VERSIONS)

    log_event(
        "dashboard.computed",
        block="muscles",
        user_id=owner,
        window=window.kind.value,
        groups=len(items),
        unattributed_sets=unattributed,
        comparatives=bool(comparatives),
    )

    return {
        "window": window,
        "metric_versions": metric_versions,
        "data_quality": envelope_quality(items),
        "limitations": limitations,
        "items": items,
        "target_source": source,
        "training_type_id": training_type_id,
        "template_id": template_plan.template_id if template_plan is not None else None,
        "template_name": template_plan.template_name if template_plan is not None else None,
        "unattributed_sets": unattributed,
        "comparatives": comparatives,
    }


def build_muscle_drilldown(
    conn: Any,
    muscle_group_id: int,
    *,
    user_id: Any,
    window: WindowInfo,
    training_type_id: Optional[int] = None,
    training_type_source: TargetSource = TargetSource.ASSIGNED_TEMPLATE,
    include_readiness: bool = True,
    now_local: Optional[datetime] = None,
    sets_limit: int = MAX_DRILLDOWN_SETS,
) -> Optional[dict[str, Any]]:
    """Resposta de `GET /analytics/dashboard/muscles/{muscle_group_id}`.

    `None` quando o grupo nao existe em `muscle_groups` (o endpoint converte
    em 404). Grupo existente sem nenhuma serie na janela devolve 200 com o
    item zerado e `sessions: []` - "nao treinei este grupo" e uma resposta,
    nao um erro."""
    owner = require_user_id(user_id)

    names = _muscle_group_names(conn)
    if int(muscle_group_id) not in names:
        return None

    summary = build_muscle_summary(
        conn,
        user_id=owner,
        window=window,
        training_type_id=training_type_id,
        training_type_source=training_type_source,
        include_readiness=include_readiness,
        now_local=now_local,
    )
    item = next(
        (i for i in summary["items"] if i["muscle_group_id"] == int(muscle_group_id)),
        None,
    )
    if item is None:
        # Grupo existe no catalogo mas nao apareceu em nenhuma serie nem em
        # nenhuma meta: item vazio, com o nome resolvido.
        item = _empty_item(
            int(muscle_group_id),
            names[int(muscle_group_id)],
            window,
            template_plan=summary["target_source"] is TargetSource.TEMPLATE_PLAN,
        )

    capped = max(1, min(int(sets_limit), MAX_DRILLDOWN_SETS))
    set_rows = _rows(
        conn,
        _with_trained_at(DRILLDOWN_SETS_SQL, window),
        (owner, window.date_from, window.date_to, int(muscle_group_id), capped + 1),
    )
    truncated = len(set_rows) > capped
    set_rows = set_rows[:capped]

    exercise_ids = {int(r["exercise_id"]) for r in set_rows if r.get("exercise_id") is not None}
    names_by_exercise = _exercise_names(conn, exercise_ids)

    sessions = _group_sets_into_sessions(set_rows, names_by_exercise)

    limitations = list(summary["limitations"])
    if truncated:
        limitations.append(
            f"O detalhamento mostra as {capped} séries mais recentes do período; há séries mais antigas "
            "fora desta lista (limite de 1 MB da resposta)."
        )

    log_event(
        "dashboard.computed",
        block="muscle_drilldown",
        user_id=owner,
        window=window.kind.value,
        muscle_group_id=int(muscle_group_id),
        sets=len(set_rows),
        truncated=truncated,
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": item["data_quality"],
        "limitations": limitations,
        "item": item,
        "sessions": sessions,
        "sets_limit": capped,
        "sets_truncated": truncated,
        "target_source": summary["target_source"],
        "training_type_id": summary["training_type_id"],
        "template_id": summary["template_id"],
        "template_name": summary["template_name"],
    }


def _group_sets_into_sessions(
    rows: list[Mapping[str, Any]], names_by_exercise: Mapping[int, str]
) -> list[dict[str, Any]]:
    """Series -> exposicoes -> sessoes, na ordem "mais recente primeiro".

    Uma exposicao e uma LINHA de `exercises` (`exercise_row_id`): duas
    passagens pelo mesmo aparelho na mesma sessao sao duas exposicoes - somar
    as duas esconderia a segunda."""
    sessions: dict[int, dict[str, Any]] = {}
    exposures: dict[tuple[int, int, str], dict[str, Any]] = {}
    # Series equivalentes de cada linha (Fase 5, PR 4): a aplicacao de tecnica
    # vale a sua fatia do bloco da ancora. O corte por `LIMIT` fica nas
    # sessoes mais antigas e, dentro do exercicio, por `set_number` - a ancora
    # vem antes das aplicacoes, entao nunca fica uma aplicacao sem ela.
    units_by_index = realized_set_units(rows, catalog=new_catalog(), exercise_key="exercise_row_id")

    for row, units in zip(rows, units_by_index):
        activity_id = int(row["activity_id"])
        session = sessions.setdefault(
            activity_id,
            {
                "activity_id": activity_id,
                "activity_name": row.get("activity_name"),
                "local_date": _as_date(row.get("local_date")),
                "started_at": _as_datetime(row.get("trained_at")),
                "physical_sets": 0,
                "effective_sets_stimulus": 0.0,
                "has_direct_work": False,
                "exposures": [],
            },
        )

        role = (row.get("role") or "").strip().upper()
        exercise_row_id = int(row["exercise_row_id"])
        exercise_id = row.get("exercise_id")
        key = (activity_id, exercise_row_id, role)
        exposure = exposures.get(key)
        if exposure is None:
            exposure = {
                "exercise_row_id": exercise_row_id,
                "exercise_id": int(exercise_id) if exercise_id is not None else None,
                "name": (
                    names_by_exercise.get(int(exercise_id)) if exercise_id is not None else None
                )
                or row.get("original_name")
                or "Exercício sem nome",
                "position_in_session": (
                    int(row["position_in_session"]) if row.get("position_in_session") is not None else None
                ),
                "role": role,
                "effective_sets_stimulus": 0.0,
                "sets": [],
            }
            exposures[key] = exposure
            session["exposures"].append(exposure)

        stimulus = (_num(row.get("stimulus_weight")) or 0.0) * units
        fatigue = (_num(row.get("fatigue_weight")) or 0.0) * units
        exposure["effective_sets_stimulus"] = round(exposure["effective_sets_stimulus"] + stimulus, 2)
        exposure["sets"].append(
            {
                "set_id": int(row["set_id"]),
                "set_number": int(row["set_number"]) if row.get("set_number") is not None else None,
                "set_type": normalize_set_type(row.get("set_type")),
                "reps": int(row["reps"]) if row.get("reps") is not None else None,
                "weight_kg": _num(row.get("weight_kg")),
                "rpe": _num(row.get("rpe")),
                "effective_sets_stimulus": round(stimulus, 3),
                "effective_sets_fatigue": round(fatigue, 3),
                "role": role,
            }
        )

        session["effective_sets_stimulus"] = round(session["effective_sets_stimulus"] + stimulus, 2)
        if role == _ROLE_DIRECT:
            session["has_direct_work"] = True

    # `physical_sets` da sessao conta SERIES distintas: a mesma serie pode
    # aparecer em dois papeis (PRIMARY e STABILIZER do mesmo grupo) e contar
    # duas vezes seria contar o mesmo trabalho duas vezes.
    seen_sets: dict[int, set[int]] = {}
    for row in rows:
        activity_id = int(row["activity_id"])
        seen_sets.setdefault(activity_id, set()).add(int(row["set_id"]))
    for activity_id, ids in seen_sets.items():
        sessions[activity_id]["physical_sets"] = len(ids)

    ordered = sorted(
        sessions.values(),
        key=lambda s: (s["started_at"] or datetime.min, s["activity_id"]),
        reverse=True,
    )
    return ordered


def _empty_item(
    muscle_group_id: int, name: str, window: WindowInfo, *, template_plan: bool = False
) -> dict[str, Any]:
    """Item de um grupo sem nenhuma serie e sem meta: tudo zero OBSERVADO
    (zero series e um fato) e a meta no fallback generico - ou, quando a
    resposta e `template_plan`, alvo 0 com min/max `None` e a limitacao
    "fora do Modelo" (o Modelo nao trabalha este grupo)."""
    if template_plan:
        source = TargetSource.TEMPLATE_PLAN
        min_sets: Optional[float] = None
        target_sets = 0.0
        max_sets: Optional[float] = None
        status = classify_planned_volume_status(0.0, 0.0)
    else:
        source = TargetSource.FALLBACK_GENERIC
        min_sets = DASHBOARD_FALLBACK_MIN_SETS
        target_sets = DASHBOARD_FALLBACK_TARGET_SETS
        max_sets = DASHBOARD_FALLBACK_MAX_SETS
        status = classify_weekly_volume_status(
            0.0, DASHBOARD_FALLBACK_MIN_SETS, DASHBOARD_FALLBACK_TARGET_SETS, DASHBOARD_FALLBACK_MAX_SETS
        )
    quality, limitations = item_data_quality(
        target_source=source,
        direct_sets=0.0,
        unattributed_sets_in_window=0,
        outside_template_plan=template_plan,
    )
    zero = lambda version_key, unit, nature: metric(  # noqa: E731
        0.0, unit=unit, nature=nature, version_key=version_key
    )
    return {
        "muscle_group_id": muscle_group_id,
        "muscle_group": name,
        "targets": {
            "min": metric(
                min_sets,
                unit="séries/sem",
                nature=DataNature.PLANNED,
                version_key="weekly_volume_status",
            ),
            "target": metric(
                target_sets,
                unit="séries/sem",
                nature=DataNature.PLANNED,
                version_key="weekly_volume_status",
            ),
            "max": metric(
                max_sets,
                unit="séries/sem",
                nature=DataNature.PLANNED,
                version_key="weekly_volume_status",
            ),
            "source": source,
            "training_type_id": None,
        },
        "realized": {
            "physical_sets": zero("physical_sets", "séries", DataNature.OBSERVED),
            "effective_sets_stimulus": zero("effective_sets_stimulus", "séries", DataNature.DERIVED),
            "effective_sets_fatigue": zero("effective_sets_fatigue", "séries", DataNature.DERIVED),
            "direct_sets": zero("role_sets", "séries", DataNature.OBSERVED),
            "indirect_sets": zero("role_sets", "séries", DataNature.OBSERVED),
            "stabilizer_sets": zero("role_sets", "séries", DataNature.OBSERVED),
            "weekly_effective_sets": zero("weekly_volume_status", "séries/sem", DataNature.DERIVED),
            "avg_rpe": metric(None, unit=None, nature=DataNature.OBSERVED, version_key="physical_sets"),
        },
        "planned": {
            "effective_sets": metric(
                None, unit="séries", nature=DataNature.PLANNED, version_key="effective_sets_stimulus"
            ),
            "weekly_effective_sets": metric(
                None, unit="séries/sem", nature=DataNature.PLANNED, version_key="weekly_volume_status"
            ),
            "plans_count": 0,
        },
        "projected": {
            "remaining_sets": metric(
                None, unit="séries no ciclo", nature=DataNature.PROJECTED, version_key="effective_sets_stimulus"
            ),
            "pending_slots": 0,
            "cycle_length_days": None,
        },
        "frequency": _frequency_block([], []),
        "readiness": None,
        "last_stimulus_local_date": None,
        "last_direct_local_date": None,
        "hours_since_last_stimulus": metric(
            None, unit="h", nature=DataNature.DERIVED, version_key="muscle_frequency"
        ),
        "status": status,
        "status_basis": "realized_only",
        "top_exercises": [],
        "data_quality": quality,
        "limitations": limitations,
    }


# ---------------------------------------------------------------------------
# F2.5 / M34 - comparativos POR SESSAO e mapa de calor
# ---------------------------------------------------------------------------
#
# Paridade com `/volume-grupamentos`, que a tela do hub nao tinha: comparativo
# das ULTIMAS SESSOES (T-0..T-3), comparativo por EQUIPAMENTO e mapa de calor.
#
# Tres diferencas deliberadas em relacao a tela legada:
#
# 1. **A unidade e declarada.** La as quatro tabelas conviviam sem dizer que
#    uma comparava sessoes e outra semanas; aqui o bloco inteiro e `unit =
#    "session"` e a tela rotula.
# 2. **Series preparatorias ficam fora, inclusive do RPE.** A cadeia de JOIN
#    e a mesma do realizado (`sql_not_preparatory`). RPE de aquecimento nao
#    mede esforco de serie de trabalho: incluir aquecimento puxaria a media
#    para baixo e faria uma sessao dura parecer leve - e a progressao do
#    projeto ja le so `working`, entao incluir aqui criaria DOIS RPEs medios
#    incompativeis na mesma tela.
# 3. **O grupo do estimulo sai das contribuicoes, nao de um "grupo primario"
#    unico.** A tela legada escolhia UM grupo por exercicio (primeiro
#    `PRIMARY`, senao primeiro `SECONDARY`) e jogava todo o volume nele; aqui
#    cada serie pinga em TODOS os grupos que ela ativa, com o peso cadastrado
#    - mesma regra do resto de `/muscles`.


def _decimal(value: Any) -> Decimal:
    """Valor do banco -> `Decimal` exato. `str()` antes de converter: um float
    vindo do shim vira o decimal que ele REPRESENTA (`73.845`), nao a expansao
    binaria (`73.844999...`). Ausente/invalido -> 0."""
    if value is None or isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return Decimal(0)


def _avg(total: Optional[float], count: Optional[float]) -> Optional[float]:
    """Media que devolve `None` - nunca zero - quando nao ha amostra."""
    n = _num(count) or 0
    if n <= 0:
        return None
    return round((_num(total) or 0.0) / n, 2)


@dataclass
class _Bucket:
    """Acumulador de uma celula (grupo x sessao x equipamento, ou qualquer
    soma desses)."""

    physical_sets: int = 0
    stimulus_sets: float = 0.0
    # Acumulada em `Decimal` (M58): a soma nao depende da ordem das linhas nem
    # do tipo que o driver devolve (`Decimal` no PyMySQL, texto/float no shim
    # da Data API) - ver `tonnage`.
    tonnage_kg: Decimal = field(default_factory=Decimal)
    tonnage_n: int = 0
    tonnage_excluded_sets: int = 0
    rpe_sum: float = 0.0
    rpe_n: int = 0
    has_direct: bool = False

    def add(self, row: Mapping[str, Any]) -> None:
        self.physical_sets += int(_num(row.get("physical_sets")) or 0)
        self.stimulus_sets += _num(row.get("stimulus_sets")) or 0.0
        self.tonnage_kg += _decimal(row.get("tonnage_kg"))
        self.tonnage_n += int(_num(row.get("tonnage_n")) or 0)
        self.tonnage_excluded_sets += int(_num(row.get("tonnage_excluded_sets")) or 0)
        self.rpe_sum += _num(row.get("rpe_sum")) or 0.0
        self.rpe_n += int(_num(row.get("rpe_n")) or 0)
        if int(_num(row.get("has_direct")) or 0) > 0:
            self.has_direct = True

    def merge(self, other: "_Bucket") -> None:
        self.physical_sets += other.physical_sets
        self.stimulus_sets += other.stimulus_sets
        self.tonnage_kg += other.tonnage_kg
        self.tonnage_n += other.tonnage_n
        self.tonnage_excluded_sets += other.tonnage_excluded_sets
        self.rpe_sum += other.rpe_sum
        self.rpe_n += other.rpe_n
        self.has_direct = self.has_direct or other.has_direct

    @property
    def effective_sets(self) -> float:
        return round(self.stimulus_sets, 2)

    @property
    def tonnage(self) -> Optional[float]:
        """`None` quando NENHUMA serie que conta na tonelagem tinha carga e
        repeticao registradas: peso corporal somando zero quilos nao e "zero de
        tonelagem", e um aparelho so `assisted`/`bodyweight` (M58) tambem nao.

        Arredondamento em `Decimal` (meia para cima), nao `round(float)`: a
        soma chega como `Decimal` num backend e `float` no outro, e
        `round(73.845, 2)` em float da 73.84 ou 73.85 conforme a representacao
        binaria - o mesmo valor nao pode divergir entre PyMySQL e Data API."""
        if self.tonnage_n <= 0:
            return None
        return float(self.tonnage_kg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @property
    def avg_rpe(self) -> Optional[float]:
        return _avg(self.rpe_sum, self.rpe_n)


def assemble_comparatives(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_names: Mapping[int, str],
    session_points: int = SESSION_COMPARATIVE_POINTS,
    equipment_limit: int = MAX_EQUIPMENTS_PER_GROUP,
    heatmap_sessions: int = MAX_HEATMAP_SESSIONS,
) -> dict[str, Any]:
    """Linhas de `SESSION_EQUIPMENT_SQL` -> os tres blocos por sessao.

    Funcao PURA: recebe as linhas ja lidas, nao toca no banco. Toda a regra
    de recorte (T-0..T-3, top equipamentos, sessoes do mapa) mora aqui, que e
    onde da para testa-la sem dataset."""
    ordered_rows = list(rows)

    # Sessoes da janela, da mais recente para a mais antiga. A ordem e por
    # (`local_date`, `activity_id`) e NAO por `started_at`: sessao importada
    # costuma vir sem horario, e ordenar por um campo que pode ser nulo faria
    # o T-0 depender da ordem em que o banco devolveu as linhas. Com o id como
    # desempate, dois treinos no mesmo dia ficam sempre na mesma ordem.
    sessions: dict[int, dict[str, Any]] = {}
    for row in ordered_rows:
        activity_id = int(_num(row.get("activity_id")) or 0)
        if activity_id <= 0:
            continue
        sessions.setdefault(
            activity_id,
            {
                "activity_id": activity_id,
                "activity_name": row.get("activity_name"),
                "local_date": _as_date(row.get("local_date")),
                "sort_key": (
                    _as_date(row.get("local_date")) or date.min,
                    activity_id,
                ),
            },
        )

    sessions_desc = sorted(sessions.values(), key=lambda s: s["sort_key"], reverse=True)

    # (grupo, sessao, equipamento) -> bucket
    by_cell: dict[tuple[int, int, Optional[str]], _Bucket] = {}
    for row in ordered_rows:
        group_id = int(_num(row.get("muscle_group_id")) or 0)
        activity_id = int(_num(row.get("activity_id")) or 0)
        if group_id <= 0 or activity_id <= 0:
            continue
        equipment = row.get("equipment_name")
        equipment = str(equipment).strip() if equipment not in (None, "") else None
        key = (group_id, activity_id, equipment)
        by_cell.setdefault(key, _Bucket()).add(row)

    group_ids = sorted({key[0] for key in by_cell})

    def group_name(group_id: int) -> str:
        return group_names.get(group_id) or f"Grupo {group_id}"

    # --- 1) por SESSAO ------------------------------------------------------
    by_session_block: list[dict[str, Any]] = []
    selected_by_group: dict[int, list[dict[str, Any]]] = {}

    for group_id in group_ids:
        per_session: dict[int, _Bucket] = {}
        for (gid, activity_id, _equipment), bucket in by_cell.items():
            if gid != group_id:
                continue
            per_session.setdefault(activity_id, _Bucket()).merge(bucket)

        ordered = [s for s in sessions_desc if s["activity_id"] in per_session]
        selected = ordered[:session_points]
        selected_by_group[group_id] = selected

        points = []
        for offset, session in enumerate(selected):
            bucket = per_session[session["activity_id"]]
            points.append(
                {
                    "offset": offset,
                    "activity_id": session["activity_id"],
                    "activity_name": session["activity_name"],
                    "local_date": session["local_date"],
                    "physical_sets": bucket.physical_sets,
                    "effective_sets_stimulus": bucket.effective_sets,
                    "tonnage_kg": bucket.tonnage,
                    "tonnage_excluded_sets": bucket.tonnage_excluded_sets,
                    "avg_rpe": bucket.avg_rpe,
                    "has_direct_work": bucket.has_direct,
                }
            )

        by_session_block.append(
            {
                "muscle_group_id": group_id,
                "muscle_group": group_name(group_id),
                "points": points,
                "sessions_in_window": len(ordered),
            }
        )

    by_session_block.sort(key=lambda item: item["muscle_group"])

    # --- 2) por EQUIPAMENTO -------------------------------------------------
    # As colunas sao as MESMAS sessoes T-0..T-3 do grupo: um equipamento que
    # nao apareceu em T-2 simplesmente nao tem ponto com `offset=2`, em vez de
    # deslizar o T-3 dele para a coluna do T-2 e sugerir uma comparacao que
    # nao existe.
    by_equipment_block: list[dict[str, Any]] = []
    for group_id in group_ids:
        selected = selected_by_group.get(group_id) or []
        offset_by_activity = {s["activity_id"]: index for index, s in enumerate(selected)}
        if not offset_by_activity:
            continue

        per_equipment: dict[Optional[str], dict[int, _Bucket]] = {}
        for (gid, activity_id, equipment), bucket in by_cell.items():
            if gid != group_id or activity_id not in offset_by_activity:
                continue
            per_equipment.setdefault(equipment, {}).setdefault(activity_id, _Bucket()).merge(bucket)

        rows_out: list[dict[str, Any]] = []
        for equipment, per_session in per_equipment.items():
            total = _Bucket()
            points = []
            for activity_id, bucket in per_session.items():
                total.merge(bucket)
                session = sessions[activity_id]
                points.append(
                    {
                        "offset": offset_by_activity[activity_id],
                        "activity_id": activity_id,
                        "local_date": session["local_date"],
                        "physical_sets": bucket.physical_sets,
                        "effective_sets_stimulus": bucket.effective_sets,
                        "tonnage_kg": bucket.tonnage,
                        "tonnage_excluded_sets": bucket.tonnage_excluded_sets,
                        "avg_rpe": bucket.avg_rpe,
                    }
                )
            points.sort(key=lambda point: point["offset"])
            rows_out.append(
                {
                    "equipment_name": equipment,
                    "points": points,
                    "effective_sets_stimulus": total.effective_sets,
                }
            )

        rows_out.sort(
            key=lambda item: (-item["effective_sets_stimulus"], item["equipment_name"] or "")
        )
        group_total = sum(item["effective_sets_stimulus"] for item in rows_out)
        for item in rows_out:
            item["share_of_group"] = (
                round(item["effective_sets_stimulus"] / group_total, 4) if group_total > 0 else None
            )

        by_equipment_block.append(
            {
                "muscle_group_id": group_id,
                "muscle_group": group_name(group_id),
                "equipments": rows_out[:equipment_limit],
                "equipment_limit": equipment_limit,
                "equipment_truncated": len(rows_out) > equipment_limit,
            }
        )

    by_equipment_block.sort(key=lambda item: item["muscle_group"])

    # --- 3) MAPA DE CALOR ---------------------------------------------------
    heatmap_block: list[dict[str, Any]] = []
    for session in sessions_desc[:heatmap_sessions]:
        activity_id = session["activity_id"]
        per_group: dict[int, _Bucket] = {}
        for (gid, act_id, _equipment), bucket in by_cell.items():
            if act_id != activity_id:
                continue
            per_group.setdefault(gid, _Bucket()).merge(bucket)

        max_effective = max((b.effective_sets for b in per_group.values()), default=0.0)
        cells = []
        for gid, bucket in per_group.items():
            share = (
                round(bucket.effective_sets / max_effective, 4) if max_effective > 0 else None
            )
            cells.append(
                {
                    "muscle_group_id": gid,
                    "muscle_group": group_name(gid),
                    "physical_sets": bucket.physical_sets,
                    "effective_sets_stimulus": bucket.effective_sets,
                    "tonnage_kg": bucket.tonnage,
                    "tonnage_excluded_sets": bucket.tonnage_excluded_sets,
                    "avg_rpe": bucket.avg_rpe,
                    "share_of_max": share,
                    "band": classify_heatmap_band(share).value,
                }
            )
        cells.sort(key=lambda cell: (-cell["effective_sets_stimulus"], cell["muscle_group"]))

        heatmap_block.append(
            {
                "activity_id": activity_id,
                "activity_name": session["activity_name"],
                "local_date": session["local_date"],
                "max_effective_sets_stimulus": round(max_effective, 2),
                "cells": cells,
            }
        )

    limitations = [SESSION_UNIT_NOTE, HEATMAP_SCOPE_NOTE]
    incompletos = [
        item["muscle_group"]
        for item in by_session_block
        if len(item["points"]) < session_points
    ]
    if incompletos:
        limitations.append(
            "Com menos de "
            f"{session_points} sessões no período, o comparativo fica curto para: "
            f"{', '.join(sorted(incompletos))}."
        )
    if len(sessions_desc) > heatmap_sessions:
        limitations.append(
            f"O mapa de calor mostra as {heatmap_sessions} sessões mais recentes do período; "
            f"há {len(sessions_desc) - heatmap_sessions} sessão(ões) mais antiga(s) fora dele."
        )
    truncados = [item["muscle_group"] for item in by_equipment_block if item["equipment_truncated"]]
    if truncados:
        limitations.append(
            f"Mais de {equipment_limit} equipamentos em: {', '.join(sorted(truncados))} — "
            "a lista mostra os que mais contribuíram."
        )

    if not sessions_desc:
        quality = DataQuality.INSUFFICIENT
    elif len(sessions_desc) < 2:
        # Uma sessão só não é comparativo nenhum: não há T-1 com que comparar.
        quality = DataQuality.LOW
    elif incompletos:
        quality = DataQuality.MEDIUM
    else:
        quality = DataQuality.HIGH

    return {
        "unit": "session",
        "sessions_compared": len(sessions_desc),
        "by_session": by_session_block,
        "by_equipment": by_equipment_block,
        "heatmap": heatmap_block,
        "data_quality": quality,
        "limitations": limitations,
    }


def build_muscle_comparatives(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    group_names: Optional[Mapping[int, str]] = None,
    technique_sets: Optional[_WindowTechniqueSets] = None,
) -> dict[str, Any]:
    """UM statement (`SESSION_EQUIPMENT_SQL`) + a montagem pura acima.

    `group_names` segue a convencao do M29: quem ja tem `{id: nome}` passa
    adiante em vez de fazer este servico reler `muscle_groups`.

    Aplicacao de tecnica (Fase 5, PR 4): entra como linha extra da MESMA
    celula (grupo x sessao x equipamento), so com o estimulo da sua fatia -
    series fisicas, tonelagem e RPE ja vieram da consulta. A leitura das
    series com tecnica e a mesma dos totais (`technique_sets`), entao o bloco
    continua custando UM statement a mais."""
    owner = require_user_id(user_id)
    names = dict(group_names) if group_names is not None else muscle_group_names(conn)
    # Convencao de carga (M58): coluna da 045 ou `NULL`. A deteccao e cacheada
    # por schema (`load_conventions`), entao em regime nao custa statement.
    with conn.cursor() as cursor:
        convention = load_convention_select_expr(cursor, alias="eqex")
    rows = _rows(conn, session_equipment_sql(convention), (owner, window.date_from, window.date_to))
    if _has_technique_rows(rows):
        loader = technique_sets or _WindowTechniqueSets(conn, owner, window)
        rows = list(rows) + [
            {
                "muscle_group_id": row["muscle_group_id"],
                "activity_id": row["activity_id"],
                "activity_name": row.get("activity_name"),
                "local_date": row.get("local_date"),
                "equipment_name": row.get("equipment_name"),
                "stimulus_sets": (_num(row.get("stimulus_weight")) or 0.0) * units,
            }
            for row, units in loader.get().applications()
        ]
    return assemble_comparatives(rows, group_names=names)


__all__ = [
    "MAX_DRILLDOWN_SETS",
    "MAX_EQUIPMENTS_PER_GROUP",
    "MAX_HEATMAP_SESSIONS",
    "MAX_TOP_EXERCISES",
    "SESSION_COMPARATIVE_POINTS",
    "SESSION_UNIT_NOTE",
    "HEATMAP_SCOPE_NOTE",
    "assemble_comparatives",
    "build_muscle_comparatives",
    "OUTSIDE_TEMPLATE_PLAN_LIMITATION",
    "TemplatePlan",
    "build_muscle_drilldown",
    "build_muscle_summary",
    "envelope_quality",
    "item_data_quality",
    "median_interval_hours",
    "metric",
    "resolve_targets",
    "resolve_template_plan",
    "template_plan_cadence_limitation",
    "PlanSlot",
    "template_activation_by_slot",
    "template_plan_weekly_targets",
    "sum_projected_activation_from_pending_slots",
    "weekly_rate",
]
