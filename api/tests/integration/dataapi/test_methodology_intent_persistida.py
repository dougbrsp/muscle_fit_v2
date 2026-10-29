"""Fase 4, PR H - intencao declarada PERSISTIDA por Modelo (migracao 056)
contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API).

O que so o banco prova:

- `PUT`/`GET /{id}/methodology-intent`: grava, le, substitui por inteiro
  (alvos anteriores somem), `standard` sem declaracao; os 422 (specialization
  sem alvo, alvo com outra intencao, alvo inexistente, intent fora do
  dominio) e o 404;
- `DELETE` do Modelo e exclusao LOGICA desde a 065: a intencao e os alvos
  ficam (historico), e o Modelo some das rotas (404);
- `duplicate` copia intencao e alvos;
- `GET /{id}/methodology-volume` SEM `intent` na query usa a persistida; COM
  `intent` ignora a persistida (precedencia query > persistida > standard);
- o gancho do PUT do Modelo usa a persistida: um Modelo de cutting deixa de
  acusar `warning` no que esta abaixo da faixa (vira `informational`, que o
  gancho nao lista);
- `GET /{id}` do Modelo traz `methodology_intent` (aditivo).

Isolamento: `truncar(raw)` com `isolamento_v3.TABELAS_V3` (as duas tabelas da
056 estao la). Sem relogio: nada aqui deriva de `now()`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support.isolamento_v3 import truncar
from tests.support.methodology_paridade import semear_macro_grupos

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

PEITORAL, COSTAS, TRICEPS = 52, 6, 14
SUPINO, INCLINADO, CRUCIFIXO, CORDA, REMADA = 7100, 7101, 7110, 7106, 7102
EQUIPAMENTO = 9001
INTENT_TABLE = "workout_plan_template_methodology_intent"
TARGETS_TABLE = "workout_plan_template_methodology_intent_targets"


@pytest.fixture
def seed(raw):
    """Um admin; tres grupos; cinco exercicios com PRIMARY. Ciclo de 3 dias
    (Push, Pull, descanso): Peitoral dentro da faixa, Triceps e Costas abaixo
    - e o que a intencao muda de severidade."""
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_f4h', 'x', 'admin')")
        admin_id = cur.lastrowid
        # Fase 6: o motor le o cadastro de macro-grupos (seed da 057).
        macro = semear_macro_grupos(cur)
        for gid, nome, porte in ((COSTAS, "Costas — largura dorsal", "large"), (PEITORAL, "Peitoral", "large"), (TRICEPS, "Tríceps", "small")):
            cur.execute(
                "INSERT INTO muscle_groups (id, name, size_class, volume_class_id) VALUES (%s, %s, %s, %s)",
                (gid, nome, porte, macro[porte]),
            )
        cur.execute("INSERT INTO equipment (id, name) VALUES (%s, 'Banco reto')", (EQUIPAMENTO,))
        for eid, nome, gid in (
            (SUPINO, "Supino reto", PEITORAL),
            (INCLINADO, "Supino inclinado", PEITORAL),
            (CRUCIFIXO, "Crucifixo", PEITORAL),
            (CORDA, "Tríceps corda", TRICEPS),
            (REMADA, "Remada curvada", COSTAS),
        ):
            cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (eid, nome))
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
                "VALUES (%s, %s, 'PRIMARY', '1.00', '1.00', 1)",
                (eid, gid),
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


# Peitoral: 3 ex / 12 series (dentro de 3-4 / 9-16 na coluna 2x). Triceps: 1
# ex / 3 series (abaixo de 2-3 / 6-12). Costas: 1 ex / 4 series (abaixo).
SLOTS = [
    {
        "sequence": 1,
        "name": "Push",
        "slot_type": "normal",
        "items": [
            _eq(1, SUPINO, PEITORAL, 4),
            _eq(2, INCLINADO, PEITORAL, 4),
            _eq(3, CRUCIFIXO, PEITORAL, 4),
            _eq(4, CORDA, TRICEPS, 3),
        ],
    },
    {"sequence": 2, "name": "Pull", "slot_type": "normal", "items": [_eq(1, REMADA, COSTAS, 4)]},
    *({"sequence": seq, "name": "Descanso", "slot_type": "descanso", "items": []} for seq in range(3, 8)),
]
MODELO = {"name": "PPL", "volume_mode": "equipment_series", "slots": SLOTS}
# Ciclo de 7 slots (Fase 7: a janela de 7 dias cobre cada slot uma vez, sem
# repetir o Push): cada grupo 1x na semana e TUDO abaixo, por sessao e na
# meta semanal - Peitoral 3 ex / 12 series (grande 1x: 4-5 / 15-20; semana
# 12 < 18), Triceps 1 / 3 (pequeno 1x: 3-4 / 9-16; semana 3 < 12), Costas
# 1 / 4 (grande 1x; semana 4 < 18). Por isso cutting silencia tudo.
AVISOS_STANDARD = [
    (gid, axis, "BELOW_RANGE")
    for gid in (COSTAS, TRICEPS, PEITORAL)
    for axis in ("direct_exercises", "direct_sets", "weekly_sets")
]


def _avisos(corpo: dict) -> list[tuple[int, str, str]]:
    return [(a["muscle_group_id"], a["axis"], a["status"]) for a in corpo["methodology_volume_warnings"]]


def _grupo(corpo: dict, group_id: int) -> dict:
    return next(g for g in corpo["groups"] if g["muscle_group_id"] == group_id)


def _linhas(raw, template_id: int) -> tuple[list[str], list[int]]:
    with raw.cursor() as cur:
        cur.execute(f"SELECT intent FROM {INTENT_TABLE} WHERE template_id = %s", (template_id,))
        intents = [str(r["intent"]) for r in (cur.fetchall() or [])]
        cur.execute(
            f"SELECT muscle_group_id FROM {TARGETS_TABLE} WHERE template_id = %s ORDER BY muscle_group_id", (template_id,)
        )
        alvos = [int(r["muscle_group_id"]) for r in (cur.fetchall() or [])]
    return intents, alvos


def _criar(api: TestClient, db_backend: str) -> int:
    criado = api.post("/workout-plan-templates", json=MODELO)
    assert criado.status_code == 200, (db_backend, criado.text)
    return criado.json()["id"]


# ---------------------------------------------------------------------------
# PUT / GET da intencao
# ---------------------------------------------------------------------------


def test_sem_declaracao_e_standard_no_get_da_intencao_e_no_modelo(seed, db_backend):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)

    intencao = api.get(f"/workout-plan-templates/{template_id}/methodology-intent")
    assert intencao.status_code == 200, (db_backend, intencao.text)
    assert intencao.json() == {"template_id": template_id, "intent": "standard", "target_group_ids": []}

    modelo = api.get(f"/workout-plan-templates/{template_id}").json()
    assert modelo["methodology_intent"] == {"intent": "standard", "target_group_ids": []}
    assert modelo["name"] == "PPL" and len(modelo["slots"]) == 7  # o resto do contrato segue


def test_put_grava_le_e_substitui_por_inteiro(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)

    gravado = api.put(
        f"/workout-plan-templates/{template_id}/methodology-intent",
        json={"intent": "specialization", "target_group_ids": [TRICEPS, PEITORAL, TRICEPS]},
    )
    assert gravado.status_code == 200, (db_backend, gravado.text)
    # De-duplicado e ordenado.
    assert gravado.json() == {"template_id": template_id, "intent": "specialization", "target_group_ids": [TRICEPS, PEITORAL]}
    assert api.get(f"/workout-plan-templates/{template_id}/methodology-intent").json() == gravado.json()
    assert api.get(f"/workout-plan-templates/{template_id}").json()["methodology_intent"] == {
        "intent": "specialization",
        "target_group_ids": [TRICEPS, PEITORAL],
    }
    assert _linhas(raw, template_id) == (["specialization"], [TRICEPS, PEITORAL])

    # Troca para cutting: UMA linha de intencao (PK = template_id) e nenhum alvo.
    trocado = api.put(f"/workout-plan-templates/{template_id}/methodology-intent", json={"intent": "cutting"})
    assert trocado.status_code == 200, (db_backend, trocado.text)
    assert trocado.json() == {"template_id": template_id, "intent": "cutting", "target_group_ids": []}
    assert _linhas(raw, template_id) == (["cutting"], [])


@pytest.mark.parametrize(
    "body, trecho",
    [
        ({"intent": "specialization"}, "grupo-alvo"),
        ({"intent": "specialization", "target_group_ids": []}, "grupo-alvo"),
        ({"intent": "cutting", "target_group_ids": [TRICEPS]}, "specialization"),
        ({"intent": "maintenance", "target_group_ids": [TRICEPS]}, "specialization"),
        ({"intent": "specialization", "target_group_ids": [TRICEPS, 999]}, "999"),
    ],
)
def test_regras_cruzadas_sao_422_e_nao_gravam(seed, db_backend, raw, body, trecho):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    resposta = api.put(f"/workout-plan-templates/{template_id}/methodology-intent", json=body)
    assert resposta.status_code == 422, (db_backend, resposta.text)
    assert trecho in resposta.json()["detail"]
    assert _linhas(raw, template_id) == ([], [])


def test_intent_fora_do_dominio_e_422_e_modelo_inexistente_e_404(seed, db_backend):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    assert api.put(f"/workout-plan-templates/{template_id}/methodology-intent", json={"intent": "bulking"}).status_code == 422
    assert api.put("/workout-plan-templates/424242/methodology-intent", json={"intent": "cutting"}).status_code == 404
    assert api.get("/workout-plan-templates/424242/methodology-intent").status_code == 404


# ---------------------------------------------------------------------------
# Ciclo de vida do Modelo: cascade e duplicate
# ---------------------------------------------------------------------------


def test_excluir_o_modelo_preserva_intencao_e_alvos_e_esconde_o_modelo(seed, db_backend, raw):
    """065: exclusao logica. O CASCADE da 056 so age no DELETE fisico, que a
    rota deixou de fazer - a intencao fica com o historico do Modelo."""
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(
        f"/workout-plan-templates/{template_id}/methodology-intent",
        json={"intent": "specialization", "target_group_ids": [PEITORAL]},
    )
    assert _linhas(raw, template_id) == (["specialization"], [PEITORAL])

    assert api.delete(f"/workout-plan-templates/{template_id}").status_code == 200, db_backend
    assert _linhas(raw, template_id) == (["specialization"], [PEITORAL])
    assert api.get(f"/workout-plan-templates/{template_id}/methodology-intent").status_code == 404
    assert api.get(f"/workout-plan-templates/{template_id}").status_code == 404


def test_duplicate_copia_intencao_e_alvos(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(
        f"/workout-plan-templates/{template_id}/methodology-intent",
        json={"intent": "specialization", "target_group_ids": [COSTAS, TRICEPS]},
    )

    copia = api.post(f"/workout-plan-templates/{template_id}/duplicate")
    assert copia.status_code == 200, (db_backend, copia.text)
    novo_id = copia.json()["id"]
    assert novo_id != template_id
    assert copia.json()["methodology_intent"] == {"intent": "specialization", "target_group_ids": [COSTAS, TRICEPS]}
    assert _linhas(raw, novo_id) == (["specialization"], [COSTAS, TRICEPS])
    # O original continua como estava.
    assert _linhas(raw, template_id) == (["specialization"], [COSTAS, TRICEPS])


def test_duplicate_de_modelo_sem_declaracao_nasce_standard(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    copia = api.post(f"/workout-plan-templates/{template_id}/duplicate")
    assert copia.status_code == 200, (db_backend, copia.text)
    assert copia.json()["methodology_intent"] == {"intent": "standard", "target_group_ids": []}
    assert _linhas(raw, copia.json()["id"]) == ([], [])


# ---------------------------------------------------------------------------
# Precedencia: query > persistida > standard
# ---------------------------------------------------------------------------


def test_get_methodology_volume_sem_query_usa_a_persistida_e_com_query_a_ignora(seed, db_backend):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    url = f"/workout-plan-templates/{template_id}/methodology-volume"

    # Sem declaracao: standard -> triceps abaixo e warning.
    antes = api.get(url).json()
    assert antes["intent"] == {"kind": "standard", "target_group_ids": []}
    assert _grupo(antes, TRICEPS)["direct_sets_axis"]["severity"] == "warning"

    api.put(
        f"/workout-plan-templates/{template_id}/methodology-intent",
        json={"intent": "specialization", "target_group_ids": [PEITORAL]},
    )

    # Sem query: a persistida. Peitoral e alvo; triceps (nao alvo, "em
    # manutencao") abaixo e o esperado -> informational; status nao muda.
    persistida = api.get(url).json()
    assert persistida["intent"] == {"kind": "specialization", "target_group_ids": [PEITORAL]}
    assert _grupo(persistida, PEITORAL)["is_intent_target"] is True
    assert (
        _grupo(persistida, TRICEPS)["direct_sets_axis"]["status"],
        _grupo(persistida, TRICEPS)["direct_sets_axis"]["severity"],
    ) == ("BELOW_RANGE", "informational")

    # Com query: a query vence por inteiro - inclusive os alvos gravados.
    explicita = api.get(url, params={"intent": "standard"}).json()
    assert explicita["intent"] == {"kind": "standard", "target_group_ids": []}
    assert _grupo(explicita, PEITORAL)["is_intent_target"] is False
    assert _grupo(explicita, TRICEPS)["direct_sets_axis"]["severity"] == "warning"

    outra = api.get(url, params={"intent": "specialization", "target_group_ids": [TRICEPS]}).json()
    assert outra["intent"] == {"kind": "specialization", "target_group_ids": [TRICEPS]}
    assert _grupo(outra, TRICEPS)["direct_sets_axis"]["severity"] == "warning"  # alvo ABAIXO: warning cheio


def test_gancho_do_put_do_modelo_usa_a_persistida(seed, db_backend):
    """Cutting declarado: ABAIXO da faixa e o esperado em todos -> a
    severidade vira `informational` e o gancho (so `warning`) deixa de
    listar. Antes da declaracao os mesmos numeros eram avisos."""
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=MODELO)
    assert _avisos(criado.json()) == AVISOS_STANDARD  # POST: ainda nao ha intencao gravada
    template_id = criado.json()["id"]

    ainda_standard = api.put(f"/workout-plan-templates/{template_id}", json=MODELO)
    assert ainda_standard.status_code == 200, (db_backend, ainda_standard.text)
    assert _avisos(ainda_standard.json()) == AVISOS_STANDARD

    api.put(f"/workout-plan-templates/{template_id}/methodology-intent", json={"intent": "cutting"})

    com_cutting = api.put(f"/workout-plan-templates/{template_id}", json=MODELO)
    assert com_cutting.status_code == 200, (db_backend, com_cutting.text)
    assert com_cutting.json()["methodology_volume_warnings"] == []
    assert com_cutting.json()["methodology_intent"] == {"intent": "cutting", "target_group_ids": []}
    # O status continua honesto na leitura completa: so a severidade mudou.
    leitura = api.get(f"/workout-plan-templates/{template_id}/methodology-volume").json()
    assert (
        _grupo(leitura, TRICEPS)["direct_sets_axis"]["status"],
        _grupo(leitura, TRICEPS)["direct_sets_axis"]["severity"],
    ) == ("BELOW_RANGE", "informational")

    # O PUT do Modelo nao mexe na intencao declarada.
    assert api.get(f"/workout-plan-templates/{template_id}/methodology-intent").json()["intent"] == "cutting"
