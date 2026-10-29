"""`GET /analytics/dashboard/engine` e `/engine/plans/{plan_id}` (F3.1) contra
MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API de
producao).

O que este arquivo prova:

- as TRES colunas ponta a ponta: o que o Modelo planejou, o que o motor gerou
  (com o motivo vindo do `generation_audit`) e o que a sessao registrou;
- a PREFERENCIA pela tabela e o FALLBACK declarado: a mesma ficha, com e sem
  linha em `generation_runs`, responde com `source` diferente e com a
  limitacao que diz o que o `raw_payload` nao tem;
- **IDOR**: ficha do usuario B responde 404 para o usuario A - o MESMO 404 de
  ficha inexistente, entao nenhum id alheio e confirmado;
- os quatro estados de ausencia declarados (ficha sem auditoria, geracao cujo
  rascunho nao virou ficha, item sem decisao de progressao, ficha sem sessao)
  produzem FRASES, nunca zeros;
- o payload e IDENTICO nos dois backends (o `Decimal` do PyMySQL vs. a string
  do shim ja quebrou leituras parecidas antes);
- **custo travado**: o detalhe da ficha emite uma contagem FIXA de statements.
  E aqui que um N+1 por item (uma consulta de progressao por exercicio, um
  nome de exercicio por candidato recusado) reprova - e e exatamente esse tipo
  de laco que estourou o timeout de 29s do API Gateway em producao (#188).

O teto de statements vive NESTE arquivo, e nao em
`test_medicao_dataset_pequeno.py`, por uma razao pratica: o dataset `PEQUENO`
(`tests/support/dataset_medicao.py`) nao semeia ficha nenhuma - ele e feito de
sessoes avulsas -, entao a medicao desta tela precisa do seu proprio cenario
com Modelo, ficha, auditoria e sessao. O helper de afirmacao continua sendo o
compartilhado (`tests/support/medicao.py`).
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
from app.intelligence.dashboard import engine_service  # noqa: F401 - precisa estar importado
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import truncar
from tests.support.medicao import assert_no_maximo_statements
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.sql_capture import recorded_sql
from tests.support.template_versions import versao_v1

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL

SUPINO = 4001
CRUCIFIXO = 4002
REMADA = 4003

# Teto do detalhe da ficha. Saudavel: 6 consultas fixas + 1 condicional (o
# nome dos candidatos recusados) + a resolucao do fuso do usuario = 8. O teto
# e 11 para caber uma consulta nova legitima; ele NAO cabe um N+1, porque a
# ficha do cenario tem 3 itens e 2 candidatos recusados - qualquer laco por
# item ou por candidato soma varios de uma vez e reprova aqui.
MAX_STATEMENTS_ENGINE_PLAN = 11

# A lista da janela: 2 consultas + o fuso = 3. Teto 5.
MAX_STATEMENTS_ENGINE_RUNS = 5


AUDIT = {
    "template_id": None,  # preenchido no seed
    "slot_sequence": 1,
    "volume_mode": "percent_budget",
    "engine_version": "v2-test",
    "llm_used": False,
    "activation": {},  # preenchido no seed (o id do grupo so existe apos o INSERT)
    "selection": {},
    "safety_clamps": [
        {"exercise_id": SUPINO, "equipment_id": None, "field": "rpe_target", "from": 9, "to": 8, "reason": "RECOVERY"}
    ],
    "substitutions": [],
    "fallbacks": [{"exercise_id": SUPINO, "reason": "SETS_REDUCED_FOR_SECONDARY_BUDGET", "from": 4, "to": 3}],
}


@pytest.fixture
def seed(raw):
    """Dois usuarios, um grupo, tres exercicios canonicos."""
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel)
            )
            ids[nome] = cur.lastrowid

        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peitoral')")
        ids["Peitoral"] = cur.lastrowid

        for exercise_id, nome in ((SUPINO, "Supino reto"), (CRUCIFIXO, "Crucifixo"), (REMADA, "Remada")):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, nome, nome),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, nome))
    return ids


# ---------------------------------------------------------------------------
# Semeadura
# ---------------------------------------------------------------------------


def _modelo(raw, *, user_id, volume_mode="percent_budget", itens=()):
    """Modelo com UM slot. `itens` = `(item_kind, mapping_id, muscle_group_id,
    sets_count)`."""
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Modelo F3.1', %s, 1, %s)",
            (volume_mode, user_id),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'Upper 1', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
        slot_id = cur.lastrowid
        for ordem, (kind, mapping_id, group_id, sets_count) in enumerate(itens, start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slot_items "
                "(slot_id, item_order, item_kind, muscle_group_id, mapping_id, sets_count, reps_target) "
                "VALUES (%s, %s, %s, %s, %s, %s, '8-10')",
                (slot_id, ordem, kind, group_id, mapping_id, sets_count),
            )
    return template_id


def _ficha(raw, *, user_id, itens, template_id=None, audit=None, nome="Ficha F3.1"):
    """Ficha salva. `audit` != None grava o bloco no `raw_payload` (o caminho
    das fichas anteriores ao M16)."""
    raw_payload = json.dumps({"generation_audit": audit}) if audit is not None else None
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, template_id, "
            "template_slot_sequence, raw_payload) VALUES (%s, %s, %s, 'full_body', 'planned', %s, 1, %s)",
            (user_id, HOJE, nome, template_id, raw_payload),
        )
        plan_id = cur.lastrowid
        item_ids = []
        for sequence, item in enumerate(itens, start=1):
            cur.execute(
                "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, mapping_id, "
                "sets_count, reps_target, load_target, rpe_target, rest_seconds) "
                "VALUES (%s, %s, %s, %s, %s, '8-10', '60', '8', 90)",
                (plan_id, sequence, item["name"], item["mapping_id"], item.get("sets_count", 3)),
            )
            item_ids.append(cur.lastrowid)
    return plan_id, item_ids


def _run(raw, *, user_id, plan_id, audit):
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO generation_runs (user_id, workout_plan_id, job_id, template_id, "
            "template_slot_sequence, volume_mode, engine_version, llm_used, plan_date, audit_hash, "
            "audit_json, created_at) VALUES (%s, %s, 'job-1', %s, 1, %s, %s, 0, %s, %s, %s, %s)",
            (
                user_id,
                plan_id,
                audit.get("template_id"),
                audit.get("volume_mode"),
                audit.get("engine_version"),
                HOJE,
                "a" * 64,
                json.dumps(audit),
                _dt.datetime.combine(HOJE, _dt.time(8, 0)),
            ),
        )
        return cur.lastrowid


def _sessao(raw, *, user_id, plan_id, exercicios, dias_atras=0):
    dia = HOJE - _dt.timedelta(days=dias_atras)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, session_status, "
            "workout_plan_id, duration_seconds) VALUES (%s, %s, %s, 'Treino', 'done', %s, 3600)",
            (user_id, dia, _dt.datetime.combine(dia, _dt.time(19, 0)), plan_id),
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


def _audit(seed, template_id):
    audit = json.loads(json.dumps(AUDIT))
    audit["template_id"] = template_id
    audit["activation"] = {
        str(seed["Peitoral"]): {
            "muscle_group_id": seed["Peitoral"],
            "muscle_group_name": "Peitoral",
            "target_weekly": 12.0,
            "target_min": 6.0,
            "target_max": 9.0,
            "result": 7.5,
            "deviation_status": "BALANCED",
            "recovery_status": "READY",
        }
    }
    audit["selection"] = {
        str(seed["Peitoral"]): [
            {"exercise_id": SUPINO, "rank": 1, "selected": True, "reason": None, "muscle_group_id": seed["Peitoral"]},
            {"exercise_id": CRUCIFIXO, "rank": 2, "selected": True, "reason": None},
            {"exercise_id": REMADA, "rank": 3, "selected": False, "reason": "SECONDARY_BUDGET_EXCEEDED"},
            {"exercise_id": 4004, "rank": 4, "selected": False, "reason": "UNAVAILABLE"},
        ]
    }
    return audit


@pytest.fixture
def cenario(raw, seed):
    """Modelo `equipment_series` que nomeia o supino, ficha com tres itens,
    linha em `generation_runs` e uma sessao que cumpriu um, interrompeu outro
    e pulou o terceiro."""
    template_id = _modelo(
        raw,
        user_id=seed["dono"],
        volume_mode="equipment_series",
        itens=(("exercise", SUPINO, seed["Peitoral"], 3), ("muscle_group", None, seed["Peitoral"], 3)),
    )
    plan_id, itens = _ficha(
        raw,
        user_id=seed["dono"],
        template_id=template_id,
        itens=[
            {"name": "Supino reto", "mapping_id": SUPINO, "sets_count": 3},
            {"name": "Crucifixo", "mapping_id": CRUCIFIXO, "sets_count": 3},
            {"name": "Remada", "mapping_id": REMADA, "sets_count": 3},
        ],
    )
    audit = _audit(seed, template_id)
    run_id = _run(raw, user_id=seed["dono"], plan_id=plan_id, audit=audit)
    _sessao(
        raw,
        user_id=seed["dono"],
        plan_id=plan_id,
        exercicios=[
            {"name": "Supino reto", "mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[0]},
            {"name": "Crucifixo", "mapping_id": CRUCIFIXO, "series": TRABALHO_1, "plan_exercise_id": itens[1]},
        ],
    )
    return {"template_id": template_id, "plan_id": plan_id, "itens": itens, "run_id": run_id, **seed}


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _runs(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/engine", params=params)


def _plano(user_id: int, plan_id: int, **params):
    return _client(user_id).get(f"/analytics/dashboard/engine/plans/{plan_id}", params=params)


def _por_sequencia(corpo):
    return {item["plan_sequence"]: item for item in corpo["items"]}


# ---------------------------------------------------------------------------
# Paridade entre backends
# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(cenario, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _plano(cenario["dono"], cenario["plan_id"], window="28d")
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]
    corpo = resultados["pymysql"]
    assert corpo["metric_versions"]["generation_audit"] == "generation-audit-v1"
    assert corpo["metric_versions"]["engine_plan_diff"] == "engine-diff-v1"


# ---------------------------------------------------------------------------
# As tres colunas
# ---------------------------------------------------------------------------


def test_planejado_gerado_executado_lado_a_lado(db_backend, cenario):
    corpo = _plano(cenario["dono"], cenario["plan_id"], window="28d").json()
    itens = _por_sequencia(corpo)

    # 1) o Modelo NOMEIA o supino -> a coluna planejada vem do item do Modelo.
    assert itens[1]["planned"]["source"] == "template_item"
    assert itens[1]["planned"]["exercise_id"] == SUPINO
    assert itens[1]["generated"]["sets_count"] == 3
    assert itens[1]["executed"]["working_sets"] == 3
    assert itens[1]["status"] == "COMPLETED"

    # 2) o Modelo NAO nomeia o crucifixo (o item dele e `muscle_group`) -> a
    # coluna planejada nao pode inventar um exercicio.
    assert itens[2]["planned"]["source"] != "template_item"
    assert itens[2]["status"] == "INTERRUPTED"
    assert itens[2]["missing_sets"] == 2

    # 3) item da ficha sem serie nenhuma.
    assert itens[3]["status"] == "SKIPPED"
    assert itens[3]["executed"] is None

    assert corpo["item_status_counts"] == {
        "completed": 1,
        "interrupted": 1,
        "skipped": 1,
        "substituted": 0,
        "extra": 0,
    }


def test_o_porque_do_item_vem_do_generation_audit(db_backend, cenario):
    corpo = _plano(cenario["dono"], cenario["plan_id"], window="28d").json()
    supino = _por_sequencia(corpo)[1]
    tipos = {reason["kind"]: reason["code"] for reason in supino["reasons"]}

    assert tipos["safety_clamp"] == "RECOVERY"
    assert tipos["fallback"] == "SETS_REDUCED_FOR_SECONDARY_BUDGET"
    assert "selection" in tipos


def test_candidatos_recusados_ganham_nome_do_catalogo_sem_consulta_por_item(db_backend, cenario):
    corpo = _plano(cenario["dono"], cenario["plan_id"], window="28d").json()
    por_id = {item["exercise_id"]: item for item in corpo["rejections"]}

    assert por_id[REMADA]["exercise_name"] == "Remada"
    assert por_id[REMADA]["reason"] == "SECONDARY_BUDGET_EXCEEDED"
    # Candidato que nao existe mais no catalogo: nome ausente, NUNCA inventado.
    assert por_id[4004]["exercise_name"] is None


def test_orcamento_por_grupo_chega_da_auditoria(db_backend, cenario):
    corpo = _plano(cenario["dono"], cenario["plan_id"], window="28d").json()
    assert corpo["group_budgets"][0]["muscle_group_name"] == "Peitoral"
    assert corpo["group_budgets"][0]["generated_sets"] == 7.5


# ---------------------------------------------------------------------------
# Preferencia pela tabela e fallback DECLARADO
# ---------------------------------------------------------------------------


def test_com_linha_em_generation_runs_a_fonte_e_a_tabela(db_backend, cenario):
    corpo = _plano(cenario["dono"], cenario["plan_id"], window="28d").json()
    assert corpo["run"]["source"] == "generation_runs"
    assert corpo["run"]["run_id"] == cenario["run_id"]
    assert corpo["run"]["created_at"] is not None
    assert engine_service.AUDIT_FROM_RAW_PAYLOAD_LIMITATION not in corpo["limitations"]


def test_ficha_antiga_cai_no_raw_payload_com_a_limitacao_escrita(db_backend, raw, seed):
    """Ficha anterior ao M16: nenhuma linha em `generation_runs`, mas o bloco
    ecoado no `raw_payload`. O conteudo aparece; a data da geracao, nao."""
    template_id = _modelo(raw, user_id=seed["dono"], itens=(("muscle_group", None, seed["Peitoral"], 3),))
    audit = _audit(seed, template_id)
    plan_id, _ = _ficha(
        raw,
        user_id=seed["dono"],
        template_id=template_id,
        audit=audit,
        itens=[{"name": "Supino reto", "mapping_id": SUPINO}],
    )

    corpo = _plano(seed["dono"], plan_id, window="28d").json()

    assert corpo["run"]["source"] == "plan_raw_payload"
    assert corpo["run"]["run_id"] is None
    assert engine_service.AUDIT_FROM_RAW_PAYLOAD_LIMITATION in corpo["limitations"]
    # O bloco continua legivel: o motivo do item nao se perde no fallback.
    assert any(r["code"] == "RECOVERY" for r in corpo["items"][0]["reasons"])


def test_ficha_sem_auditoria_nenhuma_diz_isso_em_vez_de_tabela_vazia(db_backend, raw, seed):
    plan_id, _ = _ficha(raw, user_id=seed["dono"], itens=[{"name": "Supino reto", "mapping_id": SUPINO}])

    corpo = _plano(seed["dono"], plan_id, window="28d").json()

    assert corpo["run"]["source"] == "unavailable"
    assert corpo["data_quality"] == "INSUFFICIENT"
    assert engine_service.AUDIT_UNAVAILABLE_LIMITATION in corpo["limitations"]
    # A ficha continua visivel - a auditoria e que falta.
    assert corpo["items"][0]["generated"]["sets_count"] == 3


def test_ficha_sem_modelo_declara_a_coluna_planejada_vazia(db_backend, raw, seed):
    plan_id, _ = _ficha(raw, user_id=seed["dono"], itens=[{"name": "Supino reto", "mapping_id": SUPINO}])

    corpo = _plano(seed["dono"], plan_id, window="28d").json()

    assert engine_service.NO_TEMPLATE_LIMITATION in corpo["limitations"]
    assert corpo["items"][0]["planned"]["source"] == "unavailable"


def test_ficha_sem_sessao_nao_zera_a_coluna_executada(db_backend, raw, seed):
    plan_id, _ = _ficha(raw, user_id=seed["dono"], itens=[{"name": "Supino reto", "mapping_id": SUPINO}])

    corpo = _plano(seed["dono"], plan_id, window="28d").json()

    assert engine_service.NOT_EXECUTED_LIMITATION in corpo["limitations"]
    assert corpo["items"][0]["executed"] is None
    assert corpo["sessions_count"] == 0


def test_item_sem_decisao_de_progressao_diz_que_nao_ha_vinculo(db_backend, cenario):
    corpo = _plano(cenario["dono"], cenario["plan_id"], window="28d").json()
    assert engine_service.NO_PROGRESSION_DECISION_LIMITATION in corpo["limitations"]
    assert all(item["progression"] is None for item in corpo["items"])


# ---------------------------------------------------------------------------
# Item coberto por outro slot (PR #211), ponta a ponta
# ---------------------------------------------------------------------------


def test_item_coberto_por_outro_slot_ganha_a_ressalva_sem_ser_reclassificado(db_backend, raw, seed):
    """O defeito do PR #211: a execucao do supino foi gravada no SLOT do
    crucifixo. O §5 continua dizendo `SKIPPED` para o supino - o que e verdade
    sobre o vinculo -, e a tela acrescenta a frase que diz onde a execucao
    foi parar."""
    plan_id, itens = _ficha(
        raw,
        user_id=seed["dono"],
        itens=[
            {"name": "Supino reto", "mapping_id": SUPINO},
            {"name": "Crucifixo", "mapping_id": CRUCIFIXO},
        ],
    )
    _sessao(
        raw,
        user_id=seed["dono"],
        plan_id=plan_id,
        exercicios=[{"name": "Supino reto", "mapping_id": SUPINO, "series": TRABALHO_3, "plan_exercise_id": itens[1]}],
    )

    corpo = _plano(seed["dono"], plan_id, window="28d").json()
    supino = _por_sequencia(corpo)[1]

    assert supino["status"] == "SKIPPED"
    assert engine_service.COVERED_BY_OTHER_SLOT_LIMITATION in supino["limitations"]


# ---------------------------------------------------------------------------
# Lista da janela
# ---------------------------------------------------------------------------


def test_lista_une_as_duas_fontes_sem_duplicar_a_mesma_ficha(db_backend, raw, seed, cenario):
    """A ficha do cenario tem linha em `generation_runs` E bloco possivel no
    `raw_payload`: ela nao pode aparecer duas vezes."""
    template_id = cenario["template_id"]
    _ficha(
        raw,
        user_id=seed["dono"],
        template_id=template_id,
        audit=_audit(seed, template_id),
        itens=[{"name": "Supino reto", "mapping_id": SUPINO}],
        nome="Ficha antiga",
    )

    corpo = _runs(seed["dono"], window="28d").json()
    fontes = [item["source"] for item in corpo["items"]]

    assert corpo["runs_table_total"] == 1
    assert corpo["raw_payload_total"] == 1
    assert sorted(fontes) == ["generation_runs", "plan_raw_payload"]
    ids = [item["workout_plan_id"] for item in corpo["items"]]
    assert len(ids) == len(set(ids)), "a mesma ficha apareceu por duas fontes"


def test_geracao_cujo_rascunho_nao_virou_ficha_aparece_com_a_frase_propria(db_backend, raw, seed):
    audit = _audit(seed, None)
    _run(raw, user_id=seed["dono"], plan_id=None, audit=audit)

    corpo = _runs(seed["dono"], window="28d").json()
    linha = corpo["items"][0]

    assert linha["workout_plan_id"] is None
    assert engine_service.AUDIT_DRAFT_NOT_SAVED_LIMITATION in linha["limitations"]


def test_janela_sem_geracao_responde_200_com_a_frase_e_sem_zero_inventado(db_backend, seed):
    corpo = _runs(seed["dono"], window="7d").json()

    assert corpo["items"] == []
    assert corpo["data_quality"] == "INSUFFICIENT"
    assert engine_service.NO_RUNS_IN_WINDOW_LIMITATION in corpo["limitations"]


def test_contagens_da_lista_vem_do_json_sem_trazer_o_bloco_inteiro(db_backend, cenario):
    corpo = _runs(cenario["dono"], window="28d").json()
    linha = corpo["items"][0]

    assert linha["safety_clamp_count"] == 1
    assert linha["fallback_count"] == 1
    assert linha["selection_count"] == 1  # `selection` e um dicionario de UM grupo
    assert "audit_json" not in linha


# ---------------------------------------------------------------------------
# IDOR
# ---------------------------------------------------------------------------


def test_ficha_de_outro_usuario_responde_o_mesmo_404_de_ficha_inexistente(db_backend, cenario):
    alheia = _plano(cenario["outro"], cenario["plan_id"])
    inexistente = _plano(cenario["outro"], 999_999)

    assert alheia.status_code == 404
    assert inexistente.status_code == 404
    assert alheia.json() == inexistente.json()


def test_lista_do_outro_usuario_nao_ve_geracao_alheia(db_backend, cenario):
    corpo = _runs(cenario["outro"], window="28d").json()
    assert corpo["items"] == []


# ---------------------------------------------------------------------------
# Custo travado
# ---------------------------------------------------------------------------


def test_detalhe_da_ficha_tem_contagem_fixa_de_statements(db_backend, cenario, monkeypatch, fake_rds):
    """A protecao de verdade (§15): o que derrubou estes endpoints em producao
    foi contagem de idas ao banco, nao constante de CPU. Uma consulta de
    progressao por item, ou um nome de exercicio por candidato recusado,
    reprova aqui."""
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        resposta = _plano(cenario["dono"], cenario["plan_id"], window="28d")

    assert resposta.status_code == 200, resposta.text
    print(f"[MEDIDO] engine/plans backend={db_backend}: {len(statements)} statements")
    assert_no_maximo_statements(
        statements,
        teto=MAX_STATEMENTS_ENGINE_PLAN,
        label="dashboard/engine/plans/{plan_id}",
        backend=db_backend,
    )


def test_lista_de_geracoes_tem_contagem_fixa_de_statements(db_backend, cenario, monkeypatch, fake_rds):
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        resposta = _runs(cenario["dono"], window="28d")

    assert resposta.status_code == 200, resposta.text
    print(f"[MEDIDO] engine backend={db_backend}: {len(statements)} statements")
    assert_no_maximo_statements(
        statements, teto=MAX_STATEMENTS_ENGINE_RUNS, label="dashboard/engine", backend=db_backend
    )
