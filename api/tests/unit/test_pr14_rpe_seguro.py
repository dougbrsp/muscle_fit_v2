"""PR-14 - RPE seguro em todo lugar (PLANO_20260912.md secoes 4, 5.7, 9, 11, 13
e 14; decisoes D4 revisada em 2026-09-18 e D11).

O que este arquivo prova, tudo sem banco:

1. **IA de serie clampada NO SERVIDOR** (furo G6): com a LLM mockada, sugestao
   acima do teto sai cortada e com `clamps[]`; abaixo do teto sai intacta.
2. **Intensidade efetiva** (`domain/effective_intensity.py`): prescricao
   congelada > linha legada da ficha > so seguranca; o clamp so restringe, em
   qualquer caminho, inclusive com teto mandado pelo cliente.
3. **RPE 10 onde a falha e proibida** gera `safety_event`, vale como
   intensidade atingida (`ABOVE_SAFE_CEILING`) e nao eleva alvo nenhum.
4. **`is_advanced_user` fora da seguranca** (D11) no modulo novo.
5. **As penalidades de RPE 10 precoce sumiram** para todos (secao 11): teste de
   regressao sobre os tres pontos que o plano manda remover.

A matriz completa de `compose` x readiness, a defensiva de `validate_technique`
e a precedencia override > default > NULL = 9 vivem em
`test_training_safety.py`, que e o teste do resolvedor unico.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from app import exercise_set_evaluation_ai as ai
from app import workout_ai_generation_core as core
from app.domain.next_set_load import DEFAULT_RULESET
from app.domain import effective_intensity as ei
from app.domain import prescription as pd
from app.domain import training_safety as ts
from app.domain.progression_engine import (
    INTENSITY_ABOVE_SAFE_CEILING,
    INTENSITY_BELOW_TARGET,
    INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING,
    INTENSITY_REACHED,
    QUALIFY,
    SAFETY_EVENT_RPE_ABOVE_CEILING,
    SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
    evaluate_track_session,
    intensity_status_for,
)
from app.domain.training_history import PrescribedSpec
from tests.support import progression_fixtures as fx


def _safety(max_safe_rpe: int) -> ts.TrainingSafety:
    return ts.build_training_safety(exercise_id=1, equipment_id=2, equipment_max_safe_rpe=max_safe_rpe)


def _frozen(rpe_target: str, max_safe_rpe: int, status: str = ts.READY) -> dict:
    """Snapshot congelado como o PR-11 grava, para o par dado (e o contrato de
    recuperacao projetado do grupo)."""
    return pd.build_prescription(
        {"rpe_target": rpe_target, "sets_count": 3},
        safety=_safety(max_safe_rpe),
        readiness=ts.readiness_for_status(status),
    )


# ---------------------------------------------------------------------------
# 1. IA de serie: clamp no servidor, com a LLM mockada
# ---------------------------------------------------------------------------

LLM_RESULT = {
    "model": "gpt-mock",
    "category": "INCREASE_LOAD",
    "note": "Mantenha a tecnica e suba a carga na proxima.",
    "raw_text": 'INCREASE_LOAD\nnota\n{"reps": 10, "load_kg": 42.5, "rpe": 10}',
    "suggested_next_reps": 10.0,
    "suggested_next_load_kg": 42.5,
    "suggested_next_rpe": 10.0,
}


@pytest.fixture
def ia_mockada(monkeypatch):
    """LLM mockada (a chave da OpenAI nem e lida) + par de seguranca escolhido
    pelo teste, sem tocar o banco."""
    chamadas: dict[str, object] = {}

    def fake_evaluate_set(payload, **kwargs):
        chamadas["payload"] = payload
        return dict(LLM_RESULT)

    monkeypatch.setattr(core, "evaluate_set", fake_evaluate_set)
    monkeypatch.setattr(core, "load_published_ruleset_autocommit", lambda: DEFAULT_RULESET)  # R2: sem banco

    def com_teto(max_safe_rpe):
        monkeypatch.setattr(
            core.effective_intensity,
            "resolve_pair_safety",
            lambda conn, exercise_id, equipment_id: _safety(max_safe_rpe),
        )
        monkeypatch.setattr(core, "transaction", _fake_transaction)

    chamadas["com_teto"] = com_teto
    return chamadas


class _FakeTransaction:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def _fake_transaction():
    return _FakeTransaction()


def test_sugestao_acima_do_teto_sai_cortada_com_clamps(ia_mockada):
    ia_mockada["com_teto"](9)  # par sem falha permitida

    result = core.dispatch_evaluate_set({"exercise_name": "Supino", "mapping_id": 1, "equipment_id": 2})

    assert result["suggested_next_rpe"] == 9
    assert result["clamps"] == [
        {"field": "rpe", "from": 10.0, "to": 9, "reason": ts.REASON_AI_SUGGESTION},
    ]
    assert result["effective_rpe_ceiling"] == 9
    assert result["failure_allowed"] is False
    # reps/carga nao sao tocados pelo clamp de seguranca.
    assert (result["suggested_next_reps"], result["suggested_next_load_kg"]) == (10.0, 42.5)


def test_sugestao_dentro_do_teto_passa_intacta(ia_mockada):
    ia_mockada["com_teto"](10)  # falha permitida: RPE 10 e legitimo

    result = core.dispatch_evaluate_set({"exercise_name": "Supino", "mapping_id": 1, "equipment_id": 2})

    assert result["suggested_next_rpe"] == 10.0
    assert result["clamps"] == []
    assert result["failure_allowed"] is True
    assert {key: result[key] for key in LLM_RESULT} == LLM_RESULT


def test_par_desconhecido_cai_no_default_conservador_d3(ia_mockada, monkeypatch):
    """Sem `mapping_id`/`equipment_id` (ou com leitura falhando) o clamp NAO
    desaparece: vale o teto 9 da D3. Na duvida, restringe."""
    monkeypatch.setattr(core, "transaction", _fake_transaction)
    monkeypatch.setattr(
        core.effective_intensity, "resolve_pair_safety", lambda conn, exercise_id, equipment_id: None
    )

    result = core.dispatch_evaluate_set({"exercise_name": "Supino"})

    assert result["suggested_next_rpe"] == ts.DEFAULT_MAX_SAFE_RPE == 9
    assert [clamp["reason"] for clamp in result["clamps"]] == [ts.REASON_AI_SUGGESTION]


def test_falha_ao_ler_seguranca_nao_derruba_a_avaliacao_e_restringe(ia_mockada, monkeypatch):
    def explode():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(core, "transaction", explode)

    result = core.dispatch_evaluate_set({"exercise_name": "Supino", "mapping_id": 1, "equipment_id": 2})

    assert result["suggested_next_rpe"] == 9
    assert result["category"] == "INCREASE_LOAD"


def test_teto_mandado_pelo_cliente_so_restringe(ia_mockada):
    ia_mockada["com_teto"](10)

    mais_baixo = core.dispatch_evaluate_set(
        {"exercise_name": "Supino", "mapping_id": 1, "equipment_id": 2, "effective_rpe_ceiling": 8}
    )
    mais_alto = core.dispatch_evaluate_set(
        {"exercise_name": "Supino", "mapping_id": 1, "equipment_id": 2, "effective_rpe_ceiling": 10}
    )

    assert mais_baixo["suggested_next_rpe"] == 8
    assert mais_alto["suggested_next_rpe"] == 10.0


def test_clamp_da_ia_nao_reescreve_o_texto_da_llm(ia_mockada):
    ia_mockada["com_teto"](9)
    result = core.dispatch_evaluate_set({"exercise_name": "Supino", "mapping_id": 1, "equipment_id": 2})
    assert result["note"] == LLM_RESULT["note"]


def test_apply_safety_clamp_usa_a_funcao_unica_do_resolvedor():
    """A regra do corte mora em `training_safety.clamp_ai_suggestion` - aqui so
    entra/sai o formato da resposta do endpoint."""
    fonte = inspect.getsource(ai.apply_safety_clamp)
    assert "clamp_ai_suggestion" in fonte
    assert "min(" not in fonte  # nenhum clamp reimplementado


# ---------------------------------------------------------------------------
# 2. Intensidade efetiva: as tres fontes, sempre restringindo
# ---------------------------------------------------------------------------


def test_prescricao_congelada_e_a_fonte_do_alvo_do_modo_treino():
    intensity = ei.from_prescription(_frozen("8", 10), _safety(10))
    assert (intensity.rpe_target_effective, intensity.ceiling, intensity.failure_allowed) == (8, 10, True)
    assert intensity.source == ei.SOURCE_PRESCRIPTION


def test_overlay_corta_ao_vivo_quando_o_teto_de_hoje_baixou():
    """Ficha congelada com alvo 10 (par permitia falha) e o equipamento virou
    `max_safe_rpe=8` depois: a execucao de hoje cobra 8, com clamp auditado."""
    intensity = ei.from_prescription(_frozen("10", 10), _safety(8))
    assert intensity.rpe_target_effective == 8
    assert intensity.ceiling == 8
    assert intensity.failure_allowed is False
    assert [clamp["reason"] for clamp in intensity.clamps] == [ts.REASON_EQUIPMENT_SAFETY]


def test_plano_legado_com_rpe_10_em_par_proibido_e_cortado_ao_vivo():
    intensity = ei.from_legacy_plan("10", _safety(9))
    assert intensity.rpe_target_effective == 9
    assert intensity.failure_allowed is False
    assert intensity.clamps == (
        {"field": "rpe_target", "from": 10, "to": 9, "reason": ts.REASON_LEGACY_PLAN},
    )


def test_plano_legado_dentro_do_teto_nao_gera_clamp():
    intensity = ei.from_legacy_plan("8", _safety(10))
    assert (intensity.rpe_target_effective, intensity.clamps) == (8, ())


def test_sem_ficha_nao_ha_alvo_exibido_mas_o_teto_continua_valendo():
    intensity = ei.from_safety(_safety(9))
    assert intensity.rpe_target_effective is None
    assert (intensity.ceiling, intensity.failure_allowed, intensity.source) == (9, False, ei.SOURCE_SAFETY_ONLY)


def test_par_desconhecido_nao_inventa_teto():
    assert ei.from_safety(None) is ei.UNKNOWN_INTENSITY
    assert ei.UNKNOWN_INTENSITY.known is False


@pytest.mark.parametrize("pedido", [10, 9, None, "lixo", True])
def test_restrict_ceiling_nunca_amplia(pedido):
    base = ei.from_prescription(_frozen("9", 9), _safety(9))
    assert ei.restrict_ceiling(base, pedido).ceiling == 9


def test_restrict_ceiling_baixa_alvo_e_teto_juntos():
    base = ei.from_prescription(_frozen("9", 10), _safety(10))
    restrito = ei.restrict_ceiling(base, 8)
    assert (restrito.ceiling, restrito.rpe_target_effective, restrito.failure_allowed) == (8, 8, False)


@pytest.mark.parametrize(
    "texto,teto,esperado",
    [
        ("9-10", 9, "9"),
        ("10", 9, "9"),
        ("8-9", 9, "8-9"),
        ("9-10", 10, "9-10"),
        ("7-8", 6, "6"),
        # M76 (C4): degrau a degrau, a rampa encurta no fim (repete o ultimo).
        ("8-9-10", 9, "8-9"),
        ("8-10", 9, "8-9"),
        ("8,5-9,5", 9, "8.5-9"),
        ("sem numero", 9, "sem numero"),
        (None, 9, None),
        ("9-10", None, "9-10"),
    ],
)
def test_clamp_de_faixa_textual_de_rpe(texto, teto, esperado):
    assert ei.clamp_rpe_text(texto, teto)[0] == esperado


def test_faixa_textual_cortada_registra_clamp():
    _, clamps = ei.clamp_rpe_text("9-10", 9)
    assert clamps == [{"field": "rpe_target", "from": "9-10", "to": "9", "reason": ts.REASON_EQUIPMENT_SAFETY}]


# ---------------------------------------------------------------------------
# 3. RPE 10 onde a falha e proibida
# ---------------------------------------------------------------------------


def test_rpe10_em_par_proibido_gera_safety_event():
    intensity = ei.from_prescription(_frozen("10", 9), _safety(9))
    assert ei.safety_event_for("working", 10, intensity) == SAFETY_EVENT_RPE_ABOVE_CEILING


@pytest.mark.parametrize("rpe", [9, 9.5, None, "x"])
def test_abaixo_de_10_nao_gera_safety_event(rpe):
    intensity = ei.from_prescription(_frozen("9", 9), _safety(9))
    assert ei.safety_event_for("working", rpe, intensity) is None


def test_rpe10_em_par_que_permite_falha_nao_e_evento():
    intensity = ei.from_prescription(_frozen("10", 10), _safety(10))
    assert ei.safety_event_for("working", 10, intensity) is None


@pytest.mark.parametrize("set_type", ["warmup", "feeler", "drop_set", "rest_pause", "forced_rep"])
def test_evento_so_em_serie_working(set_type):
    intensity = ei.from_prescription(_frozen("9", 9), _safety(9))
    assert ei.safety_event_for(set_type, 10, intensity) is None


def test_par_desconhecido_nao_registra_evento():
    assert ei.safety_event_for("working", 10, ei.UNKNOWN_INTENSITY) is None


# ---------------------------------------------------------------------------
# 3b. Lote 1/P1 (2026-10-11): RPE 10 com a falha negada SO pela recuperacao
# ---------------------------------------------------------------------------


def test_snapshot_congela_a_origem_da_negacao():
    assert _frozen("10", 10)["failure_denied_by"] is None
    assert _frozen("10", 10, ts.REDUCED_DOSE)["failure_denied_by"] == ts.FAILURE_DENIED_BY_RECOVERY
    assert _frozen("10", 9)["failure_denied_by"] == ts.FAILURE_DENIED_BY_PAIR
    # empate/ambos: o par vence
    assert _frozen("10", 9, ts.REDUCED_DOSE)["failure_denied_by"] == ts.FAILURE_DENIED_BY_PAIR


@pytest.mark.parametrize(
    "snapshot,esperado",
    [
        ({"failure_allowed": True, "safety_constraints": {"max_safe_rpe": 10}}, None),
        ({"failure_allowed": False, "safety_constraints": {"max_safe_rpe": 10}}, ts.FAILURE_DENIED_BY_RECOVERY),
        ({"failure_allowed": False, "safety_constraints": {"max_safe_rpe": 9}}, ts.FAILURE_DENIED_BY_PAIR),
        ({"failure_allowed": False, "safety_constraints": {"max_safe_rpe": None}}, ts.FAILURE_DENIED_BY_PAIR),
        ({"failure_allowed": False}, ts.FAILURE_DENIED_BY_PAIR),
        (None, None),
    ],
)
def test_snapshot_antigo_sem_o_campo_deriva_a_origem(snapshot, esperado):
    assert pd.failure_denied_by_of(snapshot) == esperado


def test_overlay_preserva_a_origem_e_o_par_de_hoje_vence():
    frozen = _frozen("10", 10, ts.REDUCED_DOSE)
    shown, overlay = pd.apply_safety_overlay(frozen, _safety(10))
    assert shown["failure_denied_by"] == overlay["failure_denied_by"] == ts.FAILURE_DENIED_BY_RECOVERY
    # o equipamento passou a proibir a falha depois do congelamento
    shown, overlay = pd.apply_safety_overlay(frozen, _safety(9))
    assert shown["failure_denied_by"] == overlay["failure_denied_by"] == ts.FAILURE_DENIED_BY_PAIR
    # snapshot antigo (sem o campo) em dose reduzida: deriva RECOVERY
    antigo = {key: value for key, value in frozen.items() if key != "failure_denied_by"}
    shown, _ = pd.apply_safety_overlay(antigo, _safety(10))
    assert shown["failure_denied_by"] == ts.FAILURE_DENIED_BY_RECOVERY


def test_rpe10_com_falha_negada_so_pela_recuperacao_e_informativo():
    intensity = ei.from_prescription(_frozen("10", 10, ts.REDUCED_DOSE), _safety(10))
    assert (intensity.ceiling, intensity.failure_allowed) == (9, False)
    assert intensity.failure_denied_by == ts.FAILURE_DENIED_BY_RECOVERY
    assert ei.safety_event_for("working", 10, intensity) == SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET
    assert ei.safety_event_for("working", 9, intensity) is None


def test_rpe10_em_par_proibido_e_dose_reduzida_continua_evento_de_seguranca():
    intensity = ei.from_prescription(_frozen("10", 9, ts.REDUCED_DOSE), _safety(9))
    assert ei.safety_event_for("working", 10, intensity) == SAFETY_EVENT_RPE_ABOVE_CEILING


def test_restrict_ceiling_do_cliente_que_tira_a_falha_e_da_recuperacao():
    base = ei.from_prescription(_frozen("10", 10), _safety(10))
    restrito = ei.restrict_ceiling(base, 9)
    assert restrito.failure_denied_by == ts.FAILURE_DENIED_BY_RECOVERY
    # par que ja proibia segue PAIR
    assert ei.restrict_ceiling(ei.from_safety(_safety(9)), 8).failure_denied_by == ts.FAILURE_DENIED_BY_PAIR


@pytest.mark.parametrize(
    "rpe,denied_by,esperado",
    [
        (Decimal(10), ts.FAILURE_DENIED_BY_PAIR, INTENSITY_ABOVE_SAFE_CEILING),
        (Decimal(10), None, INTENSITY_ABOVE_SAFE_CEILING),  # sem origem: conservador (par)
        (Decimal(10), ts.FAILURE_DENIED_BY_RECOVERY, INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING),
        (Decimal(8), ts.FAILURE_DENIED_BY_RECOVERY, INTENSITY_BELOW_TARGET),
    ],
)
def test_intensity_status_so_o_par_gera_above_safe_ceiling(rpe, denied_by, esperado):
    assert intensity_status_for(rpe, 9, False, failure_denied_by=denied_by) == esperado


def test_motor_classifica_o_rpe10_informativo_sem_precedencia():
    outcome = evaluate_track_session(
        fx.execution(
            set_list=fx.sets(reps=12, rpe=10, weight=50, count=3),
            prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10),
        ),
        fx.track(),
        reps_min=10,
        reps_max=12,
        ceiling=9,
        failure_allowed=False,
        failure_denied_by=ts.FAILURE_DENIED_BY_RECOVERY,
    )
    assert outcome.outcome == QUALIFY
    assert outcome.intensity_status == INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    assert {event["type"] for event in outcome.safety_events} == {SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET}
    assert {event["failure_denied_by"] for event in outcome.safety_events} == {ts.FAILURE_DENIED_BY_RECOVERY}


def test_status_da_sessao_com_evento_informativo_nao_vira_laranja():
    sets = [
        {"rpe": 9, "rpe_target_effective": 9},
        {"rpe": 10, "rpe_target_effective": 9, "safety_event": SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET},
    ]
    assert ei.session_intensity_status(sets, failure_allowed=False) == INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    # o evento do par, se houver, continua vencendo
    sets.append({"rpe": 10, "rpe_target_effective": 9, "safety_event": SAFETY_EVENT_RPE_ABOVE_CEILING})
    assert ei.session_intensity_status(sets, failure_allowed=False) == INTENSITY_ABOVE_SAFE_CEILING


def test_rpe10_registrado_qualifica_e_nao_eleva_o_alvo_futuro():
    """Secao 5.7 ponta a ponta no motor (que este PR NAO altera): num par onde
    a falha e proibida, RPE 10 nas series qualifica a sessao, gera
    `safety_event` e o alvo de esforco segue sendo 9 - nunca 10."""
    outcome = evaluate_track_session(
        fx.execution(
            set_list=fx.sets(reps=12, rpe=10, weight=50, count=3),
            prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10),
        ),
        fx.track(),
        reps_min=10,
        reps_max=12,
        ceiling=9,
        failure_allowed=False,
    )

    assert outcome.outcome == QUALIFY
    assert outcome.effort_target_rpe == Decimal(9)
    assert outcome.intensity_status == INTENSITY_ABOVE_SAFE_CEILING
    assert [event["type"] for event in outcome.safety_events] == [SAFETY_EVENT_RPE_ABOVE_CEILING] * 3


@pytest.mark.parametrize(
    "sets,failure_allowed,esperado",
    [
        # RPE 9 na ultima com alvo 9 em par proibido: alvo atingido.
        ([{"rpe": 8, "rpe_target_effective": 9}, {"rpe": 9, "rpe_target_effective": 9}], False, INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING),
        # Alvo 8 num par que permite falha: 8 basta (o teto nao e piso).
        ([{"rpe": 8, "rpe_target_effective": 8}], True, INTENSITY_REACHED),
        ([{"rpe": 7, "rpe_target_effective": 9}], False, INTENSITY_BELOW_TARGET),
        # `safety_event` gravado vence qualquer outro status.
        ([{"rpe": 10, "rpe_target_effective": 9, "safety_event": SAFETY_EVENT_RPE_ABOVE_CEILING}], False, INTENSITY_ABOVE_SAFE_CEILING),
        # Serie legada, sem alvo gravado: nada e inventado.
        ([{"rpe": 9}], False, None),
        ([], False, None),
    ],
)
def test_intensity_status_da_sessao_para_dashboards(sets, failure_allowed, esperado):
    assert ei.session_intensity_status(sets, failure_allowed=failure_allowed) == esperado


def test_intensity_status_ignora_series_preparatorias():
    sets = [
        {"set_type": "warmup", "rpe": 10, "rpe_target_effective": 9},
        {"set_type": "working", "rpe": 7, "rpe_target_effective": 9},
    ]
    assert ei.session_intensity_status(sets, failure_allowed=False) == INTENSITY_BELOW_TARGET


# ---------------------------------------------------------------------------
# 4. D11: a flag de usuario avancado nao participa da seguranca
# ---------------------------------------------------------------------------


def test_is_advanced_user_nao_aparece_no_modulo_de_intensidade():
    fonte = inspect.getsource(ei)
    assert "advanced_user" not in fonte.replace("`is_advanced_user` nao entra em nenhuma funcao deste modulo (D11)", "")


def test_usuario_avancado_nao_afrouxa_par_proibido():
    """A intensidade efetiva de um par com `max_safe_rpe=9` e a MESMA
    independente de qualquer flag do usuario: nao existe parametro para
    afrouxa-la (D11). O teste prova pela assinatura + pelo resultado."""
    assert "advanced" not in str(inspect.signature(ei.from_prescription))
    assert "advanced" not in str(inspect.signature(ei.from_legacy_plan))
    intensity = ei.from_legacy_plan("10", _safety(9))
    assert (intensity.rpe_target_effective, intensity.failure_allowed) == (9, False)


# ---------------------------------------------------------------------------
# 5. Regressao: as penalidades de RPE 10 precoce sumiram (secao 11)
# ---------------------------------------------------------------------------


_COM_DOCSTRING = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _codigo_executavel(module) -> str:
    """Fonte do modulo SEM comentarios nem docstrings - o que o Python de fato
    executa. Sem isso um teste estrutural falharia so porque um comentario CITA
    a penalidade removida, e citar e exatamente o que os comentarios deste
    codigo fazem, de proposito."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, _COM_DOCSTRING) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    return ast.unparse(tree)


