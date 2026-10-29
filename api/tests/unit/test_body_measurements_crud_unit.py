"""CRUD `/body-measurements` (F1.4) sem banco: validacao do payload,
tolerancia a tabela ausente (janela de deploy da 047) e cache SO DO POSITIVO
da deteccao da tabela (licao do PR #182).

Rodar: cd api && python -m pytest tests/unit/test_body_measurements_crud_unit.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import body_measurements as bm
from app import body_measurements_schema as schema
from app.security import Identity, get_current_identity


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------


def test_payload_valido_normaliza_local_e_arredonda_a_decima():
    item = bm.BodyMeasurementIn(local_date="2026-09-22", site=" Waist ", value_cm=84.56)
    assert item.site == "waist"
    assert item.value_cm == Decimal("84.6")
    assert item.protocol_ok is True
    assert item.source == "manual"


@pytest.mark.parametrize("site", ["belly", "", "waist_l"])
def test_site_fora_do_vocabulario_e_rejeitado(site):
    with pytest.raises(ValidationError):
        bm.BodyMeasurementIn(local_date="2026-09-22", site=site, value_cm=80)


@pytest.mark.parametrize("valor", [9.9, 300.1, -5, 0])
def test_value_cm_fora_de_10_a_300_e_rejeitado(valor):
    with pytest.raises(ValidationError):
        bm.BodyMeasurementIn(local_date="2026-09-22", site="waist", value_cm=valor)


# ---------------------------------------------------------------------------
# local_date: piso 2000-01-01, teto hoje (UTC) + 1 dia
# ---------------------------------------------------------------------------


def _hoje_utc() -> date:
    return datetime.now(timezone.utc).date()


def test_local_date_aceita_os_dois_limites():
    assert bm.LOCAL_DATE_MIN == date(2000, 1, 1)
    assert bm.BodyMeasurementIn(local_date="2000-01-01", site="waist", value_cm=80).local_date == bm.LOCAL_DATE_MIN
    amanha = _hoje_utc() + timedelta(days=1)
    assert bm.BodyMeasurementIn(local_date=amanha.isoformat(), site="waist", value_cm=80).local_date == amanha
    assert bm.BodyMeasurementIn(local_date=_hoje_utc().isoformat(), site="waist", value_cm=80).local_date == _hoje_utc()


@pytest.mark.parametrize("valor", ["0001-01-01", "1999-12-31"])
def test_local_date_antes_do_piso_e_rejeitada(valor):
    with pytest.raises(ValidationError, match="local_date"):
        bm.BodyMeasurementIn(local_date=valor, site="waist", value_cm=80)
    with pytest.raises(ValidationError, match="local_date"):
        bm.BodyMeasurementPatch(local_date=valor)


@pytest.mark.parametrize("dias", [2, 30, 365 * 10])
def test_local_date_futura_alem_de_amanha_e_rejeitada(dias):
    futura = (_hoje_utc() + timedelta(days=dias)).isoformat()
    with pytest.raises(ValidationError, match="local_date"):
        bm.BodyMeasurementIn(local_date=futura, site="waist", value_cm=80)
    with pytest.raises(ValidationError, match="local_date"):
        bm.BodyMeasurementPatch(local_date=futura)


def test_normalize_local_date_usa_o_hoje_informado():
    hoje = date(2026, 10, 10)
    assert bm.local_date_max(hoje) == date(2026, 10, 11)
    assert bm.normalize_local_date(date(2026, 10, 11), today=hoje) == date(2026, 10, 11)
    with pytest.raises(ValueError):
        bm.normalize_local_date(date(2026, 10, 12), today=hoje)
    with pytest.raises(ValueError):
        bm.normalize_local_date(date(1, 1, 1), today=hoje)


def test_patch_sem_local_date_continua_valido():
    assert bm.BodyMeasurementPatch(notes="x").model_dump(exclude_unset=True) == {"notes": "x"}


def test_espelho_do_frontend_usa_o_mesmo_piso():
    """`dashboard-body.ts` espelha o piso so como UX do `<input type="date">`;
    este teste mantem os dois lados iguais."""
    fonte = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "dashboard-body.ts"
    if not fonte.exists():
        pytest.skip("frontend fora deste checkout")
    texto = fonte.read_text(encoding="utf-8")
    assert f"export const MEASUREMENT_DATE_MIN = '{bm.LOCAL_DATE_MIN.isoformat()}';" in texto
    assert f"const MEASUREMENT_DATE_MAX_AHEAD_DAYS = {bm.LOCAL_DATE_MAX_AHEAD_DAYS};" in texto


def test_protocol_ok_precisa_ser_booleano_e_user_id_nao_e_aceito():
    with pytest.raises(ValidationError):
        bm.BodyMeasurementIn(local_date="2026-09-22", site="waist", value_cm=80, protocol_ok="talvez")
    with pytest.raises(ValidationError):
        bm.BodyMeasurementIn(local_date="2026-09-22", site="waist", value_cm=80, user_id=99)


def test_patch_parcial_valida_so_o_que_veio():
    patch = bm.BodyMeasurementPatch(value_cm=90)
    assert patch.model_dump(exclude_unset=True) == {"value_cm": Decimal("90.0")}
    with pytest.raises(ValidationError):
        bm.BodyMeasurementPatch(site="nariz")


def test_vocabulario_de_locais_tem_rotulo_para_todos():
    assert set(bm.MEASUREMENT_SITE_LABELS) == set(bm.MEASUREMENT_SITES)
    assert bm.MEASUREMENT_SITES[0] == "waist"


# ---------------------------------------------------------------------------
# Deteccao da tabela: cache so do positivo, chaveado por schema
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, schema_name: str, exists: bool):
        self.schema_name = schema_name
        self.exists = exists
        self.executed: list[str] = []
        self._row = None

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "DATABASE()" in sql and "information_schema" not in sql:
            self._row = {"db": self.schema_name}
        else:
            self._row = {"n": 1 if self.exists else 0}

    def fetchone(self):
        return self._row


@pytest.fixture(autouse=True)
def _limpa_cache():
    schema.reset_body_measurements_table_cache()
    yield
    schema.reset_body_measurements_table_cache()


def test_ausencia_nunca_e_memorizada_e_presenca_e_consultada_uma_vez():
    ausente = _Cursor("musclefit", exists=False)
    assert schema.has_body_measurements_table(ausente) is False
    assert schema.has_body_measurements_table(ausente) is False
    assert sum("information_schema.TABLES" in q for q in ausente.executed) == 2  # repete a cada chamada

    # A migracao rodou: a proxima chamada enxerga a tabela SEM restart...
    presente = _Cursor("musclefit", exists=True)
    assert schema.has_body_measurements_table(presente) is True
    # ...e a partir dai o positivo fica em cache por schema.
    assert schema.has_body_measurements_table(presente) is True
    assert sum("information_schema.TABLES" in q for q in presente.executed) == 1


def test_cache_e_por_schema():
    assert schema.has_body_measurements_table(_Cursor("a_test", exists=True)) is True
    outro = _Cursor("b_test", exists=False)
    assert schema.has_body_measurements_table(outro) is False
    assert any("information_schema.TABLES" in q for q in outro.executed)


# ---------------------------------------------------------------------------
# Rotas com a tabela ausente
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self):
        self.cursor_obj = _Cursor("musclefit", exists=False)

    def cursor(self):
        cur = self.cursor_obj

        class _Ctx:
            def __enter__(self_inner):
                return cur

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bm, "get_conn", lambda: _FakeConn())
    app = FastAPI()
    app.include_router(bm.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_leitura_com_tabela_ausente_devolve_vazio_com_limitacao(client):
    resposta = client.get("/body-measurements", params={"site": "waist"})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["items"] == []
    assert corpo["limitations"] == [schema.TABLE_MISSING_LIMITATION]


def test_escrita_com_tabela_ausente_responde_503_citando_a_047(client):
    resposta = client.post("/body-measurements", json={"local_date": "2026-09-22", "site": "waist", "value_cm": 84.5})
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["migration"] == "047"
    assert client.delete("/body-measurements/1").status_code == 503
    assert client.put("/body-measurements/1", json={"value_cm": 80}).status_code == 503


def test_validacao_da_rota_responde_422(client):
    assert client.post("/body-measurements", json={"local_date": "2026-09-22", "site": "waist", "value_cm": 5}).status_code == 422
    assert client.post("/body-measurements", json={"local_date": "0001-01-01", "site": "waist", "value_cm": 80}).status_code == 422
    assert client.put("/body-measurements/1", json={"local_date": "2999-01-01"}).status_code == 422
    assert client.get("/body-measurements", params={"site": "nariz"}).status_code == 422
    assert client.get("/body-measurements", params={"date_from": "2026-09-22", "date_to": "2026-09-01"}).status_code == 422
