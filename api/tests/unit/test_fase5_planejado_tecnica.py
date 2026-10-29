"""Fase 5, PR 5 - volume PLANEJADO do Modelo com as técnicas avançadas.

Uma regra só (`technique_accounting.planned_equivalent`): aplicações ×
`activation_set_equivalent`, limitada por `max_equivalent_per_anchor`; FST-7
e GVT valem 0 (só preenchem o número de séries). Este arquivo prova, sem
banco, que TODOS os consumidores do planejado usam essa regra:

- `planned_work_sets` / `planned_item_sets` (tabela: mesmos exemplos do
  realizado - rest-pause ×2 -> 0,8; teto; FST-7/GVT -> 0);
- preview de ativação do builder (dia e linha por item, M11);
- metodologia EXG (`direct_sets = sets_count + equiv.`), inclusive a mudança
  de status BELOW -> WITHIN que o equivalente provoca;
- Novo Dashboard (meta do Modelo `equipment_series`), numa consulta só;
- FST-7/GVT: a API zera aplicações/pausa e o validador da IA registra o mesmo
  conserto como auto-fix.
"""

from __future__ import annotations

import pytest

from app import advanced_techniques
from app import workout_plan_templates as wpt
from app.domain.technique_accounting import is_sets_only_technique, planned_equivalent, planned_work_sets
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.intelligence import technique_volume
from app.intelligence.dashboard import muscle_summary_service as mss
from app.intelligence.volume_service import VolumeService
from app.models import WorkoutPlanTemplateIn, WorkoutPlanTemplateSlotIn
from app.periodization import validation
from tests.unit.test_methodology_volume import BICEPS, GROUPS, PRIMARY, SEED, block

# Catálogo da 060 (valores do seed), no formato de `GET /advanced-techniques`.
CATALOGO = {
    "rest_pause": {"activation_set_equivalent": 0.40, "max_equivalent_per_anchor": 1.00, "default_sets_count": None},
    "drop_set": {"activation_set_equivalent": 0.50, "max_equivalent_per_anchor": 1.00, "default_sets_count": None},
    "forced_reps": {"activation_set_equivalent": 0.25, "max_equivalent_per_anchor": 0.50, "default_sets_count": None},
    "partial_reps": {"activation_set_equivalent": 0.15, "max_equivalent_per_anchor": 0.50, "default_sets_count": None},
    "fst7": {"activation_set_equivalent": 0.00, "max_equivalent_per_anchor": 0.00, "default_sets_count": 7},
    "gvt": {"activation_set_equivalent": 0.00, "max_equivalent_per_anchor": 0.00, "default_sets_count": 10},
}


@pytest.fixture
def catalogo(monkeypatch):
    """O catálogo preguiçoso de cada requisição lê ESTE dicionário; conta as
    leituras para provar que item sem técnica não lê nada."""
    leituras: list[int] = []

    def load():
        leituras.append(1)
        return CATALOGO

    monkeypatch.setattr(technique_volume, "load_catalog", load)
    return leituras


# ---------------------------------------------------------------------------
# 1) Regra: tabela (os mesmos exemplos do realizado)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sets,code,apps,esperado",
    [
        (4, "rest_pause", 2, 4.8),  # 2 × 0,4
        (4, "rest_pause", 3, 5.0),  # 1,2 -> teto 1,0
        (4, "drop_set", 1, 4.5),
        (4, "drop_set", 3, 5.0),  # 1,5 -> teto 1,0
        (3, "forced_reps", 4, 3.5),  # 1,0 -> teto 0,5
        (3, "partial_reps", 2, 3.3),
        (7, "fst7", 3, 7.0),  # só séries: equivalente 0
        (10, "gvt", 1, 10.0),
        (4, None, 2, 4.0),  # sem técnica
        (4, "rest_pause", None, 4.0),  # sem aplicações
        (4, "desconhecida", 2, 4.0),  # fora do catálogo vale 0
        (0, "rest_pause", 2, 0.0),  # técnica sem série não é trabalho
        (None, "rest_pause", 2, 0.0),
    ],
)
def test_series_planejadas_do_item(sets, code, apps, esperado):
    assert planned_work_sets(sets, code, apps, CATALOGO) == pytest.approx(esperado)
    item = {"sets_count": sets, "advanced_technique": code, "advanced_technique_applications": apps}
    assert technique_volume.planned_item_sets(item, CATALOGO) == pytest.approx(esperado)
    parcela = technique_volume.planned_item_equivalent(item, CATALOGO)
    assert parcela == pytest.approx(esperado - (sets or 0) if esperado else 0.0)


