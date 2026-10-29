"""Plano da migracao 065 (versionamento do Modelo) contra um
`information_schema` SINTETICO, montado a partir do DDL do repositorio.

Por que sintetico e nao um recorte da Aurora: nenhuma leitura nova da AWS foi
feita para este PR, e as tabelas tocadas (`workout_plan_templates`, `_slots`,
as duas da 056) tem a forma que o baseline 007 + as `.py` 016..062 dao a
elas, aplicadas iguais nos dois ambientes. O estado "producao hoje" abaixo e
exatamente essa forma - o que `test_o_estado_de_producao_reflete_o_ddl`
amarra ao texto dos arquivos.

O que se prova:
- producao hoje -> os 8 statements, na ordem (indice de apoio da FK ANTES do
  DROP do UNIQUE antigo; backfill antes do UNIQUE novo);
- convergido -> 0 statements; parcial -> so o que falta, com o backfill
  (reexecutavel) enquanto a forma final dos slots nao estiver completa;
- forma divergente -> erro ANTES do primeiro statement;
- `plan` so le `information_schema`;
- `plan_down` recusa com mais de uma versao ou Modelo excluido.

O banco de verdade (dois backends, down -> up com backfill) esta em
`tests/integration/dataapi/test_template_versions_065.py`.
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "065_workout_plan_template_versions.py"

T, S, V = "workout_plan_templates", "workout_plan_template_slots", "workout_plan_template_versions"
I, IT = "workout_plan_template_methodology_intent", "workout_plan_template_methodology_intent_targets"
ENUM_INTENT = "enum('standard','specialization','maintenance','cutting')"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _producao() -> dict:
    """Estado de hoje (064 aplicada)."""
    return {
        "tables": {T: "InnoDB", S: "InnoDB", I: "InnoDB", IT: "InnoDB"},
        "columns": {
            T: {
                "id": ("bigint", "NO"),
                "training_type_id": ("bigint", "YES"),
                "volume_mode": ("varchar(20)", "NO"),
                "template_type": ("varchar(40)", "NO"),
                "grid_rows": ("tinyint", "YES"),
                "grid_cols": ("tinyint", "YES"),
                "created_by_user_id": ("bigint", "NO"),
            },
            S: {"id": ("bigint", "NO"), "template_id": ("bigint", "NO"), "sequence": ("tinyint", "NO")},
            I: {"template_id": ("bigint", "NO"), "intent": (ENUM_INTENT, "NO")},
            IT: {"template_id": ("bigint", "NO"), "muscle_group_id": ("bigint", "NO")},
        },
        "gen": {},
        "indexes": {(S, "uq_wpts_template_sequence"): (0, ("template_id", "sequence"))},
        "fks": {(S, "fk_wpts_template"): ("template_id", T)},
    }


def _com_tabela_de_versoes(snap: dict) -> dict:
    snap["tables"][V] = "InnoDB"
    snap["columns"][V] = dict(_module()["EXPECTED_VERSION_COLUMNS"])
    snap["gen"][(V, "current_template_id")] = "if((`superseded_at` is null),`template_id`,NULL)"
    snap["indexes"][(V, "uq_wptv_current")] = (0, ("current_template_id",))
    snap["indexes"][(V, "uq_wptv_template_number")] = (0, ("template_id", "version_number"))
    snap["fks"][(V, "fk_wptv_template")] = ("template_id", T)
    return snap


def _convergido() -> dict:
    snap = _com_tabela_de_versoes(_producao())
    snap["columns"][T]["deleted_at"] = ("datetime", "YES")
    snap["columns"][S]["version_id"] = ("bigint", "YES")
    del snap["indexes"][(S, "uq_wpts_template_sequence")]
    snap["indexes"][(S, "idx_wpts_template")] = (1, ("template_id",))
    snap["indexes"][(S, "uq_wpts_version_sequence")] = (0, ("version_id", "sequence"))
    snap["fks"][(S, "fk_wpts_version")] = ("version_id", V)
    return snap


class FakeDB:
    def __init__(self, snap, *, multiplas=(), excluidos=()):
        self.snap = snap
        self.multiplas = list(multiplas)
        self.excluidos = list(excluidos)
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        s = self.snap
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": e} for t, e in s["tables"].items() if t in params]
        if "information_schema.COLUMNS" in sql:
            return [
                {
                    "TABLE_NAME": t, "COLUMN_NAME": c, "COLUMN_TYPE": typ, "IS_NULLABLE": nul,
                    "COLUMN_KEY": "", "EXTRA": "", "GENERATION_EXPRESSION": s["gen"].get((t, c), ""),
                }
                for t, cols in s["columns"].items() if t in params for c, (typ, nul) in cols.items()
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": t, "INDEX_NAME": idx, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu}
                for (t, idx), (nu, cols) in s["indexes"].items() if t in params
                for seq, col in enumerate(cols, 1)
            ]
        if "KEY_COLUMN_USAGE" in sql:
            return [
                {"TABLE_NAME": t, "CONSTRAINT_NAME": fk, "COLUMN_NAME": col, "REFERENCED_TABLE_NAME": ref,
                 "REFERENCED_COLUMN_NAME": "id"}
                for (t, fk), (col, ref) in s["fks"].items() if t in params
            ]
        if "HAVING COUNT(*) > 1" in sql:
            return [{"template_id": t, "n": n} for t, n in self.multiplas]
        if "deleted_at IS NOT NULL" in sql:
            return [{"id": i} for i in self.excluidos]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def _plan(snap) -> list[str]:
    return [" ".join(sql.split()) for sql, _ in _module()["plan"](FakeDB(snap))]


def _plan_down(snap, **kw) -> list[str]:
    return [" ".join(sql.split()) for sql, _ in _module()["plan_down"](FakeDB(snap, **kw))]


def _g(name: str) -> str:
    return " ".join(_module()[name].split())


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------


def test_producao_hoje_executa_os_oito_statements_na_ordem():
    assert _plan(_producao()) == [
        _g("CREATE_VERSIONS_SQL"),
        _g("ADD_DELETED_AT_SQL"),
        _g("ADD_SLOT_COLUMNS_SQL"),
        _g("BACKFILL_VERSIONS_SQL"),
        _g("BACKFILL_SLOTS_SQL"),
        _g("DROP_LEGACY_UNIQUE_SQL"),
        _g("ADD_VERSION_UNIQUE_SQL"),
        _g("ADD_VERSION_FK_SQL"),
    ]


def test_o_indice_de_apoio_da_fk_entra_antes_do_drop_do_unique_antigo():
    """errno 150 (licao da 049/052): `fk_wpts_template` usa o UNIQUE antigo
    como apoio ate existir `idx_wpts_template`."""
    plano = _plan(_producao())
    assert plano.index(_g("ADD_SLOT_COLUMNS_SQL")) < plano.index(_g("DROP_LEGACY_UNIQUE_SQL"))
    assert "ADD KEY idx_wpts_template (template_id)" in _g("ADD_SLOT_COLUMNS_SQL")


def test_backfill_e_uma_linha_por_modelo_vigente_desde_sempre_e_reexecutavel():
    insert = _g("BACKFILL_VERSIONS_SQL")
    assert "SELECT t.id, 1," in insert
    assert "NULL, NULL, t.created_by_user_id" in insert  # effective_from/superseded_at NULL
    assert "JSON_ARRAYAGG(mit.muscle_group_id)" in insert
    assert "COALESCE(mi.intent, 'standard')" in insert
    assert "WHERE NOT EXISTS (SELECT 1 FROM workout_plan_template_versions v WHERE v.template_id = t.id)" in insert
    update = _g("BACKFILL_SLOTS_SQL")
    assert "JOIN workout_plan_template_versions v ON v.current_template_id = s.template_id" in update
    assert update.endswith("WHERE s.version_id IS NULL")


def test_a_fk_nova_nao_aponta_para_exercise_catalog():
    """A 028 recusa FK nova para o catalogo; a 065 so liga versoes e slots."""
    for name in ("CREATE_VERSIONS_SQL", "ADD_VERSION_FK_SQL"):
        assert "exercise_catalog" not in _module()[name]


def test_convergido_nao_executa_nada():
    assert _plan(_convergido()) == []


def test_o_plano_so_le_information_schema():
    db = FakeDB(_producao())
    _module()["plan"](db)
    assert all("information_schema" in q for q in db.queries)


def test_parcial_so_tabela_criada_segue_do_ponto_certo():
    snap = _com_tabela_de_versoes(_producao())
    assert _plan(snap)[0] == _g("ADD_DELETED_AT_SQL")
    assert _g("CREATE_VERSIONS_SQL") not in _plan(snap)


def test_parcial_ate_o_drop_refaz_o_backfill_e_completa():
    """Interrompida depois do DROP: o backfill (reexecutavel) roda de novo,
    pendurando slots que tenham ficado sem versao, e entram unique + FK."""
    snap = _convergido()
    del snap["indexes"][(S, "uq_wpts_version_sequence")]
    del snap["fks"][(S, "fk_wpts_version")]
    assert _plan(snap) == [
        _g("BACKFILL_VERSIONS_SQL"),
        _g("BACKFILL_SLOTS_SQL"),
        _g("ADD_VERSION_UNIQUE_SQL"),
        _g("ADD_VERSION_FK_SQL"),
    ]


def test_parcial_so_o_indice_de_apoio_presente_vira_statement_separado():
    snap = _producao()
    snap["indexes"][(S, "idx_wpts_template")] = (1, ("template_id",))
    plano = _plan(snap)
    assert _module()["ADD_VERSION_ID_SQL"] in plano
    assert _g("ADD_SLOT_COLUMNS_SQL") not in plano


@pytest.mark.parametrize(
    "estrago, mensagem",
    [
        (lambda s: s["columns"][S].__setitem__("version_id", ("int", "YES")), "version_id"),
        (lambda s: s["columns"][T].__setitem__("deleted_at", ("timestamp", "YES")), "deleted_at"),
        (lambda s: s["tables"].__setitem__(S, "MyISAM"), "MyISAM"),
        (lambda s: s["columns"][S].pop("sequence"), "sequence"),
        (lambda s: s["columns"][T].__setitem__("grid_rows", ("int", "YES")), "grid_rows"),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(estrago, mensagem):
    snap = _producao()
    estrago(snap)
    with pytest.raises(Exception) as exc:
        _plan(snap)
    assert type(exc.value).__name__ == "TemplateVersioningError"
    assert mensagem in str(exc.value)


def test_coluna_gerada_com_outra_expressao_falha():
    snap = _com_tabela_de_versoes(_producao())
    snap["gen"][(V, "current_template_id")] = "if((`superseded_at` is null),`id`,NULL)"
    with pytest.raises(Exception, match="current_template_id"):
        _plan(snap)


def test_tabela_de_versoes_sem_o_unique_da_vigente_falha():
    snap = _com_tabela_de_versoes(_producao())
    del snap["indexes"][(V, "uq_wptv_current")]
    with pytest.raises(Exception, match="uq_wptv_current"):
        _plan(snap)


def test_o_estado_de_producao_reflete_o_ddl():
    """Amarra o estado sintetico ao repositorio: o UNIQUE antigo e as colunas
    exigidas estao nos arquivos que os criaram."""
    d = m.DEFAULT_MIGRATIONS_DIR
    baseline = (d / "007_workout_plan_templates.sql").read_text(encoding="utf-8")
    assert "UNIQUE KEY uq_wpts_template_sequence (template_id, sequence)" in baseline
    assert "sequence TINYINT NOT NULL" in baseline
    assert "ADD COLUMN grid_rows TINYINT NULL" in (d / "062_template_grid_layout.py").read_text(encoding="utf-8")
    assert ENUM_INTENT == _module()["INTENT_COLUMN_TYPE"]


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------


def test_down_do_convergido_desfaz_na_ordem_inversa():
    assert _plan_down(_convergido()) == [
        _g("DROP_VERSION_FK_SQL"),
        _g("DROP_VERSION_UNIQUE_SQL"),
        _g("RESTORE_LEGACY_UNIQUE_SQL"),
        _g("DROP_SLOT_COLUMNS_SQL"),
        _g("DROP_VERSIONS_SQL"),
        _g("DROP_DELETED_AT_SQL"),
    ]


def test_down_recusa_quando_ja_existe_mais_de_uma_versao():
    with pytest.raises(Exception, match="modelo 7: 2 versoes"):
        _plan_down(_convergido(), multiplas=[(7, 2)])


def test_down_recusa_quando_ha_modelo_excluido():
    with pytest.raises(Exception, match="excluido"):
        _plan_down(_convergido(), excluidos=[9])


def test_down_de_producao_hoje_nao_faz_nada():
    assert _plan_down(_producao()) == []



@pytest.mark.parametrize(
    "pendente",
    [
        lambda s: s["columns"][T].pop("grid_rows") and s["columns"][T].pop("grid_cols"),  # 062 pendente
        lambda s: s["tables"].pop(I) and s["tables"].pop(IT),  # 056 pendente
    ],
)
def test_dry_run_de_banco_atrasado_tolera_o_que_migracao_anterior_ainda_cria(pendente):
    """`migrate --dry-run` planeja a 065 sem ter aplicado de verdade as
    anteriores: a coluna/tabela que o backfill le pode nao existir AINDA."""
    snap = _producao()
    pendente(snap)
    assert len(_plan(snap)) == 8
