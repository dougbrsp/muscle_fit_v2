"""Regras deterministicas de insight (`insight_rules`, F1.6, plano §12).

Para CADA regra ligada: um caso que dispara e um caso que NAO dispara por
falta de dado minimo ou de comparabilidade (silencio, nao alerta). Alem
disso: nunca `CRITICAL`; fingerprint deterministica e dedupe; ordenacao por
severidade e confianca; maximo 2 em `next_actions`; regra de evento de
contexto escrita e DESLIGADA.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.dashboard import insight_rules as ir
from app.intelligence.dashboard.contracts import (
    DataNature,
    DataQuality,
    Insight,
    Severity,
    TargetSource,
    WindowInfo,
    WindowKind,
)
from app.intelligence.dashboard.metrics_registry import (
    SIBLING_MUSCLE_GROUP_PAIRS,
    THRESHOLDS,
    ExerciseProgressionStatus,
)

DATE_TO = date(2026, 9, 22)


def _window(days: int = 28, kind: WindowKind = WindowKind.ROLLING_28D) -> WindowInfo:
    return WindowInfo(
        kind=kind,
        date_from=DATE_TO - timedelta(days=days - 1),
        date_to=DATE_TO,
        timezone="America/Sao_Paulo",
        days=days,
    )


def _m(value, unit=None, nature=DataNature.DERIVED, quality=DataQuality.HIGH):
    return {
        "value": value,
        "unit": unit,
        "nature": nature,
        "metric_version": "x-v1",
        "data_quality": quality,
        "limitations": [],
    }


def _muscle_item(
    group_id: int,
    name: str,
    *,
    status: str = "ON_TARGET",
    weekly: float = 10.0,
    minimum: float = 8.0,
    target: float = 12.0,
    maximum: float = 16.0,
    source: TargetSource = TargetSource.ASSIGNED_TEMPLATE,
    direct: float = 8.0,
    indirect: float = 4.0,
    physical: float = 12.0,
    quality: DataQuality = DataQuality.HIGH,
    hours_since: float | None = 48.0,
):
    return {
        "muscle_group_id": group_id,
        "muscle_group": name,
        "status": status,
        "targets": {"min": _m(minimum), "target": _m(target), "max": _m(maximum), "source": source},
        "realized": {
            "physical_sets": _m(physical),
            "weekly_effective_sets": _m(weekly),
            "direct_sets": _m(direct),
            "indirect_sets": _m(indirect),
        },
        "frequency": {"direct_sessions": _m(3.0)},
        "hours_since_last_stimulus": _m(hours_since),
        "last_direct_local_date": DATE_TO,
        "data_quality": quality,
        "limitations": [],
    }


def _muscles(*items, unattributed: int = 0):
    return {"items": list(items), "target_source": TargetSource.ASSIGNED_TEMPLATE, "unattributed_sets": unattributed,
            "data_quality": DataQuality.HIGH, "limitations": []}


def _recovery(groups=(), level=Severity.INFO, quality=DataQuality.HIGH):
    return {
        "groups": list(groups),
        "assessment": {"level": level},
        "data_quality": quality,
        "limitations": [],
    }


def _readiness(group_id: int, status: str, pct: float = 60.0, remaining: float = 30.0):
    return {"muscle_group_id": group_id, "readiness_status": status, "recovery_pct": _m(pct), "remaining_hours": _m(remaining)}


def _exposure(load: float, reps: int, e1rm: float | None = 100.0):
    return {
        "best_load_kg": load,
        "total_reps": reps,
        "e1rm": e1rm,
        "comparability_key": {"equipment_id": 7, "load_convention": "total"},
        "local_date": DATE_TO.isoformat(),
    }


def _progression_item(exercise_id: int, status: ExerciseProgressionStatus, *, comparable: int = 4, last=DATE_TO, name=None):
    exposures = [_exposure(60.0, 30) for _ in range(comparable)]
    return {
        "exercise_id": exercise_id,
        "name": name or f"Exercicio {exercise_id}",
        "status": status.value,
        "status_reasons": ["no_change"],
        "comparable_exposures": comparable,
        "exposures_in_window": comparable,
        "last_local_date": last.isoformat(),
        "_exposures": exposures,
        # run comparavel montado por `classify_batch` (a mesma lista)
        "_comparable_run": exposures,
    }


def _ctx(**overrides) -> ir.InsightContext:
    base = dict(window=_window(), cycle_days=7, muscles=_muscles())
    base.update(overrides)
    return ir.InsightContext(**base)


def _validate(insights):
    """Todo insight produzido tem de fechar com o contrato `Insight`."""
    return [Insight.model_validate(item) for item in insights]


# ---------------------------------------------------------------------------
# Invariantes gerais
# ---------------------------------------------------------------------------


def test_severidade_nunca_e_critical():
    assert "CRITICAL" not in {s.value for s in Severity}


def test_regra_de_evento_de_contexto_esta_ligada_na_f21():
    """Ate a F2.1 esta regra era a unica DESLIGADA (`context_events` nao
    existia). Com a migracao 048 ela entra na avaliacao - e o teste passa a
    provar o contrario do que provava."""
    assert "PERFORMANCE_DROP_WITH_CONTEXT_EVENT" in ir.enabled_rule_codes()
    assert "PERFORMANCE_DROP_WITH_CONTEXT_EVENT" not in ir.disabled_rule_codes()
    ctx = _ctx(
        progression=[_progression_item(1, ExerciseProgressionStatus.REGRESSING)],
        context_events=[
            {"event_type": "alcohol", "event_type_label": "Álcool", "local_date": DATE_TO - timedelta(days=1)}
        ],
        context_events_available=True,
    )
    insights, counts = ir.evaluate_insights(ctx)
    assert counts["PERFORMANCE_DROP_WITH_CONTEXT_EVENT"] == 1
    emitido = [i for i in insights if i["code"] == "PERFORMANCE_DROP_WITH_CONTEXT_EVENT"]
    assert len(emitido) == 1
    assert emitido[0]["severity"] is Severity.INFO
    # Associacao, nunca causa: confianca baixa e a limitacao explicita.
    assert emitido[0]["data_quality"] is DataQuality.LOW
    assert any("não causa" in limite for limite in emitido[0]["limitations"])
    _validate(emitido)


def test_evento_com_duracao_cobre_a_exposicao_de_dentro_dele():
    """Viagem de 10 dias: treinar no terceiro dia e tao "durante o evento"
    quanto no primeiro. Antes da F2.1 a janela contava so do inicio."""
    inicio = DATE_TO - timedelta(days=9)
    ctx = _ctx(
        progression=[
            _progression_item(1, ExerciseProgressionStatus.REGRESSING, last=DATE_TO - timedelta(days=7))
        ],
        context_events=[
            {
                "event_type": "travel",
                "event_type_label": "Viagem",
                "local_date": inicio,
                "end_local_date": DATE_TO,
            }
        ],
        context_events_available=True,
    )
    emitido = ir.rule_performance_drop_with_context_event(ctx)
    assert len(emitido) == 1
    assert "durante" in emitido[0]["answer"]


def test_evento_muito_antes_da_exposicao_nao_associa():
    ctx = _ctx(
        progression=[_progression_item(1, ExerciseProgressionStatus.REGRESSING, last=DATE_TO)],
        context_events=[{"event_type": "alcohol", "local_date": DATE_TO - timedelta(days=10)}],
        context_events_available=True,
    )
    assert ir.rule_performance_drop_with_context_event(ctx) == []


# ---------------------------------------------------------------------------
# Colapso POR EVENTO (2026-09-25): um evento com queda associada e UM insight,
# nao um por exercicio. Medido em DEV: 3 eventos produziam 19 cartoes.
# ---------------------------------------------------------------------------


def _ctx_evento(*exercicios, event=None, **overrides) -> ir.InsightContext:
    return _ctx(
        progression=list(exercicios),
        context_events=[
            event
            or {
                "event_type": "gym_change",
                "event_type_label": "Troca de academia",
                "local_date": DATE_TO - timedelta(days=2),
            }
        ],
        context_events_available=True,
        **overrides,
    )


def test_um_evento_com_varias_quedas_vira_um_cartao_com_os_exercicios_na_evidencia():
    """O colapso do PR #197 aplicado aqui: a observacao real e UMA ("o que caiu
    em volta deste evento") e os exercicios afetados sao evidencia, com nome e
    numero, dentro do cartao."""
    emitido = ir.rule_performance_drop_with_context_event(
        _ctx_evento(
            *(
                _progression_item(
                    eid, ExerciseProgressionStatus.REGRESSING, last=DATE_TO - timedelta(days=1), name=nome
                )
                for eid, nome in ((7, "Agachamento livre"), (3, "Remada baixa"), (5, "Supino reto"))
            )
        )
    )
    assert len(emitido) == 1
    cartao = emitido[0]
    assert "3 exercícios regrediram" in cartao["answer"]
    # Nomes citados na frase, em ordem deterministica (por `exercise_id`).
    assert "Remada baixa, Supino reto, Agachamento livre" in cartao["answer"]
    contagem = [e for e in cartao["evidence"] if e["metric"] == "exercises_with_drop"]
    assert [e["to_value"] for e in contagem] == [3.0]
    por_exercicio = [e["metric"] for e in cartao["evidence"] if e["metric"].startswith("event_to_exposure_days[")]
    assert por_exercicio == [
        "event_to_exposure_days[Remada baixa]",
        "event_to_exposure_days[Supino reto]",
        "event_to_exposure_days[Agachamento livre]",
    ]
    # O que o colapso NAO muda: associacao, nunca causa.
    assert cartao["data_quality"] is DataQuality.LOW
    assert cartao["severity"] is Severity.INFO
    assert any("não causa" in limite for limite in cartao["limitations"])
    _validate(emitido)


def test_frase_diz_durante_e_depois_quando_as_exposicoes_se_dividem():
    fim = DATE_TO - timedelta(days=2)
    emitido = ir.rule_performance_drop_with_context_event(
        _ctx_evento(
            _progression_item(1, ExerciseProgressionStatus.REGRESSING, last=fim),
            _progression_item(2, ExerciseProgressionStatus.REGRESSING, last=fim + timedelta(days=2)),
        )
    )
    assert len(emitido) == 1
    assert "durante ou até 72h depois de" in emitido[0]["answer"]


def test_alvo_do_cartao_e_o_evento_a_impressao_nao_leva_exercicio():
    """A impressao (e, com ela, a chave de cooldown da F2.2) passa a identificar
    o EVENTO: entrar ou sair um exercicio afetado nao cria um alerta novo."""
    um = ir.rule_performance_drop_with_context_event(
        _ctx_evento(_progression_item(1, ExerciseProgressionStatus.REGRESSING, last=DATE_TO - timedelta(days=1)))
    )
    dois = ir.rule_performance_drop_with_context_event(
        _ctx_evento(
            _progression_item(1, ExerciseProgressionStatus.REGRESSING, last=DATE_TO - timedelta(days=1)),
            _progression_item(9, ExerciseProgressionStatus.REGRESSING, last=DATE_TO - timedelta(days=1)),
        )
    )
    assert um[0]["fingerprint"] == dois[0]["fingerprint"]
    assert "exercise_id" not in um[0]["comparison"]["keys"]
    assert um[0]["comparison"]["keys"] == {
        "event_type": "gym_change",
        "event_date": (DATE_TO - timedelta(days=2)).isoformat(),
    }


def test_dois_eventos_no_mesmo_periodo_continuam_dois_cartoes():
    """O colapso e POR EVENTO. Escolher um dos eventos seria inventar a
    hierarquia de causa que a regra se recusa a afirmar."""
    ctx = _ctx(
        progression=[_progression_item(1, ExerciseProgressionStatus.REGRESSING, last=DATE_TO)],
        context_events=[
            {"event_type": "alcohol", "event_type_label": "Álcool", "local_date": DATE_TO - timedelta(days=1)},
            {"event_type": "travel", "event_type_label": "Viagem", "local_date": DATE_TO - timedelta(days=2)},
        ],
        context_events_available=True,
    )
    emitido = ir.rule_performance_drop_with_context_event(ctx)
    assert len(emitido) == 2
    assert {i["comparison"]["keys"]["event_type"] for i in emitido} == {"alcohol", "travel"}
    _validate(emitido)


def test_evento_sem_queda_associada_nao_emite_cartao():
    emitido = ir.rule_performance_drop_with_context_event(
        _ctx_evento(_progression_item(1, ExerciseProgressionStatus.PROGRESSING, last=DATE_TO))
    )
    assert emitido == []


# ---------------------------------------------------------------------------
# F2.1 - pausa de treino ("voce parou de treinar")
# ---------------------------------------------------------------------------

_PAUSA_DIAS = int(THRESHOLDS["insight_training_pause_min_days"])
_PAUSA_SESSOES = int(THRESHOLDS["insight_training_pause_min_sessions_before"])


def _ctx_pausa(dias: int, *, sessoes_antes: int = _PAUSA_SESSOES, **overrides) -> ir.InsightContext:
    return _ctx(
        days_since_last_session=dias,
        last_session_local_date=DATE_TO - timedelta(days=dias),
        sessions_before_pause=sessoes_antes,
        **overrides,
    )


def test_pausa_dispara_acima_do_limiar_com_rotina_anterior():
    emitido = ir.rule_training_pause_detected(_ctx_pausa(_PAUSA_DIAS))
    assert len(emitido) == 1
    assert emitido[0]["code"] == "TRAINING_PAUSE_DETECTED"
    # Parar pode ser a decisao certa: observa, nao manda.
    assert emitido[0]["severity"] is Severity.ATTENTION
    assert emitido[0]["source_screen"] == "/dashboard/contexto"
    _validate(emitido)


def test_pausa_nao_dispara_um_dia_antes_do_limiar():
    assert ir.rule_training_pause_detected(_ctx_pausa(_PAUSA_DIAS - 1)) == []


def test_pausa_nao_fala_para_quem_ainda_nao_tinha_rotina():
    """Sem historico antes do silencio nao houve pausa - houve um comeco que
    nao aconteceu. Silencio, nao alerta."""
    assert ir.rule_training_pause_detected(_ctx_pausa(_PAUSA_DIAS * 3, sessoes_antes=_PAUSA_SESSOES - 1)) == []


def test_pausa_sem_sessao_nenhuma_no_historico_fica_em_silencio():
    assert ir.rule_training_pause_detected(_ctx()) == []


def test_pausa_cita_o_evento_de_contexto_que_a_cobre():
    ctx = _ctx_pausa(
        _PAUSA_DIAS + 4,
        context_events=[
            {
                "event_type": "travel",
                "event_type_label": "Viagem",
                "local_date": DATE_TO - timedelta(days=_PAUSA_DIAS),
                "end_local_date": DATE_TO,
            }
        ],
        context_events_available=True,
    )
    emitido = ir.rule_training_pause_detected(ctx)
    assert len(emitido) == 1
    assert "Viagem" in emitido[0]["answer"]


def test_pausa_sem_evento_diz_que_nada_foi_anotado():
    emitido = ir.rule_training_pause_detected(_ctx_pausa(_PAUSA_DIAS, context_events_available=True))
    assert "Nenhum evento de contexto anotado" in emitido[0]["answer"]


def test_limitacao_de_contexto_conta_a_verdade_de_cada_estado():
    """Tres estados, tres frases. Uma limitacao que mente e pior do que
    nenhuma - e era o que acontecia com a frase unica "a regra esta
    desligada" depois que a 048 nasceu."""
    indisponivel = ir.context_limitation(_ctx())
    vazio = ir.context_limitation(_ctx(context_events_available=True))
    com_evento = ir.context_limitation(
        _ctx(context_events_available=True, context_events=[{"event_type": "alcohol", "local_date": DATE_TO}])
    )
    assert indisponivel == ir.CONTEXT_EVENTS_UNAVAILABLE_LIMITATION
    assert vazio == ir.CONTEXT_EVENTS_EMPTY_LIMITATION
    assert com_evento == ir.CONTEXT_EVENTS_ASSOCIATION_LIMITATION
    assert len({indisponivel, vazio, com_evento}) == 3


