"""Edicao manual de FC de repouso / HRV em `intervals_wellness` (069).

Sem banco e sem rede. O MySQL real (os dois backends) esta em
`tests/integration/dataapi/test_intervals_wellness_edicao_manual.py`.

Cobre:
- precedencia do sync (`intervals_store.upsert_wellness`), avaliando o TEXTO
  real do `ON DUPLICATE KEY UPDATE` com a semantica do MySQL (atribuicoes da
  esquerda para a direita, cada uma ja enxergando as anteriores):
  * Intervals com valor -> sobrescreve a edicao, origem 'intervals';
  * Intervals sem valor e origem 'manual' -> mantem a edicao;
  * Intervals sem valor e origem 'intervals' -> grava NULL (como antes);
  * edicao limpa -> o proximo sync volta a preencher;
  * ARMADILHA da ordem: o resultado e o mesmo com as atribuicoes trocadas;
- `set_manual_wellness`: INSERT (cria linha) x UPDATE (so limpar), origem,
  `synced_at` preservado;
- faixas de validacao;
- PATCH /integrations/intervals/wellness/{date}: 422 (faixa, corpo vazio,
  campo extra, data futura), so a linha do usuario autenticado;
- leitura (`_cached_wellness_trend`) expoe a origem;
- plano da migracao 069.

Rodar: cd api && python -m pytest tests/unit/test_intervals_wellness_edicao_manual.py
"""

from __future__ import annotations

import functools
import re
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import intervals_integration as ii
from app import intervals_store as store
from app import migrate as m
from app.security import Identity, get_current_identity
from tests.support.fake_db import LoopingExecuteMany

# ---------------------------------------------------------------------------
# Avaliador do ON DUPLICATE KEY UPDATE (subconjunto usado pelas metricas
# editaveis: IF, AND, IS NULL, =, VALUES(col), literal e coluna).
# ---------------------------------------------------------------------------

EDITABLE = ("resting_hr_bpm", "resting_hr_source", "hrv_ms", "hrv_source")


def _split_top(text: str, sep: str = ",") -> list[str]:
    parts, depth, quote, current = [], 0, False, ""
    for ch in text:
        if ch == "'":
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth -= 1
        if ch == sep and depth == 0 and not quote:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _term(expr: str, row: dict, new: dict):
    expr = expr.strip()
    match = re.fullmatch(r"VALUES\((\w+)\)", expr)
    if match:
        return new[match.group(1)]
    if expr.startswith("'") and expr.endswith("'"):
        return expr[1:-1]
    if re.fullmatch(r"\w+", expr):
        return row[expr]
    match = re.fullmatch(r"IF\((.*)\)", expr, re.S)
    if match:
        cond, then, other = _split_top(match.group(1))
        return _term(then, row, new) if _cond(cond, row, new) else _term(other, row, new)
    raise AssertionError(f"expressao nao suportada pelo avaliador: {expr}")


def _cond(cond: str, row: dict, new: dict) -> bool:
    result = True
    for part in re.split(r"\s+AND\s+", cond.strip()):
        if part.endswith(" IS NULL"):
            result = result and _term(part[: -len(" IS NULL")], row, new) is None
        else:
            left, right = part.split(" = ")
            left_value = _term(left, row, new)
            result = result and left_value is not None and left_value == _term(right, row, new)
    return result


@functools.lru_cache(maxsize=1)
def _editable_assignments() -> list[tuple[str, str]]:
    sql = " ".join(store._UPSERT_WELLNESS_SQL.split())
    update = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
    pairs = []
    for item in _split_top(update):
        column, expr = item.split(" = ", 1)
        if column.strip() in EDITABLE:
            pairs.append((column.strip(), expr.strip()))
    return pairs


def _apply(row: dict, new: dict, assignments) -> dict:
    """Semantica do MySQL: em ordem, e cada atribuicao enxerga as anteriores."""
    current = dict(row)
    for column, expr in assignments:
        current[column] = _term(expr, current, new)
    return current


