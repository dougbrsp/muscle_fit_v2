"""`VolumeService.get_muscle_group_overview`, `MuscleRecoveryService.
get_recovery_overview` e `recovery_projection.project_recovery_items`
alimentados por linhas REAIS nos dois backends (PyMySQL e shim Data API),
com os mesmos dados e o mesmo relogio -> mesmos resultados.

Dados semeados por conexao neutra cobrem: DECIMAL (peso, RPE, pesos de
contribuicao), DATE/DATETIME (local_date, started_at),
TINYINT (muscle_pain) e TINYINT(1) (joint_pain, is_advanced_user),
`activities.workout_plan_id` (coluna) e o fallback JSON
`raw_payload.workout_plan_id`, alem de dado de outro usuario (filtro).
"""

from __future__ import annotations

import copy
import datetime as _dt
import json

import pytest

# `app.analytics` e `app.services` importados no
# topo de proposito: `use_dataapi` so troca o backend de modulos `app.*` JA
# importados (ver tests/support/dataapi_backend.py).
from app import analytics, services, training_types  # noqa: F401 - importados no topo de proposito (use_dataapi)
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.recovery_projection import project_recovery_items
from app.intelligence.volume_service import VolumeService
from tests.support.isolamento_v3 import congelar_relogio
from tests.support.dataapi_backend import BACKENDS, use_backend, use_dataapi

_REAL_DATETIME = _dt.datetime

FROZEN_NOW = _REAL_DATETIME.now().replace(second=0, microsecond=0)

SEED_TABLES = (
    "workout_plan_template_assignments",
    "workout_plan_templates",
    "training_type_muscle_group_targets",
    "training_types",
    "exercise_sets",
    "exercises",
    "activities",
    "workout_plan_exercises",
    "workout_plans",
    "exercise_muscle_contributions",
    "exercise_legacy_id_map",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "muscle_recovery_coefficients",
    "muscle_groups",
    "users",
)


@pytest.fixture
def frozen_clock(monkeypatch):
    # Tambem congela `time_windows.user_now`: o `VolumeService` mede o tempo
    # na hora LOCAL do usuario desde a correcao do relogio UTC.
    congelar_relogio(monkeypatch, FROZEN_NOW)


def _at(days_ago: float, hour: int, minute: int = 0) -> _REAL_DATETIME:
    base = FROZEN_NOW - _dt.timedelta(days=days_ago)
    return base.replace(hour=hour, minute=minute)