def test_contexto_vazio_nao_dispara_nada():
    insights, counts = ir.evaluate_insights(_ctx())
    assert insights == []
    assert set(counts) == set(ir.enabled_rule_codes())
    assert all(v == 0 for v in counts.values())


def test_fingerprint_e_deterministica_e_ordena_as_chaves():
    a = ir.build_fingerprint("X", {"b": 2, "a": [1, 2]})
    b = ir.build_fingerprint("X", {"a": [1, 2], "b": 2})
    assert a == b == "X:a=1,2&b=2"


def test_dedupe_por_fingerprint_mantem_a_primeira():
    itens = [
        {"fingerprint": "f1", "severity": Severity.INFO, "data_quality": DataQuality.HIGH, "code": "A"},
        {"fingerprint": "f1", "severity": Severity.INFO, "data_quality": DataQuality.LOW, "code": "A"},
        {"fingerprint": "f2", "severity": Severity.INFO, "data_quality": DataQuality.HIGH, "code": "A"},
    ]
    resultado = ir.dedupe_by_fingerprint(itens)
    assert [i["fingerprint"] for i in resultado] == ["f1", "f2"]
    assert resultado[0]["data_quality"] is DataQuality.HIGH


def test_ordenacao_por_severidade_depois_confianca():
    itens = [
        {"fingerprint": "1", "severity": Severity.INFO, "data_quality": DataQuality.HIGH, "code": "A"},
        {"fingerprint": "2", "severity": Severity.ACTION, "data_quality": DataQuality.LOW, "code": "B"},
        {"fingerprint": "3", "severity": Severity.ATTENTION, "data_quality": DataQuality.LOW, "code": "C"},
        {"fingerprint": "4", "severity": Severity.ATTENTION, "data_quality": DataQuality.HIGH, "code": "D"},
    ]
    assert [i["fingerprint"] for i in ir.sort_insights(itens)] == ["2", "4", "3", "1"]


