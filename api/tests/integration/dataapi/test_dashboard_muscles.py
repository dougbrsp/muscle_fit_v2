"""`GET /analytics/dashboard/muscles` e `/muscles/{muscle_group_id}` (F1.2)
contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API
de producao).

O que este arquivo prova:

- o payload e IDENTICO nos dois backends (nenhuma regra depende do tipo que o
  driver devolve - `Decimal` vs `float` foi exatamente o que quebrou leituras
  parecidas antes);
- **IDOR**: o usuario A nao ve serie, sessao nem volume do usuario B, nem no
  resumo nem no drill-down do mesmo grupo muscular (o catalogo de grupos e
  compartilhado, o historico nao);
- realizado, planejado e projetado ficam em campos SEPARADOS e o `status` sai
  so do realizado;
- direto / indireto / estabilizador sao tres numeros distintos;
- aquecimento e serie de ajuste NAO entram no estimulo (plano §6);
- exercicio sem contribuicao cadastrada vira limitacao declarada, nao some
  em silencio nem inventa grupo;
- o drill-down chega ate a SERIE de origem e declara o corte quando trunca.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import analytics_dashboard
from app.intelligence.dashboard import muscle_summary_service  # noqa: F401 - precisa estar importado
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL (tests/support/relogio_usuario.py)


SUPINO = 2001
REMADA = 2002
SEM_CONTRIBUICAO = 2003


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Dois usuarios, tres grupos, tres exercicios.

    O supino distribui nos TRES papeis (peitoral PRIMARY, triceps SECONDARY,
    ombro STABILIZER) - e o que permite provar que estabilizador nao e
    fundido com secundario."""
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel)
            )
            ids[nome] = cur.lastrowid

        for grupo in ("Peitoral", "Triceps", "Ombro", "Costas"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid

        for exercise_id, nome in (
            (SUPINO, "Supino reto"),
            (REMADA, "Remada curvada"),
            (SEM_CONTRIBUICAO, "Exercicio sem cadastro"),
        ):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, nome, nome),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, nome))

        cur.executemany(
            "INSERT INTO exercise_muscle_contributions "
            "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) VALUES (%s,%s,%s,%s,%s)",
            [
                (SUPINO, ids["Peitoral"], "PRIMARY", 1.0, 1.0),
                (SUPINO, ids["Triceps"], "SECONDARY", 0.5, 0.4),
                (SUPINO, ids["Ombro"], "STABILIZER", 0.25, 0.2),
                (REMADA, ids["Costas"], "PRIMARY", 1.0, 1.0),
            ],
        )
    return ids


def _sessao(raw, *, user_id, dias_atras, mapping_id, series, sequence=1, hora=19):
    """Uma sessao com um exercicio. `series` = `(set_number, set_type, reps, peso, rpe)`."""
    dia = HOJE - _dt.timedelta(days=dias_atras)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, session_status) "
            "VALUES (%s, %s, %s, 'Treino', 'done')",
            (user_id, dia, _dt.datetime.combine(dia, _dt.time(hora, 0))),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
            "VALUES (%s, %s, 'Exercicio', %s, %s)",
            (activity_id, sequence, mapping_id, len(series)),
        )
        exercise_row_id = cur.lastrowid
        for numero, tipo, reps, peso, rpe in series:
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (exercise_row_id, numero, tipo, reps, peso, rpe),
            )
    return activity_id


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _muscles(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/muscles", params=params)


def _drilldown(user_id: int, muscle_group_id: int, **params):
    return _client(user_id).get(f"/analytics/dashboard/muscles/{muscle_group_id}", params=params)


def _item(corpo: dict, muscle_group_id: int) -> dict:
    return next(i for i in corpo["items"] if i["muscle_group_id"] == muscle_group_id)


TRABALHO = [(1, "working", 10, "60.00", "8.00"), (2, "working", 10, "60.00", "8.50")]


# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _sessao(raw, user_id=seed["dono"], dias_atras=3, mapping_id=SUPINO, series=TRABALHO)
    _sessao(raw, user_id=seed["dono"], dias_atras=1, mapping_id=REMADA, series=TRABALHO)

    # Relogio congelado: `hours_since_last_stimulus`, `remaining_hours`,
    # `recovery_pct` e `reassess_after_hours` saem de `now - last_trained_at` e
    # sao arredondados a 2 casas de HORA (degrau de 36 s). Sem congelar, as
    # duas chamadas caem em lados diferentes de um degrau de vez em quando e a
    # comparacao vira ruido - foi o flake do CI no PR #199 (medido: duas
    # chamadas a 40 s de distancia divergem em 5 campos).
    congelar_relogio(monkeypatch)

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _muscles(seed["dono"], window="28d")
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]

    corpo = resultados["pymysql"]
    assert corpo["window"]["timezone"] == "America/Sao_Paulo"
    assert corpo["metric_versions"]["muscle_frequency"] == "freq-v1"
    assert corpo["metric_versions"]["effective_sets_stimulus"] == "effective-sets-v1"


