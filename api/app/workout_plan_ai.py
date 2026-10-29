from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time as time_cls
from typing import Any

from . import equipment_recommender, prompt_store, services
from .db import transaction
from .intelligence.muscle_recovery_service import MuscleRecoveryService
from .intelligence.recovery_projection import project_recovery_items
from .openai_client import OpenAIGenerationError
from .openai_client import call_openai as _call_openai_shared

logger = logging.getLogger(__name__)

# `OPENAI_MODEL_PLAN` e a env var que o Terraform publica nas Lambdas
# (infra/lambda_api_core.tf, infra/lambda_ai_generators.tf) e que NUNCA era
# lida: o codigo so olhava `OPENAI_WORKOUT_PLAN_MODEL`, que ninguem define, e
# caia sempre no default daqui. Corrigido no PR-16 (secao 7.1, passo 6):
# `OPENAI_WORKOUT_PLAN_MODEL` continua vencendo (override local de quem ja usa),
# depois `OPENAI_MODEL_PLAN`, depois o default. O default do Terraform foi
# alinhado a este mesmo valor no mesmo PR, para o modelo EFETIVO em producao
# continuar exatamente o que era antes desta leitura passar a valer.
OPENAI_MODEL = (
    os.environ.get("OPENAI_WORKOUT_PLAN_MODEL")
    or os.environ.get("OPENAI_MODEL_PLAN")
    or "gpt-5.4-mini"
)


class WorkoutPlanGenerationError(OpenAIGenerationError):
    pass


# Limite do catalogo no contexto do caminho sem academia - o mesmo que a
# antiga chamada `GET /exercise-mapping-catalog?limit=500&active_only=true`.
EXERCISE_CATALOG_CONTEXT_LIMIT = 500


def _load_gyms() -> list[dict[str, Any]]:
    """Academias ativas, no formato de `GET /gyms`, lidas direto do banco.

    SEG-04: antes era um self-call HTTP (`_internal_get("/gyms")`) para a URL
    publica do API Gateway com o token mestre. Mesma funcao de servico da
    rota (`gyms.list_gym_items`), entao o payload e o mesmo. Import tardio:
    `gyms` puxa o router inteiro e nao precisa pesar no import deste modulo."""
    from .gyms import list_gym_items

    with transaction() as conn:
        return list_gym_items(conn, active_only=True)


def _load_exercise_catalog() -> list[dict[str, Any]]:
    """Catalogo canonico ativo, no formato de `GET /exercise-mapping-catalog`,
    lido direto do banco (SEG-04, mesmo motivo de `_load_gyms`)."""
    from .exercise_mapping_catalog import query_mapping_catalog

    with transaction() as conn:
        return query_mapping_catalog(
            conn, active_only=True, limit=EXERCISE_CATALOG_CONTEXT_LIMIT
        )["items"]


def _trim_recent_exercise_logs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enxuga cada item de recent_exercise_logs (list_exercise_logs,
    services.py:692-772) para so os campos realmente lidos pelo motor de
    progressao legado (removido no M76 Lote 1a; hoje so o prompt le):
    mapping_id, original_name, equipment_name e sets
    reduzido a {reps, weight_kg, rpe} (sem set_number, nao usado no calculo).
    Todos os demais campos (exercise_id, activity_id, external_id,
    local_date, started_at, ended_at, gym_id, gym_name, conducted_by,
    mapped_name, muscle_primary, muscle_secondary, equipment_id,
    equipment_catalog_id, equipment_has_photo, muscle_pain, joint_pain,
    sequence) nunca sao referenciados no pipeline de geracao de ficha —
    so pesavam no prompt da IA sem uso."""
    trimmed = []
    for item in items:
        sets = [
            {"reps": s.get("reps"), "weight_kg": s.get("weight_kg"), "rpe": s.get("rpe")}
            for s in (item.get("sets") or [])
        ]
        trimmed.append(
            {
                "mapping_id": item.get("mapping_id"),
                "original_name": item.get("original_name"),
                "equipment_name": item.get("equipment_name"),
                "sets": sets,
            }
        )
    return trimmed


def _trim_high_rpe_sets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enxuga cada item de high_rpe_sets (services.py::high_rpe_sets) para so
    original_name/local_date/reps/weight_kg/rpe — nenhum consumidor do
    pipeline de geracao de ficha le activity_name/mapped_name/set_number,
    so o texto do prompt (referencia generica a 'series de alto RPE')."""
    return [
        {
            "original_name": item.get("original_name"),
            "local_date": item.get("local_date"),
            "reps": item.get("reps"),
            "weight_kg": item.get("weight_kg"),
            "rpe": item.get("rpe"),
        }
        for item in items
    ]


