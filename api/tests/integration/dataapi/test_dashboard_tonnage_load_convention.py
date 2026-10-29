"""M58: a tonelagem do DASHBOARD respeita `equipment_exercises.load_convention`
contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API
de producao) - a mesma regra do relatorio Markdown (PR #298).

O que este arquivo prova:

- comparativo por sessao / por equipamento / mapa de calor
  (`/analytics/dashboard/muscles?include_comparatives=true`), progressao por
  grupo (`/analytics/dashboard-progression-by-muscle-group`): par `assisted`
  e par `bodyweight` FORA da
  tonelagem; par `total` e par sem convencao (NULL) DENTRO;
- as series que sairam sao declaradas (`tonnage_excluded_sets`);
- series fisicas/estimulo nao mudam (so a tonelagem);
- M63: o `volume_kg` GLOBAL (`/analytics/summary`, `/analytics/training-trend`,
  serie semanal movel do contexto da IA em `/analytics/training-evolution` e
  o resumo semanal do `TrainingLoadService`) segue a MESMA regra e declara
  `tonnage_excluded_sets`; series/reps/RPE continuam contando tudo;
- IDOR: a carga do outro usuario nao entra em nenhum dos numeros;
- o payload e EQUIVALENTE nos dois backends. A comparacao usa
  `payload_diff.diferencas` com tolerancia de 0,01 (um degrau de
  arredondamento): nao queremos um terceiro flake 73.85 x 73.84.

Isolamento: `truncar` (lista centralizada de `tests/support/isolamento_v3.py`)
no inicio E no teardown do seed.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import analytics, analytics_dashboard
from app.intelligence import muscle_group_performance, muscle_group_progression  # noqa: F401
from app.intelligence import training_load_service  # noqa: F401
from app.intelligence.training_load_service import TrainingLoadService
from app.intelligence.dashboard import muscle_summary_service  # noqa: F401
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.payload_diff import diferencas
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()

SUPINO = 4201  # Peitoral, maquina com par `total`
REMADA = 4202  # Costas, maquina com par SEM convencao (NULL = total)
GRAVITON = 4203  # Costas, par `assisted`
PARALELA = 4204  # Peitoral, par `bodyweight`

# Um degrau de arredondamento de 2 casas.
TOLERANCIA = 0.011


@pytest.fixture
def seed(raw):
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome in ("dono", "outro"):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (nome,))
            ids[nome] = cur.lastrowid
        for grupo in ("Peitoral", "Costas"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid

        exercicios = (
            (SUPINO, "Supino reto", "Peitoral", "Banco reto", "total"),
            (REMADA, "Remada sentada", "Costas", "Remada baixa", None),
            (GRAVITON, "Barra fixa assistida", "Costas", "Graviton", "assisted"),
            (PARALELA, "Paralela", "Peitoral", "Paralelas", "bodyweight"),
        )
        cur.execute("INSERT INTO gyms (name) VALUES ('Academia')")
        gym_id = cur.lastrowid
        for exercise_id, nome, grupo, equipamento, convencao in exercicios:
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, nome, nome),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, nome))
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
                "VALUES (%s, %s, 'PRIMARY', 1.0, 1.0)",
                (exercise_id, ids[grupo]),
            )
            cur.execute("INSERT INTO equipment (name) VALUES (%s)", (equipamento,))
            equipment_id = cur.lastrowid
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
                (gym_id, exercise_id, equipment_id),
            )
            ids[f"ge_{exercise_id}"] = cur.lastrowid
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id, is_active, load_convention) "
                "VALUES (%s, %s, 1, %s)",
                (equipment_id, exercise_id, convencao),
            )
    raw.commit()
    yield ids
    truncar(raw)


def _sessao(raw, *, user_id, dias_atras, mapping_id, gym_equipment_id, series, dia=None):
    """`series` = `(set_number, set_type, reps, peso, rpe)`. `dia` (opcional)
    fixa a data em vez de `HOJE - dias_atras`.

    `activity_type` de musculacao: as agregacoes globais do M63
    (`compute_training_trend`/`compute_rolling_weekly_trend`) so leem
    atividades de forca (`LIKE '%muscul%'`); as do M58 nao filtram tipo."""
    dia = dia or HOJE - _dt.timedelta(days=dias_atras)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
            "VALUES (%s, %s, %s, 'Treino', 'Musculacao', 'done')",
            (user_id, dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, 'Exercicio', %s, %s, %s)",
            (activity_id, mapping_id, gym_equipment_id, len(series)),
        )
        exercise_row_id = cur.lastrowid
        for numero, tipo, reps, peso, rpe in series:
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (exercise_row_id, numero, tipo, reps, peso, rpe),
            )
    raw.commit()
    return activity_id


def _historico(raw, seed):
    dono = seed["dono"]
    # Peitoral: supino `total` 2 x 10 x 60 = 1200 kg (dentro).
    _sessao(raw, user_id=dono, dias_atras=4, mapping_id=SUPINO, gym_equipment_id=seed[f"ge_{SUPINO}"],
            series=[(1, "working", 10, "60.00", "8.00"), (2, "working", 10, "60.00", "8.00")])
    # Costas: remada par NULL 2 x 10 x 50.25 = 1005 kg (dentro: NULL = total).
    _sessao(raw, user_id=dono, dias_atras=3, mapping_id=REMADA, gym_equipment_id=seed[f"ge_{REMADA}"],
            series=[(1, "working", 10, "50.25", "8.00"), (2, "working", 10, "50.25", "8.00")])
    # Peitoral: paralela `bodyweight` com 10 kg registrados (fora).
    _sessao(raw, user_id=dono, dias_atras=2, mapping_id=PARALELA, gym_equipment_id=seed[f"ge_{PARALELA}"],
            series=[(1, "working", 12, "10.00", "8.00"), (2, "working", 12, "10.00", "9.00")])
    # Costas: graviton `assisted`, 30 kg de ASSISTENCIA (fora).
    _sessao(raw, user_id=dono, dias_atras=1, mapping_id=GRAVITON, gym_equipment_id=seed[f"ge_{GRAVITON}"],
            series=[(1, "working", 8, "30.00", "8.00"), (2, "working", 8, "30.00", "8.00"),
                    (3, "working", 7, "30.00", "9.00")])
    # Outro usuario: 200 kg no supino que nunca podem aparecer para o dono.
    _sessao(raw, user_id=seed["outro"], dias_atras=1, mapping_id=SUPINO, gym_equipment_id=seed[f"ge_{SUPINO}"],
            series=[(1, "working", 10, "200.00", "9.00")])


def _client(router, user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _comparativos(user_id: int) -> dict:
    resposta = _client(analytics_dashboard.router, user_id).get(
        "/analytics/dashboard/muscles", params={"window": "28d", "include_comparatives": "true"}
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["comparatives"]


def _progressao(user_id: int) -> dict:
    resposta = _client(analytics.router, user_id).get(
        "/analytics/dashboard-progression-by-muscle-group", params={"days": 7}
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _pontos(bloco: dict, grupo_id: int) -> list[dict]:
    return next(g for g in bloco["by_session"] if g["muscle_group_id"] == grupo_id)["points"]


# ---------------------------------------------------------------------------
# Equivalencia de payload entre os backends
# ---------------------------------------------------------------------------


def test_payload_equivalente_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _historico(raw, seed)
    congelar_relogio(monkeypatch)

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resultados[backend] = {
            "comparatives": _comparativos(seed["dono"]),
            "progression": _progressao(seed["dono"]),
        }

    problemas = diferencas(resultados["pymysql"], resultados["data-api"], tolerancia=TOLERANCIA)
    assert problemas == [], problemas


# ---------------------------------------------------------------------------
# Regra, nos dois backends
# ---------------------------------------------------------------------------


def test_comparativos_excluem_assistido_e_peso_corporal(seed, raw, db_backend):
    _historico(raw, seed)
    bloco = _comparativos(seed["dono"])

    peitoral = _pontos(bloco, seed["Peitoral"])
    # T-0 = paralela (peso corporal): sem tonelagem, 2 series declaradas;
    # as series fisicas continuam contando.
    assert (peitoral[0]["tonnage_kg"], peitoral[0]["tonnage_excluded_sets"]) == (None, 2), db_backend
    assert peitoral[0]["physical_sets"] == 2
    # T-1 = supino `total`: 1200 kg, nada excluido.
    assert peitoral[1]["tonnage_kg"] == pytest.approx(1200.0)
    assert peitoral[1]["tonnage_excluded_sets"] == 0

    costas = _pontos(bloco, seed["Costas"])
    # T-0 = graviton assistido: fora; T-1 = remada sem convencao (NULL = total).
    assert (costas[0]["tonnage_kg"], costas[0]["tonnage_excluded_sets"]) == (None, 3), db_backend
    assert costas[0]["physical_sets"] == 3
    assert costas[1]["tonnage_kg"] == pytest.approx(1005.0, abs=TOLERANCIA)
    assert costas[1]["tonnage_excluded_sets"] == 0

    equipamentos = {
        e["equipment_name"]: e
        for g in bloco["by_equipment"]
        for e in g["equipments"]
    }
    assert equipamentos["Graviton"]["points"][0]["tonnage_kg"] is None
    assert equipamentos["Paralelas"]["points"][0]["tonnage_kg"] is None
    assert equipamentos["Banco reto"]["points"][0]["tonnage_kg"] == pytest.approx(1200.0)

    celulas = [c for sessao in bloco["heatmap"] for c in sessao["cells"]]
    assert sum(c["tonnage_excluded_sets"] for c in celulas) == 5


def test_progressao_por_grupo_exclui_assistido_e_peso_corporal(seed, raw, db_backend):
    _historico(raw, seed)
    itens = {i["muscle_group_id"]: i for i in _progressao(seed["dono"])["items"]}

    peitoral = itens[seed["Peitoral"]]
    assert peitoral["current_tonnage_kg"] == pytest.approx(1200.0), db_backend
    assert peitoral["current_sessions"] == 1  # a sessao de paralela nao tem tonelagem
    assert peitoral["current_sets"] == 4  # volume continua contando tudo
    assert peitoral["current_tonnage_excluded_sets"] == 2

    costas = itens[seed["Costas"]]
    assert costas["current_tonnage_kg"] == pytest.approx(1005.0, abs=0.06)  # 1 casa no payload
    assert costas["current_sessions"] == 1
    assert costas["current_sets"] == 5
    assert costas["current_tonnage_excluded_sets"] == 3


# ---------------------------------------------------------------------------
# Isolamento por usuario
# ---------------------------------------------------------------------------


def test_idor_tonelagem_do_outro_usuario_nao_entra(seed, raw, db_backend):
    _historico(raw, seed)

    bloco_outro = _comparativos(seed["outro"])
    assert _pontos(bloco_outro, seed["Peitoral"])[0]["tonnage_kg"] == pytest.approx(2000.0), db_backend
    assert all(g["muscle_group_id"] != seed["Costas"] for g in bloco_outro["by_session"])

    bloco_dono = _comparativos(seed["dono"])
    tonelagens_dono = [p["tonnage_kg"] for g in bloco_dono["by_session"] for p in g["points"]]
    assert 2000.0 not in tonelagens_dono

    itens_dono = {i["muscle_group_id"]: i for i in _progressao(seed["dono"])["items"]}
    assert itens_dono[seed["Peitoral"]]["current_tonnage_kg"] == pytest.approx(1200.0)
    itens_outro = {i["muscle_group_id"]: i for i in _progressao(seed["outro"])["items"]}
    assert set(itens_outro) == {seed["Peitoral"]}
    assert itens_outro[seed["Peitoral"]]["current_tonnage_excluded_sets"] == 0


# ---------------------------------------------------------------------------
# M63 - volume_kg GLOBAL (card, series semanais, contexto da IA)
# ---------------------------------------------------------------------------

# Peitoral 1200 (supino `total`) + Costas 1005 (remada, par NULL = total).
VOLUME_CONTADO = 1200.0 + 1005.0
# Paralela `bodyweight` (2) + graviton `assisted` (3).
SERIES_EXCLUIDAS = 5
# Todas as series de trabalho do dono: 2 + 2 + 2 + 3.
SERIES_DO_DONO = 9


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="regular", source="session")


def _hoje_do_banco(raw) -> _dt.date:
    """"Hoje" do USUARIO (fuso default), a ancora das agregacoes globais (o
    resumo semanal comeca na segunda-feira dele e a serie movel conta D-0 a
    D-6 a partir dele). NAO `CURDATE()`: o MySQL esta em UTC e, entre 00:00 e
    03:00 UTC, esse dia e o seguinte ao do usuario - o historico semeado
    ficava fora do bucket `D-0 a D-6`. `raw` fica na assinatura por
    compatibilidade com os chamadores."""
    return hoje_do_usuario()


def _historico_hoje(raw, seed):
    """O mesmo historico de `_historico`, todo no "hoje" do usuario - para
    o resumo da semana corrente (`get_weekly_summary`) enxergar tudo."""
    dia = _hoje_do_banco(raw)
    dono = seed["dono"]
    sessoes = (
        (dono, SUPINO, [(1, "working", 10, "60.00", "8.00"), (2, "working", 10, "60.00", "8.00")]),
        (dono, REMADA, [(1, "working", 10, "50.25", "8.00"), (2, "working", 10, "50.25", "8.00")]),
        (dono, PARALELA, [(1, "working", 12, "10.00", "8.00"), (2, "working", 12, "10.00", "9.00")]),
        (dono, GRAVITON, [(1, "working", 8, "30.00", "8.00"), (2, "working", 8, "30.00", "8.00"),
                          (3, "working", 7, "30.00", "9.00")]),
        (seed["outro"], SUPINO, [(1, "working", 10, "200.00", "9.00")]),
    )
    for user_id, mapping_id, series in sessoes:
        _sessao(raw, user_id=user_id, dias_atras=0, dia=dia, mapping_id=mapping_id,
                gym_equipment_id=seed[f"ge_{mapping_id}"], series=series)


def _volumes_globais(user_id: int) -> dict:
    return {
        "summary": analytics.analytics_summary(days=30, identity=_identity(user_id))["totals"],
        "training_trend": analytics.compute_training_trend("month", 30, user_id=user_id),
        "rolling": analytics.compute_rolling_weekly_trend(14, user_id=user_id),
        "weekly_summary": {
            k: v
            for k, v in TrainingLoadService().get_weekly_summary(user_id=user_id).items()
            if k in ("total_sets", "total_volume_kg", "tonnage_excluded_sets", "completed_workouts")
        },
    }


def _somar(linhas: list[dict], campo: str) -> float:
    return sum(float(linha.get(campo) or 0) for linha in linhas)


def test_m63_volume_global_exclui_assistido_e_peso_corporal(seed, raw, db_backend):
    _historico_hoje(raw, seed)
    v = _volumes_globais(seed["dono"])

    # Card `/analytics/summary`.
    assert float(v["summary"]["volume_kg"]) == pytest.approx(VOLUME_CONTADO, abs=TOLERANCIA), db_backend
    assert int(v["summary"]["tonnage_excluded_sets"]) == SERIES_EXCLUIDAS
    assert int(v["summary"]["sets_count"]) == SERIES_DO_DONO  # series continuam contando tudo

    # `/analytics/training-trend` (mes de calendario).
    assert _somar(v["training_trend"], "volume_kg") == pytest.approx(VOLUME_CONTADO, abs=TOLERANCIA)
    assert _somar(v["training_trend"], "tonnage_excluded_sets") == SERIES_EXCLUIDAS
    assert _somar(v["training_trend"], "sets_count") == SERIES_DO_DONO

    # Serie semanal movel (contexto da IA de `/analytics/training-evolution`).
    (semana,) = [b for b in v["rolling"] if b["period"] == "D-0 a D-6"]
    assert float(semana["volume_kg"]) == pytest.approx(VOLUME_CONTADO, abs=TOLERANCIA)
    assert int(semana["tonnage_excluded_sets"]) == SERIES_EXCLUIDAS
    assert int(semana["sets_count"]) == SERIES_DO_DONO

    # Resumo da semana corrente (`weekly_summary`/`weekly_training_load` da IA).
    resumo = v["weekly_summary"]
    assert resumo["total_volume_kg"] == pytest.approx(VOLUME_CONTADO, abs=TOLERANCIA)
    assert resumo["tonnage_excluded_sets"] == SERIES_EXCLUIDAS
    assert resumo["total_sets"] == SERIES_DO_DONO
    assert resumo["completed_workouts"] == 4


def test_m63_volume_global_idor_e_par_total_sem_exclusao(seed, raw, db_backend):
    """O outro usuario so tem supino `total`: 10 x 200 = 2000 kg, nada
    excluido. O 2205 do teste acima ja prova que os 2000 kg dele nao entram
    no dono; aqui, que nada do dono entra nele."""
    _historico_hoje(raw, seed)
    v = _volumes_globais(seed["outro"])
    assert float(v["summary"]["volume_kg"]) == pytest.approx(2000.0), db_backend
    assert int(v["summary"]["tonnage_excluded_sets"]) == 0
    assert int(v["summary"]["sets_count"]) == 1
    assert v["weekly_summary"]["total_volume_kg"] == pytest.approx(2000.0)
    assert v["weekly_summary"]["tonnage_excluded_sets"] == 0


def test_m63_volume_global_equivalente_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _historico_hoje(raw, seed)
    congelar_relogio(monkeypatch)

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resultados[backend] = _volumes_globais(seed["dono"])

    problemas = diferencas(resultados["pymysql"], resultados["data-api"], tolerancia=TOLERANCIA)
    assert problemas == [], problemas
