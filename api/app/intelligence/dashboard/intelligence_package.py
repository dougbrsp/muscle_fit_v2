"""Pacote de inteligencia (F3.2, plano §12, §13, §16, §17).

O que este modulo e: um MONTADOR. Ele pega o que `build_overview` ja calculou
numa unica passada e o achata num objeto fechado, pequeno (<= 40 KB) e
autoexplicativo, destinado a dois consumidores que NAO sao a interface:

1. o prompt de `POST /insights/generate` - que antes recebia um contexto
   montado pelo FRONTEND (JSON solto de quatro endpoints v1/v2) e o dump
   COMPLETO de `rag/markdown`;
2. o Personal Trainer externo, via `GET /analytics/dashboard/intelligence-
   package`.

O que este modulo NAO e, e a razao de ele existir separado: ele nao calcula
metrica. Nenhuma soma, nenhuma media, nenhuma reclassificacao. Todo numero
que sai daqui carrega a `nature`, a `metric_version` e a `data_quality` com
que nasceu no servico que o produziu. A consequencia pratica e o teste de
custo: o pacote emite EXATAMENTE os mesmos statements do overview (o overview
ja lia contexto, grupos e intervalo de treino para alimentar as regras - o
pacote so ganhou um caminho de saida para eles).

Teto de tamanho (`PACKAGE_MAX_BYTES`): 40 KB. O corte, quando acontece, e
DECLARADO em `limitations` com a ordem exata do que saiu. Truncamento
silencioso e a pior falha possivel num pacote para LLM: o modelo responderia
com a mesma confianca sobre um recorte que ninguem escolheu, e nem quem le a
resposta nem quem escreveu o prompt teria como perceber.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Mapping, Optional

from ...domain.body_measurement_sites import SITE_LABELS, WAIST_SITE
from ...observability import log_event
from ...parallel_reads import ConnFactory
from .contracts import (
    DataNature,
    DataQuality,
    TargetSource,
    WindowInfo,
)
from .identity import require_user_id
from .overview_service import build_overview

PACKAGE_VERSION = "intelligence-package-v1"

# 40 KB, como o plano fixou (§16). O numero nao e estetico: e o ponto em que o
# pacote ainda cabe folgadamente na janela de contexto JUNTO do RAG filtrado e
# da pergunta, sem que a parte de dados passe a competir com a parte de
# metodo. Quem aumentar este teto precisa medir o prompt inteiro de novo.
PACKAGE_MAX_BYTES = 40 * 1024

# Tetos por lista, aplicados ANTES da medicao de tamanho. Existem para o caso
# normal nao chegar perto do teto global: cortar por lista e previsivel e
# explicavel; cortar so no fim, por bytes, faz o conteudo do pacote depender
# do tamanho de uma anotacao de texto livre.
MAX_EVIDENCE = 30
MAX_CONTEXT_EVENTS = 30
MAX_MUSCLE_GROUPS = 20
MAX_NOTES_CHARS = 280

# Os quatro numeros acima sao dimensionados JUNTOS com `PACKAGE_MAX_BYTES`:
# com todas as listas cheias e toda anotacao no comprimento maximo, o pacote
# fica em torno de 30 KB - dentro dos 40 KB, sem depender do corte por bytes.
# `tests/unit/test_intelligence_package.py::test_pior_caso_plausivel_cabe_no_
# teto_sem_truncar` e exatamente essa conta, e ele reprova se alguem afrouxar
# um dos tetos sem refazer a soma.

TRUNCATION_ORDER = ("context_events", "evidence", "muscle_groups")


# ---------------------------------------------------------------------------
# Vocabulario fechado de acoes
# ---------------------------------------------------------------------------
# O prompt proibe sugerir qualquer coisa fora desta lista. Cada entrada existe
# porque o produto sabe executa-la E o pacote carrega o numero que a sustenta.
# Nada de dieta, suplemento, exame ou medicacao: o pacote nao mede nada disso,
# entao uma sugestao nessa direcao seria, por construcao, sem evidencia.

ALLOWED_ACTIONS: tuple[dict[str, str], ...] = (
    {
        "code": "ADJUST_WEEKLY_VOLUME",
        "label": "Ajustar volume semanal de um grupo",
        "description": "Aumentar ou reduzir séries semanais de um grupo que está fora da faixa da meta.",
    },
    {
        "code": "ADJUST_FREQUENCY",
        "label": "Ajustar frequência de um grupo",
        "description": "Distribuir o mesmo volume em mais (ou menos) sessões na semana.",
    },
    {
        "code": "ADJUST_LOAD_OR_REPS",
        "label": "Ajustar carga ou repetições de um exercício",
        "description": "Aplicar a regra de progressão do método ao exercício citado na evidência.",
    },
    {
        "code": "SUBSTITUTE_EXERCISE",
        "label": "Substituir um exercício",
        "description": "Trocar por outro exercício do mesmo grupo quando o atual está estagnado ou indisponível.",
    },
    {
        "code": "DELOAD_OR_REST",
        "label": "Reduzir dose ou descansar um grupo",
        "description": "Baixar volume/intensidade de um grupo com sinais de recuperação ruim.",
    },
    {
        "code": "RESUME_TRAINING",
        "label": "Retomar o treino",
        "description": "Voltar à rotina após uma pausa detectada, começando abaixo da última carga registrada.",
    },
    {
        "code": "RECORD_MISSING_DATA",
        "label": "Registrar um dado que falta",
        "description": "Pesagem, RPE, medida ou evento de contexto que hoje impede uma leitura confiável.",
    },
    {
        "code": "FIX_CATALOG_GAP",
        "label": "Corrigir uma lacuna de cadastro",
        "description": "Mapeamento de exercício, contribuição muscular ou convenção de carga ausente.",
    },
    {
        "code": "NO_ACTION",
        "label": "Nenhuma ação",
        "description": "Estado dentro do esperado: dizer isso explicitamente em vez de inventar um ajuste.",
    },
)


# ---------------------------------------------------------------------------
# Achatamento
# ---------------------------------------------------------------------------


def _metric(key: str, label: str, envelope: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Copia um `Metric` da visao geral para `PackageMetricOut`.

    `None` (metrica ausente do bloco) devolve `None` e a chave simplesmente
    nao entra no pacote - diferente de `value: None`, que significa
    "existe e nao e calculavel". Os dois estados precisam continuar
    distinguiveis do lado do modelo."""
    if not envelope:
        return None
    return {
        "key": key,
        "label": label,
        "value": envelope.get("value"),
        "unit": envelope.get("unit"),
        "nature": envelope.get("nature", DataNature.DERIVED),
        "metric_version": str(envelope.get("metric_version") or "unknown"),
        "data_quality": envelope.get("data_quality", DataQuality.HIGH),
    }