@pytest.fixture
def training_history(raw, frozen_clock):
    ids: dict[str, int] = {}
    # activities.workout_plan_id vem da migracao 026 (fixture `mysql_schema`).
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in SEED_TABLES:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")

        cur.execute("INSERT INTO users (username, password_hash, role, is_advanced_user) VALUES ('atleta', 'x', 'admin', 0)")
        ids["user"] = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('outro', 'x', 'regular')")
        ids["other_user"] = cur.lastrowid

        for name in ("Peitoral", "Tríceps", "Costas", "Bíceps", "Quadríceps"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (name,))
            ids[name] = cur.lastrowid

        exercises = {
            1001: ("Supino reto", [("Peitoral", "PRIMARY", "1.00", "1.00"), ("Tríceps", "SECONDARY", "0.50", "0.40")]),
            1003: ("Remada baixa", [("Costas", "PRIMARY", "1.00", "1.00"), ("Bíceps", "SECONDARY", "0.50", "0.35")]),
            1004: ("Agachamento", [("Quadríceps", "PRIMARY", "1.00", "0.90")]),
        }
        for exercise_id, (name, contributions) in exercises.items():
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, name, name),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))
            for group, role, stimulus, fatigue in contributions:
                cur.execute(
                    "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (exercise_id, ids[group], role, stimulus, fatigue),
                )

        cur.execute("INSERT INTO workout_plans (user_id, name) VALUES (%s, 'Ficha A')", (ids["user"],))
        ids["plan"] = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, mapping_id, rpe_target) "
            "VALUES (%s, 1, 'Supino reto', 1001, '8'), (%s, 2, 'Remada baixa', 1003, '7-8')",
            (ids["plan"], ids["plan"]),
        )

        def activity(user, started_at, name, *, plan_id=None, raw_payload=None):
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, workout_plan_id, raw_payload) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user, started_at.date(), started_at, name, plan_id, raw_payload),
            )
            return cur.lastrowid

        def exercise(activity_id, sequence, mapping_id, sets, *, muscle_pain=None, joint_pain=None):
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count, muscle_pain, joint_pain) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (activity_id, sequence, exercises[mapping_id][0], mapping_id, len(sets), muscle_pain, joint_pain),
            )
            exercise_id = cur.lastrowid
            for number, (reps, weight, rpe) in enumerate(sets, start=1):
                cur.execute(
                    "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe) VALUES (%s, %s, %s, %s, %s)",
                    (exercise_id, number, reps, weight, rpe),
                )

        a1 = activity(ids["user"], _at(5, 8), "Treino 1")
        exercise(a1, 1, 1001, [(10, "60.00", "7.50"), (10, "60.00", "7.50"), (9, "60.00", "8.00")], muscle_pain=2, joint_pain=0)
        a2 = activity(ids["user"], _at(3, 8), "Treino 2")
        exercise(a2, 1, 1001, [(10, "62.50", "8.00"), (10, "62.50", "8.00")])
        # Remada: dor articular + plano so no raw_payload (fallback JSON_EXTRACT).
        a4 = activity(ids["user"], _at(2, 18, 45), "Treino 3", raw_payload=json.dumps({"workout_plan_id": ids["plan"]}))
        exercise(a4, 1, 1003, [(12, "45.25", "8.50"), (12, "45.25", "9.00")], muscle_pain=3, joint_pain=1)
        # Supino com plano (coluna), RPE acima do alvo 8 antes da ultima serie e dor 7.
        a3 = activity(ids["user"], _at(1, 7, 30), "Treino 4", plan_id=ids["plan"])
        exercise(a3, 1, 1001, [(8, "65.50", "9.50"), (8, "65.50", "9.50"), (6, "65.50", "10.00")], muscle_pain=7, joint_pain=0)
        # Agachamento: queda de reps > 30% com mesma carga, RPE nulo.
        a5 = activity(ids["user"], FROZEN_NOW - _dt.timedelta(hours=20), "Treino 5")
        exercise(a5, 1, 1004, [(12, "100.00", None), (6, "100.00", None)])
        # Outro usuario: nao pode aparecer no overview filtrado.
        other = activity(ids["other_user"], _at(1, 9), "Treino alheio")
        exercise(other, 1, 1004, [(5, "140.00", "10.00")], muscle_pain=9, joint_pain=1)
    return ids


def _collect(user_id):
    target_24h = FROZEN_NOW + _dt.timedelta(hours=24)
    target_96h = FROZEN_NOW + _dt.timedelta(hours=96)
    overview = VolumeService().get_muscle_group_overview(days=14, limit=24, user_id=user_id)
    recovery = MuscleRecoveryService().get_recovery_overview(days=14, limit=24, user_id=user_id)
    return {
        "overview": overview,
        "recovery": recovery,
        "projection_24h": project_recovery_items(copy.deepcopy(recovery), FROZEN_NOW, target_24h),
        "projection_96h": project_recovery_items(copy.deepcopy(recovery), FROZEN_NOW, target_96h),
    }


def _history_mapping_types(user_id):
    rows = VolumeService()._fetch_full_history_sets(user_id=user_id)
    return sorted({type(row["mapping_id"]).__name__ for row in rows if row.get("mapping_id") is not None})


def _run_both(training_history, monkeypatch, fake_rds, mysql_schema):
    user_id = training_history["user"]
    results = {"pymysql": _collect(user_id)}
    mapping_types = {"pymysql": _history_mapping_types(user_id)}
    use_dataapi(monkeypatch, fake_rds, mysql_schema)
    results["data-api"] = _collect(user_id)
    mapping_types["data-api"] = _history_mapping_types(user_id)
    assert fake_rds.calls_of("ExecuteStatement"), "o caminho data-api nao passou pelo dublê"
    return results, mapping_types


