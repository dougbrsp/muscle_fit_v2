"""Sinais de recuperacao do Novo Dashboard (plano §6 `readiness` e
`recovery-signals-v1`, §7 "Estou recuperando bem?", §10, §11, §15).

A tela responde a pergunta com DUAS leituras lado a lado, e nunca as funde
num numero so:

1. **Prontidao por grupo muscular** - o modelo `fullbody-no-reps-v1`
   (`MuscleRecoveryService`), o MESMO que a geracao de ficha e o Modo Treino
   consultam. Este servico nao recalcula nada dele: le `get_recovery_overview`
   e repassa status, horas, recomendacao e a `explanation` que o modelo ja
   escreve. Nao existe um segundo modelo de recuperacao no projeto.

2. **Sinais objetivos contra a baseline PESSOAL** (`recovery-signals-v1`,
   novo) - sono (duracao e regularidade), HRV, FC de repouso, TSB, dor
   muscular, dor articular e desempenho recente. Cada sinal compara a janela
   escolhida com a mediana dos `BASELINE_WINDOW_DAYS` dias ANTERIORES a ela.

Regras que o plano fixa, e que o codigo abaixo aplica literalmente:

- **Nenhum sinal isolado decreta recuperacao ruim.** Um HRV baixo sozinho nao
  muda o nivel da avaliacao; dois ou mais sinais negativos CONCORDANTES
  elevam para `ATTENTION`. `ACTION` so existe quando o MODELO classifica
  algum grupo como `NO_DIRECT_WORK` - ai a linguagem forte e a do proprio
  modelo, nao uma inferencia da tela.
- **Sinal divergente aparece, nao some.** Se o HRV esta otimo enquanto a
  prontidao esta baixa, os dois ficam visiveis com `agreement=DIVERGES`.
- **Sem baseline suficiente nao se inventa numero.** Menos de
  `MIN_BASELINE_OBSERVATIONS` pontos na janela de referencia -> `baseline`
  `null`, `agreement=NO_BASELINE` e uma limitacao declarada.
- **Wearable ausente nao zera prontidao.** Prontidao vem das SERIES
  registradas; sem Intervals.icu os sinais de HRV/FC/TSB simplesmente nao
  existem e isso e dito na resposta.
- **Evento de contexto NAO e sinal.** A partir da F2.1 o campo
  `assessment.context_events` traz os eventos reais da janela (manuais da
  tabela `context_events` + derivados de troca de ficha/academia, via
  `context_service`), mas eles ficam LADO A LADO da avaliacao: nao entram em
  `level`, nem em `allow_intensification`, nem nas contagens de concordancia.
  Contexto responde "o que mais estava acontecendo", nunca "por que".

- **Sinais da MESMA fonte contam uma vez.** Duracao e regularidade do sono saem
  da mesma noite e da mesma tabela; os quatro escores do check-in saem do mesmo
  formulario. Contados um a um, qualquer um desses conjuntos sozinho elevava a
  leitura para `ATTENTION` - exatamente o que a regra "nenhum sinal isolado
  decreta recuperacao ruim" existe para evitar. Cada conjunto declara um
  `SignalSpec.group` e vale UM na contagem de concordancia; todos continuam
  visiveis na tela, com a propria baseline (decisao do usuario, 2026-09-25).

- **Relato entra como sinal, nao como veredito.** A partir da F2.2 os quatro
  escores do check-in diario (`daily_checkins`, migracao 050: disposicao, dor
  global, estresse e humor, 1 a 5) aparecem com a mesma maquinaria de baseline
  pessoal dos objetivos. Eles contam UMA vez na concordancia, porque vem do
  mesmo formulario respondido de uma vez - ver o cabecalho de
  `checkin_service` para a medida dessa decisao. Nenhum deles e somado nem
  mediado com os outros: "dormi mal mas estou disposto" e informacao.

- **A tela legada nao mostra mais nada que aqui falte (F2.5).** Ate a M33 o
  detalhamento do MODELO - recomendacao de series, fatores do calculo e o
  ajuste global de fadiga (TSB) - so existia em `/recuperacao-muscular`. Ele
  passa a sair daqui, REPASSADO do mesmo `get_recovery_overview`: nenhum
  numero e recalculado, e nenhuma consulta nova foi aberta (o ajuste global
  vem da instancia que acabou de rodar o overview, que memoriza a leitura de
  `intervals_wellness`).

- **Dia COM treino x dia SEM treino (M36).** Sono, HRV e FC de repouso
  recortados pelos dias em que houve sessao. O recorte morava em `/treinos`,
  dentro de "Cardio & Recuperacao (Intervals)" - mas ele nunca foi cardio, e
  uma pergunta de recuperacao. Ele sai das MESMAS linhas ja lidas para os
  sinais e fica FORA de `signals`: nao entra em `level`, em
  `allow_intensification` nem em contagem de concordancia. Comparacao
  observacional declara que e associacao, nunca causa.

Custo: 4 consultas agregadas por dia (sono, wellness, dor, check-in) + a
prontidao, que desde a M20 (`intelligence/recovery_read_model.py`) custa 3
statements em vez de ~16, + 3 do contexto (eventos manuais, atribuicoes de
Modelo, academia por dia), todas por indice `(user_id, data)`.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from ...observability import log_event
from ..muscle_recovery_service import PERFORMANCE_DROP_SIGNAL_RATIO
from ..recovery_projection import ready_forecast
from .coercion import to_date, to_datetime, to_float
from .contracts import (
    DataNature,
    DataQuality,
    ReadinessReading,
    Severity,
    SignalAgreement,
    SignalDirection,
    SignalReading,
    WindowInfo,
)
from ...context_events_schema import TABLE_MISSING_LIMITATION  # noqa: F401 - re-export: os testes leem `service.TABLE_MISSING_LIMITATION`
from ...daily_checkins_schema import (
    SCORE_FIELDS,
    SCORE_HIGHER_IS_BETTER,
    SCORE_LABELS,
)
from .checkin_service import (
    CHECKIN_SIGNAL_GROUP,
    answered_days,
    load_checkins,
    signal_code as checkin_signal_code,
)
from .context_service import (
    ASSOCIATION_LIMITATION as CONTEXT_ASSOCIATION_LIMITATION,
    NO_EVENTS_LIMITATION,
    load_context_events,
)
from .identity import require_user_id
from .metrics_registry import metric_version
from .time_windows import user_now, user_wall_to_instant
from ...db_helpers import rows as _rows

# Janela de referencia da baseline pessoal: os 28 dias IMEDIATAMENTE
# anteriores a janela escolhida. Anteriores, e nao sobrepostos, de proposito -
# comparar a semana com uma media que inclui a propria semana amortece
# justamente o que se quer enxergar.
BASELINE_WINDOW_DAYS = 28

# Minimo de pontos na janela de referencia para a mediana virar baseline
# (plano §6, "baseline >= 14 pontos"). Abaixo disso o sinal vira "sem
# baseline suficiente" - nunca um numero inventado.
MIN_BASELINE_OBSERVATIONS = 14

# Dois sinais negativos CONCORDANTES elevam a atencao (plano §7). A contagem e
# por GRUPO, nao por sinal - ver `SLEEP_SIGNAL_GROUP` e `CHECKIN_SIGNAL_GROUP`.
NEGATIVE_SIGNALS_FOR_ATTENTION = 2

# Grupo de concordancia do SONO: `sleep_duration` e `sleep_regularity` saem da
# MESMA fonte (`sleep_metrics`, com fallback do Intervals.icu) e da mesma noite.
# Quem dorme pouco num horario baguncado marca as duas de uma vez - duas
# leituras do mesmo fato, nao duas observacoes independentes. Contadas
# separadamente, elas sozinhas elevavam a leitura para ATTENTION, contra a
# regra do plano (§7) de que nenhum sinal isolado decreta recuperacao ruim.
# Decisao do usuario, 2026-09-25, ao aprovar a F2.2 - o mesmo raciocinio que
# ja valia para os quatro escores do check-in diario.
SLEEP_SIGNAL_GROUP = "sleep"

_METRIC_VERSIONS: Mapping[str, str] = {
    key: metric_version(key) for key in ("readiness", "recovery_signals", "daily_checkin")
}

READINESS_MODEL = _METRIC_VERSIONS["readiness"]


# ---------------------------------------------------------------------------
# Limitacoes declaradas (texto unico, nunca escrito na tela)
# ---------------------------------------------------------------------------

# F2.1: os textos sobre CONTEXTO vivem no `context_service` (uma frase por
# estado, um dono so) e sao reexportados aqui porque a tela de recuperacao e
# um dos consumidores. Ate a F2.1 havia um `CONTEXT_EVENTS_LIMITATION`
# proprio dizendo "ainda nao sao registrados" - essa frase deixou de ser
# verdade no momento em que a 048 nasceu.
CONTEXT_EVENTS_EMPTY_LIMITATION = NO_EVENTS_LIMITATION
CONTEXT_EVENTS_ASSOCIATION_LIMITATION = CONTEXT_ASSOCIATION_LIMITATION

WEARABLE_MISSING_LIMITATION = (
    "Sem dado do Intervals.icu na janela: HRV, FC de repouso e TSB ficam ausentes. "
    "A prontidao NAO e afetada - ela vem das series registradas."
)

SLEEP_MISSING_LIMITATION = (
    "Sem sono registrado na janela: duracao e regularidade ficam ausentes (nada e estimado)."
)

NO_SIGNAL_LIMITATION = (
    "Nenhum sinal objetivo com baseline pessoal nesta janela: a leitura fica so com a prontidao "
    "do modelo, que nao observa sono, HRV nem FC de repouso."
)

ASSOCIATION_LIMITATION = (
    "Sinais objetivos indicam associacao, nunca causa: eles nao reclassificam a prontidao de "
    "nenhum grupo muscular."
)


def _baseline_limitation(label: str, observations: int) -> str:
    return (
        f"{label}: {observations} ponto(s) na janela de referencia, menos que os "
        f"{MIN_BASELINE_OBSERVATIONS} exigidos - sem baseline pessoal para comparar."
    )


# ---------------------------------------------------------------------------
# Especificacao dos sinais (unica fonte dos rotulos, unidades e limiares)
# ---------------------------------------------------------------------------


class SignalSpec:
    """Como UM sinal e lido.

    `higher_is_better` diz o que a direcao significa (HRV alto e bom, FC de
    repouso alta e ruim). `tolerance` e a faixa em que o valor conta como
    "em linha" com a baseline: relativa (fracao da baseline) para grandezas
    positivas, ABSOLUTA para as que passam perto de zero ou sao negativas -
    o TSB vive entre -30 e +20, e 5% de um TSB -0,5 nao significa nada."""

    def __init__(
        self,
        code: str,
        label: str,
        description: str,
        unit: str,
        source: str,
        higher_is_better: bool,
        tolerance: float,
        tolerance_is_absolute: bool = False,
        group: Optional[str] = None,
        subjective: bool = False,
        version_key: str = "recovery_signals",
    ) -> None:
        self.code = code
        self.label = label
        self.description = description
        self.unit = unit
        self.source = source
        self.higher_is_better = higher_is_better
        self.tolerance = tolerance
        self.tolerance_is_absolute = tolerance_is_absolute
        # F2.2: sinais do mesmo GRUPO contam UMA vez na concordancia (ver o
        # cabecalho de `checkin_service`). Sem grupo declarado, o sinal forma
        # grupo com ele mesmo - e assim os sinais objetivos continuam contando
        # exatamente como antes da F2.2.
        self.group = group or code
        self.subjective = subjective
        self.version_key = version_key


SIGNAL_SPECS: tuple[SignalSpec, ...] = (
    SignalSpec(
        code="sleep_duration",
        label="Duracao do sono",
        description="Horas dormidas por noite na janela, contra a sua mediana habitual.",
        unit="h",
        source="sleep_metrics",
        higher_is_better=True,
        tolerance=0.05,
        group=SLEEP_SIGNAL_GROUP,
    ),
    SignalSpec(
        code="sleep_regularity",
        label="Regularidade do sono",
        description="Quanto o horario de dormir se repete, contra a sua mediana habitual.",
        unit="%",
        source="sleep_metrics",
        higher_is_better=True,
        tolerance=0.05,
        group=SLEEP_SIGNAL_GROUP,
    ),
    SignalSpec(
        code="hrv",
        label="Variabilidade da FC (HRV)",
        description="HRV medida pelo wearable, contra a sua mediana habitual.",
        unit="ms",
        source="intervals_wellness",
        higher_is_better=True,
        tolerance=0.05,
    ),
    SignalSpec(
        code="resting_hr",
        label="FC de repouso",
        description="Frequencia cardiaca de repouso, contra a sua mediana habitual.",
        unit="bpm",
        source="intervals_wellness",
        higher_is_better=False,
        tolerance=0.03,
    ),
    SignalSpec(
        code="form_tsb",
        label="Forma (TSB)",
        description="Saldo entre carga cronica e aguda do Intervals.icu; negativo = fadiga acumulada.",
        unit="pts",
        source="intervals_wellness",
        higher_is_better=True,
        tolerance=5.0,
        tolerance_is_absolute=True,
    ),
    SignalSpec(
        code="muscle_pain",
        label="Dor muscular",
        description="Maior dor muscular relatada por dia de treino, contra a sua mediana habitual.",
        unit="0-10",
        source="exercises.muscle_pain",
        higher_is_better=False,
        tolerance=1.0,
        tolerance_is_absolute=True,
    ),
    SignalSpec(
        code="joint_pain",
        label="Dor articular",
        description="Sessoes com dor articular relatada na janela.",
        unit="sessoes",
        source="exercises.joint_pain",
        higher_is_better=False,
        tolerance=0.0,
        tolerance_is_absolute=True,
    ),
    SignalSpec(
        code="performance",
        label="Desempenho recente",
        description=(
            "Ultima sessao de cada exercicio contra a anterior comparavel (mesmo exercicio, "
            "equipamento e convencao de carga), agregada por grupo."
        ),
        unit="x",
        source="muscle_recovery_service.performance_ratio",
        higher_is_better=True,
        tolerance=0.05,
    ),
    # --- F2.2: check-in diario (`daily_checkins`, migracao 050) -----------
    #
    # Quatro escores de 1 a 5, RELATO (nao medida), todos no MESMO grupo de
    # concordancia: eles vem de um formulario respondido de uma vez, e num dia
    # ruim a pessoa marca os quatro. Contar quatro observacoes independentes
    # faria UM check-in ruim elevar a leitura para ATTENTION, contra a regra do
    # plano (§7) de que nenhum sinal isolado decreta recuperacao ruim.
    #
    # Tolerancia ABSOLUTA de 0,5 ponto: a escala tem 5 degraus e vive perto de
    # numeros pequenos, onde 5% de 3 (0,15) nao significa nada. Meio degrau e a
    # menor diferenca que uma pessoa consegue de fato distinguir ao responder.
    *(
        SignalSpec(
            code=checkin_signal_code(field),
            label=f"{SCORE_LABELS[field]} (check-in)",
            description=(
                f"{SCORE_LABELS[field]} que voce relatou no check-in diario (1 a 5), "
                "contra a sua mediana habitual."
            ),
            unit="1-5",
            source=f"daily_checkins.{field}",
            higher_is_better=SCORE_HIGHER_IS_BETTER[field],
            tolerance=0.5,
            tolerance_is_absolute=True,
            group=CHECKIN_SIGNAL_GROUP,
            subjective=True,
            version_key="daily_checkin",
        )
        for field in SCORE_FIELDS
    ),
)

SIGNAL_SPEC_BY_CODE: Mapping[str, SignalSpec] = {spec.code: spec for spec in SIGNAL_SPECS}

# Limiar de queda de desempenho: LIDO do dono
# (`muscle_recovery_service.PERFORMANCE_DROP_SIGNAL_RATIO`, o mesmo de
# `count_negative_signals`) - a tela nao pode chamar de "queda" algo que o
# modelo nao considera sinal. Lote 1/P2 (2026-10-11): antes era uma copia.
PERFORMANCE_DROP_RATIO = PERFORMANCE_DROP_SIGNAL_RATIO


# ---------------------------------------------------------------------------
# SQL - tres leituras agregadas POR DIA, todas com `user_id` obrigatorio
# ---------------------------------------------------------------------------

SLEEP_DAILY_SQL = """
SELECT local_date, duration_hours, regularity_percent
FROM sleep_metrics
WHERE user_id = %s AND local_date BETWEEN %s AND %s
ORDER BY local_date ASC
"""

WELLNESS_DAILY_SQL = """
SELECT wellness_date AS local_date, hrv_ms, resting_hr_bpm, form_tsb, sleep_seconds
FROM intervals_wellness
WHERE user_id = %s AND wellness_date BETWEEN %s AND %s
ORDER BY wellness_date ASC
"""

# Dor por DIA de treino. `exercises.muscle_pain`/`joint_pain` sao o rollup que
# o Modo Treino ja mantem a partir das series (`_refresh_exercise_aggregates`)
# e a MESMA fonte que o modelo de recuperacao le - nao ha uma segunda
# definicao de dor no projeto.
#
# M76 B8: a dor so conta pelo papel PRIMARY, como no modelo
# (`VolumeService.get_muscle_group_overview`, `role_is_primary`): exercicio com
# contribuicoes cadastradas mas nenhuma PRIMARY nao leva a dor a grupo nenhum,
# entao tambem nao vira "Dor muscular" aqui. Sem contribuicao cadastrada o
# modelo cai no fallback legado, em que o grupo do exercicio e PRIMARY - por
# isso esse caso continua contando. `sessions` segue contando todas as sessoes
# (e o dia de treino, nao a dor).
_PAIN_COUNTS_AS_PRIMARY = """(
    NOT EXISTS (
      SELECT 1 FROM exercise_muscle_contributions c
      WHERE c.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id) AND c.is_active = 1
    )
    OR EXISTS (
      SELECT 1 FROM exercise_muscle_contributions c
      WHERE c.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id) AND c.is_active = 1
        AND c.role = 'PRIMARY'
    )
  )"""

PAIN_DAILY_SQL = f"""
SELECT
  a.local_date                                                              AS local_date,
  MAX(CASE WHEN {_PAIN_COUNTS_AS_PRIMARY} THEN e.muscle_pain END)          AS muscle_pain,
  MAX(CASE WHEN {_PAIN_COUNTS_AS_PRIMARY} THEN COALESCE(e.joint_pain, 0) ELSE 0 END) AS joint_pain,
  COUNT(DISTINCT a.id)                                                      AS sessions
