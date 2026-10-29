"""M30 - as afirmacoes de CONTAGEM DE STATEMENTS rodando em TODO PR.

O PR #221 provou que a contagem de statements e deterministica (mesmo commit,
mesma maquina: 23 -> 23 statements com o tempo variando 62%) e converteu as
medicoes em asserçoes de contagem. Mas elas continuaram fora do caminho
critico, presas ao dataset de 3 anos: 16.000 series, ~14 min de runner nos dois
backends. Uma protecao que so roda sob demanda nao protege PR nenhum.

A descoberta que este arquivo explora: **a contagem nao depende do volume de
dados**. O que multiplica statements num N+1 e o numero de GRUPOS e de
EXERCICIOS na janela - nao quantas series cada exercicio tem. O dataset
`PEQUENO` (`tests/support/dataset_medicao.py`) tem a mesma FORMA do grande (12
grupos, 48 exercicios de catalogo, contribuicoes ponderadas) com 540 series em
vez de 16.000, e sustenta as MESMAS afirmacoes em segundos.

O que este arquivo trava, em todo PR, nos DOIS backends:

- M19: o ranking de exercicios por grupo (uma passada para todos os grupos)
  e identico ao do oraculo legado (uma passada por grupo);
- M29/overview: teto de statements do `overview`, e a meta do Modelo
  `equipment_series` resolvida EXATAMENTE 1x por requisicao (e nao 1x por
  semana);
- M20: a leitura agregada da recuperacao emite menos statements que a leitura
  serie a serie, e cabe no teto;
- `dashboard/muscles`: teto que nao acomoda uma passada por grupo.

O que CONTINUA exigindo o dataset grande (marcador `slow`) e por que:

1. o **relatorio de tempo** que vai na descricao dos PRs - tempo so significa
   algo no volume de producao;
2. `test_payload_nao_muda_full_history_vs_query_legada`: a query legada do
   historico completo so estoura o limite de 1 MB da RDS Data API com 16.000
   series - e o teste existe justamente para exibir esse estouro;
3. `test_payload_do_drilldown_cabe_no_limite_da_data_api` e o corte de
   `sets_limit`: o truncamento so acontece com muita serie;
4. os testes de EQUIVALENCIA campo a campo contra os oraculos legados
   (`legacy_top_exercises`, `legacy_volume_queries`): no dataset grande eles
   comparam milhares de linhas, e essa cobertura vale o custo sob demanda.

O poder de deteccao foi PROVADO reintroduzindo, neste dataset pequeno, duas
das regressoes artificiais do #221 (o N+1 por grupo do M19 e a meta do Modelo
resolvida por semana): os dois testes reprovam aqui, exatamente como reprovam
no dataset grande. Ver a descricao do PR.

    cd api && python -m pytest -s \\
        tests/integration/dataapi/test_medicao_dataset_pequeno.py
"""

from __future__ import annotations

import os

import pytest

from app import analytics, analytics_dashboard  # noqa: F401 - `analytics` importado para o backend de teste trocar o modulo
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.volume_service import VolumeService
from app.security import Identity
from tests.support.dataapi_backend import raw_mysql
from tests.support.dataset_medicao import (
    FROZEN_NOW,
    PEQUENO,
    criar_modelo_por_equipamento,
    garantir,
    remover_modelo,
)
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import congelar_relogio
from tests.support.legacy_overview_reads import patch_overview_com_leituras_legadas
from tests.support.medicao import assert_no_maximo_statements, assert_reduziu_statements
from tests.support.payload_diff import diferencas as _diferencas
from tests.support.sql_capture import recorded_sql

DAYS = 30
TZ = "America/Sao_Paulo"

# ---------------------------------------------------------------------------
# Tetos - MEDIDOS neste dataset, identicos nos dois backends
# ---------------------------------------------------------------------------
# Cada teto fica entre a contagem saudavel e a contagem da regressao que ele
# existe para pegar. Como a janela tem 12 grupos, qualquer passada por grupo
# soma pelo menos 12 statements - e nenhum destes tetos tem 12 de folga.

# Top-N por grupo do ranking do M19 (era o `STAGNATION_TOP_EXERCISES_PER_GROUP`
# do alerta de estagnacao, removido no M76 Lote 2c).
TOP_POR_GRUPO = 3