@pytest.fixture
def both_backends(training_history, monkeypatch, fake_rds, mysql_schema):
    """Caminho de producao sem nenhuma interferencia."""
    return _run_both(training_history, monkeypatch, fake_rds, mysql_schema)


def _by_group(items):
    return {item["muscle_group"]: item for item in items}


def _without_recent_sets(result):
    cleaned = copy.deepcopy(result)
    for items in cleaned.values():
        for item in items:
            item.get("factors", {}).pop("recent_sets", None)
    return cleaned


def test_causa_raiz_mapping_id_do_historico_chega_como_int_nos_dois_backends(both_backends):
    """Regressao do ACHADO do COALESCE (`_fetch_full_history_sets`):
    `COALESCE(elm.canonical_exercise_id [BIGINT UNSIGNED], e.mapping_id [BIGINT])`
    era tipado pelo MySQL como DECIMAL(20,0) -> `str` na Data API (stringValue)
    e `float` no PyMySQL (Decimal via _jsonable). Com `CAST(... AS SIGNED)` a
    coluna e BIGINT -> `int` nos dois backends."""
    _results, mapping_types = both_backends
    assert mapping_types == {"pymysql": ["int"], "data-api": ["int"]}


def test_paridade_campos_de_decisao(both_backends):
    """Tudo que alimenta prontidao/contrato/projecao deve ser identico nos dois
    backends (DECIMAL/DATE/DATETIME/TINYINT(1) e activities.workout_plan_id
    incluidos)."""
    results, _ = both_backends
    assert _without_recent_sets(results["data-api"]) == _without_recent_sets(results["pymysql"])


def test_rpe_acima_do_plano_nao_marca_rpe_anticipated_nos_dois_backends(both_backends):
    """Antes (2026-09-14): Peitoral (RPE 9,5 acima do alvo '8') marcava
    rpe_anticipated=True e RPE_ANTICIPATED em blocking_signals. Agora o campo
    e deprecado (sempre False) e o sinal nao existe em nenhum backend."""
    results, _ = both_backends
    for backend in BACKENDS:
        for key in ("recovery", "projection_24h", "projection_96h"):
            for item in results[backend][key]:
                assert item["rpe_anticipated"] is False, (backend, key, item["muscle_group"])
                assert "RPE_ANTICIPATED" not in {s["code"] for s in item["blocking_signals"]}, (backend, key)


# Numeros do cenario `training_history` com os pesos CADASTRADOS em
# exercise_muscle_contributions. Antes da correcao, no caminho Data API
# (producao) o lookup por mapping_id ('1001' str x chave int) falhava e a dose
# caia no fallback legado (PRIMARY 1.0 / SECONDARY 0.50): Biceps 0.824,
# Triceps 1.549, Quadriceps 0.63 e rpe_anticipated=False em Peitoral/Costas.
EXPECTED_SESSION_DOSE = {"Bíceps": 0.577, "Costas": 1.648, "Peitoral": 3.098, "Quadríceps": 0.567, "Tríceps": 1.239}
# Antes (2026-09-14): Costas e Peitoral True (RPE acima do alvo do plano).
# Agora o campo e deprecado e sempre False.
EXPECTED_RPE_ANTICIPATED = {"Bíceps": False, "Costas": False, "Peitoral": False, "Quadríceps": False, "Tríceps": False}


@pytest.mark.parametrize("backend", BACKENDS)
def test_dose_usa_pesos_de_contribuicao_cadastrados(both_backends, backend):
    recovery = _by_group(both_backends[0][backend]["recovery"])
    assert {group: item["session_dose"] for group, item in recovery.items()} == pytest.approx(
        EXPECTED_SESSION_DOSE, abs=1e-3
    )
    assert {group: item["rpe_anticipated"] for group, item in recovery.items()} == EXPECTED_RPE_ANTICIPATED


