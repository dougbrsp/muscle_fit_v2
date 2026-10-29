"""Renderizador do relatorio em Markdown (`app/intelligence/report/markdown_renderer.py`).

Funcao pura: fixture completa (todas as secoes com dado) e fixture vazia
(nada no periodo, e secoes que falharam) - nenhuma das duas pode quebrar, e a
vazia precisa dizer "sem dados no periodo" em vez de mostrar zeros.

Rodar: cd api && python -m pytest tests/unit/test_reports_markdown_renderer.py
"""

from __future__ import annotations

from datetime import date, datetime

from app.intelligence.report.markdown_renderer import SEM_DADOS, fmt_num, render_markdown, table


def full_report_data() -> dict:
    return {
        "generated_at": datetime(2026, 10, 1, 14, 30),
        "timezone": "America/Sao_Paulo",
        "window": {"date_from": date(2026, 7, 4), "date_to": date(2026, 10, 1), "days": 90},
        "muscles": {
            "available": True,
            "target_source": "assigned_template",
            "template_name": "PPL 6x",
            "groups": [
                {
                    "muscle_group_id": 1,
                    "name": "Peitoral",
                    "macro_group": "Grandes",
                    "physical_sets": 120.0,
                    "effective_sets": 118.5,
                    "weekly_effective_sets": 9.22,
                    "tonnage_kg": 54321.0,
                    "tonnage_excluded_sets": 0,
                    "avg_rpe": 8.4,
                    "direct_sessions": 24,
                    "participation_sessions": 30,
                    "target_min": 8.0,
                    "target": 12.0,
                    "target_max": 16.0,
                    "status": "BETWEEN_MIN_TARGET",
                },
                {
                    "muscle_group_id": 2,
                    "name": "Tríceps",
                    "macro_group": "Pequenos",
                    "physical_sets": 60.0,
                    "effective_sets": 45.0,
                    "weekly_effective_sets": 3.5,
                    "tonnage_kg": None,
                    "tonnage_excluded_sets": 6,
                    "avg_rpe": None,
                    "direct_sessions": 0,
                    "participation_sessions": 20,
                    "target_min": None,
                    "target": None,
                    "target_max": None,
                    "status": "BELOW_MIN",
                },
            ],
            "macro_groups": [
                {"name": "Grandes", "groups": 1, "physical_sets": 120.0, "effective_sets": 118.5,
                 "weekly_effective_sets": 9.22, "tonnage_kg": 54321.0},
                {"name": "Pequenos", "groups": 1, "physical_sets": 60.0, "effective_sets": 45.0,
                 "weekly_effective_sets": 3.5, "tonnage_kg": None},
            ],
            "unattributed_sets": 4,
            "tonnage_excluded_sets": 6,
            "tonnage_excluded_by_convention": {"assisted": 6},
            "unknown_load_conventions": [],
            "days": [
                {"local_date": date(2026, 9, 29), "muscle_group_id": 1, "name": "Peitoral", "physical_sets": 6.0,
                 "effective_sets": 6.5, "tonnage_kg": 3600.0, "avg_rpe": 8.25, "exercises": 2, "sessions": 1},
                {"local_date": date(2026, 9, 29), "muscle_group_id": 2, "name": "Tríceps", "physical_sets": 6.0,
                 "effective_sets": 3.0, "tonnage_kg": None, "avg_rpe": None, "exercises": 2, "sessions": 1},
            ],
        },
        "equipment": {
            "available": True,
            "period": [
                {"equipment": "Banco reto (Academia Centro)", "exercises": 1, "days": 12, "physical_sets": 48.0,
                 "effective_sets": 48.5, "tonnage_kg": 28800.0, "avg_rpe": 8.4, "load_max": 80.0, "load_avg": 62.5,
                 "reps": 480, "excluded_conventions": []},
                {"equipment": "Graviton", "exercises": 1, "days": 6, "physical_sets": 18.0, "effective_sets": 18.0,
                 "tonnage_kg": None, "avg_rpe": 8.0, "load_max": 30.0, "load_avg": 30.0, "reps": 140,
                 "excluded_conventions": ["assisted"]},
                {"equipment": "Sem equipamento", "exercises": 1, "days": 2, "physical_sets": 6.0, "effective_sets": 6.0,
                 "tonnage_kg": None, "avg_rpe": None, "load_max": None, "load_avg": None, "reps": 60,
                 "excluded_conventions": []},
            ],
            "days": [
                {"local_date": date(2026, 9, 29), "equipment": "Banco reto (Academia Centro)", "exercise": "Supino reto",
                 "physical_sets": 4.0, "effective_sets": 4.5, "tonnage_kg": 2400.0, "avg_rpe": 8.5, "load_max": 60.0,
                 "load_avg": 60.0, "reps": 40, "excluded_conventions": []},
                {"local_date": date(2026, 9, 30), "equipment": "Graviton", "exercise": "Barra fixa assistida",
                 "physical_sets": 3.0, "effective_sets": 3.0, "tonnage_kg": None, "avg_rpe": 8.0, "load_max": 30.0,
                 "load_avg": 30.0, "reps": 23, "excluded_conventions": ["assisted"]},
            ],
        },
        "recovery": {
            "available": True,
            "sleep": {
                "days": 80, "avg_duration_hours": 7.25, "duration_days": 80, "avg_deep_hours": 1.5,
                "deep_days": 60, "avg_rem_hours": 1.75, "rem_days": 60, "avg_regularity_percent": 82.0,
                "regularity_days": 70,
            },
            "wellness": {
                "days": 85, "avg_hrv_ms": 48.3, "hrv_days": 85, "avg_resting_hr_bpm": 52.1,
                "resting_hr_days": 85, "avg_form_tsb": -4.2, "form_tsb_days": 85,
            },
            "signals": [
                {"code": "hrv", "label": "Variabilidade da FC (HRV)", "unit": "ms", "current": 49.0,
                 "baseline": 46.0, "delta": 3.0, "direction": "ABOVE", "reading": "FAVORABLE",
                 "observations": 85, "baseline_observations": 28},
                {"code": "resting_hr", "label": "FC de repouso", "unit": "bpm", "current": 52.0,
                 "baseline": None, "delta": None, "direction": "UNKNOWN", "reading": "UNKNOWN",
                 "observations": 85, "baseline_observations": 2},
            ],
            "baseline_window_days": 28,
            "days": [
                {"local_date": date(2026, 9, 29), "sleep_hours": 7.5, "deep_hours": 1.5, "rem_hours": 2.0,
                 "regularity_percent": 80.0, "hrv_ms": 52.0, "resting_hr_bpm": 50.0, "form_tsb": -3.0,
                 "readiness": None, "energy": 4.0, "soreness": 2.0, "stress": 3.0, "mood": 4.0,
                 "muscle_pain": 3.0, "joint_pain": None},
                {"local_date": date(2026, 9, 30), "sleep_hours": 6.25, "deep_hours": None, "rem_hours": None,
                 "regularity_percent": None, "hrv_ms": None, "resting_hr_bpm": None, "form_tsb": None,
                 "readiness": None, "energy": None, "soreness": None, "stress": None, "mood": None,
                 "muscle_pain": None, "joint_pain": None},
            ],
        },
        "body": {
            "available": True,
            "weight": {"count": 40, "first_date": date(2026, 7, 4), "first_kg": 82.4,
                       "last_date": date(2026, 10, 1), "last_kg": 80.1, "change_kg": -2.1,
                       "change_pct": -2.55, "ma7_kg": 80.3, "per_week": 3.1},
            "bioimpedance": {"count": 3, "last_date": date(2026, 9, 28), "body_fat_percent": 15.2,
                             "muscle_mass_kg": 38.4, "body_water_percent": 58.0,
                             "body_fat_change": -1.1, "muscle_mass_change": 0.4},
            "measurements": [
                {"label": "Cintura", "last_cm": 84.5, "last_date": date(2026, 9, 30), "change_cm": -1.5, "count": 5},
            ],
            "daily": {
                "days": [
                    {"local_date": date(2026, 9, 28), "weight_kg": 80.4, "body_fat_percent": 15.2,
                     "muscle_mass_kg": 38.4, "body_water_percent": 58.0, "bmi": None},
                    {"local_date": date(2026, 10, 1), "weight_kg": 80.1, "body_fat_percent": None,
                     "muscle_mass_kg": None, "body_water_percent": None, "bmi": None},
                ],
                "fields": ["body_fat_percent", "muscle_mass_kg", "body_water_percent"],
                "measurement_sites": [{"site": "waist", "label": "Cintura"}, {"site": "arm_r", "label": "Braço direito"}],
                "measurement_days": [{"local_date": date(2026, 9, 30), "values": {"waist": 84.5}}],
            },
        },
        "cardio": {
            "available": True,
            "intervals_configured": True,
            "totals": {"sessions": 12, "sessions_with_hr": 10, "minutes": 480, "distance_km": 62.4,
                       "calories_kcal": 4200.0, "training_load": 310.0, "avg_hr_bpm": 138.2,
                       "max_hr_bpm": 172.0},
            "by_sport": [
                {"sport_type": "Run", "sessions": 8, "minutes": 320, "distance_km": 50.0,
                 "calories_kcal": 3000.0, "avg_hr_bpm": 145.0, "max_hr_bpm": 172.0},
            ],
            "strength_hr": {"sessions_total": 30, "sessions_with_hr": 24, "coverage_pct": 80.0,
                            "avg_hr_bpm": 118.0, "max_hr_bpm": 160.0},
            "truncated": False,
            "sessions": [
                {"local_date": date(2026, 9, 27), "name": "Corrida leve", "sport_type": "Run", "minutes": 40,
                 "distance_km": 6.25, "avg_hr_bpm": 142.0, "max_hr_bpm": 165.0, "calories_kcal": 420.0,
                 "training_load": 38.0},
            ],
        },
        "cycles": {
            "available": True,
            "segments": [
                {"date_from": date(2026, 7, 4), "date_to": date(2026, 8, 14), "template_name": None,
                 "training_type": None, "volume_mode": None, "sessions": 10},
                {"date_from": date(2026, 8, 15), "date_to": date(2026, 10, 1), "template_name": "PPL 6x",
                 "training_type": "Hipertrofia", "volume_mode": "equipment_series", "sessions": 20},
            ],
            "sessions_total": 30,
            "timeline": [
                {"local_date": date(2026, 9, 29), "template_name": "PPL 6x", "slot": "1 — Push", "name": "Treino A",
                 "minutes": 62, "physical_sets": 18, "avg_hr_bpm": 118.0, "max_hr_bpm": 151.0, "avg_rpe": 8.25},
                {"local_date": date(2026, 9, 30), "template_name": None, "slot": None, "name": "Treino livre",
                 "minutes": None, "physical_sets": 6, "avg_hr_bpm": None, "max_hr_bpm": None, "avg_rpe": None},
            ],
            "adherence": {"has_template": True, "template_name": "PPL 6x", "cycle_length_days": 7,
                          "expected_sessions": 24.0, "completed_sessions": 30.0, "planned_sessions": 20.0,
                          "unplanned_sessions": 10.0, "missed_sessions": 4.0, "rate": 0.8333},
            "current_template": {
                "id": 7, "name": "PPL 6x", "volume_mode": "equipment_series", "training_type": "Hipertrofia",
                "intent": "specialization", "intent_targets": ["Peitoral"],
                "slots": [
                    {"sequence": 1, "name": "Push", "weekday": 1, "slot_type": "normal",
                     "groups": ["Peitoral", "Tríceps"], "target_sets": 10,
                     "items": [
                         {"kind": "equipment", "groups": ["Peitoral"], "exercise": "Supino reto",
                          "equipment": "Banco reto", "sets": 4, "reps_target": "8-10", "rpe_target": None,
                          "technique": "Drop-set", "technique_applications": 1, "cardio_minutes": None},
                         {"kind": "muscle_group", "groups": ["Tríceps"], "exercise": None, "equipment": None,
                          "sets": 6, "reps_target": None, "rpe_target": None, "technique": None,
                          "technique_applications": None, "cardio_minutes": None},
                     ]},
                    {"sequence": 2, "name": "Descanso", "weekday": 2, "slot_type": "descanso",
                     "groups": [], "target_sets": 0, "items": []},
                ],
            },
        },
        "quality": {
            "unattributed_sets": 4,
            "tonnage_excluded_sets": 6,
            "tonnage_excluded_by_convention": {"assisted": 6},
            "unknown_load_conventions": [],
            "empty_sources": [],
            "unavailable_sections": [],
            "cardio_truncated": False,
        },
    }


