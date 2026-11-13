"""Arquivar versão de Modelo (migração 105): só oculta, reversível, só versão NÃO vigente.

Banco em memória mínimo: responde só os statements de listar/arquivar/desarquivar (despacho pela constante SQL).
A vigente (`superseded_at IS NULL`) nunca arquiva (409); lista padrão esconde arquivadas; `include_archived=true`
traz todas; 404 para versão de outro Modelo/inexistente/Modelo excluído; 403 para quem não é admin/PT. Reverter
para uma versão arquivada segue permitido (o `revert` não lê `archived_at`) e a nova vigente nasce visível.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import template_assignments as assignments
from app import template_versions as tv
from app import workout_plan_templates as wpt
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

ANTES = datetime(2026, 9, 1, 12, 0, 0)
AGORA = datetime(2026, 11, 5, 10, 0, 0)


def _versao(vid: int, template_id: int, number: int, superseded_at: Optional[datetime] = None) -> dict[str, Any]:
    return {
        "id": vid, "template_id": template_id, "version_number": number, "effective_from": ANTES,
        "superseded_at": superseded_at, "archived_at": None, "change_summary": None, "training_goal": None,
        "created_by_user_id": None, "author_username": None, "author_full_name": None,
        "reverted_from_number": None,
    }


class FakeDB:
    def __init__(self) -> None:
        self.templates = {10: {"id": 10, "name": "A", "description": None, "is_active": 1}}
        # Modelo 10: v1 e v2 antigas, v3 vigente. v9 é de OUTRO Modelo (11).
        self.versions: dict[int, dict[str, Any]] = {
            1: _versao(1, 10, 1, superseded_at=ANTES),
            2: _versao(2, 10, 2, superseded_at=ANTES),
            3: _versao(3, 10, 3),
            9: _versao(9, 11, 1),
        }
        self.executed: list[str] = []


class FakeCursor:
    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetchone(self):
        return dict(self._rows[0]) if self._rows else None

    def fetchall(self):
        return [dict(r) for r in self._rows]

    def execute(self, sql: str, params: Any = None) -> None:
        db, p = self.db, tuple(params or ())
        self.rowcount = 0
        if sql.startswith("SELECT id, created_by_user_id, visibility FROM workout_plan_templates"):
            # Lote 6A: guard de dono (`authorize_template`); o PT (id 1) e o dono.
            t = db.templates.get(int(p[0]))
            self._rows = [{"id": t["id"], "created_by_user_id": 1, "visibility": "privado"}] if t else []
        elif sql == wpt.LIVE_TEMPLATE_SQL:
            t = db.templates.get(int(p[0]))
            self._rows = [t] if t else []
        elif sql in (wpt.VERSIONS_LIST_SQL, wpt.VERSIONS_LIST_VISIBLE_SQL):
            rows = [v for v in db.versions.values() if v["template_id"] == p[0]]
            if sql == wpt.VERSIONS_LIST_VISIBLE_SQL:
                rows = [v for v in rows if v["archived_at"] is None]
            self._rows = sorted(rows, key=lambda v: -v["version_number"])
        elif sql == wpt.VERSION_ITEM_SQL:
            self._rows = [v for v in db.versions.values() if v["template_id"] == p[0] and v["id"] == p[1]]
        elif sql == tv.ARCHIVE_VERSION_SQL:
            agora, vid, tid = p
            v = db.versions.get(vid)
            if v and v["template_id"] == tid and v["archived_at"] is None and v["superseded_at"] is not None:
                v["archived_at"] = agora
                self.rowcount = 1
                db.executed.append(f"archive {vid}")
            self._rows = []
        elif sql == tv.UNARCHIVE_VERSION_SQL:
            vid, tid = p
            v = db.versions.get(vid)
            if v and v["template_id"] == tid and v["archived_at"] is not None:
                v["archived_at"] = None
                self.rowcount = 1
                db.executed.append(f"unarchive {vid}")
            self._rows = []
        else:  # pragma: no cover - um SQL novo sem o fake acompanhar falha alto
            raise AssertionError(f"SQL inesperado: {sql}")


class FakeConn:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)


class _Tx:
    def __init__(self, banco: FakeDB) -> None:
        self.banco = banco

    def __enter__(self):
        return FakeConn(self.banco)

    def __exit__(self, *exc):
        return False


@pytest.fixture
def db(monkeypatch):
    banco = FakeDB()
    monkeypatch.setattr(wpt, "transaction", lambda: _Tx(banco))
    monkeypatch.setattr(assignments, "db_now", lambda cur: AGORA)
    return banco


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_regra_do_dono_so_nao_vigente_e_ainda_visivel_arquiva():
    assert tv.is_archivable(None) is False
    assert tv.is_archivable(ANTES) is True
    assert tv.is_archivable(ANTES, AGORA) is False


def test_os_sql_derivados_filtram_de_verdade():
    assert "v.archived_at IS NULL" in wpt.VERSIONS_LIST_VISIBLE_SQL
    assert "v.archived_at IS NULL" not in wpt.VERSIONS_LIST_SQL
    assert "AND v.id = %s" in wpt.VERSION_ITEM_SQL and "v.archived_at" in wpt.VERSION_ITEM_SQL
    assert "archived_at IS NULL" in tv.ARCHIVE_VERSION_SQL and "superseded_at IS NOT NULL" in tv.ARCHIVE_VERSION_SQL


def test_lista_expoe_can_archive_so_nas_nao_vigentes(db):
    itens = {i["number"]: i for i in _client().get("/workout-plan-templates/10/versions").json()["items"]}
    assert [itens[n]["can_archive"] for n in (1, 2, 3)] == [True, True, False]
    assert all(i["archived"] is False for i in itens.values())


def test_arquivar_some_da_lista_padrao_e_include_archived_traz(db):
    r = _client().post("/workout-plan-templates/10/versions/1/archive")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["archived"] is True and corpo["can_archive"] is False and corpo["archived_at"] == AGORA.isoformat()
    padrao = _client().get("/workout-plan-templates/10/versions").json()["items"]
    assert [i["number"] for i in padrao] == [3, 2]
    todas = _client().get("/workout-plan-templates/10/versions?include_archived=true").json()["items"]
    assert [i["number"] for i in todas] == [3, 2, 1] and todas[2]["archived"] is True


def test_vigente_nao_arquiva(db):
    r = _client().post("/workout-plan-templates/10/versions/3/archive")
    assert r.status_code == 409 and db.versions[3]["archived_at"] is None


def test_arquivar_e_idempotente_e_desarquivar_reverte(db):
    c = _client()
    c.post("/workout-plan-templates/10/versions/2/archive")
    assert c.post("/workout-plan-templates/10/versions/2/archive").status_code == 200
    assert db.executed == ["archive 2"]
    r = c.post("/workout-plan-templates/10/versions/2/unarchive")
    assert r.status_code == 200 and r.json()["archived"] is False and r.json()["can_archive"] is True
    assert c.post("/workout-plan-templates/10/versions/2/unarchive").status_code == 200
    assert db.executed == ["archive 2", "unarchive 2"]


@pytest.mark.parametrize("rota", ["archive", "unarchive"])
def test_404_versao_de_outro_modelo_inexistente_ou_modelo_excluido(db, rota):
    c = _client()
    assert c.post(f"/workout-plan-templates/10/versions/9/{rota}").status_code == 404  # v9 é do Modelo 11
    assert c.post(f"/workout-plan-templates/10/versions/999/{rota}").status_code == 404
    assert c.post(f"/workout-plan-templates/77/versions/1/{rota}").status_code == 404  # Modelo inexistente


@pytest.mark.parametrize("papel", ["user", "regular"])
@pytest.mark.parametrize("rota", ["archive", "unarchive"])
def test_403_para_quem_nao_e_admin_nem_pt(db, rota, papel):
    # `user`: papel sem grupo (nenhuma concessao); `regular`: grupo `usuario`
    # (`modelos.treino` so `ver`/`proprios`, sem `editar`).
    r = _client(papel).post(f"/workout-plan-templates/10/versions/1/{rota}")
    assert r.status_code == 403 and db.executed == []
    assert r.json()["detail"] == FORBIDDEN_DETAIL


def test_personal_trainer_pode_arquivar(db):
    assert _client("personal_trainer").post("/workout-plan-templates/10/versions/1/archive").status_code == 200


def test_revert_nao_le_archived_at():
    """Reverter para uma versão arquivada segue permitido: nem o revert nem o escritor nem os resolvedores tocam
    `archived_at` (a nova vigente nasce com NULL)."""
    assert "archived_at" not in inspect.getsource(tv.revert_to_version)
    for sql in (tv.REVERT_INSERT_SQL, tv.INSERT_VERSION_SQL, tv.VERSION_BY_ID_SQL, tv.CURRENT_VERSION_SQL, tv.VERSION_AT_SQL):
        assert "archived_at" not in sql


def _corrida(monkeypatch, banco, vid, *, outro_arquivou: bool):
    """O UPDATE de arquivar afeta 0 linhas porque outra chamada chegou antes."""
    original = FakeCursor.execute

    def execute(self, sql, params=None):
        if sql == tv.ARCHIVE_VERSION_SQL:
            if outro_arquivou:
                banco.versions[vid]["archived_at"] = AGORA
            self.rowcount = 0
            return None
        return original(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", execute)


def test_corrida_ja_arquivada_por_outra_chamada_e_200_idempotente(db, monkeypatch):
    _corrida(monkeypatch, db, 2, outro_arquivou=True)
    r = _client().post("/workout-plan-templates/10/versions/2/archive")
    assert r.status_code == 200 and r.json()["archived"] is True


def test_corrida_sem_arquivar_continua_409(db, monkeypatch):
    _corrida(monkeypatch, db, 2, outro_arquivou=False)
    r = _client().post("/workout-plan-templates/10/versions/2/archive")
    assert r.status_code == 409