def _sync(row: dict, resting_hr=None, hrv=None, *, reverse=False) -> dict:
    new = {"resting_hr_bpm": resting_hr, "hrv_ms": hrv}
    assignments = _editable_assignments()
    if reverse:
        # troca a ordem DENTRO de cada par (valor, origem) e entre os pares
        assignments = list(reversed(assignments))
    return _apply(row, new, assignments)


def _row(resting_hr, resting_src, hrv, hrv_src) -> dict:
    return {"resting_hr_bpm": resting_hr, "resting_hr_source": resting_src, "hrv_ms": hrv, "hrv_source": hrv_src}


def test_o_sql_tem_as_quatro_atribuicoes_editaveis():
    assert [column for column, _ in _editable_assignments()] == list(EDITABLE)


@pytest.mark.parametrize("reverse", [False, True], ids=["ordem-do-codigo", "ordem-trocada"])
def test_intervals_com_valor_sobrescreve_a_edicao(reverse):
    out = _sync(_row(48.0, "manual", 70.0, "manual"), resting_hr=52.0, hrv=65.0, reverse=reverse)
    assert out == _row(52.0, "intervals", 65.0, "intervals")


@pytest.mark.parametrize("reverse", [False, True], ids=["ordem-do-codigo", "ordem-trocada"])
def test_intervals_sem_valor_mantem_a_edicao(reverse):
    out = _sync(_row(48.0, "manual", 70.0, "manual"), resting_hr=None, hrv=None, reverse=reverse)
    assert out == _row(48.0, "manual", 70.0, "manual")


@pytest.mark.parametrize("reverse", [False, True], ids=["ordem-do-codigo", "ordem-trocada"])
def test_intervals_sem_valor_e_origem_intervals_grava_null_como_antes(reverse):
    out = _sync(_row(55.0, "intervals", 60.0, "intervals"), resting_hr=None, hrv=None, reverse=reverse)
    assert out == _row(None, "intervals", None, "intervals")


@pytest.mark.parametrize("reverse", [False, True], ids=["ordem-do-codigo", "ordem-trocada"])
def test_metricas_independentes_uma_manual_outra_do_intervals(reverse):
    out = _sync(_row(48.0, "manual", 60.0, "intervals"), resting_hr=None, hrv=62.0, reverse=reverse)
    assert out == _row(48.0, "manual", 62.0, "intervals")


def test_edicao_limpa_volta_a_ser_preenchida_pelo_sync():
    """Limpar = valor NULL + origem 'intervals' (o que `set_manual_wellness`
    grava com `null`); o sync seguinte preenche ou deixa NULL."""
    limpa = _row(None, "intervals", None, "intervals")
    assert _sync(limpa, resting_hr=51.0, hrv=None) == _row(51.0, "intervals", None, "intervals")


def test_armadilha_ordem_a_origem_nao_le_o_valor_atual():
    """O que torna o UPDATE independente da ordem: a expressao da origem so le
    `VALUES(valor)` e a propria origem - nunca o valor ja trocado."""
    for column, expr in _editable_assignments():
        if column.endswith("_source"):
            value_column = column.replace("_source", "_bpm") if column.startswith("resting") else "hrv_ms"
            bare = re.sub(rf"VALUES\({value_column}\)", "", expr)
            assert value_column not in bare, expr


def test_um_update_ingenuo_dependeria_da_ordem():
    """Prova de que o avaliador pega a armadilha: uma versao "obvia" em que o
    valor decide pela origem JA atualizada muda de resultado ao trocar a
    ordem - a do codigo nao (testes parametrizados acima)."""
    naive = [
        ("resting_hr_source", "IF(VALUES(resting_hr_bpm) IS NULL, resting_hr_source, 'intervals')"),
        ("resting_hr_bpm", "IF(resting_hr_source = 'manual', resting_hr_bpm, VALUES(resting_hr_bpm))"),
    ]
    row = _row(48.0, "manual", None, "intervals")
    new = {"resting_hr_bpm": 52.0, "hrv_ms": None}
    assert _apply(row, new, naive)["resting_hr_bpm"] == 52.0
    assert _apply(row, new, list(reversed(naive)))["resting_hr_bpm"] == 48.0  # bug: edicao nao sobrescrita


