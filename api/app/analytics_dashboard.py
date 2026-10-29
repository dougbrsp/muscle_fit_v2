"""Rotas do Novo Dashboard (`/analytics/dashboard/*`, plano §10).

Router NOVO, separado de `analytics.py` de proposito: `analytics.py` tem
2.100 linhas de rotas v1/v2 que devolvem `dict` livre, e o hub novo tem
regras diferentes (envelope semantico, `response_model` obrigatorio, janela
no fuso do usuario, `require_user_id` em todo servico). Misturar os dois
esconderia a fronteira - e a aposentadoria das rotas antigas (§17, F2.4)
passa a ser "remover um arquivo", nao "achar as rotas certas".

Vive na Lambda `musclefit-engines` (decisao §18: os motores deterministicos
ja estao la e ela nao tem o segredo da OpenAI). Como `/analytics/*` nao tem
catch-all para essa Lambda, TODO path novo precisa estar tambem em
`infra/api_gateway.tf` e `nginx/local-dev.conf` - `tests/unit/
test_paridade_rotas_engines.py` falha se faltar em qualquer um dos tres.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from .db import get_conn
from .db_helpers import connect_or_503, DB_UNAVAILABLE_RDS
from .intelligence.dashboard.contracts import (
    AdherenceResponse,
    BodyResponse,
    CardioResponse,
    ContextResponse,
    DataQualityResponse,
    EnginePlanResponse,
    EngineRunsResponse,
    ExerciseProgressResponse,
    InsightsResponse,
    IntelligencePackageResponse,
    MuscleDrilldownResponse,
    MusclesResponse,
    OverviewResponse,
    RecoverySignalsResponse,
    StrengthPanelResponse,
    TargetSource,
    WindowKind,
    WorkoutDetailResponse,
    WorkoutsResponse,
)
from .intelligence.dashboard.adherence_service import (
    DEFAULT_WORKOUTS_LIMIT,
    MAX_DETAIL_SETS,
    MAX_WORKOUTS_LIMIT,
    build_adherence,
    build_workout_detail,
    build_workouts,
    resolve_cycle_length_days,
)
from .intelligence.dashboard.body_service import build_body
from .intelligence.dashboard.cardio_service import build_cardio
from .intelligence.dashboard.context_service import build_context
from .intelligence.dashboard.data_quality_service import MAX_EXAMPLES, build_data_quality
from .intelligence.dashboard.engine_service import (
    DEFAULT_RUNS_LIMIT,
    MAX_RUNS_LIMIT,
    build_engine_plan,
    build_engine_runs,
)
from .intelligence.dashboard.exposure_service import MAX_EXPOSURES, build_exercise_progress
from .training_goal_query import goal_query
from .intelligence.dashboard.intelligence_package import (
    PACKAGE_MAX_BYTES,
    build_intelligence_package,
)
from .intelligence.dashboard.muscle_summary_service import (
    MAX_DRILLDOWN_SETS,
    build_muscle_drilldown,
    build_muscle_summary,
)
from .intelligence.dashboard.overview_service import build_insights, build_overview
from .intelligence.dashboard.recovery_signals_service import build_recovery_signals
from .intelligence.dashboard.strength_panel import build_strength_panel
from .intelligence.dashboard.time_windows import resolve_window, user_now, user_timezone, user_today
from .observability import log_event
from .security import Identity, get_current_identity
from .timezones import DEFAULT_TIMEZONE  # noqa: F401 - reexportado: importado por testes/modulos que nasceram com ele aqui
from .training_types import resolve_training_type_for_window

router = APIRouter(prefix="/analytics/dashboard", tags=["dashboard-v3"])

DEFAULT_WINDOW = WindowKind.ROLLING_90D


def _connect():
    return connect_or_503(get_conn, DB_UNAVAILABLE_RDS)


def _user_timezone(conn, user_id: int) -> str:
    """Fuso do usuario (`users.timezone`, migracao 044).

    A implementacao mudou de casa na F3.2 (`time_windows.user_timezone`):
    `insights_core` monta o pacote do lado do servidor e precisa resolver a
    MESMA janela no MESMO fuso sem importar este modulo de rotas. Aqui ficou o
    alias, para as 10 chamadas existentes continuarem como estao."""
    return user_timezone(conn, user_id)


CYCLE_WITHOUT_TEMPLATE_DETAIL = (
    "window=cycle exige um Modelo de Ficha atribuído: o tamanho do ciclo vem do número de "
    "slots do Modelo. Atribua um Modelo ou use 7d|28d|90d|180d|custom."
)


def _resolve(
    window: str,
    date_from: Optional[date],
    date_to: Optional[date],
    timezone_name: str,
    user_id: int,
):
    """Janela resolvida em datas locais. `ValueError` de `resolve_window` vira
    400 (nunca 500).

    `window=cycle` passou a funcionar em TODOS os endpoints v3 a partir da
    F1.1: o tamanho do ciclo (um slot = um dia, `template_cycle_length_days`,
    mesma convenção de `/analytics/dashboard-cycle`) é resolvido por
    `adherence_service.resolve_cycle_length_days`, que não existia antes —
    era essa a única razão de os endpoints da F0.2/F0.3/F1.2 devolverem 400
    para `cycle`. Sem Modelo atribuído continua 400, agora com a mensagem que
    diz o que fazer."""
    cycle_length_days = None
    if window == WindowKind.CYCLE.value:
        cycle_length_days = resolve_cycle_length_days(user_id)
        if cycle_length_days is None:
            raise HTTPException(status_code=400, detail=CYCLE_WITHOUT_TEMPLATE_DETAIL)
    try:
        return resolve_window(
            window,
            today=user_today(timezone_name),
            date_from=date_from,
            date_to=date_to,
            cycle_length_days=cycle_length_days,
            timezone_name=timezone_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/exercises/{exercise_id}/progress", response_model=ExerciseProgressResponse)
def exercise_progress(
    exercise_id: int = Path(..., ge=1),
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=MAX_EXPOSURES, ge=1, le=MAX_EXPOSURES),
    goal: str = Depends(goal_query),
    identity: Identity = Depends(get_current_identity),
) -> ExerciseProgressResponse:
    """"Estou evoluindo neste exercicio?" (plano §8, §10).

    - 404 so quando o exercicio NAO existe no catalogo canonico.
    - Exercicio existente sem historico DESTE usuario -> 200 com
      `status=INSUFFICIENT_DATA` e `exposures: []` (nunca 404, nunca zeros).
    - `window=cycle` e aceita desde a F1.1 (o tamanho do ciclo vem do
      Modelo atribuido); sem Modelo atribuido ela responde 400 explicando.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_exercise_progress(
            conn,
            exercise_id,
            user_id=identity.user_id,
            window=resolved,
            limit=limit,
            training_goal=goal,
        )

    if payload is None:
        raise HTTPException(status_code=404, detail="Exercício não encontrado no catálogo canônico.")
    return ExerciseProgressResponse.model_validate(payload)


