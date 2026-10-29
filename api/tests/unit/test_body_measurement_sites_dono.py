"""Dono dos locais de medida por fita (`domain/body_measurement_sites.py`) e da
origem da kcal do treino (`domain/calories_source.py`), nutrição L0.

- vocabulário ordenado (ENUM), rótulo para todo local, lado dos bilaterais;
- `export_for_frontend()` é o bloco `body_measurement_sites` do JSON gerado;
- `app/body_measurements.py` e o `body_service` só reexportam (sem cópia);
- `resolve_calories_source`: manual pela coluna, intervals por igualdade com o
  link, `None` sem kcal ou origem desconhecida;
- `source_on_manual_write`: `None` ao limpar, `None` quando a kcal digitada é a
  do link (0,01; a leitura volta a inferir `intervals`), `manual` no resto."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import body_measurements as bm
from app.domain import body_measurement_sites as sites
from app.domain import calories_source as cs
from app.intelligence.dashboard import body_service


def test_vocabulario_ordenado_com_rotulo_e_lado_para_todos():
    assert sites.MEASUREMENT_SITES[0] == sites.WAIST_SITE == "waist"
    assert len(sites.MEASUREMENT_SITES) == len(set(sites.MEASUREMENT_SITES)) == 12
    assert set(sites.SITE_LABELS) == set(sites.MEASUREMENT_SITES)
    assert all(label and label[0].isupper() for label in sites.SITE_LABELS.values())
    assert len(set(sites.SITE_LABELS.values())) == len(sites.SITE_LABELS), "rótulos únicos"
    for site in sites.MEASUREMENT_SITES:
        side = sites.site_side(site)
        if site.endswith("_l"):
            assert side == sites.SIDE_LEFT and sites.SITE_LABELS[site].endswith(("esquerdo", "esquerda"))
        elif site.endswith("_r"):
            assert side == sites.SIDE_RIGHT and sites.SITE_LABELS[site].endswith(("direito", "direita"))
        else:
            assert side is None
    assert sites.NECK_SITE in sites.SITES_ADDED_075
    assert sites.site_label("neck") == "Pescoço"
    assert sites.site_label("nariz") == "nariz"
    assert sites.is_measurement_site("forearm_r") and not sites.is_measurement_site("belly")


def test_export_para_o_frontend_e_o_bloco_completo():
    bloco = sites.export_for_frontend()
    assert bloco["sites"] == list(sites.MEASUREMENT_SITES)
    assert bloco["labels"] == sites.SITE_LABELS
    assert bloco["sides"] == {
        "arm_l": "left", "arm_r": "right", "thigh_l": "left", "thigh_r": "right",
        "calf_l": "left", "calf_r": "right", "forearm_l": "left", "forearm_r": "right",
    }
    assert bloco["waist_site"] == "waist" and bloco["neck_site"] == "neck"


def test_api_e_engines_consomem_o_dono_sem_copia():
    assert bm.MEASUREMENT_SITES is sites.MEASUREMENT_SITES
    assert bm.MEASUREMENT_SITE_LABELS is sites.SITE_LABELS
    assert body_service.MEASUREMENT_SITE_LABELS is sites.SITE_LABELS
    assert body_service.WAIST_SITE == sites.WAIST_SITE
    assert bm.normalize_site(" Neck ") == "neck"
    with pytest.raises(ValueError):
        bm.normalize_site("belly")


@pytest.mark.parametrize(
    ("kcal", "stored", "linked", "esperado"),
    [
        (None, None, None, None),
        (None, "manual", Decimal("300"), None),  # sem kcal nunca tem origem
        (Decimal("412.00"), "manual", Decimal("412.00"), "manual"),  # a coluna manda
        (Decimal("412.00"), "intervals", None, "intervals"),
        (Decimal("412.00"), None, Decimal("412"), "intervals"),  # igualdade na precisão da coluna
        (412, None, 412.0, "intervals"),
        (Decimal("412.00"), None, Decimal("410.00"), None),  # kcal diferente do link: desconhecida
        (Decimal("412.00"), None, None, None),
        (Decimal("412.00"), "zepp", None, None),  # código fora do vocabulário é ignorado
        ("abc", "manual", None, None),  # kcal inválida = sem kcal
    ],
)
def test_resolve_calories_source(kcal, stored, linked, esperado):
    assert cs.resolve_calories_source(calories_kcal=kcal, stored_source=stored, intervals_calories_kcal=linked) == esperado


@pytest.mark.parametrize(
    ("kcal", "linked", "esperado"),
    [
        (None, None, None),  # limpar
        (None, Decimal("412.37"), None),
        (Decimal("412.37"), Decimal("412.37"), None),  # salvar sem mudar: continua `intervals` na leitura
        ("412.370", Decimal("412.37"), None),  # igualdade na precisão de 0,01
        (412.37, "412.37", None),
        (Decimal("412"), Decimal("412.37"), "manual"),  # o 412 arredondado da tela NÃO é o valor do link
        (Decimal("412.36"), Decimal("412.37"), "manual"),
        (Decimal("0"), None, "manual"),  # zero informado é valor, não ausência
        (Decimal("412"), None, "manual"),  # treino sem link
        ("abc", Decimal("412.37"), None),  # kcal inválida = sem kcal
    ],
)
def test_source_on_manual_write(kcal, linked, esperado):
    assert cs.source_on_manual_write(kcal, linked) == esperado


def test_gravacao_e_leitura_concordam():
    # O que a gravação deixa na coluna, a leitura resolve para a origem esperada.
    link = Decimal("412.37")
    assert cs.resolve_calories_source(
        calories_kcal=link, stored_source=cs.source_on_manual_write(link, link), intervals_calories_kcal=link
    ) == cs.CALORIES_SOURCE_INTERVALS
    manual = Decimal("500")
    assert cs.resolve_calories_source(
        calories_kcal=manual, stored_source=cs.source_on_manual_write(manual, link), intervals_calories_kcal=link
    ) == cs.CALORIES_SOURCE_MANUAL


def test_rotulos_da_origem():
    assert cs.calories_source_label("manual") == "Manual"
    assert cs.calories_source_label("intervals") == "Intervals"
    assert cs.calories_source_label(None) is None
    assert cs.calories_source_label("zepp") is None
    assert set(cs.CALORIES_SOURCE_LABELS) == set(cs.CALORIES_SOURCES)