# ---------------------------------------------------------------------------
# set_manual_wellness (statements)
# ---------------------------------------------------------------------------


class FakeCursor(LoopingExecuteMany):
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=None):
        self.conn.statements.append((" ".join(sql.split()), tuple(params or ())))

    def fetchone(self):
        return self.conn.row


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.statements: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def cursor(self):
        return FakeCursor(self)


def test_editar_um_valor_faz_insert_que_cria_ou_atualiza_a_linha():
    conn = FakeConn(row={"wellness_date": "2026-10-07"})
    store.set_manual_wellness(conn, 7, "2026-10-07", {"resting_hr_bpm": 48.0})
    (sql, params), (select_sql, select_params) = conn.statements
    assert sql.startswith("INSERT INTO intervals_wellness (wellness_date, user_id, resting_hr_bpm, resting_hr_source, manual_updated_at)")
    assert params == ("2026-10-07", 7, 48.0, "manual")
    assert "resting_hr_bpm = VALUES(resting_hr_bpm)" in sql and "resting_hr_source = VALUES(resting_hr_source)" in sql
    assert "synced_at = synced_at" in sql, "edicao manual nao e sync: nao mexe no throttle"
    assert "hrv" not in sql, "so o campo enviado muda"
    assert select_params == (7, "2026-10-07")


def test_editar_um_e_limpar_outro_no_mesmo_pedido():
    conn = FakeConn()
    store.set_manual_wellness(conn, 7, "2026-10-07", {"resting_hr_bpm": 48.0, "hrv_ms": None})
    sql, params = conn.statements[0]
    assert sql.startswith("INSERT INTO intervals_wellness")
    assert params == ("2026-10-07", 7, 48.0, "manual", None, "intervals")


def test_so_limpar_nao_cria_linha_vazia():
    conn = FakeConn()
    store.set_manual_wellness(conn, 7, "2026-10-07", {"hrv_ms": None})
    sql, params = conn.statements[0]
    assert sql.startswith("UPDATE intervals_wellness SET hrv_ms = %s, hrv_source = %s")
    assert sql.endswith("WHERE user_id = %s AND wellness_date = %s")
    assert params == (None, "intervals", 7, "2026-10-07")


def test_metrica_desconhecida_ou_vazia_e_recusada():
    with pytest.raises(store.ManualWellnessError):
        store.set_manual_wellness(FakeConn(), 7, "2026-10-07", {"sleep_seconds": 1})
    with pytest.raises(store.ManualWellnessError):
        store.set_manual_wellness(FakeConn(), 7, "2026-10-07", {})


@pytest.mark.parametrize(
    "metric,value,ok",
    [
        ("resting_hr_bpm", store.MANUAL_RESTING_HR_BPM_MIN, True),
        ("resting_hr_bpm", store.MANUAL_RESTING_HR_BPM_MAX, True),
        ("resting_hr_bpm", store.MANUAL_RESTING_HR_BPM_MIN - 1, False),
        ("resting_hr_bpm", store.MANUAL_RESTING_HR_BPM_MAX + 1, False),
        ("hrv_ms", store.MANUAL_HRV_MS_MIN, True),
        ("hrv_ms", store.MANUAL_HRV_MS_MAX, True),
        ("hrv_ms", 0, False),
        ("hrv_ms", store.MANUAL_HRV_MS_MAX + 0.5, False),
        ("hrv_ms", None, True),
        ("hrv_ms", float("nan"), False),
        ("hrv_ms", True, False),
    ],
)
def test_faixas_de_validacao(metric, value, ok):
    if ok:
        store.validate_manual_wellness_value(metric, value)
    else:
        with pytest.raises(store.ManualWellnessError):
            store.validate_manual_wellness_value(metric, value)


# ---------------------------------------------------------------------------
# PATCH /integrations/intervals/wellness/{date}
# ---------------------------------------------------------------------------