def empty_report_data() -> dict:
    return {
        "generated_at": datetime(2026, 10, 1, 8, 0),
        "timezone": "America/Sao_Paulo",
        "window": {"date_from": date(2026, 9, 1), "date_to": date(2026, 10, 1), "days": 31},
        "muscles": {"available": True, "groups": [], "macro_groups": [], "unattributed_sets": 0},
        "recovery": {"available": True, "sleep": {"days": 0}, "wellness": {"days": 0}, "signals": []},
        "body": {"available": True, "weight": {"count": 0}, "bioimpedance": {"count": 0}, "measurements": []},
        "cardio": {"available": False},
        "cycles": {"available": True, "segments": [], "adherence": {"has_template": False}, "current_template": None},
        "quality": {"empty_sources": ["Séries de musculação", "Pesagens"], "unavailable_sections": ["Cardio"]},
    }


def test_fixture_completa_monta_as_sete_secoes_em_ordem():
    md = render_markdown(full_report_data())
    titulos = [
        "# Relatório MuscleFit",
        "## 1. Treino por grupo muscular",
        "## 2. Treino por equipamento",
        "## 3. Sono, HRV, FC e recuperação",
        "## 4. Corpo",
        "## 5. Cardio",
        "## 6. Ciclos de treino",
        "## 7. Qualidade dos dados",
    ]
    posicoes = [md.index(t) for t in titulos]
    assert posicoes == sorted(posicoes)
    assert SEM_DADOS not in md
    assert md.endswith("\n")


