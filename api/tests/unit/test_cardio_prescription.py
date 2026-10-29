"""Dono da prescrição de cardio do Modelo (M98 C0): `domain/cardio_prescription.py`.

Validação por modo, tetos, padrões, tempo total (Normal e HIIT), texto
canônico, normalização para a coluna e forma canônica da versão do Modelo.

Rodar: cd api && python -m pytest tests/unit/test_cardio_prescription.py
"""

from __future__ import annotations

import pytest

from app.domain import cardio_policy
from app.domain import cardio_prescription as cp
from app.domain import hr_zones

NORMAL = {
    "item_kind": "cardio",
    "cardio_mode": "continuous",
    "cardio_warmup_minutes": 5,
    "cardio_duration_minutes": 30,
    "cardio_target_zone": 2,
    "cardio_cooldown_minutes": 5,
}
HIIT = {
    "item_kind": "cardio",
    "cardio_mode": "hiit",
    "cardio_warmup_minutes": 10,
    "hiit_low_seconds": 90,
    "hiit_high_seconds": 30,
    "hiit_low_zone": 2,
    "hiit_high_zone": 4,
    "hiit_rounds": 8,
    "cardio_cooldown_minutes": 5,
}
ANTIGO = {"item_kind": "cardio", "cardio_duration_minutes": 20}


# --- validação -------------------------------------------------------------


@pytest.mark.parametrize("item", [NORMAL, HIIT, ANTIGO, {**NORMAL, "cardio_target_zone": None}])
def test_prescricoes_validas_passam(item):
    cp.validate(item)


def test_item_antigo_sem_modo_e_normal():
    assert cp.mode_of(ANTIGO) is cp.CardioMode.CONTINUOUS
    assert cp.mode_of({**ANTIGO, "cardio_mode": "  HIIT "}) is cp.CardioMode.HIIT
    assert cp.mode_of({**ANTIGO, "cardio_mode": b"hiit"}) is cp.CardioMode.HIIT


@pytest.mark.parametrize("duracao", [None, 0, -5])
def test_normal_sem_duracao_mantem_a_mensagem_de_sempre(duracao):
    with pytest.raises(cp.CardioPrescriptionError, match="cardio_duration_minutes > 0"):
        cp.validate({**NORMAL, "cardio_duration_minutes": duracao})


def test_modo_desconhecido_e_recusado_na_escrita():
    with pytest.raises(cp.CardioPrescriptionError, match="tipo de cardio inválido"):
        cp.validate({**NORMAL, "cardio_mode": "fartlek"})


@pytest.mark.parametrize("campo", ["hiit_low_seconds", "hiit_high_seconds", "hiit_low_zone", "hiit_high_zone",
                                   "hiit_rounds"])
def test_hiit_exige_os_campos_do_modo(campo):
    with pytest.raises(cp.CardioPrescriptionError, match="cardio HIIT exige"):
        cp.validate({**HIIT, campo: None})


def test_hiit_nao_exige_duracao_e_ignora_a_do_normal():
    cp.validate({**HIIT, "cardio_duration_minutes": None})
    cp.validate({**HIIT, "cardio_duration_minutes": 9999})  # campo do outro modo: descartado


@pytest.mark.parametrize("baixa,alta", [(3, 3), (4, 2)])
def test_hiit_zona_alta_precisa_ser_maior_que_a_baixa(baixa, alta):
    with pytest.raises(cp.CardioPrescriptionError, match="zona alta precisa ser maior"):
        cp.validate({**HIIT, "hiit_low_zone": baixa, "hiit_high_zone": alta})


@pytest.mark.parametrize(
    "item,campo",
    [
        (NORMAL, "cardio_target_zone"),
        (HIIT, "hiit_low_zone"),
        (HIIT, "hiit_high_zone"),
    ],
)
def test_zonas_validas_sao_as_do_dono_das_zonas(item, campo):
    for zona in (hr_zones.HR_ZONE_MIN - 1, hr_zones.HR_ZONE_MAX + 1):
        with pytest.raises(cp.CardioPrescriptionError, match="entre Z1 e Z5"):
            cp.validate({**item, campo: zona, **({"hiit_high_zone": 5} if campo == "hiit_low_zone" else {})})