def test_nenhuma_penalidade_de_rpe10_precoce_no_codigo():
    """Secao 11 (D4 revisada): a reducao de -5% da progressao, o
    `rpe_anticipated` do volume e o `failure_penalty` da recuperacao nao
    existem mais como CALCULO - e nenhum deles volta escondido atras de
    `is_advanced_user`.

    O comportamento esta coberto por `test_recovery_rpe10_sem_penalidade.py`
    e `test_rpe_alto_sem_penalidade.py`; aqui a garantia e estrutural.

    M76 (Lote 1a): o motor legado de carga (`app/progression_service.py`, que
    tinha o +5%) foi APAGADO - a sugestao de carga e so do motor v2
    (`domain/prescription_service`), e o modulo nao pode voltar."""
    import importlib.util

    from app.domain import prescription_service
    from app.intelligence import muscle_recovery_service, volume_service

    assert importlib.util.find_spec("app.progression_service") is None
    for module in (prescription_service, volume_service, muscle_recovery_service):
        codigo = _codigo_executavel(module)
        assert "failure_penalty" not in codigo, module.__name__
        assert "is_advanced_user" not in codigo, module.__name__
        # `rpe_anticipated` sobrevive SO como campo deprecado, sempre False.
        for linha in codigo.splitlines():
            if "rpe_anticipated" in linha:
                assert "False" in linha, (module.__name__, linha)


