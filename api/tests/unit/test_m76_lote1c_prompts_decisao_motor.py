"""M76 Lote 1c: os prompts do Modo Treino recebem a decisão PRONTA do motor de
progressão v2 e a IA por série sai no passo do equipamento.

Mapa: `docs/motores/FONTES_DA_VERDADE.md` (C1, C11). Sem banco e sem OpenAI.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app import engine_decision_context as edc
from app import exercise_set_evaluation_ai as set_ai
from app import exercise_summary_ai as exercise_ai
from app import workout_ai_generation_core as core
from app import workout_summary_ai as workout_ai
from app.domain import progression_engine as pe
from app.domain.next_set_load import DEFAULT_RULESET
from app.domain.training_safety import FAILURE_RPE, IntensityComposition
from tests.support.fake_db import SharedCursorConn
from tests.support.privado import exigir

RAIZ = Path(__file__).resolve().parents[3]
PROGRESSAO = RAIZ / "api" / "privado" / "rag" / "markdown" / "PROGRESSAO.md"

DECISAO = edc.EngineDecision(
    decision=pe.INCREASE_LOAD,
    load_kg=42.5,
    reps_min=10,
    reps_max=12,
    sets_count=3,
    rpe_target=9,
)
PASSO = Decimal("2.5")
CONTEXTO = edc.PairEngineContext(step_kg=PASSO, decision=DECISAO)


# ---------------------------------------------------------------------------
# Passo do equipamento: regra do motor, não cópia
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("carga", "passo", "esperado"),
    [
        (44.63, "2.5", 45.0),  # o +5% sem passo do legado (C3) cai no passo
        (44.63, "1", 45.0),
        (41.2, "5", 40.0),
        (43.75, "2.5", 45.0),  # meio para cima, como o motor
        (44.63, None, 45.0),  # NULL -> DEFAULT_LOAD_STEP_KG do motor
        (1.0, "2.5", 2.5),  # nunca arredonda para zero
    ],
)
def test_round_to_equipment_step_usa_o_arredondamento_do_motor(carga, passo, esperado):
    assert edc.round_to_equipment_step(carga, passo) == esperado


@pytest.mark.parametrize("carga", [None, "", "abc", 0, -5, True])
def test_round_to_equipment_step_sem_carga_valida_devolve_none(carga):
    assert edc.round_to_equipment_step(carga, "2.5") is None


def test_passo_default_e_o_do_motor():
    assert edc.equipment_step_kg(None) == pe.DEFAULT_LOAD_STEP_KG
    assert edc.equipment_step_kg("1.25") == Decimal("1.25")


def test_carga_da_ia_nunca_passa_da_carga_alvo_do_motor():
    carga, ajustes = edc.bound_ai_load(47.3, CONTEXTO)
    assert carga == 42.5
    assert [a["reason"] for a in ajustes] == [edc.REASON_EQUIPMENT_STEP, edc.REASON_ENGINE_TARGET]


def test_carga_da_ia_pode_reduzir_e_cai_no_passo():
    carga, ajustes = edc.bound_ai_load(38.2, CONTEXTO)
    assert carga == 37.5
    assert ajustes == [{"field": "load_kg", "from": 38.2, "to": 37.5, "reason": edc.REASON_EQUIPMENT_STEP}]


def test_carga_ja_no_passo_e_abaixo_do_alvo_passa_intacta():
    assert edc.bound_ai_load(40.0, CONTEXTO) == (40.0, [])


def test_sem_decisao_so_arredonda_ao_passo():
    carga, ajustes = edc.bound_ai_load(47.3, edc.PairEngineContext(step_kg=PASSO))
    assert carga == 47.5
    assert [a["reason"] for a in ajustes] == [edc.REASON_EQUIPMENT_STEP]


def test_quem_ja_esta_acima_do_alvo_nao_e_empurrado_para_baixo():
    """34% das séries com alvo em produção foram executadas acima dele: a IA
    pode manter a carga recém-executada, só não sobe além dela."""
    assert edc.bound_ai_load(45.0, CONTEXTO, executed_kg=45.0) == (45.0, [])
    carga, ajustes = edc.bound_ai_load(50.0, CONTEXTO, executed_kg=45.0)
    assert carga == 45.0
    assert ajustes == [{"field": "load_kg", "from": 50.0, "to": 45.0, "reason": edc.REASON_ENGINE_TARGET}]


def test_repetir_a_carga_executada_fora_do_passo_nao_arredonda():
    """Anilha em libra (85 lb = 38,56 kg): repetir a carga real é alcançável."""
    contexto = edc.PairEngineContext(step_kg=PASSO)
    assert edc.bound_ai_load(38.56, contexto, executed_kg=38.56) == (38.56, [])
    assert edc.bound_ai_load(40.82, contexto, executed_kg=38.56)[0] == 40.0


def test_ia_sem_carga_continua_sem_carga():
    assert edc.bound_ai_load(None, CONTEXTO) == (None, [])


# ---------------------------------------------------------------------------
# Leitura da decisão: snapshot congelado da ficha (por par) e
# progression_decisions (por grupo, ao finalizar)
# ---------------------------------------------------------------------------

# Formato real de produção (workout_plan_exercises.prescription_json, Modo B).
SNAPSHOT = json.dumps(
    {
        "engine_version": "mode-b-deterministic-v1",
        "decision": pe.HOLD,
        "phase": "F2",
        "sets_count": 3,
        "reps_target": "10-12",
        "load_target": "31,5kg",
        "rpe_target": "10",
        "effective_rpe_target": 9,
        "suggested_load_kg": 31.5,
    }
)


def test_decision_from_snapshot_le_o_snapshot_do_motor():
    decisao = edc.decision_from_snapshot(SNAPSHOT)
    assert decisao == edc.EngineDecision(pe.HOLD, 31.5, 10, 12, 3, 9)
    assert decisao.label == "Manter"
    assert edc.format_decision(decisao) == "Manter · carga alvo 31,5 kg · 10 a 12 reps · 3 séries · RPE alvo 9"


@pytest.mark.parametrize("snapshot", [None, "", "{}", '{"decision": null, "suggested_load_kg": 40}', "nao-json"])
def test_snapshot_sem_decisao_do_motor_e_none(snapshot):
    """Caminho legado (sem `decision`) ou snapshot ausente: sem decisão."""
    assert edc.decision_from_snapshot(snapshot) is None


def test_snapshot_sem_carga_numerica_fica_sem_carga_alvo():
    decisao = edc.decision_from_snapshot({"decision": pe.HOLD, "suggested_load_kg": None, "reps_target": "12"})
    assert (decisao.load_kg, decisao.reps_min, decisao.reps_max) == (None, 12, 12)


def test_decisoes_por_grupo_ficam_com_a_ultima_de_cada_grupo():
    grupos = edc.group_decisions_from_rows(
        [
            {"decision": pe.HOLD, "output_json": '{"decision": "HOLD"}', "phase_before": "F1", "phase_after": "F1",
             "muscle_group": "Peitoral"},
            {"decision": pe.HOLD, "output_json": {}, "phase_before": "F1", "phase_after": "F2", "muscle_group": "Peitoral"},
            {"decision": pe.SET_REP_RANGE_10_12, "output_json": {}, "phase_before": "F1", "phase_after": "F1",
             "muscle_group": "Costas"},
            {"decision": pe.HOLD, "output_json": {}, "muscle_group": None},
        ]
    )
    assert [edc.format_group_decision(g) for g in grupos] == [
        "Peitoral: Manter (fase F1 → F2)",
        "Costas: Definir faixa de 10 a 12 repetições (fase F1)",
    ]


class _Cursor:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.executed = []
        self.atual = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))
        self.atual = self.respostas.pop(0) if self.respostas else []

    def fetchone(self):
        return self.atual[0] if self.atual else None

    def fetchall(self):
        return self.atual


def _conn(*respostas) -> SharedCursorConn:
    return SharedCursorConn(_Cursor(respostas))


def test_avaliacao_de_serie_le_o_snapshot_da_sessao_em_andamento(monkeypatch):
    from app import progression_state
    from app.domain import effective_intensity

    monkeypatch.setattr(effective_intensity, "equipment_id_of_gym_equipment", lambda conn, gym: 7)
    monkeypatch.setattr(progression_state, "load_equipment_steps", lambda conn, ids: {7: Decimal("1.25")})
    conn = _conn([{"prescription_json": SNAPSHOT}])

    contexto = edc.load_pair_context(conn, 3, 11, 2)

    assert contexto.step_kg == Decimal("1.25")
    assert contexto.decision.load_kg == 31.5
    sql, params = conn.cur.executed[0]
    assert sql == edc.IN_PROGRESS_SNAPSHOT_SQL and params == (3, 11, 2)
    assert "session_status = 'in_progress'" in sql


def test_avaliacao_de_serie_sem_par_nao_le_snapshot(monkeypatch):
    from app import progression_state
    from app.domain import effective_intensity

    monkeypatch.setattr(effective_intensity, "equipment_id_of_gym_equipment", lambda conn, gym: None)
    monkeypatch.setattr(progression_state, "load_equipment_steps", lambda conn, ids: {})
    conn = _conn()
    contexto = edc.load_pair_context(conn, 3, None, None)
    assert contexto == edc.PairEngineContext(step_kg=pe.DEFAULT_LOAD_STEP_KG)
    assert conn.cur.executed == []


def test_resumo_de_treino_le_snapshot_por_exercicio_e_decisao_por_grupo():
    conn = _conn(
        [{"exercise_row_id": 501, "prescription_json": SNAPSHOT}, {"exercise_row_id": 502, "prescription_json": None}],
        [{"decision": pe.HOLD, "output_json": {}, "phase_before": "F2", "phase_after": "F2", "muscle_group": "Peitoral"}],
    )
    decisoes = edc.load_activity_decisions(conn, 3, 99)
    assert list(decisoes["exercises"]) == [501]
    assert [g.muscle_group for g in decisoes["groups"]] == ["Peitoral"]
    assert conn.cur.executed[0] == (edc.ACTIVITY_SNAPSHOTS_SQL, (99, 3))
    assert conn.cur.executed[1] == (edc.ACTIVITY_GROUP_DECISIONS_SQL, (3, 99))


def test_leitura_que_falha_nao_derruba_a_ia():
    def explode():
        raise RuntimeError("tabela ausente")

    assert edc.safe_read(explode, default={}) == {}


# ---------------------------------------------------------------------------
# Prompt da avaliação de série
# ---------------------------------------------------------------------------

PAYLOAD = {
    "exercise_name": "Supino reto",
    "set_number": 2,
    "sets_count": 3,
    "reps_target": "10-12",
    "load_target": "40kg",
    "rpe_target": "8-9",
    "reps": 12,
    "weight_kg": 40,
    "rpe": 8,
}


def _composicao(ceiling=9, failure_allowed=False):
    return IntensityComposition(ceiling=ceiling, failure_allowed=failure_allowed, target=ceiling)


def test_prompt_da_serie_recebe_a_decisao_pronta_e_o_teto_composto():
    prompt = set_ai.build_prompt(PAYLOAD, "RAG", engine=CONTEXTO, composition=_composicao())
    exigir("Aumentar carga · carga alvo 42,5 kg · 10 a 12 reps" in prompt, "decisão pronta do motor")
    exigir(set_ai._format_engine_section(CONTEXTO) in prompt, "seção da decisão do motor")
    exigir(set_ai._text(".passo", {"passo": "2,5 kg"}) in prompt, "passo do equipamento")
    exigir(set_ai._text(".motor_limite", {"carga_alvo": "42,5 kg"}) in prompt, "limite na carga alvo do motor")
    ultima = set_ai._text(".ultima_serie_par", {"teto": "9", "falha": set_ai._text(".falha_negada_par")})
    exigir(set_ai._format_intensity_rule(PAYLOAD, _composicao()) in prompt and ultima in prompt, "teto composto do par")
    exigir(f"RPE {FAILURE_RPE}" in prompt, "RPE de falha da fonte")
    exigir("5%" not in prompt and "RPE 9 ou 10" not in prompt, "sem número inventado")


def test_prompt_da_serie_sem_decisao_nao_propoe_progressao():
    motor = edc.PairEngineContext(step_kg=PASSO)
    prompt = set_ai.build_prompt(PAYLOAD, "RAG", engine=motor)
    exigir(set_ai._text(".nenhuma_decisao") in prompt, "nenhuma decisão registrada")
    exigir(set_ai._format_engine_section(motor) in prompt, "seção sem decisão")
    # sem composição: nenhum número inventado
    exigir(set_ai._text(".ultima_serie_sem_composicao") in prompt, "teto do par sem número")


def test_resposta_da_ia_sai_no_passo_e_na_direcao_do_motor():
    resultado = set_ai.apply_engine_load_bound(
        {"suggested_next_load_kg": 46.0, "clamps": [{"field": "rpe"}]}, CONTEXTO
    )
    assert resultado["suggested_next_load_kg"] == 42.5
    assert resultado["engine_decision"] == pe.INCREASE_LOAD
    assert resultado["load_step_kg"] == 2.5
    assert [a["reason"] for a in resultado["load_adjustments"]] == [
        edc.REASON_EQUIPMENT_STEP,
        edc.REASON_ENGINE_TARGET,
    ]
    # a UI mostra todo clamp como corte de RPE: carga nunca entra em clamps[]
    assert resultado["clamps"] == [{"field": "rpe"}]


def test_dispatch_entrega_a_decisao_a_ia_e_limita_a_carga(monkeypatch):
    recebido = {}

    def fake_evaluate_set(payload, **kwargs):
        recebido.update(kwargs)
        return {
            "model": "gpt-mock",
            "category": "INCREASE_LOAD",
            "note": "nota",
            "raw_text": "",
            "suggested_next_reps": 10.0,
            "suggested_next_load_kg": 47.0,
            "suggested_next_rpe": 8.0,
        }

    monkeypatch.setattr(core, "evaluate_set", fake_evaluate_set)
    monkeypatch.setattr(core, "_evaluate_set_engine", lambda payload, user_id: CONTEXTO)
    monkeypatch.setattr(core.effective_intensity, "resolve_pair_safety", lambda conn, e, q: None)

    class _T:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(core, "transaction", lambda: _T())
    monkeypatch.setattr(core, "load_published_ruleset_autocommit", lambda: DEFAULT_RULESET)  # R2: sem banco

    # Rampa planejada (set-eval-v7): a serie 2 de "8-9" esta planejada em RPE 9,
    # entao 12 reps em RPE 8 continuam "carga leve" (sem a serie 1 no payload,
    # a serie seria a 1a da rampa, planejada 8, e a regra manteria).
    anterior = [{"set_number": 1, "reps": 12, "weight_kg": 40, "rpe": 8, "set_type": "working"}]
    resultado = core.dispatch_evaluate_set(
        dict(PAYLOAD, mapping_id=11, equipment_id=2, previous_sets_this_session=anterior), 3
    )

    assert recebido["engine"] is CONTEXTO
    assert recebido["composition"].ceiling == 9  # D3: par desconhecido
    assert resultado["suggested_next_load_kg"] == 42.5
    assert resultado["clamps"] == []
    # Lote 6/P9: sem exercicio lancado, o status vem da intensidade do par
    # (sem alvo efetivo -> sem status). set-eval-v6: 12 reps em RPE 8 -> a
    # regra da proxima serie sobe um passo (40 -> 42,5); a carga final e a
    # dela e a categoria "Aumentar carga".
    assert recebido["next_set"].direction == pe.INCREASE_LOAD
    assert resultado["next_set_load"]["load_kg"] == 42.5
    assert recebido["set_reading"].status is None
    assert resultado["category"] == pe.INCREASE_LOAD
    assert resultado["category_label"] == "Aumentar carga"


# ---------------------------------------------------------------------------
# RPE 10 Lote 6/P9: uma voz no Modo Treino (categorias do motor + status)
# ---------------------------------------------------------------------------


def test_schema_da_ia_por_serie_tem_so_acoes_do_motor():
    enum = set_ai.SET_EVALUATION_SCHEMA["properties"]["category"]["enum"]
    assert enum == [pe.HOLD, pe.INCREASE_LOAD, pe.DECREASE_LOAD]
    assert set_ai.PROMPT_VERSION == "set-eval-v8"


def test_categoria_segue_a_direcao_da_carga_final():
    """"Aumentar carga" com a carga limitada a executada vira "Manter": a IA
    nao contradiz o numero que a tela aplica."""
    resultado = set_ai.apply_engine_load_bound(
        {"category": pe.INCREASE_LOAD, "suggested_next_load_kg": 50.0, "clamps": []},
        edc.PairEngineContext(step_kg=PASSO, decision=edc.EngineDecision(decision=pe.HOLD, load_kg=40.0)),
        executed_kg=40,
    )
    assert resultado["suggested_next_load_kg"] == 40.0
    assert resultado["category"] == pe.HOLD
    assert resultado["category_from_ai"] == pe.INCREASE_LOAD
    assert resultado["category_label"] == "Manter"


def test_categoria_sem_carga_sugerida_fica_a_da_ia():
    resultado = set_ai.apply_engine_load_bound(
        {"category": pe.DECREASE_LOAD, "suggested_next_load_kg": None, "clamps": []}, CONTEXTO, executed_kg=40
    )
    assert resultado["category"] == pe.DECREASE_LOAD
    assert "category_from_ai" not in resultado


def test_prompt_recebe_o_status_da_serie_do_motor_com_o_rotulo_do_dono():
    from app.domain.effective_intensity import EffectiveIntensity, set_intensity_reading
    from app.domain.intensity_status_labels import INTENSITY_STATUS_LABELS

    intensity = EffectiveIntensity(
        rpe_target_effective=9, ceiling=9, failure_allowed=False, failure_denied_by="RECOVERY"
    )
    leitura = set_intensity_reading("working", 10, intensity)
    assert leitura.status == pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    assert leitura.safety_event == pe.SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET
    prompt = set_ai.build_prompt(PAYLOAD, "", engine=CONTEXTO, composition=_composicao(), set_reading=leitura)
    rotulo = INTENSITY_STATUS_LABELS[leitura.status]
    exigir(set_ai._format_set_status(leitura) in prompt, "status da série no prompt")
    # set-eval-v5: status de ESFORCO, numeros entre parenteses (sem o " · " que a IA copiava).
    detalhe = " (RPE executado 10; alvo efetivo do dia RPE 9; teto RPE 9, falha não permitida hoje)"
    exigir(set_ai._text(".status", {"rotulo": rotulo, "detalhe": detalhe}) in prompt, "esforço com os números da leitura")
    exigir("RPE acima do alvo do dia (" in prompt and set_ai._text(".evento_informativo") in prompt, "evento informativo")
    exigir('HOLD ("Manter"), INCREASE_LOAD ("Aumentar carga"), DECREASE_LOAD ("Reduzir carga")' in prompt, "categorias")
    for antiga in ("PROGREDIR", "APRIMORAR", "REGREDIR"):
        exigir(antiga not in prompt, f"sem a categoria antiga {antiga}")


def test_regra_critica_diz_que_a_falha_foi_negada_pela_recuperacao():
    composicao = IntensityComposition(ceiling=9, failure_allowed=False, target=9, failure_denied_by="RECOVERY")
    prompt = set_ai.build_prompt(PAYLOAD, "", engine=CONTEXTO, composition=composicao)
    exigir(set_ai._text(".ultima_serie_recuperacao", {"teto": "9"}) in prompt, "falha negada pela recuperação")
    exigir(set_ai._text(".falha_negada_par") not in prompt, "sem a negação do par")
    par = set_ai.build_prompt(PAYLOAD, "", engine=CONTEXTO, composition=_composicao())
    ultima = set_ai._text(".ultima_serie_par", {"teto": "9", "falha": set_ai._text(".falha_negada_par")})
    exigir(ultima in par, "falha negada pelo par")


def test_prompt_sem_status_nao_inventa():
    prompt = set_ai.build_prompt(PAYLOAD, "", engine=CONTEXTO, composition=_composicao())
    exigir(set_ai._text(".status_sem_alvo") in prompt, "sem alvo de esforço")


def test_dispatch_usa_a_intensidade_do_exercicio_lancado(monkeypatch):
    """Com exercicio da sessao em andamento, o status e o mesmo do salvamento
    da serie (ficha congelada), nao o do par sem alvo."""
    from app.domain.effective_intensity import EffectiveIntensity

    recebido = {}

    def fake_evaluate_set(payload, **kwargs):
        recebido.update(kwargs)
        return {"model": "m", "category": pe.HOLD, "note": "", "raw_text": "", "suggested_next_reps": None,
                "suggested_next_load_kg": None, "suggested_next_rpe": None}

    contexto = edc.PairEngineContext(step_kg=PASSO, decision=DECISAO, exercise_row_id=77)
    monkeypatch.setattr(core, "evaluate_set", fake_evaluate_set)
    monkeypatch.setattr(core, "_evaluate_set_engine", lambda payload, user_id: contexto)
    monkeypatch.setattr(core.effective_intensity, "resolve_pair_safety", lambda conn, e, q: None)
    lidos = []

    def fake_resolve(conn, exercise_id):
        lidos.append(exercise_id)
        return EffectiveIntensity(rpe_target_effective=8, ceiling=10, failure_allowed=True)

    monkeypatch.setattr(core.effective_intensity, "resolve_for_logged_exercise", fake_resolve)

    class _T:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(core, "transaction", lambda: _T())
    monkeypatch.setattr(core, "load_published_ruleset_autocommit", lambda: DEFAULT_RULESET)  # R2: sem banco
    core.dispatch_evaluate_set(dict(PAYLOAD, mapping_id=11, equipment_id=2), 3)
    assert lidos == [77]
    assert recebido["set_reading"].status == pe.INTENSITY_REACHED  # RPE 8 >= alvo 8

    recebido.clear()
    core.dispatch_evaluate_set(dict(PAYLOAD, mapping_id=11, equipment_id=2, set_type="rest_pause"), 3)
    assert recebido["set_reading"].status is None  # aplicacao de tecnica: sem status


# ---------------------------------------------------------------------------
# Resumos de exercício e de treino
# ---------------------------------------------------------------------------


def test_resumo_de_exercicio_recebe_a_decisao_e_nao_sugere_carga():
    # A orientacao (prosa do template) e privada (M103 P2,
    # `api/privado/tests/unit/test_exercise_summary_prompt.py`).
    prompt = exercise_ai.build_prompt({"exercise_name": "Supino reto", "sets": [], "engine_decision": DECISAO})
    assert f"- {edc.format_decision(DECISAO)}" in prompt
    assert "Aumentar carga · carga alvo 42,5 kg" in prompt
    assert "progredir carga" not in prompt  # o exemplo antigo induzia a IA a decidir


def test_resumo_de_exercicio_sem_decisao_nao_sugere_carga():
    prompt = exercise_ai.build_prompt({"exercise_name": "Supino reto", "sets": []})
    com = exercise_ai.build_prompt({"exercise_name": "Supino reto", "sets": [], "engine_decision": DECISAO})
    assert edc.format_decision(DECISAO) not in prompt and prompt != com
    assert "{{" not in prompt


def test_resumo_de_treino_usa_as_decisoes_do_motor():
    activity = {
        "id": 99,
        "exercises": [
            {"id": 501, "original_name": "Supino reto", "sets": []},
            {"id": 502, "original_name": "Rosca direta", "sets": []},
        ],
    }
    grupos = [edc.GroupDecision("Peitoral", pe.HOLD, "F1", "F2")]
    prompt = workout_ai.build_prompt(activity, {"exercises": {501: DECISAO}, "groups": grupos})
    # Nome do exercicio delimitado como dado do usuario (IA-10).
    assert "Supino reto</dados_do_usuario>: 0 série(s) — sem séries — decisão do motor para hoje: Aumentar carga · carga alvo 42,5 kg" in prompt
    assert "Rosca direta</dados_do_usuario>: 0 série(s) — sem séries — decisão do motor para hoje: nenhuma" in prompt
    assert "- Peitoral: Manter (fase F1 → F2)" in prompt
    assert "5%" not in prompt


def test_resumo_de_treino_sem_decisoes_nao_quebra():
    prompt = workout_ai.build_prompt({"id": 99, "exercises": [{"id": 1, "original_name": "Supino", "sets": []}]})
    assert "decisão do motor para hoje: nenhuma" in prompt
    assert workout_ai._format_group_decisions(None) in prompt


# ---------------------------------------------------------------------------
# RAG injetado (PROGRESSAO.md entra inteiro na avaliação de série, insights,
# relatório e periodização)
# ---------------------------------------------------------------------------

TRECHOS_REMOVIDOS = (
    "+5%",
    "2,5% a 5%",
    "2,5% e 5%",
    "Qualquer série anterior em RPE 10:\nreduzir",  # contradizia a D4 (bloco final antigo)
    "manter e consolidar",
    "14.7",
    "RPE <= 8:\ncarga adequada",
    "menos de 8 séries",  # faixa de volume genérica (o dono é a metodologia)
    "grupo secundário relevante: 0,5 série",  # peso do secundário (dono: contribuições)
    "mais de 10%",  # limiar de fadiga (dono: recuperação)
    "entre 20% e 40%",
)


def test_rag_de_progressao_nao_tem_os_trechos_removidos(monkeypatch):
    monkeypatch.setattr(set_ai, "RAG_MARKDOWN_DIR", PROGRESSAO.parent)
    monkeypatch.setattr(set_ai, "ensure_rag_local", lambda: None)
    texto = set_ai.load_progressao_rag_text()
    assert texto == PROGRESSAO.read_text(encoding="utf-8")
    presentes = [trecho for trecho in TRECHOS_REMOVIDOS if trecho in texto]
    assert not presentes, f"PROGRESSAO.md voltou a ter número/regra à mão: {presentes}"


def test_rag_de_progressao_diz_que_a_decisao_vem_do_motor():
    texto = PROGRESSAO.read_text(encoding="utf-8")
    assert "Quem decide a progressão é o motor de progressão do sistema" in texto
    assert "último degrau" in texto  # D4 revisada (2026-09-18): alvo = último degrau da rampa
    assert texto.count("```") % 2 == 0  # cercas de código fechadas (o bloco final antigo quebrava)
