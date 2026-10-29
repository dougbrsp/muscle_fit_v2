"""Fase 4, PR D: a projecao de ativacao de `_check_activation` virou funcao
PURA (`periodization/activation_projection.py`) sem mudanca de comportamento.

O que este arquivo trava:

1. a funcao pura devolve a projecao por grupo (caso pequeno, contas a mao);
2. o payload de warnings do caminho novo e IGUAL, byte a byte (chaves, ordem
   de chaves, arredondamentos e tipos), a um literal capturado da logica antiga
   E a uma copia congelada dessa logica (`develop` em e28ef42) - o padrao de
   oraculo de `tests/support/payload_diff.py`;
3. `_check_activation` esta de fato ligada a funcao pura, nos dois modos, com
   as leituras de banco substituidas;
4. os codigos `ACTIVATION_*` continuam importaveis de `validation` (reexport).

Nada aqui toca banco, relogio ou rede.
"""

from __future__ import annotations

import json

import pytest

from app.periodization import activation_projection as ap
from app.periodization import validation
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from tests.support.payload_diff import diferencas

# ---------------------------------------------------------------------------
# Cenario: janela de 7 dias (a semana do preview desde a Fase 7; M76 Lote 4a
# tirou a escala `window_days/7`, que ja valia 1), quatro metas, uma delas sem
# nome carregado (id 11), um grupo sem nenhum dia (Costas). Os dias 8 e 9 sao
# so dado de entrada (somam zero).
# Os totais sao exatamente representaveis em binario (3.5, 4.25, 4.75, 5.75)
# para a igualdade ser seca, sem tolerancia.
# ---------------------------------------------------------------------------

DIAS = (
    {"day": 1, "slot_sequence": 1, "slot_type": "normal", "groups": [
        {"muscle_group": "Peitoral", "total_equivalent": 3.5},
        {"muscle_group": "Bíceps", "total_equivalent": 4.75},
    ]},
    {"day": 2, "slot_sequence": 2, "slot_type": "descanso", "groups": []},
    {"day": 3, "slot_sequence": 3, "slot_type": "normal", "groups": [
        {"muscle_group": "Peitoral", "total_equivalent": 3.5},
        {"muscle_group": "Tríceps", "total_equivalent": 2.0},  # sem meta: ignorado
    ]},
    {"day": 4, "slot_sequence": 4, "slot_type": "normal", "groups": [
        {"muscle_group": "Bíceps", "total_equivalent": 5.75},
        {"muscle_group": "Peitoral", "total_equivalent": 4.25},
    ]},
    {"day": 5, "slot_sequence": 5, "slot_type": "normal", "groups": None},
    {"day": 6, "slot_sequence": 6, "slot_type": "normal", "groups": []},
    {"day": 7, "slot_sequence": 7, "slot_type": "normal", "groups": []},
    {"day": 8, "slot_sequence": 8, "slot_type": "normal", "groups": []},
    {"day": 9, "slot_sequence": 9, "slot_type": "normal", "groups": []},
)
JANELA = 7
METAS = {
    5: {"min": 10.0, "target": 15.0, "max": 20.0},
    2: {"min": 12.0, "target": 15.0, "max": 18.0},
    9: {"min": 4.0, "target": 6.0, "max": 8.0},
    11: {"min": 1.0, "target": 1.0, "max": 2.0},  # sem nome carregado -> pulado
}
NOMES = {2: "Peitoral", 5: "Costas", 9: "Bíceps"}

# Literal CAPTURADO da logica antiga (`_check_activation` em develop e28ef42)
# para o cenario acima, com janela 7. Contas: Peitoral 3.5+3.5+4.25 = 11.25 <
# 12; Costas 0.0 < 10; Bíceps 4.75+5.75 = 10.5 > 8. Ordem: por id (2, 5, 9).
WARNINGS_ANTIGOS = [
    {
        "code": "ACTIVATION_BELOW_MIN",
        "muscle_group_id": 2,
        "muscle_group_name": "Peitoral",
        "projected": 11.25,
        "min": 12.0,
        "window_days": 7,
    },
    {
        "code": "ACTIVATION_BELOW_MIN",
        "muscle_group_id": 5,
        "muscle_group_name": "Costas",
        "projected": 0.0,
        "min": 10.0,
        "window_days": 7,
    },
    {
        "code": "ACTIVATION_ABOVE_MAX",
        "muscle_group_id": 9,
        "muscle_group_name": "Bíceps",
        "projected": 10.5,
        "max": 8.0,
        "window_days": 7,
    },
]