@pytest.mark.parametrize("backend", BACKENDS)
def test_sinais_calculados(both_backends, backend):
    result = both_backends[0][backend]
    recovery = _by_group(result["recovery"])
    assert set(recovery) == {"Peitoral", "Tríceps", "Costas", "Bíceps", "Quadríceps"}

    peito = recovery["Peitoral"]
    assert peito["muscle_pain"] == 7
    # Antes True (RPE 9,5 acima do alvo '8'); deprecado desde 2026-09-14.
    assert peito["rpe_anticipated"] is False
    assert peito["performance_ratio"] is not None
    assert peito["readiness_status"] == "NO_DIRECT_WORK"  # dor muscular 7, nao RPE
    assert peito["last_session_at"] == _at(1, 7, 30).isoformat()
    assert peito["last_trained_at"] == _at(1, 7, 30).date().isoformat()

    costas = recovery["Costas"]
    assert costas["joint_pain"] is True
    assert costas["readiness_status"] == "NO_DIRECT_WORK"

    quad = recovery["Quadríceps"]
    assert quad["excessive_rep_drop"] is True
    assert quad["muscle_pain"] is None and quad["joint_pain"] is False
    assert quad["sets_count"] == 2  # dados do outro usuario excluidos

    projected = _by_group(result["projection_96h"])
    assert projected["Peitoral"]["projected_readiness_status"] == "NO_DIRECT_WORK"  # dor persiste
    # M76 B3 (decisão 2 do usuário): sinais de desempenho persistem na projeção
    # até a próxima sessão do grupo, como no retrato - antes eram zerados ≥72h.
    assert "performance_signals_reset" not in projected["Quadríceps"]
    assert projected["Quadríceps"]["excessive_rep_drop"] is True  # snapshot "agora" preservado
    for projection in (projected, _by_group(result["projection_24h"])):
        quad_projected = projection["Quadríceps"]
        assert "EXCESSIVE_REP_DROP" in [signal["code"] for signal in quad_projected["blocking_signals"]]
        assert quad_projected["projected_readiness_status"] != "READY"


@pytest.fixture
def rpe10_precoce_history(training_history, raw):
    """D4 revisada (2026-09-14): sessao MAIS RECENTE de remada (alvo do plano
    '7-8' pela coluna activities.workout_plan_id) com RPE 10 em TODAS as
    series, inclusive antes da ultima, e reps no teto da faixa 8-10."""
    ids = dict(training_history)
    with raw.cursor() as cur:
        started_at = FROZEN_NOW - _dt.timedelta(hours=6)
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, workout_plan_id) "
            "VALUES (%s, %s, %s, 'Treino RPE10 precoce', %s)",
            (ids["user"], started_at.date(), started_at, ids["plan"]),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
            "VALUES (%s, 1, 'Remada baixa', 1003, 3)",
            (activity_id,),
        )
        exercise_id = cur.lastrowid
        for number in (1, 2, 3):
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe) "
                "VALUES (%s, %s, 10, '45.25', '10.00')",
                (exercise_id, number),
            )
    return ids


@pytest.fixture
def rpe10_both_backends(rpe10_precoce_history, monkeypatch, fake_rds, mysql_schema):
    user_id = rpe10_precoce_history["user"]
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        # M76 (Lote 1a): a sugestao de carga e do motor v2 por par
        # (`suggested_progression_for_mapping` -> `prescription_service`).
        results[backend] = {
            **_collect(user_id),
            "progression": services.suggested_progression_for_mapping(1003, user_id=user_id),
        }
    return results


def test_rpe10_precoce_sem_penalidade_identico_nos_dois_backends(rpe10_both_backends):
    pymysql_result, data_api_result = rpe10_both_backends["pymysql"], rpe10_both_backends["data-api"]
    assert _without_recent_sets(
        {k: v for k, v in data_api_result.items() if k != "progression"}
    ) == _without_recent_sets({k: v for k, v in pymysql_result.items() if k != "progression"})
    assert data_api_result["progression"] == pymysql_result["progression"]

    for backend, result in rpe10_both_backends.items():
        costas = _by_group(result["recovery"])["Costas"]
        # Antes: True (RPE 10 antes da ultima, alvo 7-8, usuario nao avancado).
        assert costas["rpe_anticipated"] is False, backend
        # Antes: + failure_penalty(3) x fatigue_weight 1,00 = +0,5.
        # Sem referencia de e1RM (1 sessao anterior) e exercicio "novo" (2a ocorrencia).
        expected = 3 * MuscleRecoveryService.effort_points(10.0) * MuscleRecoveryService.exercise_factor(True)
        assert costas["session_dose"] == pytest.approx(expected, abs=1e-3), backend
        # Antes (motor legado): 45.25 -> 42.99 (-5%) e depois +5% (47.51).
        # M76 Lote 1a: motor v2 por par. O exercicio nao tem equipamento nem
        # trilha avaliada -> calibracao pela execucao (minimo das series de
        # trabalho = 45.25, nunca reduzida pelo RPE 10). Sem +5% e sem penalidade
        # de RPE. Tres series em RPE 10 ha 6h deixam Costas em NO_DIRECT_WORK:
        # o motor decide SKIP (sem faixa de reps nesta sessao, teto 8).
        progression = result["progression"]
        assert progression["decision"] == "SKIP", backend
        assert (progression["suggested_load_kg"], progression["suggested_reps"]) == (45.25, None), backend
        assert progression["load_source"] == "last_execution", backend
        assert progression["effective_rpe_ceiling"] == 8 and progression["failure_allowed"] is False, backend
        assert progression["progression_note"].startswith("Grupo sem trabalho direto"), backend
        assert progression["trend"] == "same", backend


