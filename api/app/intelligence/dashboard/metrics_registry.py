"""Registry de metricas do Novo Dashboard (plano §5, §6, §8.2, §12).

UNICA fonte de:

- `METRIC_VERSIONS`: versao de formula emitida em todo payload
  (`Metric.metric_version` / `DashboardEnvelope.metric_versions`). Mudou a
  formula -> sobe a versao aqui e o frontend/LLM enxergam a troca.
- `THRESHOLDS`: limiares das regras de status/insight (plano §8.2). Sao os
  valores INICIAIS do plano; serao validados contra dados reais antes de
  fixar (F1.6) - por isso vivem num dict, nao espalhados pelos servicos.
- `ExerciseProgressionStatus` + rotulos pt-BR: codigos novos, em ingles, sem
  renomear `PROGREDINDO/...` das rotas v2 (decisao §18).
- `SIBLING_MUSCLE_GROUP_PAIRS`: pares de grupos "irmaos" comparados na regra
  de assimetria (§12). Hoje so costas 6/7. E DADO, nao regra:
  esta em codigo so ate ganhar tabela propria (`muscle_group_pairs`, registrar
  como melhoria M no backlog) - nenhum servico novo deve hardcodar ids alem
  desta constante.

Sem I/O, sem banco: importavel por qualquer Lambda e por testes puros.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping, NamedTuple

from ...domain.e1rm import E1RM_MAX_RELIABLE_REPS
from ...domain.progression_status_labels import EXERCISE_PROGRESSION_LABELS
from ..volume_service import PLANNED_VOLUME_STATUSES, WEEKLY_VOLUME_STATUSES
from .contracts import DataQualityScope, Severity

# ---------------------------------------------------------------------------
# Versoes de formula (plano §6, coluna "Formula (versao)")
# ---------------------------------------------------------------------------

METRIC_VERSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "physical_sets": "sets-v1",
        "effective_sets_stimulus": "effective-sets-v1",
        "effective_sets_fatigue": "fatigue-sets-v1",
        "role_sets": "role-sets-v1",  # direct / indirect / stabilizer
        "weekly_volume_status": "volume-status-v1",
        "muscle_frequency": "freq-v1",
        "exposure": "exposure-v1",
        "e1rm": "epley-rir-v1",
        # v2 (2026-10-06): reps comparadas POR SERIE emparelhada na carga de
        # topo (nao mais a soma - menos series deixou de ser regressao), e1RM
        # simetrico com teto de 12 reps nos dois lados e faixa prescrita
        # avaliada por serie.
        "exercise_progression_status": "progression-status-v2",
        "tonnage": "tonnage-v1",
        # v2 (2026-10-02): o denominador mudou - sessoes esperadas saem de um
        # ciclo de UM DIA POR SLOT (`template_cycle_length_days`), nao mais de
        # 7 x nº de slots. A mesma janela passa a esperar 7x mais sessoes, e um
        # numero v1 (ex.: 525%) nao e comparavel com um v2 (ex.: 75%).
        "adherence": "adherence-v2",
        # recordes/progressoes por SESSAO (F1.1): carga maxima por exercicio
        # canonico comparada com o historico anterior do proprio usuario.
        # v2 (2026-10-06, PR #395): comparacao por chave exercicio +
        # equipamento + convencao de carga, via `compare_exposures` - um
        # numero v1 nao e comparavel com um v2.
        "session_records": "session-records-v2",
        "readiness": "fullbody-no-reps-v1",
        "recovery_signals": "recovery-signals-v1",
        "body_weight_ma7": "weight-ma-v1",
        "waist_cm": "waist-v1",
        "recomposition_signal": "recomp-v1",
        "data_quality": "dq-v1",
        # F1.6 - visao geral: resumo decomponivel da progressao (contagem por
        # status, sem score) e as regras deterministicas de insight (§12).
        "progression_summary": "progression-summary-v1",
        "insight_rules": "insight-rules-v2",
        # F2.1 - eventos de contexto: manuais (`context_events`, migracao 048)
        # UNIDOS aos derivados na leitura (troca de ficha e de academia).
        "context_events": "context-events-v1",
        # F2.2 - check-in diario subjetivo (`daily_checkins`, migracao 050):
        # disposicao, dor global, estresse e humor contra a baseline pessoal.
        # Mesma maquinaria de `recovery_signals`, versao propria porque a
        # FONTE e outra (relato, nao medida).
        "daily_checkin": "daily-checkin-v1",
        # F2.2 - cooldown de alerta (`dashboard_alert_states`, migracao 051):
        # a decisao de MOSTRAR ou SILENCIAR um insight ja visto.
        "alert_cooldown": "alert-cooldown-v1",
        # F2.3 - cardio no hub. Duas metricas com FONTES diferentes, e por
        # isso duas versoes: `cardio_sessions` agrega as atividades de cardio
        # do cache do Intervals.icu (`intervals_activities`), enquanto
        # `session_heart_rate` e a FC POR SESSAO de treino, lida do
        # casamento que o job ja gravou em `activity_intervals_link`
        # (migracao 041). Mudar a janela de tolerancia do casamento muda a
        # segunda e nao a primeira.
        "cardio_sessions": "cardio-sessions-v1",
        "session_heart_rate": "session-hr-v1",
        # M98 C4 - cardio por semana: minutos por zona (`hr_zones`), sessao
        # intensa (`cardio_envelope.is_high_intensity`) e planejado x
        # realizado (`cardio_adherence`). Versoes proprias: mudar o
        # mapeamento de zonas ou o limiar de sessao intensa muda estas, nao
        # o volume de `cardio_sessions`.
        "cardio_weekly_zones": "cardio-weekly-zones-v1",
        # v2 (27/10/2026): entram as fichas de descanso com cardio, casadas
        # pelo dia local inteiro (`intervals_hr_match.match_local_day`).
        "cardio_adherence": "cardio-adherence-v2",
        # F2.5 (M34) - comparativos POR SESSAO do resumo por grupo muscular.
        # Unidade DIFERENTE do resto de `/muscles`, que compara por JANELA:
        # aqui T-0..T-3 sao as ULTIMAS SESSOES em que o grupo apareceu, nao
        # periodos de calendario. A versao e propria justamente porque a
        # unidade e outra - reaproveitar `effective_sets_stimulus` faria a
        # tela declarar a mesma formula para duas leituras incomparaveis.
        "muscle_session_comparative": "session-comparative-v1",
        "muscle_equipment_comparative": "equipment-comparative-v1",
        # Faixa de intensidade do mapa de calor: a CLASSIFICACAO e do backend
        # (§11); o frontend so pinta o que ja veio classificado.
        "muscle_heatmap": "muscle-heatmap-v1",
        # F3.1 - `/dashboard/engine`. Duas leituras com FONTES diferentes, e
        # por isso duas versoes. `generation_audit` e a LEITURA do bloco de
        # auditoria da geracao (`generation_runs.audit_json`, com fallback
        # para `workout_plans.raw_payload.generation_audit`): mudar o que se
        # extrai de `selection`/`safety_clamps`/`substitutions`/`fallbacks`
        # muda ela. `engine_plan_diff` e a COMPARACAO planejado x gerado x
        # executado por item - ela reusa as regras de status do §5
        # (as de `adherence`) e acrescenta a coluna do Modelo, entao declarar
        # `adherence-v*` aqui faria a tela prometer uma formula que nao e a
        # que ela usa.
        "generation_audit": "generation-audit-v1",
        "engine_plan_diff": "engine-diff-v1",
        # Fase 4 (PR C) - avaliacao do Modelo pela metodologia de volume por
        # grupo (EXERCICIOS_GRUPOS / Leandro Twin): exercicios e series
        # DIRETAS por sessao contra a faixa (porte x frequencia). Unidade
        # DIFERENTE de `weekly_volume_status` (series equivalentes ponderadas)
        # - as duas nao se somam, e por isso a versao e propria.
        # Fase 6 (PR 2): v2. As faixas saem do codigo e passam a ser lidas do
        # cadastro de macro-grupos (migração 057, editável na aba admin), e
        # entram o eixo SEMANAL (`weekly_sets_axis`) e o `status` combinado do
        # grupo. Com os valores do seed, o status por sessão é o mesmo da v1.
        # Fase 7: v3. Frequência, média por sessão e eixo semanal medidos numa
        # janela de 7 dias a partir do primeiro slot de treino (sem o fator
        # 7/dias do ciclo): muda o status de ciclos que não têm 7 slots.
        # Fase 5 (PR 5): v4. `direct_sets` soma o equivalente PLANEJADO da
        # tecnica avancada do item (catalogo); Modelo sem tecnica nao muda.
        # Modo de contagem, Lote 2 (073): v5. O payload expoe `counting_mode`
        # (modo gravado na versao do Modelo) e as faixas/metas vem do cadastro
        # POR MODO; em `primary_only` os numeros sao os da v4.
        "methodology_volume": "methodology-volume-v5",
    }
)


def metric_version(metric: str) -> str:
    """Versao registrada de `metric`; `KeyError` com mensagem clara se a
    metrica nao existe (um servico novo esqueceu de registra-la)."""
    try:
        return METRIC_VERSIONS[metric]
    except KeyError as exc:
        raise KeyError(f"metrica nao registrada em METRIC_VERSIONS: {metric!r}") from exc


# ---------------------------------------------------------------------------
# Status de progressao por exercicio (plano §8.2)
# ---------------------------------------------------------------------------


class ExerciseProgressionStatus(str, Enum):
    PROGRESSING = "PROGRESSING"
    STABLE = "STABLE"
    PLATEAU_SUSPECTED = "PLATEAU_SUSPECTED"
    REGRESSING = "REGRESSING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_COMPARABLE = "NOT_COMPARABLE"

    @property
    def label(self) -> str:
        return EXERCISE_PROGRESSION_STATUS_LABELS[self]


# Texto pt-BR (com acento) vem do dono unico `domain/progression_status_labels`
# (M76 Lote 3c, C13) - o mesmo que o briefing e o frontend usam.
EXERCISE_PROGRESSION_STATUS_LABELS: Final[Mapping[ExerciseProgressionStatus, str]] = MappingProxyType(
    {status: EXERCISE_PROGRESSION_LABELS[status.value] for status in ExerciseProgressionStatus}
)


# ---------------------------------------------------------------------------
# Mapa de calor por grupo muscular (plano §11, §14) - F2.5 / M34
# ---------------------------------------------------------------------------
#
# O mapa de calor legado (`/volume-grupamentos`) pintava um gradiente continuo
# no CLIENTE: vermelho -> amarelo -> verde interpolado por volume. Duas coisas
# estao erradas nisso para o hub:
#
# 1. a classificacao vivia no frontend (o backend mandava so o numero), e o
#    plano §11 exige o contrario;
# 2. cor era o UNICO sinal. Um gradiente continuo nao tem rotulo, entao quem
#    nao distingue vermelho de verde nao le nada (§14).
#
# Aqui a intensidade vira uma FAIXA DISCRETA, nomeada, calculada no backend
# pela razao entre a celula e a MAIOR celula do mesmo escopo (a sessao, no
# caso do mapa por sessao). Faixa nomeada tem rotulo; rotulo mais valor mais
# `aria-label` sobrevivem sem cor nenhuma.


class MuscleHeatmapBand(str, Enum):
    """Quanto do estimulo daquele escopo foi para o grupo."""

    NONE = "NONE"  # nenhum estimulo efetivo
    LOW = "LOW"  # ate 25% da maior celula do escopo
    MODERATE = "MODERATE"  # 25% a 50%
    HIGH = "HIGH"  # 50% a 75%
    VERY_HIGH = "VERY_HIGH"  # acima de 75%

    @property
    def label(self) -> str:
        return MUSCLE_HEATMAP_BAND_LABELS[self]


MUSCLE_HEATMAP_BAND_LABELS: Final[Mapping[MuscleHeatmapBand, str]] = MappingProxyType(
    {
        MuscleHeatmapBand.NONE: "Sem estimulo",
        MuscleHeatmapBand.LOW: "Baixo",
        MuscleHeatmapBand.MODERATE: "Moderado",
        MuscleHeatmapBand.HIGH: "Alto",
        MuscleHeatmapBand.VERY_HIGH: "Muito alto",
    }
)

MUSCLE_HEATMAP_BAND_CODES: Final[tuple[str, ...]] = tuple(band.value for band in MuscleHeatmapBand)


def classify_heatmap_band(share: float | None) -> MuscleHeatmapBand:
    """`share` = celula / maior celula do escopo, em [0, 1].

    `None` e valores nao positivos caem em `NONE`: ausencia de estimulo NAO e
    "baixo" - ela e um estado proprio, e a tela precisa poder dizer "este
    grupo nao foi tocado nesta sessao" sem fingir um minimo."""
    if share is None or share <= 0:
        return MuscleHeatmapBand.NONE
    if share <= 0.25:
        return MuscleHeatmapBand.LOW
    if share <= 0.50:
        return MuscleHeatmapBand.MODERATE
    if share <= 0.75:
        return MuscleHeatmapBand.HIGH
    return MuscleHeatmapBand.VERY_HIGH