# `overview`: 54 saudavel (63 antes do M29), 67 com Modelo atribuido (76 antes).
# Aqui o teto e mais largo de proposito: o overview e um composto de muitos
# blocos, e essa soma cresce de forma LEGITIMA quando um bloco novo nasce (um
# teto apertado viraria vermelho falso, so de outro tipo). Mesmo largo, ele
# pega as duas regressoes que importam: a volta das leituras de plumbing do
# pre-M29 (63 / 76) e um N+1 por grupo (>= +12, a janela tem 12 grupos).
MAX_STATEMENTS_OVERVIEW = 62
MAX_STATEMENTS_OVERVIEW_COM_MODELO = 75

# `dashboard/muscles`: 11 saudavel. Uma passada por grupo somaria >= 12 (a
# janela tem 12 grupos), entao 18 acomoda consulta nova e nao acomoda N+1.
MAX_STATEMENTS_MUSCLES = 18

# Bloco POR SESSAO do M34 (`include_comparatives=true`): comparativo T-0..T-3,
# comparativo por equipamento e mapa de calor saem de UMA consulta (os tres
# sao recortes da mesma agregacao grupo x sessao x equipamento). O teto e o de
# cima MAIS UM, e o teste abaixo afirma a IGUALDADE - teto largo esconderia
# justamente a regressao provavel aqui, que e uma consulta por grupo ou por
# sessao.
MAX_STATEMENTS_MUSCLES_COM_COMPARATIVOS = MAX_STATEMENTS_MUSCLES + 1

# `get_recovery_overview` com leitura agregada (M20): 4 saudavel (referencia +
# historico empacotado + series da janela + TSB global), 12 na leitura serie a
# serie. Mesmo teto do teste `slow`.
MAX_STATEMENTS_RECOVERY_OVERVIEW = 8

# `dashboard/recovery-signals`: ~15 saudavel - a prontidao (4 acima) + 3
# leituras agregadas POR DIA (sono, wellness, dor) + 2 do check-in (sonda de
# tabela + leitura) + 4 do contexto + a resolucao do fuso do usuario. A F2.5
# (M33/M36) acrescentou recomendacao de series,
# fatores do calculo, ajuste global de fadiga e o recorte "dia com treino x
# dia sem treino" SEM nenhuma consulta nova: tudo sai de linhas ja lidas, e o
# ajuste global vem da instancia que acabou de rodar o overview. Este teto
# existe para essa afirmacao nao silenciar - uma passada por grupo somaria
# pelo menos 12 (a janela tem 12 grupos), e ele nao tem 12 de folga.
MAX_STATEMENTS_RECOVERY_SIGNALS = 21

# Fragmentos ESTAVEIS das duas consultas que resolvem a meta do Modelo - os
# mesmos de `test_dashboard_overview_performance.py` (a subconsulta de
# `slots_count` aparece nas duas variantes do cabecalho, entao a afirmacao
# sobrevive a mudanca de regra de versao do Modelo virar).
# Assinatura do cabecalho do Modelo (`ASSIGNED_TEMPLATE_HEAD_SQL`). Desde a
# 065 a contagem de slots filtra a versao vigente no mesmo statement; o
# alias `slots_count` continua sendo exclusivo desse SQL.
SQL_MODELO_CABECALHO = "WHERE s.version_id = v.id) AS slots_count"
SQL_MODELO_ATIVACAO = "SUM(i.sets_count * emc.stimulus_weight)"


@pytest.fixture
def dataset_pequeno(mysql_schema) -> dict:
    """Dataset PEQUENO, semeado sob demanda (ver `garantir`)."""
    with raw_mysql(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=mysql_schema,
    ) as conn:
        return garantir(conn, PEQUENO)


@pytest.fixture
def relogio(monkeypatch):
    """Mesmo instante do seed. Regra do projeto: nada de `date.today()` num
    teste de dashboard - o relogio do usuario e o unico que manda."""
    congelar_relogio(monkeypatch, FROZEN_NOW)


@pytest.fixture(autouse=True)
def grupo_b(raw):
    """`intervals_wellness`/`sleep_metrics` nascem na migracao 027 (grupo B),
    que o schema base da suite nao aplica - o modelo de recuperacao le essas
    tabelas em QUALQUER caminho medido aqui (o TSB global entra ate no
    `overview`), entao a fixture e autouse: sem ela a medicao mede um
    erro, nao o endpoint."""
    ensure_grupo_b_tables(raw)