def test_select_next_actions_respeita_o_maximo_e_nao_inventa():
    itens = [{"fingerprint": str(i)} for i in range(5)]
    assert len(ir.select_next_actions(itens, THRESHOLDS["overview_max_next_actions"])) == 2
    assert ir.select_next_actions([], 2) == []


# ---------------------------------------------------------------------------
# 1) Abaixo do minimo por duas semanas (regua semanal + colapso)
# ---------------------------------------------------------------------------


def _weeks(current_items, previous_items, **overrides):
    """Contexto com as duas semanas; os demais grupos (com meta) ficam no
    alvo, para nao mexer na fracao do colapso sem querer."""
    return _ctx(muscles_current_week=_muscles(*current_items), muscles_previous_week=_muscles(*previous_items), **overrides)


def _below_both_weeks(group_id: int, name: str, *, now: float = 4.0, before: float = 5.0):
    return (
        _muscle_item(group_id, name, status="BELOW_MIN", weekly=now),
        _muscle_item(group_id, name, status="BELOW_MIN", weekly=before),
    )


def _on_target_both_weeks(group_id: int, name: str):
    return _muscle_item(group_id, name), _muscle_item(group_id, name)


def test_abaixo_do_minimo_dispara_so_com_duas_semanas_consecutivas():
    costas_agora, costas_antes = _below_both_weeks(6, "Costas")
    outros = [_on_target_both_weeks(i, f"Grupo {i}") for i in (1, 2, 3)]
    ctx = _weeks([costas_agora, *(a for a, _ in outros)], [costas_antes, *(b for _, b in outros)])
    out = _validate(ir.rule_muscle_below_min_consecutive_weeks(ctx))
    assert len(out) == 1
    insight = out[0]
    assert insight.code == "MUSCLE_BELOW_MIN_TWO_WEEKS"
    assert insight.severity is Severity.ATTENTION
    assert insight.fingerprint == "MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6"
    assert insight.comparison and insight.comparison.window == "7d x 2"
    assert insight.evidence[0].from_value == 5.0 and insight.evidence[0].to_value == 4.0
    assert insight.reassess_after == DATE_TO + timedelta(days=7)
    assert insight.suggested_action and "não muda sozinha" in insight.suggested_action