# ---------------------------------------------------------------------------
# Status DERIVADOS de execucao (plano §5) - F1.1
# ---------------------------------------------------------------------------
#
# Nenhum destes valores existe como coluna: `execution_status` foi DESCARTADO
# no §18 ("dupla verdade"). Eles sao lidos do vinculo
# `exercises.workout_plan_exercise_id` + das series, a cada consulta.


class PlanItemStatus(str, Enum):
    """Status de UM item da ficha dentro de UMA sessao executada."""

    COMPLETED = "COMPLETED"  # series de trabalho >= prescrito
    INTERRUPTED = "INTERRUPTED"  # >= 1 serie de trabalho, porem menos que o prescrito
    SKIPPED = "SKIPPED"  # item da ficha sem nenhuma serie na sessao
    SUBSTITUTED = "SUBSTITUTED"  # vinculo presente, exercicio executado diferente do prescrito
    EXTRA = "EXTRA"  # exercicio executado sem vinculo com item da ficha

    @property
    def label(self) -> str:
        return PLAN_ITEM_STATUS_LABELS[self]


PLAN_ITEM_STATUS_LABELS: Final[Mapping[PlanItemStatus, str]] = MappingProxyType(
    {
        PlanItemStatus.COMPLETED: "Concluido",
        PlanItemStatus.INTERRUPTED: "Interrompido",
        PlanItemStatus.SKIPPED: "Pulado",
        PlanItemStatus.SUBSTITUTED: "Substituido",
        PlanItemStatus.EXTRA: "Extra",
    }
)