def _logica_antiga(preview: dict, targets: dict, names: dict) -> list[dict]:
    """Copia CONGELADA, linha a linha, do trecho de Modo B de
    `_check_activation` antes do PR D (develop e28ef42), com
    `report.warning(code, **fields)` substituido pela construcao do mesmo dict.
    Nao editar: e o oraculo."""
    warnings: list[dict] = []
    window_days = preview.get("window_days") or 7
    projected: dict[str, float] = {}
    for day in preview.get("days") or []:
        for group in day.get("groups") or []:
            projected[group["muscle_group"]] = projected.get(group["muscle_group"], 0.0) + float(
                group["total_equivalent"]
            )
    scale = window_days / 7.0
    for group_id, values in sorted(targets.items()):
        name = names.get(group_id)
        if not name:
            continue
        total = round(projected.get(name, 0.0), 2)
        minimum = round(values["min"] * scale, 2)
        maximum = round(values["max"] * scale, 2)
        if total < minimum:
            warnings.append(
                {
                    "code": "ACTIVATION_BELOW_MIN",
                    "muscle_group_id": group_id,
                    "muscle_group_name": name,
                    "projected": total,
                    "min": minimum,
                    "window_days": window_days,
                }
            )
        elif total > maximum:
            warnings.append(
                {
                    "code": "ACTIVATION_ABOVE_MAX",
                    "muscle_group_id": group_id,
                    "muscle_group_name": name,
                    "projected": total,
                    "max": maximum,
                    "window_days": window_days,
                }
            )
    return warnings


def _bytes(payload) -> str:
    # `sort_keys=False`: a ORDEM das chaves faz parte do contrato.
    return json.dumps(payload, ensure_ascii=False, sort_keys=False)


def _inputs(days=DIAS, window_days=JANELA, targets=METAS, names=NOMES) -> ap.ActivationProjectionInputs:
    return ap.ActivationProjectionInputs(
        preview_days=tuple(days), window_days=window_days, targets=targets, muscle_group_names=names
    )


# ---------------------------------------------------------------------------
# 1. Funcao pura: projecao por grupo
# ---------------------------------------------------------------------------


def test_project_activation_caso_pequeno():
    rows = ap.project_activation(_inputs())
    assert rows == (
        ap.ActivationProjectionRow(2, "Peitoral", 11.25, 12.0, 18.0, 7, ap.PROJECTION_BELOW_MIN),
        ap.ActivationProjectionRow(5, "Costas", 0.0, 10.0, 20.0, 7, ap.PROJECTION_BELOW_MIN),
        ap.ActivationProjectionRow(9, "Bíceps", 10.5, 4.0, 8.0, 7, ap.PROJECTION_ABOVE_MAX),
    )
    # A meta 11 nao tem nome carregado: nao entra nem como linha.
    assert [r.muscle_group_id for r in rows] == [2, 5, 9]


def test_project_activation_dentro_da_meta_nao_gera_warning():
    rows = ap.project_activation(_inputs(targets={9: {"min": 4.0, "max": 12.0}}))
    assert rows[0].status == ap.PROJECTION_WITHIN_TARGET
    assert ap.activation_target_warnings(rows) == []


def test_janela_ausente_cai_para_7_dias_como_antes():
    rows = ap.project_activation(_inputs(window_days=None, targets={2: METAS[2]}))
    assert rows[0].window_days == 7
    assert (rows[0].minimum, rows[0].maximum) == (12.0, 18.0)


def test_janela_maior_que_7_nao_escala_mais_a_meta():
    """M76 Lote 4a (E1): a meta e semanal e a janela do preview ja e a semana;
    um `window_days` legado de 9 nao multiplica a meta por 9/7."""
    rows = ap.project_activation(_inputs(window_days=9, targets={2: METAS[2]}))
    assert (rows[0].minimum, rows[0].maximum) == (12.0, 18.0)
    assert rows[0].window_days == 9