_CONSISTENCY_METRICS = (
    ("consistency.expected_sessions", "Sessões esperadas na janela", "expected_sessions"),
    ("consistency.completed_sessions", "Sessões concluídas", "completed_sessions"),
    ("consistency.planned_sessions", "Sessões que seguiram o Modelo", "planned_sessions"),
    ("consistency.unplanned_sessions", "Sessões fora do Modelo", "unplanned_sessions"),
    ("consistency.missed_sessions", "Sessões perdidas", "missed_sessions"),
    ("consistency.rate", "Aderência no período", "rate"),
    ("consistency.cycle_rate", "Aderência no ciclo corrente", "cycle_rate"),
)

_BODY_METRICS = (
    ("body.weight_latest", "Última pesagem", "weight_latest"),
    ("body.weight_ma7_latest", "Peso (média móvel de 7 dias)", "weight_ma7_latest"),
    ("body.weight_ma7_change", "Variação da média móvel na janela", "weight_ma7_change"),
    ("body.weight_ma7_change_pct", "Variação percentual da média móvel", "weight_ma7_change_pct"),
    ("body.weighings_per_week", "Pesagens por semana", "weighings_per_week"),
    ("body.body_fat_percent", "Gordura corporal (bioimpedância)", "body_fat_percent"),
    ("body.muscle_mass_kg", "Massa muscular (bioimpedância)", "muscle_mass_kg"),
    ("body.waist_cm", SITE_LABELS[WAIST_SITE], "waist_cm"),
)


