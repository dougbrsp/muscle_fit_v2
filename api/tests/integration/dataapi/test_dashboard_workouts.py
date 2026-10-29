"""`GET /analytics/dashboard/workouts`, `/workouts/{activity_id}` e
`/adherence` (F1.1) contra MySQL real, nos DOIS backends (PyMySQL de dev e
shim da RDS Data API de producao).

O que este arquivo prova:

- o payload e IDENTICO nos dois backends (o `Decimal` do PyMySQL vs. a string
  do shim ja quebrou leituras parecidas antes);
- **IDOR**: o usuario A nao ve sessao do B na lista e recebe 404 - o MESMO
  404 de sessao inexistente - ao pedir o detalhe dela pelo id;
- as cinco derivacoes do §5 ponta a ponta, sobre o vinculo real
  (`exercises.workout_plan_exercise_id`), nao sobre nome de exercicio;
- a prescricao congelada (`prescription_json`) tem prioridade sobre as
  colunas da ficha legada, e a fonte usada vai no payload;
- aderencia SEM Modelo atribuido responde 200 com `rate: null` e
  `data_quality=LOW` - nenhum denominador inventado;
- `window=cycle` (destravada por esta F1.1) resolve o ciclo pelo Modelo e
  responde 400 com mensagem clara quando nao ha Modelo;
- paginacao e teto de 1 MB da Data API.
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
from app.intelligence.dashboard import adherence_service  # noqa: F401 - precisa estar importado
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import truncar
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.template_versions import versao_v1

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL (tests/support/relogio_usuario.py)


SUPINO = 3001
REMADA = 3002
CRUCIFIXO = 3003


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Dois usuarios, um grupo, tres exercicios canonicos."""
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel)
            )
            ids[nome] = cur.lastrowid

        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peitoral')")
        ids["Peitoral"] = cur.lastrowid

        for exercise_id, nome in ((SUPINO, "Supino reto"), (REMADA, "Remada"), (CRUCIFIXO, "Crucifixo")):
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
                (CRUCIFIXO, ids["Peitoral"], "PRIMARY", 1.0, 1.0),
                (REMADA, ids["Peitoral"], "SECONDARY", 0.5, 0.4),
            ],
        )
    return ids


# ---------------------------------------------------------------------------
# Semeadura
# ---------------------------------------------------------------------------


def _template(raw, *, user_id, nomes=("Upper 1", "Lower 1", "Upper 2", "Lower 2")):
    """Modelo com N slots. Os nomes sao deliberadamente "Upper/Lower": nenhuma
    regra pode depender de letra A/B/C."""
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, template_type, is_active, created_by_user_id) "
            "VALUES ('Modelo', 'abc', 1, %s)",
            (user_id,),
        )
        template_id = cur.lastrowid
        for sequence, nome in enumerate(nomes, start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
                "VALUES (%s, %s, %s, %s, 'normal')",
                (versao_v1(cur, template_id), template_id, sequence, nome),
            )
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (template_id, user_id, user_id),
        )
    return template_id


def _plano(raw, *, user_id, itens, template_id=None, slot_sequence=None, nome="Ficha A"):
    """Ficha salva. `itens` = lista de dicts com `mapping_id`, `sets_count`,
    `prescription_json` opcional."""
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, template_id, "
            "template_slot_sequence) VALUES (%s, %s, %s, 'abc', 'planned', %s, %s)",
            (user_id, HOJE, nome, template_id, slot_sequence),
        )
        plan_id = cur.lastrowid
        plan_exercise_ids = []
        for sequence, item in enumerate(itens, start=1):
            cur.execute(
                "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, mapping_id, "
                "sets_count, reps_target, load_target, rpe_target, rest_seconds, prescription_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    plan_id,
                    sequence,
                    item.get("name", "Exercicio"),
                    item["mapping_id"],
                    item.get("sets_count"),
                    item.get("reps_target", "8-10"),
                    item.get("load_target", "60"),
                    item.get("rpe_target", "8"),
                    90,
                    json.dumps(item["prescription"]) if item.get("prescription") else None,
                ),
            )
            plan_exercise_ids.append(cur.lastrowid)
    return plan_id, plan_exercise_ids