FROM activities a
JOIN exercises e ON e.activity_id = a.id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
WHERE a.user_id = %s AND a.local_date BETWEEN %s AND %s
GROUP BY a.local_date
ORDER BY a.local_date ASC
"""


# ---------------------------------------------------------------------------
# Helpers puros (testados sem banco)
# ---------------------------------------------------------------------------


def metric(
    value: Optional[float],
    *,
    unit: Optional[str],
    nature: DataNature,
    version_key: str,
    quality: DataQuality = DataQuality.HIGH,
    limitations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Envelope `Metric` (§8.4). `None` continua `None`: ausencia nunca vira
    zero - "nao da para calcular" e diferente de "foi zero"."""
    return {
        "value": round(value, 2) if isinstance(value, float) else value,
        "unit": unit,
        "nature": nature,
        "metric_version": _METRIC_VERSIONS[version_key],
        "data_quality": quality,
        "limitations": list(limitations or []),
    }


def personal_baseline(values: Sequence[float], min_observations: int = MIN_BASELINE_OBSERVATIONS):
    """Mediana da janela de referencia, ou `None` quando ha poucos pontos.

    Mediana, e nao media: uma noite pessima ou um HRV artefato nao podem
    deslocar a referencia da pessoa inteira. `None` e o resultado CORRETO
    quando faltam dados - quem chama transforma isso em "sem baseline
    suficiente", nunca num numero."""
    usable = [float(value) for value in values if value is not None]
    if len(usable) < max(1, int(min_observations)):
        return None, len(usable)
    return statistics.median(usable), len(usable)