@pytest.fixture
def rpe_alto_todas_series_history(training_history, raw):
    """2026-09-14: sessao MAIS RECENTE de supino (plano pela coluna, alvo '8')
    com RPE 9-10 em TODAS as series, sem dor, e sessao mais recente de remada
    (alvo '7-8') em RPE 9/9,5/9 - RPE acima do alvo sem chegar a falha (o caso
    que ainda marcava rpe_anticipated apos o #70)."""
    ids = dict(training_history)
    with raw.cursor() as cur:
        for hours_ago, name, mapping_id, rpes in (
            (4, "Treino RPE alto supino", 1001, ("9.00", "9.50", "10.00")),
            (5, "Treino RPE alto remada", 1003, ("9.00", "9.50", "9.00")),
        ):
            started_at = FROZEN_NOW - _dt.timedelta(hours=hours_ago)
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, workout_plan_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (ids["user"], started_at.date(), started_at, name, ids["plan"]),
            )
            activity_id = cur.lastrowid
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count, muscle_pain, joint_pain) "
                "VALUES (%s, 1, %s, %s, 3, 0, 0)",
                (activity_id, name, mapping_id),
            )
            exercise_id = cur.lastrowid
            for number, rpe in enumerate(rpes, start=1):
                cur.execute(
                    "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe) "
                    "VALUES (%s, %s, 10, '60.00', %s)",
                    (exercise_id, number, rpe),
                )
    return ids


def test_rpe_alto_em_todas_as_series_sem_penalidade_identico_nos_dois_backends(
    rpe_alto_todas_series_history, monkeypatch, fake_rds, mysql_schema
):
    user_id = rpe_alto_todas_series_history["user"]
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        results[backend] = _collect(user_id)
    assert _without_recent_sets(results["data-api"]) == _without_recent_sets(results["pymysql"])

    for backend, result in results.items():
        recovery = _by_group(result["recovery"])
        for group in ("Peitoral", "Costas"):
            item = recovery[group]
            assert item["rpe_anticipated"] is False, (backend, group)
            codes = [s["code"] for s in item["blocking_signals"]]
            assert "RPE_ANTICIPATED" not in codes, (backend, group)
            # Sem dor/queda de performance/queda de reps: o unico sinal que
            # pode restar e o de tempo (sessao de 4-5h atras).
            assert set(codes) <= {"LOW_READINESS", "PERFORMANCE_DROP"}, (backend, group, codes)
            assert all("RPE" not in line for line in item["explanation"]), (backend, group)
            # Status identico ao obtido so com tempo/densidade/performance.
            expected_status = MuscleRecoveryService.classify_readiness(
                MuscleRecoveryService.calculate_adjusted_readiness(
                    MuscleRecoveryService.calculate_time_recovery(
                        item["hours_since_trained"], item["estimated_recovery_hours"]
                    ),
                    item["density_penalty"],
                ),
                item["performance_ratio"],
                item["excessive_rep_drop"],
                item["muscle_pain"],
                item["joint_pain"],
            )
            assert item["readiness_status"] == expected_status, (backend, group)

        for projection in ("projection_24h", "projection_96h"):
            for item in result[projection]:
                assert "RPE_ANTICIPATED" not in {s["code"] for s in item["blocking_signals"]}, (backend, projection)