@pytest.mark.parametrize("total", [0.0, 11.99, 12.0, 14.25, 15.0, 15.75, 18.0, 18.01, 40.0])
def test_status_sai_de_classify_weekly_volume_status(total):
    """E11: a regua e a de `classify_weekly_volume_status` (tolerancia +-5% no
    alvo); so os extremos viram warning."""
    from app.intelligence.volume_service import classify_weekly_volume_status

    dias = ({"groups": [{"muscle_group": "Peitoral", "total_equivalent": total}]},)
    [row] = ap.project_activation(_inputs(days=dias, targets={2: METAS[2]}))
    esperado = {"BELOW_MIN": ap.PROJECTION_BELOW_MIN, "ABOVE_MAX": ap.PROJECTION_ABOVE_MAX}.get(
        classify_weekly_volume_status(total, 12.0, 15.0, 18.0), ap.PROJECTION_WITHIN_TARGET
    )
    assert row.status == esperado


def test_sum_projected_by_group_soma_na_ordem_dos_dias():
    assert ap.sum_projected_by_group(DIAS) == {"Peitoral": 11.25, "Bíceps": 10.5, "Tríceps": 2.0}
    assert ap.sum_projected_by_group([]) == {}


# ---------------------------------------------------------------------------
# 2. Oraculo: payload identico ao da logica antiga
# ---------------------------------------------------------------------------


def test_warnings_do_modo_b_sao_identicos_ao_literal_da_logica_antiga():
    novos = ap.activation_target_warnings(ap.project_activation(_inputs()))
    assert diferencas(WARNINGS_ANTIGOS, novos, tolerancia=0.0) == []
    assert _bytes(novos) == _bytes(WARNINGS_ANTIGOS)
    # Tipos: float onde era float, int onde era int.
    for w in novos:
        assert type(w["projected"]) is float
        assert type(w["window_days"]) is int
        assert type(w["muscle_group_id"]) is int


@pytest.mark.parametrize(
    "days, window_days, targets, names",
    [
        (DIAS, JANELA, METAS, NOMES),
        (DIAS, None, METAS, NOMES),
        (DIAS, 7, {2: {"min": 0.0, "max": 5.0}}, NOMES),
        ((), 7, METAS, NOMES),
        (({"groups": [{"muscle_group": "Peitoral", "total_equivalent": "2.5"}]},), 7, METAS, NOMES),
        (DIAS, 7, {7: {"min": 1.0, "max": 2.0}}, {}),
    ],
)
def test_warnings_do_modo_b_batem_com_a_copia_congelada_da_logica_antiga(days, window_days, targets, names):
    preview = {"days": list(days), "window_days": window_days}
    antigos = _logica_antiga(preview, targets, names)
    novos = ap.activation_target_warnings(ap.project_activation(_inputs(days, window_days, targets, names)))
    assert _bytes(novos) == _bytes(antigos)


def test_coverage_warnings_pula_balanced_e_preserva_as_chaves():
    entradas = [
        {"muscle_group_id": 2, "muscle_group_name": "Peitoral", "sum_pct_min": 60, "sum_pct_max": 80,
         "status": "UNDER_ALLOCATED", "extra": "ignorado"},
        {"muscle_group_id": 5, "muscle_group_name": "Costas", "sum_pct_min": 95, "sum_pct_max": 105,
         "status": "BALANCED"},
        {"muscle_group_id": 9, "muscle_group_name": "Bíceps", "sum_pct_min": 120, "sum_pct_max": 140,
         "status": "OVER_ALLOCATED"},
    ]
    esperado = [
        {"code": "ACTIVATION_COVERAGE", "muscle_group_id": 2, "muscle_group_name": "Peitoral",
         "sum_pct_min": 60, "sum_pct_max": 80, "status": "UNDER_ALLOCATED"},
        {"code": "ACTIVATION_COVERAGE", "muscle_group_id": 9, "muscle_group_name": "Bíceps",
         "sum_pct_min": 120, "sum_pct_max": 140, "status": "OVER_ALLOCATED"},
    ]
    assert _bytes(ap.coverage_warnings(entradas)) == _bytes(esperado)
    assert ap.coverage_warnings([]) == []


# ---------------------------------------------------------------------------
# 3. `_check_activation` ligada a funcao pura (leituras substituidas)
# ---------------------------------------------------------------------------


def _template(volume_mode: str) -> WorkoutPlanTemplateIn:
    kind = "equipment" if volume_mode == "equipment_series" else "muscle_group"
    return WorkoutPlanTemplateIn(
        name="Modelo PR D",
        volume_mode=volume_mode,
        slots=[{"sequence": 1, "name": "A", "items": [
            {"item_order": 1, "item_kind": kind, "muscle_group_id": 2, "mapping_id": 100, "sets_count": 3}
        ]}],
    )