def test_tres_papeis_ficam_separados(seed, raw, db_backend):
    """Uma sessao de supino gera trabalho DIRETO no peitoral, INDIRETO no
    triceps e ESTABILIZACAO no ombro - tres itens, tres papeis, nunca
    fundidos."""
    _sessao(raw, user_id=seed["dono"], dias_atras=3, mapping_id=SUPINO, series=TRABALHO)

    corpo = _muscles(seed["dono"], window="28d").json()

    peitoral = _item(corpo, seed["Peitoral"])
    triceps = _item(corpo, seed["Triceps"])
    ombro = _item(corpo, seed["Ombro"])

    assert peitoral["realized"]["direct_sets"]["value"] == 2
    assert peitoral["realized"]["indirect_sets"]["value"] == 0
    assert peitoral["realized"]["stabilizer_sets"]["value"] == 0
    assert peitoral["realized"]["effective_sets_stimulus"]["value"] == 2.0

    assert triceps["realized"]["direct_sets"]["value"] == 0
    assert triceps["realized"]["indirect_sets"]["value"] == 2
    assert triceps["realized"]["effective_sets_stimulus"]["value"] == 1.0

    assert ombro["realized"]["stabilizer_sets"]["value"] == 2
    assert ombro["realized"]["indirect_sets"]["value"] == 0
    assert ombro["realized"]["effective_sets_stimulus"]["value"] == 0.5

    # Estimulo e fadiga sao dimensoes distintas e nao se confundem.
    assert triceps["realized"]["effective_sets_fatigue"]["value"] == pytest.approx(0.8)


def test_aquecimento_e_serie_de_ajuste_ficam_fora_do_estimulo(seed, raw, db_backend):
    """Plano §6: `effective_sets_stimulus = Sigma working x stimulus_weight`.
    As rotas v2 somavam aquecimento porque nao filtravam `set_type`."""
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=2,
        mapping_id=SUPINO,
        series=[
            (1, "warmup", 12, "20.00", None),
            (2, "feeler", 8, "40.00", None),
            (3, "working", 10, "60.00", "8.00"),
        ],
    )

    peitoral = _item(_muscles(seed["dono"], window="28d").json(), seed["Peitoral"])
    assert peitoral["realized"]["physical_sets"]["value"] == 1
    assert peitoral["realized"]["effective_sets_stimulus"]["value"] == 1.0


# ---------------------------------------------------------------------------
# F2.5 / M34 - comparativos POR SESSAO e mapa de calor
# ---------------------------------------------------------------------------


def test_bloco_por_sessao_so_vem_quando_pedido(seed, raw, db_backend):
    """`None` e "nao foi pedido"; `[]` seria "foi pedido e nao ha nada". A
    diferenca importa: a tela nao pode mostrar "sem sessoes" so porque a
    chamada da prontidao nao pediu o bloco."""
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    assert _muscles(seed["dono"], window="28d").json()["comparatives"] is None

    bloco = _muscles(seed["dono"], window="28d", include_comparatives="true").json()["comparatives"]
    assert bloco is not None
    assert bloco["unit"] == "session"
    assert bloco["sessions_compared"] == 1