def _sessao(
    raw,
    *,
    user_id,
    dias_atras,
    exercicios,
    plan_id=None,
    session_status="done",
    nome="Treino",
):
    """Sessao com N exercicios. `exercicios` = lista de dicts com
    `mapping_id`, `series` (`(numero, tipo, reps, peso, rpe)`) e o
    `plan_exercise_id` do vinculo (ou `None` para exercicio extra)."""
    dia = HOJE - _dt.timedelta(days=dias_atras)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, session_status, "
            "workout_plan_id, duration_seconds) VALUES (%s, %s, %s, %s, %s, %s, 3600)",
            (user_id, dia, _dt.datetime.combine(dia, _dt.time(19, 0)), nome, session_status, plan_id),
        )
        activity_id = cur.lastrowid
        for sequence, exercicio in enumerate(exercicios, start=1):
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count, "
                "workout_plan_exercise_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    activity_id,
                    sequence,
                    exercicio.get("name", "Exercicio"),
                    exercicio["mapping_id"],
                    len(exercicio["series"]),
                    exercicio.get("plan_exercise_id"),
                ),
            )
            exercise_row_id = cur.lastrowid
            for numero, tipo, reps, peso, rpe in exercicio["series"]:
                cur.execute(
                    "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (exercise_row_id, numero, tipo, reps, peso, rpe),
                )
    return activity_id


TRABALHO_3 = [
    (1, "working", 10, "60.00", "8.00"),
    (2, "working", 10, "60.00", "8.50"),
    (3, "working", 9, "60.00", "9.00"),
]
TRABALHO_1 = [(1, "working", 10, "60.00", "8.00")]


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _workouts(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/workouts", params=params)


def _detalhe(user_id: int, activity_id: int, **params):
    return _client(user_id).get(f"/analytics/dashboard/workouts/{activity_id}", params=params)


def _adherence(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/adherence", params=params)


# ---------------------------------------------------------------------------
# Paridade entre backends
# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    plan_id, itens = _plano(
        raw, user_id=seed["dono"], itens=[{"mapping_id": SUPINO, "sets_count": 3}]
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=2,
        plan_id=plan_id,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]}],
    )

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _workouts(seed["dono"], window="28d")
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]
    corpo = resultados["pymysql"]
    assert corpo["metric_versions"]["adherence"] == "adherence-v2"
    assert corpo["items"][0]["item_status_counts"]["completed"] == 1


# ---------------------------------------------------------------------------
# As cinco derivacoes, ponta a ponta
# ---------------------------------------------------------------------------


def test_status_dos_itens_saem_do_vinculo_e_das_series(seed, raw, db_backend):
    """Uma sessao com os cinco casos do §5 ao mesmo tempo."""
    plan_id, itens = _plano(
        raw,
        user_id=seed["dono"],
        itens=[
            {"mapping_id": SUPINO, "sets_count": 3, "name": "Supino reto"},
            {"mapping_id": SUPINO, "sets_count": 3, "name": "Supino interrompido"},
            {"mapping_id": SUPINO, "sets_count": 3, "name": "Supino pulado"},
            {"mapping_id": CRUCIFIXO, "sets_count": 3, "name": "Crucifixo prescrito"},
        ],
    )
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        plan_id=plan_id,
        exercicios=[
            {"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]},
            {"mapping_id": SUPINO, "series": TRABALHO_1, "plan_exercise_id": itens[1]},
            # itens[2] nao aparece na sessao -> SKIPPED
            {"mapping_id": REMADA, "series": TRABALHO_3, "plan_exercise_id": itens[3]},
            {"mapping_id": REMADA, "series": TRABALHO_1, "plan_exercise_id": None},
        ],
    )

    corpo = _detalhe(seed["dono"], activity_id).json()
    por_nome = {item["planned_name"]: item for item in corpo["items"] if item["planned_name"]}

    assert por_nome["Supino reto"]["status"] == "COMPLETED"
    assert por_nome["Supino interrompido"]["status"] == "INTERRUPTED"
    assert por_nome["Supino interrompido"]["missing_sets"] == 2
    assert por_nome["Supino pulado"]["status"] == "SKIPPED"
    assert por_nome["Supino pulado"]["performed"] is None

    substituido = por_nome["Crucifixo prescrito"]
    assert substituido["status"] == "SUBSTITUTED"
    assert substituido["substitution"]["planned_exercise_id"] == CRUCIFIXO
    assert substituido["substitution"]["performed_exercise_id"] == REMADA

    extras = [item for item in corpo["items"] if item["status"] == "EXTRA"]
    assert len(extras) == 1

    assert corpo["session"]["item_status_counts"] == {
        "completed": 1,
        "interrupted": 1,
        "skipped": 1,
        "substituted": 1,
        "extra": 1,
    }
    # As series de origem chegam no detalhe (o fim do drill-down da sessao).
    assert len(por_nome["Supino reto"]["performed"]["sets"]) == 3
    assert por_nome["Supino reto"]["performed"]["sets"][0]["is_working"] is True