@pytest.fixture
def modelo_por_equipamento(raw, dataset_pequeno):
    template_id = criar_modelo_por_equipamento(raw, user_id=dataset_pequeno["user_id"])
    yield template_id
    remover_modelo(raw, template_id)


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="session")


def _ocorrencias(statements, fragmento):
    return [x for x in statements if fragmento in " ".join(str(x).split())]


def _overview(identity, *, window="28d"):
    return analytics_dashboard.overview(
        window=window, date_from=None, date_to=None, training_type_id=None, identity=identity
    )


def _recovery_signals(identity, *, window="7d"):
    return analytics_dashboard.recovery_signals(
        window=window, date_from=None, date_to=None, identity=identity
    )


def _muscles(identity, *, window="28d", include_comparatives=False):
    return analytics_dashboard.muscles(
        window=window,
        date_from=None,
        date_to=None,
        training_type_id=None,
        include_readiness=False,
        include_comparatives=include_comparatives,
        identity=identity,
    )


# ---------------------------------------------------------------------------
# 0) O dataset pequeno tem a FORMA que da poder de deteccao
# ---------------------------------------------------------------------------


def test_dataset_pequeno_tem_a_forma_que_detecta_n_mais_1(db_backend, dataset_pequeno, relogio):
    """Guarda-corpo do arquivo inteiro: um N+1 por grupo so aparece se houver
    GRUPOS na janela, e um N+1 por exercicio so aparece se houver EXERCICIOS.
    Se um dia o seed encolher a ponto de a janela ficar com 2 grupos, os tetos
    abaixo continuariam verdes com o N+1 de volta - e este teste falha antes
    disso."""
    user_id = dataset_pequeno["user_id"]
    grupos = VolumeService().get_muscle_group_overview(days=DAYS, limit=100, user_id=user_id)
    assert len(grupos) >= 10, f"so {len(grupos)} grupos na janela de {DAYS}d"

    identity = _identity(user_id)
    payload = _overview(identity)
    assert payload.blocks.progression.exercises_total >= 20, (
        f"so {payload.blocks.progression.exercises_total} exercicios na janela"
    )
    assert (payload.blocks.consistency.completed_sessions.value or 0) >= 4, "poucas sessoes na janela"


# ---------------------------------------------------------------------------
# 1) M19 - ranking por grupo sem a passada por grupo muscular
# ---------------------------------------------------------------------------


def test_ranking_por_grupo_e_igual_ao_legado(db_backend, dataset_pequeno, relogio):
    """Contagem menor so vale se a RESPOSTA for a mesma: o ranking de cada
    grupo da janela tem que bater com o do oraculo legado."""
    from tests.support.legacy_top_exercises import top_exercises_for_muscle_group_legacy

    user_id = dataset_pequeno["user_id"]
    rows = VolumeService().get_muscle_group_overview(days=DAYS, limit=100, user_id=user_id)
    grupos = [int(row["muscle_group_id"]) for row in rows if row.get("muscle_group_id") is not None]
    assert grupos, "cenario vazio nao provaria nada"

    novo = VolumeService().get_top_exercises_by_muscle_group(
        days=DAYS, user_id=user_id, limit=TOP_POR_GRUPO
    )
    legado_service = VolumeService()
    for group_id in grupos:
        legado = top_exercises_for_muscle_group_legacy(
            legado_service, group_id, DAYS, user_id=user_id, limit=TOP_POR_GRUPO
        )
        assert legado == novo.get(group_id, []), f"grupo {group_id}"


# ---------------------------------------------------------------------------
# 2) Overview - teto e a meta do Modelo resolvida uma vez
# ---------------------------------------------------------------------------


def test_statements_do_overview(db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds):
    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as stmts:
            payload = _overview(identity)

    print(f"[MEDIDO] overview backend={db_backend}: {len(stmts)}")
    assert payload.blocks.progression.exercises_total > 0
    assert_no_maximo_statements(
        stmts, teto=MAX_STATEMENTS_OVERVIEW, label="overview window=28d", backend=db_backend
    )


