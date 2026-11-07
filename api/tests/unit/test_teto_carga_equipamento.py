"""Teto de carga do equipamento (migracao 110): motores, repositorio/rotas e
migracao. Regra de calculo SO nos donos (`domain/next_set_load.py`,
`domain/progression_engine.py`); aqui so se prova o contrato.

- com teto: sugestao <= teto e nenhum aumento armado; sem teto: nada muda;
- o usuario corrente e sempre o dono (user_id nunca vem do cliente).

Rodar: cd api && python -m pytest tests/unit/test_teto_carga_equipamento.py
"""

from __future__ import annotations

import functools
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import equipment_load_ceiling as elc
from app import migrate as m
from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.engine_decision_context import PairEngineContext, bound_ai_load, next_set_load_for
from app.security import Identity, get_current_identity
from tests.support import progression_fixtures as fx
from tests.support.fake_db import NullContext, fake_transaction

NOW = datetime(2026, 9, 10, 18, 0)
USER_ID = 42
PASSO = Decimal("2.5")


# ---------------------------------------------------------------------------
# Proxima serie
# ---------------------------------------------------------------------------


def _sobe(max_load_kg=None, weight=40):
    # 13 reps com RPE 8: regra padrao pede aumento.
    return nsl.suggest_next_set_load(
        set_type="working", reps=13, weight_kg=weight, rpe=8, step_kg=PASSO, max_load_kg=max_load_kg
    )


def test_sem_teto_o_comportamento_e_o_de_sempre():
    base = _sobe()
    assert base.direction == nsl.INCREASE_LOAD
    assert base.load_kg > 40
    assert _sobe(max_load_kg=None) == base


def test_carga_no_teto_mantem_e_explica():
    r = _sobe(max_load_kg=40)
    assert (r.direction, r.load_kg, r.reason) == (nsl.HOLD, 40.0, nsl.REASON_AT_EQUIPMENT_CEILING)
    assert "Carga máxima do equipamento atingida" in r.reason_label


def test_sugestao_nunca_passa_do_teto():
    sem = _sobe()
    teto = Decimal(str(sem.load_kg)) - Decimal("1")  # entre a carga atual e a sugerida
    r = _sobe(max_load_kg=teto)
    assert r.direction == nsl.INCREASE_LOAD
    assert r.load_kg == float(teto)


def test_teto_acima_da_sugestao_nao_muda_nada():
    assert _sobe(max_load_kg=500) == _sobe()


def test_teto_nao_afeta_reducao_nem_manter():
    reduz = nsl.suggest_next_set_load(set_type="working", reps=3, weight_kg=40, rpe=10, step_kg=PASSO, max_load_kg=30)
    sem_teto = nsl.suggest_next_set_load(set_type="working", reps=3, weight_kg=40, rpe=10, step_kg=PASSO)
    assert reduz == sem_teto


def test_contexto_do_par_leva_o_teto_para_a_regra():
    ctx = PairEngineContext(step_kg=PASSO, max_load_kg=Decimal("40"))
    r = next_set_load_for({"set_type": "working", "reps": 13, "weight_kg": 40, "rpe": 8}, ctx)
    assert (r.direction, r.reason) == (nsl.HOLD, nsl.REASON_AT_EQUIPMENT_CEILING)


def test_teto_abaixo_da_carga_atual_mantem_sem_reduzir():
    r = _sobe(max_load_kg=30, weight=40)
    assert (r.direction, r.load_kg, r.reason) == (nsl.HOLD, 40.0, nsl.REASON_AT_EQUIPMENT_CEILING)


def test_bound_ai_load_limita_a_ia_ao_teto_sem_regra():
    ctx = PairEngineContext(step_kg=PASSO, max_load_kg=Decimal("50"))
    carga, ajustes = bound_ai_load(60, ctx, executed_kg=None)
    assert carga == 50.0
    assert ajustes[-1]["reason"] == "EQUIPMENT_LOAD_CEILING"
    sem_teto, _ = bound_ai_load(60, PairEngineContext(step_kg=PASSO), executed_kg=None)
    assert sem_teto == 60.0


def test_bound_ai_load_nunca_reduz_por_causa_do_teto():
    # executou 55 com teto 50: a IA propoe 60 -> mantem 55 (nem sobe, nem desce ate o teto).
    ctx = PairEngineContext(step_kg=PASSO, max_load_kg=Decimal("50"))
    carga, _ = bound_ai_load(60, ctx, executed_kg=55)
    assert carga == 55.0
    # proposta que ja e reducao nao e tocada pelo teto
    reduz, _ = bound_ai_load(52.5, ctx, executed_kg=55)
    assert reduz == 52.5


