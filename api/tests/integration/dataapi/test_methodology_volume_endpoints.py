"""Fase 4, PR E - endpoints da metodologia de volume contra MySQL real, nos
DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

O que so o banco prova (o resto esta em `tests/unit/test_methodology_volume_service.py`):

- as tres leituras (`exercise_muscle_contributions` PRIMARY ativas,
  `exercise_legacy_id_map` + `exercise_catalog` para o canonico,
  `muscle_groups` com porte e pai da 055) montam a MESMA avaliacao que o
  cenario em memoria - numeros conferidos a mao, fatia somada no pai,
  id absorvido de-duplicado pelo canonico e herdando as PRIMARY dele,
  item sem PRIMARY em `data_quality`;
- `POST` e `PUT` do Modelo anexam `methodology_volume_warnings` e continuam
  respondendo 200 quando o avaliador explode (gancho aditivo, nunca bloqueia);
- `GET /{id}/methodology-volume` sobre o Modelo salvo bate com o preview do
  mesmo payload; 404 e 422 no caminho real do router.

Isolamento: `truncar(raw)` com a lista unica de `isolamento_v3.TABELAS_V3`
(o schema e um so para a execucao inteira; `users` truncada reinicia o
AUTO_INCREMENT e o "usuario 1" de outro arquivo viraria o daqui). Sem
relogio: nada aqui deriva de `now()`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.intelligence import methodology_volume_service as svc
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support.isolamento_v3 import truncar
from tests.support.methodology_paridade import semear_macro_grupos

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

# Ids reais da 002/055 para os grupos; exercicios em faixa propria.
PEITORAL, PEITORAL_SUP, COSTAS, TRICEPS, BICEPS, CORE = 52, 12, 6, 14, 5, 55
SUPINO, SUPINO_B, INCLINADO, CRUCIFIXO, CORDA, SEM_PRIMARY, REMADA = 7100, 7107, 7101, 7110, 7106, 7108, 7102
EQUIPAMENTO = 9001


@pytest.fixture
def seed(raw):
    """Um admin; seis grupos (Peitoral superior e fatia do Peitoral; Core nao
    classificado); sete exercicios - o 7107 e o supino reto de OUTRA academia,
    absorvido pelo 7100 (`exercise_legacy_id_map`, inativo no catalogo, SEM
    contribuicao propria); o 7108 so tem contribuicao SECONDARY."""
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_f4e', 'x', 'admin')")
        admin_id = cur.lastrowid
        # Fase 6: o motor le o cadastro de macro-grupos (seed da 057, os
        # numeros da v1); cada grupo e associado ao macro-grupo do porte dele.
        macro = semear_macro_grupos(cur)
        for gid, nome, porte, pai in (
            (BICEPS, "Bíceps", "small", None),
            (COSTAS, "Costas — largura dorsal", "large", None),
            (PEITORAL, "Peitoral", "large", None),
            (PEITORAL_SUP, "Peitoral superior", "large", PEITORAL),
            (TRICEPS, "Tríceps", "small", None),
            (CORE, "Core", "unclassified", None),
        ):
            cur.execute(
                "INSERT INTO muscle_groups (id, name, size_class, methodology_parent_group_id, volume_class_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (gid, nome, porte, pai, macro.get(porte)),
            )
        cur.execute("INSERT INTO equipment (id, name) VALUES (%s, 'Banco reto')", (EQUIPAMENTO,))
        for eid, nome, ativo in (
            (SUPINO, "Supino reto", 1),
            (SUPINO_B, "Supino reto (academia B)", 0),
            (INCLINADO, "Supino inclinado", 1),
            (CRUCIFIXO, "Crucifixo", 1),
            (CORDA, "Tríceps corda", 1),
            (SEM_PRIMARY, "Puxada neutra", 1),
            (REMADA, "Remada curvada", 1),
        ):
            cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, %s)", (eid, nome, ativo))
        cur.execute(
            "INSERT INTO exercise_legacy_id_map (old_exercise_id, canonical_exercise_id) VALUES (%s, %s)",
            (SUPINO_B, SUPINO),
        )
        for eid, gid, papel, ativo in (
            (SUPINO, PEITORAL, "PRIMARY", 1),
            (SUPINO, TRICEPS, "SECONDARY", 1),
            (INCLINADO, PEITORAL_SUP, "PRIMARY", 1),
            (CRUCIFIXO, PEITORAL, "PRIMARY", 1),
            (CORDA, TRICEPS, "PRIMARY", 1),
            (CORDA, BICEPS, "PRIMARY", 0),  # inativa: nao conta
            (SEM_PRIMARY, TRICEPS, "SECONDARY", 1),
            (REMADA, COSTAS, "PRIMARY", 1),
        ):
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
                "VALUES (%s, %s, %s, '1.00', '1.00', %s)",
                (eid, gid, papel, ativo),
            )
    raw.commit()
    return {"admin": admin_id}


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _eq(order: int, mapping_id: int, group: int, sets_count: int) -> dict:
    return {
        "item_order": order,
        "item_kind": "equipment",
        "mapping_id": mapping_id,
        "muscle_group_id": group,
        "equipment_id": EQUIPAMENTO,
        "sets_count": sets_count,
    }


# Ciclo de 3 (Push, Pull, descanso). Janela de 7 dias (v3) a partir do Push:
# Push, Pull, descanso, Push, Pull, descanso, Push -> Push 3x, Pull 2x.
# Peitoral (52+12): 4+4+4+3 = 15 series, 3 exercicios distintos (7107 e o
# mesmo canonico do 7100); 3x -> coluna 3x_plus (2-3 / 6-12): exercicios
# dentro, series acima. Triceps: so a corda (7108 nao tem PRIMARY) = 1 ex,
# 3 series; 3x -> dentro (1-2 / 3-8). Costas: 1 ex, 4 series; 2x -> abaixo
# (3-4 / 9-16).
SLOTS_MODO_B = [
    {
        "sequence": 1,
        "name": "Push",
        "slot_type": "normal",
        "items": [
            _eq(1, SUPINO, PEITORAL, 4),
            _eq(2, SUPINO_B, PEITORAL, 4),
            _eq(3, INCLINADO, PEITORAL_SUP, 4),
            _eq(4, CRUCIFIXO, PEITORAL, 3),
            _eq(5, CORDA, TRICEPS, 3),
            _eq(6, SEM_PRIMARY, TRICEPS, 3),
        ],
    },
    {"sequence": 2, "name": "Pull", "slot_type": "normal", "items": [_eq(1, REMADA, COSTAS, 4)]},
    {"sequence": 3, "name": "Descanso", "slot_type": "descanso", "items": []},
]


def _modelo_b(nome: str = "PPL") -> dict:
    return {"name": nome, "volume_mode": "equipment_series", "slots": SLOTS_MODO_B}


def _grupo(corpo: dict, group_id: int) -> dict:
    return next(g for g in corpo["groups"] if g["muscle_group_id"] == group_id)


def _avisos(corpo: dict) -> list[tuple[int, str, str]]:
    return [(a["muscle_group_id"], a["axis"], a["status"]) for a in corpo["methodology_volume_warnings"]]


# Fase 6: o eixo SEMANAL tambem avisa. Na janela: Costas 4 x 2 = 8 (meta
# 18-32), Triceps 3 x 3 = 9 (meta 12-24) e Peitoral 15 x 3 = 45, ACIMA de 32.
AVISOS_ESPERADOS = [
    (COSTAS, "direct_exercises", "BELOW_RANGE"),
    (COSTAS, "direct_sets", "BELOW_RANGE"),
    (COSTAS, "weekly_sets", "BELOW_RANGE"),
    (TRICEPS, "weekly_sets", "BELOW_RANGE"),
    (PEITORAL, "direct_sets", "ABOVE_RANGE"),
    (PEITORAL, "weekly_sets", "ABOVE_RANGE"),
]


# ---------------------------------------------------------------------------
# Modo B: POST anexa avisos; GET traz a leitura inteira com numeros a mao
# ---------------------------------------------------------------------------


def test_post_e_get_modo_b_numeros_a_mao(seed, db_backend):
    api = _client(seed["admin"])

    criado = api.post("/workout-plan-templates", json=_modelo_b())
    assert criado.status_code == 200, (db_backend, criado.text)
    assert _avisos(criado.json()) == AVISOS_ESPERADOS
    template_id = criado.json()["id"]

    resposta = api.get(f"/workout-plan-templates/{template_id}/methodology-volume")
    assert resposta.status_code == 200, (db_backend, resposta.text)
    corpo = resposta.json()
    assert (corpo["template_id"], corpo["template_name"]) == (template_id, "PPL")
    assert (corpo["cycle_days"], corpo["training_sessions"], corpo["frequency_is_normalized"]) == (3, 2, True)
    assert [g["muscle_group_id"] for g in corpo["groups"]] == [BICEPS, COSTAS, TRICEPS, PEITORAL, CORE]

    peitoral = _grupo(corpo, PEITORAL)
    assert peitoral["member_group_ids"] == [PEITORAL_SUP, PEITORAL]
    assert peitoral["sessions"] == [{"slot_sequence": 1, "slot_name": "Push", "direct_sets": 15, "direct_exercises": 3, "technique_equivalent": 0}]
    assert (peitoral["weekly_frequency"], peitoral["frequency_bucket"]) == (3.0, "3x_plus")
    assert (corpo["window_days"], corpo["window_start_sequence"]) == (7, 1)
    assert peitoral["direct_exercises_axis"]["status"] == "WITHIN_RANGE"
    assert peitoral["direct_sets_axis"]["status"] == "ABOVE_RANGE"
    assert (peitoral["direct_sets_axis"]["range_min"], peitoral["direct_sets_axis"]["range_max"]) == (6, 12)
    # Fase 6: faixa lida do banco (DECIMAL -> 6, nao 6.0), macro-grupo, eixo semanal.
    assert peitoral["volume_class_name"] == "Grandes"
    assert (peitoral["weekly_sets_axis"]["value_weekly"], peitoral["weekly_sets_axis"]["status"]) == (45.0, "ABOVE_RANGE")
    assert (peitoral["status"], peitoral["status_conflict"]) == ("ABOVE_RANGE", False)
    assert peitoral["weekly_projection"]["direct_sets_weekly"] == pytest.approx(45.0)

    triceps = _grupo(corpo, TRICEPS)
    assert triceps["sessions"] == [{"slot_sequence": 1, "slot_name": "Push", "direct_sets": 3, "direct_exercises": 1, "technique_equivalent": 0}]
    assert (triceps["direct_sets_axis"]["status"], triceps["direct_sets_axis"]["severity"]) == ("WITHIN_RANGE", None)

    costas = _grupo(corpo, COSTAS)
    assert (costas["direct_sets_axis"]["value_per_session"], costas["direct_sets_axis"]["status"]) == (4.0, "BELOW_RANGE")
    assert _grupo(corpo, BICEPS)["direct_sets_axis"]["status"] == "NOT_DIRECTLY_TRAINED"
    assert _grupo(corpo, CORE)["direct_sets_axis"]["reason"] == "UNCLASSIFIED_GROUP"

    # 7108: item com mapping_id, sem NENHUMA PRIMARY ativa. 7107 NAO entra aqui:
    # herdou as PRIMARY do canonico e tem canonico ativo.
    assert corpo["data_quality"] == {
        "items_without_primary_contribution": 1,
        "items_without_canonical": 0,
        "items_without_mapping": 0,
        "items_without_sets_count": 0,
        "groups_with_unknown_parent": 0,
    }


def test_preview_do_mesmo_payload_bate_com_o_get_do_modelo_salvo(seed, db_backend):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_modelo_b()).json()["id"]
    salvo = api.get(f"/workout-plan-templates/{template_id}/methodology-volume", params={"intent": "cutting"}).json()

    preview = api.post(
        "/workout-plan-templates/methodology-volume-preview",
        json={"slots": SLOTS_MODO_B, "volume_mode": "equipment_series", "intent": "cutting"},
    )
    assert preview.status_code == 200, (db_backend, preview.text)
    salvo.pop("template_id")
    salvo.pop("template_name")
    assert preview.json() == salvo
    # cutting sem alvo: ABAIXO e o esperado em todos -> informational, e o
    # gancho (sempre `standard`) continua avisando: sao leituras distintas.
    assert _grupo(preview.json(), COSTAS)["direct_sets_axis"]["severity"] == "informational"


def test_put_reavalia_e_atualiza_os_avisos(seed, db_backend):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_modelo_b()).json()["id"]

    # Pull passa a ter 3 exercicios de costas com 4 series (12): dentro.
    slots = [dict(s) for s in SLOTS_MODO_B]
    slots[1] = {
        **slots[1],
        "items": [_eq(1, REMADA, COSTAS, 4), _eq(2, REMADA, COSTAS, 4), _eq(3, REMADA, COSTAS, 4)],
    }
    atualizado = api.put(f"/workout-plan-templates/{template_id}", json={**_modelo_b(), "slots": slots})
    assert atualizado.status_code == 200, (db_backend, atualizado.text)
    # 3x a mesma remada = 1 exercicio distinto (12 series): o eixo de series
    # entra na faixa (e o semanal: 12 x 2 na janela = 24 em 18-32), o de
    # exercicios continua abaixo.
    assert _avisos(atualizado.json()) == [
        (COSTAS, "direct_exercises", "BELOW_RANGE"),
        (TRICEPS, "weekly_sets", "BELOW_RANGE"),
        (PEITORAL, "direct_sets", "ABOVE_RANGE"),
        (PEITORAL, "weekly_sets", "ABOVE_RANGE"),
    ]


# ---------------------------------------------------------------------------
# Modo A
# ---------------------------------------------------------------------------


def _modelo_a(itens_por_series: bool) -> dict:
    def item(order: int, group: int, sets_count: int) -> dict:
        base = {"item_order": order, "item_kind": "muscle_group", "muscle_group_id": group}
        if itens_por_series:
            return {**base, "sets_count": sets_count}
        return {**base, "priority_min_pct": 30, "priority_max_pct": 40}

    return {
        "name": "Modo A",
        "volume_mode": "percent_budget",
        "slots": [
            {"sequence": 1, "name": "A", "slot_type": "normal", "items": [item(1, PEITORAL, 12)]},
            {"sequence": 2, "name": "B", "slot_type": "normal", "items": [item(1, COSTAS, 5)]},
            {"sequence": 3, "name": "Descanso", "slot_type": "descanso", "items": []},
        ],
    }


def test_modo_a_por_series_avalia_so_o_eixo_de_series(seed, db_backend):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_modelo_a(itens_por_series=True))
    assert criado.status_code == 200, (db_backend, criado.text)
    # Janela: A 3x, B 2x. Costas 5 < 9 (grande 2x) e, na semana, 5 x 2 = 10 < 18;
    # exercicios nao existem em Modo A, entao nunca viram aviso. Peitoral 12
    # por sessao fica dentro de 6-12 (3x_plus), mas 12 x 3 = 36 passa de 32.
    assert _avisos(criado.json()) == [
        (COSTAS, "direct_sets", "BELOW_RANGE"),
        (COSTAS, "weekly_sets", "BELOW_RANGE"),
        (PEITORAL, "weekly_sets", "ABOVE_RANGE"),
    ]

    corpo = api.get(f"/workout-plan-templates/{criado.json()['id']}/methodology-volume").json()
    peitoral = _grupo(corpo, PEITORAL)
    assert peitoral["direct_exercises_axis"]["reason"] == "MODE_A_NO_EXERCISES"
    assert (peitoral["direct_sets_axis"]["value_per_session"], peitoral["direct_sets_axis"]["status"]) == (12.0, "WITHIN_RANGE")


def test_modo_a_so_percentual_nao_e_avaliavel_e_nao_avisa(seed, db_backend):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_modelo_a(itens_por_series=False))
    assert criado.status_code == 200, (db_backend, criado.text)
    assert criado.json()["methodology_volume_warnings"] == []

    corpo = api.get(f"/workout-plan-templates/{criado.json()['id']}/methodology-volume").json()
    # A coluna `sets_count` gravou o default 3, mas o item e governado pelo
    # percentual: o servico entrega `sets_count=None` ao motor.
    assert _grupo(corpo, PEITORAL)["direct_sets_axis"]["reason"] == "MODE_A_PERCENT_ONLY"
    assert _grupo(corpo, PEITORAL)["direct_exercises_axis"]["reason"] == "MODE_A_PERCENT_ONLY"
    assert corpo["data_quality"]["items_without_sets_count"] == 2


# ---------------------------------------------------------------------------
# O gancho NUNCA bloqueia o CRUD
# ---------------------------------------------------------------------------


def test_excecao_no_avaliador_nao_bloqueia_post_nem_put(seed, db_backend, monkeypatch):
    api = _client(seed["admin"])

    def explode(*_args, **_kwargs):
        raise RuntimeError("avaliador fora do ar")

    monkeypatch.setattr(svc, "evaluate_template", explode)

    criado = api.post("/workout-plan-templates", json=_modelo_b("Sem avaliacao"))
    assert criado.status_code == 200, (db_backend, criado.text)
    assert criado.json()["methodology_volume_warnings"] == []
    assert criado.json()["name"] == "Sem avaliacao"
    template_id = criado.json()["id"]

    atualizado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo_b("Ainda sem avaliacao"))
    assert atualizado.status_code == 200, (db_backend, atualizado.text)
    assert atualizado.json()["methodology_volume_warnings"] == []
    assert atualizado.json()["name"] == "Ainda sem avaliacao"

    # O Modelo foi mesmo gravado: o GET normal continua respondendo.
    assert api.get(f"/workout-plan-templates/{template_id}").status_code == 200


# ---------------------------------------------------------------------------
# Erros no caminho real do router
# ---------------------------------------------------------------------------


def test_get_de_modelo_inexistente_e_404(seed, db_backend):
    resposta = _client(seed["admin"]).get("/workout-plan-templates/424242/methodology-volume")
    assert resposta.status_code == 404, db_backend


def test_intent_invalida_e_422_nas_duas_rotas(seed, db_backend):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_modelo_b()).json()["id"]
    assert api.get(f"/workout-plan-templates/{template_id}/methodology-volume", params={"intent": "bulking"}).status_code == 422
    assert (
        api.post(
            "/workout-plan-templates/methodology-volume-preview",
            json={"slots": SLOTS_MODO_B, "volume_mode": "equipment_series", "intent": "bulking"},
        ).status_code
        == 422
    )
