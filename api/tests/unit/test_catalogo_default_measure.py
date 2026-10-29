"""`exercise_catalog.default_measure` (migração 089; Lote T2): sugestão de
medida da série do exercício para o editor do Modelo (T4).

- `/exercises` (admin) lê pelo `SELECT *` e grava o campo só quando ele vem no
  payload, validado pelo vocabulário do dono (`set_measures.parse_measure`);
  vazio/None = NULL (sem sugestão); desconhecido = 400.
- `GET /equipment-exercises` devolve `default_measure` em cada par."""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app import equipment_exercises as ee
from app import exercise_catalog_admin as eca
from app.domain.set_measures import SetMeasure


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("null", None),
        (SetMeasure.DURATION.value, SetMeasure.DURATION.value),
        (" MAX_REPS ", SetMeasure.MAX_REPS.value),
        (SetMeasure.REPS.value, SetMeasure.REPS.value),
    ],
)
def test_default_measure_canonico_ou_nulo(valor, esperado):
    assert eca.clean_default_measure(valor) == esperado


def test_default_measure_desconhecido_e_400():
    with pytest.raises(HTTPException) as exc_info:
        eca.clean_default_measure("minutos")
    assert exc_info.value.status_code == 400


class _Cursor:
    def __init__(self, calls):
        self.calls = calls
        self.lastrowid = 55
        self._row = {"id": 55, "name": "Prancha", "is_active": 1, "is_unilateral": 0, "is_compound": None}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return dict(self._row)


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self.calls)


@pytest.fixture
def chamadas(monkeypatch):
    calls: list = []
    monkeypatch.setattr(eca, "_connect", lambda: _Conn(calls))
    monkeypatch.setattr(eca, "_require_admin_identity", lambda *a, **k: type("I", (), {"user_id": 1})())
    monkeypatch.setattr(eca, "reclassify_after_catalog_change", lambda source: None)
    return calls


def _updates_de_medida(calls):
    return [params for sql, params in calls if sql.startswith("UPDATE exercise_catalog SET default_measure")]


def test_put_sem_o_campo_nao_toca_a_coluna(chamadas):
    eca.update_exercise(55, item={"name": "Prancha"}, x_api_token="t", musclefit_session=None)
    assert _updates_de_medida(chamadas) == []


def test_put_grava_a_medida_canonica_e_limpa_com_vazio(chamadas):
    eca.update_exercise(55, item={"default_measure": " Duration "}, x_api_token="t", musclefit_session=None)
    eca.update_exercise(55, item={"default_measure": ""}, x_api_token="t", musclefit_session=None)
    assert _updates_de_medida(chamadas) == [(SetMeasure.DURATION.value, 55), (None, 55)]


def test_post_grava_a_medida_depois_do_insert(chamadas):
    eca.create_exercise(item={"name": "Prancha", "default_measure": SetMeasure.DURATION.value}, x_api_token="t")
    assert _updates_de_medida(chamadas) == [(SetMeasure.DURATION.value, 55)]


def test_post_e_put_com_medida_desconhecida_nao_escrevem_nada(chamadas):
    with pytest.raises(HTTPException):
        eca.create_exercise(item={"name": "Prancha", "default_measure": "minutos"}, x_api_token="t")
    with pytest.raises(HTTPException):
        eca.update_exercise(55, item={"default_measure": "minutos"}, x_api_token="t", musclefit_session=None)
    assert chamadas == []


def test_listagem_de_pares_devolve_a_medida_sugerida():
    fonte = inspect.getsource(ee.list_equipment_exercises)
    assert "ec.default_measure AS default_measure" in fonte
