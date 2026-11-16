"""RPE esperado por série (#146): dono `training_history`, corte, entrada da API e Modo B."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain import training_history as th
from app.domain.effective_intensity import charged_rpe_targets_by_set
from app.domain.effective_intensity import draft_rpe_target
from app.workout_plan_templates import _slot_in_as_stored
from app.models import WorkoutPlanExerciseIn, WorkoutPlanTemplateSlotItemIn

RAIZ = Path(__file__).resolve().parents[3]


# --- dono: normalizar / compor / expandir ---------------------------------


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("8", "8"),
        ("8-8-8", "8"),  # uniforme colapsa
        ("7-8-9", "7-8-9"),
        ("8,5-9", "8.5-9"),  # meio ponto e vírgula decimal
        ("RPE 9", "9"),
        (9, "9"),
        (8.5, "8.5"),
    ],
)
def test_normalize_aceita_e_canoniza(entrada, esperado):
    assert th.normalize_rpe_target(entrada) == esperado


@pytest.mark.parametrize("entrada", ["sem número", "0", "11", "8-10.5", "8.25", "7-8.3-9"])
def test_normalize_recusa_sem_numero_fora_da_escala_ou_do_passo(entrada):
    with pytest.raises(th.RpeTargetError) as erro:
        th.normalize_rpe_target(entrada)
    assert str(erro.value)  # mensagem em português para o 422


def test_compose_lista_por_serie_e_colapso_no_fim():
    assert th.compose_rpe_target([7, 8, 9]) == "7-8-9"
    assert th.compose_rpe_target([8, 9, 9]) == "8-9"
    assert th.compose_rpe_target([]) is None
    with pytest.raises(th.RpeTargetError):
        th.compose_rpe_target([8, 10.7])


def test_expand_uniforme_crescente_ausente_e_mudanca_de_series():
    assert th.expand_rpe_targets("8", 3) == [8.0, 8.0, 8.0]
    assert th.expand_rpe_targets("7-8-9", 3) == [7.0, 8.0, 9.0]
    assert th.expand_rpe_targets("7-8-9", 5) == [7.0, 8.0, 9.0, 9.0, 9.0]  # menos degraus que séries
    assert th.expand_rpe_targets("7-8-9", 2) == [7.0, 8.0]  # degraus excedentes ignorados
    assert th.expand_rpe_targets(None, 3) == []
    assert th.expand_rpe_targets("8", None) == []
    assert th.expand_rpe_targets("8", 0) == []
    assert th.expand_rpe_targets("ilegível", 3) == []  # dado legado nunca quebra a leitura


# --- cobrado por série (ficha exibida) -------------------------------------


def test_cobrado_respeita_rampa_e_teto():
    assert charged_rpe_targets_by_set("7-8-9", 3, 9) == [7.0, 8.0, 9.0]
    assert charged_rpe_targets_by_set("8-9-10", 3, 9) == [8.0, 9.0, 9.0]  # teto de hoje corta
    assert charged_rpe_targets_by_set("9", 3, 9) == [9.0, 9.0, 9.0]  # uniforme: o alvo efetivo
    assert charged_rpe_targets_by_set("7-8-9", 5, 9) == [7.0, 8.0, 9.0, 9.0, 9.0]


def test_cobrado_sem_alvo_efetivo_ou_sem_series_e_vazio():
    assert charged_rpe_targets_by_set("7-8-9", 3, None) == []  # ficha antiga sem snapshot
    assert charged_rpe_targets_by_set(None, 0, 9) == []


# (Série preparatória/técnica sem status: já coberto por
# test_rpe10_lote6_uma_voz.py::test_serie_que_nao_e_de_trabalho_nao_tem_status.)


# --- saída de payload de rascunho (decorate_draft_payload) ------------------


def _payload(rpe, sets):
    return {"name": "m", "slots": [{"items": [{"rpe_target": rpe, "sets_count": sets}, "x"]}]}


@pytest.mark.parametrize(
    "rpe,sets,esperado",
    [("7-8-9", 3, [7.0, 8.0, 9.0]), ("7-8-9", 2, [7.0, 8.0]), ("7-8-9", None, [7.0, 8.0, 9.0]), (None, 3, [])],
)
def test_saida_do_rascunho_traz_a_lista_derivada(rpe, sets, esperado):
    from app.workout_plan_templates import decorate_draft_payload

    item = decorate_draft_payload(_payload(rpe, sets))["slots"][0]["items"][0]
    assert item["rpe_targets_by_set"] == esperado
    assert item["rpe_target"] == rpe


def test_decorar_nao_muta_o_original_e_a_lista_de_volta_nao_troca_o_texto():
    from app.workout_plan_templates import decorate_draft_payload

    original = _payload("7-8-9", 3)
    decorado = decorate_draft_payload(original)
    assert "rpe_targets_by_set" not in original["slots"][0]["items"][0]
    item = decorado["slots"][0]["items"][0]
    # Volta por engano a um caminho de regravação: o texto composto é o mesmo.
    volta = WorkoutPlanTemplateSlotItemIn(item_order=1, item_kind="equipment", **item)
    assert volta.rpe_target == "7-8-9"
    # O caminho de rascunho->schema descarta a derivada e mantém o texto gravado.
    slot = _slot_in_as_stored({"sequence": 1, "name": "A", "items": [{"item_order": 1, "item_kind": "equipment", **item}]})
    assert slot.items[0].rpe_target == "7-8-9"


def test_decorar_tolera_payload_sem_slots():
    from app.workout_plan_templates import decorate_draft_payload

    assert decorate_draft_payload(None) is None
    assert decorate_draft_payload({"name": "m"}) == {"name": "m"}


# --- entrada da API --------------------------------------------------------


def _ficha(**kw):
    return WorkoutPlanExerciseIn(sequence=1, exercise_name="x", sets_count=3, **kw)


def _slot(**kw):
    return WorkoutPlanTemplateSlotItemIn(item_order=1, item_kind="equipment", sets_count=3, **kw)


@pytest.mark.parametrize("modelo", [_slot, _ficha])
def test_entrada_por_texto_e_por_lista_dao_o_mesmo(modelo):
    assert modelo(rpe_target="7-8-9").rpe_target == "7-8-9"
    assert modelo(rpe_targets_by_set=[7, 8, 9]).rpe_target == "7-8-9"
    assert modelo(rpe_targets_by_set=[8, 8, 8]).rpe_target == "8"
    assert modelo(rpe_targets_by_set=[]).rpe_target is None
    # a lista vence o texto
    assert modelo(rpe_target="9", rpe_targets_by_set=[7, 8]).rpe_target == "7-8"


@pytest.mark.parametrize("modelo", [_slot, _ficha])
def test_entrada_invalida_vira_422(modelo):
    for kw in ({"rpe_target": "11"}, {"rpe_target": "abc"}, {"rpe_targets_by_set": [8, 10.7]},
               {"rpe_targets_by_set": [7, 8, 9, 10]}):  # mais alvos que séries
        with pytest.raises(ValidationError):
            modelo(**kw)


def test_campo_nao_enviado_continua_nao_enviado():
    item = WorkoutPlanExerciseIn(sequence=1, exercise_name="x")
    assert "rpe_target" not in item.model_fields_set  # o save preserva o gravado
    assert item.rpe_target is None


# --- Modo B ----------------------------------------------------------------


def _prescricao(alvo):
    return alvo


def test_modo_b_preserva_a_rampa_cortada_pelo_teto():
    assert draft_rpe_target(_prescricao("9"), "8-9-10") == "8-9"
    assert draft_rpe_target(_prescricao("10"), "7-8-9") == "7-8-9"


def test_modo_b_sem_rampa_ou_sem_prescricao_fica_como_hoje():
    assert draft_rpe_target(_prescricao("9"), "8") == "9"
    assert draft_rpe_target(_prescricao("9"), None) == "9"
    assert draft_rpe_target(_prescricao("9"), "8-8") == "9"
    assert draft_rpe_target(None, "8-9-10") == "8-9-10"
    assert draft_rpe_target(_prescricao(None), "8-9-10") == "8-9-10"


# --- saída do item do Modelo, cópia e texto com números a mais ------------


def test_saida_do_item_sem_series_traz_a_rampa_gravada():
    assert th.template_item_rpe_targets("7-8-9", None) == [7.0, 8.0, 9.0]
    assert th.template_item_rpe_targets("7-8-9", 0) == [7.0, 8.0, 9.0]
    assert th.template_item_rpe_targets("8", None) == [8.0]
    assert th.template_item_rpe_targets("7-8-9", 5) == [7.0, 8.0, 9.0, 9.0, 9.0]
    assert th.template_item_rpe_targets("7-8-9", 2) == [7.0, 8.0]  # excedentes ignorados (editor so tem 2 series)
    assert th.template_item_rpe_targets(None, None) == []
    assert th.template_item_rpe_targets("ilegível", 3) == []


def test_is_rpe_ramp_um_criterio_so():
    assert th.is_rpe_ramp("7-8") and th.is_rpe_ramp("8-9-10")
    assert not th.is_rpe_ramp("8-8") and not th.is_rpe_ramp("9") and not th.is_rpe_ramp(None)


def test_texto_com_numero_a_mais_fica_como_o_parser_le_hoje():
    assert th.normalize_rpe_target("7-8 (RIR 2)") == "7-8-2"  # shortcut registrado no dono


@pytest.mark.parametrize(
    "texto, series, esperado",
    [
        ("7-8-9", None, "7-8-9"),
        ("7-8-9", 2, "7-8-9"),  # rampa mais longa que as series: texto preservado
        ("7-8-9", 0, "7-8-9"),
        ("12-abc", 3, "12-abc"),  # legado fora da escala: copia sem erro e sem perder
    ],
)
def test_copia_de_slot_gravado_preserva_o_texto(texto, series, esperado):
    # forma que `_load_version_tree` devolve: texto gravado + lista derivada.
    item = {"item_order": 1, "item_kind": "equipment", "sets_count": series, "rpe_target": texto,
            "rpe_targets_by_set": th.template_item_rpe_targets(texto, series)}
    slot = {"sequence": 1, "name": "A", "items": [item, {"item_order": 2, "item_kind": "equipment"}]}
    copia = _slot_in_as_stored(slot)
    assert copia.items[0].rpe_target == esperado
    assert copia.items[0].rpe_targets_by_set is None  # a derivada nao realimenta o schema
    assert copia.items[1].rpe_target is None


# --- espelho ---------------------------------------------------------------


def test_escala_exportada_para_o_frontend():
    gerado = json.loads((RAIZ / "frontend" / "src" / "lib" / "motor-constants.generated.json").read_text("utf-8"))
    assert gerado["rpe_target_scale"] == {"min": 1.0, "max": 10.0, "step": 0.5}
    assert th.rpe_target_scale() == (Decimal(1), Decimal(10), Decimal("0.5"))


# --- item sem sets_count: a rampa não encolhe (tamanho = séries do dono) ----


@pytest.mark.parametrize("entrada", [{"rpe_targets_by_set": [7, 8, 9, 9, 9]}, {"rpe_target": "7-8-9-9-9"}])
def test_sem_series_nao_colapsa_e_o_dono_de_intensidade_concorda(entrada):
    from app.domain import intensity_density as idn

    item = WorkoutPlanTemplateSlotItemIn(item_order=1, item_kind="equipment", sets_count=None, **entrada)
    assert item.rpe_target == "7-8-9-9-9"
    lista = {"sets_count": None, "rpe_target": None, "advanced_technique": None}
    por_serie = idn.item_intensity({**lista, "rpe_target": "7-8-9-9-9"}, [])
    gravado = idn.item_intensity({**lista, "rpe_target": item.rpe_target}, [])
    assert gravado == por_serie
    assert gravado != idn.item_intensity({**lista, "rpe_target": "7-8-9"}, [])


def test_com_series_continua_colapsando_e_a_intensidade_do_dono_e_a_mesma():
    from app.domain import intensity_density as idn

    item = WorkoutPlanTemplateSlotItemIn(
        item_order=1, item_kind="equipment", sets_count=5, rpe_targets_by_set=[7, 8, 9, 9, 9]
    )
    assert item.rpe_target == "7-8-9"
    base = {"sets_count": 5, "advanced_technique": None}
    assert idn.item_intensity({**base, "rpe_target": item.rpe_target}, []) == idn.item_intensity(
        {**base, "rpe_target": "7-8-9-9-9"}, []
    )


def test_uniforme_sem_series_e_ida_e_volta_estavel():
    item = WorkoutPlanTemplateSlotItemIn(item_order=1, item_kind="equipment", sets_count=None, rpe_targets_by_set=[8, 8, 8])
    assert item.rpe_target == "8-8-8"
    assert th.template_item_rpe_targets("7-8-9-9-9", None) == [7.0, 8.0, 9.0, 9.0, 9.0]