def test_abaixo_do_minimo_uma_semana_isolada_nao_conclui():
    grupo_atual = _muscle_item(6, "Costas", status="BELOW_MIN", weekly=4.0)
    grupo_antes = _muscle_item(6, "Costas", status="ON_TARGET", weekly=12.0)
    assert ir.rule_muscle_below_min_consecutive_weeks(_weeks([grupo_atual], [grupo_antes])) == []


def test_abaixo_do_minimo_regua_e_semanal_mesmo_com_modelo_de_56_dias():
    """O ciclo do Modelo (8 slots -> 56 dias) NAO entra na regua: janela,
    fingerprint e reavaliacao sao as mesmas de um ciclo de 7 dias."""
    costas_agora, costas_antes = _below_both_weeks(6, "Costas")
    outros = [_on_target_both_weeks(i, f"Grupo {i}") for i in (1, 2, 3)]
    current = [costas_agora, *(a for a, _ in outros)]
    previous = [costas_antes, *(b for _, b in outros)]
    com_modelo = _validate(ir.rule_muscle_below_min_consecutive_weeks(_weeks(current, previous, cycle_days=56)))
    sem_modelo = _validate(ir.rule_muscle_below_min_consecutive_weeks(_weeks(current, previous, cycle_days=7)))
    assert com_modelo == sem_modelo
    assert com_modelo[0].comparison.window == f"{THRESHOLDS['insight_below_min_week_days']}d x 2"
    assert com_modelo[0].comparison.keys == {"muscle_group_id": 6}
    assert com_modelo[0].reassess_after == DATE_TO + timedelta(days=7)
    assert "56" not in com_modelo[0].answer