def test_cabecalho_tem_periodo_fuso_e_geracao():
    md = render_markdown(full_report_data())
    assert "**Período:** 04/07/2026 a 01/10/2026 (90 dias)" in md
    assert "**Fuso horário:** America/Sao_Paulo" in md
    assert "**Gerado em:** 01/10/2026 14:30" in md


def test_secao_1_tabela_por_grupo_e_subtotais():
    md = render_markdown(full_report_data())
    assert "| Peitoral | Grandes | 120 | 118,5 | 9,2 | 54.321 kg | 8,4 | 24 / 30 | 8,0 / 12,0 / 16,0 | Aproximando da meta |" in md
    # Ausencia e travessao, nunca zero.
    assert "| Tríceps | Pequenos | 60 | 45,0 | 3,5 | — | — | 0 / 20 | — | Abaixo do mínimo |" in md
    assert "### Subtotais por macro-grupo" in md
    assert "| Grandes | 1 | 120 | 118,5 | 9,2 | 54.321 kg |" in md
    assert "Modelo “PPL 6x”" in md


def test_secao_2_medias_e_baseline():
    md = render_markdown(full_report_data())
    assert "| Sono — duração | 7,25 h | 80 |" in md
    assert "| Sono — profundo | 1,50 h | 60 |" in md
    assert "| Sono — REM | 1,75 h | 60 |" in md
    assert "| HRV | 48,3 ms | 85 |" in md
    assert "| Forma (TSB) | -4,2 | 85 |" in md
    assert "| Variabilidade da FC (HRV) | 49,0 ms | 46,0 ms | ↑ acima da baseline (favorável) | 85 |" in md
    # Sem baseline: tendencia em branco, nunca inventada.
    assert "| FC de repouso | 52,0 bpm | — | — | 85 |" in md


