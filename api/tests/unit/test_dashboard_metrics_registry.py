"""Registry de metricas do Novo Dashboard (F0.1, plano §6/§8.2/§12):
versoes, limiares, status por exercicio e reexport dos 5 estados de volume.
"""

from __future__ import annotations

import pytest

from app.intelligence import volume_service
from app.intelligence.dashboard import metrics_registry as reg


def test_versoes_do_plano_estao_todas_registradas():
    esperado = {
        "sets-v1", "effective-sets-v1", "fatigue-sets-v1", "role-sets-v1", "volume-status-v1", "freq-v1",
        "exposure-v1", "epley-rir-v1", "progression-status-v2", "tonnage-v1", "adherence-v2",
        "fullbody-no-reps-v1", "recovery-signals-v1", "weight-ma-v1", "waist-v1", "recomp-v1", "dq-v1",
        # `session-records-v2` nao esta no dicionario do §6: ele nasceu na
        # F1.1 porque o §10 pede "recordes e progressoes" na rota
        # `/analytics/dashboard/workouts` e nenhuma metrica do §6 respondia
        # isso. Fica registrado aqui pelo mesmo motivo das demais - versao
        # emitida no payload.
        "session-records-v2",
        # F1.6: resumo decomponivel da progressao (contagem por status, sem
        # score) e as regras deterministicas de insight do §12.
        "progression-summary-v1",
        "insight-rules-v2",
        # F2.1: eventos de contexto (manuais da migracao 048 + derivados de
        # troca de ficha e de academia, unidos na leitura).
        "context-events-v1",
        # F2.2: check-in diario subjetivo (`daily_checkins`, migracao 050) -
        # versao PROPRIA porque a fonte e relato, nao medida - e a decisao de
        # mostrar/silenciar um alerta ja visto (`dashboard_alert_states`,
        # migracao 051), que muda o que a tela mostra e por isso e versionada
        # como qualquer outra formula.
        "daily-checkin-v1",
        "alert-cooldown-v1",
        # F2.3: cardio no hub - o agregado das atividades de cardio do cache
        # do Intervals.icu e a FC por sessao de treino, que vem do casamento
        # ja gravado em `activity_intervals_link` (fontes diferentes, versoes
        # diferentes).
        "cardio-sessions-v1",
        "session-hr-v1",
        # M98 C4: acompanhamento do cardio - minutos por zona e continuo x
        # intensa por semana (`hr_zones`, `cardio_envelope`) e planejado x
        # realizado (`cardio_adherence`). Versoes proprias: o mapeamento de
        # zonas e o limiar de sessao intensa nao mudam o volume da F2.3.
        "cardio-weekly-zones-v1",
        "cardio-adherence-v2",
        # F2.5 / M34: o bloco POR SESSAO de `/analytics/dashboard/muscles`.
        # Versoes PROPRIAS porque a UNIDADE e outra - o resto da rota compara
        # por janela, estas comparam por sessao. Reusar `effective-sets-v1`
        # faria o payload declarar a mesma formula para duas leituras que nao
        # sao comparaveis entre si.
        "session-comparative-v1",
        "equipment-comparative-v1",
        "muscle-heatmap-v1",
        # F3.1: `/dashboard/engine`. Duas versoes porque as FONTES sao
        # diferentes - `generation-audit-v1` e a leitura do bloco de auditoria
        # da geracao (`generation_runs.audit_json`, com fallback para o
        # `raw_payload`), e `engine-diff-v1` e a comparacao planejado x gerado
        # x executado por item. A segunda REUSA as regras de status do §5
        # (as de `adherence`) e acrescenta a coluna do Modelo, entao declarar
        # `adherence-v*` no lugar dela faria a tela prometer uma formula que
        # nao e a que ela usa.
        "generation-audit-v1",
        "engine-diff-v1",
        # Fase 4 (PR C): metodologia de volume por grupo (Leandro Twin) -
        # exercicios/series DIRETAS por sessao, unidade diferente de
        # `volume-status-v1` (ponderada), versao propria. Fase 6 (PR 2): v2,
        # faixas lidas do cadastro de macro-grupos + eixo semanal. Fase 7: v3,
        # janela de 7 dias a partir do primeiro slot de treino.
        "methodology-volume-v5",
    }
    assert set(reg.METRIC_VERSIONS.values()) == esperado
    assert reg.METRIC_VERSIONS["readiness"] == "fullbody-no-reps-v1"
    assert reg.metric_version("e1rm") == "epley-rir-v1"


def test_metrica_nao_registrada_falha_com_mensagem_clara():
    with pytest.raises(KeyError, match="nao registrada"):
        reg.metric_version("hipertrofia_score")
    with pytest.raises(KeyError, match="nao registrado"):
        reg.threshold("magic_number")


def test_registry_e_somente_leitura():
    with pytest.raises(TypeError):
        reg.METRIC_VERSIONS["sets"] = "sets-v2"  # type: ignore[index]
    with pytest.raises(TypeError):
        reg.THRESHOLDS["e1rm_progress_pct"] = 0.5  # type: ignore[index]