def test_equivalente_planejado_e_a_mesma_funcao_do_dominio():
    assert planned_equivalent("rest_pause", 2, CATALOGO) == pytest.approx(0.8)
    assert planned_equivalent("fst7", 5, CATALOGO) == 0.0


@pytest.mark.parametrize(
    "code,catalog,esperado",
    [
        ("fst7", None, True),
        ("gvt", None, True),
        ("rest_pause", CATALOGO, False),
        ("drop_set", CATALOGO, False),
        (None, CATALOGO, False),
        # Técnica nova cadastrada com `default_sets_count`: vale o catálogo.
        ("nova_so_series", {"nova_so_series": {"default_sets_count": 5}}, True),
    ],
)
def test_tecnica_so_de_series(code, catalog, esperado):
    assert is_sets_only_technique(code, catalog) is esperado


# ---------------------------------------------------------------------------
# 2) Preview de ativação do builder
# ---------------------------------------------------------------------------


def _slot(*items: dict, seq: int = 1) -> WorkoutPlanTemplateSlotIn:
    return WorkoutPlanTemplateSlotIn(
        sequence=seq,
        name=f"Dia {seq}",
        slot_type="normal",
        items=[{"item_order": i, "item_kind": "equipment", **item} for i, item in enumerate(items, start=1)],
    )


def test_preview_do_dia_soma_o_equivalente_vezes_o_peso(monkeypatch, catalogo):
    recebido: list[list[dict]] = []

    def fake(self, exercises):
        recebido.append(exercises)
        return []

    monkeypatch.setattr(VolumeService, "get_muscle_activation_for_exercises", fake)
    slot = _slot(
        {"mapping_id": 10, "equipment_id": 1, "sets_count": 4, "advanced_technique": "rest_pause",
         "advanced_technique_applications": 2},
        {"mapping_id": 11, "equipment_id": 1, "sets_count": 7, "advanced_technique": "fst7"},
        {"mapping_id": 12, "equipment_id": 1, "sets_count": 3},
    )
    wpt._compute_slot_activation_equipment_series(slot)
    assert [(e["mapping_id"], e["sets_count"]) for e in recebido[0]] == [
        (10, pytest.approx(4.8)),
        (11, 7.0),
        (12, 3.0),
    ]
    assert len(catalogo) == 1  # um catálogo por chamada, lido uma vez


def test_preview_sem_tecnica_nao_le_o_catalogo(monkeypatch, catalogo):
    monkeypatch.setattr(VolumeService, "get_muscle_activation_for_exercises", lambda self, exercises: [])
    wpt._compute_slot_activation_equipment_series(_slot({"mapping_id": 12, "equipment_id": 1, "sets_count": 3}))
    assert catalogo == []