def test_secao_3_corpo():
    md = render_markdown(full_report_data())
    assert "82,4 kg (04/07/2026)" in md
    assert "-2,1 kg (-2,5%)" in md or "-2,1 kg (-2,6%)" in md
    assert "| Gordura corporal | 15,2% | -1,1 p.p. |" in md
    assert "| Cintura | 84,5 cm | 30/09/2026 | -1,5 cm | 5 |" in md


def test_secao_4_cardio_janela_inteira_por_modalidade_e_fc():
    md = render_markdown(full_report_data())
    assert "| 12 | 480 | 62,4 km | 4.200 kcal | 310 | 138 bpm | 172 bpm |" in md
    assert "| Run | 8 | 320 | 50,0 km |" in md
    assert "2 de 12 sessões de cardio sem frequência cardíaca" in md
    assert "| 30 | 24 | 80% | 118 bpm | 160 bpm |" in md


def test_secao_5_ciclos_trechos_adesao_e_modelo_atual():
    md = render_markdown(full_report_data())
    assert "| 04/07/2026 | 14/08/2026 | Sem Modelo atribuído |" in md
    assert "| 15/08/2026 | 01/10/2026 | PPL 6x | Hipertrofia | Equipamento + séries fixas | 20 |" in md
    assert "| 24,0 | 30 | 20 | 10 | 4,0 | 83% |" in md
    assert "**Intenção metodológica:** Especialização (grupos-alvo: Peitoral)" in md
    assert "| 1 | Push (Segunda) | Peitoral, Tríceps | 10 | Drop-set |" in md
    assert "Descanso (Terça) — descanso" in md
    assert "- Supino reto (Banco reto) · 4 séries · 8-10 reps · técnica: Drop-set ×1" in md