def test_aquecimento_nao_conta_como_serie_de_trabalho(seed, raw, db_backend):
    plan_id, itens = _plano(
        raw, user_id=seed["dono"], itens=[{"mapping_id": SUPINO, "sets_count": 3}]
    )
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        plan_id=plan_id,
        exercicios=[
            {
                "mapping_id": SUPINO,
                "plan_exercise_id": itens[0],
                "series": [
                    (1, "warmup", 12, "20.00", None),
                    (2, "feeler", 8, "40.00", None),
                ],
            }
        ],
    )

    corpo = _detalhe(seed["dono"], activity_id).json()
    assert corpo["items"][0]["status"] == "SKIPPED"
    assert corpo["session"]["physical_sets"]["value"] == 0


def test_prescricao_congelada_tem_prioridade_sobre_as_colunas(seed, raw, db_backend):
    plan_id, itens = _plano(
        raw,
        user_id=seed["dono"],
        itens=[
            {
                "mapping_id": SUPINO,
                "sets_count": 3,
                "prescription": {
                    "schema_version": 1,
                    "engine_version": "progression-v2.0.0",
                    "sets_count": 4,
                    "reps_target": "6-8",
                    "load_target": "70",
                    "rpe_target": "9",
                },
            }
        ],
    )
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        plan_id=plan_id,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]}],
    )

    item = _detalhe(seed["dono"], activity_id).json()["items"][0]
    assert item["prescribed"]["source"] == "frozen_snapshot"
    assert item["prescribed"]["sets_count"] == 4
    assert item["prescribed"]["engine_version"] == "progression-v2.0.0"
    # 3 series de trabalho contra 4 prescritas pelo SNAPSHOT (nao as 3 da
    # coluna): o item foi interrompido.
    assert item["status"] == "INTERRUPTED"
    assert item["missing_sets"] == 1


def test_ficha_legada_usa_as_colunas_e_declara(seed, raw, db_backend):
    plan_id, itens = _plano(
        raw, user_id=seed["dono"], itens=[{"mapping_id": SUPINO, "sets_count": 3}]
    )
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        plan_id=plan_id,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]}],
    )

    item = _detalhe(seed["dono"], activity_id).json()["items"][0]
    assert item["prescribed"]["source"] == "plan_columns"
    assert item["status"] == "COMPLETED"


def test_sessao_sem_ficha_vira_tudo_extra_e_declara_a_limitacao(seed, raw, db_backend):
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3}],
    )

    corpo = _detalhe(seed["dono"], activity_id).json()
    assert corpo["session"]["plan_link"] is None
    assert corpo["session"]["item_status_counts"]["extra"] == 1
    assert any("sem vinculo com ficha" in texto for texto in corpo["session"]["limitations"])