class SessionExecutionStatus(str, Enum):
    """Status da SESSAO (`activities.session_status`), normalizado.

    `IMPORTED` e o `NULL` historico: sessao que entrou por importacao, sem
    passar pelo fluxo ao vivo. Ela conta como executada (as series existem),
    mas o campo diz que ninguem apertou "finalizar" - a diferenca aparece em
    `data_quality`, nao num numero silenciosamente diferente."""

    DONE = "DONE"
    IN_PROGRESS = "IN_PROGRESS"
    IMPORTED = "IMPORTED"

    @property
    def label(self) -> str:
        return SESSION_EXECUTION_STATUS_LABELS[self]


SESSION_EXECUTION_STATUS_LABELS: Final[Mapping[SessionExecutionStatus, str]] = MappingProxyType(
    {
        SessionExecutionStatus.DONE: "Concluida",
        SessionExecutionStatus.IN_PROGRESS: "Em andamento",
        SessionExecutionStatus.IMPORTED: "Importada",
    }
)


def normalize_session_status(raw: str | None) -> SessionExecutionStatus:
    """`activities.session_status` -> enum. `NULL`/vazio -> `IMPORTED`;
    qualquer valor desconhecido tambem, para nunca inventar um quarto estado."""
    value = (raw or "").strip().lower()
    if value == "done":
        return SessionExecutionStatus.DONE
    if value == "in_progress":
        return SessionExecutionStatus.IN_PROGRESS
    return SessionExecutionStatus.IMPORTED