def classify_direction(
    current: Optional[float], baseline: Optional[float], spec: SignalSpec
) -> SignalDirection:
    """Onde o valor caiu em relacao a baseline, respeitando a tolerancia do
    sinal. Sem valor ou sem baseline -> `UNKNOWN` (nunca `IN_LINE`, que
    afirmaria uma comparacao que nao aconteceu)."""
    if current is None or baseline is None:
        return SignalDirection.UNKNOWN
    tolerance = spec.tolerance if spec.tolerance_is_absolute else abs(baseline) * spec.tolerance
    delta = float(current) - float(baseline)
    if abs(delta) <= tolerance:
        return SignalDirection.IN_LINE
    return SignalDirection.ABOVE if delta > 0 else SignalDirection.BELOW


def classify_reading(direction: SignalDirection, spec: SignalSpec) -> SignalReading:
    """Traduz a direcao para o que ela significa em recuperacao. HRV ABAIXO
    da baseline e desfavoravel; FC de repouso ABAIXO e favoravel."""
    if direction is SignalDirection.UNKNOWN:
        return SignalReading.UNKNOWN
    if direction is SignalDirection.IN_LINE:
        return SignalReading.NEUTRAL
    is_above = direction is SignalDirection.ABOVE
    return SignalReading.FAVORABLE if is_above == spec.higher_is_better else SignalReading.UNFAVORABLE


def classify_agreement(reading: SignalReading, readiness: ReadinessReading) -> SignalAgreement:
    """Concordancia do sinal com a leitura de PRONTIDAO.

    Sinal sem leitura (sem baseline ou sem dado) nao concorda nem diverge:
    ele declara que nao da para comparar."""
    if reading is SignalReading.UNKNOWN:
        return SignalAgreement.NO_BASELINE
    if reading is SignalReading.NEUTRAL or readiness is ReadinessReading.UNKNOWN:
        return SignalAgreement.NEUTRAL
    strained = readiness is ReadinessReading.STRAINED
    unfavorable = reading is SignalReading.UNFAVORABLE
    return SignalAgreement.AGREES if strained == unfavorable else SignalAgreement.DIVERGES


def classify_readiness_reading(items: Sequence[Mapping[str, Any]]) -> ReadinessReading:
    """Resumo da prontidao do MODELO - so para dizer com o que os sinais
    concordam. `STRAINED` quando algum grupo esta bloqueado para trabalho
    direto ou quando pelo menos metade dos grupos esta em dose reduzida (um
    grupo isolado em dose reduzida e rotina num full body frequente)."""
    statuses = [str(item.get("readiness_status") or "") for item in items]
    if not statuses:
        return ReadinessReading.UNKNOWN
    if "NO_DIRECT_WORK" in statuses:
        return ReadinessReading.STRAINED
    reduced = sum(1 for status in statuses if status == "REDUCED_DOSE")
    if reduced * 2 >= len(statuses) and reduced > 0:
        return ReadinessReading.STRAINED
    return ReadinessReading.RECOVERED


def combine_assessment(
    signals: Sequence[Mapping[str, Any]], readiness: ReadinessReading, blocked_reason: Optional[str]
) -> tuple[Severity, DataQuality, list[str]]:
    """Nivel + incerteza da leitura combinada.

    - `ACTION` so quando o MODELO bloqueou trabalho direto em algum grupo
      (`blocked_reason`). Sinal objetivo nenhum promove a esse nivel.
    - `ATTENTION` com dois ou mais sinais negativos CONCORDANTES, contados por
      GRUPO: dois sinais do mesmo formulario (os quatro escores do check-in
      diario, F2.2) sao UMA observacao, nao duas. Sem isso um unico check-in
      ruim - energia baixa, humor baixo, estresse alto, dor alta, tudo marcado
      de uma vez - elevaria a leitura sozinho, exatamente contra a regra de que
      nenhum sinal isolado decreta recuperacao ruim.
    - `INFO` no resto, inclusive com um unico sinal negativo: um sinal
      isolado nao conclui nada (plano §7).
    """
    negative_signals = [
        signal
        for signal in signals
        if signal["reading"] is SignalReading.UNFAVORABLE
        and signal["agreement"] is not SignalAgreement.NO_BASELINE
    ]
    # UM representante por grupo, na ordem em que os sinais aparecem: assim
    # `len(agreeing_negatives)` E a contagem que decide o nivel, e a frase do
    # `headline_for` nunca diz "4 sinais concordam" sobre quatro respostas do
    # mesmo formulario.
    agreeing_negatives: list[str] = []
    seen_groups: set[str] = set()
    for signal in negative_signals:
        group = str(signal.get("group") or signal["code"])
        if group in seen_groups:
            continue
        seen_groups.add(group)
        agreeing_negatives.append(str(signal["code"]))
    negative_groups = seen_groups
    comparable = [
        signal["code"] for signal in signals if signal["reading"] is not SignalReading.UNKNOWN
    ]

    if blocked_reason:
        level = Severity.ACTION
    elif len(negative_groups) >= NEGATIVE_SIGNALS_FOR_ATTENTION:
        level = Severity.ATTENTION
    elif readiness is ReadinessReading.STRAINED:
        level = Severity.ATTENTION
    else:
        level = Severity.INFO

    if len(comparable) >= 4:
        uncertainty = DataQuality.HIGH
    elif len(comparable) >= 2:
        uncertainty = DataQuality.MEDIUM
    elif comparable:
        uncertainty = DataQuality.LOW
    else:
        uncertainty = DataQuality.INSUFFICIENT

    return level, uncertainty, agreeing_negatives


