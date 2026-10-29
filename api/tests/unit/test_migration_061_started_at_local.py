"""Plano da migração 061 (activities.started_at/ended_at gravados em UTC pelo
Modo Treino -> hora de parede do usuário).

Sem banco: um `_RecordingDB` (mesmo papel do das migrações 059/060) responde
à leitura das candidatas e registra o que seria executado. O filtro SQL
(prefixo, data mínima, |created_at - started_at| < 120 s) é conferido no
texto; o teste alimenta só linhas que o banco já devolveria.

Rodar: cd api && python -m pytest tests/unit/test_migration_061_started_at_local.py
"""

from __future__ import annotations

import datetime as _dt
import functools

from app import migrate as m

MIGRATION_061 = m.DEFAULT_MIGRATIONS_DIR / "061_activities_started_at_hora_local.py"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_061).__globals__


_ACTIVITY_COLUMNS = ("id", "user_id", "external_id", "started_at", "ended_at", "local_date", "created_at")


def _schema(*, timezone: bool = True, activity_columns=_ACTIVITY_COLUMNS) -> list[dict]:
    rows = [{"TABLE_NAME": "activities", "COLUMN_NAME": c} for c in activity_columns]
    rows.append({"TABLE_NAME": "users", "COLUMN_NAME": "id"})
    if timezone:
        rows.append({"TABLE_NAME": "users", "COLUMN_NAME": "timezone"})
    return rows