def test_volume_service_soma_o_equivalente_vezes_stimulus_weight(monkeypatch, catalogo):
    """Motor real (`_build_muscle_activation_rows`), contribuições em memória:
    rest-pause ×2 em 4 séries = 4,8 × 1,0 no primário e × 0,5 no secundário."""
    import app.intelligence.muscle_contribution_resolver as resolver

    monkeypatch.setattr(
        resolver,
        "_resolve_muscle_contributions_for_mappings",
        lambda ids: {
            10: [
                {"muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0},
                {"muscle_group_name": "Tríceps", "role": "SECONDARY", "stimulus_weight": 0.5},
            ]
        },
    )
    rows = VolumeService().get_muscle_activation_for_exercises(
        [{"mapping_id": 10, "sets_count": 4, "advanced_technique": "rest_pause", "advanced_technique_applications": 2}]
    )
    por_grupo = {row["muscle_group"]: row for row in rows}
    assert por_grupo["Peitoral"]["primary_equivalent"] == pytest.approx(4.8)
    assert por_grupo["Tríceps"]["secondary_equivalent"] == pytest.approx(2.4)


def test_linha_por_item_usa_as_mesmas_series_do_dia(monkeypatch, catalogo):
    monkeypatch.setattr(
        wpt,
        "_load_preview_contributions",
        lambda ids: {10: [{"muscle_group_name": "Peitoral", "muscle_group_id": 1, "role": "PRIMARY", "stimulus_weight": 1.0}]},
    )
    slot = _slot(
        {"mapping_id": 10, "equipment_id": 1, "sets_count": 4, "advanced_technique": "drop_set",
         "advanced_technique_applications": 1}
    )
    days = [{"slot_sequence": 1, "groups": [{"muscle_group": "Peitoral", "total_equivalent": 4.5}]}]
    out = wpt._compute_item_breakdown([slot], days, "equipment_series", {})
    assert out["items"][0]["contributions"][0]["value"] == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# 3) Metodologia EXG: direct_sets = sets_count + equivalente
# ---------------------------------------------------------------------------

ROSCA_B = 120
PRIMARY_COM_ROSCA_B = {**PRIMARY, ROSCA_B: {BICEPS}}


def _item(mapping_id: int, sets: int, equiv: float = 0.0) -> mv.MethodologyItemInput:
    return mv.MethodologyItemInput(mapping_id=mapping_id, canonical_exercise_id=mapping_id, sets_count=sets,
                                   technique_equivalent=equiv)


def _ciclo_biceps_2x(equiv: float) -> list[mv.MethodologySlotInput]:
    """Bíceps 2x (Pequenos 2x: 2-3 exercícios, 6-12 séries por sessão; semana
    12-24): 3 + 2 séries por sessão = 5 -> abaixo; com +1,0 de técnica = 6."""
    sessao = (_item(104, 3, equiv), _item(ROSCA_B, 2))
    slots = [
        mv.MethodologySlotInput(sequence=1, slot_type="normal", name="A", items=sessao),
        mv.MethodologySlotInput(sequence=2, slot_type="descanso", name="D"),
        mv.MethodologySlotInput(sequence=3, slot_type="normal", name="B", items=sessao),
    ]
    slots += [mv.MethodologySlotInput(sequence=s, slot_type="descanso", name="D") for s in range(4, 8)]
    return slots


def _avaliar(slots):
    return mv.evaluate_methodology_volume(slots, PRIMARY_COM_ROSCA_B, GROUPS, "equipment_series", None, SEED)


def test_equivalente_muda_o_status_de_abaixo_para_dentro():
    sem = block(_avaliar(_ciclo_biceps_2x(0.0)), BICEPS)
    com = block(_avaliar(_ciclo_biceps_2x(1.0)), BICEPS)

    assert sem.direct_sets_per_session == 5 and sem.status is mv.MethodologyVolumeStatus.BELOW_RANGE
    assert com.direct_sets_per_session == 6 and com.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert com.weekly_sets_axis.value_weekly == 12
    # A parcela da técnica sai separada (a tela escreve "5 séries + 1 equiv.").
    assert com.direct_sets_cycle_total == 12 and com.technique_equivalent_cycle_total == 1 * 2
    assert [(s.direct_sets, s.technique_equivalent) for s in com.sessions] == [(6, 1), (6, 1)]
    assert sem.technique_equivalent_cycle_total == 0


def test_series_fracionarias_ficam_com_duas_casas():
    r = block(_avaliar(_ciclo_biceps_2x(0.8)), BICEPS)
    assert r.direct_sets_per_session == pytest.approx(5.8)
    assert r.direct_sets_cycle_total == pytest.approx(11.6)
    assert r.technique_equivalent_cycle_total == pytest.approx(1.6)


def test_item_da_metodologia_calcula_o_equivalente_pelo_catalogo():
    slots = [
        {
            "sequence": 1,
            "slot_type": "normal",
            "name": "A",
            "items": [
                {"item_kind": "equipment", "mapping_id": 104, "sets_count": 4, "advanced_technique": "rest_pause",
                 "advanced_technique_applications": 2},
                {"item_kind": "equipment", "mapping_id": 106, "sets_count": 7, "advanced_technique": "fst7",
                 "advanced_technique_applications": 1},
                {"item_kind": "equipment", "mapping_id": 102, "sets_count": 3},
            ],
        }
    ]
    items = svc.build_slot_inputs(slots, "equipment_series", {}, CATALOGO)[0].items
    assert [i.technique_equivalent for i in items] == [pytest.approx(0.8), 0.0, 0.0]
    assert [i.sets_count for i in items] == [4, 7, 3]


def test_payload_da_metodologia_traz_a_parcela_da_tecnica():
    payload = svc.evaluation_to_payload(_avaliar(_ciclo_biceps_2x(0.8)))
    biceps = next(g for g in payload["groups"] if g["muscle_group_id"] == BICEPS)
    assert biceps["technique_equivalent_cycle_total"] == pytest.approx(1.6)
    assert biceps["sessions"][0]["technique_equivalent"] == pytest.approx(0.8)
    assert payload["metric_version"] == "methodology-volume-v5"


# ---------------------------------------------------------------------------
# 4) Novo Dashboard: meta do Modelo `equipment_series` com o equivalente
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self._conn.executed.append(sql)
        self._rows = self._conn.head if "FROM workout_plan_template_assignments" in sql else self._conn.activation

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, activation):
        self.head = [{"template_id": 7, "template_name": "B", "volume_mode": "equipment_series", "slots_count": 2}]
        self.activation = activation
        self.executed: list[str] = []

    def cursor(self):
        return _Cursor(self)