def headline_for(level: Severity, negatives: Sequence[str], readiness: ReadinessReading) -> str:
    """Texto curto da avaliacao. Linguagem forte SO no nivel `ACTION`, que e
    o unico em que o modelo de fato bloqueou trabalho direto."""
    if level is Severity.ACTION:
        return "Algum grupo esta bloqueado para trabalho direto pelo modelo de recuperacao."
    if level is Severity.ATTENTION and len(negatives) >= NEGATIVE_SIGNALS_FOR_ATTENTION:
        return f"{len(negatives)} sinais objetivos concordam em apontar recuperacao incompleta."
    if level is Severity.ATTENTION:
        return "A prontidao de parte dos grupos esta reduzida; os sinais objetivos nao contradizem."
    if negatives:
        return "Um sinal isolado fora da sua baseline - sozinho, nao conclui nada."
    if readiness is ReadinessReading.UNKNOWN:
        return "Sem sessoes recentes suficientes para avaliar recuperacao."
    return "Nada indica recuperacao incompleta nesta janela."


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _rows_or_empty(conn: Any, sql: str, params: tuple[Any, ...], source: str) -> tuple[list[dict[str, Any]], bool]:
    """Uma fonte de sinal ausente (tabela nao criada, integracao nao
    provisionada) NAO derruba a tela: devolve vazio e avisa que a fonte nao
    respondeu, para a limitacao ser declarada em vez de virar zero."""
    try:
        return _rows(conn, sql, params), True
    except Exception as exc:  # noqa: BLE001 - ver docstring
        log_event("dashboard.recovery_signal_source_unavailable", source=source, error=str(exc))
        return [], False


def _split_by_window(
    rows: Sequence[Mapping[str, Any]], window: WindowInfo, column: str
) -> tuple[list[float], list[float]]:
    """Separa os valores de uma coluna em (janela escolhida, janela de
    referencia). Dia sem medida nao vira zero: fica de fora das duas
    listas."""
    current: list[float] = []
    baseline: list[float] = []
    for row in rows:
        day = to_date(row.get("local_date"))
        value = to_float(row.get(column))
        if day is None or value is None:
            continue
        if window.date_from <= day <= window.date_to:
            current.append(value)
        elif day < window.date_from:
            baseline.append(value)
    return current, baseline