# Status de sessao que contam como EXECUTADA nos agregados de aderencia.
# `in_progress` fica de fora: uma sessao que ninguem finalizou nao prova que
# o planejado foi cumprido (mesma leitura da regra de qualidade
# `SESSIONS_WITHOUT_FINAL_STATUS`).
EXECUTED_SESSION_STATUSES: Final[frozenset[SessionExecutionStatus]] = frozenset(
    {SessionExecutionStatus.DONE, SessionExecutionStatus.IMPORTED}
)


# ---------------------------------------------------------------------------
# Status de volume semanal (5 estados) - reexportado de volume_service
# ---------------------------------------------------------------------------

# Mesma tupla de `classify_weekly_volume_status` (nao duplicar os nomes).
WEEKLY_VOLUME_STATUS_CODES: Final[tuple[str, ...]] = WEEKLY_VOLUME_STATUSES

WEEKLY_VOLUME_STATUS_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "BELOW_MIN": "Abaixo do minimo",
        "BETWEEN_MIN_TARGET": "Entre minimo e alvo",
        "ON_TARGET": "No alvo",
        "BETWEEN_TARGET_MAX": "Entre alvo e maximo",
        "ABOVE_MAX": "Acima do maximo",
    }
)

# Status de volume PLANEJADO x realizado (3 estados) - Modelo
# `equipment_series`, `target_source = template_plan`. Mesma tupla de
# `classify_planned_volume_status` (nao duplicar os nomes).
PLANNED_VOLUME_STATUS_CODES: Final[tuple[str, ...]] = PLANNED_VOLUME_STATUSES

PLANNED_VOLUME_STATUS_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "BELOW_TARGET": "Abaixo do planejado",
        "ON_TARGET": "No planejado",
        "ABOVE_TARGET": "Acima do planejado",
    }
)


# ---------------------------------------------------------------------------
# Limiares (plano §8.2 e §12) - valores iniciais, a validar com dados reais
# ---------------------------------------------------------------------------

