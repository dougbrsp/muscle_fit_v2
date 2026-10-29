"""GET /me/training-preferences (tela "Preferencias de Treino", 2026-10-13)
sem banco: contrato do item, academia padrao (ficha mais recente), 404 de
academia inexistente, lista vazia sem academia, usuario corrente SEMPRE nos
parametros, so leitura, e 503 com a tabela da 035 ausente.

O SQL de verdade (joins, filtro de `gym_equipment` ativo) roda no CI de
integracao; aqui o cursor e roteirizado por trecho do SQL.

Rodar: cd api && python -m pytest tests/unit/test_training_preferences_unit.py
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import equipment_exercise_preferences as prefs
from app.domain import equipment_ranking
from app.equipment_recommender import PREFERENCES_TABLE, PUBLIC_ASSETS_DOMAIN
from app.security import Identity, get_current_identity
from tests.support.fake_db import NullContext, fake_transaction

USER_ID = 42

ROW_SEM_NOTA = {
    "equipment_exercise_id": 7,
    "exercise_id": 101,
    "exercise_name": "Supino reto",
    "muscle_group_id": 3,
    "primary_group": "Peitoral",
    "equipment_id": 11,
    "equipment_name": "Supino articulado",
    "photo_url": None,
    "stars": None,
}
ROW_COM_NOTA = {
    **ROW_SEM_NOTA,
    "equipment_exercise_id": 8,
    "equipment_id": 12,
    "equipment_name": "Barra livre",
    "photo_url": f"https://{PUBLIC_ASSETS_DOMAIN}/equipment/12.jpg",
    "stars": 5,
}
ROW_SEM_GRUPO = {**ROW_SEM_NOTA, "equipment_exercise_id": 9, "muscle_group_id": None, "primary_group": None}


class _Cursor(NullContext):
    """Roteiriza por trecho do SQL: ficha mais recente, academia, pares."""

    def __init__(self, *, default_gym: int | None = 5, gym_exists: bool = True, pairs=None, error=None):
        self.default_gym = default_gym
        self.gym_exists = gym_exists
        self.pairs = list(pairs if pairs is not None else [ROW_SEM_NOTA, ROW_COM_NOTA])
        self.error = error
        self.executed: list[tuple[str, Any]] = []
        self._result: list[dict[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        if "FROM workout_plans" in sql:
            self._result = [] if self.default_gym is None else [{"gym_id": self.default_gym}]
        elif "FROM gyms" in sql:
            self._result = [{"id": params[0]}] if self.gym_exists else []
        elif "FROM gym_equipment" in sql:
            if self.error is not None:
                raise self.error
            self._result = self.pairs
        else:  # pragma: no cover - SQL inesperado e falha do teste
            raise AssertionError(f"SQL inesperado: {sql}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


def _client(monkeypatch, cur: _Cursor) -> TestClient:
    monkeypatch.setattr(prefs, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(prefs.training_preferences_router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=USER_ID, role="regular", source="session"
    )
    return TestClient(app)


def test_contrato_do_item_e_nota_nula_sem_avaliacao(monkeypatch):
    cur = _Cursor()
    body = _client(monkeypatch, cur).get("/me/training-preferences?gym_id=9").json()
    assert body["ok"] is True
    assert body["gym_id"] == 9
    assert body["default_gym_id"] == 5
    assert body["default_stars"] == equipment_ranking.DEFAULT_STARS
    assert body["items"] == [
        {
            "equipment_exercise_id": 7,
            "exercise_id": 101,
            "exercise_name": "Supino reto",
            "muscle_group_id": 3,
            "primary_group": "Peitoral",
            "equipment_id": 11,
            "equipment_name": "Supino articulado",
            "photo_url": None,
            "stars": None,
        },
        {
            "equipment_exercise_id": 8,
            "exercise_id": 101,
            "exercise_name": "Supino reto",
            "muscle_group_id": 3,
            "primary_group": "Peitoral",
            "equipment_id": 12,
            "equipment_name": "Barra livre",
            "photo_url": f"https://{PUBLIC_ASSETS_DOMAIN}/equipment/12.jpg",
            "stars": 5,
        },
    ]


def test_sem_gym_id_usa_a_academia_da_ficha_mais_recente(monkeypatch):
    cur = _Cursor(default_gym=5)
    body = _client(monkeypatch, cur).get("/me/training-preferences").json()
    assert body["gym_id"] == 5
    assert body["default_gym_id"] == 5
    pares = [params for sql, params in cur.executed if "FROM gym_equipment" in sql]
    assert pares == [(USER_ID, 5)]


def test_sem_gym_id_e_sem_ficha_devolve_lista_vazia(monkeypatch):
    cur = _Cursor(default_gym=None)
    body = _client(monkeypatch, cur).get("/me/training-preferences").json()
    assert body == {"ok": True, "gym_id": None, "default_gym_id": None, "default_stars": 3, "items": []}
    assert not any("FROM gym_equipment" in sql for sql, _ in cur.executed)


def test_academia_inexistente_responde_404(monkeypatch):
    response = _client(monkeypatch, _Cursor(gym_exists=False)).get("/me/training-preferences?gym_id=999")
    assert response.status_code == 404


@pytest.mark.parametrize("valor", ["0", "-1", "abc"])
def test_gym_id_invalido_responde_422(monkeypatch, valor):
    response = _client(monkeypatch, _Cursor()).get(f"/me/training-preferences?gym_id={valor}")
    assert response.status_code == 422


def test_usuario_vem_da_identidade_e_nunca_da_query(monkeypatch):
    cur = _Cursor()
    _client(monkeypatch, cur).get("/me/training-preferences?gym_id=9&user_id=1")
    default_sql = [params for sql, params in cur.executed if "FROM workout_plans" in sql]
    pares = [params for sql, params in cur.executed if "FROM gym_equipment" in sql]
    assert default_sql == [(USER_ID,)]
    assert pares == [(USER_ID, 9)]


def test_exercicio_sem_grupo_primario_continua_na_lista(monkeypatch):
    cur = _Cursor(pairs=[ROW_SEM_GRUPO])
    item = _client(monkeypatch, cur).get("/me/training-preferences?gym_id=9").json()["items"][0]
    assert item["primary_group"] is None and item["muscle_group_id"] is None


def test_so_leitura(monkeypatch):
    cur = _Cursor()
    _client(monkeypatch, cur).get("/me/training-preferences?gym_id=9")
    for sql, _ in cur.executed:
        assert sql.lstrip().upper().startswith("SELECT"), sql


def test_sql_projeta_o_universo_do_recomendador_e_a_nota_do_usuario():
    sql = prefs._TRAINING_PREFERENCES_SQL
    assert "ge.gym_id = %s AND ge.is_active = 1" in sql
    assert "ee.equipment_id = ge.equipment_id AND ee.exercise_id = ge.mapping_id" in sql
    assert f"LEFT JOIN {PREFERENCES_TABLE} p ON p.equipment_exercise_id = ee.id AND p.user_id = %s" in sql
    assert f"CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path)" in sql
    # Nenhuma regra de ranking/neutro no SQL: a nota sai crua (null = sem avaliacao).
    assert "COALESCE(p.stars" not in sql
    assert "ORDER BY p.stars" not in sql


def test_tabela_da_035_ausente_responde_503(monkeypatch):
    erro = RuntimeError(f"(1146, \"Table 'x.{PREFERENCES_TABLE}' doesn't exist\")")
    response = _client(monkeypatch, _Cursor(error=erro)).get("/me/training-preferences?gym_id=9")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "equipment_exercise_preferences_unavailable"


def test_rota_registrada_no_app_principal():
    from app.main import app

    # `app.routes` guarda routers incluidos de forma preguicosa nesta versao do
    # FastAPI; o OpenAPI enxerga o path final.
    assert "get" in app.openapi()["paths"]["/me/training-preferences"]