# ---------------------------------------------------------------------------
# Proxima sessao (progression_engine)
# ---------------------------------------------------------------------------

SAFETY = fx.safety(max_safe_rpe=10)


def _trilha(teto=None, load=40, pendente=None):
    track = replace(fx.track(load=load), max_load_kg=None if teto is None else Decimal(str(teto)))
    if pendente is not None:
        track = replace(track, pending_load_kg=Decimal(str(pendente)), pending_load_since=NOW)
    return track


def test_next_load_respeita_o_teto():
    assert pe.next_load(_trilha(teto=41))[0] == Decimal("41")
    assert pe.next_load(_trilha(teto=40))[0] == Decimal("40")
    assert pe.next_load(_trilha())[0] == Decimal("42.5")


def test_trilha_no_teto_nao_arma_aumento_de_carga():
    track = _trilha(teto=40)
    assert pe.arm_track_load_increase(track, now=NOW, source=pe.PENDING_LOAD_SOURCE_EXCESS) == track
    sem_teto = pe.arm_track_load_increase(_trilha(), now=NOW, source=pe.PENDING_LOAD_SOURCE_EXCESS)
    assert sem_teto.pending_load_kg == Decimal("42.5")


def test_aumento_pendente_e_limitado_pelo_teto_e_some_no_teto():
    assert pe.track_pending_load(_trilha(teto=41, pendente=42.5), now=NOW) == Decimal("41")
    assert pe.track_pending_load(_trilha(teto=40, pendente=42.5), now=NOW) is None


def test_prescribe_no_teto_segue_sem_aumentar_e_explica():
    track = _trilha(teto=40, pendente=42.5)
    d = pe.prescribe(track, fx.group(phase=pe.F2), SAFETY, now=NOW)
    assert d.decision != pe.INCREASE_LOAD
    assert d.suggested_load_kg == 40.0
    assert d.evidence[pe.EVIDENCE_EQUIPMENT_LOAD_CEILING] == pe.EQUIPMENT_CEILING_REACHED
    assert pe.EQUIPMENT_LOAD_CEILING_NOTE in d.reason


def test_prescribe_abaixo_do_teto_sobe_ate_o_teto():
    track = _trilha(teto=41, pendente=42.5)
    d = pe.prescribe(track, fx.group(phase=pe.F2), SAFETY, now=NOW)
    assert d.decision == pe.INCREASE_LOAD
    assert d.suggested_load_kg == 41.0


def test_prescribe_sem_teto_identico_ao_anterior():
    track = _trilha(pendente=42.5)
    d = pe.prescribe(track, fx.group(phase=pe.F2), SAFETY, now=NOW)
    assert (d.decision, d.suggested_load_kg) == (pe.INCREASE_LOAD, 42.5)
    assert pe.EVIDENCE_EQUIPMENT_LOAD_CEILING not in d.evidence


# ---------------------------------------------------------------------------
# Repositorio e rotas
# ---------------------------------------------------------------------------


class _Cursor(NullContext):
    def __init__(self, *, link=7, ceiling=None, error=None):
        self.link, self.ceiling, self.error = link, ceiling, error
        self.executed: list[tuple[str, Any]] = []
        self.rowcount = 1
        self._row: dict | None = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.error is not None and elc.CEILINGS_TABLE in sql:
            raise self.error
        if f"JOIN {elc.CEILINGS_TABLE} c" in sql:
            self._row = None if self.ceiling is None else {"max_load_kg": self.ceiling}
        elif "SELECT eqex.id AS link_id" in sql:
            self._row = None if self.link is None else {"link_id": self.link}
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