THRESHOLDS: Final[Mapping[str, float | int]] = MappingProxyType(
    {
        # "mesma carga": |carga_atual - carga_anterior| / carga_anterior <= 2%
        "same_load_tolerance_pct": 0.02,
        # PROGRESSING/REGRESSING por e1RM: variacao >= 3% (reps <= 12)
        "e1rm_progress_pct": 0.03,
        # Teto de confiabilidade do Epley: o e1RM so decide sozinho (para
        # progresso E para regressao) quando a serie que o gerou tem ate
        # 12 reps nas DUAS exposicoes; acima disso valem as reps por serie.
        # Dono: `domain/e1rm.E1RM_MAX_RELIABLE_REPS` (M76, Lote 3a).
        "e1rm_max_reliable_reps": E1RM_MAX_RELIABLE_REPS,
        # PROGRESSING por RPE: mesmo desempenho com RPE medio <= -0.75
        "rpe_improvement_delta": 0.75,
        # < 2 exposicoes comparaveis -> INSUFFICIENT_DATA
        "min_comparable_exposures": 2,
        # >= 3 exposicoes sem progredir nem regredir -> PLATEAU_SUSPECTED
        "plateau_min_exposures": 3,
        # exposicoes consideradas: ultimos 90 dias
        "exposure_lookback_days": 90,
        # recomposicao: peso "estavel" = variacao <= 1% na janela
        "weight_stable_pct": 0.01,
        # recomposicao: cintura caiu >= 1.0 cm
        "waist_drop_cm": 1.0,
        # insight "direto << indireto": razao direto/indireto < 0.3
        "direct_indirect_min_ratio": 0.3,
        # qualidade: sessao `in_progress` vira pendencia depois de 24h (§8.3)
        "dq_in_progress_stale_hours": 24,
        # qualidade: pesagens do mesmo dia divergindo mais de 1 kg (§8.3)
        "dq_weight_divergence_kg": 1.0,
        # qualidade: exemplos por regra na resposta (nunca dump de linha)
        "dq_max_examples": 5,
        # ------------------------------------------------------------------
        # F1.6 - regras de insight (§12). Valores INICIAIS do plano, validados
        # contra dados reais no PR da F1.6 (ver tabela de disparos na
        # descricao do PR). Limiar e decisao de produto: mudar aqui exige
        # decisao do usuario, nunca ajuste silencioso.
        # ------------------------------------------------------------------
        # grupo abaixo do minimo por N SEMANAS consecutivas (uma semana
        # isolada nao conclui). A regua e SEMANAL e independe do ciclo do
        # Modelo: as metas (`min_weekly_sets`) sao semanais, entao comparar
        # "2 ciclos" de 56 dias media 112 dias de treino contra uma meta de
        # 7 (decisao do usuario, 2026-09-23, apos validar o F1.6).
        "insight_below_min_consecutive_weeks": 2,
        "insight_below_min_week_days": 7,
        # quando >= esta fracao dos grupos COM META dispara a regra acima no
        # mesmo periodo, sai UM insight consolidado (pausa/reducao geral de
        # volume) no lugar dos individuais - 16 cartoes iguais sao uma so
        # observacao
        "insight_below_min_collapse_ratio": 0.5,
        # ciclo, quando nao ha Modelo atribuido: metas sao semanais
        "insight_default_cycle_days": 7,
        # exercicio PLATEAU_SUSPECTED so vira insight com >= N exposicoes
        # comparaveis (regra de status exige 3; o insight exige mais)
        "insight_plateau_min_exposures": 4,
        # >= N exercicios REGRESSING (ultima exposicao dentro de
        # `insight_regressing_lookback_days`)
        "insight_regressing_min_exercises": 3,
        "insight_regressing_lookback_days": 28,
        # item de ficha pulado >= N vezes quando esta no fim da sessao
        # (ultimas `insight_end_of_session_positions` posicoes da ficha)
        "insight_skipped_min_count": 3,
        "insight_end_of_session_positions": 2,
        # direto << indireto so e avaliado com >= N series indiretas na janela
        "insight_min_indirect_sets": 6,
        # assimetria entre grupos irmaos: diferenca de atingimento do alvo
        "insight_sibling_asymmetry_min_diff": 0.30,
        # recomposicao: >= N exercicios comparaveis e >= share PROGRESSING/STABLE
        "insight_recomp_min_comparable_exercises": 3,
        "insight_recomp_progress_share": 0.5,
        # recomposicao/peso: a media movel precisa cobrir >= N dias
        "insight_weight_min_span_days": 14,
        # qualidade comprometendo: > ratio das series de trabalho sem mapping
        "insight_dq_unmapped_sets_max_ratio": 0.20,
        "insight_dq_min_sets": 20,
        # frequencia atrasada: sem estimulo direto ha mais de N horas
        "insight_frequency_lag_hours": 168,
        # F2.1 - pausa de treino: N dias civis sem NENHUMA sessao ate o fim da
        # janela. 10 (decisao do usuario, 2026-09-24, ao aprovar a F2.1).
        # Evidencia que embasou a decisao - 10 nao e chute: no dataset real de
        # DEV (usuario 1) ha 48 dias COM treino em 59 dias corridos, ou seja 47
        # intervalos entre dias de treino consecutivos, e o MAIOR deles e de 2
        # dias (36x de 1 dia, 11x de 2 dias, NENHUM >= 3). 10 dias e 5x o maior
        # intervalo ja observado - variacao de rotina nao dispara - e ainda
        # menor que as 2 semanas da regra de volume, que SILENCIA de proposito
        # numa pausa total. Alternativas apresentadas e descartadas pelo
        # usuario: 7 (3,5x o maior intervalo, avisa antes) e 14 (coincide com a
        # regra de volume e chega tarde).
        "insight_training_pause_min_days": 10,
        # ...e so vale como PAUSA se havia rotina antes: >= N dias de treino
        # nos 28 dias que terminam na ultima sessao. 4 (mesma decisao do
        # usuario, 2026-09-24) = uma sessao por semana no mes anterior. Sem
        # esse piso, "voce parou de treinar" seria dito a quem ainda nao
        # comecou.
        "insight_training_pause_min_sessions_before": 4,
        # peso: media movel de N dias; qualidade HIGH com >= N pesagens/semana
        "weight_ma_days": 7,
        "weight_ma_min_weighings_per_week": 4,
        # visao geral: maximo de acoes na home (§7 "maximo 2")
        "overview_max_next_actions": 2,
        # ------------------------------------------------------------------
        # F2.2 - cooldown de alerta (§12, migracao 051).
        #
        # Os cinco numeros abaixo sao DECISAO DO USUARIO, 2026-09-25, ao
        # aprovar a F2.2 (mesmo regime dos limiares 10/4 do insight de pausa e
        # do colapso em 50%): mudar qualquer um deles exige decisao dele, nunca
        # ajuste silencioso.
        #
        # Evidencia que embasou a decisao - nenhum deles e chute. Medicao do
        # dataset real de DEV (usuario 1, oito leituras de
        # `/dashboard/insights` entre 2026-08-14 e 2026-09-25):
        #
        #  - 21 insights na leitura de 2026-09-25, dos quais 19 (90%) sao
        #    `PERFORMANCE_DROP_WITH_CONTEXT_EVENT` de severidade INFO, com as
        #    MESMAS 19 impressoes presentes nas 5 leituras que cobrem
        #    2026-09-04 -> 2026-09-25 (21 dias repetindo sem mudar);
        #  - o `reassess_after` que essas 19 declaram venceu entre 2026-09-06 e
        #    2026-09-10, ou seja, 15 a 19 dias antes de elas continuarem sendo
        #    mostradas. A propria regra ja dizia "reavaliar em ~7 dias" e
        #    ninguem lia esse campo;
        #  - nenhuma das 21 mudou de severidade nas 8 leituras.
        #
        # A duracao do cooldown NAO e um numero solto: e o `reassess_after` que
        # cada regra ja declara, limitado pelo piso da severidade e pelo teto
        # geral. O piso e o unico numero novo, e existe porque `reassess_after`
        # pode ja estar no passado (as 19 acima) - nesse caso o cooldown seria
        # zero e nada mudaria.
        #
        # Piso por severidade (dias):
        #  - INFO 14: associacao de baixa confianca repetida por 3 semanas e o
        #    ruido medido; 14 dias derruba 19 cartoes por leitura para 19 a cada
        #    duas semanas e devolve as 2 vagas de `next_actions` ao que importa.
        #  - ATTENTION 7: e a cadencia que as PROPRIAS regras escolheram - as
        #    duas ATTENTION do dataset (`EXERCISES_REGRESSING`,
        #    `TRAINING_PAUSE_DETECTED`) declaram `reassess_after` = +7 dias.
        #  - ACTION 3: o usuario treina 48 de 59 dias no dataset e o maior
        #    intervalo entre dias de treino e de 2 dias (medicao da F2.1), logo
        #    3 dias garante que um ACTION reaparece a cada ~2 sessoes.
        #
        # Efeito medido dos tres pisos sobre o dataset real, com o servico de
        # verdade: 21 disparos -> 21 visiveis na primeira leitura, 21 na
        # segunda do mesmo dia (mesma ocasiao), 0 no D+1, 2 no D+8 (as duas
        # ATTENTION) e 21 no D+15 (o retorno das INFO, ver o teto abaixo).
        "alert_cooldown_min_days_info": 14,
        "alert_cooldown_min_days_attention": 7,
        "alert_cooldown_min_days_action": 3,
        # Teto: nada fica silencioso por mais de 60 dias. Uma condicao ainda
        # verdadeira depois de dois meses merece ser dita outra vez, mesmo que
        # a regra tenha pedido reavaliacao muito la na frente.
        #
        # Consequencia ACEITA de propria vontade (decisao do usuario,
        # 2026-09-25, ao escolher "mantem o retorno"): com o piso INFO de 14
        # dias, as 19 associacoes medidas VOLTAM a aparecer no D+15 enquanto a
        # condicao continuar verdadeira. A alternativa apresentada e descartada
        # era piso INFO = teto, ou seja, uma associacao de confianca LOW nunca
        # mais voltar enquanto nada mudasse. Ele preferiu que nada fique calado
        # para sempre - isto nao e default acidental.
        "alert_cooldown_max_days": 60,
        # Episodio encerrado: a condicao nao aparece ha N dias -> a proxima
        # ocorrencia e NOTICIA NOVA (primeiro aviso, sem cooldown herdado).
        # 30 > os 21 dias em que as 19 impressoes ficaram continuamente
        # presentes, entao um episodio em curso nao e encerrado por engano.
        "alert_state_resolution_gap_days": 30,
        # ------------------------------------------------------------------
        # Fase 4 (PR C, 2026-09-26) - escalar TRANSVERSAL da metodologia de
        # volume por grupo (EXERCICIOS_GRUPOS / Leandro Twin). As FAIXAS em si
        # NAO ficam aqui: desde a Fase 6 (PR 2) elas moram no cadastro de
        # macro-grupos (migração 057), editável pelo admin, cada célula com a
        # origem e a evidência. As constantes da derivação 3x+ (proposta D2)
        # saíram junto com `_derive_3x_plus`: o seed da 057 guarda o resultado.
        #
        # 3-4 series validas por exercicio: e a UNICA conversao que o
        # documento afirma (EXG-004, "5 exercicios ~ 15 a 20 series"). Segue em
        # uso pela geração por IA (âncora do prompt e o default de séries do
        # validador).
        "methodology_volume_sets_per_exercise_min": 3,
        "methodology_volume_sets_per_exercise_max": 4,
    }
)


