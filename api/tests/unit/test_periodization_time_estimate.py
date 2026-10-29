"""Dono do "Tempo total de treino" do Modelo (`periodization/time_estimate.py`,
M76 Lote 4c — E7). Antes o dono era o TS do builder e isto era um porte; agora
o backend é o dono e o TS é o espelho (paridade completa em
`template-time-estimate.parity.json`, gerada por
`test_m76_lote1b_espelhos_fe.py --gravar` e rodada pelo vitest).

Trava aqui:
1. as decisões da Fase 7 (3 s/rep, reps vazias = 10, sem descanso conta 0 s
   e entra em `items_without_rest`);
2. a diferença entre "descanso não definido" (0 s na estimativa) e o default
   de prescrição (60 s, `prescription_defaults`);
3. o TS não tem constante numérica própria: lê o JSON gerado.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain import prescription_defaults as pd
from app.periodization import limits, time_estimate

LIB = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib"
TS_FILE = LIB / "template-time-estimate.ts"


def test_premissas_da_fase_7():
    assert time_estimate.EXEC_SECONDS_PER_REP == 3
    assert time_estimate.DEFAULT_ESTIMATED_REPS == 10
    assert time_estimate.REST_WHEN_UNDEFINED_SECONDS == 0


def test_sem_descanso_conta_zero_e_e_contado():
    estimate = time_estimate.estimate_slot_time([{"item_kind": "equipment", "sets_count": 4, "reps_target": "10"}])
    assert estimate["total_seconds"] == 120  # 4 x 10 x 3 s, sem descanso
    assert estimate["items_without_rest"] == 1
    assert estimate["breakdown"]["rest_seconds"] == 0
    assert estimate["has_estimated_reps"] is False


def test_com_descanso_e_reps_estimadas():
    items = [
        {"item_kind": "equipment", "sets_count": 3, "reps_target": None, "rest_seconds": 60},
        {"item_kind": "cardio", "cardio_duration_minutes": 5},
    ]
    estimate = time_estimate.estimate_slot_time(items)
    assert estimate["breakdown"]["work"] == 3 * 10 * 3 + 3 * 60
    assert estimate["breakdown"]["cardio"] == 300
    assert estimate["total_seconds"] == 270 + 300
    assert estimate["items_without_rest"] == 0
    assert estimate["has_estimated_reps"] is True


def test_total_e_a_soma_das_parcelas_e_estimate_slot_seconds_e_o_total():
    items = [
        {
            "item_kind": "equipment",
            "sets_count": 2,
            "reps_target": "8-12",
            "rest_seconds": 45,
            "warmup_sets_count": 1,
            "warmup_reps": 10,
            "warmup_rest_seconds": 30,
            "advanced_technique_applications": 2,
            "advanced_technique_duration_seconds": 10,
        }
    ]
    estimate = time_estimate.estimate_slot_time(items)
    parts = estimate["breakdown"]
    assert estimate["total_seconds"] == sum(parts[k] for k in ("warmup", "feeler", "work", "technique", "cardio"))
    assert time_estimate.estimate_slot_seconds(items) == estimate["total_seconds"]


def test_descanso_nao_definido_nao_e_o_default_de_prescricao():
    """Estimativa: 0 s (tela) ou o teto do intervalo (complemento). Ficha/timer:
    o ponto médio do intervalo padrão. Três perguntas, três nomes."""
    assert time_estimate.REST_WHEN_UNDEFINED_SECONDS == 0
    assert pd.default_rest_seconds() == 60
    assert limits.COMPLEMENT_DEFAULT_REST_SECONDS == pd.DEFAULT_MAX_REST_SECONDS == 90
    item = [{"item_kind": "equipment", "sets_count": 1, "reps_target": "10"}]
    assert time_estimate.estimate_slot_seconds(item) == 30
    assert time_estimate.estimate_slot_seconds(item, limits.COMPLEMENT_DEFAULT_REST_SECONDS) == 120


def test_parse_reps_target():
    assert time_estimate.parse_reps_target("8-12") == 10
    assert time_estimate.parse_reps_target("8 a 12") == 10
    assert time_estimate.parse_reps_target("7,5") == 7.5
    assert time_estimate.parse_reps_target("") is None
    assert time_estimate.parse_reps_target("até a falha") is None


def test_espelho_ts_le_as_premissas_do_json_gerado():
    """O TS não escreve 3 s/rep, 10 reps nem 60 s/min à mão: importa de
    `motor-constants.generated.json` (seção `time_estimate`)."""
    texto = TS_FILE.read_text(encoding="utf-8")
    assert "motor-constants.generated.json" in texto
    assert not re.search(r"export const (EXEC_SECONDS_PER_REP|DEFAULT_ESTIMATED_REPS) = \d", texto)
    assert "sem descanso definido conta 0 s" in texto


def test_tecnica_rest_pause_sem_duracao_usa_a_pausa_padrao_do_dono():
    """2026-10-25: a estimativa consome o MESMO padrão do rest-pause do dono
    (`progression_engine.REST_PAUSE_DEFAULT_PAUSE_SECONDS`); duração do item
    vence; drop set sem duração continua 0."""
    from app.domain.progression_engine import REST_PAUSE_DEFAULT_PAUSE_SECONDS

    def tecnica(code, duration):
        item = {
            "item_kind": "equipment",
            "advanced_technique": code,
            "advanced_technique_applications": 2,
            "advanced_technique_duration_seconds": duration,
        }
        return time_estimate.estimate_slot_time([item])["breakdown"]["technique"]

    assert tecnica("rest_pause", None) == 2 * REST_PAUSE_DEFAULT_PAUSE_SECONDS
    assert tecnica("rest_pause", 20) == 40
    assert tecnica("drop_set", None) == 0
    assert time_estimate.export_for_frontend()["rest_pause_default_pause_seconds"] == REST_PAUSE_DEFAULT_PAUSE_SECONDS