def test_abaixo_do_minimo_silencia_sem_meta_real_ou_sem_sessao_na_semana():
    generico = _muscle_item(6, "Costas", status="BELOW_MIN", weekly=1.0, source=TargetSource.FALLBACK_GENERIC)
    assert ir.rule_muscle_below_min_consecutive_weeks(_weeks([generico], [generico])) == []

    # Semana anterior sem NENHUMA serie (ferias) -> nao e deficit, e ausencia.
    atual = _muscle_item(6, "Costas", status="BELOW_MIN", weekly=1.0)
    ferias = _muscle_item(6, "Costas", status="BELOW_MIN", weekly=0.0, physical=0.0)
    assert ir.rule_muscle_below_min_consecutive_weeks(_weeks([atual], [ferias])) == []

    # Sem as duas semanas no contexto -> silencio.
    assert ir.rule_muscle_below_min_consecutive_weeks(_ctx(muscles_current_week=_muscles(atual))) == []


def test_abaixo_do_minimo_colapsa_em_um_insight_quando_a_maioria_dispara():
    """3 de 4 grupos com meta (75% >= 50%): UM insight consolidado, ATTENTION,
    sem os individuais; a frase diz N de M e a lista completa vai na evidencia."""
    abaixo = [_below_both_weeks(i, f"Grupo {i}", now=float(i), before=float(i) + 1) for i in (3, 1, 2)]
    ok = _on_target_both_weeks(4, "Grupo 4")
    generico = _muscle_item(9, "Sem meta", status="BELOW_MIN", weekly=0.5, source=TargetSource.FALLBACK_GENERIC)
    ctx = _weeks([*(a for a, _ in abaixo), ok[0], generico], [*(b for _, b in abaixo), ok[1], generico])
    out = _validate(ir.rule_muscle_below_min_consecutive_weeks(ctx))
    assert len(out) == 1
    insight = out[0]
    assert insight.code == "TRAINING_VOLUME_BROADLY_BELOW_MIN"
    assert insight.severity is Severity.ATTENTION
    assert insight.answer.startswith("3 de 4 grupos com meta ficaram abaixo do mínimo nas últimas 2 semanas")
    assert "Grupo 1" not in insight.answer
    assert "pausa" in insight.answer
    assert insight.fingerprint == "TRAINING_VOLUME_BROADLY_BELOW_MIN:muscle_group_ids=1,2,3"
    assert insight.comparison and insight.comparison.window == "7d x 2"
    assert [e.metric for e in insight.evidence] == [
        "muscle_groups_below_min",
        "muscle_groups_with_target",
        "weekly_effective_sets[Grupo 1]",
        "weekly_effective_sets[Grupo 2]",
        "weekly_effective_sets[Grupo 3]",
    ]
    assert insight.evidence[0].to_value == 3 and insight.evidence[1].to_value == 4
    assert insight.evidence[2].from_value == 2.0 and insight.evidence[2].to_value == 1.0
    assert any("viagem" in alt and "doença" in alt for alt in insight.alternative_explanations)
    assert insight.suggested_action and "retomar o plano" in insight.suggested_action
    assert "16" not in insight.suggested_action and "aumentar" not in insight.suggested_action
    assert insight.reassess_after == DATE_TO + timedelta(days=14)
    assert insight.source_screen == "/dashboard/musculos"