@pytest.mark.parametrize(
    "item,campo",
    [
        (NORMAL, "cardio_duration_minutes"),
        (NORMAL, "cardio_warmup_minutes"),
        (NORMAL, "cardio_cooldown_minutes"),
        (HIIT, "hiit_low_seconds"),
        (HIIT, "hiit_high_seconds"),
        (HIIT, "hiit_rounds"),
    ],
)
def test_tetos_e_minimos_por_campo(item, campo):
    minimo, teto = cp.FIELD_LIMITS[campo]
    with pytest.raises(cp.CardioPrescriptionError, match="precisa estar entre"):
        cp.validate({**item, campo: teto + 1})
    if minimo > 0:
        with pytest.raises(cp.CardioPrescriptionError):
            cp.validate({**item, campo: minimo - 1})


def test_valor_fracionario_e_recusado():
    with pytest.raises(cp.CardioPrescriptionError, match="número inteiro"):
        cp.validate({**HIIT, "hiit_rounds": 2.5})


def test_teto_da_sessao_inteira():
    limite = cp.CARDIO_SESSION_MAX_MINUTES
    no_limite = {**NORMAL, "cardio_warmup_minutes": 0, "cardio_cooldown_minutes": 0, "cardio_duration_minutes": limite}
    cp.validate(no_limite)
    with pytest.raises(cp.CardioPrescriptionError, match=f"passa de {limite} min"):
        cp.validate({**no_limite, "cardio_cooldown_minutes": 1})


# --- tempo total e texto ----------------------------------------------------


def test_tempo_total_normal_e_hiit():
    assert cp.total_seconds(NORMAL) == (5 + 30 + 5) * 60
    assert cp.total_seconds(HIIT) == 10 * 60 + 8 * (30 + 90) + 5 * 60
    assert cp.main_seconds(HIIT) == 8 * 120


def test_tempo_do_item_antigo_e_o_de_sempre():
    assert cp.total_seconds(ANTIGO) == 20 * 60
    assert cp.total_seconds({**ANTIGO, "cardio_duration_minutes": 12.5}) == 750


def test_tempo_tolera_campo_ausente_ou_negativo():
    assert cp.total_seconds({"item_kind": "cardio", "cardio_mode": "hiit"}) == 0
    assert cp.total_seconds({**HIIT, "cardio_warmup_minutes": -3, "cardio_cooldown_minutes": "x"}) == 8 * 120


def test_texto_canonico():
    assert cp.describe(NORMAL) == "5 min aquec. + 30 min Z2 + 5 min recuperação"
    assert cp.describe(HIIT) == "10 min aquec. + 8× [30 s Z4 / 90 s Z2] + 5 min recuperação"
    assert cp.describe(ANTIGO) == "20 min"


def test_resumo_do_slot_e_minutos_do_rascunho():
    itens = [NORMAL, {"item_kind": "equipment", "cardio_duration_minutes": 99}, HIIT]
    resumo = cp.summarize_slot(itens)
    assert [r["cardio_mode"] for r in resumo] == ["continuous", "hiit"]
    assert resumo[1]["total_seconds"] == 1860
    assert resumo[1]["text"].startswith("10 min aquec.")
    assert cp.slot_cardio_minutes(itens) == 40 + 31
    assert cp.slot_cardio_minutes([ANTIGO]) == 20
    assert cp.slot_cardio_minutes([{"item_kind": "equipment"}]) is None
    assert cp.draft_cardio([]) == {"cardio_minutes": None, "cardio_prescriptions": []}


# --- normalização e forma canônica -----------------------------------------


def test_normalizacao_descarta_campos_do_outro_modo():
    normal = cp.normalize_fields({**NORMAL, "hiit_rounds": 8, "hiit_low_zone": 2})
    assert normal["hiit_rounds"] is None and normal["hiit_low_zone"] is None
    assert normal["cardio_mode"] == "continuous" and normal["cardio_duration_minutes"] == 30
    hiit = cp.normalize_fields({**HIIT, "cardio_duration_minutes": 20, "cardio_target_zone": 3})
    assert hiit["cardio_duration_minutes"] is None and hiit["cardio_target_zone"] is None
    assert cp.normalize_fields(ANTIGO)["cardio_mode"] == "continuous"