class _RecordingDB:
    """Responde information_schema com `schema` e a leitura das candidatas
    com `rows`; registra execute()."""

    def __init__(self, rows, *, dry_run: bool = False, schema=None):
        self.rows = rows
        self.dry_run = dry_run
        self.schema = _schema() if schema is None else schema
        self.queries: list[tuple[str, tuple | None]] = []
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        g = _module()
        if sql == g["COLUMNS_SQL"]:
            assert params == ("activities", "users")
            return list(self.schema)
        self.queries.append((sql, params))
        assert sql in (g["CANDIDATES_SQL"], g["CANDIDATES_NO_TIMEZONE_SQL"])
        return list(self.rows)

    def query_one(self, sql, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        return 1


def _row(id_, started, ended=None, local_date=None, tz="America/Sao_Paulo"):
    return {
        "id": id_,
        "started_at": started,
        "ended_at": ended,
        "local_date": local_date if local_date is not None else started.date(),
        "timezone": tz,
    }


def _dt_(*args):
    return _dt.datetime(*args)


def test_filtro_das_candidatas():
    g = _module()
    sql = g["CANDIDATES_SQL"]
    assert "a.external_id LIKE %s" in sql
    assert "a.started_at IS NOT NULL" in sql
    assert "a.local_date >= %s" in sql
    assert "ABS(TIMESTAMPDIFF(SECOND, a.created_at, a.started_at)) < %s" in sql
    assert "JOIN users u ON u.id = a.user_id" in sql
    db = _RecordingDB([])
    g["plan"](db)
    assert db.queries[0][1] == ("zepp-session-%", "2026-09-01", 120)


def test_prefixo_e_o_gerado_pelo_start_session():
    import inspect

    from app import workout_sessions

    assert 'f"zepp-session-' in inspect.getsource(workout_sessions.start_session)


def test_sessao_utc_volta_3h_em_started_e_ended():
    row = _row(219, _dt_(2026, 9, 15, 12, 10, 0), ended=_dt_(2026, 9, 15, 13, 20, 0))
    db = _RecordingDB([row])
    _module()["up"](db)
    assert db.executed == [
        (
            "UPDATE activities SET started_at = %s, ended_at = %s, local_date = %s WHERE id = %s AND started_at = %s",
            (_dt_(2026, 9, 15, 9, 10), _dt_(2026, 9, 15, 10, 20), _dt.date(2026, 9, 15), 219, _dt_(2026, 9, 15, 12, 10)),
        )
    ]


def test_treino_depois_das_21h_volta_para_o_dia_certo():
    row = _row(230, _dt_(2026, 9, 26, 1, 0), ended=_dt_(2026, 9, 26, 2, 0))
    params = _module()["plan"](_RecordingDB([row]))[0][1]
    assert params[0] == _dt_(2026, 9, 25, 22, 0)
    assert params[2] == _dt.date(2026, 9, 25)


def test_local_date_editado_a_mao_e_preservado():
    row = _row(225, _dt_(2026, 9, 20, 12, 0), local_date=_dt.date(2026, 9, 19))
    params = _module()["plan"](_RecordingDB([row]))[0][1]
    assert params[0] == _dt_(2026, 9, 20, 9, 0)
    assert params[2] == _dt.date(2026, 9, 19)


def test_ended_at_nulo_fica_nulo():
    row = _row(226, _dt_(2026, 9, 21, 12, 0))
    params = _module()["plan"](_RecordingDB([row]))[0][1]
    assert params[1] is None


def test_ended_at_de_outro_relogio_nao_e_tocado():
    # Antes do started_at antigo (editado à mão) e mais de 24 h depois.
    antes = _row(227, _dt_(2026, 9, 22, 12, 0), ended=_dt_(2026, 9, 22, 10, 0))
    longe = _row(228, _dt_(2026, 9, 22, 12, 0), ended=_dt_(2026, 9, 24, 12, 0))
    statements = _module()["plan"](_RecordingDB([antes, longe]))
    assert statements[0][1][1] == _dt_(2026, 9, 22, 10, 0)
    assert statements[1][1][1] == _dt_(2026, 9, 24, 12, 0)


def test_usuario_utc_nao_gera_update():
    row = _row(229, _dt_(2026, 9, 22, 12, 0), tz="UTC")
    assert _module()["plan"](_RecordingDB([row])) == []


def test_fuso_nulo_ou_invalido_usa_sao_paulo():
    rows = [_row(231, _dt_(2026, 9, 23, 12, 0), tz=None), _row(232, _dt_(2026, 9, 23, 12, 0), tz="Fuso/Quebrado")]
    statements = _module()["plan"](_RecordingDB(rows))
    assert [p[0] for _, p in statements] == [_dt_(2026, 9, 23, 9, 0)] * 2


def test_valores_em_texto_da_data_api_sao_aceitos():
    row = {"id": 233, "started_at": "2026-09-24 12:00:00", "ended_at": "2026-09-24 13:00:00",
           "local_date": "2026-09-24", "timezone": "America/Sao_Paulo"}
    params = _module()["plan"](_RecordingDB([row]))[0][1]
    assert params[:3] == (_dt_(2026, 9, 24, 9, 0), _dt_(2026, 9, 24, 10, 0), _dt.date(2026, 9, 24))
    assert params[4] == _dt_(2026, 9, 24, 12, 0)


def test_reexecucao_sem_candidatas_nao_escreve_nada():
    # Depois de aplicada, created_at - started_at ~ 3 h: o filtro SQL não devolve mais a linha.
    db = _RecordingDB([])
    _module()["up"](db)
    assert db.executed == []


def test_dry_run_so_planeja():
    rows = [_row(234, _dt_(2026, 9, 29, 12, 0)), _row(235, _dt_(2026, 9, 30, 12, 0))]
    db = _RecordingDB(rows, dry_run=True)
    statements = _module()["plan"](db)
    assert len(statements) == 2
    assert db.executed == []


def test_sem_down_de_dados():
    g = _module()
    assert "down" not in g and "plan_down" not in g


# --- banco parcial (DRY-RUN de lote / testes de migração) ---------------------


def test_sem_coluna_users_timezone_le_sem_join_e_usa_sao_paulo():
    """044 pendente: `users.timezone` não existe. A leitura não pode citar
    `u.timezone` (MySQL 1054) e todas as linhas usam America/Sao_Paulo."""
    g = _module()
    row = _row(1, _dt_(2026, 9, 20, 12, 0, 0), _dt_(2026, 9, 20, 13, 0, 0), tz=None)
    db = _RecordingDB([row], schema=_schema(timezone=False))
    statements = g["plan"](db)
    assert len(db.queries) == 1
    sql, params = db.queries[0]
    assert sql == g["CANDIDATES_NO_TIMEZONE_SQL"]
    assert "u.timezone" not in sql and "JOIN users" not in sql
    assert params == ("zepp-session-%", "2026-09-01", 120)
    assert len(statements) == 1
    new_started, new_ended, new_local_date, id_, old_started = statements[0][1]
    assert new_started == _dt_(2026, 9, 20, 9, 0, 0)
    assert new_ended == _dt_(2026, 9, 20, 10, 0, 0)
    assert id_ == 1 and old_started == _dt_(2026, 9, 20, 12, 0, 0)


def test_com_coluna_users_timezone_usa_join():
    g = _module()
    db = _RecordingDB([])
    g["plan"](db)
    assert db.queries[0][0] == g["CANDIDATES_SQL"]


def test_sem_tabela_activities_nao_le_nem_planeja():
    g = _module()
    db = _RecordingDB([], schema=_schema(activity_columns=()))
    assert g["plan"](db) == []
    assert db.queries == []


def test_activities_sem_coluna_do_filtro_nao_le_nem_planeja():
    g = _module()
    cols = tuple(c for c in _ACTIVITY_COLUMNS if c != "external_id")
    db = _RecordingDB([], schema=_schema(activity_columns=cols))
    g["up"](db)
    assert db.queries == [] and db.executed == []


def test_information_schema_em_bytes_e_maiusculas():
    """Data API pode devolver bytes; MySQL pode devolver nomes em caixa alta."""
    g = _module()
    schema = [
        {"TABLE_NAME": b"ACTIVITIES", "COLUMN_NAME": c.upper().encode()} for c in _ACTIVITY_COLUMNS
    ] + [{"TABLE_NAME": b"users", "COLUMN_NAME": b"timezone"}]
    db = _RecordingDB([], schema=schema)
    g["plan"](db)
    assert db.queries[0][0] == g["CANDIDATES_SQL"]
