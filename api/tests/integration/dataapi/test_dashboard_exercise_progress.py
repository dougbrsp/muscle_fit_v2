"""`GET /analytics/dashboard/exercises/{exercise_id}/progress` contra MySQL
real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- o endpoint responde o MESMO payload nos dois backends (nenhuma regra nova
  depende de tipo devolvido pelo driver);
- IDOR: o usuario A nao ve exposicao do usuario B, nem mesmo pedindo o id de
  exercicio que so B treinou - responde 200 com `INSUFFICIENT_DATA` e lista
  vazia, nunca os dados do outro;
- so series `working` entram (aquecimento fica de fora);
- a `load_convention` cadastrada em `equipment_exercises` chega na chave de
  comparabilidade e muda a direcao da leitura (`assisted`);
- o teto de 30 exposicoes segura a resposta bem abaixo do limite de 1 MB da
  Data API, mesmo com historico longo.

Os dados sao semeados por conexao NEUTRA (PyMySQL direto), entao os dois
backends leem exatamente as mesmas linhas.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import analytics_dashboard
from app.intelligence.dashboard import exposure_service  # noqa: F401 - precisa estar importado para a troca
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL (tests/support/relogio_usuario.py)


SUPINO = 1001
GRAVITON = 1002
LEGADO_SUPINO = 9001  # id antigo que a consolidacao absorveu em SUPINO


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Catalogo minimo + historico de dois usuarios."""
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome] = cur.lastrowid

        cur.execute("INSERT INTO gyms (name) VALUES ('Academia')")
        ids["gym"] = cur.lastrowid

        for nome in ("Banco reto", "Graviton"):
            cur.execute("INSERT INTO equipment (name) VALUES (%s)", (nome,))
            ids[nome] = cur.lastrowid

        for exercise_id, nome in ((SUPINO, "Supino reto"), (GRAVITON, "Barra fixa assistida")):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, nome, f"{nome} (AMAZFIT)"),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, nome))
            # o nome AMAZFIT exibido vem da tabela canonica de mapeamentos
            # externos (nao de `exercise_zepp_mappings`, que e legada)
            cur.execute(
                "INSERT INTO exercise_external_mappings (exercise_id, provider, external_name, is_active) "
                "VALUES (%s, 'AMAZFIT', %s, 1)",
                (exercise_id, f"{nome} (AMAZFIT)"),
            )

        # id legado absorvido pelo canonico (caso "exercicio renomeado/fundido")
        cur.execute(
            "INSERT INTO exercise_legacy_id_map (old_exercise_id, canonical_exercise_id) VALUES (%s, %s)",
            (LEGADO_SUPINO, SUPINO),
        )
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
            (LEGADO_SUPINO, "Supino reto (legado)", "Supino reto (legado)"),
        )
        # A linha antiga CONTINUA no catalogo canonico: em producao
        # `exercises.mapping_id` tem FK para `exercise_catalog(id)` (028), e a
        # consolidacao registra old -> canonical sem apagar o id antigo. Nome
        # diferente do canonico de proposito, para que um fallback por nome nao
        # mascare o match por `mapping_id`.
        cur.execute(
            "INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)",
            (LEGADO_SUPINO, "Supino reto (legado)"),
        )

        # `gym_equipment` e o cache por academia: exercises.equipment_id aponta
        # para ELE, e o equipamento canonico sai de gym_equipment.equipment_id.
        for exercise_id, equipamento in ((SUPINO, "Banco reto"), (GRAVITON, "Graviton")):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
                (ids["gym"], exercise_id, ids[equipamento]),
            )
            ids[f"ge_{equipamento}"] = cur.lastrowid
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id, is_active) VALUES (%s, %s, 1)",
                (ids[equipamento], exercise_id),
            )
        # o graviton e assistido: MENOS kg = progresso
        cur.execute(
            "UPDATE equipment_exercises SET load_convention = 'assisted' WHERE equipment_id = %s",
            (ids["Graviton"],),
        )
    return ids