def _plain(
    key: str,
    label: str,
    value: Any,
    *,
    unit: Optional[str],
    nature: DataNature,
    metric_version: str,
    quality: DataQuality,
) -> dict[str, Any]:
    """Metrica do pacote para um numero que NAO nasceu como `Metric` no bloco
    (contagens: quantos exercicios em cada status, quantos grupos prontos).

    Ela continua carregando `nature`/`metric_version`/`data_quality` - a versao
    e a do bloco de origem, nao uma inventada aqui. Uma contagem sem versao
    seria o unico numero do pacote que o modelo nao conseguiria rastrear."""
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "nature": nature,
        "metric_version": metric_version,
        "data_quality": quality,
    }


def collect_metrics(overview: Mapping[str, Any]) -> list[dict[str, Any]]:
    """As metricas do pacote, na ordem em que um humano leria a visao geral.

    Nenhuma delas e recalculada: cada uma e a COPIA de um envelope que
    `build_overview` ja devolveu."""
    blocks = overview.get("blocks", {})
    versions = dict(overview.get("metric_versions", {}))
    metrics: list[dict[str, Any]] = []

    consistency = blocks.get("consistency", {})
    for key, label, field in _CONSISTENCY_METRICS:
        item = _metric(key, label, consistency.get(field))
        if item:
            metrics.append(item)

    progression = blocks.get("progression", {})
    prog_version = versions.get("progression_summary", "unknown")
    prog_quality = progression.get("data_quality", DataQuality.HIGH)
    metrics.append(
        _plain(
            "progression.exercises_total",
            "Exercícios na janela",
            int(progression.get("exercises_total") or 0),
            unit="exercises",
            nature=DataNature.DERIVED,
            metric_version=prog_version,
            quality=prog_quality,
        )
    )
    metrics.append(
        _plain(
            "progression.comparable_total",
            "Exercícios comparáveis (mesma chave de comparabilidade)",
            int(progression.get("comparable_total") or 0),
            unit="exercises",
            nature=DataNature.DERIVED,
            metric_version=prog_version,
            quality=prog_quality,
        )
    )
    for count in progression.get("counts", []) or []:
        status = str(count.get("status"))
        metrics.append(
            _plain(
                f"progression.count.{status}",
                f"Exercícios com status {count.get('label') or status}",
                int(count.get("count") or 0),
                unit="exercises",
                nature=DataNature.DERIVED,
                metric_version=versions.get("exercise_progression_status", prog_version),
                quality=prog_quality,
            )
        )

    priority = blocks.get("priority_muscles", {})
    muscle_version = versions.get("weekly_volume_status", "unknown")
    muscle_quality = priority.get("data_quality", DataQuality.HIGH)
    for key, label, field in (
        ("muscles.groups_total", "Grupos musculares na janela", "groups_total"),
        ("muscles.groups_with_target", "Grupos com meta cadastrada", "groups_with_target"),
    ):
        metrics.append(
            _plain(
                key,
                label,
                int(priority.get(field) or 0),
                unit="groups",
                nature=DataNature.DERIVED,
                metric_version=muscle_version,
                quality=muscle_quality,
            )
        )
    for key, label, field in (
        ("muscles.below_min", "Grupos abaixo do mínimo", "below_min"),
        ("muscles.above_max", "Grupos acima do máximo", "above_max"),
        ("muscles.frequency_lagging", "Grupos sem estímulo recente", "frequency_lagging"),
    ):
        metrics.append(
            _plain(
                key,
                label,
                len(priority.get(field) or []),
                unit="groups",
                nature=DataNature.DERIVED,
                metric_version=muscle_version,
                quality=muscle_quality,
            )
        )

    body = blocks.get("body", {})
    for key, label, field in _BODY_METRICS:
        item = _metric(key, label, body.get(field))
        if item:
            metrics.append(item)

    recovery = blocks.get("recovery", {})
    recovery_version = versions.get("recovery_signals", "unknown")
    recovery_quality = recovery.get("data_quality", DataQuality.HIGH)
    for key, label, field, unit in (
        ("recovery.groups_ready", "Grupos prontos", "groups_ready", "groups"),
        ("recovery.groups_reduced_dose", "Grupos em dose reduzida", "groups_reduced_dose", "groups"),
        ("recovery.groups_no_direct_work", "Grupos sem trabalho direto", "groups_no_direct_work", "groups"),
        ("recovery.signals_agreeing", "Sinais concordantes", "signals_agreeing", "signals"),
        ("recovery.signals_diverging", "Sinais divergentes", "signals_diverging", "signals"),
        ("recovery.signals_unavailable", "Sinais indisponíveis", "signals_unavailable", "signals"),
    ):
        metrics.append(
            _plain(
                key,
                label,
                int(recovery.get(field) or 0),
                unit=unit,
                nature=DataNature.INFERRED,
                metric_version=recovery_version,
                quality=recovery_quality,
            )
        )

    gap = overview.get("_training_gap") or {}
    if gap.get("days_since_last_session") is not None:
        metrics.append(
            _plain(
                "training.days_since_last_session",
                "Dias desde a última sessão",
                int(gap["days_since_last_session"]),
                unit="days",
                nature=DataNature.OBSERVED,
                metric_version=versions.get("adherence", "unknown"),
                quality=DataQuality.HIGH,
            )
        )

    metrics.append(
        _plain(
            "data_quality.issues",
            "Itens abertos de qualidade de dado",
            int(overview.get("data_quality_issues") or 0),
            unit="issues",
            nature=DataNature.DERIVED,
            metric_version=versions.get("data_quality", "unknown"),
            quality=DataQuality.HIGH,
        )
    )
    return metrics