def test_plan_link_traz_o_slot_do_modelo_e_nao_o_nome_da_sessao(seed, raw, db_backend):
    """O agrupamento da tela passa a ser por SLOT: duas sessoes com nomes
    escritos de formas diferentes continuam no mesmo slot."""
    template_id = _template(raw, user_id=seed["dono"])
    plan_id, itens = _plano(
        raw,
        user_id=seed["dono"],
        itens=[{"mapping_id": SUPINO, "sets_count": 3}],
        template_id=template_id,
        slot_sequence=1,
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=5,
        plan_id=plan_id,
        nome="Treino A",
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]}],
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        plan_id=plan_id,
        nome="treino a ",
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]}],
    )

    corpo = _workouts(seed["dono"], window="28d").json()
    assert {item["name"] for item in corpo["items"]} == {"Treino A", "treino a"}
    slots = {item["plan_link"]["template_slot_sequence"] for item in corpo["items"]}
    nomes = {item["plan_link"]["template_slot_name"] for item in corpo["items"]}
    assert slots == {1}
    assert nomes == {"Upper 1"}
    # Grupo pronto do dono (`session_group_of`): Modelo + posicao, nunca o nome.
    grupos = {item["group"]["key"] for item in corpo["items"]}
    assert grupos == {f"slot:{template_id}:1"}
    assert {item["group"]["template_name"] for item in corpo["items"]} != {None}


# ---------------------------------------------------------------------------
# Recordes
# ---------------------------------------------------------------------------


def test_recorde_compara_com_o_historico_e_progressao_com_a_anterior(seed, raw, db_backend):
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=10,
        exercicios=[{"mapping_id": SUPINO, "series": [(1, "working", 10, "60.00", "8.00")]}],
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=2,
        exercicios=[{"mapping_id": SUPINO, "series": [(1, "working", 10, "65.00", "8.00")]}],
    )

    corpo = _workouts(seed["dono"], window="28d").json()
    recente, antiga = corpo["items"][0], corpo["items"][1]
    assert recente["records"]["value"] == 1
    assert recente["progressions"]["value"] == 1
    assert antiga["records"]["value"] == 0


def test_mais_carga_com_menos_reps_e_recorde_mas_nao_progressao(seed, raw, db_backend):
    """Progressao segue `compare_exposures` (regra do drill-down): mais carga
    com menos reps, sem RPE para o e1RM decidir, e inconclusivo. O recorde de
    CARGA continua valendo (70 > 60 na mesma chave)."""
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=10,
        exercicios=[{"mapping_id": SUPINO, "series": [(1, "working", 10, "60.00", None)]}],
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=2,
        exercicios=[{"mapping_id": SUPINO, "series": [(1, "working", 6, "70.00", None)]}],
    )

    corpo = _workouts(seed["dono"], window="28d").json()
    recente = corpo["items"][0]
    assert recente["records"]["value"] == 1, db_backend
    assert recente["progressions"]["value"] == 0, db_backend


# ---------------------------------------------------------------------------
# IDOR
# ---------------------------------------------------------------------------


def test_sessao_de_outro_usuario_nao_aparece_e_devolve_404(seed, raw, db_backend):
    alheia = _sessao(
        raw,
        user_id=seed["outro"],
        dias_atras=1,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3}],
    )
    minha = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_1}],
    )

    corpo = _workouts(seed["dono"], window="28d").json()
    assert [item["activity_id"] for item in corpo["items"]] == [minha]
    assert corpo["total"] == 1

    # O MESMO 404 de sessao inexistente: nenhum id alheio e confirmado.
    assert _detalhe(seed["dono"], alheia).status_code == 404
    assert _detalhe(seed["dono"], 9_999_999).status_code == 404


def test_ficha_de_outro_usuario_nao_vaza_no_detalhe(seed, raw, db_backend):
    """A ficha e buscada com `wp.user_id = %s`: um `workout_plan_id` apontando
    para ficha alheia nao traz os itens dela."""
    plan_id, itens = _plano(
        raw, user_id=seed["outro"], itens=[{"mapping_id": SUPINO, "sets_count": 3}]
    )
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        plan_id=plan_id,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3}],
    )

    corpo = _detalhe(seed["dono"], activity_id).json()
    assert [item["status"] for item in corpo["items"]] == ["EXTRA"]


# ---------------------------------------------------------------------------
# Aderencia e `window=cycle`
# ---------------------------------------------------------------------------


