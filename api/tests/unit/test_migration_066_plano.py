"""Plano da migracao 066 (versao do Modelo na ficha; saneamento de slots sem
versao) contra um `information_schema` sintetico.

- producao hoje (065 aplicada, sem orfao) -> 2 ALTER; com orfao -> + 3;
- convergido -> 0; parcial -> so o que falta;
- `version_id` continua NULL-able (a 065, imutavel, confere essa forma);
- orfaos com `sequence` repetida no mesmo Modelo -> erro ANTES do primeiro
  statement, listando os Modelos;
- dry-run de banco atrasado (065/054 so planejadas) -> sem erro e sem a
  consulta de colisao;
- o MESMO instante fecha a vigente e abre a versao dos orfaos;
- `plan_down`.

O banco de verdade (orfaos viram a versao nova; NOT NULL; idempotencia) esta
em `tests/integration/dataapi/test_template_version_on_plans_066.py`.
"""

from __future__ import annotations

import functools
from datetime import datetime

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "066_template_version_on_plans.py"
P, R, S, V = "workout_plans", "generation_runs", "workout_plan_template_slots", "workout_plan_template_versions"
AGORA = datetime(2026, 10, 6, 9, 0, 0)


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _producao() -> dict:
    return {
        "tables": {P: "InnoDB", R: "InnoDB", S: "InnoDB", V: "InnoDB"},
        "columns": {(S, "version_id"): ("bigint", "YES")},
        "indexes": {},
    }


def _convergido() -> dict:
    snap = _producao()
    for table, idx in ((P, "idx_wp_template_version"), (R, "idx_gr_template_version")):
        snap["columns"][(table, "template_version_id")] = ("bigint", "YES")
        snap["indexes"][(table, idx)] = "template_version_id"
    return snap


class FakeDB:
    def __init__(self, snap, colisoes=(), orfaos=0):
        self.snap = snap
        self.colisoes = list(colisoes)
        self.orfaos = orfaos
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        s = self.snap
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": e} for t, e in s["tables"].items()]
        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": c, "COLUMN_TYPE": typ, "IS_NULLABLE": nul}
                for (t, c), (typ, nul) in s["columns"].items()
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": t, "INDEX_NAME": i, "SEQ_IN_INDEX": 1, "COLUMN_NAME": col, "NON_UNIQUE": 1}
                for (t, i), col in s["indexes"].items()
            ]
        if sql.startswith("SELECT COUNT(*) AS n FROM workout_plan_template_slots"):
            return [{"n": self.orfaos}]
        if "HAVING COUNT(*) > 1" in sql:
            return [{"template_id": t, "sequence": q, "n": n} for t, q, n in self.colisoes]
        if sql.startswith("SELECT NOW()"):
            return [{"agora": AGORA}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError("o plano nao escreve")


def _plan(snap, **kw):
    db = FakeDB(snap, **kw)
    return [(" ".join(sql.split()), params) for sql, params in _module()["plan"](db)], db


def _g(name):
    return " ".join(_module()[name].split())


def test_producao_hoje_sem_orfao():
    plano, _ = _plan(_producao())
    assert plano == [(_g("ADD_PLANS_SQL"), None), (_g("ADD_RUNS_SQL"), None)]


def test_producao_com_orfaos_sanea_no_mesmo_instante():
    plano, _ = _plan(_producao(), orfaos=3)
    assert plano == [
        (_g("ADD_PLANS_SQL"), None),
        (_g("ADD_RUNS_SQL"), None),
        (_g("ORPHAN_CLOSE_SQL"), (AGORA,)),
        (_g("ORPHAN_OPEN_SQL"), (AGORA,)),
        (_g("ORPHAN_ATTACH_SQL"), None),
    ]


def test_version_id_nao_vira_not_null():
    """A 065 (imutavel) confere `version_id` NULL-able; o NOT NULL a tornaria
    nao-reexecutavel sobre o schema convergido."""
    for name in ("ORPHAN_CLOSE_SQL", "ORPHAN_OPEN_SQL", "ORPHAN_ATTACH_SQL", "ADD_PLANS_SQL", "ADD_RUNS_SQL"):
        assert "NOT NULL" not in _module()[name]
    assert "NOT_NULL_SQL" not in _module()


def test_o_mesmo_instante_fecha_a_vigente_e_abre_a_versao_dos_orfaos():
    close = _g("ORPHAN_CLOSE_SQL")
    open_ = _g("ORPHAN_OPEN_SQL")
    assert "SET v.superseded_at = %s WHERE v.superseded_at IS NULL" in close
    assert "WHERE version_id IS NULL" in close  # so Modelos com orfaos
    assert "%s, NULL, NULL," in open_  # effective_from = o instante; vigente
    assert "MAX(x.version_number)" in open_  # n+1 (ou v1 sem versao)
    assert "NOT EXISTS (SELECT 1 FROM workout_plan_template_versions c WHERE c.current_template_id = t.id)" in open_
    assert _g("ORPHAN_ATTACH_SQL").endswith("WHERE s.version_id IS NULL")


def test_convergido_nao_faz_nada():
    plano, db = _plan(_convergido())
    assert plano == []
    assert not any("HAVING" in q or q.startswith("SELECT NOW()") for q in db.queries)


def test_convergido_com_orfao_so_sanea():
    plano, _ = _plan(_convergido(), orfaos=1)
    assert [sql for sql, _ in plano] == [_g("ORPHAN_CLOSE_SQL"), _g("ORPHAN_OPEN_SQL"), _g("ORPHAN_ATTACH_SQL")]


def test_parcial_so_a_coluna_sem_indice():
    snap = _convergido()
    del snap["indexes"][(P, "idx_wp_template_version")]
    plano, _ = _plan(snap)
    assert plano == [("ALTER TABLE workout_plans ADD KEY idx_wp_template_version (template_version_id)", None)]


def test_orfaos_com_sequence_repetida_param_antes_de_tudo():
    with pytest.raises(Exception) as exc:
        _plan(_producao(), colisoes=[(7, 2, 2)], orfaos=2)
    assert type(exc.value).__name__ == "TemplateVersionOnPlansError"
    assert "modelo 7 sequence 2 (2x)" in str(exc.value)


def test_dry_run_de_banco_atrasado_sem_065_e_sem_054():
    snap = _producao()
    del snap["tables"][R]
    del snap["tables"][V]
    del snap["columns"][(S, "version_id")]
    plano, db = _plan(snap)
    assert len(plano) == 5  # sem `version_id` nao da para contar: os 3 entram (no-op sem orfao)
    assert not any("HAVING" in q or "COUNT(*)" in q for q in db.queries)


def test_tipo_errado_falha():
    snap = _convergido()
    snap["columns"][(P, "template_version_id")] = ("int", "YES")
    with pytest.raises(Exception, match="template_version_id"):
        _plan(snap)


def test_down():
    plano = [" ".join(s.split()) for s, _ in _module()["plan_down"](FakeDB(_convergido()))]
    assert plano == [_g("DROP_RUNS_SQL"), _g("DROP_PLANS_SQL")]
    assert _module()["plan_down"](FakeDB({"tables": {}, "columns": {}, "indexes": {}})) == []
