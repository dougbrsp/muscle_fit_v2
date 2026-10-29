"""Contratos semanticos do Novo Dashboard (plano §8.4, §10, §12).

Modelos Pydantic v2 de RESPOSTA. Regras nao negociaveis:

- `Metric.value = None` significa "nao calculavel" (dado insuficiente, sem
  RPE, sem meta...). `0` significa "observado zero". Nenhum servico converte
  ausencia em zero - o frontend renderiza `None` como "-".
- Todo valor exibido carrega `nature` (observado/derivado/planejado/projetado/
  estimado/inferido), `metric_version` (do `metrics_registry`) e
  `data_quality`.
- Enums sao `str` para serializar direto em JSON e ficar legiveis nos logs.

Nenhuma rota existente recebe `response_model` neste PR: os primeiros usos
chegam com os endpoints `/analytics/dashboard/*` (F0.2+).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Dono: `domain/data_nature.py` (módulo puro, também usado pelo motor de
# nutrição). Reexportado aqui para a API e os importadores do dashboard.
from ...domain.data_nature import DataNature, DataQuality


class Severity(str, Enum):
    INFO = "INFO"
    ATTENTION = "ATTENTION"
    ACTION = "ACTION"


class DataQualityScope(str, Enum):
    """A regra de qualidade olhou a JANELA ou o CADASTRO? (plano §8.3)

    - `WINDOW`: o item conta ocorrencias dentro de `[date_from, date_to]` -
      mudar o periodo muda a contagem.
    - `CATALOG`: o item e de cadastro (contribuicao muscular, convencao de
      carga do par, meta do grupo). Vale para TODO o historico do usuario e
      IGNORA a janela de proposito: uma lacuna de cadastro nao deixa de
      existir porque o periodo selecionado e curto.
    """

    WINDOW = "window"
    CATALOG = "catalog"


class WindowKind(str, Enum):
    """Janelas suportadas pelo filtro global (plano §10)."""

    CYCLE = "cycle"
    ROLLING_7D = "7d"
    ROLLING_28D = "28d"
    ROLLING_90D = "90d"
    ROLLING_180D = "180d"
    CUSTOM = "custom"


class _Contract(BaseModel):
    """Base comum: campos desconhecidos sao erro (contrato fechado) e enums
    saem como valor (`"OBSERVED"`), nunca como `DataNature.OBSERVED`."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Metric(_Contract):
    """Envelope de um valor exibido (plano §8.4)."""

    value: float | int | None = None
    unit: str | None = None
    nature: DataNature
    metric_version: str
    data_quality: DataQuality = DataQuality.HIGH
    limitations: list[str] = Field(default_factory=list)


class Evidence(_Contract):
    """Um par antes/depois que sustenta um insight (plano §12)."""

    metric: str
    from_value: float | int | None = None
    to_value: float | int | None = None
    unit: str | None = None
    nature: DataNature
    observed_at: date | datetime | None = None


class Comparison(_Contract):
    """Contra o que o insight comparou: base (`previous_exposure`,
    `personal_baseline_28d`...), janela e as chaves que garantem
    comparabilidade (`comparability_key`, `muscle_group_id`...)."""

    basis: str
    window: str
    keys: dict[str, Any] = Field(default_factory=dict)


class InsightCooldownOut(_Contract):
    """Memoria de exibicao deste alerta (`dashboard_alert_states`, migracao
    051; F2.2).

    Ela e CONTRATO, nao detalhe interno: um cartao que desaparece sem
    explicacao e indistinguivel de defeito. Com este bloco a tela pode dizer
    "voce ja viu isso em 04/09, silenciado ate 09/10".

    `reason` e um de `first_seen | new_episode | escalated | cooldown_expired |
    cooldown_active`. Os quatro primeiros aparecem; `cooldown_active` e o unico
    que silencia. `escalated` e o alerta que PIOROU de severidade e por isso
    furou o cooldown."""

    alert_key: str
    reason: str
    suppressed: bool = False
    first_seen_at: datetime | None = None
    last_emitted_at: datetime | None = None
    emitted_count: int = Field(default=0, ge=0)
    suppressed_count: int = Field(default=0, ge=0)
    snoozed_until: datetime | None = None
    metric_version: str


class Insight(_Contract):
    """Contrato de insight/alerta explicavel (plano §12)."""

    code: str
    question: str
    status: str
    severity: Severity
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    comparison: Comparison | None = None
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    reassess_after: date | None = None
    source_screen: str
    fingerprint: str
    # F2.2: ausente quando o cooldown nao esta disponivel (antes da migracao
    # 051 ou com `DASHBOARD_ALERT_COOLDOWN=0`) - `None` e "nao houve decisao de
    # cooldown", nunca "nao silenciado".
    cooldown: InsightCooldownOut | None = None


class WindowInfo(_Contract):
    """Janela resolvida em datas locais do usuario (`time_windows`)."""

    kind: WindowKind
    date_from: date
    date_to: date
    timezone: str
    days: int = Field(ge=1)


class DashboardEnvelope(_Contract):
    """Base de toda resposta `/analytics/dashboard/*`: cada endpoint estende
    com seus blocos. `metric_versions` lista as versoes de formula usadas
    (chave = nome tecnico da metrica, valor = versao do registry)."""

    window: WindowInfo
    metric_versions: dict[str, str] = Field(default_factory=dict)
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# F0.2 - `GET /analytics/dashboard/exercises/{exercise_id}/progress`
# (plano §8, §10). Primeiro endpoint do projeto com `response_model`: o
# contrato deixa de ser "o que o dict devolveu" e passa a ser verificado pelo
# FastAPI a cada resposta.
# ---------------------------------------------------------------------------


class ComparabilityKeyOut(_Contract):
    """`(canonical_exercise_id, equipment_id, load_convention)` (§8.1).
    `equipment_agnostic` marca o unico caso em que comparar sem equipamento e
    permitido (nenhuma exposicao tem equipamento resolvido)."""

    canonical_exercise_id: int | None = None
    equipment_id: int | None = None
    load_convention: str
    equipment_agnostic: bool = False
    # Faixa de fadiga previa da exposicao (dono `domain/prior_fatigue.py`);
    # `None` = contexto nao medido.
    fatigue_context: Literal["rested", "pre_fatigued", "heavily_fatigued"] | None = None


class FatigueContextOut(_Contract):
    """Contexto de fadiga previa de uma exposicao na sessao REAL (dono
    `domain/prior_fatigue.py`): faixa, rotulo pronto, musculo principal e
    series equivalentes previas nele."""

    band: Literal["rested", "pre_fatigued", "heavily_fatigued"]
    band_label: str | None = None
    muscle_group_id: int | None = None
    muscle_group_name: str | None = None
    prior_sets: float = 0.0


class ExerciseSetOut(_Contract):
    """Uma serie `working` como foi registrada (OBSERVED)."""

    set_number: int | None = None
    reps: int | None = None
    weight_kg: float | None = None
    rpe: float | None = None
    rest_seconds: int | None = None
    notes: str | None = None
    # Medidas de serie (Lote T3): `reps`/`duration`/`max_reps` (dono
    # `domain/set_measures.py`), os segundos e o texto pronto ("58 s").
    measure: str | None = None
    duration_seconds: int | None = None
    measure_text: str | None = None


class ExposureOut(_Contract):
    """Uma execucao do exercicio numa sessao (metrica `exposure`).

    `e1rm` e `None` - nunca `0` - quando a serie nao tem RPE ou a convencao
    de carga nao permite estimar 1RM (`assisted`/`bodyweight`)."""

    activity_id: int | None = None
    local_date: date | None = None
    started_at: datetime | None = None
    equipment_id: int | None = None
    equipment_name: str | None = None
    load_convention: str
    load_convention_source: str
    position_in_session: int | None = None
    # Contexto de fadiga previa na sessao real; `None` = nao medido.
    fatigue_context: FatigueContextOut | None = None
    sets: list[ExerciseSetOut] = Field(default_factory=list)
    total_reps: int | None = None
    best_load_kg: float | None = None
    avg_rpe: float | None = None
    e1rm: float | None = None
    tonnage: float | None = None
    comparability_key: ComparabilityKeyOut
    # Reps da serie que gerou o e1RM e se ele e confiavel (ate
    # `e1rm_max_reliable_reps` reps) - o mesmo teto que a regra usa.
    e1rm_reps: int | None = None
    e1rm_reliable: bool = False
    # Lote T3: a exposicao teve serie por tempo/maximo de reps (fora dos
    # agregados acima; o exercicio fica NOT_COMPARABLE).
    has_non_rep_sets: bool = False
    # Comparavel com a chave ATUAL (a da ultima exposicao) e, se nao, por que
    # (`comparability.mismatch_with_current`, M76 Lote 3c): a tela nao refaz
    # `keys_match`.
    comparable_with_current: bool = True
    mismatch_with_current: (
        Literal[
            "load_convention",
            "equipment_unknown",
            "equipment",
            "fatigue_context_changed",
            # M97 F1: as leituras ja chegam filtradas por objetivo (nao emitido)
            "training_goal_changed",
        ]
        | None
    ) = None


class PairedRepsOut(_Contract):
    """Reps por serie emparelhada nas cargas de topo (N = menor numero de
    series de topo dos dois lados) - o que `compare_exposures` compara.

    `previous`/`current`/somas/`delta` = o VALOR comparado: `reps + RIR`
    quando `effort_adjusted` (M78; pode ter meia rep, ex.: RPE 8,5), senao
    reps cruas. `previous_reps`/`current_reps` = reps cruas das mesmas series."""

    pairs: int
    previous: list[int | float] = Field(default_factory=list)
    current: list[int | float] = Field(default_factory=list)
    previous_sum: int | float
    current_sum: int | float
    delta: int | float
    effort_adjusted: bool = False
    previous_reps: list[int] = Field(default_factory=list)
    current_reps: list[int] = Field(default_factory=list)


class LatestComparisonOut(_Contract):
    """As duas ultimas exposicoes com os numeros que a regra usou."""

    comparable: bool
    paired_reps: PairedRepsOut | None = None
    e1rm_decisive: bool = False
    e1rm_max_reliable_reps: int


class ExerciseRecordOut(_Contract):
    """Melhor marca DENTRO da mesma `comparability_key`."""

    value: float | None = None
    activity_id: int | None = None
    local_date: date | None = None
    # So em `best_e1rm` (M76 Lote 3d): `True` = e1RM confiavel (serie com ate
    # 12 reps); `False` = a chave nao tem nenhum confiavel e o valor veio de
    # serie com mais de 12 reps (a tela marca "nao confiavel (>12 reps)").
    reliable: bool | None = None
    # Teto de reps do e1RM confiavel (`domain/e1rm.E1RM_MAX_RELIABLE_REPS`),
    # para o rotulo da tela nao escrever o numero a mao. So em `best_e1rm`.
    max_reliable_reps: int | None = None


class ExerciseRecordsOut(_Contract):
    best_e1rm: ExerciseRecordOut | None = None
    best_load_kg: ExerciseRecordOut | None = None


class ProgressionDecisionOut(_Contract):
    """Decisao do motor de progressao v2 anexada como EVIDENCIA (§8.2):
    informa, nunca sobrepoe a regra de comparabilidade."""

    id: int
    kind: str | None = None
    decision: str | None = None
    phase_before: str | None = None
    phase_after: str | None = None
    engine_version: str | None = None
    created_at: datetime | None = None
    equipment_id: int | None = None


class ExerciseRefOut(_Contract):
    exercise_id: int
    name: str | None = None
    amazfit_name: str | None = None


class E1rmPointOut(_Contract):
    """Ponto da linha UNICA de e1RM (M97 F4): uma exposicao de qualquer
    objetivo, sem deload. `training_goal` e o da execucao (a tela destaca as de
    forca); `same_physical_key` = mesma chave fisica do recorde."""

    activity_id: int | None = None
    local_date: date | None = None
    e1rm: float
    e1rm_reps: int | None = None
    e1rm_reliable: bool = False
    training_goal: str
    same_physical_key: bool = True


