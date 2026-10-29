"""Cards ilustrados no Modo Treino (PR5) contra MySQL real, nos DOIS backends
(PyMySQL de DEV e o shim da RDS Data API de producao).

O que so o banco prova - o caminho quente do treino:

1. `services.get_workout_plan` devolve `exercise_card` por exercicio
   (`{url, thumbnail_url, version}`) quando o catalogo tem `card_version`, e
   `None` quando nao tem, sem vazar as colunas auxiliares do SELECT;
2. item da ficha gravado com id LEGADO (`exercise_legacy_id_map`) recebe o
   card do exercicio CANONICO - nunca o do id cru, que continua existindo no
   catalogo (em producao, 2026-10-03: as 93 linhas do mapa ainda estao em
   `exercise_catalog`);
3. item sem `mapping_id` (texto livre) -> `None`;
4. o briefing (`GET .../briefing`) devolve o mesmo campo no bloco `exercise`.

As URLs saem de `public_assets` (base trocada por env no teste, para provar
que nada monta URL com literal de dominio).

Isolamento: `isolamento_v3.truncar` (fichas, catalogo, mapa legado, usuarios,
historico). Sem relogio congelado: nada aqui depende do instante.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_dataapi` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import exercise_briefing, services
from app.security import Identity, get_current_identity
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

BASE = "https://cdn.cards-teste.example"
SUPINO = 42  # card publicado (v3)
AGACHAMENTO = 43  # sem card
SUPINO_LEGADO = 900  # id antigo fundido no SUPINO; a linha continua no catalogo
DESCRICAO = "Preparação:\n- Ajuste o banco\nExecução:\n- Empurre"

CARD_SUPINO = {
    "url": f"{BASE}/exercise-cards/{SUPINO}/v3/card.webp",
    "thumbnail_url": f"{BASE}/exercise-cards/{SUPINO}/v3/thumb.webp",
    "version": 3,
}


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('aluno_card_treino', 'x', 'regular')")
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_catalog (id, name, is_active, execution_description, card_generation_id, "
            "card_version, card_published_at) VALUES (%s, %s, 1, %s, 7, 3, %s)",
            (SUPINO, "Supino reto com barra", DESCRICAO, datetime(2026, 10, 3, 12, 0, 0)),
        )
        cur.execute(
            "INSERT INTO exercise_catalog (id, name, is_active, execution_description) VALUES (%s, %s, 1, %s)",
            (AGACHAMENTO, "Agachamento livre", DESCRICAO),
        )
        # Linha legada SEM card: se a leitura usasse o id cru, o item 3 sairia
        # `None` - o teste so passa resolvendo para o canonico.
        cur.execute(
            "INSERT INTO exercise_catalog (id, name, is_active, execution_description) VALUES (%s, %s, 0, %s)",
            (SUPINO_LEGADO, "Supino reto (legado)", DESCRICAO),
        )
        cur.execute(
            "INSERT INTO exercise_legacy_id_map (old_exercise_id, canonical_exercise_id) VALUES (%s, %s)",
            (SUPINO_LEGADO, SUPINO),
        )
        cur.execute(
            "INSERT INTO workout_plans (user_id, name, status) VALUES (%s, 'Ficha cards', 'planned')", (user_id,)
        )
        plan_id = cur.lastrowid
        wpe = {}
        for sequence, name, mapping_id in (
            (1, "Supino reto com barra", SUPINO),
            (2, "Agachamento livre", AGACHAMENTO),
            (3, "Supino reto (ficha antiga)", SUPINO_LEGADO),
            (4, "Prancha (texto livre)", None),
        ):
            cur.execute(
                "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, mapping_id, sets_count) "
                "VALUES (%s, %s, %s, %s, 3)",
                (plan_id, sequence, name, mapping_id),
            )
            wpe[sequence] = cur.lastrowid
    raw.commit()
    return {"user_id": user_id, "plan_id": plan_id, "wpe": wpe}


@pytest.fixture
def base_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_ASSETS_BASE_URL", BASE + "/")
    return BASE


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(exercise_briefing.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app)


def test_ficha_entrega_card_do_exercicio_canonico(db_backend, seed, base_url):
    plan = services.get_workout_plan(seed["plan_id"], user_id=seed["user_id"])
    by_sequence = {ex["sequence"]: ex for ex in plan["exercises"]}

    assert by_sequence[1]["exercise_card"] == CARD_SUPINO
    assert by_sequence[2]["exercise_card"] is None
    # id legado -> card do canonico (e nao o da linha legada, que nao tem).
    assert by_sequence[3]["exercise_card"] == CARD_SUPINO
    assert by_sequence[4]["exercise_card"] is None

    for ex in plan["exercises"]:
        # As colunas auxiliares do SELECT nao vazam para o payload.
        assert "card_version" not in ex and "card_exercise_id" not in ex
        # Campo ja existente continua igual (mesmo COALESCE do legado).
        assert ex["exercise_description"] is None


def test_briefing_entrega_o_mesmo_card(db_backend, seed, base_url):
    api = _client(seed["user_id"])

    def briefing(sequence: int) -> dict:
        response = api.get(f"/workout-plans/{seed['plan_id']}/exercises/{seed['wpe'][sequence]}/briefing")
        assert response.status_code == 200, response.text
        return response.json()["exercise"]

    supino = briefing(1)
    assert supino["id"] == SUPINO
    assert supino["exercise_card"] == CARD_SUPINO

    assert briefing(2)["exercise_card"] is None

    legado = briefing(3)
    assert legado["id"] == SUPINO
    assert legado["exercise_card"] == CARD_SUPINO

    texto_livre = briefing(4)
    assert texto_livre["id"] is None
    assert texto_livre["exercise_card"] is None


def test_sem_card_publicado_em_lugar_nenhum_tudo_e_none(db_backend, seed, raw):
    """Estado de producao ate a flag ser ligada: nenhum `card_version` - o
    payload ganha so `exercise_card: null` em cada item."""
    with raw.cursor() as cur:
        cur.execute("UPDATE exercise_catalog SET card_generation_id = NULL, card_version = NULL, card_published_at = NULL")
    raw.commit()

    plan = services.get_workout_plan(seed["plan_id"], user_id=seed["user_id"])
    assert [ex["exercise_card"] for ex in plan["exercises"]] == [None, None, None, None]