def test_meta_do_modelo_soma_equivalente_vezes_peso_numa_consulta_so(catalogo):
    # Peitoral: 8 séries sem técnica (peso 1) + 4 séries rest-pause ×2 com
    # peso 1 -> 8 + 4 + 0,8 = 12,8. Tríceps (secundário 0,5 do rest-pause):
    # 2 + 0,8 × 0,5 = 2,4. FST-7 não soma nada.
    activation = [
        {"muscle_group_id": 1, "advanced_technique": None, "advanced_technique_applications": None,
         "planned_sets": 8.0, "planned_weight": 2.0},
        {"muscle_group_id": 1, "advanced_technique": "rest_pause", "advanced_technique_applications": 2,
         "planned_sets": 4.0, "planned_weight": 1.0},
        {"muscle_group_id": 2, "advanced_technique": "rest_pause", "advanced_technique_applications": 2,
         "planned_sets": 2.0, "planned_weight": 0.5},
        {"muscle_group_id": 3, "advanced_technique": "fst7", "advanced_technique_applications": 1,
         "planned_sets": 7.0, "planned_weight": 1.0},
    ]
    conn = _Conn(activation)
    plan = mss.resolve_template_plan(conn, user_id=1)
    assert plan is not None
    assert plan.activation_by_group[1] == pytest.approx(12.8)
    assert plan.activation_by_group[2] == pytest.approx(2.4)
    assert plan.activation_by_group[3] == pytest.approx(7.0)
    # Cabeçalho + ativação: a técnica não acrescenta consulta ao banco.
    assert len(conn.executed) == 2
    assert "i.advanced_technique" in mss.TEMPLATE_PLAN_ACTIVATION_SQL


def test_meta_do_modelo_sem_tecnica_nao_le_o_catalogo(catalogo):
    conn = _Conn([{"muscle_group_id": 1, "planned_sets": 9.0, "planned_weight": 3.0}])
    plan = mss.resolve_template_plan(conn, user_id=1)
    assert plan.activation_by_group[1] == pytest.approx(9.0)
    assert catalogo == []


# ---------------------------------------------------------------------------
# 5) FST-7/GVT: aplicações e pausa zeradas (API e validador da IA)
# ---------------------------------------------------------------------------


def _template(*items: dict) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(
        name="M",
        volume_mode="equipment_series",
        slots=[
            {
                "sequence": 1,
                "name": "A",
                "slot_type": "normal",
                "items": [
                    {"item_order": i, "item_kind": "equipment", "muscle_group_id": BICEPS, "mapping_id": 100 + i,
                     "equipment_id": 1, **item}
                    for i, item in enumerate(items, start=1)
                ],
            }
        ],
    )


@pytest.mark.parametrize(
    "item,zera",
    [
        ({"sets_count": 7, "advanced_technique": "fst7", "advanced_technique_applications": 3,
          "advanced_technique_duration_seconds": 45}, True),
        ({"sets_count": 10, "advanced_technique": "gvt", "advanced_technique_duration_seconds": 60}, True),
        ({"sets_count": 10, "advanced_technique": "gvt", "advanced_technique_applications": 2}, True),
        ({"sets_count": 4, "advanced_technique": "rest_pause", "advanced_technique_applications": 2,
          "advanced_technique_duration_seconds": 15}, False),
        ({"sets_count": 7, "advanced_technique": "fst7"}, False),  # já zerado: nada a registrar
        ({"sets_count": 3, "advanced_technique_applications": 2}, False),  # sem técnica: campo ignorado
    ],
)
def test_api_zera_aplicacoes_e_pausa_de_fst7_gvt(item, zera, catalogo):
    template = _template(item)
    changes = wpt.clear_sets_only_technique_fields(template)
    saved = template.slots[0].items[0]
    if zera:
        assert changes == [
            {
                "slot": 1,
                "item": 1,
                "technique": item["advanced_technique"],
                "applications": item.get("advanced_technique_applications"),
                "duration_seconds": item.get("advanced_technique_duration_seconds"),
            }
        ]
        assert saved.advanced_technique_applications is None
        assert saved.advanced_technique_duration_seconds is None
        assert saved.sets_count == item["sets_count"]  # séries continuam editáveis
    else:
        assert changes == []
        assert saved.advanced_technique_applications == item.get("advanced_technique_applications")
        assert saved.advanced_technique_duration_seconds == item.get("advanced_technique_duration_seconds")
    # FST-7/GVT decidem pelo código; rest-pause consulta o catálogo (1 leitura).
    assert len(catalogo) == (1 if item.get("advanced_technique") == "rest_pause" else 0)