# ---------------------------------------------------------------------------
# 3c. Lote 1/P1 x Lote 2/P7: a avaliacao contra a ficha congelada usa a MESMA
# origem da negacao que a tela usou para gravar o evento
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frozen_kwargs,esperado",
    [
        # snapshot novo: origem congelada
        ({"failure_denied_by": ts.FAILURE_DENIED_BY_RECOVERY, "pair_max_safe_rpe": 10}, ts.FAILURE_DENIED_BY_RECOVERY),
        # snapshot antigo sem a origem: derivada do teto do par congelado
        ({"pair_max_safe_rpe": 10}, ts.FAILURE_DENIED_BY_RECOVERY),
        ({"pair_max_safe_rpe": 9}, ts.FAILURE_DENIED_BY_PAIR),
        ({}, ts.FAILURE_DENIED_BY_PAIR),
    ],
)
def test_avaliacao_com_ficha_congelada_herda_a_origem_da_negacao(frozen_kwargs, esperado):
    from app.domain.progression_evaluator import SessionInputs
    from app.domain.training_history import FrozenPrescription

    frozen = FrozenPrescription(
        sets_count=3, effective_rpe_target=9, effective_rpe_ceiling=9, failure_allowed=False, **frozen_kwargs
    )
    execution = fx.execution(
        set_list=fx.sets(reps=12, rpe=10, weight=50, count=3),
        prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=Decimal(9), frozen=frozen),
    )
    inputs = SessionInputs(safety={(1, 1): fx.safety(max_safe_rpe=10)})
    ceiling, failure_allowed, _status, denied_by = inputs.evaluation_intensity_for(execution, (1, 1), None)
    assert (ceiling, failure_allowed, denied_by) == (9, False, esperado)