HOJE = date(2026, 10, 8)


@pytest.fixture
def patch_env(monkeypatch):
    env = {"calls": [], "conn": FakeConn()}

    def fake_set_manual(conn, user_id, wellness_date, values):
        env["calls"].append((user_id, wellness_date, values))
        return {
            "wellness_date": date.fromisoformat(wellness_date),
            "resting_hr_bpm": values.get("resting_hr_bpm"),
            "resting_hr_source": "manual" if values.get("resting_hr_bpm") is not None else "intervals",
            "hrv_ms": values.get("hrv_ms"),
            "hrv_source": "manual" if values.get("hrv_ms") is not None else "intervals",
            "manual_updated_at": None,
        }

    monkeypatch.setattr(ii, "_connect", lambda: env["conn"])
    monkeypatch.setattr(ii, "_user_today", lambda conn, user_id: HOJE)
    monkeypatch.setattr(store, "set_manual_wellness", fake_set_manual)

    app = FastAPI()
    app.include_router(ii.router)
    env["identity"] = Identity(user_id=7, role="regular", source="session")
    app.dependency_overrides[get_current_identity] = lambda: env["identity"]
    env["client"] = TestClient(app)
    return env


def _patch(env, day, body):
    return env["client"].patch(f"/integrations/intervals/wellness/{day}", json=body)


def test_patch_grava_so_o_campo_enviado_na_linha_do_usuario(patch_env):
    response = _patch(patch_env, "2026-10-07", {"resting_hr_bpm": 48})
    assert response.status_code == 200
    assert patch_env["calls"] == [(7, "2026-10-07", {"resting_hr_bpm": 48.0})]
    item = response.json()["item"]
    assert item == {
        "wellness_date": "2026-10-07",
        "resting_hr_bpm": 48.0,
        "resting_hr_source": "manual",
        "hrv_ms": None,
        "hrv_source": "intervals",
        "manual_updated_at": None,
    }


def test_patch_null_explicito_limpa(patch_env):
    assert _patch(patch_env, "2026-10-07", {"hrv_ms": None}).status_code == 200
    assert patch_env["calls"] == [(7, "2026-10-07", {"hrv_ms": None})]


def test_patch_hoje_e_aceito(patch_env):
    assert _patch(patch_env, HOJE.isoformat(), {"hrv_ms": 60}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"resting_hr_bpm": store.MANUAL_RESTING_HR_BPM_MAX + 1},
        {"resting_hr_bpm": store.MANUAL_RESTING_HR_BPM_MIN - 1},
        {"hrv_ms": 0},
        {"hrv_ms": store.MANUAL_HRV_MS_MAX + 1},
        {},
        {"resting_hr_bpm": "abc"},
        {"sleep_seconds": 100},
    ],
    ids=["fc-alta", "fc-baixa", "hrv-zero", "hrv-alto", "vazio", "texto", "campo-extra"],
)
def test_patch_invalido_e_422_e_nao_grava(patch_env, body):
    response = _patch(patch_env, "2026-10-07", body)
    assert response.status_code == 422
    assert patch_env["calls"] == []


def test_patch_mensagem_da_faixa_vem_do_backend(patch_env):
    response = _patch(patch_env, "2026-10-07", {"resting_hr_bpm": 300})
    assert response.json()["detail"] == "resting_hr_bpm deve estar entre 20 e 250"


def test_patch_data_futura_e_422(patch_env):
    response = _patch(patch_env, (HOJE + timedelta(days=1)).isoformat(), {"hrv_ms": 60})
    assert response.status_code == 422
    assert patch_env["calls"] == []


def test_patch_data_invalida_e_422(patch_env):
    assert _patch(patch_env, "2026-13-40", {"hrv_ms": 60}).status_code == 422