def test_pacote_de_inteligencia_custa_o_mesmo_que_o_overview(
    db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds
):
    """F3.2: o `intelligence_package` e um ACHATAMENTO do overview, nao uma
    segunda leitura.

    O overview ja lia eventos de contexto, grupos musculares e intervalo desde
    a ultima sessao para alimentar as regras de insight; o pacote so ganhou um
    caminho de saida para esses insumos. Entao a contagem tem que ser
    **igual** - nao "parecida", nao "dentro do teto".

    A regressao que este teste pega e concreta e provavel: alguem chamar
    `build_context`/`build_muscle_summary` de dentro do montador "para ter o
    dado do jeito certo". Num caminho de RDS Data API, cada statement e uma
    ida ao servico."""
    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as do_overview:
            _overview(identity)
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as do_pacote:
            pacote = analytics_dashboard.intelligence_package(
                window="28d",
                date_from=None,
                date_to=None,
                training_type_id=None,
                question=None,
                identity=identity,
            )

    print(f"[MEDIDO] pacote backend={db_backend}: overview={len(do_overview)} pacote={len(do_pacote)}")
    assert pacote.metrics, "cenario vazio nao provaria nada"
    assert len(do_pacote) == len(do_overview), (
        "o pacote passou a ler o banco por conta propria: "
        f"{len(do_pacote)} statements contra {len(do_overview)} do overview"
    )
    assert_no_maximo_statements(
        do_pacote,
        teto=MAX_STATEMENTS_OVERVIEW,
        label="intelligence-package window=28d",
        backend=db_backend,
    )


def test_insights_nao_paga_o_veredito_de_qualidade_dos_dados(
    db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds
):
    """PERF-02: `/insights` reusa a avaliacao do overview, mas o veredito de
    qualidade dos dados (`build_data_quality`, as consultas `COUNT(*) OVER ()`)
    so serve a visao geral - nao entra nas regras nem no cooldown. Se alguem
    voltar a chama-lo pelo caminho dos insights, este teste reprova."""
    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as do_overview:
            _overview(identity)
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as do_insights:
            analytics_dashboard.insights(
                window="28d",
                date_from=None,
                date_to=None,
                training_type_id=None,
                limit=20,
                offset=0,
                include_suppressed=True,
                identity=identity,
            )

    veredito = [sql for sql in do_overview if "OVER ()" in sql]
    print(
        f"[MEDIDO] insights backend={db_backend}: overview={len(do_overview)} "
        f"insights={len(do_insights)} veredito={len(veredito)}"
    )
    assert veredito, "o overview deveria calcular o veredito (cenario nao provaria nada)"
    assert not [sql for sql in do_insights if "OVER ()" in sql]
    assert len(do_insights) < len(do_overview)
    assert_no_maximo_statements(
        do_insights, teto=MAX_STATEMENTS_OVERVIEW, label="insights window=28d", backend=db_backend
    )


def test_overview_resolve_a_meta_do_modelo_uma_vez(
    db_backend, dataset_pequeno, relogio, modelo_por_equipamento, monkeypatch, fake_rds
):
    """`build_overview` resolve o Modelo UMA vez e repassa o objeto as tres
    leituras (janela, semana atual, semana anterior). Se a resolucao voltar a
    acontecer por semana, aparecem 3 - em qualquer maquina, com qualquer
    volume de dados."""
    from app.intelligence.dashboard.contracts import TargetSource

    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as stmts:
            payload = _overview(identity)

    print(f"[MEDIDO] overview COM Modelo backend={db_backend}: {len(stmts)}")
    assert payload.target_source is TargetSource.TEMPLATE_PLAN
    for fragmento, nome in (
        (SQL_MODELO_CABECALHO, "cabecalho do Modelo atribuido"),
        (SQL_MODELO_ATIVACAO, "ativacao planejada por grupo"),
    ):
        vezes = len(_ocorrencias(stmts, fragmento))
        assert vezes == 1, (
            f"{nome}: {vezes} consultas em UM overview no backend {db_backend} "
            "(esperado exatamente 1). Mais de uma ocorrencia significa que o "
            "reaproveitamento da resolucao se perdeu; zero significa que o "
            "cenario nao esta exercitando o Modelo."
        )
    assert_no_maximo_statements(
        stmts,
        teto=MAX_STATEMENTS_OVERVIEW_COM_MODELO,
        label="overview window=28d COM Modelo",
        backend=db_backend,
    )


# ---------------------------------------------------------------------------
# 3) `dashboard/muscles` e a leitura agregada da recuperacao (M20)
# ---------------------------------------------------------------------------


def test_statements_do_muscles(db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds):
    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as stmts:
            lista = _muscles(identity)
    print(f"[MEDIDO] muscles backend={db_backend}: {len(stmts)}")
    assert lista.items
    assert_no_maximo_statements(
        stmts, teto=MAX_STATEMENTS_MUSCLES, label="dashboard/muscles 28d", backend=db_backend
    )