def _signal(
    spec: SignalSpec,
    *,
    current_values: Sequence[float],
    baseline_values: Sequence[float],
    readiness: ReadinessReading,
    source_available: bool = True,
    extra_limitations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Monta UM sinal a partir dos valores brutos das duas janelas."""
    limitations = list(extra_limitations or [])
    current = statistics.median(current_values) if current_values else None
    baseline, baseline_observations = personal_baseline(baseline_values)

    if baseline is None and current is not None and source_available:
        limitations.append(_baseline_limitation(spec.label, baseline_observations))

    direction = classify_direction(current, baseline, spec)
    reading = classify_reading(direction, spec)
    delta = None if (current is None or baseline is None) else float(current) - float(baseline)

    quality = DataQuality.HIGH if baseline is not None else DataQuality.INSUFFICIENT

    return {
        "code": spec.code,
        "label": spec.label,
        "description": spec.description,
        "current": metric(
            current,
            unit=spec.unit,
            nature=DataNature.OBSERVED,
            version_key=spec.version_key,
            quality=DataQuality.HIGH if current is not None else DataQuality.INSUFFICIENT,
        ),
        "baseline": metric(
            baseline,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key=spec.version_key,
            quality=quality,
        ),
        "delta": metric(
            delta,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key=spec.version_key,
            quality=quality,
        ),
        "direction": direction,
        "reading": reading,
        "agreement": classify_agreement(reading, readiness),
        "observations": len(current_values),
        "baseline_observations": baseline_observations,
        "min_observations": MIN_BASELINE_OBSERVATIONS,
        "source": spec.source,
        "limitations": limitations,
        "group": spec.group,
        "subjective": spec.subjective,
    }


def _readiness_items(
    user_id: int, now_local: Optional[datetime] = None
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    """Prontidao do servico existente. Desde a M20 a leitura custa 3
    statements, entao ela deixou de ser opt-in: e o coracao desta tela.

    Devolve tambem o AJUSTE GLOBAL de prontidao (fator de TSB do
    Intervals.icu) que o proprio modelo acabou de aplicar - o mesmo numero
    que `/analytics/muscle-recovery` expoe na raiz e que a tela legada mostra
    em destaque (M33). Ele sai da MESMA instancia que rodou o overview, que
    desde o M33 memoriza a leitura de `intervals_wellness`: nenhum statement
    novo, e nenhum segundo calculo do fator.

    Falha aqui nao derruba a resposta - os sinais objetivos continuam
    valendo, e a ausencia da prontidao e declarada.

    M76 B3: janela e limite são os do modelo (defaults de
    `get_recovery_overview`: `RECOVERY_LOOKBACK_DAYS`/`RECOVERY_GROUP_LIMIT`),
    os mesmos da geração de ficha - o período da tela não muda a prontidão.

    `now_local` ("Bom para", 2026-10-17): o "agora" na parede do usuário que o
    chamador já leu. Repassado ao overview, mede `hours_since_trained` com o
    MESMO instante que depois ancora `ready_forecast` (padrão de
    `MuscleRecoveryService.user_now`)."""
    from ..muscle_recovery_service import MuscleRecoveryService

    service = MuscleRecoveryService()
    overview_kwargs: dict[str, Any] = {"user_id": user_id}
    if now_local is not None:
        overview_kwargs["now_local"] = now_local
    try:
        items = service.get_recovery_overview(**overview_kwargs)
    except Exception as exc:  # noqa: BLE001 - ver docstring
        log_event("dashboard.readiness_unavailable", user_id=user_id, error=str(exc))
        return [], None

    try:
        adjustment = service.get_global_readiness_adjustment(user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - transparencia nao derruba a tela
        log_event("dashboard.readiness_adjustment_unavailable", user_id=user_id, error=str(exc))
        adjustment = None
    return items, _adjustment_out(adjustment)


def _adjustment_out(adjustment: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Ajuste global (TSB) no formato da tela. `None` continua `None`: sem
    Intervals.icu sincronizado NAO existe ajuste, e "fator 1,0" afirmaria um
    calculo que nao aconteceu."""
    if not adjustment:
        return None
    factor = to_float(adjustment.get("factor"))
    tsb = to_float(adjustment.get("form_tsb"))
    if factor is None:
        return None
    stale = bool(adjustment.get("stale"))
    max_age_days = adjustment.get("max_age_days")
    if stale:
        # M52: existe wellness, mas velho demais - o modelo aplicou fator
        # neutro e a tela avisa de quando e o ultimo dado.
        description = (
            f"O ultimo dado do Intervals.icu tem mais de {max_age_days} dias: o ajuste por fadiga "
            "geral (TSB) foi ignorado e o tempo de recuperacao nao foi alterado."
        )
    else:
        description = (
            "Fadiga geral do Intervals.icu (TSB) multiplica o TEMPO de recuperacao estimado de "
            "todos os grupos; ela nao reclassifica nenhum grupo sozinha."
        )
    return {
        "factor": factor,
        "form_tsb": tsb,
        "wellness_date": to_date(adjustment.get("wellness_date")),
        "stale": stale,
        "max_age_days": int(max_age_days) if max_age_days is not None else None,
        "description": description,
    }


# Quantas series da ULTIMA sessao entram na explicacao do calculo. O
# `VolumeService` ja limita a lista a 5 (`recent_sets_for_display`); repetir o
# teto aqui e so a garantia de que um payload maior la nao engorda este.
MAX_FACTOR_SETS = 5


def _recommendation_out(item: Mapping[str, Any]) -> dict[str, Any]:
    """Recomendacao de series do MODELO (`MuscleRecoveryService.
    _RECOMMENDATIONS`), repassada como esta. A tela nao escolhe numero de
    serie nenhum - a mesma tabela governa a geracao de ficha e o Modo
    Treino, e era ela que so existia em `/recuperacao-muscular` (M33)."""
    recommendation = item.get("recommendation") or {}

    def _int(key: str) -> Optional[int]:
        value = recommendation.get(key)
        return None if value is None else int(value)

    return {
        "direct_sets_min": _int("direct_sets_min"),
        "direct_sets_max": _int("direct_sets_max"),
        "maximum_rpe": _int("maximum_rpe"),
        "allow_failure_on_last_set": (
            None
            if recommendation.get("allow_failure_on_last_set") is None
            else bool(recommendation["allow_failure_on_last_set"])
        ),
        "allow_progression": (
            None
            if recommendation.get("allow_progression") is None
            else bool(recommendation["allow_progression"])
        ),
        "allow_indirect_work": (
            None
            if recommendation.get("allow_indirect_work") is None
            else bool(recommendation["allow_indirect_work"])
        ),
        "volume_multiplier": to_float(recommendation.get("volume_multiplier")),
        "minimum_sets": _int("minimum_sets"),
        "sets_rule": recommendation.get("sets_rule"),
    }


def _factor_set_out(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trained_at": to_datetime(row.get("trained_at")),
        "reps": None if row.get("reps") is None else int(row["reps"]),
        "rpe": to_float(row.get("rpe")),
        "set_type": str(row.get("set_type") or "-"),
        "role": str(row.get("role") or "-"),
        "participation": to_float(row.get("participation")),
        "effort_points": to_float(row.get("effort_points")),
        "repetition_factor": to_float(row.get("repetition_factor")),
        "set_type_factor": to_float(row.get("set_type_factor")),
    }


def _factors_out(item: Mapping[str, Any]) -> dict[str, Any]:
    """De ONDE saiu o numero: os mesmos fatores que `VolumeService` ja
    calcula e que a tela legada mostrava num tooltip (M33).

    Nada aqui e recalculado - nem a dose, nem a penalidade de densidade, nem
    a razao de desempenho. Campo sem dado fica `None`, nunca 0: "nao houve
    serie com RPE" e diferente de "o RPE foi zero"."""
    factors = item.get("factors") or {}
    recent_sets = list(factors.get("recent_sets") or [])[:MAX_FACTOR_SETS]
    notes = [factors["rpe10_isolated_note"]] if factors.get("rpe10_isolated_note") else []
    def _from_factors_or_item(key: str) -> Optional[float]:
        # `or` nao serve aqui: dose 0,0 e um valor, nao ausencia.
        value = factors.get(key)
        return to_float(item.get(key) if value is None else value)

    return {
        "session_dose": _from_factors_or_item("session_dose"),
        "dose_last_72h": _from_factors_or_item("dose_last_72h"),
        "avg_rpe_used": to_float(factors.get("avg_rpe_used")),
        "avg_rpe_window": to_float(item.get("avg_rpe")),
        "density_penalty": to_float(item.get("density_penalty")),
        "signal_penalty": to_float(item.get("signal_penalty")),
        "performance_ratio": to_float(item.get("performance_ratio")),
        "recent_sets": [_factor_set_out(row) for row in recent_sets],
        "notes": notes,
    }


def _ready_out(item: Mapping[str, Any], now_local: datetime, timezone_name: str) -> dict[str, Any]:
    """"Bom para" (2026-10-17): o instante vem do dono da projecao
    (`recovery_projection.ready_forecast`); aqui so se anexa o fuso do
    usuario, para a API devolver um instante que o navegador nao desloca.

    Mesma regra de `_readiness_items`: uma falha aqui nao derruba a tela, a
    visao geral nem o relatorio - o grupo fica "sem previsao"."""
    try:
        forecast = ready_forecast(dict(item), now_local)
    except Exception as exc:  # noqa: BLE001 - ver docstring
        log_event(
            "dashboard.ready_forecast_unavailable",
            muscle_group_id=item.get("muscle_group_id"),
            error=str(exc),
        )
        return {"ready_at": None, "ready_label": None}
    return {
        "ready_at": (
            user_wall_to_instant(forecast.ready_at, timezone_name) if forecast.ready_at is not None else None
        ),
        "ready_label": forecast.label,
    }


def _group_out(item: Mapping[str, Any], *, now_local: datetime, timezone_name: str) -> dict[str, Any]:
    recommendation = item.get("recommendation") or {}
    return {
        "muscle_group_id": item.get("muscle_group_id"),
        "muscle_group": str(item.get("muscle_group") or "-"),
        "readiness_status": str(item.get("readiness_status") or "READY"),
        "guidance": item.get("guidance"),
        "recovery_pct": metric(
            to_float(item.get("recovery_pct")),
            unit="%",
            nature=DataNature.INFERRED,
            version_key="readiness",
        ),
        "estimated_recovery_hours": metric(
            to_float(item.get("estimated_recovery_hours")),
            unit="h",
            nature=DataNature.INFERRED,
            version_key="readiness",
        ),
        "remaining_hours": metric(
            to_float(item.get("remaining_hours")),
            unit="h",
            nature=DataNature.INFERRED,
            version_key="readiness",
        ),
        "hours_since_last_stimulus": metric(
            to_float(item.get("hours_since_trained")),
            unit="h",
            nature=DataNature.OBSERVED,
            version_key="readiness",
        ),
        "session_dose": metric(
            to_float(item.get("session_dose")),
            unit="dose",
            nature=DataNature.DERIVED,
            version_key="readiness",
        ),
        "last_session_at": to_datetime(item.get("last_session_at")),
        "last_trained_local_date": to_date(item.get("last_trained_at")),
        **_ready_out(item, now_local, timezone_name),
        "max_rpe": item.get("max_rpe"),
        "allow_failure": item.get("allow_failure"),
        "volume_multiplier": to_float(recommendation.get("volume_multiplier")),
        "blocking_signal_codes": [
            str(signal.get("code"))
            for signal in (item.get("blocking_signals") or [])
            if signal.get("code")
        ],
        "explanation": list(item.get("explanation") or []),
        # M33: o que so existia em `/recuperacao-muscular`.
        "recommendation": _recommendation_out(item),
        "factors": _factors_out(item),
    }


# ---------------------------------------------------------------------------
# M36 - dia COM treino x dia SEM treino (sono, HRV, FC de repouso)
#
# O recorte existia na tela legada `/treinos`, dentro de "Cardio & Recuperacao
# (Intervals)", vindo de `intervals_correlation`. Ele nunca foi cardio: a
# pergunta e "voce dorme pior e tem HRV pior nos dias em que treina?", que e
# uma pergunta de RECUPERACAO. Aqui ele nasce das linhas que esta tela JA leu
# (sono, wellness e dias de sessao), sem nenhuma consulta nova.
#
# Diferenca de metodo em relacao a tela legada, deliberada: la o "dia de
# treino" dependia de uma sessao WeightTraining do Intervals.icu ter casado
# com uma atividade; aqui e o dia em que EXISTE sessao registrada no proprio
# app. Sem wearable de musculacao o recorte continua existindo.
# ---------------------------------------------------------------------------

# Minimo de dias de cada lado para a comparacao valer alguma coisa. Abaixo
# disso os numeros continuam visiveis (eles sao observacoes reais), mas a
# limitacao declara de quantos dias cada lado e feito.
TRAINING_VS_REST_MIN_DAYS = 3

# Quais sinais tem recorte por dia de treino. Sono e wearable; dor e
# desempenho so existem em dia de treino, entao para eles o recorte seria
# vazio de um lado por construcao - nao entram.
TRAINING_VS_REST_CODES: tuple[str, ...] = (
    "sleep_duration",
    "sleep_regularity",
    "hrv",
    "resting_hr",
)

TRAINING_VS_REST_ASSOCIATION_LIMITATION = (
    "Dia com treino e dia sem treino sao dois grupos observados, nao um experimento: a diferenca "
    "e associacao, nunca causa."
)

TRAINING_VS_REST_SAME_DAY_LIMITATION = (
    "Sono, HRV e FC de repouso sao os do MESMO dia civil da sessao - a noite ANTERIOR ao treino e "
    "a noite seguinte caem em dias diferentes."
)

TRAINING_VS_REST_SESSION_SOURCE_LIMITATION = (
    "Dia de treino e o dia com sessao registrada no app (com exercicio lancado); sessao importada "
    "sem exercicio nao conta como dia de treino."
)

TRAINING_VS_REST_NO_TRAINING_LIMITATION = (
    "Nenhum dia de treino nesta janela: nao ha recorte por dia de treino a fazer."
)

TRAINING_VS_REST_NO_REST_LIMITATION = (
    "Todos os dias desta janela tem treino registrado: nao ha dia de descanso para comparar."
)


def _training_days(
    pain_rows: Sequence[Mapping[str, Any]], window: WindowInfo
) -> set[Any]:
    """Dias da JANELA em que houve sessao registrada. Sai das mesmas linhas
    que ja alimentam os sinais de dor - nenhuma consulta nova."""
    days = set()
    for row in pain_rows:
        day = to_date(row.get("local_date"))
        if day is not None and window.date_from <= day <= window.date_to:
            days.add(day)
    return days


def _split_by_training_day(
    rows: Sequence[Mapping[str, Any]], column: str, window: WindowInfo, training_days: set[Any]
) -> tuple[list[float], list[float]]:
    """Separa os valores da JANELA em (dia com treino, dia sem treino). Dia
    sem medida nao entra em lado nenhum - ausencia nunca vira zero."""
    on_training: list[float] = []
    on_rest: list[float] = []
    for row in rows:
        day = to_date(row.get("local_date"))
        value = to_float(row.get(column))
        if day is None or value is None or not (window.date_from <= day <= window.date_to):
            continue
        (on_training if day in training_days else on_rest).append(value)
    return on_training, on_rest


def _training_vs_rest_metric(
    spec: SignalSpec,
    *,
    on_training: Sequence[float],
    on_rest: Sequence[float],
    source_available: bool,
) -> dict[str, Any]:
    """UMA metrica do recorte. O lado de DESCANSO faz o papel de referencia,
    e a comparacao usa a MESMA tolerancia e a mesma leitura dos sinais - nao
    ha um segundo criterio de "diferente" no projeto."""
    training_value = statistics.median(on_training) if on_training else None
    rest_value = statistics.median(on_rest) if on_rest else None
    direction = classify_direction(training_value, rest_value, spec)
    reading = classify_reading(direction, spec)
    delta = (
        None
        if (training_value is None or rest_value is None)
        else float(training_value) - float(rest_value)
    )

    limitations: list[str] = []
    if not source_available:
        limitations.append(f"{spec.label}: a fonte nao respondeu nesta janela.")
    elif training_value is None and rest_value is None:
        limitations.append(f"{spec.label}: nenhuma medida na janela - nada a comparar.")
    elif training_value is None:
        limitations.append(f"{spec.label}: nenhuma medida em dia de treino nesta janela.")
    elif rest_value is None:
        limitations.append(f"{spec.label}: nenhuma medida em dia sem treino nesta janela.")
    elif min(len(on_training), len(on_rest)) < TRAINING_VS_REST_MIN_DAYS:
        limitations.append(
            f"{spec.label}: {len(on_training)} dia(s) com treino e {len(on_rest)} sem treino, "
            f"menos que os {TRAINING_VS_REST_MIN_DAYS} de cada lado que tornam a diferenca "
            "interpretavel."
        )

    quality = (
        DataQuality.HIGH
        if (
            training_value is not None
            and rest_value is not None
            and min(len(on_training), len(on_rest)) >= TRAINING_VS_REST_MIN_DAYS
        )
        else (DataQuality.LOW if delta is not None else DataQuality.INSUFFICIENT)
    )

    return {
        "code": spec.code,
        "label": spec.label,
        "unit": spec.unit,
        "source": spec.source,
        "higher_is_better": spec.higher_is_better,
        "training_days": metric(
            training_value,
            unit=spec.unit,
            nature=DataNature.OBSERVED,
            version_key=spec.version_key,
            quality=DataQuality.HIGH if training_value is not None else DataQuality.INSUFFICIENT,
        ),
        "rest_days": metric(
            rest_value,
            unit=spec.unit,
            nature=DataNature.OBSERVED,
            version_key=spec.version_key,
            quality=DataQuality.HIGH if rest_value is not None else DataQuality.INSUFFICIENT,
        ),
        "delta": metric(
            delta,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key=spec.version_key,
            quality=quality,
        ),
        "direction": direction,
        "reading": reading,
        "training_observations": len(on_training),
        "rest_observations": len(on_rest),
        "min_days": TRAINING_VS_REST_MIN_DAYS,
        "limitations": limitations,
    }


def _training_vs_rest(
    *,
    window: WindowInfo,
    training_days: set[Any],
    series: Mapping[str, tuple[Sequence[Mapping[str, Any]], str, bool]],
) -> dict[str, Any]:
    """Bloco `training_vs_rest` (M36).

    `series` mapeia o codigo do sinal para (linhas, coluna, fonte respondeu).
    As linhas sao EXATAMENTE as que os sinais ja usaram - inclusive o
    fallback de duracao de sono vindo do Intervals.icu, para os dois blocos
    nunca contarem noites diferentes."""
    window_days = (window.date_to - window.date_from).days + 1
    rest_days = max(window_days - len(training_days), 0)

    metrics: list[dict[str, Any]] = []
    for code in TRAINING_VS_REST_CODES:
        if code not in series:
            continue
        rows, column, source_available = series[code]
        on_training, on_rest = _split_by_training_day(rows, column, window, training_days)
        metrics.append(
            _training_vs_rest_metric(
                SIGNAL_SPEC_BY_CODE[code],
                on_training=on_training,
                on_rest=on_rest,
                source_available=source_available,
            )
        )

    limitations: list[str] = []
    if not training_days:
        limitations.append(TRAINING_VS_REST_NO_TRAINING_LIMITATION)
    elif not rest_days:
        limitations.append(TRAINING_VS_REST_NO_REST_LIMITATION)
    limitations.append(TRAINING_VS_REST_SESSION_SOURCE_LIMITATION)
    limitations.append(TRAINING_VS_REST_SAME_DAY_LIMITATION)
    limitations.append(TRAINING_VS_REST_ASSOCIATION_LIMITATION)

    return {
        "training_days": len(training_days),
        "rest_days": rest_days,
        "min_days": TRAINING_VS_REST_MIN_DAYS,
        "metrics": metrics,
        "limitations": limitations,
    }


def build_recovery_signals(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/recovery-signals`."""
    user_id = require_user_id(user_id)
    baseline_start = window.date_from - timedelta(days=BASELINE_WINDOW_DAYS)
    range_params = (user_id, baseline_start, window.date_to)

    sleep_rows, sleep_ok = _rows_or_empty(conn, SLEEP_DAILY_SQL, range_params, "sleep_metrics")
    wellness_rows, wellness_ok = _rows_or_empty(
        conn, WELLNESS_DAILY_SQL, range_params, "intervals_wellness"
    )
    pain_rows, _pain_ok = _rows_or_empty(conn, PAIN_DAILY_SQL, range_params, "exercises")

    # "Agora" na parede do usuario, lido UMA vez: o overview mede
    # `hours_since_trained` com ele e o "Bom para" de cada grupo parte dele.
    now_local = user_now(window.timezone)
    items, global_adjustment = _readiness_items(user_id, now_local=now_local)
    readiness = classify_readiness_reading(items)

    # F2.1: eventos de contexto REAIS (manuais da tabela `context_events` +
    # derivados de troca de ficha/academia). A limitacao declarada passa a
    # descrever o estado em que o contexto esta, em vez de repetir "ainda nao
    # existe" depois que ele passou a existir.
    context_events, context_table_available, context_limitations = load_context_events(
        conn, user_id=user_id, window=window
    )
    limitations: list[str] = [*context_limitations]
    if context_table_available and not context_events:
        limitations.append(CONTEXT_EVENTS_EMPTY_LIMITATION)
    limitations.append(CONTEXT_EVENTS_ASSOCIATION_LIMITATION)
    limitations.append(ASSOCIATION_LIMITATION)
    signals: list[dict[str, Any]] = []

    # --- sono ------------------------------------------------------------
    duration_current, duration_baseline = _split_by_window(sleep_rows, window, "duration_hours")
    # M36: o recorte "dia com treino x dia sem treino" precisa das MESMAS
    # linhas e da MESMA coluna que o sinal de duracao usou (inclusive quando
    # o fallback do Intervals.icu entra), para os dois blocos nunca contarem
    # noites diferentes.
    duration_rows: Sequence[Mapping[str, Any]] = sleep_rows
    duration_column = "duration_hours"
    if not duration_current and not duration_baseline and wellness_rows:
        # Fallback declarado: o Intervals.icu tambem traz duracao de sono.
        hours_rows = [
            {"local_date": row.get("local_date"), "sleep_hours": (to_float(row.get("sleep_seconds")) or 0.0) / 3600.0}
            for row in wellness_rows
            if to_float(row.get("sleep_seconds"))
        ]
        duration_current, duration_baseline = _split_by_window(hours_rows, window, "sleep_hours")
        duration_rows, duration_column = hours_rows, "sleep_hours"
        sleep_source_note = ["Duracao do sono vinda do Intervals.icu (nao ha registro manual na janela)."]
    else:
        sleep_source_note = []

    signals.append(
        _signal(
            SIGNAL_SPEC_BY_CODE["sleep_duration"],
            current_values=duration_current,
            baseline_values=duration_baseline,
            readiness=readiness,
            source_available=sleep_ok,
            extra_limitations=sleep_source_note,
        )
    )
    regularity_current, regularity_baseline = _split_by_window(sleep_rows, window, "regularity_percent")
    signals.append(
        _signal(
            SIGNAL_SPEC_BY_CODE["sleep_regularity"],
            current_values=regularity_current,
            baseline_values=regularity_baseline,
            readiness=readiness,
            source_available=sleep_ok,
        )
    )

    # --- wearable --------------------------------------------------------
    for code, column in (("hrv", "hrv_ms"), ("resting_hr", "resting_hr_bpm"), ("form_tsb", "form_tsb")):
        current_values, baseline_values = _split_by_window(wellness_rows, window, column)
        signals.append(
            _signal(
                SIGNAL_SPEC_BY_CODE[code],
                current_values=current_values,
                baseline_values=baseline_values,
                readiness=readiness,
                source_available=wellness_ok,
            )
        )

    # --- dor -------------------------------------------------------------
    pain_current, pain_baseline = _split_by_window(pain_rows, window, "muscle_pain")
    signals.append(
        _signal(
            SIGNAL_SPEC_BY_CODE["muscle_pain"],
            current_values=pain_current,
            baseline_values=pain_baseline,
            readiness=readiness,
        )
    )
    signals.append(_joint_pain_signal(pain_rows, window, readiness))

    # --- check-in diario (F2.2) ------------------------------------------
    # Relato, nao medida: quatro escores de 1 a 5 no MESMO grupo de
    # concordancia. `soreness` daqui e a dor GLOBAL do dia (inclusive em dia
    # sem treino) e NAO se soma ao `muscle_pain` acima, que e a dor por sessao
    # que o Modo Treino registra - duas perguntas diferentes, dois sinais.
    checkin_rows, checkin_available, checkin_limitations = load_checkins(
        conn, user_id=user_id, date_from=baseline_start, date_to=window.date_to
    )
    limitations.extend(checkin_limitations)
    for field in SCORE_FIELDS:
        current_values, baseline_values = _split_by_window(checkin_rows, window, field)
        signals.append(
            _signal(
                SIGNAL_SPEC_BY_CODE[checkin_signal_code(field)],
                current_values=current_values,
                baseline_values=baseline_values,
                readiness=readiness,
                source_available=checkin_available,
                extra_limitations=(
                    [
                        f"{SCORE_LABELS[field]}: respondida em {answered_days(checkin_rows, field)} "
                        "dia(s) do período lido - dia sem check-in nao entra como zero."
                    ]
                    if checkin_available and checkin_rows and not current_values
                    else []
                ),
            )
        )

    # --- desempenho ------------------------------------------------------
    signals.append(_performance_signal(items, readiness))

    blocked_reason = _intensification_block(items, signals)
    level, uncertainty, negatives = combine_assessment(signals, readiness, blocked_reason)

    if not any(signal["reading"] is not SignalReading.UNKNOWN for signal in signals):
        limitations.append(NO_SIGNAL_LIMITATION)
    if not wellness_rows:
        limitations.append(WEARABLE_MISSING_LIMITATION)
    if not sleep_rows and not duration_current:
        limitations.append(SLEEP_MISSING_LIMITATION)

    explanation = _explanation(signals, items, readiness, level)
    quality = _envelope_quality(signals, items)

    # M36: recorte por dia de treino, sobre as linhas JA lidas acima.
    training_vs_rest = _training_vs_rest(
        window=window,
        training_days=_training_days(pain_rows, window),
        series={
            "sleep_duration": (duration_rows, duration_column, sleep_ok or wellness_ok),
            "sleep_regularity": (sleep_rows, "regularity_percent", sleep_ok),
            "hrv": (wellness_rows, "hrv_ms", wellness_ok),
            "resting_hr": (wellness_rows, "resting_hr_bpm", wellness_ok),
        },
    )

    log_event(
        "dashboard.computed",
        screen="recovery-signals",
        user_id=user_id,
        window=window.kind.value,
        groups=len(items),
        signals_with_reading=sum(
            1 for signal in signals if signal["reading"] is not SignalReading.UNKNOWN
        ),
        level=level.value,
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": quality,
        "limitations": limitations,
        "groups": [_group_out(item, now_local=now_local, timezone_name=window.timezone) for item in items],
        "signals": signals,
        "assessment": {
            "level": level,
            "readiness_reading": readiness,
            "headline": headline_for(level, negatives, readiness),
            "uncertainty": uncertainty,
            "allow_intensification": blocked_reason is None,
            "intensification_blocked_reason": blocked_reason,
            "agreeing_codes": [
                signal["code"] for signal in signals if signal["agreement"] is SignalAgreement.AGREES
            ],
            "diverging_codes": [
                signal["code"] for signal in signals if signal["agreement"] is SignalAgreement.DIVERGES
            ],
            "unavailable_codes": [
                signal["code"]
                for signal in signals
                if signal["agreement"] is SignalAgreement.NO_BASELINE
            ],
            "explanation": explanation,
            "reassess_after_hours": _reassess_after_hours(items),
            # F2.1: eventos REAIS da janela (manuais + derivados). Eles
            # aparecem ao lado da avaliacao, mas NAO entram em `level`,
            # `allow_intensification` nem em nenhuma contagem de sinal - o
            # contexto e o que estava acontecendo, nao um sinal a mais.
            "context_events": list(context_events),
        },
        # M36: sono/HRV/FC de repouso em dia COM treino x dia SEM treino. Fica
        # FORA de `signals` de proposito: e um recorte descritivo, e nao entra
        # em `level`, em `allow_intensification` nem em contagem nenhuma de
        # concordancia.
        "training_vs_rest": training_vs_rest,
        # M33: o ajuste global de fadiga (TSB) que o modelo APLICOU no tempo
        # estimado de recuperacao. `None` quando nao ha Intervals.icu
        # sincronizado - nunca "fator 1,0", que afirmaria um calculo que nao
        # aconteceu.
        "global_readiness_adjustment": global_adjustment,
        "baseline_window_days": BASELINE_WINDOW_DAYS,
        "readiness_model": READINESS_MODEL,
    }


def _joint_pain_signal(
    pain_rows: Sequence[Mapping[str, Any]], window: WindowInfo, readiness: ReadinessReading
) -> dict[str, Any]:
    """Dor articular e EVENTO, nao medida continua: a comparacao honesta e
    "aconteceu ou nao na janela", nao uma mediana. Por isso ela nasce sem
    baseline - mas COM leitura, porque uma dor articular relatada e sinal
    desfavoravel por si (e o modelo ja bloqueia trabalho direto no grupo)."""
    spec = SIGNAL_SPEC_BY_CODE["joint_pain"]
    days_with_pain = 0
    baseline_days = 0
    days_with_session = 0
    for row in pain_rows:
        day = to_date(row.get("local_date"))
        if day is None:
            continue
        in_window = window.date_from <= day <= window.date_to
        if in_window:
            days_with_session += 1
        if not bool(to_float(row.get("joint_pain"))):
            continue
        if in_window:
            days_with_pain += 1
        elif day < window.date_from:
            baseline_days += 1

    if not days_with_session:
        # Sem dia de treino na janela nao ha o que relatar: "zero dores" aqui
        # seria afirmar uma observacao que nunca aconteceu.
        reading = SignalReading.UNKNOWN
    else:
        reading = SignalReading.UNFAVORABLE if days_with_pain else SignalReading.NEUTRAL
    return {
        "code": spec.code,
        "label": spec.label,
        "description": spec.description,
        "current": metric(
            float(days_with_pain) if days_with_session else None,
            unit=spec.unit,
            nature=DataNature.OBSERVED,
            version_key="recovery_signals",
            quality=DataQuality.HIGH if days_with_session else DataQuality.INSUFFICIENT,
        ),
        "baseline": metric(
            None,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key="recovery_signals",
            quality=DataQuality.INSUFFICIENT,
        ),
        "delta": metric(
            None,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key="recovery_signals",
            quality=DataQuality.INSUFFICIENT,
        ),
        "direction": (
            SignalDirection.UNKNOWN
            if not days_with_session
            else (SignalDirection.ABOVE if days_with_pain else SignalDirection.IN_LINE)
        ),
        "reading": reading,
        "agreement": classify_agreement(reading, readiness),
        "observations": days_with_pain,
        "baseline_observations": baseline_days,
        "min_observations": MIN_BASELINE_OBSERVATIONS,
        "source": spec.source,
        "limitations": [
            "Dor articular e evento, nao medida continua: nao existe mediana pessoal para comparar."
        ],
        "group": spec.group,
        "subjective": spec.subjective,
    }


def _performance_signal(
    items: Sequence[Mapping[str, Any]], readiness: ReadinessReading
) -> dict[str, Any]:
    """`performance_ratio` ja e uma razao contra a sessao ANTERIOR
    COMPARAVEL de cada exercicio (fonte unica de progresso,
    `exercise_performance` -> `dashboard/comparability`), entao a baseline
    dele e 1,0 por construcao - nao ha o que recalcular aqui.

    Desde 2026-10-06 so exercicios com par comparavel (mesmo exercicio,
    equipamento e convencao) entram; abaixo de 1,0 so quando a regra de
    progresso chama a sessao de REGRESSING. Por isso "Desempenho caiu" deixou
    de aparecer por troca de aparelho, serie acima de 12 reps ou serie a
    menos."""
    spec = SIGNAL_SPEC_BY_CODE["performance"]
    ratios = [
        to_float(item.get("performance_ratio"))
        for item in items
        if to_float(item.get("performance_ratio")) is not None
    ]
    current = statistics.median(ratios) if ratios else None

    if current is None:
        direction = SignalDirection.UNKNOWN
        reading = SignalReading.UNKNOWN
    elif current < PERFORMANCE_DROP_RATIO:
        direction = SignalDirection.BELOW
        reading = SignalReading.UNFAVORABLE
    elif current > 1.0 + spec.tolerance:
        direction = SignalDirection.ABOVE
        reading = SignalReading.FAVORABLE
    else:
        direction = SignalDirection.IN_LINE
        reading = SignalReading.NEUTRAL

    return {
        "code": spec.code,
        "label": spec.label,
        "description": spec.description,
        "current": metric(
            current,
            unit=spec.unit,
            nature=DataNature.ESTIMATED,
            version_key="recovery_signals",
            quality=DataQuality.MEDIUM if current is not None else DataQuality.INSUFFICIENT,
        ),
        "baseline": metric(
            1.0 if current is not None else None,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key="recovery_signals",
            quality=DataQuality.MEDIUM if current is not None else DataQuality.INSUFFICIENT,
        ),
        "delta": metric(
            None if current is None else current - 1.0,
            unit=spec.unit,
            nature=DataNature.DERIVED,
            version_key="recovery_signals",
            quality=DataQuality.MEDIUM if current is not None else DataQuality.INSUFFICIENT,
        ),
        "direction": direction,
        "reading": reading,
        "agreement": classify_agreement(reading, readiness),
        "observations": len(ratios),
        "baseline_observations": len(ratios),
        "min_observations": 1,
        "source": spec.source,
        "limitations": [
            "e1RM e estimativa (Epley + RIR) e so existe em series com carga, repeticoes e RPE.",
            "So entram exercicios com sessao anterior comparavel (mesmo exercicio, equipamento e "
            "convencao de carga); acima de 12 repeticoes a comparacao e pelas repeticoes emparelhadas.",
        ],
        "group": spec.group,
        "subjective": spec.subjective,
    }


def _intensification_block(
    items: Sequence[Mapping[str, Any]], signals: Sequence[Mapping[str, Any]]
) -> Optional[str]:
    """Quando NAO da para intensificar. A regra e do MODELO, nao da tela:
    `classify_readiness` bloqueia trabalho direto do grupo com dor articular
    ou dor muscular > 6, e `_RECOMMENDATIONS` ja diz `allow_progression:
    False` fora do estado READY."""
    blocked = [
        str(item.get("muscle_group") or "-")
        for item in items
        if str(item.get("readiness_status")) == "NO_DIRECT_WORK"
    ]
    if blocked:
        joint = any(
            "JOINT_PAIN" in [str(signal.get("code")) for signal in (item.get("blocking_signals") or [])]
            for item in items
            if str(item.get("readiness_status")) == "NO_DIRECT_WORK"
        )
        motivo = "dor articular relatada" if joint else "prontidao insuficiente"
        return f"{', '.join(sorted(set(blocked)))}: {motivo} - o modelo bloqueia trabalho direto."

    joint_signal = next((s for s in signals if s["code"] == "joint_pain"), None)
    if joint_signal and joint_signal["reading"] is SignalReading.UNFAVORABLE:
        return "Dor articular relatada na janela - nao e hora de aumentar carga ou volume."
    return None


def _explanation(
    signals: Sequence[Mapping[str, Any]],
    items: Sequence[Mapping[str, Any]],
    readiness: ReadinessReading,
    level: Severity,
) -> list[str]:
    """Por que a leitura combinada deu nisso - em frases, na ordem em que a
    tela mostra."""
    lines: list[str] = []
    agreeing = [s for s in signals if s["agreement"] is SignalAgreement.AGREES]
    diverging = [s for s in signals if s["agreement"] is SignalAgreement.DIVERGES]
    unavailable = [s for s in signals if s["agreement"] is SignalAgreement.NO_BASELINE]

    if items:
        lines.append(
            f"Prontidao calculada para {len(items)} grupo(s) pelo modelo {READINESS_MODEL}, "
            "a partir das series registradas."
        )
    else:
        lines.append("Nenhuma sessao na janela de prontidao: nao ha dose de que se recuperar.")

    if agreeing:
        lines.append(
            "Concordam com a leitura de prontidao: " + ", ".join(s["label"] for s in agreeing) + "."
        )
    if diverging:
        lines.append(
            "Divergem da leitura de prontidao (ficam visiveis, nao sao descartados): "
            + ", ".join(s["label"] for s in diverging)
            + "."
        )
    if unavailable:
        lines.append(
            "Sem baseline suficiente para comparar: " + ", ".join(s["label"] for s in unavailable) + "."
        )
    if level is Severity.INFO and any(
        s["reading"] is SignalReading.UNFAVORABLE for s in signals
    ):
        lines.append(
            "Ha um sinal fora da baseline, mas um sinal isolado nao caracteriza recuperacao ruim."
        )
    return lines


def _reassess_after_hours(items: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Quando vale reavaliar: a maior espera restante entre os grupos ainda
    nao recuperados. `None` quando nao ha nada pendente."""
    remaining = [
        to_float(item.get("remaining_hours")) or 0.0
        for item in items
        if str(item.get("readiness_status")) != "READY"
    ]
    positives = [value for value in remaining if value > 0]
    return round(max(positives), 1) if positives else None


def _envelope_quality(
    signals: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]
) -> DataQuality:
    """Confianca NESTA resposta: prontidao sem sessao nenhuma e sinais sem
    baseline nenhuma nao sustentam conclusao."""
    comparable = sum(1 for signal in signals if signal["reading"] is not SignalReading.UNKNOWN)
    if not items and not comparable:
        return DataQuality.INSUFFICIENT
    if comparable >= 4:
        return DataQuality.HIGH
    if comparable >= 2:
        return DataQuality.MEDIUM
    return DataQuality.LOW


__all__ = [
    "BASELINE_WINDOW_DAYS",
    "MIN_BASELINE_OBSERVATIONS",
    "NEGATIVE_SIGNALS_FOR_ATTENTION",
    "PERFORMANCE_DROP_RATIO",
    "SIGNAL_SPECS",
    "SIGNAL_SPEC_BY_CODE",
    "SLEEP_SIGNAL_GROUP",
    "TRAINING_VS_REST_CODES",
    "TRAINING_VS_REST_MIN_DAYS",
    "SignalSpec",
    "build_recovery_signals",
    "classify_agreement",
    "classify_direction",
    "classify_reading",
    "classify_readiness_reading",
    "combine_assessment",
    "headline_for",
    "personal_baseline",
]
