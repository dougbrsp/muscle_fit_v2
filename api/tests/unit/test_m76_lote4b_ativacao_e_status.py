"""M76 Lote 4b — ativação com técnica e status do backend nas telas.

- E3: payload ÚNICO de ativação (`technique_volume.activation_payload`) e o
  breakdown por exercício somando o equivalente da técnica (Fase 5).
- E4: `template_slot_targets` do Modo B em séries EQUIVALENTES
  (`mode_b.planned_slot_targets`) e o status da tabela de
  `/registro-exercicios` calculado no backend
  (`volume_service.assess_activation_against_target`).
- E6: metodologia do REALIZADO (`methodology_realized.evaluate_realized_window`).
  O resultado do cenário fica em `frontend/src/lib/methodology-realized.fixture.json`,
  que o vitest da tabela de 7 dias desenha. Mudou a regra no dono? Regenere:

    cd api && python tests/unit/test_m76_lote4b_ativacao_e_status.py --gravar
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parents[3]
if __name__ == "__main__":  # execução direta (--gravar): `app` importável sem pytest
    sys.path.insert(0, str(RAIZ / "api"))

from app.generation.mode_b import planned_slot_targets  # noqa: E402
from app.intelligence.methodology_realized import METRIC_VERSION, evaluate_realized_window  # noqa: E402
from app.intelligence.methodology_volume import (  # noqa: E402
    MethodologyGroupInput,
    MethodologyRange,
    RangeSource,
    VolumeClassSpec,
)
from app.intelligence.technique_volume import activation_payload  # noqa: E402
from app.intelligence.volume_service import (  # noqa: E402
    DASHBOARD_FALLBACK_MAX_SETS,
    DASHBOARD_FALLBACK_MIN_SETS,
    DASHBOARD_FALLBACK_TARGET_SETS,
    VolumeService,
    assess_activation_against_target,
    assess_activation_rows,
    classify_planned_volume_status,
    classify_weekly_volume_status,
)
from app.models import MuscleActivationExerciseIn  # noqa: E402

FIXTURE = RAIZ / "frontend" / "src" / "lib" / "methodology-realized.fixture.json"

CATALOGO = {
    "rest_pause": {"activation_set_equivalent": 0.4, "max_equivalent_per_anchor": 1.0},
    "fst7": {"activation_set_equivalent": 0.0, "max_equivalent_per_anchor": 0.0, "default_sets_count": 7},
}


# ---------------------------------------------------------------------------
# E3 — payload único e breakdown com técnica
# ---------------------------------------------------------------------------


def test_payload_unico_tem_o_shape_do_item_do_modelo():
    linha = {"mapping_id": 10, "sets_count": 4, "advanced_technique": "rest_pause",
             "advanced_technique_applications": 2, "reps_target": "8-10"}
    assert activation_payload(linha) == {
        "mapping_id": 10,
        "sets_count": 4,
        "advanced_technique": "rest_pause",
        "advanced_technique_applications": 2,
        "is_laboral": None,
    }


def test_item_laboral_nao_ativa_nada_m100():
    """M100: exercicio laboral fica fora da ativacao planejada."""
    from app.intelligence.technique_volume import planned_item_sets

    assert planned_item_sets({"mapping_id": 10, "sets_count": 3, "is_laboral": True}, None) == 0.0
    assert planned_item_sets({"mapping_id": 10, "sets_count": 3, "is_laboral": 0}, None) == 3.0
    assert planned_item_sets(MuscleActivationExerciseIn(mapping_id=10, sets_count=3, is_laboral=True), None) == 0.0


def test_entrada_da_rota_aceita_a_tecnica():
    item = MuscleActivationExerciseIn(mapping_id=10, sets_count=4, advanced_technique="rest_pause",
                                      advanced_technique_applications=2)
    assert item.model_dump() == activation_payload(item)


def _contribuicoes(monkeypatch):
    import app.intelligence.muscle_contribution_resolver as resolver

    monkeypatch.setattr(
        resolver,
        "_resolve_muscle_contributions_for_mappings",
        lambda ids: {
            10: [
                {"muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0},
                {"muscle_group_name": "Tríceps", "role": "SECONDARY", "stimulus_weight": 0.5},
            ],
            11: [{"muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0}],
        },
    )


def test_breakdown_soma_o_equivalente_da_tecnica(monkeypatch):
    _contribuicoes(monkeypatch)
    linhas = VolumeService().get_muscle_activation_breakdown_by_exercise(
        [
            # rest-pause x2 em 4 séries: 4 + 2 x 0,4 = 4,8.
            {"mapping_id": 10, "sets_count": 4, "advanced_technique": "rest_pause", "advanced_technique_applications": 2},
            {"mapping_id": 11, "sets_count": 3},
            # FST-7: só séries (equivalente 0).
            {"mapping_id": 11, "sets_count": 7, "advanced_technique": "fst7"},
            {"mapping_id": None, "sets_count": 3},
        ],
        catalog=CATALOGO,
    )
    assert linhas[0]["primary_activation"] == pytest.approx(4.8)
    assert linhas[0]["secondary"] == [{"muscle_group_name": "Tríceps", "activation": pytest.approx(2.4)}]
    assert linhas[1]["primary_activation"] == pytest.approx(3.0)
    assert linhas[2]["primary_activation"] == pytest.approx(7.0)
    assert linhas[3] == {"mapping_id": None, "primary_activation": 0.0, "secondary": [], "stabilizer": []}


def test_breakdown_e_preview_dao_o_mesmo_numero(monkeypatch):
    _contribuicoes(monkeypatch)
    payload = [{"mapping_id": 10, "sets_count": 4, "advanced_technique": "rest_pause", "advanced_technique_applications": 2}]
    service = VolumeService()
    [linha] = service.get_muscle_activation_breakdown_by_exercise(payload, catalog=CATALOGO)
    por_grupo = {r["muscle_group"]: r for r in service.get_muscle_activation_for_exercises(payload, catalog=CATALOGO)}
    assert por_grupo["Peitoral"]["primary_equivalent"] == pytest.approx(linha["primary_activation"])


# ---------------------------------------------------------------------------
# E4 — alvo do slot em séries equivalentes e status no backend
# ---------------------------------------------------------------------------


def test_alvo_do_slot_do_modo_b_e_a_ativacao_planejada():
    ativacao = {
        "Peitoral": {"total_equivalent": 6.8},  # 4,8 do item com técnica + 2 de outro
        "Tríceps": {"total_equivalent": 2.4},
    }
    alvos = planned_slot_targets({14, 52}, {52: "Peitoral", 14: "Tríceps"}, ativacao)
    assert alvos == [
        {"muscle_group_id": 14, "muscle_group_name": "Tríceps", "sets_count": 2.4, "effective_max_sets": None},
        {"muscle_group_id": 52, "muscle_group_name": "Peitoral", "sets_count": 6.8, "effective_max_sets": None},
    ]


def test_grupo_do_slot_sem_ativacao_tem_alvo_zero():
    assert planned_slot_targets({1}, {1: "Abdômen"}, {})[0]["sets_count"] == 0.0


def test_sem_alvo_usa_a_meta_generica_com_classify_weekly():
    for actual in (3.0, 7.0, 9.0, 12.0, 30.0):
        avaliacao = assess_activation_against_target(actual, None)
        assert avaliacao["status"] == classify_weekly_volume_status(
            actual, DASHBOARD_FALLBACK_MIN_SETS, DASHBOARD_FALLBACK_TARGET_SETS, DASHBOARD_FALLBACK_MAX_SETS
        )
        assert avaliacao["status_domain"] == "weeklyVolume"
        assert avaliacao["target_source"] == "generic_fallback"
        assert avaliacao["target_sets"] is None


def test_alvo_do_slot_usa_os_estados_do_planejado():
    alvo = {"sets_count": 6.0, "max_weekly_sets": None, "target_source": "template_slot"}
    for actual in (2.0, 5.8, 6.2, 9.0):
        avaliacao = assess_activation_against_target(actual, alvo)
        assert avaliacao["status"] == classify_planned_volume_status(actual, 6.0)
        assert avaliacao["status_domain"] == "plannedVolume"
        assert avaliacao["target_sets"] == 6.0


def test_alvo_do_slot_com_teto_passado_e_acima_do_maximo():
    alvo = {"sets_count": 6.5, "max_weekly_sets": 7.0, "target_source": "template_slot"}
    assert assess_activation_against_target(7.5, alvo)["status"] == "ABOVE_MAX"
    assert assess_activation_against_target(7.5, alvo)["status_domain"] == "weeklyVolume"
    assert assess_activation_against_target(6.6, alvo)["status"] == "ON_TARGET"


def test_teto_zero_do_modo_b_nao_e_teto():
    alvo = {"sets_count": 4.0, "max_weekly_sets": 0.0, "target_source": "template_slot"}
    avaliacao = assess_activation_against_target(9.0, alvo)
    assert avaliacao["status"] == "ABOVE_TARGET"
    assert avaliacao["max_sets"] is None


def test_meta_do_tipo_de_treino_usa_os_5_estados():
    alvo = {"sets_count": 12.0, "min_weekly_sets": 8.0, "max_weekly_sets": 18.0, "target_source": "training_type"}
    for actual in (5.0, 10.0, 12.0, 15.0, 20.0):
        assert assess_activation_against_target(actual, alvo)["status"] == classify_weekly_volume_status(
            actual, 8.0, 12.0, 18.0
        )


def test_linhas_casam_o_alvo_pelo_nome_do_grupo():
    linhas = assess_activation_rows(
        [{"muscle_group": "Peitoral", "total_equivalent": 6.0}, {"muscle_group": "Bíceps", "total_equivalent": 2.0}],
        [{"muscle_group_name": "Peitoral", "sets_count": 6.0, "target_source": "template_slot"}],
    )
    assert linhas[0]["assessment"]["target_source"] == "template_slot"
    assert linhas[0]["assessment"]["status"] == "ON_TARGET"
    assert linhas[1]["assessment"]["target_source"] == "generic_fallback"
    assert linhas[1]["assessment"]["status"] == "BELOW_MIN"


# ---------------------------------------------------------------------------
# E6 — metodologia do realizado
# ---------------------------------------------------------------------------

DAYS = ["2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]


def _faixa(low: float, high: float) -> MethodologyRange:
    return MethodologyRange(sets_min=low, sets_max=high, sets_source=RangeSource.ADMIN)


GRANDES = VolumeClassSpec(
    id=1,
    name="Grandes",
    ranges={"1x": _faixa(8, 12), "2x": _faixa(5, 8), "3x_plus": _faixa(3, 5)},
    weekly_sets_min=10,
    weekly_sets_max=20,
)
# Pequenos: sem faixa para 2x, com meta semanal.
PEQUENOS = VolumeClassSpec(id=2, name="Pequenos", ranges={"1x": _faixa(8, 12)}, weekly_sets_min=6, weekly_sets_max=12)

GROUPS = (
    MethodologyGroupInput(id=1, name="Peitoral", volume_class_id=1),
    MethodologyGroupInput(id=2, name="Costas", volume_class_id=1),
    MethodologyGroupInput(id=3, name="Quadríceps", volume_class_id=1),
    MethodologyGroupInput(id=4, name="Bíceps", volume_class_id=2),
    MethodologyGroupInput(id=5, name="Antebraço"),
    # Fatia de Costas e NETO (fatia da fatia): os dois somam em Costas.
    MethodologyGroupInput(id=6, name="Dorsal alto", methodology_parent_group_id=2),
    MethodologyGroupInput(id=7, name="Romboides", methodology_parent_group_id=6),
)


def _act(day: int, group: str, direct: float, sets: float | None = None, gid: int | None = None) -> dict[str, Any]:
    return {"local_date": DAYS[day], "muscle_group": group, "muscle_group_id": gid,
            "sets_count": direct if sets is None else sets, "direct_sets": direct}


ITEMS = [
    # Peitoral 3 dias (3x+: 3-5): 4 dentro, 2 abaixo, 6 acima; média 4 dentro; semana 12 dentro de 10-20.
    _act(0, "Peitoral", 4, 4, 1),
    _act(2, "Peitoral", 2, 3, 1),
    _act(4, "Peitoral", 6, 6, 1),
    # Costas 1 dia (1x: 8-12) + fatia + neto no mesmo dia: 5 + 3 + 2 = 10 dentro.
    _act(1, "Costas", 5),
    _act(1, "Dorsal alto", 3, 3, 6),
    _act(1, "Romboides", 2, 2, 7),
    # Bíceps 2 dias: Pequenos sem faixa 2x -> sem cor; semana 5 < 6 insuficiente.
    _act(3, "Bíceps", 3, 3, 4),
    _act(5, "Bíceps", 2, 2, 4),
    # Antebraço sem macro-grupo.
    _act(6, "Antebraço", 3, 3, 5),
    # Só indireto: direct 0, equivalente 2.
    _act(6, "Peitoral", 0, 2, 1),
    # Fora da janela: ignorado.
    {"local_date": "2026-09-20", "muscle_group": "Peitoral", "muscle_group_id": 1, "sets_count": 50, "direct_sets": 50},
]


def resultado() -> dict[str, Any]:
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote4b_ativacao_e_status.py (--gravar): resposta de "
            "GET /analytics/methodology-realized para o cenário do teste "
            "(api/app/intelligence/methodology_realized.py). O vitest da tabela de 7 dias desenha "
            "este payload. Não edite à mão."
        ),
        "metric_version": METRIC_VERSION,
        "counting_mode": "primary_only",
        "days": DAYS,
        "items": evaluate_realized_window(ITEMS, DAYS, GROUPS, {1: GRANDES, 2: PEQUENOS}),
    }


def _por_nome() -> dict[str, dict[str, Any]]:
    return {row["muscle_group"]: row for row in resultado()["items"]}


def test_3x_usa_a_faixa_por_treino_e_a_media_e_a_leitura_da_metodologia():
    peitoral = _por_nome()["Peitoral"]
    assert peitoral["frequency_bucket"] == "3x_plus"
    assert [d["status"] for d in peitoral["days"]] == [
        "WITHIN_RANGE", None, "BELOW_RANGE", None, "ABOVE_RANGE", None, None
    ]
    assert peitoral["direct_sets_per_session"] == 4.0
    assert peitoral["session_status"] == "WITHIN_RANGE"
    assert peitoral["weekly_total"] == 12.0
    assert peitoral["weekly_status"] == "WITHIN_RANGE"


def test_fatia_e_neto_somam_no_bloco_pela_cadeia_inteira():
    linhas = _por_nome()
    assert "Dorsal alto" not in linhas and "Romboides" not in linhas
    costas = linhas["Costas"]
    assert costas["frequency_bucket"] == "1x"
    assert costas["days"][1]["direct_sets"] == 10.0
    assert costas["days"][1]["status"] == "WITHIN_RANGE"
    assert {m["muscle_group_id"] for m in costas["member_groups"]} == {2, 6, 7}


def test_frequencia_sem_faixa_nao_avalia_o_dia_mas_avalia_a_semana():
    biceps = _por_nome()["Bíceps"]
    assert biceps["frequency_bucket"] == "2x"
    assert biceps["session_range"] is None
    assert biceps["days"][3]["status"] == "NOT_EVALUABLE"
    assert biceps["session_status"] == "NOT_EVALUABLE"
    assert biceps["weekly_status"] == "BELOW_RANGE"


def test_grupo_sem_macro_grupo_nao_e_avaliado():
    antebraco = _por_nome()["Antebraço"]
    assert antebraco["volume_class_name"] is None
    assert antebraco["days"][6]["status"] == "NOT_EVALUABLE"
    assert antebraco["weekly_status"] == "NOT_EVALUABLE"


def test_grupo_com_meta_semanal_sem_treino_e_insuficiente():
    quadriceps = _por_nome()["Quadríceps"]
    assert quadriceps["days_trained"] == 0
    assert quadriceps["weekly_total"] == 0.0
    assert quadriceps["weekly_status"] == "BELOW_RANGE"
    assert quadriceps["session_status"] == "NOT_DIRECTLY_TRAINED"


def test_dia_so_indireto_e_neutro_e_nao_conta_na_frequencia():
    peitoral = _por_nome()["Peitoral"]
    assert peitoral["days"][6] == {"date": DAYS[6], "direct_sets": 0.0, "effective_sets": 2.0, "status": None}
    assert peitoral["days_trained"] == 3


def _ler(caminho: Path) -> Any:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _gravar(caminho: Path, dados: Any) -> None:
    with caminho.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(json.dumps(dados, ensure_ascii=False, indent=2) + "\n")


def test_fixture_do_frontend_e_a_saida_do_dono():
    assert _ler(FIXTURE) == resultado(), (
        f"{FIXTURE.name} divergiu do dono: rode `python tests/unit/test_m76_lote4b_ativacao_e_status.py --gravar`"
    )


if __name__ == "__main__":
    if "--gravar" in sys.argv:
        _gravar(FIXTURE, resultado())
        print(f"gravado: {FIXTURE}")
