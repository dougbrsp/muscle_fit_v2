"""#241: RPE esperado x realizado no relatorio em Markdown (por sessao)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app import analytics
from app.domain.effective_intensity import RPE_VS_LABELS
from app.intelligence.report import daily_breakdown as daily
from app.intelligence.report.markdown_renderer import render_markdown

from tests.unit.test_reports_markdown_renderer import full_report_data

D = date(2026, 9, 3)


def _s(activity_id, rpe, planned, set_type="working"):
    return {"activity_id": activity_id, "set_type": set_type, "rpe": rpe, "rpe_target_planned": planned}


def _timeline(set_rows, activity_id=1):
    sessions = [{"activity_id": activity_id, "local_date": D, "name": "Push", "slot_sequence": None}]
    return daily.build_cycle_timeline(sessions, [], [], set_rows)[0]


def test_contagem_abaixo_no_esperado_e_acima():
    row = _timeline([_s(1, 7, 8), _s(1, 8, 8), _s(1, 9, 8), _s(1, 9.5, 8)])
    assert row["rpe_vs_expected_counts"] == {"below": 1, "on_target": 1, "above": 2, "compared": 4}


def test_dia_sem_esperado_nao_tem_contagem_e_misto_conta_so_com_esperado():
    assert _timeline([_s(1, 8, None), _s(1, 9, None)])["rpe_vs_expected_counts"] is None
    misto = _timeline([_s(1, 8, None), _s(1, 9, 8)])["rpe_vs_expected_counts"]
    assert misto == {"below": 0, "on_target": 0, "above": 1, "compared": 1}


def test_mesmo_criterio_do_historico():
    rows = [
        _s(1, 7, 8), _s(1, 9, 8), _s(1, 9, 8, "warmup"), _s(1, 9, 8, "technique"), _s(1, 9, 8, "backoff"),
        _s(1, 8, 8), _s(1, 8, None),
    ]
    hist = analytics._build_progression_sessions(
        [{**r, "local_date": None, "activity_name": "T", "mapping_id": 1, "set_number": i, "reps": 10,
          "weight_kg": 50, "measure": "reps"} for i, r in enumerate(rows)]
    )
    assert _timeline(rows)["rpe_vs_expected_counts"] == hist[0]["rpe_vs_expected_counts"]


def test_historico_devolve_rotulos_do_dono():
    hist = analytics._build_progression_sessions(
        [{"activity_id": 1, "local_date": None, "activity_name": "T", "mapping_id": 1, "set_number": 1,
          "set_type": "working", "reps": 10, "weight_kg": 50, "rpe": 8, "measure": "reps",
          "rpe_target_planned": 8}]
    )
    assert hist[0]["rpe_vs_expected_labels"] == RPE_VS_LABELS


def test_consulta_unica_da_janela_filtra_por_usuario():
    calls = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return []

    conn = SimpleNamespace(cursor=lambda: _Cur())
    window = SimpleNamespace(date_from=D, date_to=D)
    daily.load_session_set_rpe(conn, user_id=7, window=window)
    assert len(calls) == 1
    assert calls[0][1] == (7, D, D)
    assert "a.user_id = %s" in calls[0][0] and "CASE" not in calls[0][0]
    assert "es.rpe" in calls[0][0] and "rpe_target_planned" in calls[0][0]


def test_falha_na_leitura_do_rpe_nao_derruba_a_secao_de_ciclos(monkeypatch):
    from app import template_assignments
    from app.intelligence.dashboard import adherence_service
    from app.intelligence.report import markdown_report_service as svc

    sessions = [{"activity_id": 1, "local_date": D, "name": "Push", "slot_sequence": None}]

    def _boom(*a, **k):
        raise RuntimeError("coluna rpe_target_planned ausente")

    monkeypatch.setattr(template_assignments, "segment_window", lambda *a, **k: [])
    monkeypatch.setattr(adherence_service, "build_adherence", lambda *a, **k: {})
    monkeypatch.setattr(svc, "_fetchall", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_training_type_names", lambda *a, **k: {})
    monkeypatch.setattr(svc, "_current_template", lambda *a, **k: None)
    monkeypatch.setattr(daily, "load_cycle_sessions", lambda *a, **k: (sessions, []))
    monkeypatch.setattr(daily, "load_session_set_rpe", _boom)
    window = SimpleNamespace(date_from=D, date_to=D)
    out = svc._collect_cycles(None, user_id=7, window=window)
    assert out["available"] is True
    assert len(out["timeline"]) == 1
    assert out["timeline"][0]["rpe_vs_expected_counts"] is None


def test_renderer_mostra_linha_com_rotulos_e_omite_sessao_sem_esperado():
    data = full_report_data()
    base = data["cycles"]["timeline"][0]
    data["cycles"]["timeline"] = [
        {**base, "name": "Com", "rpe_vs_expected_counts": {"below": 1, "on_target": 2, "above": 3, "compared": 6}},
        {**base, "name": "Sem", "rpe_vs_expected_counts": None},
    ]
    md = render_markdown(data)
    for label in RPE_VS_LABELS.values():
        assert label.lower() in md
    assert "abaixo do esperado: 1, no esperado: 2, acima do esperado: 3" in md
    assert ", Sem: " not in md