def test_t0_e_a_ultima_sessao_do_grupo_nao_a_ultima_semana(seed, raw, db_backend):
    """A unidade e SESSAO. Dois treinos no MESMO dia sao T-0 e T-1 - por
    semana eles virariam um ponto so, e a tela nao mostraria que houve dois.
    Foi essa mistura, sem rotulo, que a `/volume-grupamentos` fazia."""
    _sessao(raw, user_id=seed["dono"], dias_atras=1, mapping_id=SUPINO, series=TRABALHO, hora=8)
    _sessao(
        raw, user_id=seed["dono"], dias_atras=1, mapping_id=SUPINO, series=TRABALHO, hora=19, sequence=1
    )
    _sessao(raw, user_id=seed["dono"], dias_atras=9, mapping_id=SUPINO, series=TRABALHO)

    bloco = _muscles(seed["dono"], window="28d", include_comparatives="true").json()["comparatives"]
    peitoral = next(g for g in bloco["by_session"] if g["muscle_group_id"] == seed["Peitoral"])

    assert [p["offset"] for p in peitoral["points"]] == [0, 1, 2]
    assert peitoral["sessions_in_window"] == 3
    # As duas do mesmo dia ocupam DUAS colunas.
    assert peitoral["points"][0]["local_date"] == peitoral["points"][1]["local_date"]
    assert any("SESSÃO" in limitation for limitation in bloco["limitations"])


def test_aquecimento_fica_fora_tambem_do_rpe_por_sessao(seed, raw, db_backend):
    """A decisao do M35/M34: RPE agrega SO series `working`.

    Aqui o aquecimento tem RPE 3 e a serie de trabalho, RPE 9. Incluindo o
    aquecimento a media seria 6 - uma sessao dura pareceria moderada. A tela
    de progressao ja le so `working`; dois RPE medios incompativeis na mesma
    interface seriam piores que nenhum."""
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=2,
        mapping_id=SUPINO,
        series=[
            (1, "warmup", 12, "20.00", "3.00"),
            (2, "working", 10, "60.00", "9.00"),
        ],
    )

    bloco = _muscles(seed["dono"], window="28d", include_comparatives="true").json()["comparatives"]
    peitoral = next(g for g in bloco["by_session"] if g["muscle_group_id"] == seed["Peitoral"])
    ponto = peitoral["points"][0]

    assert ponto["avg_rpe"] == 9.0
    assert ponto["physical_sets"] == 1
    # A tonelagem segue a mesma regra: so a serie de trabalho.
    assert ponto["tonnage_kg"] == 600.0


def test_mapa_de_calor_classifica_no_backend_e_normaliza_dentro_da_sessao(seed, raw, db_backend):
    """Cor nunca e o unico sinal (§14): a faixa vem NOMEADA do backend, junto
    com a razao que a gerou."""
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    bloco = _muscles(seed["dono"], window="28d", include_comparatives="true").json()["comparatives"]
    sessao = bloco["heatmap"][0]
    celulas = {c["muscle_group_id"]: c for c in sessao["cells"]}

    peitoral = celulas[seed["Peitoral"]]
    triceps = celulas[seed["Triceps"]]

    # O peitoral e o maior da sessao: ele e a referencia, nao um absoluto.
    assert peitoral["share_of_max"] == 1.0
    assert peitoral["band"] == "VERY_HIGH"
    # Triceps com metade do estimulo do peitoral.
    assert triceps["share_of_max"] == 0.5
    assert triceps["band"] == "MODERATE"
    assert all(cell["band"] for cell in sessao["cells"])
    assert any("mesma sessão" in limitation for limitation in bloco["limitations"])


def test_status_sai_so_do_realizado_e_declara_a_base(seed, raw, db_backend):
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    peitoral = _item(_muscles(seed["dono"], window="28d").json(), seed["Peitoral"])

    assert peitoral["status_basis"] == "realized_only"
    # 2 series efetivas em 28 dias = 0,5/semana -> abaixo do minimo generico (6)
    assert peitoral["status"] == "BELOW_MIN"
    assert peitoral["realized"]["weekly_effective_sets"]["value"] == pytest.approx(0.5)
    # Planejado e projetado existem em campos PROPRIOS, com natureza propria.
    assert peitoral["planned"]["effective_sets"]["nature"] == "PLANNED"
    assert peitoral["projected"]["remaining_sets"]["nature"] == "PROJECTED"