def _pedido(volume_mode: str) -> PeriodizationDraftIn:
    return PeriodizationDraftIn(training_type_id=77, volume_mode=volume_mode, days_per_cycle=1)


def test_check_activation_modo_b_emite_o_payload_da_funcao_pura(monkeypatch):
    from app import training_types, workout_plan_templates as wpt

    monkeypatch.setattr(training_types, "get_training_type_muscle_group_targets", lambda tt_id: dict(METAS))
    chamadas: dict[str, object] = {}

    def fake_preview(payload):
        chamadas["preview"] = payload
        return {"cycle_length": 9, "window_days": JANELA, "days": list(DIAS)}

    monkeypatch.setattr(wpt, "preview_activation", fake_preview)
    monkeypatch.setattr(wpt, "_load_muscle_group_names", lambda ids: {i: NOMES[i] for i in ids if i in NOMES})

    report = validation.ValidationReport()
    validation._check_activation(_template("equipment_series"), _pedido("equipment_series"), report)

    assert _bytes(report.to_dict()["warnings"]) == _bytes(WARNINGS_ANTIGOS)
    assert report.errors == [] and report.auto_fixes == []
    assert chamadas["preview"].volume_mode == "equipment_series"


def test_check_activation_modo_a_usa_coverage_warnings(monkeypatch):
    from app import training_types
    from app.intelligence import template_volume_planner

    monkeypatch.setattr(training_types, "get_training_type_muscle_group_targets", lambda tt_id: dict(METAS))
    monkeypatch.setattr(
        template_volume_planner,
        "validate_template_volume_coverage",
        lambda template: [
            {"muscle_group_id": 2, "muscle_group_name": "Peitoral", "sum_pct_min": 60, "sum_pct_max": 80,
             "status": "UNDER_ALLOCATED"},
            {"muscle_group_id": 5, "muscle_group_name": "Costas", "sum_pct_min": 95, "sum_pct_max": 105,
             "status": "BALANCED"},
        ],
    )

    report = validation.ValidationReport()
    validation._check_activation(_template("percent_budget"), _pedido("percent_budget"), report)

    assert report.to_dict()["warnings"] == [
        {"code": "ACTIVATION_COVERAGE", "muscle_group_id": 2, "muscle_group_name": "Peitoral",
         "sum_pct_min": 60, "sum_pct_max": 80, "status": "UNDER_ALLOCATED"},
    ]


def test_check_activation_sem_perfil_ou_sem_metas_nao_avisa(monkeypatch):
    from app import training_types

    report = validation.ValidationReport()
    validation._check_activation(
        _template("equipment_series"), PeriodizationDraftIn(volume_mode="equipment_series", days_per_cycle=1), report
    )
    assert report.warnings == []

    monkeypatch.setattr(training_types, "get_training_type_muscle_group_targets", lambda tt_id: {})
    validation._check_activation(_template("equipment_series"), _pedido("equipment_series"), report)
    assert report.warnings == []


def test_falha_de_leitura_continua_virando_warning_unavailable(monkeypatch):
    from app import training_types, workout_plan_templates as wpt

    monkeypatch.setattr(training_types, "get_training_type_muscle_group_targets", lambda tt_id: dict(METAS))

    def estoura(payload):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(wpt, "preview_activation", estoura)
    report = validation.ValidationReport()
    validation._check_activation(_template("equipment_series"), _pedido("equipment_series"), report)
    assert report.warnings == [{"code": "ACTIVATION_COVERAGE", "status": "UNAVAILABLE", "message": "banco fora"}]


# ---------------------------------------------------------------------------
# 4. Reexport dos codigos
# ---------------------------------------------------------------------------


def test_codigos_de_ativacao_continuam_importaveis_de_validation():
    assert validation.ACTIVATION_BELOW_MIN == ap.ACTIVATION_BELOW_MIN == "ACTIVATION_BELOW_MIN"
    assert validation.ACTIVATION_ABOVE_MAX == ap.ACTIVATION_ABOVE_MAX == "ACTIVATION_ABOVE_MAX"
    assert validation.ACTIVATION_COVERAGE == ap.ACTIVATION_COVERAGE == "ACTIVATION_COVERAGE"