def _trim_muscle_recovery(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove factors/activity_dose_history de cada item de muscle_recovery —
    campos produzidos por volume_service.py para auditoria/consumidores como
    /analytics/muscle-recovery-consecutive-check, nunca lidos em
    workout_plan_ai.py/abc_workout_ai.py. Todos os demais campos (incluindo
    os novos da reforma de volume: direct_sets, stimulus_equivalent_sets
    etc.) sao preservados intactos."""
    return [
        {k: v for k, v in item.items() if k not in ("factors", "activity_dose_history")}
        for item in items
    ]


_EXERCISE_CATALOG_DROP_FIELDS = {
    "created_at",
    "updated_at",
    "mapping_status",
    "source",
    "is_active",
    "primary_group_id",
    "secondary_group_id",
}


def _trim_exercise_catalog(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove metadados de auditoria/administracao (created_at, updated_at,
    mapping_status, source, is_active, primary_group_id, secondary_group_id)
    de cada item do catalogo — nao usados na escolha de exercicio pela IA,
    que so precisa dos campos descritivos (nome, equipamento, biomecanica,
    grupos por nome, palavras-chave, descricao)."""
    return [
        {k: v for k, v in item.items() if k not in _EXERCISE_CATALOG_DROP_FIELDS}
        for item in items
    ]


# Campos ADITIVOS do PR-08 nos itens de recomendacao (estrelas, seguranca).
# Nenhum motor de geracao atual os le - os motores novos (PR-12/13) vao
# consumir o recomendador direto. Ficam fora do contexto para o prompt dos
# motores legados (LLM) continuar com o mesmo formato/tamanho de antes.
_RECOMMENDATION_FIELDS_OUT_OF_PROMPT = (
    "gym_equipment_id",
    "user_preference_stars",
    "last_used_by_user_at",
    "max_safe_rpe",
    "failure_allowed",
    "allowed_advanced_techniques",
    "safety_source",
)


def _trim_gym_equipment_recommendation(recommendation: dict[str, Any]) -> dict[str, Any]:
    def trim_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {key: value for key, value in item.items() if key not in _RECOMMENDATION_FIELDS_OUT_OF_PROMPT}

    def trim_group(group: dict[str, Any]) -> dict[str, Any]:
        return {
            "has_equipment_for_group": group.get("has_equipment_for_group"),
            "recommended": trim_item(group.get("recommended")),
            "alternatives": [trim_item(item) for item in group.get("alternatives") or []],
        }

    return {
        "ok": True,
        "gym_id": recommendation.get("gym_id"),
        "groups": {name: trim_group(group) for name, group in (recommendation.get("groups") or {}).items()},
        "groups_by_id": {key: trim_group(group) for key, group in (recommendation.get("groups_by_id") or {}).items()},
    }


def gather_ficha_context(gym_id: int | None, user_id: int) -> dict[str, Any]:
    """Reune os mesmos dados que o agente Telegram/OpenClaw consulta ao montar
    uma ficha (ver workspace/AGENTS.md secao 5): series recentes, series de
    alto RPE, recuperacao muscular, catalogo Amazfit/Zepp e, se uma academia
    for informada, a recomendacao de equipamento (menos usado recentemente)
    para todos os grupos musculares de uma so vez.

    Cada lista e enxugada (_trim_*) para so os campos efetivamente
    consumidos pelo pipeline de geracao de ficha (workout_plan_ai.py/
    abc_workout_ai.py/abc_workout_template.py) antes de entrar no dict —
    reduz o peso do prompt serializado via json.dumps sem remover nenhum
    dado usado. `recent_activities` (nunca lido neste pipeline) e removido
    por completo. `exercise_catalog` e removido por completo quando gym_id
    e informado, pois gym_equipment_recommendation ja cobre os candidatos de
    exercicio para aquele caso e o catalogo bruto nunca e referenciado.

    Correcao (Fase 4 do vazamento de dados entre usuarios, 2026-09-01):
    `recent_exercise_logs`/`high_rpe_sets`/`muscle_recovery` deixaram de vir
    de `_internal_get` (que sempre usava o token MESTRE, resolvendo como o
    admin "doug" independente de quem pediu a ficha — o vazamento mais grave
    de todos, pois contaminava a propria recomendacao de exercicios/cargas
    de qualquer outro usuario) e passaram a chamar diretamente as funcoes
    Python ja corrigidas nas fases anteriores, filtrando por `user_id`.
    `gyms` continua deliberadamente NAO filtrado (compartilhado entre
    usuarios, fora de escopo desta correcao).

    SEG-04 (2026-10-09): `gyms` e `exercise_catalog` tambem deixaram o
    self-call HTTP com token mestre e sao lidos direto do banco
    (`_load_gyms`/`_load_exercise_catalog`, mesmas funcoes de servico das
    rotas). A geracao de ficha nao depende mais de `API_TOKEN` nem de
    `INTERNAL_API_BASE_URL`."""
    context: dict[str, Any] = {
        "recent_exercise_logs": _trim_recent_exercise_logs(
            services.list_exercise_logs(limit=300, user_id=user_id)
        ),
        "high_rpe_sets": _trim_high_rpe_sets(
            services.high_rpe_sets(min_rpe=9, limit=15, user_id=user_id)
        ),
        "muscle_recovery": _trim_muscle_recovery(
            # Janela e limite: os do modelo (M76 B3), os mesmos da tela.
            MuscleRecoveryService().get_recovery_overview(user_id=user_id)
        ),
        "gyms": _load_gyms(),
    }

    if gym_id:
        # PR-08: o recomendador passou a ranquear POR USUARIO (estrelas +
        # recencia dele). Via self-call HTTP (token mestre = admin) a ficha de
        # qualquer usuario sairia no ranking do admin - mesmo vazamento
        # corrigido acima para historico/recuperacao. Chamada direta em Python
        # com o `user_id` de quem pediu; mesmo payload da rota.
        with transaction() as conn:
            context["gym_equipment_recommendation"] = _trim_gym_equipment_recommendation(
                equipment_recommender.recommend_all(conn, gym_id=gym_id, user_id=user_id)
            )
    else:
        context["exercise_catalog"] = _trim_exercise_catalog(_load_exercise_catalog())

    return context


def resolve_gym_name(gym_id: int | None, gym_name: str | None, gyms: list[dict[str, Any]]) -> str | None:
    """Resolve o nome de exibicao da academia para o prompt: usa `gym_name`
    se vier preenchido (compatibilidade com quem ja manda o nome), senao
    procura `gym_id` na lista de academias ja carregada em
    `context["gyms"]` (gather_ficha_context) e usa o `name` de la. Evita o
    prompt afirmar "academia nao informada" quando na verdade gym_id foi
    passado e equipamento/recomendacoes especificos da academia estao sendo
    usados (bug real corrigido aqui: o app cliente normalmente so envia
    gym_id, nao gym_name, e o texto do prompt so olhava gym_name)."""
    if gym_name:
        return gym_name
    if gym_id is None:
        return None
    for gym in gyms:
        if gym.get("id") == gym_id:
            return gym.get("name")
    return None


def project_recovery_for_date(
    items: list[dict[str, Any]],
    plan_date: str | None,
    plan_time: str | None = None,
    *,
    user_id: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """A recuperacao muscular retornada pela API e um snapshot de 'agora'. Se o
    plano e para uma data futura, projeta a recuperacao esperada para aquela
    data usando as mesmas funcoes puras do modelo v2 (fullbody-no-reps-v1) do
    backend (app/intelligence/muscle_recovery_service.py), em vez de
    reimplementar aqui uma copia independente da formula.

    Diferente do modelo v1 (fadiga residual exponencial acumulando entre
    sessoes - substituido por completo), a projecao aqui e so: (a) o tempo
    decorrido total (ja passado + ate a data do plano) comparado com as horas
    estimadas da sessao mais recente daquele grupo (session_dose), e (b) a
    densidade de 72h, que zera quando a data do plano esta alem de 72h no
    futuro (as sessoes atuais ja terao saido da janela de 72h nessa data,
    assumindo que nenhum novo treino aconteca entre agora e a data do plano).
    Retorna um gradiente de 3 niveis (readiness_status), nao mais um corte
    binario >=80%.

    `plan_time` (string "HH:MM[:SS]", opcional): hora pretendida do treino
    naquela data (`workout_plans.plan_time` — ver `WorkoutPlanGenerateIn`).
    Sem isso, o alvo da projecao sempre caia em MEIA-NOITE de `plan_date`
    (`datetime.min.time()`), o que SUBESTIMA as horas decorridas ate o
    treino sempre que ele e planejado de manha (bug real reportado
    2026-08-30: plano para as 6h do dia seguinte contava so ate meia-noite,
    "perdendo" as 6h seguintes de recuperacao e derrubando grupos em
    NO_DIRECT_WORK que ja estariam prontos as 6h). Quando ausente
    (chamador antigo que ainda nao repassa a hora), mantem o comportamento
    historico de meia-noite.

    `now`/`user_id`: o "agora" da projecao e a hora LOCAL do usuario
    (`MuscleRecoveryService.user_now`), a mesma referencia de
    `hours_since_trained` e de `plan_date`/`plan_time`. `datetime.now()` era o
    relogio UTC da Lambda e encolhia a antecedencia em 3h (UTC-3). Sem
    `user_id`, vale o fuso default - o mesmo que o retrato usa nesse caso."""
    if now is None:
        now = MuscleRecoveryService().user_now(user_id)
    if plan_date:
        try:
            plan_date_obj = date.fromisoformat(plan_date)
            time_of_day = datetime.min.time()
            if plan_time:
                try:
                    time_of_day = time_cls.fromisoformat(plan_time)
                except ValueError:
                    time_of_day = datetime.min.time()
            target = datetime.combine(plan_date_obj, time_of_day)
        except ValueError:
            target = now
    else:
        target = now

    # PR-09 (G3): a projecao em si vive em
    # app/intelligence/recovery_projection.py (funcao pura, testada sem
    # banco). Antes, qualquer alvo a frente (inclusive hoje mais tarde com
    # plan_time) zerava joint_pain/muscle_pain e os sinais de performance,
    # liberando para treino direto um grupo com dor articular. Politica
    # atual: dor persiste; sinais de performance so resetam com alvo >= 72h;
    # status/percentual reclassificados e reconciliados apos projetar.
    return project_recovery_items(items, now, target)


def _recent_fullbody_plan_ids(
    gym_id: int | None, limit_plans: int, cur, plan_type: str | None = "full_body", user_id: int | None = None
) -> list[int]:
    """`plan_type` opcional (aditivo, feature "Modelos de Ficha de Treino
    Personalizados"): default "full_body" preserva o comportamento original
    de todo chamador existente (Full Body). `plan_type=None` remove o filtro
    de tipo inteiramente — usado pelo novo fluxo de geração via modelo
    (`workout_plan_templates_engine.py`), que quer histórico de variedade de
    QUALQUER tipo de ficha salva (ABC, full_body, ou de outro modelo), não só
    "full_body".

    `user_id` opcional (mesmo padrao de `_recent_abc_plan_ids` em
    abc_workout_ai.py, fix do vazamento de dados entre usuarios): quando
    informado, filtra tambem por `workout_plans.user_id` — sem isso o
    "historico de variedade" de um usuario podia vir de fichas Full Body
    salvas por OUTRO usuario."""
    where_clauses = []
    params: list[Any] = []
    if plan_type is not None:
        where_clauses.append("plan_type = %s")
        params.append(plan_type)
    if gym_id:
        where_clauses.append("gym_id = %s")
        params.append(gym_id)
    if user_id is not None:
        where_clauses.append("user_id = %s")
        params.append(user_id)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit_plans)
    cur.execute(
        f"""
        SELECT id FROM workout_plans
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return [row["id"] for row in cur.fetchall()]


def get_recent_fullbody_exercise_names_by_group(
    gym_id: int | None, limit_plans: int = 4, plan_type: str | None = "full_body", user_id: int | None = None
) -> dict[str, list[str]]:
    """Mesmo padrao de get_recent_abc_exercise_names_by_sequence (LER essa
    funcao em abc_workout_ai.py), mas agrupando por `muscle_group` em vez de
    `sequence`/papel biomecanico ABC (o Full Body nao tem papel fixo por
    posicao entre execucoes, so o grupo muscular e estavel).

    Retorna, por grupo muscular, a lista de nomes de exercicio usados nas
    ultimas `limit_plans` fichas Full Body salvas (mesma academia, quando
    informada), do mais recente para o mais antigo. Usado para dar contexto
    de variedade a IA (evitar repetir sempre o mesmo exercicio no mesmo
    grupo — causa raiz do problema original de repeticao).

    `plan_type` opcional, repassado para `_recent_fullbody_plan_ids` (ver
    docstring lá) — default preserva o comportamento original (só fichas
    "full_body").

    `user_id` opcional (fix do vazamento de dados entre usuarios, mesmo
    padrao de `get_recent_abc_exercise_names_by_sequence`): quando informado,
    filtra tambem por `workout_plans.user_id`."""
    with transaction() as conn:
        with conn.cursor() as cur:
            plan_ids = _recent_fullbody_plan_ids(gym_id, limit_plans, cur, plan_type=plan_type, user_id=user_id)
            if not plan_ids:
                return {}

            placeholders = ",".join(["%s"] * len(plan_ids))
            cur.execute(
                f"""
                SELECT plan_id, muscle_primary, exercise_name
                FROM workout_plan_exercises
                WHERE plan_id IN ({placeholders})
                ORDER BY sequence ASC
                """,
                tuple(plan_ids),
            )
            rows = cur.fetchall()

    recency_order = {plan_id: index for index, plan_id in enumerate(plan_ids)}
    rows.sort(key=lambda row: recency_order.get(row["plan_id"], len(plan_ids)))

    result: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        muscle_group = row.get("muscle_primary")
        name = row.get("exercise_name")
        if not muscle_group or not name:
            continue
        result[muscle_group].append(name)
    return dict(result)


def get_latest_intervals_wellness(user_id: int | None = None) -> dict[str, Any] | None:
    """Registro mais recente de wellness sincronizado do Intervals.icu
    (fitness/fadiga/forma/ramp_rate/readiness/carga diaria) para o prompt de
    geracao de ficha. Retorna None se nao houver nenhum registro ou se a
    consulta falhar (dado e so contexto adicional para a IA, nao deve quebrar
    a geracao da ficha).

    M52: le pela MESMA query do fator global de recuperacao
    (`IntelligenceServiceBase.get_latest_intervals_wellness`, que traz o fuso
    do dono) e marca `stale=True` quando o dado passou da idade maxima
    (`wellness_freshness.WELLNESS_MAX_AGE_DAYS`, no fuso do usuario).
    `format_intervals_wellness_section` omite a secao nesse caso - a IA nao
    pode ajustar volume por uma fadiga de semanas atras.

    `user_id` opcional (Multiusuario Parte 2): sem ele devolve o wellness
    mais recente entre TODOS os usuarios (correto apenas enquanto so o admin
    tiver Intervals.icu configurado)."""
    try:
        from .intelligence import wellness_freshness
        from .intelligence.base import IntelligenceServiceBase

        row = IntelligenceServiceBase().get_latest_intervals_wellness(user_id=user_id)
        if not row:
            return None
        return {**row, "stale": wellness_freshness.is_row_stale(row)}
    except Exception:
        return None


def format_intervals_wellness_section(wellness: dict[str, Any] | None) -> str:
    """Formata o registro mais recente de wellness do Intervals.icu como um
    bloco de texto para o prompt, tolerante a campos ausentes/None.

    M52: wellness marcado `stale` (mais velho que a idade maxima) OMITE a
    secao inteira - string vazia, nem cabecalho."""
    if wellness and wellness.get("stale"):
        return ""
    if not wellness:
        return "\n\n" + _intervals_text(".sem_dados", {})

    def _fmt(value: Any) -> str:
        return "não disponível" if value is None else str(value)

    dados = "\n".join(
        [
            f"- Fitness (CTL): {_fmt(wellness.get('fitness_ctl'))}",
            f"- Fadiga (ATL): {_fmt(wellness.get('fatigue_atl'))}",
            f"- Forma (TSB): {_fmt(wellness.get('form_tsb'))}",
            f"- Ramp rate: {_fmt(wellness.get('ramp_rate'))}",
            f"- Readiness score: {_fmt(wellness.get('readiness_score'))}",
            f"- Carga diária de esforço: {_fmt(wellness.get('daily_effort_load'))}",
        ]
    )
    return "\n\n" + _intervals_text("", {"data": _fmt(wellness.get("wellness_date")), "dados": dados})


# M103 P4 (varredura final): a PROSA da secao do Intervals.icu (cabecalho e a
# instrucao de nao ajustar pelo TSB) mora no repositorio privado,
# `api/privado/prompts/intervals_wellness/<versao>[.parte].md`; aqui ficam o
# ramo (sem dado / velho / fresco) e os dados. A versao so nomeia os templates
# (a secao vai no prompt da ficha legada, `workout_plan_templates_engine`).
INTERVALS_PROMPT_FEATURE_DIR = "intervals_wellness"
INTERVALS_PROMPT_TEMPLATE_VERSION = "intervals-wellness-v1"


def _intervals_text(part: str, values: dict[str, str]) -> str:
    return prompt_store.render(INTERVALS_PROMPT_FEATURE_DIR, f"{INTERVALS_PROMPT_TEMPLATE_VERSION}{part}", values)


_AMAZFIT_FIELD_KEYS = {"amazfit_name", "mapped_name", "mapping_amazfit_name"}


def _strip_amazfit_fields(value: Any) -> Any:
    """Remove recursivamente as chaves que carregam o nome do relogio
    Amazfit/Zepp (amazfit_name/mapped_name/mapping_amazfit_name) de
    qualquer dict/list aninhado, sem mutar o valor original (copia rasa por
    nivel). Usado SOMENTE no que vai para o prompt de IA (motor v1 do
    caminho legado, `workout_plan_templates_engine`) -
    o campo continua existindo normalmente nas funcoes de origem
    (list_exercise_logs, high_rpe_sets etc.), que alimentam outros
    endpoints legitimos fora do prompt. Regra de negocio: amazfit_name e so
    para correlacionar dados com o relogio, nunca deve influenciar
    avaliacoes/geracoes via IA."""
    if isinstance(value, dict):
        return {
            key: _strip_amazfit_fields(val)
            for key, val in value.items()
            if key not in _AMAZFIT_FIELD_KEYS
        }
    if isinstance(value, list):
        return [_strip_amazfit_fields(item) for item in value]
    return value


def call_openai(prompt: str) -> str:
    """Wrapper fino sobre `openai_client.call_openai`, mantendo a assinatura
    de um argumento só (prompt) usada pelos geradores que importam esta
    função e reexportando qualquer
    falha como `WorkoutPlanGenerationError` (subclasse de
    `OpenAIGenerationError`), para não alterar o comportamento observável."""
    try:
        return _call_openai_shared(prompt, model=OPENAI_MODEL, timeout=90, feature="workout_plan")
    except OpenAIGenerationError as exc:
        raise WorkoutPlanGenerationError(str(exc)) from exc


def reconcile_with_gym_catalog(
    exercises: list[dict[str, Any]],
    gym_equipment_recommendation: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Quando uma academia foi informada, a IA as vezes parafraseia ou inventa
    um exercise_name que nao corresponde a nenhum exercicio com equipamento
    cadastrado naquela academia para o grupo escolhido — isso deixava o
    equipamento sem opcao valida no frontend (Select nao encontra o exercicio
    no catalogo da academia). Corrige usando os dados reais de
    gym_equipment_recommendation (mesma fonte usada pelo Select do frontend)
    em vez de confiar no texto livre da IA.

    Tambem cobre o caso em que a academia simplesmente NAO TEM nenhum
    equipamento cadastrado para o grupo (has_equipment_for_group=false):
    nesse caso nao ha candidato nenhum para substituir o exercicio, entao ele
    e removido da ficha (em vez de ficar com exercise_name/equipment vazios).

    Retorna (kept, adjusted, excluded_due_to_no_equipment):
    - kept: exercicios mantidos (iguais ou com nome/equipamento corrigidos in-place);
    - adjusted: subconjunto de kept que precisou ter o nome substituido, para transparencia;
    - excluded_due_to_no_equipment: exercicios removidos por a academia nao ter
      nenhum equipamento cadastrado para aquele grupo muscular.
    """
    if not gym_equipment_recommendation:
        return list(exercises), [], []

    def _normalize_name(value: Any) -> str:
        # NFC + casefold elimina falsos "ajustes" quando o nome ja bate
        # exatamente e so variava por acentuacao/espacamento (ex: nomes vindos
        # com composicao unicode diferente, espacos duplicados etc.), que
        # geravam entradas ruidosas em adjusted_due_to_catalog_mismatch com
        # exercise_name identico a original_exercise_name.
        return unicodedata.normalize("NFC", str(value or "").strip().casefold())

    groups = gym_equipment_recommendation.get("groups", {})
    # Correcao 2026-08-25 (bug de nomenclatura renomeada em `muscle_groups`):
    # `groups` e indexado pelo nome VIVO do grupo (sempre correto, vem do
    # banco a cada chamada) — o que quebrava era `exercise.get("muscle_primary")`
    # vir de uma string hardcoded congelada (ABC_TEMPLATE) ou de um
    # `muscle_group` obsoleto. `groups_by_id` (aditivo, ver `gyms.py`) e
    # indexado por `muscle_group_id` (estavel a renomeacao) e passa a ser
    # PREFERIDO sempre que o exercicio tiver esse campo; o lookup por nome em
    # `groups` continua so como fallback (exercicio legado/edicao manual sem
    # `muscle_group_id`), e esse caminho fica logado para nao falhar
    # silenciosamente.
    # `gym_equipment_recommendation` normalmente chega aqui apos um round-trip
    # HTTP interno (gather_ficha_context -> GET /gym-equipment/recommend-all),
    # entao chaves inteiras de `groups_by_id` viram string no JSON — normaliza
    # de volta para int aqui, no unico ponto de consumo, em vez de em cada
    # lookup.
    groups_by_id = {
        int(key): value for key, value in (gym_equipment_recommendation.get("groups_by_id") or {}).items()
        if str(key).lstrip("-").isdigit()
    }
    kept: list[dict[str, Any]] = []
    adjusted: list[dict[str, Any]] = []
    excluded_due_to_no_equipment: list[dict[str, Any]] = []

    def _resolve_group_info(exercise: dict[str, Any]) -> dict[str, Any] | None:
        group_id = exercise.get("muscle_group_id")
        if group_id is not None and group_id in groups_by_id:
            return groups_by_id.get(group_id)
        group_info = groups.get(exercise.get("muscle_primary"))
        if group_info is not None:
            logger.warning(
                "reconcile_with_gym_catalog: exercicio '%s' resolvido por NOME de grupo "
                "('%s') em vez de muscle_group_id — sem id valido em groups_by_id. "
                "Matching por nome e sensivel a renomeacao de muscle_groups.name.",
                exercise.get("exercise_name"),
                exercise.get("muscle_primary"),
            )
        return group_info

    def _candidate_matches(candidate: dict[str, Any], name: str) -> bool:
        # Match por nome exato do catalogo OU por qualquer apelido
        # cadastrado em exercise_aliases para aquele mapping_id (ex.:
        # ABC_TEMPLATE usa "Pulley Frente", catalogo real tem "Front Pull
        # Down / Lat Pulldown" — aliases sao estritamente aditivos, o
        # match por nome exato continua funcionando como antes).
        if _normalize_name(candidate.get("mapping_exercise_name")) == name:
            return True
        aliases = candidate.get("mapping_aliases") or []
        if any(_normalize_name(alias) == name for alias in aliases):
            return True
        # A IA por vezes escreve o nome do EQUIPAMENTO (ex.: "Puxador alto
        # articulado") em exercise_name em vez do nome do exercicio/movimento
        # (ex.: "Front Pull Down / Lat Pulldown") — mesmo violando a regra do
        # prompt. Sem este match, o exercicio caia no fallback generico e
        # trocava para um equipamento totalmente diferente do pretendido.
        # Reconhecer equipment_name aqui permite canonicalizar para o
        # mapping_exercise_name correto do MESMO equipamento.
        if _normalize_name(candidate.get("equipment_name")) == name:
            return True
        return False

    def _select_match(exercise: dict[str, Any], candidates: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        # Varios equipamentos do mesmo grupo muscular podem servir ao MESMO
        # exercicio (ex.: "Super Horizontal Multi Press" e "HumanSport
        # Shoulder Chest" ambos mapeados para "Supino Reto na Maquina"). Sem
        # desempate, o primeiro match da lista (ordenada por menor uso
        # recente) vencia mesmo quando o equipamento ORIGINAL do
        # template/ficha ja era um candidato valido — trocando a foto/nome do
        # equipamento sem necessidade. Desempata preferindo o candidato cujo
        # equipment_name ja bate com o equipment_name original do exercicio;
        # só cai no primeiro da lista LRU se nenhum candidato preservar o
        # equipamento original.
        matches = [c for c in candidates if _candidate_matches(c, name)]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        original_equipment_name = _normalize_name(exercise.get("equipment_name"))
        preserved = next(
            (c for c in matches if _normalize_name(c.get("equipment_name")) == original_equipment_name),
            None,
        )
        return preserved or matches[0]

    def _find_match(exercise: dict[str, Any]) -> dict[str, Any] | None:
        group_info = _resolve_group_info(exercise)
        if not group_info:
            return None
        candidates = [c for c in [group_info.get("recommended"), *(group_info.get("alternatives") or [])] if c]
        name = _normalize_name(exercise.get("exercise_name"))
        return _select_match(exercise, candidates, name)

    # Pre-scan: exercicios que ja batem por nome exato/apelido reservam o nome
    # do catalogo ANTES de qualquer fallback ser escolhido. Isso importa
    # porque a ordem dos exercicios no template nao garante que o exercicio
    # com match exato venha antes do exercicio sem match no mesmo grupo — se
    # o fallback for escolhido primeiro (na ordem da lista), ele nao pode
    # "roubar" um nome que um exercicio processado DEPOIS vai reivindicar por
    # match genuino.
    reserved_exercise_names: set[str] = {
        _normalize_name(match.get("mapping_exercise_name"))
        for exercise in exercises
        if (match := _find_match(exercise)) is not None
    }

    # Nomes finais (normalizados) ja atribuidos a exercicios anteriores NESTA
    # MESMA ficha (matches genuinos reservados acima + fallbacks ja
    # escolhidos ao longo do loop). Usado so para desempatar o fallback (evita
    # que dois exercicios distintos do template, um resolvido por match exato
    # e outro sem match, colapsem no mesmo candidates[0] do grupo). Nao remove
    # exercicios — se todos os candidatos
    # do grupo ja estiverem em uso, aceita a duplicata mesmo assim.
    used_exercise_names: set[str] = set(reserved_exercise_names)

    for exercise in exercises:
        group_info = _resolve_group_info(exercise)
        if not group_info:
            # Sem contexto de grupo para esta academia (grupo nao bate com
            # nenhuma chave conhecida): mantem o exercicio como esta hoje.
            kept.append(exercise)
            continue

        candidates = [c for c in [group_info.get("recommended"), *(group_info.get("alternatives") or [])] if c]
        if not candidates:
            # O grupo existe no contexto da academia, mas ela nao tem NENHUM
            # equipamento cadastrado para ele (has_equipment_for_group=false):
            # nao ha como manter esse exercicio com um equipamento valido,
            # entao ele e excluido da ficha.
            excluded_due_to_no_equipment.append(exercise)
            continue

        name = _normalize_name(exercise.get("exercise_name"))
        match = _select_match(exercise, candidates, name)

        if match:
            # Nome ja e valido nesta academia (por nome exato ou por apelido)
            # — canonicaliza exercise_name/amazfit_name para o texto real do
            # catalogo (relevante quando o match veio de um apelido, ex.
            # "Pulley Frente" -> "Front Pull Down / Lat Pulldown") e garante
            # que o equipamento gravado bate com o mapping correto (evita
            # nomes de equipamento divergentes por erro de digitacao da IA).
            exercise["exercise_name"] = match.get("mapping_exercise_name")
            exercise["amazfit_name"] = match.get("mapping_amazfit_name")
            exercise["equipment_name"] = match.get("equipment_name")
            # mapping_id do catalogo (FK estavel para exercise_zepp_mappings):
            # propagado aqui para permitir que assign_progression_indicators
            # (motor v2 por par, M76 Lote 1a) compare carga
            # historica pelo mesmo exercicio canonico, em vez de depender de
            # exercise_name (texto livre) bater com o historico.
            exercise["mapping_id"] = match.get("mapping_id")
            used_exercise_names.add(_normalize_name(match.get("mapping_exercise_name")))
            kept.append(exercise)
            continue

        # Nome nao corresponde a nenhum exercicio com equipamento cadastrado
        # nesta academia para o grupo: substitui pelo recomendado (equipamento
        # menos usado recentemente), que sabidamente existe no catalogo.
        #
        # original_exercise_name precisa ser capturado ANTES da mutacao e o
        # snapshot em `adjusted` DEPOIS: um bug anterior aqui montava o dict
        # de `adjusted` com `**exercise` antes de exercise["exercise_name"]
        # ser reescrito, entao exercise_name == original_exercise_name em
        # TODA entrada de adjusted, por construcao (nao por falha so de
        # normalizacao de string) — daí o ruido reproduzido em producao.
        #
        # Prefere o primeiro candidato cujo nome ainda nao foi usado nesta
        # ficha (candidates ja vem ordenada por menor uso recente). Se todos
        # ja estiverem em uso, aceita a duplicata (candidates[0]) mesmo assim.
        fallback = next(
            (c for c in candidates if _normalize_name(c.get("mapping_exercise_name")) not in used_exercise_names),
            candidates[0],
        )
        original_exercise_name = exercise.get("exercise_name")
        exercise["exercise_name"] = fallback.get("mapping_exercise_name")
        exercise["amazfit_name"] = fallback.get("mapping_amazfit_name")
        exercise["equipment_name"] = fallback.get("equipment_name")
        exercise["mapping_id"] = fallback.get("mapping_id")
        used_exercise_names.add(_normalize_name(fallback.get("mapping_exercise_name")))
        adjusted.append({**exercise, "original_exercise_name": original_exercise_name})
        kept.append(exercise)

    return kept, adjusted, excluded_due_to_no_equipment


def assign_warmup_flags(exercises: list[dict[str, Any]]) -> None:
    """Marca warmup=True apenas no primeiro exercicio (na ordem final) que
    ativa um grupo muscular ainda nao trabalhado (nem como primario nem como
    secundario) em nenhum exercicio anterior desta ficha. Feito no backend em
    vez de depender da IA marcar isso corretamente."""
    activated: set[str] = set()
    for exercise in exercises:
        primary = (exercise.get("muscle_primary") or "").strip().lower()
        secondary = (exercise.get("muscle_secondary") or "").strip().lower()

        exercise["warmup"] = bool(primary) and primary not in activated

        if primary:
            activated.add(primary)
        if secondary:
            activated.add(secondary)


LOAD_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def normalize_load_target(value: Any) -> str | None:
    """Forca o formato "<numero>kg" (ex: "40kg", "42,5kg"), extraindo apenas o
    primeiro valor numerico de qualquer texto que a IA tenha retornado (faixas
    como "40-45kg" viram "40kg"; textos como "40kg com base no historico" viram
    "40kg"). Nao confia no prompt sozinho para garantir esse formato."""
    if value is None:
        return None

    match = LOAD_NUMBER_RE.search(str(value))
    if not match:
        return None

    number = float(match.group(1).replace(",", "."))
    if number == int(number):
        formatted = str(int(number))
    else:
        formatted = f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")

    return f"{formatted}kg"


def assign_progression_indicators(exercises: list[dict[str, Any]], *, user_id: int | None) -> None:
    """Os 4 indicadores que a UI consome (`progression`, `progression_note`,
    `suggested_load_kg`, `suggested_reps`) para os exercicios de um rascunho
    gerado pelos caminhos legados (Full Body/ABC/`GENERATION_ENGINE=legacy`).

    M76 (Lote 1a, C1): a decisao e do motor v2 por par
    (`domain/prescription_service.suggest_for_pairs`), com a seguranca do par e
    o contrato de recuperacao projetado - o mesmo da geracao deterministica.
    Acabou o motor legado `progression_service.suggest_load_and_reps` (+5% sem
    passo). O historico e lido COMPLETO aqui (com `set_type`): a versao enxuta
    de `gather_ficha_context` nao serve, porque aquecimento nao pode virar
    carga base. Sem usuario ou com falha, os indicadores ficam vazios (a
    ficha salva congela de novo pelo motor, em `_manual_rows_snapshots`)."""
    from .domain.prescription_service import PairQuery, legacy_indicators, suggest_for_pairs
    from .generation.freeze_mode_b import equipment_by_gym_equipment

    vazio = legacy_indicators(None)
    queries: list[PairQuery | None] = [None] * len(exercises)
    found: dict[Any, Any] = {}
    if user_id is not None and exercises:
        try:
            with transaction() as conn:
                equipment_by_gym = equipment_by_gym_equipment(
                    conn, [exercise.get("preferred_equipment_id") for exercise in exercises]
                )
                for index, exercise in enumerate(exercises):
                    if not exercise.get("mapping_id"):
                        continue
                    gym_equipment_id = exercise.get("preferred_equipment_id")
                    queries[index] = PairQuery(
                        exercise_id=int(exercise["mapping_id"]),
                        equipment_id=equipment_by_gym.get(int(gym_equipment_id)) if gym_equipment_id else None,
                        rpe_target=exercise.get("rpe_target"),
                    )
                wanted = [query for query in queries if query is not None]
                if wanted:
                    logs = services.list_exercise_logs(limit=300, user_id=user_id)
                    found = suggest_for_pairs(conn, int(user_id), wanted, logs=logs)
        except Exception:
            logger.exception("Falha ao calcular os indicadores de progressao do rascunho (motor v2)")
            found = {}
    for exercise, query in zip(exercises, queries):
        exercise.update(legacy_indicators(found.get(query.key)) if query is not None else vazio)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    candidate = match.group(0) if match else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise WorkoutPlanGenerationError(f"Resposta da IA nao veio em JSON valido: {exc}") from exc