def test_abaixo_do_minimo_colapso_no_limiar_exato_e_fingerprint_estavel():
    """2 de 4 (= 50%) ja colapsa; a mesma entrada, em outra ordem, da a mesma
    impressao."""
    abaixo = [_below_both_weeks(i, f"Grupo {i}") for i in (7, 5)]
    ok = [_on_target_both_weeks(i, f"Grupo {i}") for i in (1, 2)]
    direto = _weeks([*(a for a, _ in abaixo), *(a for a, _ in ok)], [*(b for _, b in abaixo), *(b for _, b in ok)])
    invertido = _weeks(
        list(reversed([*(a for a, _ in abaixo), *(a for a, _ in ok)])),
        list(reversed([*(b for _, b in abaixo), *(b for _, b in ok)])),
    )
    a = ir.rule_muscle_below_min_consecutive_weeks(direto)
    b = ir.rule_muscle_below_min_consecutive_weeks(invertido)
    assert [i["code"] for i in a] == ["TRAINING_VOLUME_BROADLY_BELOW_MIN"]
    assert a[0]["fingerprint"] == b[0]["fingerprint"] == "TRAINING_VOLUME_BROADLY_BELOW_MIN:muscle_group_ids=5,7"


def test_abaixo_do_minimo_abaixo_da_fracao_mantem_os_individuais():
    """1 de 4 grupos com meta (25% < 50%): cada grupo continua individual."""
    costas = _below_both_weeks(6, "Costas")
    ok = [_on_target_both_weeks(i, f"Grupo {i}") for i in (1, 2, 3)]
    ctx = _weeks([costas[0], *(a for a, _ in ok)], [costas[1], *(b for _, b in ok)])
    out = ir.rule_muscle_below_min_consecutive_weeks(ctx)
    assert [i["code"] for i in out] == ["MUSCLE_BELOW_MIN_TWO_WEEKS"]

    # 2 de 5 (40%): dois individuais, nenhum consolidado.
    peito = _below_both_weeks(2, "Peito")
    ok = [_on_target_both_weeks(i, f"Grupo {i}") for i in (1, 3, 4)]
    ctx = _weeks([costas[0], peito[0], *(a for a, _ in ok)], [costas[1], peito[1], *(b for _, b in ok)])
    out = ir.rule_muscle_below_min_consecutive_weeks(ctx)
    assert sorted(i["code"] for i in out) == ["MUSCLE_BELOW_MIN_TWO_WEEKS"] * 2


def test_evaluate_conta_o_codigo_consolidado_e_zera_a_regra_de_origem():
    abaixo = [_below_both_weeks(i, f"Grupo {i}") for i in (1, 2)]
    ctx = _weeks([a for a, _ in abaixo], [b for _, b in abaixo])
    insights, counts = ir.evaluate_insights(ctx)
    assert [i["code"] for i in insights] == ["TRAINING_VOLUME_BROADLY_BELOW_MIN"]
    assert counts["TRAINING_VOLUME_BROADLY_BELOW_MIN"] == 1
    assert counts["MUSCLE_BELOW_MIN_TWO_WEEKS"] == 0


# ---------------------------------------------------------------------------
# 2) Acima do maximo + prontidao piorando
# ---------------------------------------------------------------------------


def test_acima_do_maximo_exige_prontidao_piorando():
    grupo = _muscle_item(3, "Quadriceps", status="ABOVE_MAX", weekly=20.0, maximum=16.0)
    com_prontidao_ruim = _ctx(muscles=_muscles(grupo), recovery=_recovery([_readiness(3, "REDUCED_DOSE", 55.0, 30.0)]))
    out = _validate(ir.rule_muscle_above_max_readiness_strained(com_prontidao_ruim))
    assert len(out) == 1 and out[0].severity is Severity.ATTENTION
    assert out[0].reassess_after == DATE_TO + timedelta(days=2)

    pronto = _ctx(muscles=_muscles(grupo), recovery=_recovery([_readiness(3, "READY")]))
    assert ir.rule_muscle_above_max_readiness_strained(pronto) == []
    assert ir.rule_muscle_above_max_readiness_strained(_ctx(muscles=_muscles(grupo))) == []


# ---------------------------------------------------------------------------
# 3) Plato suspeito
# ---------------------------------------------------------------------------


def test_plato_exige_quatro_exposicoes_comparaveis():
    quatro = _progression_item(11, ExerciseProgressionStatus.PLATEAU_SUSPECTED, comparable=4)
    tres = _progression_item(12, ExerciseProgressionStatus.PLATEAU_SUSPECTED, comparable=3)
    out = _validate(ir.rule_exercise_plateau_suspected(_ctx(progression=[quatro, tres])))
    assert [i.fingerprint for i in out] == ["EXERCISE_PLATEAU_SUSPECTED:equipment_id=7&exercise_id=11&load_convention=total"]
    assert out[0].severity is Severity.INFO
    assert out[0].source_screen == "/dashboard/exercicios/11"
    assert out[0].data_quality is DataQuality.HIGH


def test_plato_sem_e1rm_rebaixa_a_confianca():
    item = _progression_item(11, ExerciseProgressionStatus.PLATEAU_SUSPECTED, comparable=4)
    item["_exposures"][-1]["e1rm"] = None
    out = ir.rule_exercise_plateau_suspected(_ctx(progression=[item]))
    assert out[0]["data_quality"] is DataQuality.MEDIUM


# ---------------------------------------------------------------------------
# 4) Varios exercicios regredindo
# ---------------------------------------------------------------------------