def test_secao_6_qualidade():
    md = render_markdown(full_report_data())
    assert "**4 série(s) sem grupo muscular:**" in md
    assert "**6 série(s) fora da tonelagem** (6 assistido)" in md


def test_fixture_vazia_nao_quebra_e_diz_sem_dados():
    md = render_markdown(empty_report_data())
    for titulo in (
        "## 1. Treino por grupo muscular",
        "## 2. Treino por equipamento",
        "## 3. Sono, HRV, FC e recuperação",
        "## 4. Corpo",
        "## 5. Cardio",
        "## 6. Ciclos de treino",
        "## 7. Qualidade dos dados",
    ):
        assert titulo in md
    assert md.count(SEM_DADOS) >= 4
    # Secao que falhou na coleta e dita indisponivel, nao "zero".
    assert "Seção indisponível" in md
    assert "Nenhum Modelo de treino atribuído hoje." in md
    assert "Fontes sem registro no período:** Séries de musculação; Pesagens." in md
    assert "Seções que não puderam ser lidas agora:** Cardio." in md
    assert "| 0 |" not in md


def test_dicionario_quase_vazio_tambem_renderiza():
    md = render_markdown({})
    assert md.count(SEM_DADOS) == 6
    assert "Nenhuma lacuna encontrada" in md


def test_formatacao_brasileira_e_celula_escapada():
    assert fmt_num(1234567.891, 2) == "1.234.567,89"
    assert fmt_num(None) == "—"
    assert table(["a"], [["x|y"]]).splitlines()[-1] == "| x\\|y |"


# ---------------------------------------------------------------------------
# Detalhamento diario
# ---------------------------------------------------------------------------


def test_detalhamento_diario_por_grupo():
    md = render_markdown(full_report_data())
    assert "### Detalhamento diário por grupo" in md
    assert "| Data | Grupo | Séries físicas | Séries efetivas | Tonelagem | RPE médio | Exercícios / sessões |" in md
    assert "| 29/09/2026 | Peitoral | 6 | 6,50 | 3.600 kg | 8,2 | 2 / 1 |" in md or (
        "| 29/09/2026 | Peitoral | 6 | 6,50 | 3.600 kg | 8,3 | 2 / 1 |" in md
    )
    assert "| 29/09/2026 | Tríceps | 6 | 3,00 | — | — | 2 / 1 |" in md