def test_meta_generica_derruba_a_qualidade_do_item(seed, raw, db_backend):
    """Sem Meta de Ativacao atribuida, "abaixo/acima" nao reflete objetivo
    nenhum - e a tela precisa dizer isso."""
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    corpo = _muscles(seed["dono"], window="28d").json()
    peitoral = _item(corpo, seed["Peitoral"])

    assert corpo["target_source"] == "fallback_generic"
    assert peitoral["targets"]["source"] == "fallback_generic"
    assert peitoral["data_quality"] == "LOW"
    assert any("meta cadastrada" in limitacao for limitacao in peitoral["limitations"])


def test_meta_explicita_sobe_a_qualidade_e_muda_o_status(seed, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO training_types (name, created_by_user_id) VALUES ('Hipertrofia', %s)",
            (seed["dono"],),
        )
        training_type_id = cur.lastrowid
        cur.execute(
            "INSERT INTO training_type_muscle_group_targets "
            "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets) "
            "VALUES (%s, %s, 0.2, 0.5, 1.0)",
            (training_type_id, seed["Peitoral"]),
        )
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    corpo = _muscles(seed["dono"], window="28d", training_type_id=training_type_id).json()
    peitoral = _item(corpo, seed["Peitoral"])

    assert corpo["target_source"] == "explicit"
    assert peitoral["targets"]["source"] == "explicit"
    assert peitoral["targets"]["training_type_id"] == training_type_id
    assert peitoral["data_quality"] == "HIGH"
    assert peitoral["status"] == "ON_TARGET"  # 0,5/semana == alvo

    # O triceps NAO tem linha de meta: cai em fallback individualmente, mesmo
    # com `training_type_id` explicito na requisicao.
    triceps = _item(corpo, seed["Triceps"])
    assert triceps["targets"]["source"] == "fallback_generic"
    assert triceps["targets"]["training_type_id"] is None


def test_frequencia_e_intervalo_entre_estimulos(seed, raw, db_backend):
    for dias in (6, 4, 2):
        _sessao(raw, user_id=seed["dono"], dias_atras=dias, mapping_id=SUPINO, series=TRABALHO)

    peitoral = _item(_muscles(seed["dono"], window="28d").json(), seed["Peitoral"])

    assert peitoral["frequency"]["direct_sessions"]["value"] == 3
    assert peitoral["frequency"]["participation_sessions"]["value"] == 3
    assert peitoral["frequency"]["median_interval_hours"]["value"] == pytest.approx(48.0)


def test_um_unico_estimulo_nao_tem_intervalo(seed, raw, db_backend):
    """`None`, nunca `0`: zero diria que houve dois estimulos simultaneos."""
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    peitoral = _item(_muscles(seed["dono"], window="28d").json(), seed["Peitoral"])
    assert peitoral["frequency"]["median_interval_hours"]["value"] is None
    assert peitoral["frequency"]["median_interval_hours"]["limitations"]


def test_exercicio_sem_contribuicao_vira_limitacao_declarada(seed, raw, db_backend):
    """Nao some em silencio (o volume ficaria menor do que foi treinado) nem
    inventa um grupo "Outros" que nao existe em `muscle_groups`."""
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=2,
        mapping_id=SEM_CONTRIBUICAO,
        series=TRABALHO,
        sequence=2,
    )

    corpo = _muscles(seed["dono"], window="28d").json()
    assert corpo["unattributed_sets"] == 2
    assert any("sem contribuicao" in limitacao for limitacao in corpo["limitations"])
    assert all(item["muscle_group_id"] in seed.values() for item in corpo["items"])


def test_top_exercicios_trazem_o_peso_da_contribuicao(seed, raw, db_backend):
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    peitoral = _item(_muscles(seed["dono"], window="28d").json(), seed["Peitoral"])
    (top,) = peitoral["top_exercises"]

    assert top["exercise_id"] == SUPINO
    assert top["name"] == "Supino reto"
    assert top["contribution_share"] == 1.0
    assert top["roles"] == ["PRIMARY"]


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------