def test_regressao_exige_tres_exercicios_recentes():
    recentes = [_progression_item(i, ExerciseProgressionStatus.REGRESSING) for i in (1, 2, 3)]
    out = _validate(ir.rule_exercises_regressing(_ctx(progression=recentes)))
    assert len(out) == 1 and out[0].severity is Severity.ATTENTION
    assert out[0].comparison.keys == {"exercise_ids": [1, 2, 3]}

    dois = recentes[:2]
    assert ir.rule_exercises_regressing(_ctx(progression=dois)) == []

    # Terceiro fora dos 28 dias nao conta.
    antigo = _progression_item(9, ExerciseProgressionStatus.REGRESSING, last=DATE_TO - timedelta(days=40))
    assert ir.rule_exercises_regressing(_ctx(progression=dois + [antigo])) == []


# ---------------------------------------------------------------------------
# 5) Item pulado no fim da sessao
# ---------------------------------------------------------------------------


def test_item_pulado_no_fim_da_ficha():
    fim = {"plan_exercise_id": 50, "name": "Panturrilha", "plan_sequence": 8, "plan_items_total": 8, "sessions": 5, "skipped": 3, "last_local_date": DATE_TO}
    out = _validate(ir.rule_plan_item_skipped_at_end(_ctx(skipped_items=[fim])))
    assert len(out) == 1 and out[0].severity is Severity.INFO
    assert out[0].source_screen == "/dashboard/treinos"


def test_item_pulado_no_meio_ou_poucas_vezes_nao_dispara():
    meio = {"plan_exercise_id": 51, "name": "Supino", "plan_sequence": 2, "plan_items_total": 8, "sessions": 5, "skipped": 4}
    poucas = {"plan_exercise_id": 52, "name": "Panturrilha", "plan_sequence": 8, "plan_items_total": 8, "sessions": 5, "skipped": 2}
    assert ir.rule_plan_item_skipped_at_end(_ctx(skipped_items=[meio, poucas])) == []


# ---------------------------------------------------------------------------
# 6) Direto << indireto
# ---------------------------------------------------------------------------


def test_direto_muito_abaixo_do_indireto():
    grupo = _muscle_item(4, "Triceps", direct=1.0, indirect=10.0)
    out = _validate(ir.rule_direct_far_below_indirect(_ctx(muscles=_muscles(grupo))))
    assert len(out) == 1 and out[0].severity is Severity.INFO


def test_direto_indireto_silencia_com_poucas_series_ou_sem_meta():
    poucas = _muscle_item(4, "Triceps", direct=0.0, indirect=3.0)
    sem_meta = _muscle_item(5, "Biceps", direct=0.0, indirect=20.0, source=TargetSource.FALLBACK_GENERIC)
    ok = _muscle_item(6, "Costas", direct=8.0, indirect=10.0)
    assert ir.rule_direct_far_below_indirect(_ctx(muscles=_muscles(poucas, sem_meta, ok))) == []


# ---------------------------------------------------------------------------
# 7) Assimetria entre grupos irmaos
# ---------------------------------------------------------------------------


def test_assimetria_usa_o_par_do_registry():
    a, b = SIBLING_MUSCLE_GROUP_PAIRS[0]
    itens = _muscles(_muscle_item(a, "Costas largura", weekly=12.0, target=12.0), _muscle_item(b, "Costas miolo", weekly=4.0, target=12.0))
    out = _validate(ir.rule_sibling_asymmetry(_ctx(muscles=itens)))
    assert len(out) == 1
    assert out[0].comparison.keys == {"muscle_group_ids": [a, b]}
    assert "Costas miolo" in out[0].answer


def test_assimetria_silencia_sem_meta_real_ou_sem_treino():
    a, b = SIBLING_MUSCLE_GROUP_PAIRS[0]
    sem_treino = _muscles(_muscle_item(a, "A", weekly=0.0), _muscle_item(b, "B", weekly=0.0))
    assert ir.rule_sibling_asymmetry(_ctx(muscles=sem_treino)) == []
    generico = _muscles(
        _muscle_item(a, "A", weekly=12.0, source=TargetSource.FALLBACK_GENERIC),
        _muscle_item(b, "B", weekly=2.0),
    )
    assert ir.rule_sibling_asymmetry(_ctx(muscles=generico)) == []
    equilibrado = _muscles(_muscle_item(a, "A", weekly=11.0), _muscle_item(b, "B", weekly=10.0))
    assert ir.rule_sibling_asymmetry(_ctx(muscles=equilibrado)) == []


# ---------------------------------------------------------------------------
# 8) Recomposicao (INFERRED, sem cintura)
# ---------------------------------------------------------------------------


def _body(change_pct: float | None, span_days: int = 21):
    return {
        "weight_ma7_change_pct": _m(change_pct),
        "weight_ma7_latest": _m(80.1),
        "weight_ma7_first": _m(80.0),
        "span_days": span_days,
        "limitations": [],
    }


def test_recomposicao_declara_a_falta_de_cintura_e_rebaixa_a_confianca():
    progressao = [
        _progression_item(1, ExerciseProgressionStatus.PROGRESSING),
        _progression_item(2, ExerciseProgressionStatus.STABLE),
        _progression_item(3, ExerciseProgressionStatus.REGRESSING),
        _progression_item(4, ExerciseProgressionStatus.INSUFFICIENT_DATA),
    ]
    out = _validate(ir.rule_recomposition_compatible(_ctx(body=_body(0.3), progression=progressao)))
    assert len(out) == 1
    assert out[0].data_quality is DataQuality.LOW
    assert ir.WAIST_UNAVAILABLE_LIMITATION in out[0].limitations
    assert out[0].severity is Severity.INFO