def collect_baselines(overview: Mapping[str, Any]) -> list[dict[str, Any]]:
    """O "antes" contra o qual cada variacao do pacote faz sentido.

    Vem de tres lugares que a visao geral ja tinha: a meta de cada grupo
    prioritario (faixa min/max), a primeira media movel de peso da janela
    (`weight_ma7_change` e a diferenca contra ela) e o numero de exposicoes
    por exercicio que a classificacao de progressao usou. Nenhum deles e
    calculado aqui."""
    blocks = overview.get("blocks", {})
    versions = dict(overview.get("metric_versions", {}))
    baselines: list[dict[str, Any]] = []
    window_label = _window_label(overview.get("window"))

    priority = blocks.get("priority_muscles", {})
    seen: set[str] = set()
    for bucket in ("below_min", "above_max", "frequency_lagging"):
        for item in priority.get(bucket) or []:
            name = str(item.get("muscle_group"))
            if name in seen:
                continue
            seen.add(name)
            for field, suffix, label in (
                ("target_min", "min", "mínimo semanal"),
                ("target_max", "max", "máximo semanal"),
            ):
                envelope = item.get(field) or {}
                baselines.append(
                    {
                        "key": f"muscles.{name}.target_{suffix}",
                        "label": f"Meta de {name} ({label})",
                        "basis": "muscle_group_target",
                        "window": "week",
                        "value": envelope.get("value"),
                        "unit": envelope.get("unit"),
                        "nature": envelope.get("nature", DataNature.PLANNED),
                    }
                )

    body = blocks.get("body", {})
    latest = (body.get("weight_ma7_latest") or {}).get("value")
    change = (body.get("weight_ma7_change") or {}).get("value")
    if latest is not None and change is not None:
        baselines.append(
            {
                "key": "body.weight_ma7_first",
                "label": "Média móvel de peso no início da janela",
                "basis": "personal_baseline_window",
                "window": window_label,
                "value": round(float(latest) - float(change), 2),
                "unit": (body.get("weight_ma7_latest") or {}).get("unit"),
                "nature": DataNature.DERIVED,
            }
        )

    progression = blocks.get("progression", {})
    if progression:
        baselines.append(
            {
                "key": "progression.exposures_per_exercise",
                "label": "Exposições lidas por exercício na classificação",
                "basis": "previous_exposure",
                "window": window_label,
                "value": int(progression.get("exposures_per_exercise") or 0),
                "unit": "exposures",
                "nature": DataNature.DERIVED,
            }
        )

    adherence_version = versions.get("adherence")
    consistency = blocks.get("consistency", {})
    if adherence_version and consistency.get("cycle_length_days"):
        baselines.append(
            {
                "key": "consistency.cycle_length_days",
                "label": "Tamanho do ciclo do Modelo atribuído",
                "basis": "assigned_template",
                "window": "cycle",
                "value": int(consistency["cycle_length_days"]),
                "unit": "days",
                "nature": DataNature.PLANNED,
            }
        )
    return baselines