def test_aderencia_sem_modelo_nao_inventa_denominador(seed, raw, db_backend):
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3}],
    )

    corpo = _adherence(seed["dono"], window="28d").json()
    assert corpo["has_template"] is False
    assert corpo["rate"]["value"] is None
    assert corpo["expected_sessions"]["value"] is None
    assert corpo["completed_sessions"]["value"] == 1
    assert corpo["data_quality"] == "LOW"
    assert any("Modelo de Ficha" in texto for texto in corpo["limitations"])


def test_aderencia_com_modelo_resolve_ciclo_e_slots(seed, raw, db_backend):
    template_id = _template(raw, user_id=seed["dono"])
    plan_id, itens = _plano(
        raw,
        user_id=seed["dono"],
        itens=[{"mapping_id": SUPINO, "sets_count": 3}],
        template_id=template_id,
        slot_sequence=1,
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=3,
        plan_id=plan_id,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]}],
    )

    corpo = _adherence(seed["dono"], window="28d").json()
    assert corpo["has_template"] is True
    # 4 slots -> ciclo de 4 dias (um slot = um dia, `template_cycle_length_days`,
    # mesma convencao de /analytics/dashboard-cycle e do motor de geracao): 7
    # voltas na janela de 28 dias x 4 slots de treino = 28 sessoes esperadas.
    assert corpo["cycle_length_days"] == 4
    assert corpo["expected_sessions"]["value"] == 28
    assert corpo["planned_sessions"]["value"] == 1
    assert corpo["missed_sessions"]["value"] == 27
    # 1/28 = 0,0357 -> o envelope `metric` arredonda todo valor a 2 casas.
    assert corpo["rate"]["value"] == 0.04
    assert [slot["status"] for slot in corpo["slots"]].count("done") == 1
    # Nenhum branch por nome: os slots vem do Modelo como estao.
    assert [slot["name"] for slot in corpo["slots"]] == ["Upper 1", "Lower 1", "Upper 2", "Lower 2"]


def test_window_cycle_funciona_com_modelo_e_falha_com_mensagem_sem_modelo(seed, raw, db_backend):
    # Sem Modelo: 400 com a instrucao do que fazer (nunca 500).
    sem_modelo = _workouts(seed["dono"], window="cycle")
    assert sem_modelo.status_code == 400
    assert "Modelo de Ficha" in sem_modelo.json()["detail"]

    _template(raw, user_id=seed["dono"], nomes=("Upper", "Lower"))
    com_modelo = _workouts(seed["dono"], window="cycle")
    assert com_modelo.status_code == 200
    # 2 slots -> ciclo de 2 dias (um slot = um dia).
    assert com_modelo.json()["window"]["days"] == 2
    assert com_modelo.json()["window"]["kind"] == "cycle"


def test_window_cycle_tambem_vale_para_os_outros_endpoints(seed, raw, db_backend):
    """A F1.1 destravou `cycle` para TODOS os endpoints v3 - antes dela os
    quatro respondiam 400 porque ninguem sabia resolver o tamanho do ciclo.
    `/muscles` prova a troca (o `_resolve` e o mesmo para os quatro)."""
    _template(raw, user_id=seed["dono"], nomes=("Upper", "Lower", "Full"))
    resposta = _client(seed["dono"]).get(
        "/analytics/dashboard/muscles", params={"window": "cycle"}
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["window"]["days"] == 3


def test_sessao_em_andamento_nao_conta_como_realizada(seed, raw, db_backend):
    plan_id, itens = _plano(
        raw, user_id=seed["dono"], itens=[{"mapping_id": SUPINO, "sets_count": 3}]
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=0,
        plan_id=plan_id,
        session_status="in_progress",
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_1, "plan_exercise_id": itens[0]}],
    )

    corpo = _adherence(seed["dono"], window="28d").json()
    assert corpo["completed_sessions"]["value"] == 0

    lista = _workouts(seed["dono"], window="28d").json()
    sessao = lista["items"][0]
    assert sessao["session_status"] == "IN_PROGRESS"
    # O item ainda nao executado NAO vira "pulado" enquanto o treino roda.
    assert sessao["item_status_counts"]["skipped"] == 0


