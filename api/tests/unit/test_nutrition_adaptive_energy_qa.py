"""QA independente do estimador adaptativo: propriedades sob entradas aleatórias/sujas (sementes fixas)."""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta

import pytest

from app.domain.nutrition import adaptive_energy as ae
from app.domain.nutrition.dashboard import DayState

D0 = date(2026, 1, 1)
_ESTADOS = list(DayState)
_SUJOS = [None, float("nan"), float("inf"), -float("inf"), -5.0, 0.0, "abc", True, "", 1e12]


def _entrada_aleatoria(rng: random.Random, i: int, sujo: bool) -> ae.DayInput:
    def num(base: float, spread: float):
        if sujo and rng.random() < 0.25:
            return rng.choice(_SUJOS)
        return max(0.0, rng.gauss(base, spread))

    pesos = tuple(
        (rng.choice(_SUJOS) if sujo and rng.random() < 0.2 else rng.gauss(80, 6))
        for _ in range(rng.choice([0, 0, 1, 1, 2]))
    )
    return ae.DayInput(
        day=D0 + timedelta(days=i),
        day_state=rng.choice(_ESTADOS) if sujo else rng.choice([DayState.LOGGED, DayState.LOGGED, DayState.NO_RECORD, DayState.INCOMPLETE]),
        intake_kcal=num(2400, 700),
        bmr_kcal=num(2000, 200) if rng.random() < 0.95 else None,
        weights_kg=pesos,
        fat_mass_kg=num(20, 5) if rng.random() < 0.9 else None,
        active_kcal=num(500, 400),
        activity_confirmed=rng.choice([True, True, False, None]),
    )


def _serie(seed: int, n: int = 70, sujo: bool = True) -> list[ae.DayInput]:
    rng = random.Random(seed)
    return [_entrada_aleatoria(rng, i, sujo) for i in range(n)]


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("sujo", [False, True])
def test_invariantes_sob_entradas_aleatorias_e_sujas(seed, sujo):
    estados = ae.replay(_serie(seed, sujo=sujo))
    anterior = None
    for s in estados:
        assert 0.0 <= s.confidence <= 1.0
        assert s.window_days <= ae.WINDOW_DAYS
        assert -250.0 <= s.activity_adjustment_kcal <= 300.0
        for v in (s.adaptive_kcal, s.base_kcal, s.estimated_today_kcal, s.observed_kcal):
            assert v is None or math.isfinite(v)
        if anterior is not None and anterior.adaptive_kcal is not None and s.adaptive_kcal is not None:
            assert abs(s.adaptive_kcal - anterior.adaptive_kcal) <= ae.EXPENDITURE_MAX_STEP_KCAL + 1e-9
        if anterior is not None and anterior.adaptive_kcal is not None:
            # valor adaptativo, uma vez existente, nunca some (janela inválida não derruba)
            assert s.adaptive_kcal is not None
        json.dumps(s.to_public_dict(), allow_nan=False)
        anterior = s


@pytest.mark.parametrize("seed", range(10))
def test_replay_igual_a_steps_encadeados_e_retomada_em_qualquer_ponto(seed):
    entradas = _serie(seed, n=60)
    completo = ae.replay(entradas)
    encadeado = None
    for e in entradas:
        encadeado = ae.step(encadeado, e)
    assert encadeado == completo[-1]
    corte = random.Random(seed).randint(1, 58)
    retomado = ae.replay(entradas[corte:], initial=completo[corte - 1])
    assert retomado[-1] == completo[-1]


def test_replay_ignora_ordem_de_entrada():
    entradas = _serie(3, n=40)
    embaralhado = list(entradas)
    random.Random(1).shuffle(embaralhado)
    assert ae.replay(embaralhado)[-1] == ae.replay(entradas)[-1]


def test_entrada_toda_suja_nao_levanta_e_fica_indisponivel():
    sujas = [
        ae.DayInput(day=D0 + timedelta(days=i), day_state=DayState.LOGGED, intake_kcal=float("nan"),
                    bmr_kcal=float("inf"), weights_kg=(float("nan"), -3, "x"), fat_mass_kg=-1.0,
                    active_kcal=float("nan"), activity_confirmed=True)
        for i in range(30)
    ]
    final = ae.replay(sujas)[-1]
    assert final.adaptive_kcal is None
    assert final.confidence == 0.0
    assert ae.FLAG_ADAPTIVE_UNAVAILABLE in final.flags


def test_saltos_grandes_de_dias_nao_quebram_a_janela():
    a = ae.step(None, ae.DayInput(day=D0, day_state=DayState.LOGGED, intake_kcal=2400, bmr_kcal=2000))
    b = ae.step(a, ae.DayInput(day=D0 + timedelta(days=400), day_state=DayState.LOGGED, intake_kcal=2400, bmr_kcal=2000))
    assert b.window_days == ae.WINDOW_DAYS
    assert 0.0 <= b.confidence <= 1.0