def test_statements_do_bloco_por_sessao(db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds):
    """M34: o bloco por sessao custa exatamente UMA consulta a mais."""
    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as sem_bloco:
            _muscles(identity)
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as com_bloco:
            lista = _muscles(identity, include_comparatives=True)
    print(
        f"[MEDIDO] muscles+comparativos backend={db_backend}: "
        f"{len(sem_bloco)} -> {len(com_bloco)}"
    )
    assert_no_maximo_statements(
        com_bloco,
        teto=MAX_STATEMENTS_MUSCLES_COM_COMPARATIVOS,
        label="dashboard/muscles 28d + comparativos",
        backend=db_backend,
    )
    assert len(com_bloco) == len(sem_bloco) + 1
    assert lista.comparatives is not None
    assert lista.comparatives.unit == "session"


def test_statements_da_recuperacao_no_hub(
    db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds, raw
):
    """F2.5 (M33/M36): o detalhamento que veio de `/recuperacao-muscular` e o
    recorte que veio de `/treinos` nao custaram consulta nenhuma. Se alguem
    voltar a buscar o ajuste global numa segunda ida ao banco, ou ler o
    historico por grupo para montar os fatores, este teto reprova.

    M76 B3: a prontidao desta tela usa a janela unica do modelo
    (`RECOVERY_LOOKBACK_DAYS`). Desde o Lote 2b ela e contada do "hoje" do
    USUARIO (`user_today`, que `relogio` congela em `FROZEN_NOW`), e nao mais
    de `CURDATE()` do banco - o seed fica dentro da janela em qualquer data
    em que a suite rodar, sem ancoragem manual."""
    identity = _identity(dataset_pequeno["user_id"])
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as stmts:
            corpo = _recovery_signals(identity, window="28d")
    print(f"[MEDIDO] recovery-signals backend={db_backend}: {len(stmts)}")
    assert corpo.groups, "cenario vazio nao provaria nada"
    assert any(grupo.recommendation is not None for grupo in corpo.groups)
    assert corpo.training_vs_rest.metrics
    assert_no_maximo_statements(
        stmts,
        teto=MAX_STATEMENTS_RECOVERY_SIGNALS,
        label="dashboard/recovery-signals 28d",
        backend=db_backend,
    )


def test_statements_da_leitura_agregada_da_recuperacao(
    db_backend, dataset_pequeno, relogio, monkeypatch, fake_rds, raw
):
    """M20: a leitura empacotada emite menos idas ao banco que a leitura
    serie a serie - e cabe num teto onde nenhuma passada por grupo cabe."""
    user_id = dataset_pequeno["user_id"]
    dias = 14  # contados do "hoje" congelado do usuario (Lote 2b), nao de CURDATE()
    with monkeypatch.context() as ctx:
        with recorded_sql(db_backend, ctx, fake_rds) as antes:
            MuscleRecoveryService().get_recovery_overview(
                days=dias, limit=100, user_id=user_id, aggregated_read=False
            )
    with recorded_sql(db_backend, monkeypatch, fake_rds) as depois:
        agregado = MuscleRecoveryService().get_recovery_overview(
            days=dias, limit=100, user_id=user_id, aggregated_read=True
        )

    print(f"[MEDIDO] recovery agregada backend={db_backend}: {len(antes)} -> {len(depois)}")
    assert agregado, "cenario vazio nao provaria nada"
    assert_reduziu_statements(
        antes, depois, label="get_recovery_overview (leitura agregada do M20)", backend=db_backend
    )
    assert_no_maximo_statements(
        depois,
        teto=MAX_STATEMENTS_RECOVERY_OVERVIEW,
        label="get_recovery_overview (leitura agregada do M20)",
        backend=db_backend,
    )