def _client(monkeypatch, cur):
    monkeypatch.setattr(elc, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(elc.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=USER_ID, role="regular", source="session")
    return TestClient(app)


def test_put_grava_para_o_usuario_corrente_e_resolve_o_par_no_servidor(monkeypatch):
    cur = _Cursor()
    r = _client(monkeypatch, cur).put(
        "/me/equipment-load-ceiling", json={"mapping_id": 5, "gym_equipment_id": 9, "max_load_kg": 100}
    )
    assert r.status_code == 200 and r.json()["max_load_kg"] == 100.0
    sql, params = cur.executed[-1]
    assert "INSERT INTO user_equipment_load_ceilings" in sql
    assert params[0] == USER_ID and params[1] == 7


def test_put_recusa_user_id_no_corpo_e_valor_invalido(monkeypatch):
    c = _client(monkeypatch, _Cursor())
    base = {"mapping_id": 5, "gym_equipment_id": 9, "max_load_kg": 100}
    assert c.put("/me/equipment-load-ceiling", json={**base, "user_id": 1}).status_code == 422
    assert c.put("/me/equipment-load-ceiling", json={**base, "max_load_kg": 0}).status_code == 422
    assert c.put("/me/equipment-load-ceiling", json={**base, "max_load_kg": -5}).status_code == 422


def test_put_par_inexistente_404(monkeypatch):
    r = _client(monkeypatch, _Cursor(link=None)).put(
        "/me/equipment-load-ceiling", json={"mapping_id": 5, "gym_equipment_id": 9, "max_load_kg": 80}
    )
    assert r.status_code == 404


def test_get_le_so_o_teto_do_usuario_corrente(monkeypatch):
    cur = _Cursor(ceiling=Decimal("120.00"))
    r = _client(monkeypatch, cur).get("/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9")
    assert r.json() == {"ok": True, "max_load_kg": 120.0}
    assert USER_ID in cur.executed[-1][1]
    sem = _client(monkeypatch, _Cursor()).get("/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9")
    assert sem.json()["max_load_kg"] is None


def test_delete_so_apaga_do_usuario_corrente_e_e_idempotente(monkeypatch):
    cur = _Cursor()
    r = _client(monkeypatch, cur).delete("/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9")
    assert r.json()["deleted"] is True
    sql, params = cur.executed[-1]
    assert sql.startswith("DELETE FROM user_equipment_load_ceilings") and params == (USER_ID, 7)
    vazio = _client(monkeypatch, _Cursor(link=None)).delete(
        "/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9"
    )
    assert vazio.json()["deleted"] is False


def test_tabela_ausente_503_na_escrita_e_sem_teto_na_leitura(monkeypatch):
    erro = Exception("(1146) Table 'x.user_equipment_load_ceilings' doesn't exist")
    c = _client(monkeypatch, _Cursor(error=erro))
    put = c.put("/me/equipment-load-ceiling", json={"mapping_id": 5, "gym_equipment_id": 9, "max_load_kg": 80})
    assert put.status_code == 503
    assert elc.pair_ceiling(_Cursor(error=erro), 1, 5, 9) is None


# ---------------------------------------------------------------------------
# Migracao 110 (forma)
# ---------------------------------------------------------------------------

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "110_user_equipment_load_ceilings.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class _FakeDB:
    def __init__(self, *, tabela=True, engine="InnoDB", colunas=None, primary=None):
        g = _mod()
        self.tabela, self.engine = tabela, engine
        self.colunas = dict(g["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.primary = list(g["PRIMARY_COLUMNS"] if primary is None else primary)
        self.executados: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "t", "ENGINE": self.engine}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            return [{"COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nul} for n, (t, nul) in self.colunas.items()]
        return [{"COLUMN_NAME": c} for c in self.primary]

    def execute(self, sql, params=None):
        self.executados.append(sql)


def test_migracao_110_cria_quando_ausente_e_converge_quando_presente():
    plano = _mod()["plan"](_FakeDB(tabela=False))
    assert len(plano) == 1 and "CREATE TABLE IF NOT EXISTS user_equipment_load_ceilings" in plano[0][0]
    assert _mod()["plan"](_FakeDB()) == []


def test_migracao_110_sem_fk_e_com_pk_do_par_e_reversivel():
    sql = _mod()["CREATE_SQL"]
    assert "FOREIGN KEY" not in sql and "REFERENCES" not in sql
    assert "PRIMARY KEY (user_id, equipment_exercise_id)" in sql and "max_load_kg DECIMAL" in sql
    assert _mod()["plan_down"](_FakeDB(tabela=False)) == []
    assert _mod()["plan_down"](_FakeDB()) == [(_mod()["DROP_TABLE_SQL"], None)]


def test_migracao_110_forma_divergente_falha_antes_de_qualquer_statement():
    err = _mod()["UserEquipmentLoadCeilingsError"]
    ruim = {**_mod()["EXPECTED_COLUMNS"], "max_load_kg": ("int", "NO")}
    with pytest.raises(err):
        _mod()["plan"](_FakeDB(colunas=ruim))
    with pytest.raises(err):
        _mod()["plan"](_FakeDB(engine="MyISAM"))
    with pytest.raises(err):
        _mod()["plan"](_FakeDB(primary=["user_id"]))