def collect_evidence(overview: Mapping[str, Any], *, window: WindowInfo) -> list[dict[str, Any]]:
    """Todo par antes/depois que uma regra deterministica produziu, cada um com
    uma data que o modelo pode CITAR.

    A ordem e a dos insights (severidade e confianca ja decididas por
    `insight_rules`), entao um corte pela cauda tira sempre o MENOS severo
    primeiro.

    O ponto delicado e `observed_at`. Metade das chamadas de `_evidence(...)`
    em `insight_rules.py` (12 de 26, medido) NAO informa a data, e nao informa
    de proposito: elas sao agregados de JANELA (`weekly_effective_sets` de um
    grupo, `working_sets_total`, `progress_share`) ou constantes de PLANO
    (`target_min_weekly_sets`). Um agregado de 28 dias nao tem um dia.

    Repassar esse `None` adiante - que foi o que este montador fez ate o CI do
    #241 reprovar - entrega ao modelo uma evidencia que o proprio prompt o
    proibe de citar. Descartar seria pior: `weekly_effective_sets[grupo]` e
    justamente a evidencia central do alerta mais comum do produto. E inventar
    um dia unico para um agregado seria o pior dos tres - falso, com aparencia
    de precisao.

    Entao a data da janela entra, MARCADA como tal (`observed_scope="window"`,
    `observed_from`), e o prompt tem duas formas de citacao, uma para cada
    alcance. Nada de `None` em silencio, nada inventado."""
    evidence: list[dict[str, Any]] = []
    for insight in overview.get("_insights", []) or []:
        for item in insight.get("evidence", []) or []:
            observed_at = item.get("observed_at")
            pontual = observed_at is not None
            evidence.append(
                {
                    "insight_code": str(insight.get("code")),
                    "severity": insight.get("severity"),
                    "metric": str(item.get("metric")),
                    "from_value": item.get("from_value"),
                    "to_value": item.get("to_value"),
                    "unit": item.get("unit"),
                    "nature": item.get("nature", DataNature.OBSERVED),
                    "observed_at": observed_at if pontual else window.date_to,
                    "observed_scope": "point" if pontual else "window",
                    "observed_from": None if pontual else window.date_from,
                }
            )
    return evidence


def collect_muscle_groups(overview: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Grupos da janela, do mais carregado para o menos.

    Esta lista tem uma segunda funcao alem de informar o modelo: os NOMES
    daqui sao o filtro de `rag_chunking.select_rag_text`. Um grupo que some
    daqui some do RAG - por isso o corte por tamanho e o ULTIMO da ordem de
    truncamento."""
    items = overview.get("_muscle_items") or []
    rows: list[dict[str, Any]] = []
    for item in items:
        realized = item.get("realized") or {}
        frequency = item.get("frequency") or {}
        rows.append(
            {
                "muscle_group_id": int(item["muscle_group_id"]),
                "muscle_group": str(item["muscle_group"]),
                "status": str(item.get("status")),
                "weekly_effective_sets": (realized.get("weekly_effective_sets") or {}).get("value"),
                "direct_sessions": (frequency.get("direct_sessions") or {}).get("value"),
                "hours_since_last_stimulus": (item.get("hours_since_last_stimulus") or {}).get("value"),
            }
        )
    rows.sort(key=lambda row: (-(row["weekly_effective_sets"] or 0.0), row["muscle_group"]))
    return rows


def collect_context_events(overview: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Eventos de contexto da janela (manuais + derivados), do mais recente ao
    mais antigo.

    Contexto e ASSOCIACAO, nunca causa - e o prompt repete isso. Aqui a unica
    transformacao e o corte da anotacao livre em `MAX_NOTES_CHARS`: texto de
    usuario e a unica parte do pacote com tamanho imprevisivel, e ela nao pode
    ser o que empurra uma metrica para fora do teto."""
    events: list[dict[str, Any]] = []
    for row in overview.get("_context_events", []) or []:
        event = dict(row)
        notes = event.get("notes")
        if isinstance(notes, str) and len(notes) > MAX_NOTES_CHARS:
            event["notes"] = notes[:MAX_NOTES_CHARS] + "…"
        events.append(event)
    # `load_context_events` ja devolve ordenado (`context_service._sort_key`);
    # aqui so invertemos para o mais RECENTE vir primeiro - assim o corte pela
    # cauda tira o evento mais antigo, que e o menos relevante para a pergunta.
    # Reordenar por conta propria seria arriscar comparar `date` com `str`
    # dependendo do backend.
    events.reverse()
    return events


# ---------------------------------------------------------------------------
# Tamanho
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, WindowInfo):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    return str(value)


