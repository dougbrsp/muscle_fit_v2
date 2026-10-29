"""`app/intelligence/dashboard/exposure_service.py` sem banco: cursor fake que
registra as queries.

O que estes testes protegem (plano §15 e §8):

- UMA query por leitura de exposicoes (sem N+1);
- cap de 30 exposicoes, pelo limite de 1 MB da resposta da Data API;
- so series `working` e id canonico resolvido pelo mapa de ids legados - os
  dois no SQL, nao no Python;
- `user_id` OBRIGATORIO no WHERE (`require_user_id`): nenhum caminho aceita
  `None`;
- e1RM da funcao vigente (`epley-rir-v1`), `None` sem RPE;
- recordes so dentro da MESMA `comparability_key`.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import load_conventions
from app.domain import e1rm as e1rm_dono
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard.contracts import WindowInfo, WindowKind
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus
from app.intelligence.muscle_recovery_service import MuscleRecoveryService

JANELA = WindowInfo(
    kind=WindowKind.ROLLING_90D,
    date_from=date(2026, 6, 24),
    date_to=date(2026, 9, 22),
    timezone="America/Sao_Paulo",
    days=90,
)

USUARIO = 7
OUTRO_USUARIO = 8
EXERCICIO = 1001
MAQUINA = 11


@pytest.fixture(autouse=True)
def _sem_cache_de_coluna():
    """A deteccao de `load_convention` (migracao 045) e cacheada POR PROCESSO;
    cada teste comeca sem ela para nao herdar a resposta de outro fake."""
    load_conventions.reset_load_convention_column_cache()
    yield
    load_conventions.reset_load_convention_column_cache()


def linha(
    *,
    exercise_row_id: int,
    activity_id: int,
    dia: int,
    set_number: int,
    reps: int | None = 10,
    weight: float | None = 40.0,
    rpe: float | None = 8.0,
    equipment_id: int | None = MAQUINA,
    convention: str | None = None,
    reps_target: str | None = "10-12",
) -> dict:
    return {
        "exercise_row_id": exercise_row_id,
        "activity_id": activity_id,
        "local_date": date(2026, 9, dia),
        "started_at": datetime(2026, 9, dia, 19, 0, 0),
        "position_in_session": 2,
        "equipment_id": equipment_id,
        "equipment_name": "Leg press" if equipment_id else None,
        "load_convention": convention,
        "prescribed_reps_target": reps_target,
        "set_number": set_number,
        "reps": reps,
        "weight_kg": weight,
        "rpe": rpe,
        "rest_seconds": 90,
        "notes": None,
    }


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.db.executed.append((normalized, tuple(params)))
        self._result = self.db.respond(normalized, tuple(params))

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeDb:
    def __init__(self, rows=(), *, exercicio=True, decisions=(), tem_coluna=True, schema="musclefit"):
        self.executed: list[tuple[str, tuple]] = []
        self.rows = list(rows)
        self.exercicio = exercicio
        self.decisions = list(decisions)
        self.tem_coluna = tem_coluna
        self.schema = schema

    def cursor(self):
        return FakeCursor(self)

    def respond(self, sql, params):
        if sql.startswith("SELECT DATABASE()"):
            return [{"db": self.schema}]
        if "information_schema.COLUMNS" in sql:
            # migracao 045 aplicada (o caso contrario tem teste proprio)
            return [{"n": 1 if self.tem_coluna else 0}]
        if sql.startswith("SELECT sel.exercise_row_id"):
            return self.rows
        if "FROM exercise_catalog ec" in sql:
            if not self.exercicio:
                return []
            return [{"exercise_id": EXERCICIO, "exercise_name": "Leg press", "exercise_amazfit_name": "Leg Press"}]
        if "FROM progression_decisions" in sql:
            return self.decisions
        return []

    def queries(self, prefixo):
        return [q for q, _ in self.executed if q.startswith(prefixo)]


# ---------------------------------------------------------------------------
# A query
# ---------------------------------------------------------------------------


def _queries_de_dados(db) -> list[str]:
    """Queries de negocio: fora a deteccao da coluna 045 (`SELECT DATABASE()`
    + `information_schema`), que e infraestrutura."""
    return [
        sql
        for sql, _params in db.executed
        if "information_schema" not in sql and not sql.startswith("SELECT DATABASE()")
    ]


def test_uma_unica_query_para_todas_as_exposicoes():
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=n)
        for n in (1, 2, 3)
    ] + [linha(exercise_row_id=2, activity_id=101, dia=8, set_number=n) for n in (1, 2)]
    db = FakeDb(rows)

    exposicoes = svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA)

    queries = _queries_de_dados(db)
    assert len([sql for sql in queries if sql.startswith("SELECT sel.exercise_row_id")]) == 1, "uma query de exposicoes"
    # + contexto de fadiga em lote (sessoes envolvidas + resolvedor de
    # contribuicoes): custo FIXO, nunca por exposicao (sem N+1).
    assert len(queries) <= 4
    assert len(exposicoes) == 2
    assert [len(item["sets"]) for item in exposicoes] == [3, 2]


def test_a_deteccao_da_coluna_045_acontece_uma_vez_por_schema():
    """A consulta cara (`information_schema`) e cacheada por SCHEMA: nao vira
    uma por chamada, muito menos uma por exposicao."""
    db = FakeDb([])
    svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    checagens = [sql for sql, _p in db.executed if "information_schema" in sql]
    assert len(checagens) == 1
    assert len(_queries_de_dados(db)) == 2


def test_o_cache_da_coluna_045_nao_vaza_entre_schemas():
    """O caso que quebrou o CI: um processo que fala com dois schemas - um com
    a 045 aplicada e outro sem (a suite de integracao cria um schema por
    arquivo). O segundo NAO pode herdar a resposta do primeiro."""
    migrado = FakeDb([], schema="musclefit_run1_test", tem_coluna=True)
    svc.get_exercise_exposures(migrado, EXERCICIO, user_id=USUARIO, window=JANELA)
    assert "eqex.load_convention" in _queries_de_dados(migrado)[0]

    antigo = FakeDb([], schema="musclefit_run2_test", tem_coluna=False)
    svc.get_exercise_exposures(antigo, EXERCICIO, user_id=USUARIO, window=JANELA)
    sql = _queries_de_dados(antigo)[0]
    assert "eqex.load_convention" not in sql
    assert "NULL" in sql

    # e o schema ja visto continua respondido pelo cache, sem consulta nova
    svc.get_exercise_exposures(migrado, EXERCICIO, user_id=USUARIO, window=JANELA)
    checagens = [sql for sql, _p in migrado.executed if "information_schema" in sql]
    assert len(checagens) == 1


def test_sem_a_migracao_045_a_leitura_cai_no_default_total():
    """Codigo publicado ANTES da 045 (ordem de deploy) continua respondendo:
    a coluna vira `NULL`, que ja significa `total`."""
    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, convention=None)]
    db = FakeDb(rows, tem_coluna=False)

    exposicao = svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA)[0]

    sql = _queries_de_dados(db)[0]
    assert "eqex.load_convention" not in sql
    assert "NULL AS load_convention" in sql
    assert exposicao["load_convention"] == "total"
    assert exposicao["comparability_key"]["load_convention"] == "total"


def _params_de_dados(db) -> list[tuple]:
    return [
        params
        for sql, params in db.executed
        if "information_schema" not in sql and not sql.startswith("SELECT DATABASE()")
    ]


def test_o_sql_filtra_usuario_working_e_id_canonico():
    sql = svc.EXPOSURES_SQL_TEMPLATE.format(load_convention="eqex.load_convention")
    assert "a2.user_id = %s" in sql
    assert "COALESCE(elm2.canonical_exercise_id, e2.mapping_id) = %s" in sql
    assert "COALESCE(elm.canonical_exercise_id, e.mapping_id)" in sql
    assert "exercise_legacy_id_map" in sql
    # so series de trabalho (o predicado vem de set_types, nunca literal)
    assert "'working'" in sql
    assert "ge.equipment_id" in sql and "gym_equipment ge" in sql
    assert "equipment_exercises eqex" in sql


def test_parametros_incluem_usuario_janela_e_cap():
    db = FakeDb([])
    svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    params = _params_de_dados(db)[0]
    # M97 F1: o objetivo (default) logo depois do exercicio
    assert params == (USUARIO, EXERCICIO, "hypertrophy", JANELA.date_from, JANELA.date_to, svc.MAX_EXPOSURES)


def test_cap_de_30_exposicoes_pelo_limite_de_1_mb_da_data_api():
    assert svc.MAX_EXPOSURES == 30
    db = FakeDb([])
    svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA, limit=999)
    assert _params_de_dados(db)[-1][-1] == 30
    db = FakeDb([])
    svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA, limit=5)
    assert _params_de_dados(db)[-1][-1] == 5


@pytest.mark.parametrize("invalido", [None, 0, -1, "abc", True])
def test_ownership_user_id_e_obrigatorio(invalido):
    db = FakeDb([])
    with pytest.raises(ValueError):
        svc.get_exercise_exposures(db, EXERCICIO, user_id=invalido, window=JANELA)
    assert db.executed == [], "nenhuma query pode sair sem user_id valido"


def test_outro_usuario_nunca_entra_no_where():
    db = FakeDb([])
    svc.get_exercise_exposures(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    assert all(OUTRO_USUARIO not in params for _sql, params in db.executed)


# ---------------------------------------------------------------------------
# Agregados por exposicao
# ---------------------------------------------------------------------------


def test_agregados_usam_a_funcao_vigente_de_e1rm():
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, reps=10, weight=40.0, rpe=8.0),
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=2, reps=8, weight=45.0, rpe=9.0),
    ]
    exposicao = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)[0]

    assert exposicao["total_reps"] == 18
    assert exposicao["best_load_kg"] == 45.0
    assert exposicao["avg_rpe"] == 8.5
    assert exposicao["tonnage"] == pytest.approx(10 * 40.0 + 8 * 45.0)

    esperado = max(
        MuscleRecoveryService.estimated_1rm(40.0, 10, 8.0),
        MuscleRecoveryService.estimated_1rm(45.0, 8, 9.0),
    )
    assert exposicao["e1rm"] == pytest.approx(esperado)


def test_sem_rpe_o_e1rm_e_none_e_nao_zero():
    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, rpe=None)]
    exposicao = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)[0]
    assert exposicao["e1rm"] is None
    assert exposicao["avg_rpe"] is None
    assert exposicao["total_reps"] == 10  # o que foi observado continua observado


def test_convencao_nula_vira_total_e_marca_a_origem():
    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, convention=None)]
    exposicao = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)[0]
    assert exposicao["load_convention"] == "total"
    assert exposicao["load_convention_source"] == "default_total"

    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, convention="per_side")]
    exposicao = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)[0]
    assert exposicao["load_convention"] == "per_side"
    assert exposicao["load_convention_source"] == "equipment_exercises"


def test_assistido_tem_como_melhor_carga_a_menor():
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, weight=30.0, convention="assisted"),
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=2, weight=20.0, convention="assisted"),
    ]
    exposicao = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)[0]
    assert exposicao["best_load_kg"] == 20.0
    assert exposicao["e1rm"] is None  # convencao sem carga levantada
    # M58: quilos de ASSISTENCIA nao sao tonelagem (mesma regra do relatorio).
    assert exposicao["tonnage"] is None


@pytest.mark.parametrize(
    "convention,esperado",
    [(None, 400.0), ("total", 400.0), ("per_side", 400.0), ("assisted", None), ("bodyweight", None)],
)
def test_m58_tonelagem_da_exposicao_segue_a_regra_compartilhada(convention, esperado):
    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, convention=convention)]
    exposicao = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)[0]
    assert exposicao["tonnage"] == esperado


def test_todas_sem_equipamento_liberam_a_chave_agnostica():
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, equipment_id=None),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=1, equipment_id=None),
    ]
    exposicoes = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert all(item["comparability_key"]["equipment_agnostic"] for item in exposicoes)

    # basta UMA com equipamento para a excecao deixar de valer
    rows[1]["equipment_id"] = MAQUINA
    exposicoes = svc.get_exercise_exposures(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert not any(item["comparability_key"]["equipment_agnostic"] for item in exposicoes)


# ---------------------------------------------------------------------------
# Resposta completa
# ---------------------------------------------------------------------------


def test_exercicio_inexistente_devolve_none_para_o_404():
    db = FakeDb([], exercicio=False)
    assert svc.build_exercise_progress(db, 999, user_id=USUARIO, window=JANELA) is None


def test_exercicio_sem_historico_responde_insufficient_data_e_nao_zeros():
    payload = svc.build_exercise_progress(FakeDb([]), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload is not None
    assert payload["status"] == ExerciseProgressionStatus.INSUFFICIENT_DATA.value
    assert payload["exposures"] == []
    assert payload["comparability_key"] is None
    assert payload["records"] == {"best_e1rm": None, "best_load_kg": None}
    assert payload["data_quality"].value == "INSUFFICIENT"


def test_a_resposta_nao_vaza_campos_internos():
    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1)]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    for exposicao in payload["exposures"]:
        assert not [chave for chave in exposicao if chave.startswith("_")]


def test_recordes_ficam_dentro_da_mesma_chave():
    """Uma carga maior em OUTRA maquina nao vira recorde desta."""
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, weight=100.0, equipment_id=99),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=1, weight=40.0, equipment_id=MAQUINA),
        linha(exercise_row_id=3, activity_id=102, dia=15, set_number=1, weight=45.0, equipment_id=MAQUINA),
    ]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["records"]["best_load_kg"]["value"] == 45.0
    assert payload["records"]["best_load_kg"]["activity_id"] == 102


def test_recorde_de_e1rm_so_com_serie_confiavel_ate_12_reps():
    """M76 Lote 3a: a série de 20 reps tem o maior Epley, mas não é recorde -
    vale o maior e1RM das séries com até 12 reps (a 10 reps da mesma
    exposição ou de outra)."""
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, reps=10, weight=40.0, rpe=10.0),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=1, reps=20, weight=40.0, rpe=10.0),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=2, reps=12, weight=40.0, rpe=10.0),
        linha(exercise_row_id=3, activity_id=102, dia=15, set_number=1, reps=13, weight=45.0, rpe=10.0),
    ]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    recorde = payload["records"]["best_e1rm"]
    assert recorde["activity_id"] == 101
    assert recorde["value"] == pytest.approx(40.0 * (1 + 12 / 30))
    assert recorde["reliable"] is True
    # o e1RM da exposição continua o maior de todas as séries (a regra trava por e1rm_reps)
    assert payload["exposures"][1]["e1rm"] == pytest.approx(40.0 * (1 + 20 / 30))
    assert payload["exposures"][1]["e1rm_reliable"] is False


def test_sem_serie_confiavel_mostra_o_maior_e1rm_nao_confiavel():
    """M76 Lote 3d (decisão de tela 2026-10-07): sem nenhum e1RM confiável na
    chave, o recorde traz o maior e1RM das séries com mais de 12 reps,
    marcado `reliable = False`."""
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, reps=15, weight=40.0, rpe=9.0),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=1, reps=20, weight=40.0, rpe=10.0),
    ]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    recorde = payload["records"]["best_e1rm"]
    assert recorde["reliable"] is False
    assert recorde["max_reliable_reps"] == e1rm_dono.E1RM_MAX_RELIABLE_REPS
    assert recorde["activity_id"] == 101
    assert recorde["value"] == pytest.approx(e1rm_dono.estimated_1rm(40.0, 20, 10.0))
    assert payload["exposures"][0]["e1rm"] is not None


def test_e1rm_nao_confiavel_ainda_exige_rpe():
    """Política SKIP: série de >12 reps sem RPE não gera nem o não confiável."""
    rows = [linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, reps=15, weight=40.0, rpe=None)]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["records"]["best_e1rm"] is None


def test_havendo_confiavel_o_nao_confiavel_maior_e_ignorado():
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, reps=8, weight=40.0, rpe=8.0),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=1, reps=25, weight=50.0, rpe=10.0),
    ]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    recorde = payload["records"]["best_e1rm"]
    assert recorde["reliable"] is True
    assert recorde["activity_id"] == 100
    assert recorde["value"] == pytest.approx(e1rm_dono.estimated_1rm(40.0, 8, 8.0))


def test_versoes_de_formula_vem_do_registry():
    payload = svc.build_exercise_progress(FakeDb([]), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["metric_versions"]["e1rm"] == "epley-rir-v1"
    assert payload["metric_versions"]["exposure"] == "exposure-v1"
    assert payload["metric_versions"]["exercise_progression_status"] == "progression-status-v2"


def test_decisoes_de_progressao_sao_evidencia_e_nunca_derrubam_a_resposta():
    class SemTabela(FakeDb):
        def respond(self, sql, params):
            if "FROM progression_decisions" in sql:
                raise RuntimeError("Table 'progression_decisions' doesn't exist")
            return super().respond(sql, params)

    payload = svc.build_exercise_progress(SemTabela([]), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["progression_decisions"] == []
    assert payload["status"] == ExerciseProgressionStatus.INSUFFICIENT_DATA.value


def test_limitacao_de_par_sem_convencao_aparece_na_resposta():
    rows = [
        linha(exercise_row_id=1, activity_id=100, dia=1, set_number=1, convention=None),
        linha(exercise_row_id=2, activity_id=101, dia=8, set_number=1, convention=None),
    ]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert any("convencao de carga" in item for item in payload["limitations"])


# ---------------------------------------------------------------------------
# Reps por serie (progression-status-v2)
# ---------------------------------------------------------------------------


def _agachamento_real() -> list[dict]:
    """Cenario sintetico baseado em caso real (Agachamento livre, mesmo
    aparelho, 50 kg):
    02/10 11@9, 10@9, 10@9 contra 06/10 14@9.5, 13@9."""
    return [
        linha(exercise_row_id=1, activity_id=100, dia=2, set_number=1, reps=11, weight=50.0, rpe=9.0),
        linha(exercise_row_id=1, activity_id=100, dia=2, set_number=2, reps=10, weight=50.0, rpe=9.0),
        linha(exercise_row_id=1, activity_id=100, dia=2, set_number=3, reps=10, weight=50.0, rpe=9.0),
        linha(exercise_row_id=2, activity_id=200, dia=6, set_number=1, reps=14, weight=50.0, rpe=9.5),
        linha(exercise_row_id=2, activity_id=200, dia=6, set_number=2, reps=13, weight=50.0, rpe=9.0),
    ]


def test_resumo_leva_as_series_para_as_regras():
    exposures = svc.get_exercise_exposures(FakeDb(_agachamento_real()), EXERCICIO, user_id=USUARIO, window=JANELA)
    summaries = svc.to_summaries(exposures)
    assert [(s.reps, s.weight_kg) for s in summaries[0].sets] == [(11, 50.0), (10, 50.0), (10, 50.0)]
    assert [(s.reps, s.weight_kg) for s in summaries[1].sets] == [(14, 50.0), (13, 50.0)]
    # o agregado continua igual no payload (contrato preservado)
    assert [item["total_reps"] for item in exposures] == [31, 27]


def test_caso_real_do_agachamento_e_progresso_com_aviso_de_menos_series():
    payload = svc.build_exercise_progress(FakeDb(_agachamento_real()), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["status"] == ExerciseProgressionStatus.PROGRESSING.value
    assert "same_load_more_reps" in payload["status_reasons"]
    assert "fewer_working_sets" in payload["status_reasons"]
