"""`app/domain/training_safety.py` (PR-07): precedencia override > default >
NULL = 9, matriz completa de `compose`, `validate_technique` defensiva e
`clamp_ai_suggestion`. Tudo puro - nenhum teste aqui toca banco.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.domain import training_safety as ts

# failure_requirement do catalogo (secao 5.9 / seed da 031).
CATALOG = {
    "rest_pause": "requires_failure",
    "drop_set": "requires_failure",
    "fst7": "requires_failure",
    "gvt": "failure_not_required",
    "forced_reps": "beyond_failure",
    "negative_reps": "beyond_failure",
    "partial_reps": "beyond_failure",
    "cheat_reps": "beyond_failure",
}


def _safety(equipment=None, override=None, equipment_techniques=None, override_techniques=None, catalog=CATALOG):
    return ts.build_training_safety(
        exercise_id=1,
        equipment_id=2,
        equipment_exercise_id=3,
        equipment_max_safe_rpe=equipment,
        override_max_safe_rpe=override,
        equipment_techniques=equipment_techniques,
        override_techniques=override_techniques,
        technique_failure_requirements=catalog,
    )


# ---------------------------------------------------------------------------
# Precedencia: override > default > NULL = 9 (D3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "equipment, override, expected, source",
    [
        (None, None, 9, None),  # D3
        (10, None, 10, ts.SOURCE_EQUIPMENT_DEFAULT),
        (7, None, 7, ts.SOURCE_EQUIPMENT_DEFAULT),
        (10, 8, 8, ts.SOURCE_OVERRIDE),  # override restringe
        (8, 10, 10, ts.SOURCE_OVERRIDE),  # override amplia
        (None, 10, 10, ts.SOURCE_OVERRIDE),
        (None, 6, 6, ts.SOURCE_OVERRIDE),
        (Decimal("10"), None, 10, ts.SOURCE_EQUIPMENT_DEFAULT),  # como o driver pode devolver
        ("8", None, 8, ts.SOURCE_EQUIPMENT_DEFAULT),
    ],
)
def test_precedencia_de_max_safe_rpe(equipment, override, expected, source):
    safety = _safety(equipment, override)
    assert (safety.max_safe_rpe, safety.safety_source) == (expected, source)
    assert safety.failure_allowed is (expected == 10)


@pytest.mark.parametrize("dirty", [0, 5, 11, 99, -1, 9.5, "abc", "", True, float("nan"), float("inf")])
def test_valor_sujo_e_desconhecido_nunca_permissao(dirty):
    assert ts.normalize_max_safe_rpe(dirty) is None
    assert _safety(dirty).max_safe_rpe == 9 and _safety(dirty).failure_allowed is False
    # override sujo nao esconde um default valido do equipamento
    safety = _safety(8, dirty)
    assert (safety.max_safe_rpe, safety.safety_source) == (8, ts.SOURCE_EQUIPMENT_DEFAULT)


def test_tecnica_sem_marcacao_e_incompativel():
    safety = _safety(10)
    assert safety.allowed_techniques == () and safety.blocked_techniques == ()
    for code in CATALOG:
        assert ts.validate_technique(code, safety).reason == ts.TECHNIQUE_NOT_COMPATIBLE


def test_precedencia_de_tecnicas_override_vence_default_nos_dois_sentidos():
    safety = _safety(
        10,
        equipment_techniques={"rest_pause": 1, "drop_set": 1, "gvt": 0},
        override_techniques={"drop_set": 0, "gvt": 1, "partial_reps": 1},
    )
    assert [item.to_dict() for item in safety.allowed_techniques] == [
        {"code": "gvt", "source": ts.SOURCE_OVERRIDE},
        {"code": "partial_reps", "source": ts.SOURCE_OVERRIDE},
        {"code": "rest_pause", "source": ts.SOURCE_EQUIPMENT_DEFAULT},
    ]
    assert "drop_set" not in safety.allowed_codes  # negado pelo par


def test_tecnica_marcada_mas_fora_do_catalogo_ativo_e_ignorada():
    safety = _safety(10, equipment_techniques={"rest_pause": 1, "bi_set": 1}, catalog={"rest_pause": "requires_failure"})
    assert safety.allowed_codes == {"rest_pause"}
    assert ts.validate_technique("bi_set", safety).reason == ts.TECHNIQUE_UNKNOWN


def test_to_dict_segue_o_contrato_da_recomendacao_secao_9():
    safety = _safety(10, equipment_techniques={"rest_pause": 1}, override_techniques={"drop_set": 1})
    assert safety.to_dict() == {
        "exercise_id": 1,
        "equipment_id": 2,
        "equipment_exercise_id": 3,
        "max_safe_rpe": 10,
        "failure_allowed": True,
        "safety_source": "equipment_default",
        "allowed_advanced_techniques": [
            {"code": "drop_set", "source": "exercise_equipment_override"},
            {"code": "rest_pause", "source": "equipment_default"},
        ],
        "blocked_advanced_techniques": [],
    }


# ---------------------------------------------------------------------------
# compose: max_safe_rpe (NULL, 6..10) x READY / REDUCED_DOSE / NO_DIRECT_WORK
# ---------------------------------------------------------------------------

RECOVERY_MAX = {ts.READY: 10, ts.REDUCED_DOSE: 9, ts.NO_DIRECT_WORK: 8}
MATRIX = [(max_safe, status) for max_safe in (None, 6, 7, 8, 9, 10) for status in RECOVERY_MAX]


def test_limites_de_readiness_tem_paridade_com_o_motor_de_recuperacao():
    from app.intelligence.muscle_recovery_service import MuscleRecoveryService

    for status, rec in MuscleRecoveryService._RECOMMENDATIONS.items():
        readiness = ts.readiness_for_status(status)
        assert (readiness.max_rpe, readiness.allow_failure) == (rec["maximum_rpe"], rec["allow_failure_on_last_set"])
    with pytest.raises(ValueError):
        ts.readiness_for_status("DESCONHECIDO")


@pytest.mark.parametrize("max_safe, status", MATRIX)
def test_compose_matriz_completa_sem_prescricao(max_safe, status):
    result = ts.compose(_safety(max_safe), ts.readiness_for_status(status))
    expected_ceiling = min(9 if max_safe is None else max_safe, RECOVERY_MAX[status])
    assert result.ceiling == expected_ceiling
    # D4 revisada em 2026-09-18: sem prescricao o alvo e o DEFAULT cortado
    # pelo teto - o teto NUNCA vira piso de exigencia.
    assert result.target == min(ts.DEFAULT_TARGET_RPE, expected_ceiling)
    assert result.failure_allowed is (max_safe == 10 and status == ts.READY)
    assert result.clamps == ()  # nao houve valor prescrito para clampar


@pytest.mark.parametrize("ceiling", [8, 9, 10])
def test_sem_prescricao_o_teto_nao_vira_piso(ceiling):
    """Tetos 8, 9 e 10 dao o MESMO alvo 8: o teto so corta para baixo."""
    result = ts.compose(_safety(ceiling), ts.readiness_for_status(ts.READY))
    assert (result.ceiling, result.target, result.clamps) == (ceiling, 8, ())


def test_sem_prescricao_o_teto_abaixo_do_default_corta_o_alvo():
    result = ts.compose(_safety(6), ts.readiness_for_status(ts.READY))
    assert (result.ceiling, result.target, result.clamps) == (6, 6, ())


def test_default_de_alvo_e_fonte_unica_compartilhada_com_o_motor_de_progressao():
    from decimal import Decimal

    from app.domain.progression_engine import DEFAULT_EFFORT_TARGET_RPE

    assert DEFAULT_EFFORT_TARGET_RPE == Decimal(ts.DEFAULT_TARGET_RPE)


@pytest.mark.parametrize("max_safe, status", MATRIX)
@pytest.mark.parametrize("prescribed", [6, 8, 9, 10])
def test_compose_matriz_completa_com_prescricao(max_safe, status, prescribed):
    effective_safety = 9 if max_safe is None else max_safe
    result = ts.compose(_safety(max_safe), ts.readiness_for_status(status), prescribed)
    ceiling = min(effective_safety, RECOVERY_MAX[status])
    assert result.ceiling == ceiling
    assert result.target == min(prescribed, ceiling)
    assert result.target <= effective_safety and result.target <= RECOVERY_MAX[status]
    if prescribed <= ceiling:
        assert result.clamps == ()
    else:
        (clamp,) = result.clamps
        expected_reason = ts.REASON_RECOVERY if RECOVERY_MAX[status] < effective_safety else ts.REASON_EQUIPMENT_SAFETY
        assert clamp.to_dict() == {"field": "rpe_target", "from": prescribed, "to": ceiling, "reason": expected_reason}
    if not result.failure_allowed:
        assert result.target < 10  # a ficha nunca mostra 10 sem falha permitida


def test_compose_exemplos_do_plano():
    # exemplo da secao 9: {"field":"rpe_target","from":10,"to":9,"reason":"EQUIPMENT_SAFETY"}
    result = ts.compose(_safety(None), ts.readiness_for_status(ts.READY), 10)
    assert result.to_dict() == {
        "effective_rpe_ceiling": 9,
        "failure_allowed": False,
        "failure_denied_by": ts.FAILURE_DENIED_BY_PAIR,
        "rpe_target": 9,
        "clamps": [{"field": "rpe_target", "from": 10, "to": 9, "reason": "EQUIPMENT_SAFETY"}],
    }
    # equipamento permite falha, recuperacao nao
    result = ts.compose(_safety(10), ts.readiness_for_status(ts.REDUCED_DOSE), 10)
    assert (result.ceiling, result.failure_allowed, result.clamps[0].reason) == (9, False, ts.REASON_RECOVERY)


@pytest.mark.parametrize(
    "max_safe, status, esperado",
    [
        (10, "READY", None),  # falha permitida
        (10, "REDUCED_DOSE", "RECOVERY"),  # par permite, so a recuperacao negou
        (10, "NO_DIRECT_WORK", "RECOVERY"),
        (9, "READY", "PAIR"),  # par proibe
        (9, "REDUCED_DOSE", "PAIR"),  # empate/ambos = PAIR
        (8, "NO_DIRECT_WORK", "PAIR"),
        (None, "READY", "PAIR"),  # par desconhecido (D3: 9)
    ],
)
def test_compose_expoe_a_origem_da_negacao_da_falha(max_safe, status, esperado):
    """Lote 1/P1 (2026-10-11): `failure_denied_by` diz QUEM negou a falha."""
    result = ts.compose(_safety(max_safe), ts.readiness_for_status(status), 10)
    assert result.failure_denied_by == esperado
    assert ts.failure_denial_origin(result.failure_allowed, _safety(max_safe).max_safe_rpe) == esperado


def test_compose_recuperacao_que_nega_falha_com_max_rpe_10_derruba_o_ceiling_para_9():
    readiness = ts.Readiness(ts.READY, 10, False)  # contrato futuro (PR-09) pode negar falha com max 10
    result = ts.compose(_safety(10), readiness, 10)
    assert (result.ceiling, result.target, result.failure_allowed) == (9, 9, False)
    assert result.clamps[0].reason == ts.REASON_RECOVERY
    assert result.failure_denied_by == ts.FAILURE_DENIED_BY_RECOVERY


# ---------------------------------------------------------------------------
# validate_technique (defensiva)
# ---------------------------------------------------------------------------

ALL_MARKED = {code: 1 for code in CATALOG}


@pytest.mark.parametrize("max_safe", [None, 6, 7, 8, 9])
def test_tecnica_que_exige_falha_e_invalida_sem_falha_mesmo_marcada_compativel(max_safe):
    safety = _safety(max_safe, equipment_techniques=ALL_MARKED)
    assert safety.allowed_codes == {"gvt"}
    for code, requirement in CATALOG.items():
        result = ts.validate_technique(code, safety)
        if requirement == "failure_not_required":
            assert result.valid and result.reason is None
        else:
            assert not result.valid and result.reason == ts.FAILURE_REQUIRED_NOT_ALLOWED
            assert result.source == ts.SOURCE_EQUIPMENT_DEFAULT


def test_com_falha_permitida_todas_as_marcadas_valem():
    safety = _safety(10, equipment_techniques=ALL_MARKED)
    assert all(ts.validate_technique(code, safety).valid for code in CATALOG)


def test_falha_negada_pela_recuperacao_invalida_tecnica_mesmo_em_par_que_permite():
    safety = _safety(10, equipment_techniques=ALL_MARKED)
    composed = ts.compose(safety, ts.readiness_for_status(ts.REDUCED_DOSE))
    assert ts.validate_technique("drop_set", safety, failure_allowed=composed.failure_allowed).reason == ts.FAILURE_REQUIRED_NOT_ALLOWED
    assert ts.validate_technique("gvt", safety, failure_allowed=composed.failure_allowed).valid


def test_chamador_nao_consegue_ampliar_a_permissao_de_falha():
    safety = _safety(9, equipment_techniques=ALL_MARKED)
    assert not ts.validate_technique("drop_set", safety, failure_allowed=True).valid


def test_may_reach_failure_nao_exige_falha():
    catalog = {"cluster": "may_reach_failure"}
    safety = _safety(8, equipment_techniques={"cluster": 1}, catalog=catalog)
    assert ts.validate_technique("cluster", safety).valid


# ---------------------------------------------------------------------------
# clamp_ai_suggestion
# ---------------------------------------------------------------------------


def _composition(max_safe, status=ts.READY):
    return ts.compose(_safety(max_safe), ts.readiness_for_status(status))


def test_clamp_ai_limita_o_rpe_ao_ceiling_e_preserva_o_resto():
    suggestion = {"reps": 10, "load_kg": 42.5, "rpe": 10}
    result, clamps = ts.clamp_ai_suggestion(suggestion, _composition(None))
    assert result == {"reps": 10, "load_kg": 42.5, "rpe": 9}
    assert [c.to_dict() for c in clamps] == [{"field": "rpe", "from": 10, "to": 9, "reason": "AI_SUGGESTION"}]
    assert suggestion["rpe"] == 10  # entrada nao e mutada


@pytest.mark.parametrize("rpe", [None, 6, 8.5, 9, 9.0])
def test_clamp_ai_nao_mexe_quando_dentro_do_ceiling_ou_ausente(rpe):
    suggestion = {"reps": 8, "load_kg": None, "rpe": rpe}
    assert ts.clamp_ai_suggestion(suggestion, _composition(9)) == (suggestion, [])


def test_clamp_ai_fracionario_acima_do_ceiling_e_limitado():
    result, clamps = ts.clamp_ai_suggestion({"rpe": 9.5}, _composition(9))
    assert result["rpe"] == 9 and clamps[0].from_value == 9.5


def test_clamp_ai_respeita_recuperacao_e_permite_10_so_com_falha_permitida():
    assert ts.clamp_ai_suggestion({"rpe": 10}, _composition(10))[1] == []
    result, clamps = ts.clamp_ai_suggestion({"rpe": 10}, _composition(10, ts.NO_DIRECT_WORK))
    assert result["rpe"] == 8 and len(clamps) == 1


@pytest.mark.parametrize("rpe", ["forte", True, [], {}])
def test_clamp_ai_valor_nao_numerico_passa_sem_clamp(rpe):
    assert ts.clamp_ai_suggestion({"rpe": rpe}, _composition(9)) == ({"rpe": rpe}, [])


# ---------------------------------------------------------------------------
# Garantias de desenho
# ---------------------------------------------------------------------------


def test_is_advanced_user_nao_participa_da_seguranca():
    import ast

    identifiers = set()
    for node in ast.walk(ast.parse(inspect.getsource(ts))):
        identifiers |= {getattr(node, attr, None) for attr in ("id", "arg", "attr", "name")}
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" not in node.value:
            identifiers.add(node.value)  # chaves de dict/colunas; docstrings ficam de fora
    assert not [name for name in identifiers if isinstance(name, str) and ("advanced_user" in name or "experience" in name)]


# `test_resolvedor_so_esta_ligado_nos_consumidores_previstos` saiu no PR-14, como
# a propria docstring dele previa: com a IA de serie (`workout_ai_generation_core`/
# `exercise_set_evaluation_ai`), o Modo Treino (`workout_sessions` via
# `domain/effective_intensity.py`) e os analytics ligados, a lista de
# consumidores permitidos deixou de dizer algo - o resolvedor passou a ser o
# caminho normal de qualquer codigo que precise de teto de RPE.
