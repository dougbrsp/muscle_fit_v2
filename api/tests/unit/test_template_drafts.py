"""Rascunho do Modelo de Treino (migracao 067) - sem banco.

O que se prova aqui, rapido:

- a 067: tabela ausente -> 1 `CREATE TABLE`; convergida -> 0; forma
  divergente -> erro antes do primeiro statement; sem FK para
  `exercise_catalog` (regra da 028), sem DML; `plan_down` -> `DROP TABLE`;
- NENHUM leitor fora do rascunho/rotas do Modelo cita a tabela (motor,
  dashboards, ficha, relatorios leem so a vigente);
- `PUT /{id}/draft` so escreve na tabela do rascunho (nada de versao, slots,
  vigente ou colunas do Modelo) e aceita estado que a publicacao recusaria;
- publicar invalido (422) nao chega a apagar o rascunho;
- permissao: usuario `regular` recebe 403 nas tres rotas do rascunho;
- janela de deploy: tabela ausente = "sem rascunho" (leitura e descarte).

O fluxo contra MySQL real (rascunho -> publicar -> versao nova e rascunho
apagado; reverter apaga; 422 mantem) esta em
`tests/integration/dataapi/test_template_drafts_067.py`.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import template_drafts as td
from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity

APP_DIR = Path(__file__).resolve().parents[2] / "app"
MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "067_workout_plan_template_drafts.py"
T, D = "workout_plan_templates", "workout_plan_template_drafts"


# ---------------------------------------------------------------------------
# Migracao 067
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeSchemaDB:
    def __init__(self, tables: dict[str, str], columns: dict[str, dict[str, tuple[str, str]]]):
        self.tables = tables
        self.columns = columns
        self.queries: list[str] = []
        self.executed: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": e} for t, e in self.tables.items() if t in params]
        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": c, "COLUMN_TYPE": typ, "IS_NULLABLE": nul, "COLUMN_KEY": ""}
                for t, cols in self.columns.items()
                if t in params
                for c, (typ, nul) in cols.items()
            ]
        raise AssertionError(sql)

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))


def _producao() -> FakeSchemaDB:
    return FakeSchemaDB({T: "InnoDB"}, {T: {"id": ("bigint(20)", "NO")}})


def _convergido() -> FakeSchemaDB:
    db = _producao()
    db.tables[D] = "InnoDB"
    db.columns[D] = dict(_module()["EXPECTED_COLUMNS"])
    return db


def test_067_producao_hoje_cria_a_tabela_e_plan_so_le():
    db = _producao()
    statements = _module()["plan"](db)
    assert [" ".join(sql.split()).split(" (")[0] for sql, _ in statements] == [f"CREATE TABLE IF NOT EXISTS {D}"]
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed


def test_067_reexecucao_no_estado_convergido_nao_faz_nada():
    assert _module()["plan"](_convergido()) == []


@pytest.mark.parametrize(
    "mexer",
    [
        lambda db: db.columns[D].update(payload=("longtext", "NO")),
        lambda db: db.columns[D].update(updated_by_user_id=("bigint", "NO")),
        lambda db: db.columns[D].pop("updated_at"),
        lambda db: db.tables.update({D: "MyISAM"}),
        lambda db: db.tables.pop(T),
        lambda db: db.columns[T].update(id=("int", "NO")),
    ],
)
def test_067_forma_divergente_falha_antes_do_primeiro_statement(mexer):
    db = _convergido()
    mexer(db)
    with pytest.raises(_module()["TemplateDraftsError"]):
        _module()["up"](db)
    assert not db.executed


def test_067_ddl_aditivo_sem_fk_para_exercise_catalog_e_sem_dml():
    sqls = " ".join(v for k, v in _module().items() if k.endswith("_SQL")).upper()
    assert "EXERCISE_CATALOG" not in sqls
    assert sqls.count("FOREIGN KEY") == 1 and f"REFERENCES {T.upper()}(ID) ON DELETE CASCADE" in sqls
    for sql in (v for k, v in _module().items() if k.endswith("_SQL")):
        assert sql.strip().upper().startswith(("CREATE TABLE", "DROP TABLE")), sql
    for proibido in ("PREPARE", "SET @", "USE "):
        assert proibido not in sqls


def test_067_down_derruba_so_se_existe():
    db = _convergido()
    _module()["down"](db)
    assert db.executed == [f"DROP TABLE IF EXISTS {D}"]
    assert _module()["plan_down"](_producao()) == []


# ---------------------------------------------------------------------------
# Guarda: ninguem alem do rascunho le o rascunho
# ---------------------------------------------------------------------------

# Os UNICOS arquivos de `api/app` que podem citar a tabela.
LEITORES_PERMITIDOS = {"template_drafts.py", "workout_plan_templates.py"}


def test_nenhum_leitor_oficial_le_o_rascunho():
    citam = set()
    for path in APP_DIR.rglob("*.py"):
        if D in path.read_text(encoding="utf-8"):
            citam.add(path.relative_to(APP_DIR).as_posix())
    assert citam <= LEITORES_PERMITIDOS, sorted(citam - LEITORES_PERMITIDOS)
    # Sanidade: a guarda enxerga o modulo do rascunho.
    assert "template_drafts.py" in citam


def test_get_template_do_motor_nao_toca_o_rascunho():
    """`_get_template` e lido pelo motor de geracao e pelos dashboards: o selo
    do rascunho entra so nas rotas (`_with_draft_flags`)."""
    tree = ast.parse((APP_DIR / "workout_plan_templates.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_get_template")
    assert "template_drafts" not in ast.unparse(fn)


# ---------------------------------------------------------------------------
# Rotas (banco falso que grava o SQL)
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, db: "FakeDB"):
        self.db = db
        self._rows: list[dict] = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.db.sql.append(flat)
        if self.db.missing_table and D in flat:
            raise Exception(f"(1146, \"Table 'musclefit.{D}' doesn't exist\")")
        self._rows, self.rowcount = [], 0
        if flat.startswith("SELECT id, name, description, is_active FROM workout_plan_templates"):
            self._rows = [{"id": params[0], "name": "M", "description": None, "is_active": 1}] if self.db.live else []
        elif flat.startswith(f"INSERT INTO {D}"):
            self.db.draft = {"template_id": params[0], "payload": params[1], "updated_by_user_id": params[2]}
            self.rowcount = 1
        elif flat.startswith("SELECT d.template_id"):
            if self.db.draft:
                self._rows = [dict(self.db.draft, updated_at="2026-10-06 10:00:00", updated_by_username="pt")]
        elif flat.startswith(f"DELETE FROM {D}"):
            self.rowcount = 1 if self.db.draft else 0
            self.db.draft = None
        elif flat.startswith(f"SELECT template_id, updated_at FROM {D}"):
            self._rows = [{"template_id": self.db.draft["template_id"], "updated_at": "x"}] if self.db.draft else []
        self.rowcount = self.rowcount or len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, *, live=True, missing_table=False):
        self.sql: list[str] = []
        self.draft: dict | None = None
        self.live = live
        self.missing_table = missing_table

    @contextlib.contextmanager
    def transaction(self):
        class Conn:
            def cursor(conn_self):
                return FakeCursor(self)

        yield Conn()


def _client(monkeypatch, db: FakeDB, role: str = "personal_trainer") -> TestClient:
    monkeypatch.setattr(wpt, "transaction", db.transaction)
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=7, role=role, source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    return TestClient(app)


def _incompleto() -> dict:
    """Estado que a PUBLICACAO recusaria (slot de descanso com item de grupo
    muscular, item sem grupo, nome vazio) - o rascunho aceita."""
    return {
        "name": "",
        "volume_mode": "equipment_series",
        "slots": [
            {"sequence": 1, "name": "Dia 1", "slot_type": "descanso",
             "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": None}]},
            {"sequence": 2, "name": "Dia 2", "slot_type": "normal",
             "items": [{"item_order": 1, "item_kind": "equipment", "mapping_id": None, "sets_count": None}]},
        ],
        "generation_log_id": 99,
    }


def test_salvar_rascunho_so_escreve_na_tabela_do_rascunho(monkeypatch):
    db = FakeDB()
    api = _client(monkeypatch, db)
    resposta = api.put("/workout-plan-templates/5/draft", json=_incompleto())
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["payload"]["slots"][0]["slot_type"] == "descanso"
    assert corpo["updated_by"] == {"id": 7, "name": "pt"}
    # `generation_log_id` e da criacao: nao vai para o rascunho.
    assert "generation_log_id" not in json.loads(db.draft["payload"])
    escritas = [s for s in db.sql if re.match(r"(INSERT|UPDATE|DELETE)\b", s)]
    assert escritas and all(D in s for s in escritas), escritas
    assert not any("workout_plan_template_versions" in s or "workout_plan_template_slots" in s for s in db.sql)


def test_get_e_delete_do_rascunho(monkeypatch):
    db = FakeDB()
    api = _client(monkeypatch, db)
    assert api.get("/workout-plan-templates/5/draft").status_code == 404
    api.put("/workout-plan-templates/5/draft", json=_incompleto())
    lido = api.get("/workout-plan-templates/5/draft")
    assert lido.status_code == 200 and lido.json()["payload"]["volume_mode"] == "equipment_series"
    assert api.delete("/workout-plan-templates/5/draft").status_code == 200
    assert api.delete("/workout-plan-templates/5/draft").status_code == 404
    assert db.draft is None


def test_modelo_excluido_ou_inexistente_da_404(monkeypatch):
    api = _client(monkeypatch, FakeDB(live=False))
    assert api.put("/workout-plan-templates/5/draft", json=_incompleto()).status_code == 404
    assert api.get("/workout-plan-templates/5/draft").status_code == 404
    assert api.delete("/workout-plan-templates/5/draft").status_code == 404


def test_formato_invalido_e_422_do_pydantic(monkeypatch):
    api = _client(monkeypatch, FakeDB())
    assert api.put("/workout-plan-templates/5/draft", json={"slots": []}).status_code == 422  # sem `name`
    corpo = dict(_incompleto(), volume_mode="outro")
    assert api.put("/workout-plan-templates/5/draft", json=corpo).status_code == 422


def test_rascunho_grande_demais_e_413(monkeypatch):
    monkeypatch.setattr(td, "MAX_PAYLOAD_BYTES", 100)
    api = _client(monkeypatch, FakeDB())
    assert api.put("/workout-plan-templates/5/draft", json=_incompleto()).status_code == 413


@pytest.mark.parametrize("metodo", ["get", "put", "delete"])
def test_usuario_regular_recebe_403(monkeypatch, metodo):
    db = FakeDB()
    api = _client(monkeypatch, db, role="regular")
    kwargs = {"json": _incompleto()} if metodo == "put" else {}
    assert getattr(api, metodo)("/workout-plan-templates/5/draft", **kwargs).status_code == 403
    assert db.sql == []


def test_publicar_invalido_422_nao_apaga_o_rascunho(monkeypatch):
    """A validacao do PUT roda ANTES da transacao de escrita: um 422 nunca
    alcanca o `discard_draft`."""
    chamadas = []
    monkeypatch.setattr(td, "discard_draft", lambda cur, tid: chamadas.append(tid))
    monkeypatch.setattr(wpt, "_get_template", lambda tid, **kw: {"id": tid, "slots": [], "methodology_intent": None})
    api = _client(monkeypatch, FakeDB())
    corpo = dict(_incompleto(), name="Modelo")
    resposta = api.put("/workout-plan-templates/5", json=corpo)
    assert resposta.status_code == 422, resposta.text
    assert chamadas == []


# ---------------------------------------------------------------------------
# Janela de deploy: 067 ainda nao aplicada
# ---------------------------------------------------------------------------


def test_sem_a_tabela_leitura_e_descarte_viram_sem_rascunho(monkeypatch):
    db = FakeDB(missing_table=True)
    api = _client(monkeypatch, db)
    assert api.get("/workout-plan-templates/5/draft").status_code == 404
    assert api.delete("/workout-plan-templates/5/draft").status_code == 404
    assert api.put("/workout-plan-templates/5/draft", json=_incompleto()).status_code == 503
    with db.transaction() as conn:
        cur = conn.cursor()
        assert td.draft_stamps(cur, [5]) == {}
        assert td.discard_draft(cur, 5) is False
        assert td.get_draft(cur, 5) is None


def test_outro_erro_de_banco_nao_e_engolido_no_descarte():
    class Quebrado:
        def execute(self, sql, params=None):
            raise RuntimeError("Lock wait timeout exceeded")

    with pytest.raises(RuntimeError):
        td.discard_draft(Quebrado(), 5)


def test_attach_draft_flags():
    assert td.attach_draft_flags({"id": 1}, {1: "t"}) == {"id": 1, "has_draft": True, "draft_updated_at": "t"}
    assert td.attach_draft_flags({"id": 2}, {1: "t"}) == {"id": 2, "has_draft": False, "draft_updated_at": None}