def test_limiares_do_plano_8_2():
    assert dict(reg.THRESHOLDS) == {
        "same_load_tolerance_pct": 0.02,
        "e1rm_progress_pct": 0.03,
        "e1rm_max_reliable_reps": 12,
        "rpe_improvement_delta": 0.75,
        "min_comparable_exposures": 2,
        "plateau_min_exposures": 3,
        "exposure_lookback_days": 90,
        "weight_stable_pct": 0.01,
        "waist_drop_cm": 1.0,
        "direct_indirect_min_ratio": 0.3,
        # F0.3 - regras de qualidade (§8.3)
        "dq_in_progress_stale_hours": 24,
        "dq_weight_divergence_kg": 1.0,
        "dq_max_examples": 5,
        # F1.6 - regras de insight (§12) e visao geral. Valores INICIAIS do
        # plano; validados contra dados reais no PR da F1.6. Mudar aqui e
        # decisao de produto, nunca ajuste silencioso.
        "insight_below_min_consecutive_weeks": 2,
        "insight_below_min_week_days": 7,
        "insight_below_min_collapse_ratio": 0.5,
        "insight_default_cycle_days": 7,
        "insight_plateau_min_exposures": 4,
        "insight_regressing_min_exercises": 3,
        "insight_regressing_lookback_days": 28,
        "insight_skipped_min_count": 3,
        "insight_end_of_session_positions": 2,
        "insight_min_indirect_sets": 6,
        "insight_sibling_asymmetry_min_diff": 0.30,
        "insight_recomp_min_comparable_exercises": 3,
        "insight_recomp_progress_share": 0.5,
        "insight_weight_min_span_days": 14,
        "insight_dq_unmapped_sets_max_ratio": 0.20,
        "insight_dq_min_sets": 20,
        "insight_frequency_lag_hours": 168,
        # F2.1 - pausa de treino. Decisao do usuario, 2026-09-24, com os
        # numeros do dataset real de DEV na descricao do PR (maior intervalo
        # observado entre dias de treino: 2 dias; nenhum >= 3).
        "insight_training_pause_min_days": 10,
        "insight_training_pause_min_sessions_before": 4,
        "weight_ma_days": 7,
        "weight_ma_min_weighings_per_week": 4,
        "overview_max_next_actions": 2,
        # F2.2 - cooldown de alerta. DECISAO DO USUARIO, 2026-09-25 (medicao no
        # registry e na descricao do PR da F2.2): a duracao vem do
        # `reassess_after` da propria regra, e estes cinco numeros so a limitam.
        # Mudar qualquer um deles exige decisao dele - por isso os valores estao
        # fixados aqui, e nao lidos do registry.
        "alert_cooldown_min_days_info": 14,
        "alert_cooldown_min_days_attention": 7,
        "alert_cooldown_min_days_action": 3,
        "alert_cooldown_max_days": 60,
        "alert_state_resolution_gap_days": 30,
        # Fase 4 (PR C) - 3-4 series por exercicio = EXG-004 (as faixas vivem
        # no cadastro de macro-grupos desde a Fase 6; a derivação 3x+ saiu).
        "methodology_volume_sets_per_exercise_min": 3,
        "methodology_volume_sets_per_exercise_max": 4,
    }
    assert reg.threshold("plateau_min_exposures") > reg.threshold("min_comparable_exposures")
    # O insight de plato exige mais exposicoes do que o status (nao alarma cedo).
    assert reg.threshold("insight_plateau_min_exposures") > reg.threshold("plateau_min_exposures")
    # Home com no maximo 2 acoes (brief: "maximo 1 ou 2").
    assert reg.threshold("overview_max_next_actions") == 2


def test_status_de_progressao_por_exercicio_com_rotulos_pt_br():
    codigos = {s.value for s in reg.ExerciseProgressionStatus}
    assert codigos == {
        "PROGRESSING", "STABLE", "PLATEAU_SUSPECTED", "REGRESSING", "INSUFFICIENT_DATA", "NOT_COMPARABLE"
    }
    # todo status tem rotulo, e o rotulo e acessivel pelo enum
    assert set(reg.EXERCISE_PROGRESSION_STATUS_LABELS) == set(reg.ExerciseProgressionStatus)
    assert reg.ExerciseProgressionStatus.PLATEAU_SUSPECTED.label == "Suspeita de platô"
    assert reg.ExerciseProgressionStatus.NOT_COMPARABLE.label == "Não comparável"
    assert reg.ExerciseProgressionStatus("PROGRESSING").label == "Progredindo"


def test_estados_de_volume_sao_reexportados_de_volume_service_sem_duplicar():
    assert reg.WEEKLY_VOLUME_STATUS_CODES is volume_service.WEEKLY_VOLUME_STATUSES
    assert reg.WEEKLY_VOLUME_STATUSES is volume_service.WEEKLY_VOLUME_STATUSES
    assert set(reg.WEEKLY_VOLUME_STATUS_LABELS) == set(reg.WEEKLY_VOLUME_STATUS_CODES)


@pytest.mark.parametrize("actual", [0.0, 3.0, 5.99, 6.0, 8.0, 8.55, 9.0, 9.45, 9.46, 20.0, 27.0, 27.01, 99.0])
def test_classify_weekly_volume_status_so_devolve_os_5_estados_registrados(actual):
    status = volume_service.classify_weekly_volume_status(actual, 6.0, 9.0, 27.0)
    assert status in reg.WEEKLY_VOLUME_STATUS_CODES


def test_par_de_grupos_irmaos_mora_so_no_registry():
    """(6, 7) = costas superior/inferior: ate migrar para tabela, o registry
    e a unica fonte (a copia `SIBLING_MUSCLE_GROUP_IDS` do alerta legado saiu
    com `dashboard_alert_service` no M76 Lote 2c)."""
    assert reg.SIBLING_MUSCLE_GROUP_PAIRS == ((6, 7),)