@router.get("/strength-panel", response_model=StrengthPanelResponse)
def strength_panel(
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> StrengthPanelResponse:
    """Painel de forca (M97 F4): tendencia do e1RM (linha unica entre
    objetivos, sem deload) dos compostos principais e o top set de cada
    sessao de forca. Regra no dono `intelligence/dashboard/strength_panel.py`;
    sem sessao de forca na janela, `lifts: []`."""
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_strength_panel(conn, user_id=identity.user_id, window=resolved)
    return StrengthPanelResponse.model_validate(payload)


@router.get("/data-quality", response_model=DataQualityResponse)
def data_quality(
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    examples_limit: int = Query(default=MAX_EXAMPLES, ge=1, le=MAX_EXAMPLES),
    identity: Identity = Depends(get_current_identity),
) -> DataQualityResponse:
    """"Dá para concluir alguma coisa com o que está registrado?" (§8.3, §10).

    - Sempre 200: base impecável responde `items: []` e `overall=HIGH`. Não
      existe "score" — o que existe é uma lista de problemas com a frase do
      que fica impossível concluir e o link da tela que resolve.
    - Itens de escopo `catalog` (contribuição muscular, convenção de carga,
      meta do grupo) IGNORAM a janela de propósito: lacuna de cadastro não
      deixa de existir porque o período é curto. O campo `scope` diz qual é
      qual, item a item.
    - `window=cycle` é aceita desde a F1.1 (o ciclo vem do Modelo atribuído);
      sem Modelo atribuído ela responde 400 com a mensagem do que fazer.

    Nenhuma escrita: o serviço é somente leitura, inclusive para a coluna
    `load_convention` (se a migração 045 ainda não rodou neste banco, a regra
    correspondente vira limitação declarada, nunca 500).
    """
    # Resolução de meta que já existe (explícita -> Modelo atribuído ->
    # fallback genérico), reusada e não reimplementada. Uma chamada por
    # requisição, fora do laço de regras — não é um N+1.
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        training_type_id = resolve_training_type_for_window(identity.user_id, resolved.date_to)
        payload = build_data_quality(
            conn,
            user_id=identity.user_id,
            window=resolved,
            now_local=user_now(timezone_name),
            training_type_id=training_type_id,
            max_examples=examples_limit,
        )

    return DataQualityResponse.model_validate(payload)


def _resolve_target_source(
    training_type_id: Optional[int], user_id: int, date_to: Optional[date] = None
) -> tuple[Optional[int], TargetSource]:
    """Meta a comparar: `training_type_id` explicito > Meta de Ativacao do
    Modelo atribuido > fallback generico. MESMA cadeia de
    `_build_muscle_status_items` (v2) e de `/analytics/dashboard/data-quality`
    - nunca uma segunda regra de resolucao de meta.

    Um degrau a mais vive no SERVICO, nao aqui: quando o Modelo atribuido mais
    recente e `equipment_series`, `build_muscle_summary` troca a meta pela
    ativacao planejada do proprio Modelo (`target_source = template_plan`),
    a nao ser que o `training_type_id` explicito tenha sido pedido.

    Nada de IDOR aqui: `training_type_id` explicito so escolhe QUAL meta
    comparar; ele nao seleciona dado de treino. Todo numero da resposta sai de
    consultas com `a.user_id = %s` obrigatorio (`require_user_id`)."""
    if training_type_id is not None:
        return training_type_id, TargetSource.EXPLICIT
    # PR3: tipo de treino da versao do Modelo no FIM da janela (flag ligada);
    # desligada, o de hoje. Um statement, como antes.
    resolved = resolve_training_type_for_window(user_id, date_to)
    if resolved is not None:
        return resolved, TargetSource.ASSIGNED_TEMPLATE
    return None, TargetSource.FALLBACK_GENERIC


@router.get("/muscles", response_model=MusclesResponse)
def muscles(
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    training_type_id: Optional[int] = Query(default=None, ge=1),
    include_readiness: bool = Query(default=True),
    include_comparatives: bool = Query(default=False),
    identity: Identity = Depends(get_current_identity),
) -> MusclesResponse:
    """"Quais grupos estao atrasados ou em excesso, e o volume chegou no
    musculo certo?" (plano §7, §10).

    Tres numeros SEPARADOS por grupo, nunca somados na mesma metrica:
    realizado (`OBSERVED`/`DERIVED`), planejado (`PLANNED`) e projetado
    (`PROJECTED`). O `status` classifica SO o realizado - diferenca
    deliberada em relacao a `/analytics/dashboard-cycle`, que soma o
    projetado porque responde outra pergunta ("se eu completar o ciclo, onde
    fico?").

    - Sempre 200: sem nenhuma serie na janela a resposta traz os grupos com
      meta zerados e `data_quality=INSUFFICIENT` - nunca 404.
    - `include_readiness` vem LIGADO por padrao desde a F1.5. Ele nasceu
      opt-in porque a prontidao custava ~4s a mais (o modelo lia dois datasets
      inteiros de series por chamada); a M20 derrubou esse custo para 3
      statements, entao esconder a prontidao deixou de fazer sentido. O
      parametro continua existindo - `false` ainda pula o bloco - para quem
      quiser a resposta mais enxuta possivel.
    - `window=cycle` e aceita desde a F1.1 (o ciclo vem do Modelo atribuido).
    - `include_comparatives` (F2.5 / M34) acrescenta o bloco `comparatives`:
      comparativo das ULTIMAS SESSOES (T-0..T-3), comparativo por EQUIPAMENTO
      e mapa de calor - a paridade que faltava com `/volume-grupamentos`.
      Custa UM statement a mais e vem DESLIGADO por padrao, porque a tela faz
      duas chamadas (tabela e prontidao) e so a primeira usa o bloco.

      **A unidade dele e outra.** Todo o resto desta resposta compara por
      JANELA; `comparatives` compara por SESSAO (`unit = "session"`): T-0 e a
      ultima sessao em que o GRUPO apareceu, nao "esta semana". Os dois
      numeros nao se somam nem se substituem, e a tela rotula cada um.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        resolved_type_id, source = _resolve_target_source(training_type_id, identity.user_id, resolved.date_to)
        payload = build_muscle_summary(
            conn,
            user_id=identity.user_id,
            window=resolved,
            training_type_id=resolved_type_id,
            training_type_source=source,
            include_readiness=include_readiness,
            now_local=user_now(timezone_name),
            include_comparatives=include_comparatives,
        )

    return MusclesResponse.model_validate(payload)


@router.get("/muscles/{muscle_group_id}", response_model=MuscleDrilldownResponse)
def muscle_drilldown(
    muscle_group_id: int = Path(..., ge=1),
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    training_type_id: Optional[int] = Query(default=None, ge=1),
    include_readiness: bool = Query(default=True),
    sets_limit: int = Query(default=MAX_DRILLDOWN_SETS, ge=1, le=MAX_DRILLDOWN_SETS),
    identity: Identity = Depends(get_current_identity),
) -> MuscleDrilldownResponse:
    """Drill-down de UM grupo ate a SERIE de origem (plano §7, §10).

    - 404 so quando o grupo NAO existe em `muscle_groups`. Grupo existente sem
      serie na janela responde 200 com o item zerado e `sessions: []`.
    - `sets_limit`/`sets_truncated` sao contrato: a resposta da RDS Data API
      tem teto de 1 MB (§15), entao o corte fica nas sessoes mais ANTIGAS e e
      declarado - a tela diz "ha series mais antigas fora desta lista" em vez
      de mostrar um total errado.
    - Nao existe caminho para ver grupo de outro usuario: o id aqui e de
      `muscle_groups` (catalogo compartilhado, sem dono), e toda serie vem de
      consulta com `a.user_id = %s`.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        resolved_type_id, source = _resolve_target_source(training_type_id, identity.user_id, resolved.date_to)
        payload = build_muscle_drilldown(
            conn,
            muscle_group_id,
            user_id=identity.user_id,
            window=resolved,
            training_type_id=resolved_type_id,
            training_type_source=source,
            include_readiness=include_readiness,
            now_local=user_now(timezone_name),
            sets_limit=sets_limit,
        )

    if payload is None:
        raise HTTPException(status_code=404, detail="Grupo muscular não encontrado.")
    return MuscleDrilldownResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# F1.1 - "Cumpri o planejado?" (plano §5, §7, §10)
# ---------------------------------------------------------------------------


@router.get("/workouts", response_model=WorkoutsResponse)
def workouts(
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=DEFAULT_WORKOUTS_LIMIT, ge=1, le=MAX_WORKOUTS_LIMIT),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_current_identity),
) -> WorkoutsResponse:
    """Sessoes executadas na janela, com planejado x executado resumido.

    Cada sessao traz: data, nome, ficha/slot de origem (`plan_link` - o
    vinculo REAL, nao o nome), duracao, series fisicas e efetivas, estimulo,
    fadiga, RPE medio, recordes/progressoes e a contagem de itens por status
    DERIVADO (§5: concluido/interrompido/pulado/substituido/extra).

    - Sempre 200: janela sem sessao responde `items: []` com
      `data_quality=INSUFFICIENT`.
    - PAGINADO (`limit`/`offset`, `total`/`has_more`): e o unico endpoint do
      hub com paginacao (§10), porque o numero de sessoes cresce sem teto e a
      resposta da Data API tem limite de 1 MB (§15).
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_workouts(
            conn,
            user_id=identity.user_id,
            window=resolved,
            limit=limit,
            offset=offset,
        )

    return WorkoutsResponse.model_validate(payload)


@router.get("/workouts/{activity_id}", response_model=WorkoutDetailResponse)
def workout_detail(
    activity_id: int = Path(..., ge=1),
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    sets_limit: int = Query(default=MAX_DETAIL_SETS, ge=1, le=MAX_DETAIL_SETS),
    identity: Identity = Depends(get_current_identity),
) -> WorkoutDetailResponse:
    """Uma sessao item a item: prescrito x executado lado a lado.

    - 404 quando a sessao nao existe OU nao e do usuario - os dois casos
      respondem igual, entao nenhum id alheio e confirmado (IDOR).
    - `SUBSTITUTED` traz o exercicio original e o executado; `INTERRUPTED`
      traz `missing_sets` (quanto faltou para o prescrito).
    - `sets_limit`/`sets_truncated` sao contrato: o teto de 1 MB da Data API
      (§15) corta os ULTIMOS itens e a tela DIZ que cortou.
    - A sessao pode estar fora da janela do filtro: o id manda, a janela so
      acompanha o payload (e serve ao `window=cycle`).
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_workout_detail(
            conn,
            activity_id,
            user_id=identity.user_id,
            window=resolved,
            sets_limit=sets_limit,
        )

    if payload is None:
        raise HTTPException(status_code=404, detail="Sessão de treino não encontrada.")
    return WorkoutDetailResponse.model_validate(payload)