def threshold(name: str) -> float | int:
    try:
        return THRESHOLDS[name]
    except KeyError as exc:
        raise KeyError(f"limiar nao registrado em THRESHOLDS: {name!r}") from exc


# ---------------------------------------------------------------------------
# Regras de qualidade de dados (plano §8.3) - catalogo UNICO
# ---------------------------------------------------------------------------
#
# `data_quality_service` implementa a QUERY de cada regra; o que a regra
# significa para quem le (rotulo, frase de impacto, severidade, tela que
# resolve e se ela olha a janela ou o cadastro) mora aqui, junto das versoes e
# limiares - mesmo motivo de `EXERCISE_PROGRESSION_STATUS_LABELS`: o frontend
# recebe `code` + texto pronto e nao mantem uma segunda copia dos rotulos.
#
# `severity` e FIXA por regra, NUNCA derivada da contagem: `SETS_WITHOUT_RPE`
# e informativo com 1 ou com 10.000 series (§8.3) - a contagem muda o tamanho
# do problema, nao a natureza dele.


class DataQualityRule(str, Enum):
    EXERCISES_WITHOUT_CANONICAL_MAPPING = "EXERCISES_WITHOUT_CANONICAL_MAPPING"
    MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION = "MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION"
    SETS_WITH_UNKNOWN_SET_TYPE = "SETS_WITH_UNKNOWN_SET_TYPE"
    EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION = "EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION"
    SETS_WITHOUT_RPE = "SETS_WITHOUT_RPE"
    SESSIONS_WITHOUT_FINAL_STATUS = "SESSIONS_WITHOUT_FINAL_STATUS"
    SESSIONS_NOT_LINKED_TO_PLAN = "SESSIONS_NOT_LINKED_TO_PLAN"
    DUPLICATE_WEIGHT_SAME_DAY = "DUPLICATE_WEIGHT_SAME_DAY"
    ACTIVITIES_WITHOUT_STARTED_AT = "ACTIVITIES_WITHOUT_STARTED_AT"
    MUSCLE_GROUPS_WITHOUT_TARGET = "MUSCLE_GROUPS_WITHOUT_TARGET"