def measure_bytes(payload: Mapping[str, Any]) -> int:
    """Tamanho do pacote na serializacao COMPACTA (sem espaco, sem escapar
    acento) - a mesma que o transporte usa e a mesma que o teste mede. Medir
    com `indent=2` inflaria o numero em ~30% e o teto passaria a punir
    formatacao, nao conteudo."""
    return len(json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def enforce_size(payload: dict[str, Any], *, max_bytes: int = PACKAGE_MAX_BYTES) -> dict[str, Any]:
    """Corta o pacote ate ele caber, na ordem de `TRUNCATION_ORDER`, e DECLARA
    cada corte em `limitations`.

    A ordem nao e arbitraria: eventos de contexto sao o item mais volumoso e o
    menos estrutural (associacao, nunca causa); evidencia vem depois, cortada
    pela cauda (menos severa primeiro); grupos musculares sao os ultimos
    porque eles tambem filtram o RAG. Metricas e baselines NUNCA sao cortados -
    se um dia o pacote nao couber so com eles, o certo e reduzir a janela ou
    rever o teto, nao entregar meia leitura.

    Se mesmo apos todos os cortes o pacote nao couber, ele e devolvido assim
    mesmo, com a limitacao dizendo isso. Estourar 40 KB e um problema; mentir
    que coube e pior."""
    removed: list[str] = []
    for field in TRUNCATION_ORDER:
        while measure_bytes(payload) > max_bytes and payload.get(field):
            payload[field] = payload[field][:-1]
            removed.append(field)
    if not removed:
        return payload

    payload["truncated"] = True
    counts: dict[str, int] = {}
    for field in removed:
        counts[field] = counts.get(field, 0) + 1
    detail = "; ".join(f"{field}: {count} item(ns)" for field, count in counts.items())
    payload["limitations"] = [
        *payload.get("limitations", []),
        f"Pacote truncado no teto de {max_bytes // 1024} KB, na ordem "
        f"{' > '.join(TRUNCATION_ORDER)} — removidos {detail}. Nenhuma métrica ou baseline foi cortada.",
    ]
    if measure_bytes(payload) > max_bytes:
        payload["limitations"].append(
            f"Mesmo após o truncamento o pacote tem {measure_bytes(payload)} bytes, acima do teto de {max_bytes}."
        )
    return payload


def _window_label(window: Any) -> str:
    if isinstance(window, WindowInfo):
        return window.kind.value
    return str((window or {}).get("kind", "custom"))


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def package_from_overview(
    overview: Mapping[str, Any], *, question: Optional[str] = None, max_bytes: int = PACKAGE_MAX_BYTES
) -> dict[str, Any]:
    """Monta o pacote a partir de um `build_overview` JA executado.

    Separado de `build_intelligence_package` de proposito: o endpoint e o
    `insights_core` chamam a versao que abre a conexao, e os testes (e um dia
    um segundo consumidor que ja tenha o overview em maos) montam o pacote sem
    banco nenhum."""
    window = overview["window"]
    metrics = collect_metrics(overview)
    baselines = collect_baselines(overview)
    evidence = collect_evidence(overview, window=window)[:MAX_EVIDENCE]
    muscle_groups = collect_muscle_groups(overview)[:MAX_MUSCLE_GROUPS]
    context_events = collect_context_events(overview)[:MAX_CONTEXT_EVENTS]

    limitations = list(overview.get("limitations", []))
    limitations.extend(overview.get("_context_limitations", []) or [])
    if not overview.get("_context_events_available", True):
        limitations.append(
            "Eventos de contexto registrados manualmente não estão disponíveis nesta base; "
            "os derivados (troca de ficha, troca de academia) continuam presentes."
        )
    limitations.append(
        "Este pacote não contém série a série nem histórico completo: ele é o recorte agregado "
        "da janela. Toda conclusão precisa citar uma evidência daqui, com a data."
    )

    payload: dict[str, Any] = {
        "window": window,
        "metric_versions": dict(overview.get("metric_versions", {})),
        "data_quality": overview["data_quality"],
        "limitations": limitations,
        "question": (question or None),
        "timezone": overview.get("timezone") or _window_timezone(overview["window"]),
        "package_version": PACKAGE_VERSION,
        "metrics": metrics,
        "baselines": baselines,
        "evidence": evidence,
        "context_events": context_events,
        "muscle_groups": muscle_groups,
        "data_quality_overall": overview["data_quality_overall"],
        "allowed_actions": [dict(action) for action in ALLOWED_ACTIONS],
        "truncated": False,
    }
    payload = enforce_size(payload, max_bytes=max_bytes)
    payload["size_bytes"] = measure_bytes(payload)
    return payload


def _window_timezone(window: Any) -> str:
    if isinstance(window, WindowInfo):
        return window.timezone
    return str((window or {}).get("timezone", "UTC"))


def build_intelligence_package(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    now_local: datetime,
    question: Optional[str] = None,
    training_type_id: Optional[int] = None,
    training_type_source: TargetSource = TargetSource.FALLBACK_GENERIC,
    max_bytes: int = PACKAGE_MAX_BYTES,
    conn_factory: Optional[ConnFactory] = None,
) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/intelligence-package`.

    UMA chamada a `build_overview` e nada mais - e essa e a afirmacao que o
    teste de custo trava no dataset pequeno. O pacote e sempre do usuario de
    `user_id`; nao ha parametro para ler outro (ver o cabecalho do endpoint em
    `analytics_dashboard.py` para o porque de isso ser estrutural, e nao uma
    checagem)."""
    owner = require_user_id(user_id)
    overview = build_overview(
        conn,
        user_id=owner,
        window=window,
        now_local=now_local,
        training_type_id=training_type_id,
        training_type_source=training_type_source,
        conn_factory=conn_factory,
    )
    payload = package_from_overview(overview, question=question, max_bytes=max_bytes)
    log_event(
        "intelligence_package.built",
        user_id=owner,
        window=window.kind.value,
        days=window.days,
        size_bytes=payload["size_bytes"],
        truncated=payload["truncated"],
        metrics=len(payload["metrics"]),
        evidence=len(payload["evidence"]),
        muscle_groups=len(payload["muscle_groups"]),
        has_question=bool(question),
    )
    return payload


def rag_target_groups(payload: Mapping[str, Any]) -> list[str]:
    """Nomes dos grupos do pacote, na ordem, para `select_rag_text`.

    E o unico lugar que traduz pacote -> filtro de RAG. Antes da F3.2 nao
    havia traducao nenhuma: `/insights/generate` injetava o dump inteiro de
    `rag/markdown` em toda pergunta, de qualquer assunto."""
    return [str(group["muscle_group"]) for group in payload.get("muscle_groups", []) if group.get("muscle_group")]


__all__ = [
    "ALLOWED_ACTIONS",
    "MAX_CONTEXT_EVENTS",
    "MAX_EVIDENCE",
    "MAX_MUSCLE_GROUPS",
    "PACKAGE_MAX_BYTES",
    "PACKAGE_VERSION",
    "TRUNCATION_ORDER",
    "build_intelligence_package",
    "collect_baselines",
    "collect_context_events",
    "collect_evidence",
    "collect_metrics",
    "collect_muscle_groups",
    "enforce_size",
    "measure_bytes",
    "package_from_overview",
    "rag_target_groups",
]