@router.get("/adherence", response_model=AdherenceResponse)
def adherence(
    window: str = Query(default=DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> AdherenceResponse:
    """"Cumpri o planejado?" e "a frequencia bate com o ciclo?" (§7).

    E este o endpoint que o bloco "Consistencia" da visao geral (F1.6) vai
    consumir: ciclo corrente (slots `done|next|pending`), sessoes esperadas,
    realizadas, sem ficha e nao executadas, mais a taxa de aderencia da
    janela e a do ciclo.

    Sem Modelo de Ficha atribuido a resposta EXISTE (os contadores observados
    continuam valendo), mas `rate`/`expected_sessions` vem `null` com
    `data_quality=LOW` e a limitacao declarada: o denominador so pode vir do
    Modelo, e inventa-lo seria responder "cumpri o planejado?" sem plano
    (§6, `adherence-v2`).
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_adherence(conn, user_id=identity.user_id, window=resolved)

    return AdherenceResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# F1.5 - "Estou recuperando bem?" (plano §6, §7, §10, §11)
# ---------------------------------------------------------------------------

RECOVERY_DEFAULT_WINDOW = WindowKind.ROLLING_7D


@router.get("/recovery-signals", response_model=RecoverySignalsResponse)
def recovery_signals(
    window: str = Query(default=RECOVERY_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> RecoverySignalsResponse:
    """"Estou recuperando bem?" (plano §7).

    Duas leituras LADO A LADO, nunca fundidas num numero so: a prontidao por
    grupo, que sai do modelo `fullbody-no-reps-v1` (o mesmo da geracao de
    ficha e do Modo Treino), e os sinais objetivos comparados com a BASELINE
    PESSOAL dos 28 dias anteriores a janela.

    - Janela default `7d`: recuperacao e uma pergunta sobre agora. `cycle`,
      `28d`, `90d`, `180d` e `custom` continuam aceitos.
    - Sempre 200. Sem wearable, sem sono e sem sessao, a resposta existe com
      `data_quality=INSUFFICIENT` e as limitacoes declaradas - ausencia de
      sinal NUNCA zera a prontidao nem vira zero.
    - Nenhum sinal isolado eleva o alarme: `ACTION` so aparece quando o
      proprio modelo bloqueia trabalho direto em algum grupo.
    - A rota v2 `/analytics/dashboard-recovery` continua existindo (a tela e
      que deixa de consumi-la); a remocao fica para a F2.4.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_recovery_signals(conn, user_id=identity.user_id, window=resolved)

    return RecoverySignalsResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# F1.6 - visao geral: 6 blocos + "Proxima acao" explicavel (plano §7, §8.1 do
# brief, §10, §11, §12)
# ---------------------------------------------------------------------------

OVERVIEW_DEFAULT_WINDOW = WindowKind.ROLLING_28D
INSIGHTS_DEFAULT_LIMIT = 20
INSIGHTS_MAX_LIMIT = 50


def _public(payload: dict) -> dict:
    """Remove os campos internos (`_insights`, `_timings`...) antes do
    `response_model` (contrato fechado, `extra="forbid"`)."""
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


@router.get("/overview", response_model=OverviewResponse)
def overview(
    window: str = Query(default=OVERVIEW_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    training_type_id: Optional[int] = Query(default=None, ge=1),
    identity: Identity = Depends(get_current_identity),
) -> OverviewResponse:
    """A visao geral do hub: "Estou evoluindo?" e "Proxima acao" (§7).

    Seis blocos, cada um respondendo UMA pergunta com uma frase e os numeros
    que a sustentam, apontando o drill-down e carregando a propria
    `data_quality`: consistencia (aderencia), progressao (contagem por
    status, SEM score), musculos prioritarios, corpo (peso com media movel de
    7 dias; cintura declarada indisponivel ate a F1.4), recuperacao (janela
    propria de 7 dias) e proxima acao (no maximo 2 insights; o resto em
    `/insights`).

    - Tudo que aparece ja existe como endpoint v3; aqui cada servico e
      chamado UMA vez por chamada direta (nunca HTTP interno) e o resultado
      alimenta bloco e regras.
    - Sempre 200: usuario sem historico responde com os blocos em
      `INSUFFICIENT` e `next_actions.items: []` ("nenhuma acao sugerida").
    - Nada muda automaticamente: cada insight e uma sugestao com evidencia,
      comparacao, confianca, alternativas e data de reavaliacao.
    - Janela default `28d` (a pergunta "estou evoluindo?" e sobre o mes);
      `window=cycle` aceita com Modelo atribuido.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        resolved_type_id, source = _resolve_target_source(training_type_id, identity.user_id, resolved.date_to)
        payload = build_overview(
            conn,
            user_id=identity.user_id,
            window=resolved,
            now_local=user_now(timezone_name),
            training_type_id=resolved_type_id,
            training_type_source=source,
            # PERF-05: blocos de leitura em paralelo, cada um com a propria conexao.
            conn_factory=_connect,
        )

    return OverviewResponse.model_validate(_public(payload))


@router.get("/insights", response_model=InsightsResponse)
def insights(
    window: str = Query(default=OVERVIEW_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    training_type_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=INSIGHTS_DEFAULT_LIMIT, ge=1, le=INSIGHTS_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    include_suppressed: bool = Query(default=False),
    identity: Identity = Depends(get_current_identity),
) -> InsightsResponse:
    """Os insights disparados na janela (§12), deduplicados por `fingerprint` e
    ordenados por severidade e confianca, com paginacao simples. E a MESMA
    avaliacao da visao geral (reusa `build_overview`), nao uma segunda -
    `next_actions` da home e o prefixo desta lista.

    F2.2 - cooldown (`dashboard_alert_states`, migracao 051): por padrao a
    lista traz so o que o usuario ainda NAO viu (ou viu e PIOROU de
    severidade); `suppressed_total` diz quantos ficaram de fora e
    `include_suppressed=true` acrescenta-os ao FIM, cada um com o bloco
    `cooldown` (motivo, desde quando, silenciado ate quando) - um cartao nunca
    desaparece sem explicacao. Antes da 051, ou com
    `DASHBOARD_ALERT_COOLDOWN=0`, nada e silenciado e `cooldown_available` vem
    `false` com a limitacao declarada.

    `rules_disabled` lista as regras escritas mas desligadas (vazia desde a
    F2.1)."""
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        resolved_type_id, source = _resolve_target_source(training_type_id, identity.user_id, resolved.date_to)
        payload = build_insights(
            conn,
            user_id=identity.user_id,
            window=resolved,
            now_local=user_now(timezone_name),
            training_type_id=resolved_type_id,
            training_type_source=source,
            limit=limit,
            offset=offset,
            include_suppressed=include_suppressed,
            conn_factory=_connect,
        )

    return InsightsResponse.model_validate(_public(payload))


# ---------------------------------------------------------------------------
# F1.4 - corpo: peso (media movel), cintura, bioimpedancia, recomposicao
# (plano §6, §10, §11 `/dashboard/corpo`)
# ---------------------------------------------------------------------------

BODY_DEFAULT_WINDOW = WindowKind.ROLLING_90D


@router.get("/body", response_model=BodyResponse)
def body(
    window: str = Query(default=BODY_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> BodyResponse:
    """"Como esta o corpo?" (plano §7) - tela `/dashboard/corpo`.

    - Peso: `weight-ma-v1`, um ponto por dia COM pesagem (fonte declarada),
      media movel de 7 dias so sobre dias pesados - nunca zero.
    - Cintura: `waist-v1`, serie de `body_measurements`; ultima medida NO
      PROTOCOLO; fora do protocolo entra com `LOW` e fica fora do sinal.
    - Bioimpedancia: ESTIMATED; uma leitura isolada nao confirma tendencia.
    - Recomposicao: `recomp-v1` (INFERRED) em janela PROPRIA de 28 dias -
      `compatible | not_compatible | insufficient_data`, com evidencias e
      limitacoes; "compativel com", nunca "confirmada".
    - Desempenho: e1RM medio dos exercicios comparaveis, do MESMO lote de
      progressao da visao geral (1 consulta), para o grafico alinhado.
    - Sempre 200: sem pesagem e sem medida a resposta existe com
      `INSUFFICIENT`. Antes da migracao 047 a cintura vem indisponivel com
      a limitacao declarada. Janela default `90d`; `cycle` aceita com Modelo.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_body(conn, user_id=identity.user_id, window=resolved)

    return BodyResponse.model_validate(_public(payload))


# ---------------------------------------------------------------------------
# F2.1 - contexto: timeline de eventos manuais + derivados (plano §11
# `/dashboard/contexto`, §12)
# ---------------------------------------------------------------------------

CONTEXT_DEFAULT_WINDOW = WindowKind.ROLLING_90D


@router.get("/context", response_model=ContextResponse)
def context(
    window: str = Query(default=CONTEXT_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> ContextResponse:
    """"O que mais estava acontecendo?" (plano §7) - tela `/dashboard/contexto`.

    - LEITURA apenas. O registro manual e do CRUD da api-core
      (`POST/PUT/DELETE /context-events`): entrada de dado do usuario nao
      pertence a Lambda dos motores.
    - Timeline unica com duas origens: eventos MANUAIS (`context_events`,
      migracao 048) e DERIVADOS calculados na leitura (troca de ficha, a
      partir das atribuicoes de Modelo; troca de academia, a partir de
      `activities.gym_id`). Derivado nao e materializado - ver o cabecalho de
      `context_service` para o porque (idempotencia e dupla verdade).
    - Evento com duracao entra na janela se o INTERVALO dele a toca.
    - Sempre 200: sem evento nenhum a resposta existe com `INSUFFICIENT` e a
      limitacao declarada. Antes da 048 os manuais vem vazios com a limitacao
      e os derivados continuam aparecendo. Janela default `90d` (a timeline e
      historico, nao "agora"); `cycle` aceita com Modelo.
    - Evento de contexto e ASSOCIACAO, nunca causa: nada aqui reclassifica
      metrica nenhuma.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_context(conn, user_id=identity.user_id, window=resolved)

    return ContextResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# F2.3 - cardio: volume de cardio do Intervals.icu + FC por sessao de treino
# (plano §11 `/dashboard/cardio`, §17 F2.3)
# ---------------------------------------------------------------------------

CARDIO_DEFAULT_WINDOW = WindowKind.ROLLING_90D


@router.get("/cardio", response_model=CardioResponse)
def cardio(
    window: str = Query(default=CARDIO_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> CardioResponse:
    """"Quanto de cardio eu fiz, e com que FC eu treinei?" - tela
    `/dashboard/cardio`.

    - LEITURA do CACHE, nunca da API externa. As atividades vem de
      `intervals_activities`, que o job agendado
      (`musclefit-intervals-sync`) mantem; a data da ultima sincronizacao e
      DECLARADA na resposta. A tela antiga `/cardio` consulta o Intervals.icu
      ao vivo a cada carregamento, e por isso depende da chave do usuario e
      da API externa estar de pe - o hub nao.
    - FC por sessao: lida de `activity_intervals_link` (migracao 041, M2). O
      casamento treino <-> atividade `WeightTraining`, por horario de inicio
      e tolerancia de 60 minutos, e do job - aqui ele nao e refeito.
      `MATCHED_AMBIGUOUS` chega ao payload: a tela diz que aquela FC pode ser
      de outro treino em vez de apresenta-la como certeza.
    - Sem Intervals configurado: 200 com tudo vazio, `INSUFFICIENT` e a
      limitacao que diz o que fazer. Quatro estados nao sao confundidos -
      "nao configurado", "configurado e sem sincronizacao", "sincronizado e
      sem cardio na janela" e "cardio sem FC" tem frases proprias.
    - O bloco de FC por sessao NAO depende de a chave estar valida agora: o
      link e historico. Revogar a chave para de trazer cardio novo, nao apaga
      a FC dos treinos passados.
    - Janela default `90d`; `cycle` aceita com Modelo atribuido.
    - M98 C4: tambem o acompanhamento - minutos por zona e continuo x intensa
      por semana, planejado x realizado (`cardio_adherence`), CTL/ATL/TSB,
      HRV, FC de repouso, PAI, a proxima sessao da ultima recomendacao
      `cardio-agent-v3` (valida a partir de "hoje" no fuso do usuario) e as
      zonas em uso. E a UNICA leitura do Intervals para cardio: o
      `/integrations/intervals/cardio-dashboard` ao vivo saiu.
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_cardio(
            conn, user_id=identity.user_id, window=resolved, today=user_today(timezone_name)
        )

    return CardioResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# F3.2 - `GET /analytics/dashboard/intelligence-package` (plano §12, §13, §16,
# §17 F3.2): o pacote que a LLM e o Personal Trainer externo consomem.
# ---------------------------------------------------------------------------

PACKAGE_DEFAULT_WINDOW = WindowKind.ROLLING_28D

# Limite de acesso por usuario. E uma rede de contencao DELIBERADAMENTE
# modesta, e o PR diz isso em voz alta: o contador vive na memoria do processo,
# entao numa Lambda ele e POR CONTAINER e nao sobrevive a um cold start. Ele
# serve para o caso real (um script do PT em laco, ou uma tela que dispara em
# loop) e nao serve como defesa contra abuso distribuido - essa, se um dia for
# necessaria, e usage plan do API Gateway, nao codigo de aplicacao.
PACKAGE_RATE_LIMIT = 30
PACKAGE_RATE_WINDOW_SECONDS = 60
_package_hits: dict[int, list[float]] = {}


def _rate_limit_package(user_id: int) -> None:
    from time import monotonic

    now = monotonic()
    hits = [t for t in _package_hits.get(user_id, []) if now - t < PACKAGE_RATE_WINDOW_SECONDS]
    if len(hits) >= PACKAGE_RATE_LIMIT:
        _package_hits[user_id] = hits
        log_event("intelligence_package.rate_limited", user_id=user_id, hits=len(hits))
        raise HTTPException(
            status_code=429,
            detail=(
                f"Limite de {PACKAGE_RATE_LIMIT} leituras do pacote por "
                f"{PACKAGE_RATE_WINDOW_SECONDS}s atingido. Tente novamente em instantes."
            ),
        )
    hits.append(now)
    _package_hits[user_id] = hits


@router.get("/intelligence-package", response_model=IntelligencePackageResponse)
def intelligence_package(
    window: str = Query(default=PACKAGE_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    training_type_id: Optional[int] = Query(default=None, ge=1),
    question: Optional[str] = Query(default=None, max_length=500),
    identity: Identity = Depends(get_current_identity),
) -> IntelligencePackageResponse:
    """O pacote de inteligência da janela — a MESMA leitura da visão geral,
    achatada num contrato fechado de no máximo 40 KB para um consumidor que
    não é a interface: um LLM ou o Personal Trainer externo.

    **Escopo, e por que ele é estrutural e não uma checagem.** Não existe
    parâmetro `user_id` nesta rota. O dono do pacote é sempre
    `identity.user_id`, resolvido pelo mesmo `get_current_identity` de todos
    os endpoints v3 (cookie de sessão, token de dispositivo ou token mestre).
    Ler o pacote de outra pessoa não é uma permissão negada — é uma chamada
    que não tem como ser escrita. Um filtro que se pode esquecer de aplicar é
    exatamente o tipo de defesa que falha em silêncio.

    **Somente leitura.** `GET`, nenhum efeito colateral de domínio: o pacote
    é montado a partir de `build_overview`, que já é a leitura da home.

    **Acesso do PT externo: proposta, não improviso.** Hoje esta rota aceita o
    mesmo conjunto de credenciais que o resto do hub — o que significa que o
    PT só a consome com uma credencial do próprio usuário. Um token de PT
    verdadeiro (escopo de leitura, revogável, sem poder de escrita) NÃO nasce
    aqui de lado: o mecanismo existente de `devices` resolve o token para o
    dono do dispositivo com o papel dele (M40, `security._device_identity`)
    e sem escopo de leitura, o que daria ao PT escrita em tudo do usuário. A proposta e o
    caminho estão no PR e no plano (§17 F3.2) — é decisão do usuário, não
    efeito colateral desta entrega.

    Custo: exatamente os mesmos statements de `/overview` (o pacote reaproveita
    a leitura, não faz uma segunda) — travado em
    `tests/integration/dataapi/test_medicao_dataset_pequeno.py`.
    """
    _rate_limit_package(identity.user_id)
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        resolved_type_id, source = _resolve_target_source(training_type_id, identity.user_id, resolved.date_to)
        payload = build_intelligence_package(
            conn,
            user_id=identity.user_id,
            window=resolved,
            now_local=user_now(timezone_name),
            question=question,
            training_type_id=resolved_type_id,
            training_type_source=source,
            conn_factory=_connect,
        )

    # Log de ACESSO, separado do log de montagem (`intelligence_package.built`,
    # em `intelligence_package.py`): quem leu, de que origem de identidade e
    # com que janela. O pacote e o objeto mais sensivel do hub - ele resume o
    # historico inteiro de uma pessoa numa pagina -, e "quem leu" precisa ser
    # uma pergunta respondivel sem adivinhar.
    log_event(
        "intelligence_package.accessed",
        user_id=identity.user_id,
        identity_source=identity.source,
        role=identity.role,
        window=resolved.kind.value,
        size_bytes=payload["size_bytes"],
        max_bytes=PACKAGE_MAX_BYTES,
        truncated=payload["truncated"],
    )
    return IntelligencePackageResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# F3.1 - motor de geracao: planejado x gerado x executado (plano §4 "Engine
# runs", §17 F3.1)
# ---------------------------------------------------------------------------

ENGINE_DEFAULT_WINDOW = WindowKind.ROLLING_90D


@router.get("/engine", response_model=EngineRunsResponse)
def engine_runs(
    window: str = Query(default=ENGINE_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=DEFAULT_RUNS_LIMIT, ge=1, le=MAX_RUNS_LIMIT),
    identity: Identity = Depends(get_current_identity),
) -> EngineRunsResponse:
    """As GERACOES de ficha da janela - o seletor da tela `/dashboard/engine`.

    - DUAS fontes, com preferencia e fallback DECLARADO: `generation_runs`
      (migracao 054, que registra toda geracao desde 2026-09-26, inclusive a
      de rascunho descartado) e, para as fichas anteriores a ela,
      `workout_plans.raw_payload.generation_audit`. Cada linha diz de qual
      fonte veio (`source`), porque do `raw_payload` nao sai a data da
      geracao, nem o job, nem o pedido.
    - Geracao cujo rascunho NAO virou ficha aparece com `workout_plan_id:
      null` e a limitacao propria: ela existe, e nao ha o que comparar.
    - Sempre 200: janela sem geracao responde `items: []`,
      `data_quality=INSUFFICIENT` e a frase que diz desde quando a serie
      existe - nunca um zero que pareca medida.
    - Custo fixo de 2 consultas; as contagens de `selection`/`safety_clamps`/
      `substitutions`/`fallbacks` saem de `JSON_LENGTH` no banco, e o
      `audit_json` inteiro nunca trafega aqui (§15).
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_engine_runs(conn, user_id=identity.user_id, window=resolved, limit=limit)

    return EngineRunsResponse.model_validate(payload)


@router.get("/engine/plans/{plan_id}", response_model=EnginePlanResponse)
def engine_plan(
    plan_id: int = Path(..., ge=1),
    window: str = Query(default=ENGINE_DEFAULT_WINDOW.value),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> EnginePlanResponse:
    """"Por que a minha ficha saiu assim, e o que eu fiz com ela?" - as tres
    colunas, item a item.

    - PLANEJADO e o que o MODELO pediu; GERADO e o que o motor colocou na
      ficha, com o porque (`selection`, `safety_clamps`, `substitutions`,
      `fallbacks` e a decisao de `progression_decisions`); EXECUTADO e o que
      a sessao registrou, pelo vinculo `exercises.workout_plan_exercise_id`.
    - No modo `percent_budget` o Modelo NAO nomeia exercicio: a coluna
      planejada mostra o orcamento do grupo e diz isso. Inventar "o Modelo
      pediu supino" seria atribuir a ele uma decisao do motor.
    - Os status dos itens sao os MESMOS do §5 (`adherence_service`), nunca um
      segundo vocabulario.
    - 404 quando a ficha nao existe OU nao e do usuario - o mesmo 404 nos
      dois casos, entao nenhum id alheio e confirmado (IDOR).
    - A ficha pode estar FORA da janela do filtro: o id manda, e a janela so
      acompanha o payload (mesma regra de `/workouts/{activity_id}`).
    """
    with _connect() as conn:
        timezone_name = _user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        payload = build_engine_plan(conn, plan_id, user_id=identity.user_id, window=resolved)

    if payload is None:
        raise HTTPException(status_code=404, detail="Ficha de treino não encontrada.")
    return EnginePlanResponse.model_validate(payload)