class DataQualityRuleMeta(NamedTuple):
    label: str
    description: str
    severity: Severity
    scope: DataQualityScope
    fix_link: str


DATA_QUALITY_RULES: Final[Mapping[DataQualityRule, DataQualityRuleMeta]] = MappingProxyType(
    {
        DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING: DataQualityRuleMeta(
            label="Exercicios sem vinculo canonico",
            description=(
                "Sem vinculo com o catalogo canonico, estes exercicios nao entram em nenhum agregado de "
                "volume, grupo muscular ou progressao."
            ),
            severity=Severity.ACTION,
            scope=DataQualityScope.WINDOW,
            fix_link="/dominio-exercicios",
        ),
        DataQualityRule.MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION: DataQualityRuleMeta(
            label="Exercicios sem contribuicao muscular",
            description=(
                "Sem contribuicao cadastrada o exercicio nao distribui serie efetiva para nenhum grupo: "
                "o volume do grupo aparece menor do que foi treinado."
            ),
            severity=Severity.ACTION,
            scope=DataQualityScope.CATALOG,
            fix_link="/dominio-exercicios",
        ),
        DataQualityRule.SETS_WITH_UNKNOWN_SET_TYPE: DataQualityRuleMeta(
            label="Series com tipo desconhecido",
            description=(
                "Serie com set_type fora do vocabulario nao e classificada como trabalho nem como "
                "preparatoria, entao pode entrar ou sumir dos agregados sem aviso."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.WINDOW,
            fix_link="/registro-exercicios",
        ),
        DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION: DataQualityRuleMeta(
            label="Pares equipamento/exercicio sem convencao de carga",
            description=(
                "Sem a convencao do par o dashboard assume carga total e pode ler o progresso invertido "
                "(assistido) ou pela metade (por lado/halter)."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.CATALOG,
            fix_link="/equipamentos-exercicios",
        ),
        DataQualityRule.SETS_WITHOUT_RPE: DataQualityRuleMeta(
            label="Series de trabalho sem RPE",
            description=(
                "Sem RPE nao ha RIR, entao o e1RM destas series nao e estimado; carga e repeticoes "
                "continuam sendo lidas normalmente. Excecao: o Desempenho por grupo assume RIR 0 "
                "nelas (Epley puro), para nao perder a serie."
            ),
            severity=Severity.INFO,
            scope=DataQualityScope.WINDOW,
            fix_link="/registro-exercicios",
        ),
        DataQualityRule.SESSIONS_WITHOUT_FINAL_STATUS: DataQualityRuleMeta(
            label="Sessoes sem status final",
            description=(
                "Sessao sem status final pode estar incompleta: conta-la como concluida distorce "
                "aderencia, frequencia e volume da semana."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.WINDOW,
            fix_link="/registro-exercicios",
        ),
        DataQualityRule.SESSIONS_NOT_LINKED_TO_PLAN: DataQualityRuleMeta(
            label="Sessoes sem vinculo com a ficha",
            description=(
                "Sem vinculo com a ficha nao da para dizer o que foi cumprido, pulado ou substituido em "
                "relacao ao que estava planejado."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.WINDOW,
            fix_link="/registro-exercicios",
        ),
        DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY: DataQualityRuleMeta(
            label="Pesagens divergentes no mesmo dia",
            description=(
                "Duas pesagens de fontes diferentes no mesmo dia impedem afirmar qual e o peso do dia e "
                "contaminam a media movel."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.WINDOW,
            fix_link="/evolucao-corporal",
        ),
        DataQualityRule.ACTIVITIES_WITHOUT_STARTED_AT: DataQualityRuleMeta(
            label="Sessoes sem horario de inicio",
            description=(
                "Sem horario de inicio nao da para ordenar as exposicoes dentro do dia nem medir o "
                "intervalo entre estimulos."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.WINDOW,
            fix_link="/registro-exercicios",
        ),
        DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET: DataQualityRuleMeta(
            label="Grupos musculares sem meta",
            description=(
                "Sem meta cadastrada o grupo e comparado contra o fallback generico, entao 'abaixo' ou "
                "'acima' nao reflete o seu objetivo."
            ),
            severity=Severity.ATTENTION,
            scope=DataQualityScope.CATALOG,
            fix_link="/metas-ativacao",
        ),
    }
)


def data_quality_rule_meta(rule: DataQualityRule) -> DataQualityRuleMeta:
    """Metadados de `rule`; `KeyError` claro se uma regra nova nasceu sem
    rotulo/severidade (o servico nunca inventa um texto por conta propria)."""
    try:
        return DATA_QUALITY_RULES[rule]
    except KeyError as exc:
        raise KeyError(f"regra de qualidade sem metadados em DATA_QUALITY_RULES: {rule!r}") from exc


# ---------------------------------------------------------------------------
# Dados que ainda vivem em codigo (a migrar para tabela)
# ---------------------------------------------------------------------------

# Pares (muscle_group_id, muscle_group_id) de grupos "irmaos" para a regra de
# assimetria (§12). (6, 7) = costas superior/inferior. Migrar para
# tabela `muscle_group_pairs` (backlog M) - ate la, este e o UNICO lugar.
SIBLING_MUSCLE_GROUP_PAIRS: Final[tuple[tuple[int, int], ...]] = ((6, 7),)


__all__ = [
    "DATA_QUALITY_RULES",
    "EXECUTED_SESSION_STATUSES",
    "PLAN_ITEM_STATUS_LABELS",
    "PlanItemStatus",
    "SESSION_EXECUTION_STATUS_LABELS",
    "SessionExecutionStatus",
    "normalize_session_status",
    "DataQualityRule",
    "DataQualityRuleMeta",
    "EXERCISE_PROGRESSION_STATUS_LABELS",
    "ExerciseProgressionStatus",
    "METRIC_VERSIONS",
    "MUSCLE_HEATMAP_BAND_CODES",
    "MUSCLE_HEATMAP_BAND_LABELS",
    "MuscleHeatmapBand",
    "classify_heatmap_band",
    "PLANNED_VOLUME_STATUSES",
    "PLANNED_VOLUME_STATUS_CODES",
    "PLANNED_VOLUME_STATUS_LABELS",
    "SIBLING_MUSCLE_GROUP_PAIRS",
    "THRESHOLDS",
    "WEEKLY_VOLUME_STATUSES",
    "WEEKLY_VOLUME_STATUS_CODES",
    "WEEKLY_VOLUME_STATUS_LABELS",
    "data_quality_rule_meta",
    "metric_version",
    "threshold",
]
