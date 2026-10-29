"""Calendário de fases (dono `app/domain/nutrition/phases.py`): MATADOR 2/2
(Byrne 2018), ICECAP 3/1 (Peos 2021), refeeds (Campbell 2020), teto de semanas
de cutting sem pausa e %G de parada (decisão D-C)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.domain.nutrition import phases as ph
from app.domain.nutrition.anthropometrics import Sex
from app.domain.nutrition.strategy import Strategy

K = ph.PhaseKind
P = ph.BreakProtocol
SEGUNDA = date(2026, 10, 12)  # segunda-feira


def test_constantes_das_fontes():
    assert (ph.MATADOR_ON_WEEKS, ph.MATADOR_OFF_WEEKS) == (2, 2)
    assert (ph.ICECAP_ON_WEEKS, ph.ICECAP_OFF_WEEKS) == (3, 1)
    assert ph.REFEED_DAYS_PER_WEEK_DEFAULT == 2
    assert ph.MAX_CUT_WEEKS_WITHOUT_BREAK == 8
    assert ph.BULK_STOP_BODY_FAT_PERCENT == {Sex.MALE: 15.0, Sex.FEMALE: 25.0}
    assert ph.CUT_STOP_BODY_FAT_PERCENT == {Sex.MALE: 10.0, Sex.FEMALE: 18.0}
    assert ph.MAINTENANCE_KCAL_PHASES == {K.MAINTENANCE, K.DIET_BREAK, K.REFEED}


@pytest.mark.parametrize(
    ("estrategia", "semanas", "protocolo", "blocos", "avisos"),
    [
        (Strategy.CUTTING, 12, P.MATADOR, [(K.CUTTING, 2), (K.DIET_BREAK, 2)] * 3, ()),
        (Strategy.CUTTING, 5, P.MATADOR, [(K.CUTTING, 2), (K.DIET_BREAK, 2), (K.CUTTING, 1)], ()),
        (Strategy.CUTTING, 8, P.ICECAP, [(K.CUTTING, 3), (K.DIET_BREAK, 1)] * 2, ()),
        (Strategy.CUTTING, 10, P.ICECAP, [(K.CUTTING, 3), (K.DIET_BREAK, 1)] * 2 + [(K.CUTTING, 2)], ()),
        (Strategy.CUTTING, 8, P.NONE, [(K.CUTTING, 8)], ()),
        (Strategy.CUTTING, 9, P.NONE, [(K.CUTTING, 9)], (ph.WARN_EXCEEDS_MAX_CUT_WEEKS,)),
        (Strategy.BULKING, 12, P.MATADOR, [(K.BULKING, 12)], ()),  # pausa só no cutting
        (Strategy.MAINTENANCE, 4, P.NONE, [(K.MAINTENANCE, 4)], ()),
        (Strategy.CUTTING, 1, P.ICECAP, [(K.CUTTING, 1)], ()),
    ],
)
def test_propose_calendar(estrategia, semanas, protocolo, blocos, avisos):
    calendario = ph.propose_calendar(estrategia, SEGUNDA, semanas, protocolo)
    assert [(b.kind, b.days // 7) for b in calendario.blocks] == blocos
    assert calendario.warnings == avisos
    if estrategia != Strategy.CUTTING:
        assert calendario.protocol == P.NONE
    # Invariantes: blocos contíguos, semanas inteiras, índices em ordem, cobre a janela toda.
    assert calendario.blocks[0].start == SEGUNDA
    assert calendario.blocks[-1].end == SEGUNDA + timedelta(days=7 * semanas - 1)
    assert sum(b.days for b in calendario.blocks) == 7 * semanas
    assert [b.index for b in calendario.blocks] == list(range(len(calendario.blocks)))
    for anterior, seguinte in zip(calendario.blocks, calendario.blocks[1:]):
        assert seguinte.start == anterior.end + timedelta(days=1)
        assert anterior.kind != seguinte.kind
    json.dumps(calendario.to_dict())


@pytest.mark.parametrize("semanas", [0, -1])
def test_semanas_precisam_ser_positivas(semanas):
    with pytest.raises(ValueError, match=ph.LIMIT_WEEKS_NOT_POSITIVE):
        ph.propose_calendar(Strategy.CUTTING, SEGUNDA, semanas)


def test_protocolo_nunca_passa_do_teto_de_semanas_sem_pausa():
    for protocolo in (P.MATADOR, P.ICECAP):
        calendario = ph.propose_calendar(Strategy.CUTTING, SEGUNDA, 52, protocolo)
        assert max(b.days // 7 for b in calendario.blocks if b.kind == K.CUTTING) <= ph.MAX_CUT_WEEKS_WITHOUT_BREAK
        assert calendario.warnings == ()


def test_phase_for_date():
    blocos = ph.propose_calendar(Strategy.CUTTING, SEGUNDA, 4, P.MATADOR).blocks
    assert ph.phase_for_date(blocos, SEGUNDA).kind == K.CUTTING
    assert ph.phase_for_date(blocos, SEGUNDA + timedelta(days=13)).kind == K.CUTTING
    assert ph.phase_for_date(blocos, SEGUNDA + timedelta(days=14)).kind == K.DIET_BREAK
    assert ph.phase_for_date(blocos, SEGUNDA + timedelta(days=27)).kind == K.DIET_BREAK
    assert ph.phase_for_date(blocos, SEGUNDA + timedelta(days=28)) is None
    assert ph.phase_for_date(blocos, SEGUNDA - timedelta(days=1)) is None


def test_refeeds_so_nos_blocos_de_cutting():
    blocos = ph.propose_calendar(Strategy.CUTTING, SEGUNDA, 8, P.MATADOR).blocks
    datas = ph.refeed_dates(blocos, (5, 6))  # sábado e domingo
    semanas_de_cutting = sum(b.days // 7 for b in blocos if b.kind == K.CUTTING)
    assert len(datas) == ph.REFEED_DAYS_PER_WEEK_DEFAULT * semanas_de_cutting
    assert all(d.weekday() in (5, 6) for d in datas)
    assert all(ph.phase_for_date(blocos, d).kind == K.CUTTING for d in datas)
    assert datas == tuple(sorted(datas))


def test_refeed_fora_de_cutting_e_lista_vazia():
    bulking = ph.propose_calendar(Strategy.BULKING, SEGUNDA, 4).blocks
    assert ph.refeed_dates(bulking, (5, 6)) == ()
    assert ph.refeed_dates(ph.propose_calendar(Strategy.CUTTING, SEGUNDA, 4).blocks, ()) == ()


@pytest.mark.parametrize(
    ("estrategia", "sexo", "esperado"),
    [
        (Strategy.BULKING, Sex.MALE, 15.0),
        (Strategy.BULKING, Sex.FEMALE, 25.0),
        (Strategy.CUTTING, Sex.MALE, 10.0),
        (Strategy.CUTTING, Sex.FEMALE, 18.0),
        (Strategy.BULKING, Sex.OTHER, None),
        (Strategy.CUTTING, None, None),
        (Strategy.MAINTENANCE, Sex.MALE, None),
    ],
)
def test_stop_body_fat_default(estrategia, sexo, esperado):
    assert ph.stop_body_fat_default(estrategia, sexo) == esperado


@pytest.mark.parametrize(
    ("estrategia", "gordura", "limite", "esperado"),
    [
        (Strategy.BULKING, 15.0, 15.0, True),
        (Strategy.BULKING, 14.9, 15.0, False),
        (Strategy.CUTTING, 10.0, 10.0, True),
        (Strategy.CUTTING, 10.1, 10.0, False),
        (Strategy.MAINTENANCE, 20.0, 15.0, None),
        (Strategy.BULKING, None, 15.0, None),
        (Strategy.CUTTING, 12.0, None, None),
    ],
)
def test_should_stop(estrategia, gordura, limite, esperado):
    assert ph.should_stop(estrategia, gordura, limite) is esperado