@pytest.fixture
def metas_de_ativacao_history(training_history, raw):
    """Meta de Ativacao (training_type do template ativo atribuido), series
    equivalentes/semana: Peitoral min 2/max 6, Triceps min 2/max 10, Biceps
    min 2/max 8; Costas e Quadriceps sem meta cadastrada."""
    ids = dict(training_history)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO training_types (name, created_by_user_id) VALUES ('Hipertrofia', %s)", (ids["user"],)
        )
        ids["training_type"] = cur.lastrowid
        cur.execute(
            "INSERT INTO training_type_muscle_group_targets "
            "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets) "
            "VALUES (%s, %s, '2.00', '4.00', '6.00'), (%s, %s, '2.00', '6.00', '10.00'), "
            "(%s, %s, '2.00', '5.00', '8.00')",
            (
                ids["training_type"], ids["Peitoral"],
                ids["training_type"], ids["Tríceps"],
                ids["training_type"], ids["Bíceps"],
            ),
        )
        cur.execute(
            "INSERT INTO workout_plan_templates (name, created_by_user_id, training_type_id) VALUES ('Modelo', %s, %s)",
            (ids["user"], ids["training_type"]),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) VALUES (%s, %s, %s)",
            (template_id, ids["user"], ids["user"]),
        )
    return ids


def test_volume_status_por_meta_de_ativacao_identico_nos_dois_backends(
    metas_de_ativacao_history, monkeypatch, fake_rds, mysql_schema
):
    """Janela de 7 dias (fator semanal 1):
    - Peitoral 8,0 eq (supino 3+2+3, PRIMARY 1,00) > max 6 -> excesso;
    - Triceps 4,0 (SECONDARY 0,50) entre min 2 e max 10 -> ideal;
    - Biceps 1,0 (remada 2 x 0,50) < min 2 -> subtreino;
    - Costas (sem meta) -> ideal; Quadriceps (sem meta, 2 series fisicas,
      antes 'subtreino' por sets_count <= 6) -> ideal."""
    user_id = metas_de_ativacao_history["user"]
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        results[backend] = VolumeService().get_volume_breakdown(days=7, user_id=user_id)
    assert _without_recent_sets({"b": results["data-api"]["items"]}) == _without_recent_sets(
        {"b": results["pymysql"]["items"]}
    )

    for backend, result in results.items():
        items = _by_group(result["items"])
        peito, triceps, costas = items["Peitoral"], items["Tríceps"], items["Costas"]
        assert (peito["weekly_equivalent_sets"], peito["max_weekly_sets"], peito["volume_status"]) == (8.0, 6.0, "excesso"), backend
        assert (triceps["weekly_equivalent_sets"], triceps["max_weekly_sets"]) == (4.0, 10.0), backend
        assert triceps["volume_status"] == "ideal", backend
        biceps, quad = items["Bíceps"], items["Quadríceps"]
        assert (biceps["weekly_equivalent_sets"], biceps["min_weekly_sets"], biceps["volume_status"]) == (1.0, 2.0, "subtreino"), backend
        assert costas["max_weekly_sets"] is None and costas["volume_status"] == "ideal", backend
        assert quad["min_weekly_sets"] is None and quad["sets_count"] == 2 and quad["volume_status"] == "ideal", backend
        assert peito["volume_target_training_type_id"] == metas_de_ativacao_history["training_type"], backend


def test_paridade_resultado_serializavel_em_json(both_backends):
    results, _ = both_backends
    for name, result in results.items():
        json.dumps(result), name


def test_paridade_resultado_completo(both_backends):
    """Antes (ate 2026-09-14) era xfail: factors.recent_sets[] trazia `rpe` como
    str ('9.50') e `trained_at` como '2026-09-12 07:30:00' no Data API. Com a
    conversao por typeName no shim, o resultado inteiro e identico."""
    results, _ = both_backends
    assert results["data-api"] == results["pymysql"]