def _sessao(raw, *, user_id, dias_atras, mapping_id, gym_equipment_id, series, sequence=1):
    """Uma sessao com um exercicio e suas series. `series` = lista de
    `(set_number, set_type, reps, weight, rpe)`."""
    dia = HOJE - _dt.timedelta(days=dias_atras)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, session_status) "
            "VALUES (%s, %s, %s, 'Treino', 'done')",
            (user_id, dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, %s, 'Exercicio', %s, %s, %s)",
            (activity_id, sequence, mapping_id, gym_equipment_id, len(series)),
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


def _progress(user_id: int, exercise_id: int, **params):
    resposta = _client(user_id).get(f"/analytics/dashboard/exercises/{exercise_id}/progress", params=params)
    return resposta


# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=14,
        mapping_id=SUPINO,
        gym_equipment_id=seed["ge_Banco reto"],
        series=[(1, "working", 10, "60.00", "8.00"), (2, "working", 10, "60.00", "8.50")],
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=7,
        mapping_id=SUPINO,
        gym_equipment_id=seed["ge_Banco reto"],
        series=[(1, "working", 12, "60.00", "8.00"), (2, "working", 11, "60.00", "8.50")],
    )

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _progress(seed["dono"], SUPINO)
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]

    corpo = resultados["pymysql"]
    assert corpo["status"] == "PROGRESSING"  # mesma carga, mais reps
    assert len(corpo["exposures"]) == 2
    assert corpo["exposures"][0]["local_date"] < corpo["exposures"][1]["local_date"]
    assert corpo["exercise"]["name"] == "Supino reto"
    assert corpo["exercise"]["amazfit_name"] == "Supino reto (AMAZFIT)"
    assert corpo["metric_versions"]["e1rm"] == "epley-rir-v1"
    assert corpo["window"]["timezone"] == "America/Sao_Paulo"


def test_idor_usuario_a_nao_ve_exposicao_do_usuario_b(seed, raw, db_backend):
    """B treinou o GRAVITON; A nunca. Pedindo o id do exercicio de B, A recebe
    200 com historico VAZIO - nunca as series do outro."""
    _sessao(
        raw,
        user_id=seed["outro"],
        dias_atras=5,
        mapping_id=GRAVITON,
        gym_equipment_id=seed["ge_Graviton"],
        series=[(1, "working", 8, "30.00", "9.00")],
    )

    do_outro = _progress(seed["outro"], GRAVITON)
    assert do_outro.status_code == 200, db_backend
    assert len(do_outro.json()["exposures"]) == 1, db_backend

    do_dono = _progress(seed["dono"], GRAVITON)
    assert do_dono.status_code == 200, db_backend
    corpo = do_dono.json()
    assert corpo["exposures"] == [], db_backend
    assert corpo["status"] == "INSUFFICIENT_DATA", db_backend
    assert corpo["comparability_key"] is None, db_backend
    # nenhum numero do outro usuario vazou para o payload
    assert "30.0" not in json.dumps(corpo), db_backend


def test_exercicio_inexistente_e_404_mas_sem_historico_e_200(seed, db_backend):
    ausente = _progress(seed["dono"], 999999)
    assert ausente.status_code == 404, db_backend

    sem_historico = _progress(seed["dono"], SUPINO)
    assert sem_historico.status_code == 200, db_backend
    assert sem_historico.json()["status"] == "INSUFFICIENT_DATA", db_backend


def test_apenas_series_working_entram(seed, raw, db_backend):
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=3,
        mapping_id=SUPINO,
        gym_equipment_id=seed["ge_Banco reto"],
        series=[
            (1, "warmup", 15, "20.00", "5.00"),
            (2, "feeler", 8, "40.00", "6.00"),
            (3, "working", 10, "60.00", "8.00"),
        ],
    )
    corpo = _progress(seed["dono"], SUPINO).json()
    exposicao = corpo["exposures"][0]
    assert [item["set_number"] for item in exposicao["sets"]] == [3], db_backend
    assert exposicao["total_reps"] == 10, db_backend
    assert exposicao["best_load_kg"] == 60.0, db_backend