def test_item_que_nao_e_cardio_grava_tudo_nulo():
    assert set(cp.normalize_fields({"item_kind": "equipment", "cardio_duration_minutes": 20}).values()) == {None}


def test_forma_canonica_do_item_antigo_e_do_backfill_e_vazia():
    assert cp.canonical_extras(ANTIGO) == ()
    assert cp.canonical_extras({**ANTIGO, "cardio_mode": "continuous"}) == ()
    assert cp.canonical_extras({**ANTIGO, "cardio_warmup_minutes": 0, "cardio_cooldown_minutes": None}) == ()
    assert cp.canonical_extras({"item_kind": "equipment"}) == ()


def test_forma_canonica_ve_a_prescricao_nova():
    assert cp.canonical_extras(NORMAL) == (
        ("cardio_warmup_minutes", 5),
        ("cardio_cooldown_minutes", 5),
        ("cardio_target_zone", 2),
    )
    extras = dict(cp.canonical_extras(HIIT))
    assert extras["cardio_mode"] == "hiit" and extras["hiit_rounds"] == 8
    # Data API devolve inteiro como texto: mesma forma.
    assert cp.canonical_extras({**HIIT, "hiit_rounds": "8"}) == cp.canonical_extras(HIIT)


# --- padrões e export -------------------------------------------------------


def test_padroes_validos_e_vindos_da_politica_de_cardio():
    for modo, valores in cp.DEFAULTS.items():
        cp.validate({"item_kind": "cardio", "cardio_mode": modo.value, **valores})
    assert cp.DEFAULTS[cp.CardioMode.CONTINUOUS]["cardio_duration_minutes"] == (
        cardio_policy.Z2_DURATION_LADDER_MINUTES[0]
    )
    assert cp.DEFAULTS[cp.CardioMode.HIIT]["hiit_rounds"] == cardio_policy.HIIT_PREFERRED_ROUNDS


def test_export_para_o_frontend():
    dados = cp.export_for_frontend()
    assert dados["modes"] == list(cp.CARDIO_MODE_VALUES)
    assert dados["default_mode"] == "continuous"
    assert dados["seconds_per_minute"] == cp.SECONDS_PER_MINUTE
    assert dados["limits"]["cardio_duration_minutes"] == {
        "min": cp.CARDIO_DURATION_MINUTES_MIN,
        "max": cp.CARDIO_DURATION_MINUTES_MAX,
    }
    assert set(dados["defaults"]) == set(cp.CARDIO_MODE_VALUES)
    assert dados["zone_fields"] == sorted(cp.ZONE_FIELDS)


# --- M98 C1: resumo do item recolhido e mensagem para o editor ----------------


def test_resumo_curto_do_item_recolhido():
    hiit = {"item_kind": "cardio", "cardio_mode": "hiit", "cardio_warmup_minutes": 5, "hiit_rounds": 8,
            "hiit_high_seconds": 30, "hiit_high_zone": 4, "hiit_low_seconds": 90, "hiit_low_zone": 2,
            "cardio_cooldown_minutes": 5}
    assert cp.describe_short(hiit) == "HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min"
    assert cp.describe_short({"item_kind": "cardio", "cardio_duration_minutes": 20}) == "Normal 20 min"
    normal = {"item_kind": "cardio", "cardio_duration_minutes": 30, "cardio_target_zone": 2,
              "cardio_cooldown_minutes": 5}
    assert cp.describe_short(normal) == "Normal 30 min Z2 · 0+5 min"


def test_mensagem_de_validacao_para_o_editor():
    assert cp.validation_error(NORMAL) is None
    invertido = {**cp.DEFAULTS[cp.CardioMode.HIIT], "item_kind": "cardio", "cardio_mode": "hiit",
                 "hiit_low_zone": 4, "hiit_high_zone": 2}
    assert cp.validation_error(invertido) == "no HIIT a zona alta precisa ser maior que a zona baixa."