def test_drilldown_chega_ate_a_serie_de_origem(seed, raw, db_backend):
    activity_id = _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    resposta = _drilldown(seed["dono"], seed["Peitoral"], window="28d")
    assert resposta.status_code == 200, db_backend
    corpo = resposta.json()

    assert corpo["item"]["muscle_group_id"] == seed["Peitoral"]
    (sessao,) = corpo["sessions"]
    assert sessao["activity_id"] == activity_id
    assert sessao["has_direct_work"] is True
    (exposicao,) = sessao["exposures"]
    assert exposicao["exercise_id"] == SUPINO
    assert exposicao["role"] == "PRIMARY"
    assert [s["set_number"] for s in exposicao["sets"]] == [1, 2]
    assert exposicao["sets"][0]["weight_kg"] == 60.0
    assert exposicao["sets"][0]["effective_sets_stimulus"] == 1.0
    assert corpo["sets_truncated"] is False


def test_drilldown_declara_o_corte_quando_trunca(seed, raw, db_backend):
    for dias in (5, 4, 3):
        _sessao(raw, user_id=seed["dono"], dias_atras=dias, mapping_id=SUPINO, series=TRABALHO)

    corpo = _drilldown(seed["dono"], seed["Peitoral"], window="28d", sets_limit=2).json()

    assert corpo["sets_limit"] == 2
    assert corpo["sets_truncated"] is True
    assert any("mais antigas" in limitacao for limitacao in corpo["limitations"])
    total = sum(len(e["sets"]) for s in corpo["sessions"] for e in s["exposures"])
    assert total == 2
    # O corte fica nas sessoes mais ANTIGAS: a primeira sessao listada e a mais
    # recente (dias_atras=3).
    assert corpo["sessions"][0]["local_date"] == str(HOJE - _dt.timedelta(days=3))


def test_drilldown_de_grupo_inexistente_responde_404(seed, raw, db_backend):
    assert _drilldown(seed["dono"], 999_999, window="28d").status_code == 404


def test_drilldown_de_grupo_sem_treino_responde_200_vazio(seed, raw, db_backend):
    """"Nao treinei este grupo" e uma resposta, nao um erro."""
    resposta = _drilldown(seed["dono"], seed["Costas"], window="28d")
    assert resposta.status_code == 200, db_backend
    corpo = resposta.json()
    assert corpo["sessions"] == []
    assert corpo["item"]["realized"]["effective_sets_stimulus"]["value"] == 0.0


# ---------------------------------------------------------------------------
# IDOR
# ---------------------------------------------------------------------------


def test_idor_resumo_nao_vaza_volume_do_outro_usuario(seed, raw, db_backend):
    _sessao(raw, user_id=seed["outro"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    do_outro = _muscles(seed["outro"], window="28d").json()
    assert _item(do_outro, seed["Peitoral"])["realized"]["physical_sets"]["value"] == 2

    do_dono = _muscles(seed["dono"], window="28d").json()
    # O dono nao treinou nada: nenhum grupo com volume (a lista fica vazia
    # porque nao ha nem meta cadastrada para gerar linha).
    assert all(
        item["realized"]["physical_sets"]["value"] == 0 for item in do_dono["items"]
    ), db_backend
    assert do_dono["unattributed_sets"] == 0


def test_idor_drilldown_nao_vaza_serie_do_outro_usuario(seed, raw, db_backend):
    """O id de grupo muscular e de catalogo COMPARTILHADO - pedir o mesmo id
    nao pode trazer a sessao do outro."""
    _sessao(raw, user_id=seed["outro"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    do_outro = _drilldown(seed["outro"], seed["Peitoral"], window="28d").json()
    assert len(do_outro["sessions"]) == 1, db_backend

    do_dono = _drilldown(seed["dono"], seed["Peitoral"], window="28d")
    assert do_dono.status_code == 200, db_backend
    assert do_dono.json()["sessions"] == [], db_backend


def test_window_cycle_recusada_com_400(seed, db_backend):
    assert _muscles(seed["dono"], window="cycle").status_code == 400
    assert _drilldown(seed["dono"], seed["Peitoral"], window="cycle").status_code == 400