def test_secao_equipamento_resumo_e_diario():
    md = render_markdown(full_report_data())
    assert "| Banco reto (Academia Centro) | 1 | 12 | 48 | 48,50 | 28.800 kg | 8,4 | 80,0 kg / 62,5 kg | 480 |" in md
    # Assistido: sem tonelagem, a convencao e declarada e as reps aparecem.
    assert "| Graviton | 1 | 6 | 18 | 18,00 | — (assistido: ver reps) | 8,0 | 30,0 kg / 30,0 kg | 140 |" in md
    assert "| Sem equipamento | 1 | 2 | 6 | 6,00 | — | — | — | 60 |" in md
    assert (
        "| 29/09/2026 | Banco reto (Academia Centro) | Supino reto | 4 | 4,50 | 2.400 kg | 8,5 | 60,0 kg / 60,0 kg | 40 |"
        in md
    )
    assert "| 30/09/2026 | Graviton | Barra fixa assistida | 3 | 3,00 | — (assistido: ver reps) |" in md


def test_detalhamento_diario_de_recuperacao_corta_coluna_vazia():
    md = render_markdown(full_report_data())
    assert "| 29/09/2026 | 7,50 h | 1,50 h | 2,00 h | 80% | 52,0 ms | 50 bpm | -3,0 | 4 | 2 | 3 | 4 | 3 |" in md
    assert "| 30/09/2026 | 6,25 h | — | — | — | — | — | — | — | — | — | — | — |" in md
    # Readiness e dor articular sem nenhum registro no periodo: sem coluna.
    assert "Readiness" not in md
    assert "Dor articular" not in md


def test_detalhamento_diario_de_corpo_cardio_e_ciclos():
    md = render_markdown(full_report_data())
    assert "| 28/09/2026 | 80,4 kg | 15,2% | 38,4 kg | 58,0% |" in md
    assert "| 01/10/2026 | 80,1 kg | — | — | — |" in md
    assert "| Data | Cintura | Braço direito |" in md
    assert "| 30/09/2026 | 84,5 cm | — |" in md
    assert "| 27/09/2026 | Run | Corrida leve | 40 min | 6,25 km | 142 bpm | 165 bpm | 420 kcal | 38 |" in md
    assert "| 29/09/2026 | PPL 6x | 1 — Push | Treino A | 62 min | 18 | 118 bpm | 151 bpm | 8,2 |" in md or (
        "| 29/09/2026 | PPL 6x | 1 — Push | Treino A | 62 min | 18 | 118 bpm | 151 bpm | 8,3 |" in md
    )
    assert "| 30/09/2026 | Sem Modelo atribuído | — | Treino livre | — | 6 | — | — | — |" in md


def test_datas_do_detalhamento_em_ordem_cronologica():
    md = render_markdown(full_report_data())
    secao = md[md.index("### Detalhamento diário por equipamento"):md.index("## 3.")]
    assert secao.index("29/09/2026") < secao.index("30/09/2026")


def test_serie_por_tempo_soma_os_segundos_ao_lado_das_reps():
    """Lote T3 (medidas de serie): a celula de reps ganha o tempo das series
    por tempo no texto do dono (`format_set_measure`: "175 s"); sem serie por
    tempo, a celula e a de sempre."""
    data = full_report_data()
    linha = data["equipment"]["days"][0]
    linha["duration_seconds"] = 175
    md = render_markdown(data)
    assert f"| {linha['reps']} + 175 s |" in md
    assert "| 480 |" in md  # resumo sem tempo continua igual


def test_divergencia_da_invariante_vai_para_a_qualidade():
    data = full_report_data()
    data["quality"]["daily_invariant_problems"] = ["Peitoral: physical_sets período=10 dias=9"]
    md = render_markdown(data)
    assert "**Detalhamento diário não fecha com o total do período:** Peitoral: physical_sets" in md


def test_item_de_cardio_usa_o_texto_canonico_do_dono():
    """M98 C0: o serviço manda `cardio_text` (`cardio_prescription.describe`);
    dado sem o texto cai nos minutos, como antes."""
    from app.intelligence.report.markdown_renderer import _item_label

    texto = "5 min aquec. + 8× [30 s Z4 / 90 s Z2] + 5 min recuperação"
    assert _item_label({"kind": "cardio", "cardio_text": texto, "cardio_minutes": 26}) == f"Cardio — {texto}"
    assert _item_label({"kind": "cardio", "cardio_minutes": 20}) == "Cardio — 20 min"
    assert _item_label({"kind": "cardio"}) == "Cardio"