@pytest.mark.parametrize(
    "item,auto_fix",
    [
        ({"sets_count": 7, "advanced_technique": "fst7", "advanced_technique_applications": 3,
          "advanced_technique_duration_seconds": 45}, True),
        ({"sets_count": 10, "advanced_technique": "gvt", "advanced_technique_applications": 1}, True),
        ({"sets_count": 4, "advanced_technique": "rest_pause", "advanced_technique_applications": 2}, False),
    ],
)
def test_validador_da_ia_registra_o_mesmo_conserto(item, auto_fix, monkeypatch, catalogo):
    monkeypatch.setattr(advanced_techniques, "active_codes", lambda: set(CATALOGO))
    template = _template(item)
    report = validation.ValidationReport()
    validation._check_template_rules(template, report)
    fixes = [f for f in report.to_dict()["auto_fixes"] if f["code"] == validation.TECHNIQUE_FIELDS_CLEARED]
    if auto_fix:
        assert fixes == [
            {
                "code": validation.TECHNIQUE_FIELDS_CLEARED,
                "slot": 1,
                "item": 1,
                "technique": item["advanced_technique"],
                "applications": item.get("advanced_technique_applications"),
                "duration_seconds": item.get("advanced_technique_duration_seconds"),
            }
        ]
        assert template.slots[0].items[0].advanced_technique_applications is None
        assert template.slots[0].items[0].advanced_technique_duration_seconds is None
    else:
        assert fixes == []
        assert template.slots[0].items[0].advanced_technique_applications == 2
    assert report.to_dict()["errors"] == []


def test_validador_da_ia_avalia_a_metodologia_com_o_equivalente(monkeypatch, catalogo):
    """BELOW sem a técnica, sem aviso com ela (mesmo ciclo de bíceps 2x)."""
    monkeypatch.setattr(
        svc,
        "load_methodology_inputs",
        lambda ids, mode=None: svc.MethodologyInputs(
            canonical_ids={m: m for m in ids},
            contributions={m: PRIMARY_COM_ROSCA_B[m] for m in ids if m in PRIMARY_COM_ROSCA_B},
            groups=GROUPS,
            volume_classes=SEED,
        ),
    )

    def rascunho(apps):
        dia = [
            {"item_order": 1, "item_kind": "equipment", "muscle_group_id": BICEPS, "mapping_id": 104,
             "equipment_id": 1, "sets_count": 3, "advanced_technique": "rest_pause" if apps else None,
             "advanced_technique_applications": apps},
            {"item_order": 2, "item_kind": "equipment", "muscle_group_id": BICEPS, "mapping_id": ROSCA_B,
             "equipment_id": 1, "sets_count": 2},
        ]
        slots = [{"sequence": 1, "name": "A", "slot_type": "normal", "items": dia},
                 {"sequence": 2, "name": "D", "slot_type": "descanso", "items": []},
                 {"sequence": 3, "name": "B", "slot_type": "normal", "items": dia}]
        slots += [{"sequence": s, "name": "D", "slot_type": "descanso", "items": []} for s in range(4, 8)]
        return WorkoutPlanTemplateIn(name="R", volume_mode="equipment_series", slots=slots)

    def codigos(template):
        report = validation.ValidationReport()
        validation._check_methodology(template, report)
        return {
            w["code"] for w in report.to_dict()["warnings"] if w.get("muscle_group_id") == BICEPS
        }

    assert codigos(rascunho(None)) == {validation.METHODOLOGY_BELOW_RANGE, validation.METHODOLOGY_WEEKLY_BELOW_RANGE}
    assert codigos(rascunho(3)) == set()  # 3 × 0,4 = 1,2 -> teto 1,0: 6 por sessão, 12 na semana