# ---------------------------------------------------------------------------
# 4) M29 - menos statements no overview, com o MESMO payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("com_modelo", [False, True])
def test_overview_payload_nao_muda_com_as_leituras_encurtadas(
    db_backend, dataset_pequeno, relogio, raw, monkeypatch, com_modelo
):
    """M29, a metade que importa: o payload INTEIRO do overview e identico
    campo a campo ao do caminho ANTIGO (oraculo literal em
    `tests/support/legacy_overview_reads.py`), nos dois backends, com e sem
    Modelo atribuido. Otimizacao que muda resposta e regressao, nao ganho."""
    template_id = (
        criar_modelo_por_equipamento(raw, user_id=dataset_pequeno["user_id"]) if com_modelo else None
    )
    try:
        identity = _identity(dataset_pequeno["user_id"])
        depois = _overview(identity).model_dump(mode="json")
        with monkeypatch.context() as ctx:
            patch_overview_com_leituras_legadas(ctx)
            antes = _overview(identity).model_dump(mode="json")
    finally:
        if template_id is not None:
            remover_modelo(raw, template_id)

    assert antes["target_source"] == ("template_plan" if com_modelo else "fallback_generic")
    problemas = _diferencas(antes, depois, "overview", 0.0)
    assert not problemas, f"{len(problemas)} divergencias:\n" + "\n".join(problemas[:40])


@pytest.mark.parametrize("com_modelo", [False, True])
def test_overview_emite_menos_statements_que_o_caminho_legado(
    db_backend, dataset_pequeno, relogio, raw, monkeypatch, fake_rds, com_modelo
):
    """M29, a metade que se mede: o caminho novo emite MENOS idas ao banco que
    o antigo. O que sumiu foram leituras de PLUMBING - o nome do schema, que a
    conexao ja sabia, e a releitura de `muscle_groups` a cada janela."""
    template_id = (
        criar_modelo_por_equipamento(raw, user_id=dataset_pequeno["user_id"]) if com_modelo else None
    )
    identity = _identity(dataset_pequeno["user_id"])
    try:
        with monkeypatch.context() as ctx:
            patch_overview_com_leituras_legadas(ctx)
            with recorded_sql(db_backend, ctx, fake_rds) as antes:
                _overview(identity)
        with recorded_sql(db_backend, monkeypatch, fake_rds) as depois:
            _overview(identity)
    finally:
        if template_id is not None:
            remover_modelo(raw, template_id)

    print(f"[MEDIDO] M29 overview com_modelo={com_modelo} {db_backend}: {len(antes)} -> {len(depois)}")
    assert_reduziu_statements(antes, depois, label="overview (M29)", backend=db_backend)
    assert not _ocorrencias(depois, "SELECT DATABASE()"), (
        "o nome do schema voltou a ser perguntado ao banco: a conexao ja o "
        "conhece (`load_conventions.current_schema`)."
    )
    assert len(_ocorrencias(depois, "SELECT id, name FROM muscle_groups")) <= 1, (
        "`muscle_groups` voltou a ser lida uma vez por janela; a visao geral "
        "resolve os nomes UMA vez e repassa em `group_names=`."
    )


# ---------------------------------------------------------------------------
# 5) PERF-01 - memo POR REQUISICAO (Modelo, fuso, nomes de grupos)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("com_modelo", [False, True])
def test_overview_em_escopo_de_requisicao_le_modelo_e_fuso_uma_vez(
    db_backend, dataset_pequeno, relogio, raw, monkeypatch, fake_rds, com_modelo
):
    """`RequestMemoMiddleware` abre o escopo numa requisicao GET: dentro dele o
    overview le o Modelo inteiro e `users.timezone` UMA vez, com a resposta
    IDENTICA a do mesmo codigo sem escopo (que continua lendo a cada chamada)."""
    from app import request_memo

    template_id = (
        criar_modelo_por_equipamento(raw, user_id=dataset_pequeno["user_id"]) if com_modelo else None
    )
    identity = _identity(dataset_pequeno["user_id"])
    try:
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as sem_escopo:
                antes = _overview(identity).model_dump(mode="json")
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as com_escopo:
                with request_memo.request_scope():
                    depois = _overview(identity).model_dump(mode="json")
    finally:
        if template_id is not None:
            remover_modelo(raw, template_id)

    print(f"[MEDIDO] PERF-01 overview com_modelo={com_modelo} {db_backend}: {len(sem_escopo)} -> {len(com_escopo)}")
    problemas = _diferencas(antes, depois, "overview", 0.0)
    assert not problemas, f"{len(problemas)} divergencias:\n" + "\n".join(problemas[:40])
    assert_reduziu_statements(sem_escopo, com_escopo, label="overview (PERF-01)", backend=db_backend)
    assert len(_ocorrencias(com_escopo, "SELECT timezone FROM users")) <= 1
    assert len(_ocorrencias(com_escopo, "SELECT id, name FROM muscle_groups")) <= 1