def test_recent_sets_com_os_mesmos_tipos_nos_dois_backends(both_backends):
    results, _ = both_backends
    for data_api_item, pymysql_item in zip(results["data-api"]["overview"], results["pymysql"]["overview"]):
        left, right = data_api_item["factors"]["recent_sets"], pymysql_item["factors"]["recent_sets"]
        assert left and len(left) == len(right)
        for a, b in zip(left, right):
            assert {k: type(v) for k, v in a.items()} == {k: type(v) for k, v in b.items()}
            assert "T" in a["trained_at"]
            assert a["rpe"] is None or isinstance(a["rpe"], float)


# ---------------------------------------------------------------------------
# Regressao de TIPO das 6 queries que projetam
# `CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id`
# e efeito nos consumidores (series equivalentes, ativacao, progressao).
# ---------------------------------------------------------------------------

LEGACY_EXERCISE_ID = 9001
CANONICAL_EXERCISE_ID = 1001


@pytest.fixture
def legacy_history(training_history, raw):
    """Acrescenta a sessao MAIS RECENTE de supino registrada com um id ANTIGO
    (9001) consolidado em 1001 via exercise_legacy_id_map e com nome diferente
    do catalogo. Cobre o ramo `elm.canonical_exercise_id` do COALESCE e impede
    que o fallback por nome mascare o match por mapping_id."""
    ids = dict(training_history)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
            (LEGACY_EXERCISE_ID, "Supino maquina (id antigo)", "Supino maquina (id antigo)"),
        )
        # O id antigo CONTINUA no catalogo canonico: em producao
        # `exercises.mapping_id` tem FK para `exercise_catalog(id)` (028) e a
        # consolidacao nao apaga a linha antiga - so registra old -> canonical.
        # O nome segue diferente do canonico, que e o ponto do cenario.
        cur.execute(
            "INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)",
            (LEGACY_EXERCISE_ID, "Supino maquina (id antigo)"),
        )
        cur.execute(
            "INSERT INTO exercise_legacy_id_map (old_exercise_id, canonical_exercise_id) VALUES (%s, %s)",
            (LEGACY_EXERCISE_ID, CANONICAL_EXERCISE_ID),
        )
        started_at = FROZEN_NOW - _dt.timedelta(hours=10)
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name) VALUES (%s, %s, %s, 'Treino legado')",
            (ids["user"], started_at.date(), started_at),
        )
        ids["legacy_activity"] = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
            "VALUES (%s, 1, 'Supino maquina (nome antigo)', %s, 2)",
            (ids["legacy_activity"], LEGACY_EXERCISE_ID),
        )
        exercise_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe) "
            "VALUES (%s, 1, 10, '70.00', '8.00'), (%s, 2, 10, '70.00', '8.00')",
            (exercise_id, exercise_id),
        )
    return ids


def _spy_mapping_rows(service: VolumeService) -> list[dict]:
    """Guarda as linhas cruas das queries de `service` que projetam mapping_id
    resolvido pelo exercise_legacy_id_map (os metodos so devolvem agregados)."""
    captured: list[dict] = []
    original = service.fetch_all

    def spy(sql, params=()):
        rows = original(sql, params)
        if "exercise_legacy_id_map" in sql and "AS mapping_id" in sql:
            captured.extend(rows)
        return rows

    service.fetch_all = spy
    return captured


def _run_mapping_queries(ids) -> dict[str, dict]:
    user_id = ids["user"]
    out: dict[str, dict] = {}

    rows = VolumeService()._fetch_full_history_sets(user_id=user_id)
    out["volume_service._fetch_full_history_sets"] = {"rows": rows, "result": None}

    service = VolumeService()
    captured = _spy_mapping_rows(service)
    result = service.get_equivalent_sets_by_activity(user_id=user_id)
    out["volume_service.get_equivalent_sets_by_activity"] = {"rows": captured, "result": result}

    service = VolumeService()
    captured = _spy_mapping_rows(service)
    result = service.get_muscle_activation_by_activity(ids["legacy_activity"], user_id=user_id)
    out["volume_service.get_muscle_activation_by_activity"] = {"rows": captured, "result": result}

    service = VolumeService()
    captured = _spy_mapping_rows(service)
    result = service.get_daily_muscle_activation(days=7, user_id=user_id)
    out["volume_service.get_daily_muscle_activation"] = {"rows": captured, "result": result}

    rows = services.list_exercise_logs(limit=300, user_id=user_id)
    out["services.list_exercise_logs"] = {"rows": rows, "result": None}

    rows = analytics._fetch_exercise_set_rows(user_id, 30, mapping_id=CANONICAL_EXERCISE_ID)
    out["analytics._fetch_exercise_set_rows"] = {"rows": rows, "result": None}
    return out