class DeloadSessionOut(_Contract):
    """Sessao de deload do exercicio (M97 F4): so a marca no grafico; fica
    fora das exposicoes, do e1RM e do recorde (dono `domain/deload`)."""

    activity_id: int | None = None
    local_date: date | None = None
    deload_source: str
    deload_label: str | None = None


class ExerciseProgressResponse(DashboardEnvelope):
    """Resposta de `/analytics/dashboard/exercises/{exercise_id}/progress`.

    Exercicio existente sem historico do usuario responde 200 com
    `status=INSUFFICIENT_DATA` e `exposures: []` - nunca 404 e nunca zeros."""

    exercise: ExerciseRefOut
    comparability_key: ComparabilityKeyOut | None = None
    status: str
    status_reasons: list[str] = Field(default_factory=list)
    # Contexto de fadiga da exposicao mais recente: o status vale para ele.
    fatigue_context: FatigueContextOut | None = None
    exposures: list[ExposureOut] = Field(default_factory=list)
    latest_comparison: LatestComparisonOut | None = None
    records: ExerciseRecordsOut = Field(default_factory=ExerciseRecordsOut)
    # M97 F4: objetivo da progressao lida (`?goal=`, ausente = default), a
    # linha unica de e1RM (todos os objetivos) e as sessoes de deload marcadas.
    training_goal: str | None = None
    e1rm_series: list[E1rmPointOut] = Field(default_factory=list)
    deload_sessions: list[DeloadSessionOut] = Field(default_factory=list)
    progression_decisions: list[ProgressionDecisionOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# M97 F4 - `GET /analytics/dashboard/strength-panel` (painel de forca)
# ---------------------------------------------------------------------------


class StrengthTopSetOut(_Contract):
    """Top set de UMA sessao de forca: carga de topo x maior numero de reps
    nas series de topo (`exposure_snapshot`)."""

    activity_id: int | None = None
    local_date: date | None = None
    load_kg: float | None = None
    reps: int | None = None
    top_sets_count: int = 0
    avg_rpe: float | None = None
    e1rm: float | None = None
    e1rm_reliable: bool = False


class StrengthLiftOut(_Contract):
    """Um composto principal (regra de selecao do dono `strength_panel`)."""

    exercise_id: int
    name: str | None = None
    strength_sessions: int
    e1rm_series: list[E1rmPointOut] = Field(default_factory=list)
    top_sets: list[StrengthTopSetOut] = Field(default_factory=list)
    best_e1rm: ExerciseRecordOut | None = None


class StrengthPanelResponse(DashboardEnvelope):
    """Resposta de `/analytics/dashboard/strength-panel`. Sem sessao de forca
    na janela: `lifts: []` (a tela esconde o painel)."""

    training_goal: str
    training_goal_label: str
    max_exercises: int
    lifts: list[StrengthLiftOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# F0.3 - `GET /analytics/dashboard/data-quality` (plano §8.3, §10)
# ---------------------------------------------------------------------------


class DataQualityExampleOut(_Contract):
    """Um exemplo do problema, para a pessoa reconhecer o caso - NUNCA um
    dump da linha do banco. `id` e o identificador da entidade que a tela de
    correcao usa (exercicio, sessao, par, grupo); `detail` e um complemento
    curto (data, contagem), sem nota livre nem nada escrito pelo usuario."""

    id: int | None = None
    label: str
    detail: str | None = None


class DataQualityItemOut(_Contract):
    """Uma regra do §8.3 que ENCONTROU ocorrencias.

    `description` diz o que fica impossivel concluir por causa do problema -
    e a razao de o item existir. `severity` vem do registry (fixa por regra,
    nao derivada da contagem)."""

    code: str
    label: str
    description: str
    count: int = Field(ge=0)
    severity: Severity
    scope: DataQualityScope
    fix_link: str
    examples: list[DataQualityExampleOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DataQualityResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/data-quality`.

    `items` traz SO as regras com `count > 0` - lista vazia significa
    "nenhum problema encontrado", nao "nada foi verificado" (`checked_rules`
    diz quantas regras rodaram).

    Dois campos de qualidade, com significados diferentes:

    - `data_quality` (do envelope): confianca NESTA resposta. Cai para
      `MEDIUM` quando alguma regra nao pode ser avaliada (ex.: a coluna
      `load_convention` ainda nao existe no schema).
    - `overall`: o veredito sobre os DADOS do usuario, derivado das
      severidades encontradas - qualquer `ACTION` -> `LOW`; so `ATTENTION` ->
      `MEDIUM`; nada (ou so `INFO`) -> `HIGH`.
    """

    items: list[DataQualityItemOut] = Field(default_factory=list)
    overall: DataQuality
    checked_rules: int = Field(ge=0)


# ---------------------------------------------------------------------------
# F1.2 - `GET /analytics/dashboard/muscles` e `/muscles/{muscle_group_id}`
# (plano §6, §7 "Volume chegou ao musculo certo?", §8.3 do brief original,
# §10). Regra estrutural desta tela: REALIZADO, PLANEJADO e PROJETADO sao
# tres campos SEPARADOS e nunca somados na mesma metrica exibida - o
# `weekly_volume_status` classifica SO o realizado.
# ---------------------------------------------------------------------------


class TargetSource(str, Enum):
    """De onde veio a meta min/target/max do grupo (plano §6).

    - `explicit`: `training_type_id` informado na requisicao;
    - `assigned_template`: Meta de Ativacao do Modelo atribuido ao usuario;
    - `template_plan`: o Modelo atribuido e do tipo `equipment_series`
      (equipamento + series fixas). Esse Modelo NAO tem Meta de Ativacao
      porque a propria especificacao dele e a meta: a ativacao planejada de
      cada slot, normalizada para series efetivas por semana, e o alvo. Sem
      min/max (`null`): um plano fixo nao tem faixa, e inventar +-X% seria
      um numero que ninguem definiu;
    - `fallback_generic`: NENHUMA meta cadastrada - os limiares genericos de
      `volume_service.DASHBOARD_FALLBACK_*`. Neste caso o "abaixo/acima" nao
      reflete objetivo nenhum do usuario, entao a qualidade do item cai.
    """

    EXPLICIT = "explicit"
    ASSIGNED_TEMPLATE = "assigned_template"
    TEMPLATE_PLAN = "template_plan"
    FALLBACK_GENERIC = "fallback_generic"


class MuscleTargetsOut(_Contract):
    """Meta semanal de series efetivas do grupo, e a origem dela.

    Com `source = template_plan`, `min.value` e `max.value` sao `None` (nao
    zero): o status desse item sai de `classify_planned_volume_status` (tres
    estados: `BELOW_TARGET`/`ON_TARGET`/`ABOVE_TARGET`), nao dos cinco de
    `classify_weekly_volume_status`."""

    min: Metric
    target: Metric
    max: Metric
    source: TargetSource
    training_type_id: int | None = None


class MuscleRealizedOut(_Contract):
    """O que de fato aconteceu na janela (OBSERVED/DERIVED).

    `direct`/`indirect`/`stabilizer` sao os tres papeis SEPARADOS
    (`PRIMARY`/`SECONDARY`/`STABILIZER`): fundir estabilizador com secundario
    esconderia que o grupo so participou segurando a carga."""

    physical_sets: Metric
    effective_sets_stimulus: Metric
    effective_sets_fatigue: Metric
    direct_sets: Metric
    indirect_sets: Metric
    stabilizer_sets: Metric
    weekly_effective_sets: Metric
    avg_rpe: Metric


class MusclePlannedOut(_Contract):
    """PLANNED: series efetivas PRESCRITAS nas fichas SALVAS na janela
    (`workout_plan_exercises.sets_count x stimulus_weight`, so series de
    trabalho; `raw_payload.template_slot_targets` para ficha antiga sem
    prescricao aproveitavel). Nunca somado ao realizado numa mesma metrica
    exibida."""

    effective_sets: Metric
    weekly_effective_sets: Metric
    plans_count: int = Field(ge=0)


class MuscleProjectedOut(_Contract):
    """PROJECTED: o que ainda FALTA rodar no ciclo corrente do Modelo
    atribuido (`muscle_summary_service.template_activation_by_slot` nos slots pendentes).

    Unidade deliberadamente diferente do realizado: sao series do CICLO que
    resta, nao uma taxa semanal - normalizar daria a impressao de ser
    comparavel com a meta, e nao e. Nao entra no `status`."""

    remaining_sets: Metric
    pending_slots: int = Field(ge=0)
    cycle_length_days: int | None = None


class MuscleFrequencyOut(_Contract):
    """`freq-v1` (plano §6): quantas sessoes tocaram o grupo DIRETAMENTE na
    janela e a mediana do intervalo (em horas) entre estimulos diretos."""

    direct_sessions: Metric
    participation_sessions: Metric
    median_interval_hours: Metric


class MuscleReadinessOut(_Contract):
    """Prontidao do grupo (`MuscleRecoveryService`, modelo vigente). Sempre
    INFERRED - e um modelo, nao uma medicao.

    `status` e o codigo canonico `READY|REDUCED_DOSE|NO_DIRECT_WORK` (o mesmo
    `readiness_status` de `/recovery-signals`), nunca a cor `green|yellow|red`."""

    status: str | None = None
    guidance: str | None = None
    estimated_recovery_hours: Metric
    hours_since_last_stimulus: Metric
    explanation: list[str] = Field(default_factory=list)


class MuscleTopExerciseOut(_Contract):
    """Exercicio que contribuiu para o grupo, com o PESO da contribuicao.

    `exercise_id` e o id CANONICO (`exercise_catalog`) - o mesmo que
    `/dashboard/exercicios/[id]` espera."""

    exercise_id: int | None = None
    name: str
    sets_count: int = Field(ge=0)
    effective_sets_stimulus: float
    contribution_share: float | None = None
    roles: list[str] = Field(default_factory=list)
    last_local_date: date | None = None


class MuscleItemOut(_Contract):
    """Uma linha de `/analytics/dashboard/muscles`."""

    muscle_group_id: int
    muscle_group: str
    targets: MuscleTargetsOut
    realized: MuscleRealizedOut
    planned: MusclePlannedOut
    projected: MuscleProjectedOut
    frequency: MuscleFrequencyOut
    readiness: MuscleReadinessOut | None = None
    last_stimulus_local_date: date | None = None
    last_direct_local_date: date | None = None
    hours_since_last_stimulus: Metric
    status: str
    status_basis: str = "realized_only"
    top_exercises: list[MuscleTopExerciseOut] = Field(default_factory=list)
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# F2.5 / M34 - comparativos POR SESSAO e mapa de calor.
#
# UNIDADE DIFERENTE do resto de `/muscles`. Todo o resto desta rota compara
# por JANELA (o que aconteceu nos ultimos 28/90/180 dias, normalizado para
# taxa semanal). O que entra aqui compara por SESSAO: T-0 e a ultima sessao em
# que o grupo apareceu, T-1 a anterior, e assim por diante - sem calendario
# nenhum. Um grupo que treinou duas vezes numa semana e nenhuma na seguinte
# tem T-0 e T-1 na MESMA semana; ler esses numeros como "semana passada"
# levaria a decisao errada.
#
# Por isso o bloco e um objeto separado (`comparatives`) com `unit` explicito,
# e nao mais campos dentro de `MuscleItemOut`: misturar as duas leituras na
# mesma linha e exatamente o que a tela legada fazia.
#
# Series preparatorias (aquecimento/ajuste) ficam FORA de tudo aqui, inclusive
# do RPE: a cadeia de JOIN e a mesma `_FROM_REALIZED` do realizado, que ja
# aplica `sql_not_preparatory`. RPE de aquecimento nao mede esforco da serie
# de trabalho - inclui-lo puxaria a media para baixo e faria uma sessao dura
# parecer leve.
# ---------------------------------------------------------------------------


class MuscleSessionPointOut(_Contract):
    """Uma SESSAO do comparativo por sessao. `offset` 0 = T-0 (a mais
    recente em que o grupo apareceu)."""

    offset: int = Field(ge=0)
    activity_id: int
    activity_name: str | None = None
    local_date: date | None = None
    physical_sets: int = Field(ge=0)
    effective_sets_stimulus: float
    tonnage_kg: float | None = None
    # M58: series deste recorte FORA da tonelagem (`assisted`/`bodyweight`).
    # Opcional com default 0: cliente antigo ignora, payload antigo valida.
    tonnage_excluded_sets: int = Field(default=0, ge=0)
    avg_rpe: float | None = None
    has_direct_work: bool = False


class MuscleSessionComparativeOut(_Contract):
    """T-0..T-3 de UM grupo. `sessions_in_window` diz quantas sessoes o grupo
    teve na janela inteira - `points` menor que isso e recorte, nao falta de
    dado."""

    muscle_group_id: int
    muscle_group: str
    points: list[MuscleSessionPointOut] = Field(default_factory=list)
    sessions_in_window: int = Field(ge=0)


class MuscleEquipmentPointOut(_Contract):
    """O mesmo T-n, restrito a UM equipamento. `offset` indexa as sessoes DO
    GRUPO (as mesmas de `MuscleSessionComparativeOut.points`), nao as do
    equipamento: sem isso as colunas de dois equipamentos nao se alinhariam."""

    offset: int = Field(ge=0)
    activity_id: int
    local_date: date | None = None
    physical_sets: int = Field(ge=0)
    effective_sets_stimulus: float
    tonnage_kg: float | None = None
    # M58: series deste recorte FORA da tonelagem (`assisted`/`bodyweight`).
    # Opcional com default 0: cliente antigo ignora, payload antigo valida.
    tonnage_excluded_sets: int = Field(default=0, ge=0)
    avg_rpe: float | None = None


class MuscleEquipmentRowOut(_Contract):
    """Um equipamento dentro de um grupo. `equipment_name` e `None` quando a
    sessao nao registrou equipamento - a tela diz isso, nunca inventa um
    "Sem equipamento" que pareca um aparelho de verdade."""

    equipment_name: str | None = None
    points: list[MuscleEquipmentPointOut] = Field(default_factory=list)
    effective_sets_stimulus: float
    share_of_group: float | None = None


class MuscleEquipmentComparativeOut(_Contract):
    """Equipamentos de UM grupo, do que mais contribuiu para o que menos."""

    muscle_group_id: int
    muscle_group: str
    equipments: list[MuscleEquipmentRowOut] = Field(default_factory=list)
    equipment_limit: int = Field(ge=1)
    equipment_truncated: bool = False


class MuscleHeatmapCellOut(_Contract):
    """Uma celula do mapa de calor: quanto de estimulo UM grupo recebeu num
    escopo. `band` ja vem CLASSIFICADA (§11) e `share_of_max` e a razao que
    gerou a faixa - a tela exibe rotulo + valor, nunca so a cor (§14)."""

    muscle_group_id: int
    muscle_group: str
    physical_sets: int = Field(ge=0)
    effective_sets_stimulus: float
    tonnage_kg: float | None = None
    # M58: series deste recorte FORA da tonelagem (`assisted`/`bodyweight`).
    # Opcional com default 0: cliente antigo ignora, payload antigo valida.
    tonnage_excluded_sets: int = Field(default=0, ge=0)
    avg_rpe: float | None = None
    share_of_max: float | None = None
    band: str


class MuscleHeatmapSessionOut(_Contract):
    """Mapa de calor de UMA sessao. A normalizacao (`share_of_max`) e SEMPRE
    interna a sessao: comparar celulas de sessoes diferentes pela cor seria
    comparar escalas distintas."""

    activity_id: int
    activity_name: str | None = None
    local_date: date | None = None
    max_effective_sets_stimulus: float
    cells: list[MuscleHeatmapCellOut] = Field(default_factory=list)


class MuscleComparativesOut(_Contract):
    """Bloco por SESSAO de `/analytics/dashboard/muscles` (M34).

    `unit` existe para a tela poder rotular a leitura sem adivinhar; hoje o
    unico valor e `"session"`, e ele e verificado por teste justamente para
    um eventual bloco por janela nao entrar aqui por engano."""

    unit: Literal["session"] = "session"
    sessions_compared: int = Field(ge=0)
    by_session: list[MuscleSessionComparativeOut] = Field(default_factory=list)
    by_equipment: list[MuscleEquipmentComparativeOut] = Field(default_factory=list)
    heatmap: list[MuscleHeatmapSessionOut] = Field(default_factory=list)
    data_quality: DataQuality = DataQuality.HIGH
    limitations: list[str] = Field(default_factory=list)


class MusclesResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/muscles`."""

    items: list[MuscleItemOut] = Field(default_factory=list)
    # `None` (nao lista vazia) quando `include_comparatives=false`: "nao foi
    # pedido" e diferente de "foi pedido e nao ha nada".
    comparatives: MuscleComparativesOut | None = None
    target_source: TargetSource
    training_type_id: int | None = None
    # Preenchidos so quando `target_source = template_plan`: qual Modelo
    # `equipment_series` forneceu a meta.
    template_id: int | None = None
    template_name: str | None = None
    unattributed_sets: int = Field(default=0, ge=0)


class MuscleSourceSetOut(_Contract):
    """A SERIE de origem do estimulo (OBSERVED) - o fim do drill-down."""

    set_id: int
    set_number: int | None = None
    set_type: str
    reps: int | None = None
    weight_kg: float | None = None
    rpe: float | None = None
    effective_sets_stimulus: float
    effective_sets_fatigue: float
    role: str


class MuscleExposureOut(_Contract):
    """Uma execucao de UM exercicio dentro de UMA sessao que gerou estimulo
    para o grupo, com as series de origem."""

    exercise_row_id: int
    exercise_id: int | None = None
    name: str
    position_in_session: int | None = None
    role: str
    effective_sets_stimulus: float
    sets: list[MuscleSourceSetOut] = Field(default_factory=list)


class MuscleSessionOut(_Contract):
    """Uma sessao que gerou estimulo para o grupo."""

    activity_id: int
    activity_name: str | None = None
    local_date: date | None = None
    started_at: datetime | None = None
    physical_sets: int = Field(ge=0)
    effective_sets_stimulus: float
    has_direct_work: bool
    exposures: list[MuscleExposureOut] = Field(default_factory=list)


class MuscleDrilldownResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/muscles/{muscle_group_id}`.

    `sets_limit`/`sets_truncated` sao contrato, nao detalhe interno: a
    resposta da RDS Data API tem teto de 1 MB (§15), entao o drill-down corta
    pelas sessoes MAIS RECENTES e DECLARA o corte - a tela precisa poder
    dizer "faltam sessoes mais antigas" em vez de mostrar um total errado."""

    item: MuscleItemOut
    sessions: list[MuscleSessionOut] = Field(default_factory=list)
    sets_limit: int = Field(ge=1)
    sets_truncated: bool = False
    target_source: TargetSource
    training_type_id: int | None = None
    template_id: int | None = None
    template_name: str | None = None


# ---------------------------------------------------------------------------
# F1.1 - `GET /analytics/dashboard/workouts`, `/workouts/{activity_id}` e
# `/adherence` (plano §5, §6 `adherence-v2`, §7 "Cumpri o planejado?" e
# "Frequencia coerente com o ciclo?", §10, §11).
#
# Regra estrutural desta tela: o status de cada item da ficha e DERIVADO
# (§5) - nao existe coluna `execution_status` nem migracao. O que o banco
# guarda e o vinculo (`exercises.workout_plan_exercise_id`) e as series; o
# resto e leitura.
# ---------------------------------------------------------------------------


class PrescriptionSource(str, Enum):
    """De onde saiu o PRESCRITO de um item da ficha.

    - `frozen_snapshot`: `workout_plan_exercises.prescription_json` (migracao
      037) - o que o motor congelou quando a ficha nasceu;
    - `plan_columns`: as colunas da linha (`sets_count`, `reps_target`...),
      caminho das fichas LEGADAS, anteriores ao congelamento;
    - `unavailable`: nem uma coisa nem outra - sem prescrito nao da para
      dizer se o item foi cumprido ou interrompido.
    """

    FROZEN_SNAPSHOT = "frozen_snapshot"
    PLAN_COLUMNS = "plan_columns"
    UNAVAILABLE = "unavailable"


class PlanItemSubstitutionOut(_Contract):
    """Troca observada num item: a ficha pedia um exercicio e a sessao
    registrou outro (`exercises.mapping_id` != `workout_plan_exercises.mapping_id`)."""

    planned_exercise_id: int | None = None
    planned_name: str | None = None
    performed_exercise_id: int | None = None
    performed_name: str | None = None


class PrescribedOut(_Contract):
    """O que a ficha pedia para o item, com a fonte declarada."""

    source: PrescriptionSource
    sets_count: int | None = None
    reps_target: str | None = None
    load_target: str | None = None
    rpe_target: str | None = None
    rest_seconds: int | None = None
    engine_version: str | None = None


class PerformedSetOut(_Contract):
    """Serie de origem do item (OBSERVED) - o fim do drill-down da sessao."""

    set_id: int
    set_number: int | None = None
    set_type: str
    is_working: bool
    reps: int | None = None
    weight_kg: float | None = None
    rpe: float | None = None
    rest_seconds: int | None = None


class PerformedOut(_Contract):
    """O que de fato foi registrado para o item."""

    exercise_row_id: int | None = None
    exercise_id: int | None = None
    name: str | None = None
    position_in_session: int | None = None
    # Series de TRABALHO (sem aquecimento/ajuste e, desde a Fase 5 PR 4, sem
    # aplicacao de tecnica) - a base da completude do item.
    working_sets: int = Field(default=0, ge=0)
    # Linhas de aplicacao de tecnica avancada (mini-bloco, drop, reps alem da
    # falha) registradas no item - o X de "tecnica aplicada X/N".
    technique_sets: int = Field(default=0, ge=0)
    total_sets: int = Field(default=0, ge=0)
    total_reps: int | None = None
    best_load_kg: float | None = None
    avg_rpe: float | None = None
    sets: list[PerformedSetOut] = Field(default_factory=list)


class PlanItemTechniqueOut(_Contract):
    """"Tecnica aplicada X/N" de um item (Fase 5, PR 4) - indicador PROPRIO,
    fora da completude (que conta so series de trabalho).

    `planned_applications` (N) so existe para tecnica de APLICACAO com
    quantidade na ficha; FST-7/GVT so preenchem series. `equivalent_sets` e a
    soma das fatias do bloco que entrou no volume do grupo."""

    code: str | None = None
    planned_applications: int | None = None
    performed_applications: int = Field(default=0, ge=0)
    equivalent_sets: float | None = None


class PlanItemOut(_Contract):
    """Um item da ficha (ou um exercicio EXTRA) dentro de uma sessao, com o
    status DERIVADO (§5).

    `missing_sets` so existe para `INTERRUPTED`: e quanto faltou para o
    prescrito - `None` quando nao ha prescrito para comparar (nunca `0`,
    que diria "nao faltou nada")."""

    status: str
    plan_exercise_id: int | None = None
    plan_sequence: int | None = None
    planned_name: str | None = None
    prescribed: PrescribedOut
    performed: PerformedOut | None = None
    missing_sets: int | None = None
    technique: PlanItemTechniqueOut | None = None
    substitution: PlanItemSubstitutionOut | None = None
    is_record: bool = False
    notes: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ItemStatusCountsOut(_Contract):
    """Contagem de itens por status derivado dentro de UMA sessao."""

    completed: int = Field(default=0, ge=0)
    interrupted: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    substituted: int = Field(default=0, ge=0)
    extra: int = Field(default=0, ge=0)


class PlanLinkOut(_Contract):
    """Ligacao REAL entre a sessao e a ficha/slot que a originou - o que
    substitui o agrupamento por string de nome da tela v2."""

    workout_plan_id: int | None = None
    plan_name: str | None = None
    template_id: int | None = None
    template_slot_sequence: int | None = None
    template_slot_name: str | None = None


class SessionGroupOut(_Contract):
    """Grupo da sessao em `/dashboard/treinos`, pronto do dono
    (`slot_session_comparison.session_group_of`): a chave e Modelo + posicao
    do slot; `label`/`template_name` sao so rotulo."""

    key: str
    kind: str
    label: str
    template_id: int | None = None
    template_name: str | None = None
    # Raiz da linhagem do Modelo (copia -> origem, 090): a chave usa ela.
    lineage_template_id: int | None = None
    slot_sequence: int | None = None


class WorkoutSessionOut(_Contract):
    """Uma sessao executada na janela."""

    activity_id: int
    local_date: date | None = None
    started_at: datetime | None = None
    name: str | None = None
    session_status: str
    # M97 F4: origem do deload da sessao (`period`/`manual`; None = normal) e o
    # rotulo pronto do dono `domain/deload`.
    deload_source: str | None = None
    deload_label: str | None = None
    duration_seconds: int | None = None
    exercises_count: int = Field(default=0, ge=0)
    physical_sets: Metric
    effective_sets_stimulus: Metric
    effective_sets_fatigue: Metric
    avg_rpe: Metric
    records: Metric
    progressions: Metric
    item_status_counts: ItemStatusCountsOut
    plan_link: PlanLinkOut | None = None
    group: SessionGroupOut
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


class WorkoutsResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/workouts` (paginada)."""

    items: list[WorkoutSessionOut] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool = False


class WorkoutDetailResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/workouts/{activity_id}`.

    `sets_limit`/`sets_truncated` sao contrato (teto de 1 MB da Data API,
    §15): o corte fica nos itens do FIM da sessao e e declarado."""

    session: WorkoutSessionOut
    items: list[PlanItemOut] = Field(default_factory=list)
    sets_limit: int = Field(ge=1)
    sets_truncated: bool = False
    notes: str | None = None


class CycleSlotOut(_Contract):
    """Um slot do Modelo atribuido na posicao do ciclo corrente.

    `status` usa o MESMO vocabulario de `/analytics/dashboard-cycle`
    (`done|next|pending`) - a tela ja sabe le-lo e um segundo enum para o
    mesmo estado so criaria divergencia."""

    sequence: int
    name: str | None = None
    slot_type: str | None = None
    weekday: int | None = None
    status: str
    last_activity_id: int | None = None
    last_local_date: date | None = None


class AdherenceResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/adherence` - o bloco
    "Consistencia" (§7, F1.6).

    Sem Modelo atribuido a resposta EXISTE, mas `rate` vem `null` com
    `data_quality=LOW`: nao ha denominador honesto a inventar (§6)."""

    has_template: bool
    template_id: int | None = None
    template_name: str | None = None
    cycle_length_days: int | None = None
    slots: list[CycleSlotOut] = Field(default_factory=list)
    next_slot: CycleSlotOut | None = None
    expected_sessions: Metric
    completed_sessions: Metric
    planned_sessions: Metric
    unplanned_sessions: Metric
    missed_sessions: Metric
    rate: Metric
    cycle_rate: Metric


# ---------------------------------------------------------------------------
# F1.5 - `GET /analytics/dashboard/recovery-signals` (plano §6 `readiness` e
# `recovery-signals-v1`, §7 "Estou recuperando bem?", §10, §11).
#
# Duas leituras LADO A LADO, nunca fundidas num numero so:
#
# - PRONTIDAO por grupo: o modelo `fullbody-no-reps-v1`
#   (`MuscleRecoveryService`), o mesmo que a geracao de ficha e o Modo Treino
#   usam. E ele - e so ele - que decide `READY/REDUCED_DOSE/NO_DIRECT_WORK`.
# - SINAIS objetivos contra a BASELINE PESSOAL: sono, HRV, FC de repouso,
#   TSB, dor e desempenho recente. Eles nao reclassificam prontidao nenhuma;
#   eles CONCORDAM ou DIVERGEM dela, e a tela mostra os dois casos.
#
# Regra estrutural: nenhum sinal isolado decreta "recuperacao ruim". Sem
# baseline suficiente o sinal diz "sem baseline", nunca um numero inventado.
# ---------------------------------------------------------------------------


class SignalDirection(str, Enum):
    """Onde o valor da janela caiu em relacao a baseline pessoal."""

    ABOVE = "ABOVE"
    IN_LINE = "IN_LINE"
    BELOW = "BELOW"
    UNKNOWN = "UNKNOWN"  # sem valor ou sem baseline


class SignalReading(str, Enum):
    """O que a DIRECAO significa para recuperacao - depende do sinal (HRV
    baixo e ruim, FC de repouso baixa e boa)."""

    FAVORABLE = "FAVORABLE"
    NEUTRAL = "NEUTRAL"
    UNFAVORABLE = "UNFAVORABLE"
    UNKNOWN = "UNKNOWN"


class SignalAgreement(str, Enum):
    """Relacao do sinal com a leitura de PRONTIDAO do modelo."""

    AGREES = "AGREES"
    DIVERGES = "DIVERGES"
    NEUTRAL = "NEUTRAL"
    NO_BASELINE = "NO_BASELINE"


class ReadinessReading(str, Enum):
    """Resumo da prontidao do modelo, so para dizer com o que os sinais
    concordam ou divergem."""

    RECOVERED = "RECOVERED"
    STRAINED = "STRAINED"
    UNKNOWN = "UNKNOWN"


class RecoverySignalOut(_Contract):
    """Um sinal objetivo comparado com a propria baseline da pessoa.

    `baseline.value = None` + `agreement = NO_BASELINE` e o estado honesto de
    "ainda nao da para comparar" (menos de `min_observations` pontos na
    janela de referencia). Nunca se preenche a baseline com a media geral de
    ninguem."""

    code: str
    label: str
    description: str
    current: Metric
    baseline: Metric
    delta: Metric
    direction: SignalDirection
    reading: SignalReading
    agreement: SignalAgreement
    observations: int = Field(ge=0)
    baseline_observations: int = Field(ge=0)
    min_observations: int = Field(ge=1)
    source: str
    limitations: list[str] = Field(default_factory=list)
    # F2.2: sinais do MESMO grupo contam UMA vez na concordancia (§7, "nenhum
    # sinal isolado decreta recuperacao ruim"). Os quatro escores do check-in
    # diario vem do mesmo formulario, respondido de uma vez: num dia ruim a
    # pessoa marca os quatro, e conta-los como quatro observacoes
    # independentes faria um unico check-in ruim elevar a leitura para
    # ATTENTION. Sinal sem grupo declarado forma grupo com ele mesmo (o
    # comportamento anterior, para os sinais objetivos nao mudarem de numero).
    group: str | None = None
    subjective: bool = False


class ContextEventOut(_Contract):
    """Evento de contexto (alcool, viagem, doenca, troca de ficha...).

    Duas origens no MESMO formato (F2.1): `source='manual'` e uma linha de
    `context_events` (migracao 048) que o usuario registrou e pode editar;
    `source='derived'` e calculado NA LEITURA a partir do historico (troca de
    ficha, troca de academia) - por isso ele nao tem `id` e vem com
    `editable=False`. A tela nao precisa de dois caminhos de renderizacao.

    `intensity` e `null` quando o evento nao foi graduado - nunca 0: "sem
    gradacao" e ausencia, nao o menor grau possivel."""

    id: int | None = None
    event_type: str
    event_type_label: str | None = None
    local_date: date
    end_local_date: date | None = None
    intensity: int | None = None
    notes: str | None = None
    source: str = "manual"
    derived: bool = False
    editable: bool = True


class RecoveryAssessmentOut(_Contract):
    """Leitura COMBINADA, com a incerteza explicita.

    `level` so chega a `ACTION` quando o MODELO classifica algum grupo como
    `NO_DIRECT_WORK` - sinal objetivo nenhum, sozinho ou em conjunto, promove
    a esse nivel. Dois ou mais sinais negativos concordantes elevam para
    `ATTENTION`."""

    level: Severity
    readiness_reading: ReadinessReading
    headline: str
    uncertainty: DataQuality
    allow_intensification: bool
    intensification_blocked_reason: str | None = None
    agreeing_codes: list[str] = Field(default_factory=list)
    diverging_codes: list[str] = Field(default_factory=list)
    unavailable_codes: list[str] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    reassess_after_hours: float | None = None
    context_events: list[ContextEventOut] = Field(default_factory=list)


class RecoveryRecommendationOut(_Contract):
    """Recomendacao de series do MODELO `fullbody-no-reps-v1` (M33).

    E a MESMA tabela (`MuscleRecoveryService._RECOMMENDATIONS`) que governa a
    geracao de ficha e o Modo Treino. A tela nao escolhe nem arredonda numero
    de serie nenhum; ate a F2.5 este bloco so existia em
    `/recuperacao-muscular`."""

    direct_sets_min: int | None = None
    direct_sets_max: int | None = None
    maximum_rpe: int | None = None
    allow_failure_on_last_set: bool | None = None
    allow_progression: bool | None = None
    allow_indirect_work: bool | None = None
    volume_multiplier: float | None = None
    # M76 B2: dose reduzida = `ceil(S × multiplicador)` por exercício, com piso
    # `minimum_sets`; `sets_rule` é o texto da regra gerado pelo modelo.
    minimum_sets: int | None = None
    sets_rule: str | None = None


class RecoveryFactorSetOut(_Contract):
    """UMA serie recente do grupo, com os fatores que a transformaram em
    dose. Explicabilidade pura: a tela mostra a conta, nao a refaz."""

    trained_at: datetime | None = None
    reps: int | None = None
    rpe: float | None = None
    set_type: str
    role: str
    participation: float | None = None
    effort_points: float | None = None
    repetition_factor: float | None = None
    set_type_factor: float | None = None


class RecoveryFactorsOut(_Contract):
    """De ONDE saiu a prontidao do grupo (M33).

    Todos os campos sao repassados de `VolumeService`/`MuscleRecoveryService`
    - nenhum e recalculado aqui. `None` e "nao da para calcular", nunca zero:
    um grupo sem serie com RPE tem `avg_rpe_used = None`, e isso e diferente
    de RPE 0."""

    session_dose: float | None = None
    dose_last_72h: float | None = None
    avg_rpe_used: float | None = None
    avg_rpe_window: float | None = None
    density_penalty: float | None = None
    signal_penalty: float | None = None
    performance_ratio: float | None = None
    recent_sets: list[RecoveryFactorSetOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class GlobalReadinessAdjustmentOut(_Contract):
    """Ajuste global de fadiga (TSB do Intervals.icu) que o modelo APLICOU no
    tempo estimado de recuperacao de todos os grupos (M33).

    O campo inteiro e `None` quando nao ha Intervals.icu sincronizado - e
    deliberado nao devolver `factor = 1.0` nesse caso: "nao houve ajuste
    porque nao ha dado" e diferente de "o ajuste calculado deu neutro"."""

    factor: float
    form_tsb: float | None = None
    wellness_date: date | None = None
    # M52: `True` quando o ultimo wellness passou da idade maxima - o fator
    # aplicado foi neutro (1.0) e `form_tsb` e o valor IGNORADO. A tela usa a
    # flag para o aviso de dado desatualizado.
    stale: bool = False
    max_age_days: int | None = Field(default=None, ge=0)
    description: str


class TrainingVsRestMetricOut(_Contract):
    """UMA metrica recortada por dia COM treino x dia SEM treino (M36).

    O lado de DESCANSO faz o papel de referencia, e `direction`/`reading`
    usam a mesma tolerancia e a mesma leitura dos sinais - nao ha um segundo
    criterio de "diferente" no projeto. Lado sem medida fica `None`, nunca
    zero, e a limitacao diz de quantos dias cada lado e feito."""

    code: str
    label: str
    unit: str
    source: str
    higher_is_better: bool
    training_days: Metric
    rest_days: Metric
    delta: Metric
    direction: SignalDirection
    reading: SignalReading
    training_observations: int = Field(ge=0)
    rest_observations: int = Field(ge=0)
    min_days: int = Field(ge=1)
    limitations: list[str] = Field(default_factory=list)


class TrainingVsRestOut(_Contract):
    """Sono, HRV e FC de repouso em dia com treino x dia sem treino (M36).

    Fica FORA de `signals` de proposito: e um recorte DESCRITIVO e nao entra
    em `assessment.level`, em `allow_intensification` nem em nenhuma contagem
    de concordancia. Dois grupos observados nao sao um experimento - a
    limitacao de associacao e sempre declarada."""

    training_days: int = Field(ge=0)
    rest_days: int = Field(ge=0)
    min_days: int = Field(ge=1)
    metrics: list[TrainingVsRestMetricOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RecoveryGroupOut(_Contract):
    """Prontidao de UM grupo muscular, como o modelo devolve (INFERRED).

    `explanation` e a mesma lista que `MuscleRecoveryService` ja monta - a
    tela nao reescreve o motivo, so mostra."""

    muscle_group_id: int | None = None
    muscle_group: str
    readiness_status: str
    guidance: str | None = None
    recovery_pct: Metric
    estimated_recovery_hours: Metric
    remaining_hours: Metric
    hours_since_last_stimulus: Metric
    session_dose: Metric
    last_session_at: datetime | None = None
    last_trained_local_date: date | None = None
    # "Bom para" (2026-10-17): primeiro instante, COM o fuso do usuario, em
    # que a projecao do modelo (`recovery_projection.ready_forecast`) deixa o
    # grupo READY. `None` + `ready_label` = ja esta pronto; `None` sem rotulo
    # = sem previsao (sinal que so uma nova sessao do grupo substitui). A tela
    # so formata (dia da semana, data e hora no fuso de `window.timezone`).
    # `AwareDatetime`: sem fuso e erro de contrato (os outros instantes do
    # payload sao naive, na parede do usuario).
    ready_at: AwareDatetime | None = None
    ready_label: str | None = None
    max_rpe: int | None = None
    allow_failure: bool | None = None
    volume_multiplier: float | None = None
    blocking_signal_codes: list[str] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    # M33: o detalhamento que so existia em `/recuperacao-muscular`.
    recommendation: RecoveryRecommendationOut | None = None
    factors: RecoveryFactorsOut | None = None


class RecoverySignalsResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/recovery-signals`.

    Sempre 200: sem wearable, sem sono registrado e sem sessao na janela a
    resposta existe com `groups`/`signals` do que houver e
    `data_quality=INSUFFICIENT` - ausencia de sinal NUNCA zera a prontidao,
    que vem de outra fonte (as series registradas)."""

    groups: list[RecoveryGroupOut] = Field(default_factory=list)
    signals: list[RecoverySignalOut] = Field(default_factory=list)
    assessment: RecoveryAssessmentOut
    # M36 / M33: recorte por dia de treino e o ajuste global que o modelo
    # aplicou. Os dois nasceram para fechar a paridade com
    # `/recuperacao-muscular` e `/treinos` (F2.5).
    training_vs_rest: TrainingVsRestOut
    global_readiness_adjustment: GlobalReadinessAdjustmentOut | None = None
    baseline_window_days: int = Field(ge=1)
    readiness_model: str


# ---------------------------------------------------------------------------
# F1.6 - `GET /analytics/dashboard/overview` e `/insights` (plano §7, §8.1 do
# brief, §10, §11, §12).
#
# Regras estruturais da visao geral:
#
# - NENHUM score unico opaco. O resumo de progressao e a CONTAGEM por status
#   (decomponivel: cada contagem lista os exercicios que a compoem).
# - Cada bloco responde UMA pergunta (`question` + `answer` em uma frase),
#   aponta o drill-down (`drilldown_href`) e carrega a PROPRIA
#   `data_quality`/`limitations` - a visao geral nunca esconde a ressalva
#   de um bloco atras da confianca de outro.
# - `next_actions` traz no maximo `overview_max_next_actions` (2) insights;
#   o restante fica so em `/insights`. Nada muda automaticamente: cada
#   `Insight` e uma sugestao com evidencia, comparacao, confianca,
#   explicacoes alternativas e data de reavaliacao.
# ---------------------------------------------------------------------------


class OverviewBlockBase(_Contract):
    """O que TODO bloco da visao geral carrega."""

    question: str
    answer: str
    drilldown_href: str
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


class ConsistencyBlockOut(OverviewBlockBase):
    """"Cumpri o planejado?" <- `adherence_service` (ciclo corrente + periodo)."""

    has_template: bool
    template_name: str | None = None
    cycle_length_days: int | None = None
    next_slot: CycleSlotOut | None = None
    cycle_slots_done: int = Field(default=0, ge=0)
    cycle_slots_total: int = Field(default=0, ge=0)
    expected_sessions: Metric
    completed_sessions: Metric
    planned_sessions: Metric
    unplanned_sessions: Metric
    missed_sessions: Metric
    rate: Metric
    cycle_rate: Metric


class ProgressionExerciseOut(_Contract):
    """Um exercicio dentro de uma contagem por status. `exercise_id` e o id
    CANONICO - o mesmo que `/dashboard/exercicios/[id]` espera."""

    exercise_id: int
    name: str | None = None
    status: str
    status_reasons: list[str] = Field(default_factory=list)
    # Contexto de fadiga em que o status vale (o da exposicao mais recente).
    fatigue_context: FatigueContextOut | None = None
    comparable_exposures: int = Field(ge=0)
    exposures_in_window: int = Field(ge=0)
    last_local_date: date | None = None


class ProgressionStatusCountOut(_Contract):
    """Uma barra do resumo: status, rotulo, quantos exercicios e QUAIS (todos)
    - e isso que torna a contagem decomponivel."""

    status: str
    label: str
    count: int = Field(ge=0)
    exercises: list[ProgressionExerciseOut] = Field(default_factory=list)


class ProgressionBlockOut(OverviewBlockBase):
    """"Estou evoluindo?" <- contagem por `ExerciseProgressionStatus`, sem score.

    `exposures_per_exercise` e `truncated` sao contrato: o caminho em lote le
    as ultimas N exposicoes de CADA exercicio numa unica consulta (teto de 1 MB
    da Data API, §15) e declara o corte."""

    exercises_total: int = Field(ge=0)
    comparable_total: int = Field(ge=0)
    counts: list[ProgressionStatusCountOut] = Field(default_factory=list)
    exposures_per_exercise: int = Field(ge=1)
    truncated: bool = False


class PriorityMuscleOut(_Contract):
    """Um grupo que merece atencao, com os numeros que sustentam a leitura."""

    muscle_group_id: int
    muscle_group: str
    status: str
    weekly_effective_sets: Metric
    target_min: Metric
    target_max: Metric
    target_source: TargetSource
    direct_sets: Metric
    indirect_sets: Metric
    direct_sessions: Metric
    hours_since_last_stimulus: Metric
    last_direct_local_date: date | None = None


class PriorityMusclesBlockOut(OverviewBlockBase):
    """"Quais grupos estao atrasados ou em excesso?" <- `muscle_summary_service`."""

    groups_total: int = Field(ge=0)
    groups_with_target: int = Field(ge=0)
    target_source: TargetSource
    below_min: list[PriorityMuscleOut] = Field(default_factory=list)
    above_max: list[PriorityMuscleOut] = Field(default_factory=list)
    frequency_lagging: list[PriorityMuscleOut] = Field(default_factory=list)


class WeightPointOut(_Contract):
    """Um dia COM pesagem: valor observado e media movel de 7 dias
    (`weight-ma-v1`). Dias sem pesagem nao existem nesta lista - nunca viram
    zero."""

    local_date: date
    weight_kg: float
    source: str
    ma7_kg: float | None = None
    ma7_points: int = Field(ge=1)


class BodyBlockOut(OverviewBlockBase):
    """"Como esta o corpo?" <- `body_service.body_block` (F1.4): peso (3
    fontes, prioridade por dia), bioimpedancia como ESTIMATED e cintura
    (`waist_cm`, ultima medida no protocolo). Sem `body_measurements` ou sem
    medida, `waist_cm.value` e `None` com a limitacao declarada."""

    weight_latest: Metric
    weight_ma7_latest: Metric
    weight_ma7_change: Metric
    weight_ma7_change_pct: Metric
    weighings_count: int = Field(ge=0)
    weighings_per_week: Metric
    weight_points: list[WeightPointOut] = Field(default_factory=list)
    body_fat_percent: Metric
    muscle_mass_kg: Metric
    bioimpedance_local_date: date | None = None
    waist_cm: Metric


class RecoveryBlockOut(OverviewBlockBase):
    """"Estou recuperando bem?" <- `recovery_signals_service` (avaliacao
    combinada + contagem de sinais concordantes/divergentes). `window` e a
    janela PROPRIA do bloco (recuperacao e uma pergunta sobre agora)."""

    window: WindowInfo
    level: Severity
    readiness_reading: ReadinessReading
    headline: str
    uncertainty: DataQuality
    allow_intensification: bool
    intensification_blocked_reason: str | None = None
    groups_total: int = Field(ge=0)
    groups_ready: int = Field(ge=0)
    groups_reduced_dose: int = Field(ge=0)
    groups_no_direct_work: int = Field(ge=0)
    signals_agreeing: int = Field(ge=0)
    signals_diverging: int = Field(ge=0)
    signals_unavailable: int = Field(ge=0)
    reassess_after_hours: float | None = None


class NextActionsBlockOut(OverviewBlockBase):
    """"Proxima acao" <- `insight_rules`. `items` tem no maximo 2; `total_triggered`
    diz quantos dispararam ao todo (o resto esta em `/insights`). Lista vazia e
    um estado POSITIVO real ("nenhuma acao sugerida agora")."""

    items: list[Insight] = Field(default_factory=list)
    total_triggered: int = Field(ge=0)
    max_items: int = Field(ge=1)


class OverviewBlocksOut(_Contract):
    consistency: ConsistencyBlockOut
    progression: ProgressionBlockOut
    priority_muscles: PriorityMusclesBlockOut
    body: BodyBlockOut
    recovery: RecoveryBlockOut
    next_actions: NextActionsBlockOut


class OverviewResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/overview`.

    `data_quality` (envelope) = a PIOR qualidade entre os blocos;
    `data_quality_overall` = o veredito do `data_quality_service` sobre os
    DADOS (o mesmo `overall` de `/dashboard/data-quality`)."""

    timezone: str
    blocks: OverviewBlocksOut
    data_quality_overall: DataQuality
    data_quality_issues: int = Field(ge=0)
    target_source: TargetSource
    training_type_id: int | None = None


# ---------------------------------------------------------------------------
# F1.4 - `GET /analytics/dashboard/body` (plano §6 `body_weight_ma7`,
# `waist_cm`, `recomposition_signal`; §10; §11 `/dashboard/corpo`)
# ---------------------------------------------------------------------------


class BodyWeightOut(_Contract):
    """Peso com media movel de 7 dias (`weight-ma-v1`): um ponto por dia COM
    pesagem, fonte declarada por dia; dias sem pesagem nao existem."""

    answer: str
    latest: Metric
    ma7_latest: Metric
    change: Metric
    change_pct: Metric
    weighings_count: int = Field(ge=0)
    weighings_per_week: Metric
    points: list[WeightPointOut] = Field(default_factory=list)
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


class MeasurementPointOut(_Contract):
    """Uma medida por fita. Fora do protocolo -> `data_quality: LOW` (entra na
    serie, nao entra no sinal de recomposicao)."""

    id: int | None = None
    local_date: date
    value_cm: float
    protocol_ok: bool
    data_quality: DataQuality
    notes: str | None = None


class BodyWaistOut(_Contract):
    """`waist-v1`: ultima cintura no protocolo, variacao entre a primeira e a
    ultima no protocolo dentro da janela, e a serie inteira."""

    available: bool
    table_available: bool
    latest: Metric
    latest_local_date: date | None = None
    change: Metric
    change_span_days: int | None = None
    points: list[MeasurementPointOut] = Field(default_factory=list)
    protocol_points: int = Field(ge=0)
    out_of_protocol_points: int = Field(ge=0)
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


class BodyMeasurementSeriesOut(_Contract):
    """Serie simples de um local medido por fita, exceto a cintura (vocabulario e
    rotulo do dono `domain/body_measurement_sites.py`)."""

    site: str
    label: str
    latest: Metric
    latest_local_date: date
    change: Metric
    points: list[MeasurementPointOut] = Field(default_factory=list)
    data_quality: DataQuality


class BioimpedancePointOut(_Contract):
    local_date: date
    weight_kg: float | None = None
    body_fat_percent: float | None = None
    muscle_mass_kg: float | None = None
    body_water_percent: float | None = None


class BodyBioimpedanceOut(_Contract):
    """Bioimpedancia da balanca - sempre ESTIMATED. `readings_count` conta as
    leituras DENTRO da janela; tendencia (`*_change`) so com >= 2."""

    readings_count: int = Field(ge=0)
    latest_local_date: date | None = None
    body_fat_percent: Metric
    muscle_mass_kg: Metric
    body_water_percent: Metric
    body_fat_change: Metric
    muscle_mass_change: Metric
    points: list[BioimpedancePointOut] = Field(default_factory=list)
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


class RecompositionStatus(str, Enum):
    COMPATIBLE = "compatible"
    NOT_COMPATIBLE = "not_compatible"
    INSUFFICIENT_DATA = "insufficient_data"


class RecompositionCheckOut(_Contract):
    """Uma das tres verificacoes do `recomp-v1`; `result=None` = nao avaliavel."""

    code: str
    label: str
    result: bool | None = None
    detail: str


class RecompositionSignalOut(_Contract):
    """`recomp-v1` (INFERRED): "compativel com recomposicao" - nunca
    "confirmada". Sempre com `evidence[]` e `limitations[]`."""

    status: RecompositionStatus
    nature: DataNature
    metric_version: str
    window: WindowInfo
    answer: str
    checks: list[RecompositionCheckOut] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    comparable_exercises: int = Field(ge=0)
    progressing_or_stable: int = Field(ge=0)


class PerformancePointOut(_Contract):
    local_date: date
    e1rm_avg_kg: float
    exercises: int = Field(ge=1)


class BodyPerformanceOut(_Contract):
    """e1RM medio diario dos exercicios comparaveis (do lote de progressao),
    para o grafico alinhado no tempo com o peso - eixo proprio, sem dual-axis."""

    points: list[PerformancePointOut] = Field(default_factory=list)
    exercises_total: int = Field(ge=0)
    comparable_exercises: int = Field(ge=0)
    progressing_or_stable: int = Field(ge=0)
    data_quality: DataQuality
    limitations: list[str] = Field(default_factory=list)


class BodyMilestoneOut(_Contract):
    local_date: date
    kind: str
    label: str
    detail: str | None = None


class BodyResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/body`. `data_quality` (envelope)
    = a pior entre peso e cintura; cada secao carrega a propria."""

    timezone: str
    weight: BodyWeightOut
    waist: BodyWaistOut
    measurements: list[BodyMeasurementSeriesOut] = Field(default_factory=list)
    bioimpedance: BodyBioimpedanceOut
    recomposition: RecompositionSignalOut
    performance: BodyPerformanceOut
    milestones: list[BodyMilestoneOut] = Field(default_factory=list)
    milestones_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# F2.1 - `GET /analytics/dashboard/context` (plano §11 `/dashboard/contexto`,
# §12). A timeline do que estava acontecendo em volta dos numeros.
# ---------------------------------------------------------------------------


class ContextEventCountOut(_Contract):
    """Quantos eventos de cada tipo na janela. E contagem de REGISTRO, nunca
    de ocorrencia: "0 álcool" quer dizer "nada anotado", nao "nao bebeu"."""

    event_type: str
    event_type_label: str
    count: int = Field(ge=0)


class ContextResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/context`.

    Sempre 200: sem evento nenhum a resposta existe com `INSUFFICIENT` e a
    limitacao que diz a verdade ("nada foi anotado"). `table_available=False`
    e a janela de deploy antes da migracao 048 - os DERIVADOS continuam
    aparecendo, porque nao dependem da tabela nova.

    `days_since_last_session` e o insumo (observado) do insight
    `TRAINING_PAUSE_DETECTED`; a tela mostra o numero, a regra decide se ele
    e uma pausa."""

    items: list[ContextEventOut] = Field(default_factory=list)
    counts: list[ContextEventCountOut] = Field(default_factory=list)
    manual_total: int = Field(ge=0)
    derived_total: int = Field(ge=0)
    table_available: bool = True
    nature: DataNature = DataNature.OBSERVED
    last_session_local_date: date | None = None
    days_since_last_session: int | None = None


class InsightsResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/insights` (paginacao simples).

    `items` ja vem deduplicado por `fingerprint` e ordenado por severidade e
    confianca. `rules_disabled` lista as regras escritas mas desligadas - a
    partir da F2.1 ela costuma vir VAZIA (a regra de evento de contexto, a
    unica desligada ate entao, foi ligada com a migracao 048)."""

    items: list[Insight] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool = False
    rules_evaluated: int = Field(ge=0)
    rules_disabled: list[str] = Field(default_factory=list)
    # F2.2 - cooldown. `suppressed_total` conta o que ficou de fora por ja ter
    # sido visto; `include_suppressed=True` acrescenta esses itens ao fim de
    # `items`. `cooldown_available=False` e a janela de deploy (migracao 051
    # pendente) ou a flag desligada - ai NADA foi silenciado, e a limitacao
    # declarada diz isso.
    suppressed_total: int = Field(default=0, ge=0)
    include_suppressed: bool = False
    cooldown_available: bool = True


# ---------------------------------------------------------------------------
# F2.3 - `GET /analytics/dashboard/cardio` (plano §11 `/dashboard/cardio`)
# ---------------------------------------------------------------------------


class CardioSessionOut(_Contract):
    """Uma atividade de CARDIO sincronizada do Intervals.icu.

    Todo numero e `null` quando nao foi medido - nunca 0. Um passeio sem
    sensor de FC tem `avg_hr_bpm=None` e ainda assim conta nos minutos; uma
    esteira sem GPS tem `distance_km=None` e nao vira "0 km"."""

    intervals_activity_id: int | None = None
    external_id: str
    activity_date: date | None = None
    start_time: datetime | None = None
    name: str | None = None
    sport_type: str | None = None
    kind: str
    minutes: int | None = None
    distance_km: float | None = None
    calories_kcal: float | None = None
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
    training_load: float | None = None
    # M98 C4: tempo em zona Z1..Z5 do app (`hr_zones`) e a classe do envelope
    # (`cardio_envelope.is_high_intensity`). Sem tempo em zona: tudo None.
    zone_seconds: list[int] | None = None
    zone_minutes: dict[str, int] | None = None
    high_intensity: bool | None = None


class CardioWeekOut(_Contract):
    """Cardio de UMA semana (segunda a domingo) - M98 C4. Contagens sao
    observadas (0 = nada sincronizado na semana); `zone_minutes` e
    `calories_kcal` sao None quando nenhuma sessao trouxe o dado."""

    week_start: date
    sessions: int = Field(ge=0)
    minutes: int = Field(ge=0)
    calories_kcal: float | None = None
    sessions_with_zones: int = Field(ge=0)
    zone_minutes: dict[str, int] | None = None
    continuous_sessions: int = Field(ge=0)
    continuous_minutes: int = Field(ge=0)
    high_intensity_sessions: int = Field(ge=0)
    high_intensity_minutes: int = Field(ge=0)
    unclassified_sessions: int = Field(ge=0)
    unclassified_minutes: int = Field(ge=0)


class CardioAdherenceSessionOut(_Contract):
    """Planejado x realizado de UMA sessao (dono `cardio_adherence`). Dia de
    descanso com cardio (ficha salva para a data, sem sessao de forca):
    `activity_id` = None, `workout_plan_id` = a ficha, `anchor` = `local_day`."""

    activity_id: int | None = None
    workout_plan_id: int | None = None
    anchor: str | None = None
    local_date: date | None = None
    mode: str | None = None
    prescription_text: str | None = None
    planned_minutes: int | None = None
    realized_minutes: int | None = None
    time_completion_pct: float | None = None
    zone_completion_pct: float | None = None
    has_zone_data: bool = False
    match_status: str | None = None
    status: str
    status_label: str


class CardioAdherenceWeekOut(_Contract):
    week_start: date
    planned_sessions: int = Field(ge=0)
    done_sessions: int = Field(ge=0)
    planned_minutes: int | None = None
    realized_minutes: int | None = None
    time_completion_pct: float | None = None
    by_status: dict[str, int] = Field(default_factory=dict)


class CardioAdherenceOut(_Contract):
    anchor: str
    sessions: list[CardioAdherenceSessionOut] = Field(default_factory=list)
    weeks: list[CardioAdherenceWeekOut] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)
    status_labels: dict[str, str] = Field(default_factory=dict)


class CardioWellnessPointOut(_Contract):
    date: date
    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    hrv_ms: float | None = None
    resting_hr_bpm: float | None = None


class CardioWellnessLatestOut(_Contract):
    ctl: float | None = None
    ctl_date: date | None = None
    atl: float | None = None
    atl_date: date | None = None
    tsb: float | None = None
    tsb_date: date | None = None
    hrv_ms: float | None = None
    hrv_ms_date: date | None = None
    resting_hr_bpm: float | None = None
    resting_hr_bpm_date: date | None = None


class CardioWellnessOut(_Contract):
    points: list[CardioWellnessPointOut] = Field(default_factory=list)
    latest: CardioWellnessLatestOut


class CardioPaiPointOut(_Contract):
    date: date
    pai: float


class CardioPaiOut(_Contract):
    """PAI de 7 dias ESTIMADO do tempo em zona do Intervals (M99, motor
    `cardio_load`); a meta e de `cardio_policy`."""

    points: list[CardioPaiPointOut] = Field(default_factory=list)
    target_min: float
    latest: float | None = None
    latest_date: date | None = None
    latest_below_target: bool | None = None
    estimated: bool = True
    model_version: str | None = None


class CardioNextSessionOut(_Contract):
    """Proxima sessao da ultima recomendacao `cardio-agent-v3` ainda valida."""

    recommendation_id: int | None = None
    generated_at: datetime | None = None
    prompt_version: str | None = None
    date: str | None = None
    date_label: str | None = None
    prescription_text: str | None = None
    prescription_short: str | None = None
    total_minutes_label: str | None = None
    rationale: str | None = None
    signals: list[str] = Field(default_factory=list)
    adjustment_labels: list[str] = Field(default_factory=list)
    slot_name: str | None = None
    plan_action: str | None = None


class CardioZoneOut(_Contract):
    zone: int
    label: str
    lower_bpm: int
    upper_bpm: int


class CardioZonesOut(_Contract):
    """Zonas em uso (dono `hr_zones`): `source` = intervals | manual_max_hr | none."""

    source: str
    max_hr_bpm: int | None = None
    zones: list[CardioZoneOut] = Field(default_factory=list)


class CardioTotalsOut(_Contract):
    """Totais da janela. `sessions` e contagem (0 e observado); os demais sao
    `null` quando NADA foi medido, porque somar nada nao da zero, da
    ausencia."""

    sessions: int = Field(ge=0)
    sessions_with_hr: int = Field(ge=0)
    minutes: int = Field(ge=0)
    distance_km: float | None = None
    calories_kcal: float | None = None
    training_load: float | None = None
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None


class CardioSportOut(_Contract):
    """Agregado por modalidade (`sport_type` como o Intervals.icu o
    escreve)."""

    sport_type: str
    sessions: int = Field(ge=0)
    minutes: int = Field(ge=0)
    distance_km: float | None = None
    calories_kcal: float | None = None
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None


class SessionHeartRateOut(_Contract):
    """A FC de UMA sessao de treino, com a ORIGEM dela declarada.

    `hr_source` distingue as duas origens possiveis, e a distincao e do
    usuario, nao interna: `activity` e a FC MEDIDA na propria sessao
    (`activities.avg_hr_bpm`, do import do relogio) e `intervals_link` e a da
    atividade `WeightTraining` que o job casou por horario. A primeira e
    medida; a segunda, associacao por proximidade. Apresenta-las como a mesma
    coisa seria apagar essa diferenca.

    `match_status`, `match_delta_seconds` e `candidates_count` vem direto de
    `activity_intervals_link` e sao CONTRATO, nao detalhe interno: eles sao a
    unica forma de a tela dizer "esta FC pode ser de outro treino"
    (`MATCHED_AMBIGUOUS`) em vez de apresentar o numero como certeza.

    `has_heart_rate` e `avg_hr_bpm is not None`, nao "existe link": o relogio
    pode ter registrado a atividade sem medir a FC."""

    activity_id: int | None = None
    local_date: date | None = None
    name: str | None = None
    started_at: datetime | None = None
    minutes: int | None = None
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
    hr_source: str | None = None
    calories_kcal: float | None = None
    training_load: float | None = None
    match_status: str | None = None
    match_delta_seconds: int | None = None
    candidates_count: int | None = None
    has_heart_rate: bool = False


class StrengthHeartRateOut(_Contract):
    """Cobertura de FC das sessoes de treino da janela.

    `coverage_pct` e `null` (nao 0) sem sessao nenhuma: sem denominador nao ha
    percentual."""

    sessions_total: int = Field(ge=0)
    sessions_with_hr: int = Field(ge=0)
    coverage_pct: float | None = None
    # Casamentos ambiguos NA JANELA e, separado, os que estao de fato EM USO
    # (a sessao que tem FC propria nao fica menos confiavel porque o job achou
    # dois candidatos para ela).
    ambiguous_total: int = Field(default=0, ge=0)
    ambiguous_in_use_total: int = Field(default=0, ge=0)
    from_activity_total: int = Field(default=0, ge=0)
    from_link_total: int = Field(default=0, ge=0)
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
    items: list[SessionHeartRateOut] = Field(default_factory=list)


class CardioResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/cardio` (F2.3).

    Sempre 200. Sem Intervals.icu configurado a resposta EXISTE, vazia, com
    `INSUFFICIENT` e a limitacao que diz o que fazer - a tela nunca mostra
    zeros que parecam medida.

    Os dois lados da tela tem dependencias DIFERENTES, de proposito:
    `totals`/`by_sport`/`sessions` exigem o Intervals configurado e
    sincronizado, enquanto `strength_heart_rate` le o HISTORICO ja associado
    (`activity_intervals_link`) e continua valendo mesmo se a chave for
    revogada depois.

    `strength_activities_total` e `other_activities_total` existem para a
    ausencia ser explicavel: "12 atividades sincronizadas, nenhuma de cardio"
    e uma resposta diferente de "nenhuma atividade sincronizada".

    `unmatched_strength_activities_total` (M37) e o LADO DO INTERVALS do
    casamento: `strength_heart_rate` conta sessoes sem FC, este conta
    atividades WeightTraining que nao viraram FC de sessao nenhuma. Subconjunto
    de `strength_activities_total`, nunca maior."""

    nature: DataNature = DataNature.OBSERVED
    intervals_configured: bool = False
    last_synced_at: datetime | None = None
    totals: CardioTotalsOut
    by_sport: list[CardioSportOut] = Field(default_factory=list)
    sessions: list[CardioSessionOut] = Field(default_factory=list)
    strength_activities_total: int = Field(default=0, ge=0)
    unmatched_strength_activities_total: int = Field(default=0, ge=0)
    other_activities_total: int = Field(default=0, ge=0)
    strength_heart_rate: StrengthHeartRateOut
    # M98 C4 - acompanhamento. `adherence` None = sem Intervals configurado
    # (sem realizado nao ha aderencia) ou bloco indisponivel; `zones` None =
    # bloco indisponivel.
    weeks: list[CardioWeekOut] = Field(default_factory=list)
    adherence: CardioAdherenceOut | None = None
    wellness: CardioWellnessOut
    pai: CardioPaiOut
    next_session: CardioNextSessionOut | None = None
    zones: CardioZonesOut | None = None


# ---------------------------------------------------------------------------
# F3.2 - `GET /analytics/dashboard/intelligence-package` (plano §12, §13, §16,
# §17 F3.2). O pacote que a LLM (e o Personal Trainer externo) recebe NO LUGAR
# de um contexto montado pelo cliente.
#
# Regra central: este contrato NAO produz numero novo. Cada campo aqui e uma
# COPIA de algo que `build_overview`/`context_service` ja calcularam, com a
# mesma `nature`, a mesma `metric_version` e a mesma `data_quality`. Se um dia
# alguem precisar somar dois valores para preencher um campo daqui, o lugar
# certo e o servico que ja calcula, nunca este montador - senao nasce uma
# segunda verdade cujo unico leitor e um modelo de linguagem, que nao sabe
# checar.
# ---------------------------------------------------------------------------


class PackageMetricOut(_Contract):
    """Um numero do pacote, achatado a partir de um `Metric` da visao geral.

    `key` e o nome tecnico estavel (`consistency.rate`,
    `body.weight_ma7_change_pct`...) - e por ele que a avaliacao de respostas
    (`tests/eval/`) afirma "esta pergunta tem que trazer esta metrica". `label`
    e o texto em portugues que o modelo pode citar sem reescrever."""

    key: str
    label: str
    value: float | int | None = None
    unit: str | None = None
    nature: DataNature
    metric_version: str
    data_quality: DataQuality = DataQuality.HIGH


class PackageBaselineOut(_Contract):
    """Contra o que um numero do pacote se compara (§12 `Comparison`).

    Baseline NAO e meta: `target_min`/`target_max` sao metas e viajam como
    metrica. Aqui vive o "antes" (semana anterior, primeira media movel da
    janela) que torna uma variacao interpretavel sem o modelo ter que
    recalcular nada."""

    key: str
    label: str
    basis: str
    window: str
    value: float | int | None = None
    unit: str | None = None
    nature: DataNature


class PackageEvidenceOut(Evidence):
    """`Evidence` (§12) com a origem e o ALCANCE da data declarados.

    `insight_code` e a regra deterministica que produziu o par antes/depois.
    O prompt obriga a citar evidencia por data, e e este campo que torna a
    citacao verificavel: quem le a resposta consegue voltar ao insight exato.

    `observed_scope` existe porque "a data da evidencia" nao e uma coisa so, e
    tratar as duas como se fossem foi o defeito que o CI do #241 encontrou:

    - `point`: a regra informou `observed_at` - ha um dia especifico ao qual a
      observacao pertence (a ultima exposicao de um exercicio, a pesagem).
    - `window`: a regra NAO informou, e nao informou de proposito. Metade das
      evidencias de `insight_rules` sao agregados de JANELA (series semanais
      efetivas de um grupo, total de series de trabalho, % de progresso) ou
      constantes de PLANO (meta minima semanal). Elas nao tem um dia - tem um
      periodo. Nesses casos `observed_at` recebe `window.date_to` e
      `observed_from` recebe `window.date_from`, e o alcance fica DECLARADO.

    Por que declarar em vez de descartar ou de deixar `None`: descartar tiraria
    de `evidence[]` justamente a evidencia central dos alertas mais comuns (o
    `weekly_effective_sets` de cada grupo abaixo do minimo); deixar `None`
    entregaria ao modelo uma evidencia que o proprio prompt o proibe de citar.
    Inventar um dia unico para um agregado de 28 dias seria pior que os dois:
    seria falso, e falso com aparencia de precisao."""

    insight_code: str
    severity: Severity
    observed_scope: Literal["point", "window"] = "point"
    observed_from: date | None = None


class PackageMuscleGroupOut(_Contract):
    """Um grupo muscular presente na janela.

    Existe por dois motivos, nenhum deles cosmetico: (1) e a lista que filtra
    o RAG (`rag_chunking.select_rag_text`) - sem ela o prompt voltaria a levar
    o dump inteiro de `rag/markdown`; (2) sem ela o modelo nao tem como saber
    que um grupo simplesmente nao apareceu na janela, e "nao treinou" viraria
    "nao ha dado"."""

    muscle_group_id: int
    muscle_group: str
    status: str
    weekly_effective_sets: float | None = None
    direct_sessions: float | None = None
    hours_since_last_stimulus: float | None = None


class AllowedActionOut(_Contract):
    """Uma acao que o modelo PODE sugerir, com vocabulario fechado.

    O pacote leva a lista inteira e o prompt proibe sair dela. Nao e
    decoracao: sem ela a resposta livre sugere mudar dieta, trocar suplemento
    ou "fazer exames" - coisas que o produto nao faz, nao mede e nao tem como
    sustentar com a evidencia que esta no pacote."""

    code: str
    label: str
    description: str


class IntelligencePackageResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/intelligence-package` e entrada
    unica do prompt de `POST /insights/generate` (plano §16, §17 F3.2).

    Teto de **40 KB** (`intelligence_package.PACKAGE_MAX_BYTES`), verificado em
    teste. Quando o conteudo nao cabe, o corte e DECLARADO: `truncated=True` e
    uma linha em `limitations` dizendo o que saiu e em que ordem. Corte
    silencioso e pior que resposta ausente - o modelo responderia com
    confianca sobre um recorte que ninguem pediu.

    `size_bytes` e o tamanho medido da serializacao JSON compacta do pacote
    **antes** de este campo ser escrito (um campo que se mede a si mesmo nao
    tem ponto fixo). A diferenca e de dezenas de bytes, e o teto do teste e
    aplicado sobre o payload FINAL - entao a folga esta do lado seguro."""

    question: str | None = None
    timezone: str
    package_version: str
    metrics: list[PackageMetricOut] = Field(default_factory=list)
    baselines: list[PackageBaselineOut] = Field(default_factory=list)
    evidence: list[PackageEvidenceOut] = Field(default_factory=list)
    context_events: list[ContextEventOut] = Field(default_factory=list)
    muscle_groups: list[PackageMuscleGroupOut] = Field(default_factory=list)
    data_quality_overall: DataQuality
    allowed_actions: list[AllowedActionOut] = Field(default_factory=list)
    truncated: bool = False
    size_bytes: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# F3.1 - `GET /analytics/dashboard/engine` e
# `GET /analytics/dashboard/engine/plans/{plan_id}` (plano §4 "Engine runs",
# §10, §17).
#
# A pergunta da tela: "por que a minha ficha saiu assim, e o que eu fiz com
# ela?". Tres colunas por item, e nenhuma delas pode ser confundida com as
# outras:
#
# - PLANEJADO  - o que o MODELO pediu (`workout_plan_template_slot_items`, ou,
#                no modo `percent_budget`, o orcamento do grupo muscular, que
#                nao nomeia exercicio nenhum);
# - GERADO     - o que o motor colocou na ficha (`workout_plan_exercises` /
#                `prescription_json`), com o PORQUE (`generation_audit`:
#                `selection`, `safety_clamps`, `substitutions`, `fallbacks`,
#                `activation`, e a decisao de `progression_decisions`);
# - EXECUTADO  - o que a sessao registrou (`exercises`/`exercise_sets` pelo
#                vinculo `workout_plan_exercise_id`), com o status derivado do
#                §5 - exatamente as regras de `adherence_service`, nunca uma
#                segunda tabela de status.
# ---------------------------------------------------------------------------


class EngineAuditSource(str, Enum):
    """De ONDE veio o `generation_audit` desta ficha.

    - `GENERATION_RUNS`: a linha de `generation_runs` (migracao 054) - a
      fonte preferida, porque ela existe mesmo quando o rascunho foi
      descartado e carrega `created_at`, `job_id` e `request_json`;
    - `PLAN_RAW_PAYLOAD`: o bloco ecoado em `workout_plans.raw_payload`. E o
      unico caminho das fichas ANTERIORES ao M16, e por isso o fallback e
      DECLARADO em vez de silencioso: dele nao sai a data da geracao nem o
      pedido que a originou;
    - `UNAVAILABLE`: nem uma coisa nem outra (ficha criada a mao, ficha antiga
      salva sem o payload). A coluna "gerado" continua existindo; o "por que"
      nao - e dizer isso e melhor do que exibir uma tabela vazia.
    """

    GENERATION_RUNS = "generation_runs"
    PLAN_RAW_PAYLOAD = "plan_raw_payload"
    UNAVAILABLE = "unavailable"


class EnginePlannedSource(str, Enum):
    """De onde saiu a coluna PLANEJADO do item.

    - `TEMPLATE_ITEM`: o Modelo nomeia exercicio/equipamento do item
      (`item_kind` `exercise`/`equipment`, modo `equipment_series`);
    - `MUSCLE_GROUP_BUDGET`: o Modelo so pediu VOLUME para um grupo (modo
      `percent_budget`) - quem escolheu o exercicio foi o motor, e escrever
      "planejado: supino" atribuiria ao Modelo uma decisao que nao foi dele;
    - `UNAVAILABLE`: a ficha nao aponta para Modelo nenhum (geracao avulsa, ou
      Modelo/slot apagado depois).
    """

    TEMPLATE_ITEM = "template_item"
    MUSCLE_GROUP_BUDGET = "muscle_group_budget"
    UNAVAILABLE = "unavailable"


class EngineReasonKind(str, Enum):
    """Por que o gerado e o que e. Um item pode ter varias razoes."""

    SELECTION = "selection"  # `generation_audit.selection[]` (escolha/recusa)
    SAFETY_CLAMP = "safety_clamp"  # teto de seguranca aplicado a carga/RPE/reps
    SUBSTITUTION = "substitution"  # troca de exercicio/equipamento pelo M6
    FALLBACK = "fallback"  # caminho alternativo ao pretendido
    PROGRESSION = "progression"  # decisao do motor de progressao v2


class EngineReasonOut(_Contract):
    """Uma razao, com o CODIGO tal como o motor o gravou.

    `code` nunca e traduzido aqui: ele e a chave estavel (`reason`,
    `decision`) e a tela rotula o que conhece. `data` sao os campos restantes
    da entrada de auditoria, sem nada acrescentado e sem nada inventado."""

    kind: EngineReasonKind
    code: str | None = None
    detail: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EngineProgressionOut(_Contract):
    """A decisao de `progression_decisions` que prescreveu carga/reps deste
    item. Ausente = a decisao nao foi gravada com o vinculo
    (`workout_plan_exercise_id`), NUNCA "nao houve progressao"."""

    id: int
    decision: str | None = None
    kind: str | None = None
    phase_before: str | None = None
    phase_after: str | None = None
    engine_version: str | None = None
    created_at: datetime | None = None


class EnginePlannedOut(_Contract):
    """O que o MODELO pediu para este item."""

    source: EnginePlannedSource
    item_kind: str | None = None
    item_order: int | None = None
    muscle_group_id: int | None = None
    muscle_group_name: str | None = None
    exercise_id: int | None = None
    exercise_name: str | None = None
    equipment_id: int | None = None
    sets_count: int | None = None
    reps_target: str | None = None
    rpe_target: str | None = None
    advanced_technique: str | None = None
    prioritize: bool | None = None
    # M101d: item de exercicio livre do Modelo ("o motor escolhe o
    # exercicio"); `exercise_id`/`exercise_name` vem vazios.
    free_exercise: bool | None = None
    # Orcamento do grupo (modo `percent_budget`, `generation_audit.activation`).
    budget_min_sets: float | None = None
    budget_max_sets: float | None = None
    budget_weekly_sets: float | None = None


class EngineItemOut(_Contract):
    """Um item da ficha nas tres colunas.

    `executed` e `None` quando NADA foi registrado para o item - diferente de
    "executou zero serie": quem diz o que aconteceu e o `status` derivado
    (`SKIPPED`), com a limitacao ao lado quando ela existe."""

    plan_exercise_id: int | None = None
    plan_sequence: int | None = None
    status: str
    planned: EnginePlannedOut
    generated_name: str | None = None
    generated_exercise_id: int | None = None
    generated: PrescribedOut
    executed: PerformedOut | None = None
    missing_sets: int | None = None
    substitution: PlanItemSubstitutionOut | None = None
    reasons: list[EngineReasonOut] = Field(default_factory=list)
    progression: EngineProgressionOut | None = None
    limitations: list[str] = Field(default_factory=list)


class EngineGroupBudgetOut(_Contract):
    """Uma linha de `generation_audit.activation`: quanto o orcamento pedia
    para o grupo e quanto a ficha alcancou."""

    muscle_group_id: int | None = None
    muscle_group_name: str | None = None
    target_weekly_sets: float | None = None
    target_min_sets: float | None = None
    target_max_sets: float | None = None
    generated_sets: float | None = None
    deviation_status: str | None = None
    recovery_status: str | None = None


class EngineRejectionOut(_Contract):
    """Candidato que o motor CONSIDEROU e nao colocou na ficha
    (`generation_audit.selection[]` com `selected` falso)."""

    muscle_group_id: int | None = None
    exercise_id: int | None = None
    exercise_name: str | None = None
    rank: int | None = None
    reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EngineRunOut(_Contract):
    """Uma geracao. Existe mesmo quando o rascunho NAO virou ficha
    (`workout_plan_id: null`) - foi para isso que a migracao 054 nasceu."""

    run_id: int | None = None
    source: EngineAuditSource
    created_at: datetime | None = None
    plan_date: date | None = None
    workout_plan_id: int | None = None
    plan_name: str | None = None
    template_id: int | None = None
    template_slot_sequence: int | None = None
    volume_mode: str | None = None
    engine_version: str | None = None
    llm_used: bool | None = None
    job_id: str | None = None
    selection_count: int | None = None
    safety_clamp_count: int | None = None
    substitution_count: int | None = None
    fallback_count: int | None = None
    limitations: list[str] = Field(default_factory=list)


class EngineRunsResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/engine` - as geracoes da janela.

    Janela sem nenhuma geracao responde 200 com `items: []` e
    `INSUFFICIENT`: "nenhuma geracao neste periodo" nao e um zero medido."""

    items: list[EngineRunOut] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    runs_table_total: int = Field(default=0, ge=0)
    raw_payload_total: int = Field(default=0, ge=0)


class EnginePlanResponse(DashboardEnvelope):
    """Resposta de `GET /analytics/dashboard/engine/plans/{plan_id}` -
    planejado x gerado x executado de UMA ficha.

    404 quando a ficha nao existe OU nao e do usuario: os dois casos respondem
    igual, entao nenhum id alheio e confirmado (IDOR)."""

    run: EngineRunOut
    plan_id: int
    plan_name: str | None = None
    plan_date: date | None = None
    template_id: int | None = None
    template_name: str | None = None
    template_slot_sequence: int | None = None
    template_slot_name: str | None = None
    # PR4: numero da versao do Modelo de que o PLANEJADO saiu ("vN" na tela);
    # vem do mesmo JOIN do cabecalho, sem statement a mais.
    template_version_number: int | None = None
    volume_mode: str | None = None
    items: list[EngineItemOut] = Field(default_factory=list)
    item_status_counts: ItemStatusCountsOut
    group_budgets: list[EngineGroupBudgetOut] = Field(default_factory=list)
    rejections: list[EngineRejectionOut] = Field(default_factory=list)
    plan_level_reasons: list[EngineReasonOut] = Field(default_factory=list)
    sessions_count: int = Field(default=0, ge=0)


__all__ = [
    "EngineAuditSource",
    "EngineGroupBudgetOut",
    "EngineItemOut",
    "EnginePlanResponse",
    "EnginePlannedOut",
    "EnginePlannedSource",
    "EngineProgressionOut",
    "EngineReasonKind",
    "EngineReasonOut",
    "EngineRejectionOut",
    "EngineRunOut",
    "EngineRunsResponse",
    "AllowedActionOut",
    "IntelligencePackageResponse",
    "PackageBaselineOut",
    "PackageEvidenceOut",
    "PackageMetricOut",
    "PackageMuscleGroupOut",
    "CardioResponse",
    "CardioSessionOut",
    "CardioSportOut",
    "CardioTotalsOut",
    "SessionHeartRateOut",
    "StrengthHeartRateOut",
    "InsightCooldownOut",
    "ContextEventCountOut",
    "ContextEventOut",
    "ContextResponse",
    "BodyBlockOut",
    "ConsistencyBlockOut",
    "InsightsResponse",
    "NextActionsBlockOut",
    "OverviewBlockBase",
    "OverviewBlocksOut",
    "OverviewResponse",
    "PriorityMuscleOut",
    "PriorityMusclesBlockOut",
    "ProgressionBlockOut",
    "ProgressionExerciseOut",
    "ProgressionStatusCountOut",
    "RecoveryBlockOut",
    "WeightPointOut",
    "ContextEventOut",
    "ReadinessReading",
    "RecoveryAssessmentOut",
    "RecoveryFactorSetOut",
    "RecoveryFactorsOut",
    "RecoveryGroupOut",
    "RecoveryRecommendationOut",
    "RecoverySignalOut",
    "RecoverySignalsResponse",
    "GlobalReadinessAdjustmentOut",
    "TrainingVsRestMetricOut",
    "TrainingVsRestOut",
    "SignalAgreement",
    "SignalDirection",
    "SignalReading",
    "TargetSource",
    "AdherenceResponse",
    "CycleSlotOut",
    "ItemStatusCountsOut",
    "PerformedOut",
    "PerformedSetOut",
    "PlanItemOut",
    "PlanItemSubstitutionOut",
    "PlanLinkOut",
    "PrescribedOut",
    "PrescriptionSource",
    "WorkoutDetailResponse",
    "WorkoutSessionOut",
    "WorkoutsResponse",

    "MusclesResponse",
    "MuscleTopExerciseOut",
    "MuscleTargetsOut",
    "MuscleSourceSetOut",
    "MuscleSessionOut",
    "MuscleRealizedOut",
    "MuscleReadinessOut",
    "MuscleProjectedOut",
    "MusclePlannedOut",
    "MuscleItemOut",
    "MuscleFrequencyOut",
    "MuscleExposureOut",
    "MuscleDrilldownResponse",
    "ComparabilityKeyOut",
    "Comparison",
    "DashboardEnvelope",
    "ExerciseProgressResponse",
    "ExerciseRecordOut",
    "ExerciseRecordsOut",
    "ExerciseRefOut",
    "ExerciseSetOut",
    "ExposureOut",
    "ProgressionDecisionOut",
    "DataNature",
    "DataQuality",
    "DataQualityExampleOut",
    "DataQualityItemOut",
    "DataQualityResponse",
    "DataQualityScope",
    "Evidence",
    "Insight",
    "Metric",
    "Severity",
    "WindowInfo",
    "WindowKind",
]