def test_patch_nao_aceita_user_id_no_corpo(patch_env):
    """Isolamento: a linha e sempre a do usuario autenticado; o corpo nao
    escolhe usuario."""
    response = _patch(patch_env, "2026-10-07", {"hrv_ms": 60, "user_id": 99})
    assert response.status_code == 422
    patch_env["identity"] = Identity(user_id=8, role="regular", source="session")
    assert _patch(patch_env, "2026-10-07", {"hrv_ms": 60}).status_code == 200
    assert patch_env["calls"] == [(8, "2026-10-07", {"hrv_ms": 60.0})]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def test_cached_wellness_trend_expoe_a_origem(monkeypatch):
    rows = [
        {"wellness_date": "2026-10-01", "resting_hr_bpm": 48, "resting_hr_source": "manual",
         "hrv_ms": 60, "hrv_source": "intervals"},
        {"wellness_date": "2026-10-02", "resting_hr_bpm": None, "hrv_ms": None},
    ]
    captured = {}

    def fake_fetch_all(sql, params=None):
        captured["sql"] = " ".join(sql.split())
        return rows

    monkeypatch.setattr(ii, "_fetch_all", fake_fetch_all)
    trend = ii._cached_wellness_trend(7, 30)
    assert "resting_hr_source" in captured["sql"] and "hrv_source" in captured["sql"]
    assert [(p["restingHrSource"], p["hrvSource"]) for p in trend] == [("manual", "intervals"), ("intervals", "intervals")]


# ---------------------------------------------------------------------------
# Migracao 069
# ---------------------------------------------------------------------------

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "069_intervals_wellness_manual_source.py"
ENUM = "enum('intervals','manual')"


@functools.lru_cache(maxsize=1)
def _mig() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class SchemaDB:
    def __init__(self, columns: dict | None, dry_run=False):
        self.columns = columns  # None = tabela ausente
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [] if self.columns is None else [{"TABLE_NAME": "intervals_wellness"}]
        if "information_schema.COLUMNS" in sql:
            return [{"COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nul} for n, (t, nul) in self.columns.items()]
        raise AssertionError(sql)

    def execute(self, sql, params=None):
        raise AssertionError("o plano nao escreve")


CONVERGIDO = {"resting_hr_source": (ENUM, "NO"), "hrv_source": (ENUM, "NO"), "manual_updated_at": ("timestamp", "YES")}


def test_069_producao_hoje_adiciona_tres_colunas_com_default_intervals():
    plano = [sql for sql, _ in _mig()["plan"](SchemaDB({}))]
    assert plano == [_mig()["ADD_RESTING_HR_SOURCE_SQL"], _mig()["ADD_HRV_SOURCE_SQL"], _mig()["ADD_MANUAL_UPDATED_AT_SQL"]]
    assert all("DEFAULT 'intervals'" in sql for sql in plano[:2])


def test_069_convergido_e_zero_statements():
    assert _mig()["plan"](SchemaDB(dict(CONVERGIDO))) == []


def test_069_parcial_so_o_que_falta():
    parcial = {"resting_hr_source": (ENUM, "NO")}
    assert [sql for sql, _ in _mig()["plan"](SchemaDB(parcial))] == [
        _mig()["ADD_HRV_SOURCE_SQL"],
        _mig()["ADD_MANUAL_UPDATED_AT_SQL"],
    ]


def test_069_tipo_incompativel_falha_antes_de_executar():
    errado = {**CONVERGIDO, "hrv_source": ("varchar(20)", "NO")}
    with pytest.raises(Exception, match="069"):
        _mig()["plan"](SchemaDB(errado))


def test_069_sem_tabela_falha_na_execucao_real_e_planeja_no_dry_run():
    with pytest.raises(Exception, match="027"):
        _mig()["plan"](SchemaDB(None))
    assert len(_mig()["plan"](SchemaDB(None, dry_run=True))) == 3


def test_069_down_solta_as_colunas_presentes():
    assert [sql for sql, _ in _mig()["plan_down"](SchemaDB(dict(CONVERGIDO)))] == [
        "ALTER TABLE intervals_wellness DROP COLUMN manual_updated_at",
        "ALTER TABLE intervals_wellness DROP COLUMN hrv_source",
        "ALTER TABLE intervals_wellness DROP COLUMN resting_hr_source",
    ]