# ---------------------------------------------------------------------------
# Paginacao e teto de 1 MB
# ---------------------------------------------------------------------------


def test_paginacao_e_payload_abaixo_de_1mb(seed, raw, db_backend):
    for dia in range(1, 31):
        _sessao(
            raw,
            user_id=seed["dono"],
            dias_atras=dia,
            exercicios=[
                {"mapping_id": SUPINO, "series": TRABALHO_3},
                {"mapping_id": REMADA, "series": TRABALHO_3},
            ],
        )

    primeira = _workouts(seed["dono"], window="90d", limit=20, offset=0).json()
    segunda = _workouts(seed["dono"], window="90d", limit=20, offset=20).json()

    assert primeira["total"] == 30
    assert len(primeira["items"]) == 20
    assert primeira["has_more"] is True
    assert len(segunda["items"]) == 10
    assert segunda["has_more"] is False
    # Sem sessao repetida entre as paginas.
    ids = [item["activity_id"] for item in primeira["items"] + segunda["items"]]
    assert len(set(ids)) == 30

    tamanho = len(json.dumps(primeira).encode("utf-8"))
    assert tamanho < 1_000_000, f"pagina com {tamanho} bytes - acima do teto de 1 MB da Data API"


def test_detalhe_declara_o_corte_de_series(seed, raw, db_backend):
    activity_id = _sessao(
        raw,
        user_id=seed["dono"],
        dias_atras=1,
        exercicios=[{"mapping_id": SUPINO, "series": TRABALHO_3}],
    )

    corpo = _detalhe(seed["dono"], activity_id, sets_limit=2).json()
    assert corpo["sets_limit"] == 2
    assert corpo["sets_truncated"] is True
    assert len(corpo["items"][0]["performed"]["sets"]) == 2

    inteiro = _detalhe(seed["dono"], activity_id).json()
    assert inteiro["sets_truncated"] is False


def test_janela_sem_sessao_responde_200_vazio(seed, raw, db_backend):
    corpo = _workouts(seed["dono"], window="7d").json()
    assert corpo["items"] == []
    assert corpo["total"] == 0
    assert corpo["data_quality"] == "INSUFFICIENT"


# ---------------------------------------------------------------------------
# Planejado de /dashboard/musculos (2026-10-02)
# ---------------------------------------------------------------------------


def test_planejado_de_musculos_sai_da_prescricao_da_ficha(seed, raw, db_backend):
    """A coluna Planejado somava so `raw_payload.template_slot_targets`, que a
    geracao `equipment_series` nunca grava: ficava vazia. Agora a fonte e a
    prescricao (`sets_count x stimulus_weight`), so series de trabalho; a
    chave antiga continua valendo para a ficha sem prescricao."""
    _plano(
        raw,
        user_id=seed["dono"],
        itens=[{"mapping_id": SUPINO, "sets_count": 3}, {"mapping_id": REMADA, "sets_count": 4}],
    )
    alvos_antigos = [{"muscle_group_id": seed["Peitoral"], "muscle_group_name": "Peitoral", "sets_count": 6}]
    with raw.cursor() as cur:
        # Aquecimento e ajuste moram em colunas proprias e nao entram.
        cur.execute("UPDATE workout_plan_exercises SET warmup_sets_count = 2, feeler_set_sets_count = 1")
        cur.execute(
            "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, raw_payload) "
            "VALUES (%s, %s, 'Ficha antiga', 'abc', 'planned', %s)",
            (seed["dono"], HOJE, json.dumps({"template_slot_targets": alvos_antigos})),
        )

    resposta = _client(seed["dono"]).get("/analytics/dashboard/muscles", params={"window": "28d"})
    assert resposta.status_code == 200, resposta.text
    peitoral = next(i for i in resposta.json()["items"] if i["muscle_group_id"] == seed["Peitoral"])
    # Ficha nova: 3 x 1,0 (supino) + 4 x 0,5 (remada) = 5; ficha antiga: 6.
    assert peitoral["planned"]["effective_sets"]["value"] == pytest.approx(11.0)
    assert peitoral["planned"]["plans_count"] == 2