MAPPING_QUERIES = (
    "volume_service._fetch_full_history_sets",
    "volume_service.get_equivalent_sets_by_activity",
    "volume_service.get_muscle_activation_by_activity",
    "volume_service.get_daily_muscle_activation",
    "services.list_exercise_logs",
    "analytics._fetch_exercise_set_rows",
)


@pytest.fixture
def mapping_queries_both(legacy_history, monkeypatch, fake_rds, mysql_schema):
    out = {"pymysql": _run_mapping_queries(legacy_history)}
    use_dataapi(monkeypatch, fake_rds, mysql_schema)
    out["data-api"] = _run_mapping_queries(legacy_history)
    assert fake_rds.calls_of("ExecuteStatement"), "o caminho data-api nao passou pelo dublê"
    return out


@pytest.mark.parametrize("backend", BACKENDS)
def test_regressao_tipo_mapping_id_e_int_nas_6_queries(mapping_queries_both, backend):
    types: dict[str, list[str]] = {}
    for query in MAPPING_QUERIES:
        values = [row["mapping_id"] for row in mapping_queries_both[backend][query]["rows"] if row.get("mapping_id") is not None]
        assert values, f"{query}: nenhuma linha com mapping_id"
        types[query] = sorted({type(value).__name__ for value in values})
        # id antigo resolvido para o canonico (ramo elm.canonical_exercise_id)
        assert CANONICAL_EXERCISE_ID in values and LEGACY_EXERCISE_ID not in values, query
    assert types == {query: ["int"] for query in MAPPING_QUERIES}


def test_paridade_series_equivalentes_e_ativacao(mapping_queries_both, legacy_history):
    """Agregados que dependem de `contributions_by_mapping_id[mapping_id]`:
    antes da correcao, no Data API, ficavam VAZIOS (chave '1001' nao casava)."""
    for query in (
        "volume_service.get_equivalent_sets_by_activity",
        "volume_service.get_muscle_activation_by_activity",
        "volume_service.get_daily_muscle_activation",
    ):
        pymysql_result = mapping_queries_both["pymysql"][query]["result"]
        assert pymysql_result, query
        assert mapping_queries_both["data-api"][query]["result"] == pymysql_result, query

    legacy = {
        item["muscle_group"]: item["equivalent_sets"]
        for item in mapping_queries_both["data-api"]["volume_service.get_equivalent_sets_by_activity"]["result"]
        if item["activity_id"] == legacy_history["legacy_activity"]
    }
    # 2 series x stimulus_weight cadastrado (PRIMARY 1.00 / SECONDARY 0.50)
    assert legacy == {"Peitoral": 2.0, "Tríceps": 1.0}


@pytest.mark.parametrize("backend", BACKENDS)
def test_progressao_encontra_historico_pelo_mapping_id_canonico(
    legacy_history, monkeypatch, fake_rds, mysql_schema, backend
):
    """`services.suggested_progression_for_mapping` (GET
    /exercises/suggested-progression) filtra `list_exercise_logs` por
    `mapping_id == <int>` para a "ultima vez". Com mapping_id str (Data API antes da correcao) o
    filtro nunca casava e caia no match por NOME: aqui pegaria a sessao mais
    antiga 'Supino reto' (65.5 kg x 6) em vez da mais recente (70 kg x 10)."""
    use_backend(backend, monkeypatch, fake_rds, mysql_schema)
    user_id = legacy_history["user"]

    suggestion = services.suggested_progression_for_mapping(LEGACY_EXERCISE_ID, user_id=user_id)
    assert suggestion["has_history"] is True
    assert (suggestion["last_load_kg"], suggestion["last_reps"]) == (70.0, 10)