def test_recomposicao_silencia_sem_peso_estavel_ou_sem_exercicios_comparaveis():
    progressao = [_progression_item(i, ExerciseProgressionStatus.PROGRESSING) for i in (1, 2, 3)]
    assert ir.rule_recomposition_compatible(_ctx(body=_body(-2.5), progression=progressao)) == []
    assert ir.rule_recomposition_compatible(_ctx(body=_body(None), progression=progressao)) == []
    assert ir.rule_recomposition_compatible(_ctx(body=_body(0.2, span_days=5), progression=progressao)) == []
    assert ir.rule_recomposition_compatible(_ctx(body=_body(0.2), progression=progressao[:2])) == []
    regredindo = [_progression_item(i, ExerciseProgressionStatus.REGRESSING) for i in (1, 2, 3)]
    assert ir.rule_recomposition_compatible(_ctx(body=_body(0.2), progression=regredindo)) == []


# ---------------------------------------------------------------------------
# 10) Qualidade comprometendo
# ---------------------------------------------------------------------------


def test_qualidade_dispara_acima_de_20_por_cento_com_base_minima():
    out = _validate(ir.rule_data_quality_unmapped_sets(_ctx(working_sets_total=100, unmapped_working_sets=25)))
    assert len(out) == 1 and out[0].severity is Severity.ACTION
    assert ir.rule_data_quality_unmapped_sets(_ctx(working_sets_total=100, unmapped_working_sets=20)) == []
    assert ir.rule_data_quality_unmapped_sets(_ctx(working_sets_total=10, unmapped_working_sets=9)) == []


# ---------------------------------------------------------------------------
# Avaliacao completa
# ---------------------------------------------------------------------------


def test_evaluate_ordena_dedupa_e_conta_por_regra():
    a, b = SIBLING_MUSCLE_GROUP_PAIRS[0]
    muscles = _muscles(
        _muscle_item(a, "A", weekly=12.0, target=12.0),
        _muscle_item(b, "B", weekly=3.0, target=12.0, direct=0.0, indirect=9.0),
    )
    ctx = _ctx(
        muscles=muscles,
        working_sets_total=100,
        unmapped_working_sets=40,
        progression=[_progression_item(i, ExerciseProgressionStatus.REGRESSING) for i in (1, 2, 3)],
    )
    insights, counts = ir.evaluate_insights(ctx)
    codigos = [i["code"] for i in insights]
    assert codigos[0] == "DATA_QUALITY_UNMAPPED_SETS"  # ACTION primeiro
    assert codigos[1] == "EXERCISES_REGRESSING"  # ATTENTION
    assert set(codigos[2:]) == {"DIRECT_FAR_BELOW_INDIRECT", "SIBLING_ASYMMETRY"}  # INFO
    assert counts["DATA_QUALITY_UNMAPPED_SETS"] == 1 and counts["EXERCISES_REGRESSING"] == 1
    assert len({i["fingerprint"] for i in insights}) == len(insights)
    assert all(Severity(i["severity"]) in {Severity.INFO, Severity.ATTENTION, Severity.ACTION} for i in insights)
    _validate(insights)


@pytest.mark.parametrize("code", ir.enabled_rule_codes())
def test_toda_regra_ligada_esta_no_registro(code):
    assert any(rule.code == code and rule.enabled for rule in ir.RULES)


def test_plato_evidencia_reps_emparelhadas_e_nao_o_total():
    """Evidencia do plato = reps POR SERIE emparelhada nas cargas de topo (o
    que a regra compara), nunca `total_reps` (menos series nao e menos reps)."""
    from app.intelligence.dashboard.comparability import build_key

    key = build_key(11, 7, "total")

    def exposicao(reps):
        sets = [{"reps": r, "weight_kg": 60.0, "rpe": 8.0} for r in reps]
        return {
            **_exposure(60.0, sum(reps)),
            "sets": sets,
            "avg_rpe": 8.0,
            "_key": key,
            "_e1rm_reps": max(reps),
            "_prescribed_reps_min": None,
            "_prescribed_reps_max": None,
        }

    item = _progression_item(11, ExerciseProgressionStatus.PLATEAU_SUSPECTED, comparable=4)
    item["_exposures"] = [exposicao([10, 10, 10]), exposicao([10, 10, 10]), exposicao([10, 10, 10]), exposicao([10, 10])]
    item["_comparable_run"] = item["_exposures"]
    (insight,) = ir.rule_exercise_plateau_suspected(_ctx(progression=[item]))
    metrics = {e["metric"]: e for e in insight["evidence"]}
    assert "total_reps" not in metrics
    # 3 series de 10 contra 2 de 10: emparelha 2 a 2 -> sem "queda". Todas
    # com RPE 8 -> cada serie vale reps + RIR (10 + 2), 24 x 24 (M78).
    assert metrics["paired_top_reps"]["from_value"] == 24
    assert metrics["paired_top_reps"]["to_value"] == 24
    assert metrics["paired_top_reps"]["unit"] == "reps + RIR"