def test_id_legado_cai_no_mesmo_historico_canonico(seed, raw, db_backend):
    """Uma sessao gravada com o id ANTIGO e outra com o canonico sao o mesmo
    exercicio (via `exercise_legacy_id_map`)."""
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=20,
        mapping_id=LEGADO_SUPINO,
        gym_equipment_id=seed["ge_Banco reto"],
        series=[(1, "working", 10, "60.00", "8.00")],
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=10,
        mapping_id=SUPINO,
        gym_equipment_id=seed["ge_Banco reto"],
        series=[(1, "working", 12, "60.00", "8.00")],
    )
    corpo = _progress(seed["dono"], SUPINO).json()
    assert len(corpo["exposures"]) == 2, db_backend
    assert corpo["status"] == "PROGRESSING", db_backend
    chaves = {item["comparability_key"]["canonical_exercise_id"] for item in corpo["exposures"]}
    assert chaves == {SUPINO}, db_backend


def test_convencao_cadastrada_chega_na_chave_e_inverte_a_direcao(seed, raw, db_backend):
    """Graviton = `assisted`: cair de 30 para 20 kg de assistencia com as
    mesmas reps na faixa prescrita e PROGRESSO, nao regressao."""
    for dias, peso in ((14, "30.00"), (7, "20.00")):
        _sessao(
            raw,
            user_id=seed["dono"],
            dias_atras=dias,
            mapping_id=GRAVITON,
            gym_equipment_id=seed["ge_Graviton"],
            series=[(1, "working", 8, peso, "8.00")],
        )

    corpo = _progress(seed["dono"], GRAVITON).json()
    assert corpo["comparability_key"]["load_convention"] == "assisted", db_backend
    assert corpo["exposures"][-1]["e1rm"] is None, db_backend  # assistido nao estima 1RM
    assert corpo["exposures"][-1]["best_load_kg"] == 20.0, db_backend
    assert corpo["status"] != "REGRESSING", db_backend
    # o recorde do assistido e a MENOR assistencia
    assert corpo["records"]["best_load_kg"]["value"] == 20.0, db_backend


def test_historico_longo_respeita_o_cap_e_fica_abaixo_de_1_mb(seed, raw, db_backend):
    """45 sessoes de 5 series: sem o cap, a resposta cresceria sem limite e a
    Data API recusaria com UnsupportedResultException (teto de 1 MB)."""
    for dia in range(45):
        _sessao(
            raw,
            user_id=seed["dono"],
            dias_atras=dia + 1,
            mapping_id=SUPINO,
            gym_equipment_id=seed["ge_Banco reto"],
            series=[(n, "working", 10, "60.00", "8.00") for n in range(1, 6)],
        )

    resposta = _progress(seed["dono"], SUPINO)
    assert resposta.status_code == 200, (db_backend, resposta.text)
    corpo = resposta.json()
    assert len(corpo["exposures"]) == 30, db_backend
    assert len(resposta.content) < 1_000_000, db_backend
    # o corte e pelas MAIS RECENTES
    assert corpo["exposures"][-1]["local_date"] == (HOJE - _dt.timedelta(days=1)).isoformat(), db_backend


def test_janela_invalida_responde_400_e_nao_500(seed, db_backend):
    assert _progress(seed["dono"], SUPINO, window="ontem").status_code == 400, db_backend
    assert _progress(seed["dono"], SUPINO, window="custom").status_code == 400, db_backend
    assert _progress(seed["dono"], SUPINO, window="cycle").status_code == 400, db_backend
    ok = _progress(
        seed["dono"],
        SUPINO,
        window="custom",
        date_from=(HOJE - _dt.timedelta(days=30)).isoformat(),
        date_to=HOJE.isoformat(),
    )
    assert ok.status_code == 200, (db_backend, ok.text)
    assert ok.json()["window"]["days"] == 31, db_backend
