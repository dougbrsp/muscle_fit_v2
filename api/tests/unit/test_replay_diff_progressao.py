"""Testes puros do resumo/diff de `scripts/replay_diff_progressao.py` (Lote 0 RPE 10).

O caminho com banco (subprocesso por versao) e conferido a mao no DEV; aqui so
a parte que nao depende de I/O: resumo por versao, lista de diferencas, Markdown
e a recusa de backend que nao seja o MySQL DEV.
"""

from __future__ import annotations

import copy

import pytest

from scripts import replay_diff_progressao as rd


def _report() -> dict:
    return {
        "api": "/base/api",
        "engine_version": "progression-v2.0.0",
        "sessions_read": 2,
        "decision_types": {"HOLD": 2},
        "loop_matches_evaluate_history": True,
        "final": {
            "groups": {"10": {"phase": "F2", "reps": [10, 12], "pending_action": None}},
            "tracks": {
                "1:1": {
                    "muscle_group_id": 10,
                    "load_status": "ACTIVE",
                    "working_load_kg": "35",
                    "sets_count": 4,
                    "technique_code": None,
                }
            },
        },
        "sessions": [
            {
                "activity_id": 1,
                "date": "2026-09-01",
                "tracks": {
                    "1:1": {
                        "outcome": "NEUTRAL",
                        "reasons": ["track_in_calibration"],
                        "safety_events": [],
                        "working_load_kg": "35",
                        "working_sets": 4,
                        "intensity_status": "REACHED",
                    }
                },
                "decisions": [{"muscle_group_id": 10, "decision": "HOLD", "phase_before": "F2", "phase_after": "F2"}],
            },
            {
                "activity_id": 2,
                "date": "2026-09-08",
                "tracks": {
                    "1:1": {
                        "outcome": "NEUTRAL",
                        "reasons": ["sets_below_prescribed_dose"],
                        "safety_events": ["RPE_ABOVE_SAFE_CEILING"],
                        "working_load_kg": "35",
                        "working_sets": 3,
                        "intensity_status": "REACHED",
                    }
                },
                "decisions": [{"muscle_group_id": 10, "decision": "HOLD", "phase_before": "F2", "phase_after": "F2"}],
            },
        ],
    }


def test_resumo_conta_veredito_por_motivo_decisoes_e_eventos():
    summary = rd.summarize(_report())
    assert summary["outcomes"] == {"NEUTRAL": 2}
    assert summary["outcomes_by_reason"] == {
        "NEUTRAL sets_below_prescribed_dose": 1,
        "NEUTRAL track_in_calibration": 1,
    }
    assert summary["decision_types"] == {"HOLD": 2}
    assert summary["safety_events"] == {"RPE_ABOVE_SAFE_CEILING": 1}
    assert summary["phases"] == {"10": "F2"}


def test_mesmo_relatorio_nao_tem_diferencas():
    base = _report()
    assert rd.diff_reports(base, copy.deepcopy(base)) == []
    result = {
        "user_id": 1,
        "base": base,
        "current": base,
        "summary": {"base": rd.summarize(base), "current": rd.summarize(base)},
        "differences": [],
    }
    assert "Nenhuma diferenca" in rd.render_markdown(result)


def test_diferencas_por_trilha_decisao_e_estado_final():
    base = _report()
    current = copy.deepcopy(base)
    track = current["sessions"][1]["tracks"]["1:1"]
    track.update(outcome="QUALIFY", reasons=[])
    current["sessions"][1]["decisions"][0]["decision"] = "INCREASE_LOAD"
    current["final"]["tracks"]["1:1"]["sets_count"] = 3
    diffs = rd.diff_reports(base, current)
    fields = {(d["where"].split(" (")[0], d["field"]) for d in diffs}
    assert ("sessao 2", "outcome") in fields
    assert ("sessao 2", "reasons") in fields
    assert ("sessao 2", "decisoes") in fields
    assert ("estado final trilha 1:1", "tracks") in fields
    result = {
        "user_id": 1,
        "base": base,
        "current": current,
        "summary": {"base": rd.summarize(base), "current": rd.summarize(current)},
        "differences": diffs,
    }
    markdown = rd.render_markdown(result)
    assert f"diferencas: **{len(diffs)}**" in markdown
    assert "| QUALIFY | 0 | 1 | +1 |" in markdown


def test_acoes_de_carga_e_proxima_ficha_entram_no_resumo_e_no_diff():
    """Lote 3/P4-P5: aumento da trilha (regra de excesso), aumento do grupo
    expirado e a proxima ficha por trilha. Relatorio de motor antigo (sem as
    chaves) continua comparavel."""
    base = _report()
    current = copy.deepcopy(base)
    current["sessions"][1]["track_load_actions"] = {"1:1": "armed"}
    current["sessions"][1]["group_pending_expired"] = [10]
    # Lote 4/P6: aumento pelo plato da trilha e F2 -> F3 pela maioria
    current["sessions"][1]["track_plateau_actions"] = {"1:1": "armed"}
    current["sessions"][1]["group_phase_advance"] = [10]
    current["next_prescriptions"] = {"1:1": {"decision": "INCREASE_LOAD", "load_kg": "37.5"}}
    base["next_prescriptions"] = {"1:1": {"decision": "HOLD", "load_kg": "35"}}
    summary = rd.summarize(current)
    assert summary["load_actions"] == {
        "grupo F2->F3 pela maioria das trilhas": 1,
        "grupo INCREASE_LOAD expirado": 1,
        "trilha armed": 1,
        "trilha plato armed": 1,
    }
    assert summary["next_prescription_types"] == {"INCREASE_LOAD": 1}
    assert rd.summarize(_report())["load_actions"] == {}
    fields = {(d["where"].split(" (")[0], d["field"]) for d in rd.diff_reports(base, current)}
    assert ("sessao 2", "track_load_actions") in fields
    assert ("sessao 2", "group_pending_expired") in fields
    assert ("sessao 2", "track_plateau_actions") in fields
    assert ("sessao 2", "group_phase_advance") in fields
    assert ("proxima ficha trilha 1:1", "prescricao") in fields


def test_recusa_backend_que_nao_seja_o_mysql_dev(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "data-api")
    with pytest.raises(rd.DiffError):
        rd._worker_env()
