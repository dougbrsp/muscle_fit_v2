"""`GET /reports/markdown` e o coletor `collect_report_data` contra MySQL real,
nos DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- o SQL proprio do relatorio (tonelagem ciente de `load_convention`, medias de
  sono/wearable, sessoes por trecho de vigencia) roda igual nos dois backends
  e o arquivo gerado e IDENTICO (fora a linha "Gerado em");
- tonelagem: so series de TRABALHO (aquecimento fora), ponderada por
  `stimulus_weight`, e o par equipamento<->exercicio `assisted` fica fora,
  com as series excluidas contadas;
- os numeros por grupo sao os de `build_muscle_summary` (series fisicas,
  efetivas, RPE medio) - o relatorio nao recalcula nada;
- IDOR: o usuario A nao ve serie do usuario B;
- um periodo sem nada registrado gera o arquivo sem erro.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py). Inclui TUDO
# que o coletor importa sob demanda - um modulo importado pela primeira vez
# durante o teste do shim guardaria o `transaction`/`get_conn` do shim para
# os testes seguintes.
from app import (  # noqa: F401 - precisam estar importados
    advanced_techniques,
    analytics_dashboard,
    reports,
    template_assignments,
    training_types,
    training_type_targets,
    workout_plan_templates,
    workout_plan_templates_engine,
)
from app.intelligence import methodology_volume_service  # noqa: F401
from app.intelligence.dashboard import (  # noqa: F401
    adherence_service,
    body_service,
    cardio_service,
    muscle_summary_service,
    recovery_signals_service,
)
from app.intelligence.dashboard.contracts import WindowKind
from app.intelligence.dashboard.time_windows import resolve_window
from app.intelligence.report import markdown_report_service
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.methodology_paridade import semear_macro_grupos
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()

SUPINO = 3101
GRAVITON = 3102


@pytest.fixture
def seed(raw):
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome in ("dono", "outro"):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (nome,))
            ids[nome] = cur.lastrowid

        macro = semear_macro_grupos(cur)
        ids["macro"] = next(iter(macro.values()))
        cur.execute("SELECT name FROM muscle_volume_classes WHERE id = %s", (ids["macro"],))
        ids["macro_nome"] = cur.fetchone()["name"]

        for grupo in ("Peitoral", "Triceps", "Costas"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid
        cur.execute("UPDATE muscle_groups SET volume_class_id = %s WHERE id = %s", (ids["macro"], ids["Peitoral"]))

        for exercise_id, nome in ((SUPINO, "Supino reto"), (GRAVITON, "Barra fixa assistida")):
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
                (GRAVITON, ids["Costas"], "PRIMARY", 1.0, 1.0),
            ],
        )

        cur.execute("INSERT INTO gyms (name) VALUES ('Academia')")
        gym_id = cur.lastrowid
        for exercise_id, equipamento in ((SUPINO, "Banco reto"), (GRAVITON, "Graviton")):
            cur.execute("INSERT INTO equipment (name) VALUES (%s)", (equipamento,))
            equipment_id = cur.lastrowid
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
                (gym_id, exercise_id, equipment_id),
            )
            ids[f"ge_{exercise_id}"] = cur.lastrowid
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id, is_active) VALUES (%s, %s, 1)",
                (equipment_id, exercise_id),
            )
        # Graviton: o peso ALIVIA o movimento - nao e carga movida.
        cur.execute(
            "UPDATE equipment_exercises SET load_convention = 'assisted' WHERE exercise_id = %s", (GRAVITON,)
        )
    return ids


def _sessao(raw, *, user_id, dias_atras, mapping_id, gym_equipment_id, series):
    """`series` = `(set_number, set_type, reps, peso, rpe)`."""
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


def _historico(raw, seed):
    # Supino: 1 aquecimento (fora de tudo) + 2 de trabalho 10 x 60 kg.
    _sessao(
        raw, user_id=seed["dono"], dias_atras=3, mapping_id=SUPINO, gym_equipment_id=seed[f"ge_{SUPINO}"],
        series=[(1, "warmup", 15, "20.00", None), (2, "working", 10, "60.00", "8.00"), (3, "working", 10, "60.00", "9.00")],
    )
    # Graviton assistido: 3 series de trabalho que NAO entram na tonelagem.
    _sessao(
        raw, user_id=seed["dono"], dias_atras=2, mapping_id=GRAVITON, gym_equipment_id=seed[f"ge_{GRAVITON}"],
        series=[(1, "working", 8, "30.00", "8.00"), (2, "working", 8, "30.00", "8.00"), (3, "working", 7, "30.00", "9.00")],
    )
    # Outro usuario: nada disso pode aparecer no relatorio do dono.
    _sessao(
        raw, user_id=seed["outro"], dias_atras=1, mapping_id=SUPINO, gym_equipment_id=seed[f"ge_{SUPINO}"],
        series=[(1, "working", 10, "200.00", "9.00")],
    )
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO sleep_metrics (user_id, local_date, duration_hours, deep_sleep_hours, rem_sleep_hours, "
            "regularity_percent) VALUES (%s, %s, 7.50, 1.50, 2.00, 80.00), (%s, %s, 6.50, NULL, 1.00, NULL)",
            (seed["dono"], HOJE - _dt.timedelta(days=1), seed["dono"], HOJE - _dt.timedelta(days=2)),
        )


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(reports.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _sem_gerado_em(texto: str) -> str:
    return "\n".join(linha for linha in texto.splitlines() if not linha.startswith("- **Gerado em:**"))


def test_markdown_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _historico(raw, seed)
    congelar_relogio(monkeypatch)

    corpos = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _client(seed["dono"]).get("/reports/markdown", params={"window": "30d"})
        assert resposta.status_code == 200, (backend, resposta.text)
        assert resposta.headers["content-type"] == "text/markdown; charset=utf-8"
        corpos[backend] = _sem_gerado_em(resposta.text)

    assert corpos["pymysql"] == corpos["data-api"]
    corpo = corpos["pymysql"]
    assert "Seções que não puderam ser lidas agora" not in corpo, corpo
    # Peitoral: 2 series fisicas de trabalho, tonelagem 2 x 10 x 60 x 1,0.
    linha_peitoral = next(linha for linha in corpo.splitlines() if linha.startswith("| Peitoral |"))
    assert f"| Peitoral | {seed['macro_nome']} | 2 | 2,0 |" in linha_peitoral
    assert "| 1.200 kg | 8,5 |" in linha_peitoral
    # Triceps: secundario (0,5) -> 600 kg.
    assert "| 600 kg |" in next(linha for linha in corpo.splitlines() if linha.startswith("| Triceps |"))
    # Costas (graviton assistido): sem tonelagem, series declaradas.
    assert "| — |" in next(linha for linha in corpo.splitlines() if linha.startswith("| Costas |"))
    assert "**3 série(s) fora da tonelagem** (3 assistido)" in corpo
    assert "| Sono — duração | 7,00 h | 2 |" in corpo
    assert "| Sono — profundo | 1,50 h | 1 |" in corpo


def test_coletor_isola_usuario_e_bate_com_o_resumo_muscular(seed, raw, db_backend):
    _historico(raw, seed)
    from app.db import get_conn

    janela = resolve_window(WindowKind.ROLLING_90D, today=HOJE, timezone_name="America/Sao_Paulo")
    with get_conn() as conn:
        dados = markdown_report_service.collect_report_data(conn, user_id=seed["dono"], window=janela)

    assert dados["quality"]["unavailable_sections"] == [], db_backend
    grupos = {g["name"]: g for g in dados["muscles"]["groups"]}
    assert grupos["Peitoral"]["physical_sets"] == 2
    assert grupos["Peitoral"]["tonnage_kg"] == 1200.0  # 200 kg do outro usuario nao entram
    assert grupos["Peitoral"]["avg_rpe"] == 8.5
    assert grupos["Triceps"]["tonnage_kg"] == 600.0
    assert grupos["Costas"]["tonnage_kg"] is None
    assert grupos["Costas"]["tonnage_excluded_sets"] == 3
    assert dados["quality"]["tonnage_excluded_sets"] == 3
    assert dados["recovery"]["sleep"]["days"] == 2
    assert dados["cycles"]["sessions_total"] == 2
    assert dados["cycles"]["current_template"] is None


def test_periodo_sem_dados_gera_sem_erro(seed, raw, db_backend):
    resposta = _client(seed["outro"]).get(
        "/reports/markdown",
        params={
            "window": "custom",
            "date_from": (HOJE - _dt.timedelta(days=179)).isoformat(),
            "date_to": (HOJE - _dt.timedelta(days=100)).isoformat(),
        },
    )
    assert resposta.status_code == 200, (db_backend, resposta.text)
    assert "Sem dados no período" in resposta.text
    assert "Seções que não puderam ser lidas agora" not in resposta.text


# ---------------------------------------------------------------------------
# Detalhamento diario (grupo, equipamento, recuperacao, ciclos)
# ---------------------------------------------------------------------------


def _br(dias_atras: int) -> str:
    return (HOJE - _dt.timedelta(days=dias_atras)).strftime("%d/%m/%Y")


def test_markdown_diario_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _historico(raw, seed)
    congelar_relogio(monkeypatch)

    corpos = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _client(seed["dono"]).get("/reports/markdown", params={"window": "30d"})
        assert resposta.status_code == 200, (backend, resposta.text)
        corpos[backend] = _sem_gerado_em(resposta.text)

    assert corpos["pymysql"] == corpos["data-api"]
    corpo = corpos["pymysql"]
    assert "Detalhamento diário não fecha" not in corpo
    # Grupo por dia: data DD/MM/AAAA, tonelagem com a mesma regra do resumo.
    assert f"| {_br(3)} | Peitoral | 2 | 2,00 | 1.200 kg | 8,5 | 1 / 1 |" in corpo
    assert f"| {_br(3)} | Triceps | 2 | 1,00 | 600 kg | 8,5 | 1 / 1 |" in corpo
    # Equipamento: carga TOTAL sem conversao; assistido sem tonelagem, com reps.
    assert (
        f"| {_br(3)} | Banco reto (Academia) | Supino reto | 2 | 2,00 | 1.200 kg | 8,5 | 60,0 kg / 60,0 kg | 20 |"
    ) in corpo
    assert (
        f"| {_br(2)} | Graviton (Academia) | Barra fixa assistida | 3 | 3,00 | — (assistido: ver reps) | 8,3 | "
        "30,0 kg / 30,0 kg | 23 |"
    ) in corpo
    # Sono por dia, em ordem cronologica.
    assert corpo.index(f"| {_br(2)} | 6,50 h |") < corpo.index(f"| {_br(1)} | 7,50 h |")


def test_coletor_diario_soma_bate_com_o_periodo_e_isola_usuario(seed, raw, db_backend):
    _historico(raw, seed)
    # Serie sem equipamento cadastrado: entra como "Sem equipamento".
    _sessao(
        raw, user_id=seed["dono"], dias_atras=1, mapping_id=SUPINO, gym_equipment_id=None,
        series=[(1, "working", 12, "50.00", "7.00")],
    )
    from app.db import get_conn

    janela = resolve_window(WindowKind.ROLLING_90D, today=HOJE, timezone_name="America/Sao_Paulo")
    with get_conn() as conn:
        dados = markdown_report_service.collect_report_data(conn, user_id=seed["dono"], window=janela)

    assert dados["quality"]["unavailable_sections"] == [], db_backend
    assert dados["quality"]["daily_invariant_problems"] == [], db_backend

    grupos = {g["name"]: g for g in dados["muscles"]["groups"]}
    peitoral_dias = [d for d in dados["muscles"]["days"] if d["name"] == "Peitoral"]
    assert len(peitoral_dias) == 2
    assert sum(d["physical_sets"] for d in peitoral_dias) == grupos["Peitoral"]["physical_sets"] == 3
    assert sum(d["tonnage_kg"] for d in peitoral_dias) == pytest.approx(grupos["Peitoral"]["tonnage_kg"])
    datas = [d["local_date"] for d in dados["muscles"]["days"]]
    assert datas == sorted(datas)

    equipamentos = {e["equipment"]: e for e in dados["equipment"]["period"]}
    assert set(equipamentos) == {"Banco reto (Academia)", "Graviton (Academia)", "Sem equipamento"}
    # 200 kg do outro usuario nao entram; carga gravada (total), sem conversao.
    assert equipamentos["Banco reto (Academia)"]["tonnage_kg"] == 1200.0
    assert equipamentos["Banco reto (Academia)"]["load_max"] == 60.0
    assert equipamentos["Graviton (Academia)"]["tonnage_kg"] is None
    assert equipamentos["Graviton (Academia)"]["excluded_conventions"] == ["assisted"]
    assert equipamentos["Graviton (Academia)"]["reps"] == 23
    assert equipamentos["Sem equipamento"]["tonnage_kg"] == 600.0
    assert equipamentos["Sem equipamento"]["physical_sets"] == 1

    assert [d["local_date"] for d in dados["recovery"]["days"]] == [
        HOJE - _dt.timedelta(days=2),
        HOJE - _dt.timedelta(days=1),
    ]
    assert [t["physical_sets"] for t in dados["cycles"]["timeline"]] == [2, 3, 1]
    assert dados["cycles"]["timeline"][0]["avg_rpe"] == 8.5


def test_periodo_vazio_tem_detalhamento_vazio_sem_quebrar(seed, raw, db_backend):
    _historico(raw, seed)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('vazio', 'x', 'regular')")
        vazio = cur.lastrowid
    from app.db import get_conn

    janela = resolve_window(WindowKind.ROLLING_90D, today=HOJE, timezone_name="America/Sao_Paulo")
    with get_conn() as conn:
        dados = markdown_report_service.collect_report_data(conn, user_id=vazio, window=janela)

    assert dados["quality"]["unavailable_sections"] == [], db_backend
    assert dados["equipment"] == {"available": True, "days": [], "period": [], "daily_invariant_problems": []}
    assert dados["muscles"]["days"] == []
    assert dados["recovery"]["days"] == []
    assert dados["cycles"]["timeline"] == []
