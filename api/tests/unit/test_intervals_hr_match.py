"""Associacao FC x treino realizado por JANELA DE HORARIO
(`app/intervals_hr_match.py`, melhoria M2), sem banco e sem rede.

Cobre os casos que definem o produto: match exato, match dentro da janela,
fora da janela, multiplos candidatos, nenhum candidato, treino sem horario e
atividade que nao e `WeightTraining`.

Rodar: cd api && python -m pytest tests/unit/test_intervals_hr_match.py
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import intervals_hr_match as hr

TREINO = datetime(2026, 9, 18, 19, 30, 0)


def _row(external_id: str, start_time, *, sport_type="WeightTraining", avg_hr=140.0, row_id=None):
    return {
        "id": row_id if row_id is not None else abs(hash(external_id)) % 10_000,
        "external_id": external_id,
        "start_time": start_time,
        "sport_type": sport_type,
        "moving_time_seconds": 3600,
        "avg_hr_bpm": avg_hr,
        "max_hr_bpm": 172.0,
        "calories_kcal": 480.0,
        "training_load": 55.0,
    }


def _candidates(*rows):
    return hr.eligible_candidates(rows)


# ---------------------------------------------------------------------------
# Elegibilidade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sport_type", ["WeightTraining", "weighttraining", "Weight Training", "weight_training"])
def test_sport_type_weighttraining_e_aceito_em_qualquer_grafia(sport_type):
    assert hr.is_weight_training(sport_type)
    assert len(_candidates(_row("a", TREINO, sport_type=sport_type))) == 1


@pytest.mark.parametrize("sport_type", ["Ride", "Run", "VirtualRun", "Walk", "StrengthTraining", "", None])
def test_atividade_que_nao_e_weighttraining_nunca_e_candidata(sport_type):
    assert _candidates(_row("a", TREINO, sport_type=sport_type)) == []


def test_atividade_sem_horario_nao_e_candidata():
    """Sem `start_time` nao ha o que comparar - casar por data era exatamente
    o problema que esta entrega veio corrigir."""
    assert _candidates(_row("a", None)) == []


# ---------------------------------------------------------------------------
# Casamento
# ---------------------------------------------------------------------------


def test_match_exato_no_mesmo_horario():
    result = hr.match_session(TREINO, _candidates(_row("a", TREINO)))
    assert result.status == hr.STATUS_MATCHED
    assert result.delta_seconds == 0
    assert result.candidates_count == 1
    assert result.candidate.external_id == "a"
    assert result.persisted


@pytest.mark.parametrize("minutes", [-59, -30, -1, 1, 30, 59, 60])
def test_match_dentro_da_janela_de_tolerancia(minutes):
    candidate_start = TREINO + timedelta(minutes=minutes)
    result = hr.match_session(TREINO, _candidates(_row("a", candidate_start)))
    assert result.status == hr.STATUS_MATCHED
    assert result.delta_seconds == abs(minutes) * 60


@pytest.mark.parametrize("minutes", [61, 90, 240, -61, -1440])
def test_fora_da_janela_nao_casa(minutes):
    result = hr.match_session(TREINO, _candidates(_row("a", TREINO + timedelta(minutes=minutes))))
    assert result.status == hr.STATUS_NO_CANDIDATE
    assert result.candidate is None
    assert not result.persisted


def test_nenhum_candidato_no_periodo():
    result = hr.match_session(TREINO, [])
    assert result.status == hr.STATUS_NO_CANDIDATE
    assert result.candidates_count == 0
    assert not result.persisted


def test_treino_sem_started_at_nao_casa():
    result = hr.match_session(None, _candidates(_row("a", TREINO)))
    assert result.status == hr.STATUS_NO_START_TIME
    assert not result.persisted


def test_treino_com_data_sem_hora_nao_casa():
    """`date` puro nao vira meia-noite: seria um casamento inventado."""
    result = hr.match_session(TREINO.date(), _candidates(_row("a", TREINO)))
    assert result.status == hr.STATUS_NO_START_TIME


# ---------------------------------------------------------------------------
# Multiplos candidatos
# ---------------------------------------------------------------------------


def test_multiplos_candidatos_escolhe_o_mais_proximo_e_marca_ambiguo():
    candidates = _candidates(
        _row("longe", TREINO + timedelta(minutes=50)),
        _row("perto", TREINO + timedelta(minutes=4)),
        _row("medio", TREINO - timedelta(minutes=20)),
    )
    result = hr.match_session(TREINO, candidates)
    assert result.status == hr.STATUS_MATCHED_AMBIGUOUS
    assert result.candidate.external_id == "perto"
    assert result.delta_seconds == 4 * 60
    assert result.candidates_count == 3
    # Ambiguo ainda GRAVA: o dado de FC e util, a linha so fica sinalizada.
    assert result.persisted


def test_candidato_fora_da_janela_nao_conta_para_a_ambiguidade():
    candidates = _candidates(
        _row("dentro", TREINO + timedelta(minutes=10)),
        _row("fora", TREINO + timedelta(hours=5)),
    )
    result = hr.match_session(TREINO, candidates)
    assert result.status == hr.STATUS_MATCHED
    assert result.candidates_count == 1


def test_empate_exato_tem_desempate_deterministico():
    """Duas atividades a mesma distancia: a escolha nao pode depender da ordem
    em que o banco devolveu as linhas, senao o job nao e idempotente."""
    antes = _row("zzz", TREINO - timedelta(minutes=15))
    depois = _row("aaa", TREINO + timedelta(minutes=15))

    primeiro = hr.match_session(TREINO, _candidates(antes, depois))
    segundo = hr.match_session(TREINO, _candidates(depois, antes))

    assert primeiro.candidate.external_id == segundo.candidate.external_id == "zzz"
    assert primeiro.status == segundo.status == hr.STATUS_MATCHED_AMBIGUOUS


def test_start_time_em_string_iso_do_shim_data_api():
    """O backend Data API devolve datetime como string - tem que casar igual."""
    result = hr.match_session("2026-09-18T19:30:00", _candidates(_row("a", "2026-09-18 19:35:00")))
    assert result.status == hr.STATUS_MATCHED
    assert result.delta_seconds == 300


# ---------------------------------------------------------------------------
# Janela configuravel
# ---------------------------------------------------------------------------


def test_janela_default_e_60_minutos():
    assert hr.DEFAULT_TOLERANCE_SECONDS == 3600
    assert hr.tolerance_seconds({}) == 3600


@pytest.mark.parametrize("raw, esperado", [("15", 900), ("120", 7200), ("0", 3600), ("-5", 3600), ("abc", 3600), (None, 3600)])
def test_janela_vem_do_ambiente_e_valor_invalido_cai_no_default(raw, esperado):
    env = {} if raw is None else {hr.TOLERANCE_ENV: raw}
    assert hr.tolerance_seconds(env) == esperado


def test_tolerancia_explicita_sobrepoe_o_default():
    candidates = _candidates(_row("a", TREINO + timedelta(minutes=40)))
    assert hr.match_session(TREINO, candidates, tolerance=600).status == hr.STATUS_NO_CANDIDATE
    assert hr.match_session(TREINO, candidates, tolerance=3600).status == hr.STATUS_MATCHED
