"""Validador do Motor de Periodizacao: metodologia de volume por grupo e
sessao completa no Modo B, COM ou SEM perfil de metas (correcao do
diagnostico de 2026-09-28).

Cenarios, com a unica costura com banco (`load_methodology_inputs`) trocada
por dados em memoria - os MESMOS grupos/contribuicoes de
`test_methodology_volume.py`:

1. o rascunho do relato (8 slots, 3 exercicios x 3 series por dia, sem perfil
   de metas) recebe `SLOT_TOO_FEW_EXERCISES` em cada dia e
   `METHODOLOGY_BELOW_RANGE` por grupo, com o payload combinado;
2. um rascunho dentro da faixa fica quieto;
3. Modo A e slot de descanso nao sao avaliados; falha de leitura vira aviso
   `UNAVAILABLE`, nunca excecao;
4. `validate_draft` de ponta a ponta liga a etapa nova (com o resto do banco
   substituido) e nada vira `error`;
5. Fase 6: a meta SEMANAL do macro-grupo tambem vira aviso
   (`METHODOLOGY_WEEKLY_BELOW_RANGE` / `_ABOVE_RANGE`), e o payload traz o
   macro-grupo. O cadastro e o seed da 057 (os numeros da v1).
"""

from __future__ import annotations

import pytest

from app.intelligence import methodology_volume_service as svc
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import validation
from tests.unit.test_methodology_volume import CANONICAL, GROUPS, PEQUENOS as SEED_PEQUENOS, PRIMARY, SEED, BICEPS, PEITORAL, TRICEPS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def leituras(monkeypatch):
    """Troca `load_methodology_inputs` por dados em memoria (mesma tecnica de
    `test_methodology_volume_service.py`) e registra os `mapping_ids` pedidos."""
    pedidos: list[set[int]] = []

    def fake(mapping_ids: set[int], counting_mode=None) -> svc.MethodologyInputs:
        pedidos.append(set(mapping_ids))
        return svc.MethodologyInputs(
            canonical_ids={m: CANONICAL.get(m) for m in mapping_ids},
            contributions={m: PRIMARY[m] for m in mapping_ids if m in PRIMARY},
            groups=GROUPS,
            volume_classes=SEED,
        )

    monkeypatch.setattr(svc, "load_methodology_inputs", fake)
    return pedidos


def _eq(order: int, mapping_id: int, group: int, sets_count: int) -> dict:
    return {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": group,
        "mapping_id": mapping_id,
        "equipment_id": 900 + mapping_id,
        "sets_count": sets_count,
        "reps_target": "8-12",
        "rpe_target": "8",
    }


def _slot(seq: int, items: list[dict], slot_type: str = "normal", name: str | None = None) -> dict:
    return {"sequence": seq, "name": name or f"Dia {seq}", "slot_type": slot_type, "items": items}


def _template(slots: list[dict], volume_mode: str = "equipment_series") -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(name="Rascunho", volume_mode=volume_mode, slots=slots)


# O rascunho do relato: 8 slots, cada dia de treino com 3 exercicios x 3 series,
# peito 1x (9 series diretas contra 15-20 de EXG-015), biceps 1x (3 contra
# 9-16 de EXG-017), triceps 1x (3 contra 9-16), tres dias de descanso.
ESBOCO_3X3 = [
    _slot(1, [_eq(1, 100, PEITORAL, 3), _eq(2, 110, PEITORAL, 3), _eq(3, 113, PEITORAL, 3)], name="Peito"),
    _slot(2, [], slot_type="descanso"),
    _slot(3, [_eq(1, 102, 6, 3), _eq(2, 109, 6, 3), _eq(3, 104, BICEPS, 3)], name="Costas"),
    _slot(4, [], slot_type="descanso"),
    _slot(5, [_eq(1, 106, TRICEPS, 3), _eq(2, 105, 1, 3), _eq(3, 112, PEITORAL, 3)], name="Braços"),
    _slot(6, [], slot_type="descanso"),
    _slot(7, [_eq(1, 101, 12, 3), _eq(2, 111, 12, 3), _eq(3, 107, PEITORAL, 3)], name="Peito 2"),
    _slot(8, [], slot_type="descanso"),
]

# Ciclo de 7 dias, tudo dentro da faixa POR SESSAO da celula de cada grupo E
# da meta SEMANAL do macro-grupo (Fase 6; seed: Grandes 18-32, Pequenos e
# Outros 12-24): peito 1x, 18 series (EXG-015: 15-20); costas 1x, 18
# (EXG-015); biceps 2x, 6 + 6 = 6 por sessao (EXG-018: 6-12), 12 na semana;
# triceps 1x, 12 (EXG-017: 9-16); abdomen 1x, 12 (derivada D1: 6-12). Cada
# sessao com 6 itens.
DENTRO_DA_FAIXA = [
    _slot(
        1,
        [
            _eq(1, 100, PEITORAL, 4),
            _eq(2, 101, 12, 4),
            _eq(3, 110, PEITORAL, 4),
            _eq(4, 111, 12, 3),
            _eq(5, 113, PEITORAL, 3),
            _eq(6, 104, BICEPS, 6),
        ],
        name="Peito e bíceps",
    ),
    _slot(2, [], slot_type="descanso"),
    _slot(
        3,
        [
            _eq(1, 102, 6, 6),
            _eq(2, 109, 6, 6),
            _eq(3, 103, 7, 6),
            _eq(4, 106, TRICEPS, 12),
            _eq(5, 105, 1, 12),
            _eq(6, 104, BICEPS, 6),
        ],
        name="Costas e tríceps",
    ),
    *[_slot(seq, [], slot_type="descanso") for seq in (4, 5, 6, 7)],
]


# ---------------------------------------------------------------------------
# 1. O esboco do relato: avisos por dia e por grupo, SEM perfil de metas
# ---------------------------------------------------------------------------


def test_esboco_3x3_sem_perfil_recebe_slot_too_few_exercises_em_cada_dia_de_treino(leituras):
    report = validation.ValidationReport()
    validation._check_methodology(_template(ESBOCO_3X3), report)

    poucos = [w for w in report.warnings if w["code"] == validation.SLOT_TOO_FEW_EXERCISES]
    assert [w["slot"] for w in poucos] == [1, 3, 5, 7]  # descanso nao conta
    assert poucos[0] == {
        "code": "SLOT_TOO_FEW_EXERCISES",
        "slot": 1,
        "slot_name": "Peito",
        "items": 3,
        "min": validation.SESSION_EXERCISES_MIN,
    }
    assert validation.SESSION_EXERCISES_MIN == 6


def test_esboco_3x3_sem_perfil_recebe_methodology_below_range_por_grupo(leituras):
    report = validation.ValidationReport()
    validation._check_methodology(_template(ESBOCO_3X3), report)

    abaixo = {w["muscle_group_id"]: w for w in report.warnings if w["code"] == validation.METHODOLOGY_BELOW_RANGE}
    # Biceps: 1 sessao com 3 series diretas, celula (small, 1x) = 9-16 (EXG-017).
    assert abaixo[BICEPS] == {
        "code": "METHODOLOGY_BELOW_RANGE",
        "muscle_group_id": BICEPS,
        "muscle_group_name": "Biceps",
        "volume_class_id": SEED_PEQUENOS,
        "volume_class_name": "Pequenos",
        "size_class": "small",
        "frequency_bucket": "1x",
        "frequency": 1,
        "projected_sets": 3.0,
        "min": 9,
        "max": 16,
        "evidence": "EXG-017",
    }
    # Costas (fatia miolo somada no pai): 1 sessao num ciclo de 8 dias ->
    # 0,875x -> coluna 1x; 6 series diretas contra 15-20 (EXG-015).
    costas = abaixo[6]
    assert (costas["size_class"], costas["frequency_bucket"], costas["frequency"]) == ("large", "1x", 1)
    assert (costas["projected_sets"], costas["min"], costas["max"], costas["evidence"]) == (6.0, 15, 20, "EXG-015")
    # Triceps e abdomen tambem abaixo (3 series em 1x).
    assert abaixo[TRICEPS]["min"] == 9 and abaixo[1]["volume_class_name"] == "Outros"
    # Peitoral (fatia superior somada no pai): 3 sessoes em 8 dias -> 2,625x ->
    # coluna 3x_plus DERIVADA (6-12 por sessao); 9 + 3 + 9 = 21 series em 3
    # sessoes = 7 por sessao: dentro da faixa derivada, entao SEM aviso. O
    # esboco e apontado pelos slots (SLOT_TOO_FEW_EXERCISES), nao por este grupo.
    assert PEITORAL not in abaixo
    # Nada vira erro; a leitura foi pedida uma vez, com todos os mapping_ids.
    assert report.errors == []
    assert len(leituras) == 1
    assert leituras[0] == {100, 110, 113, 102, 109, 104, 106, 105, 112, 101, 111, 107}


def test_esboco_3x3_tambem_fica_abaixo_da_meta_semanal(leituras):
    """Fase 6: a soma da janela de 7 dias (v3) contra a meta semanal do macro-grupo."""
    report = validation.ValidationReport()
    validation._check_methodology(_template(ESBOCO_3X3), report)
    semanal = {
        w["muscle_group_id"]: w for w in report.warnings if w["code"] == validation.METHODOLOGY_WEEKLY_BELOW_RANGE
    }
    # Biceps: 3 series na janela de 7 dias (o slot 8 fica fora) = 3 por semana, meta 12-24.
    assert semanal[BICEPS] == {
        "code": "METHODOLOGY_WEEKLY_BELOW_RANGE",
        "muscle_group_id": BICEPS,
        "muscle_group_name": "Biceps",
        "volume_class_id": SEED_PEQUENOS,
        "volume_class_name": "Pequenos",
        "size_class": "small",
        "frequency": 1,
        "weekly_sets": 3.0,
        "min": 12,
        "max": 24,
        "evidence": "2x × faixa por sessão",
    }
    # Peitoral: dentro da faixa 3x+ por sessao, e 21 series na janela (slots 1-7)
    # esta DENTRO de 18-32 - nenhum aviso para ele em eixo nenhum.
    assert PEITORAL not in semanal
    assert not [w for w in report.warnings if w["code"] == validation.METHODOLOGY_WEEKLY_ABOVE_RANGE]


def test_acima_da_meta_semanal_vira_methodology_weekly_above_range(leituras):
    # Biceps 3x por semana com 10 series por sessao: 30 na semana, acima de 24.
    # Por sessao, 3x+ de Pequenos e 3-8: tambem ACIMA (os dois avisos saem).
    slots = [
        _slot(seq, [_eq(1, 104, BICEPS, 10)] if seq in (1, 3, 5) else [], slot_type="normal" if seq in (1, 3, 5) else "descanso")
        for seq in range(1, 8)
    ]
    report = validation.ValidationReport()
    validation._check_methodology(_template(slots), report)
    codigos = [(w["code"], w["muscle_group_id"]) for w in report.warnings if w["code"].startswith("METHODOLOGY_")]
    assert codigos == [("METHODOLOGY_ABOVE_RANGE", BICEPS), ("METHODOLOGY_WEEKLY_ABOVE_RANGE", BICEPS)]
    semanal = report.warnings[-1]
    assert (semanal["weekly_sets"], semanal["min"], semanal["max"]) == (30.0, 12, 24)


def test_grupos_nao_tocados_e_nao_classificados_nao_geram_aviso(leituras):
    """NOT_DIRECTLY_TRAINED (musculo zero, EXG-025) e NOT_EVALUABLE nao sao
    BELOW_RANGE - o falso alarme ensinaria a ignorar o painel."""
    report = validation.ValidationReport()
    semana = [ESBOCO_3X3[0]] + [_slot(seq, [], slot_type="descanso") for seq in range(2, 8)]  # so o dia de peito
    validation._check_methodology(_template(semana), report)
    ids = {w.get("muscle_group_id") for w in report.warnings if w["code"].startswith("METHODOLOGY_")}
    assert ids == {PEITORAL}  # costas, biceps, triceps, abdomen, core: nenhum aviso


# ---------------------------------------------------------------------------
# 2. Dentro da faixa: quieto
# ---------------------------------------------------------------------------


def test_rascunho_dentro_da_faixa_nao_gera_aviso_nenhum(leituras):
    report = validation.ValidationReport()
    validation._check_methodology(_template(DENTRO_DA_FAIXA), report)
    assert report.warnings == [], report.warnings


def test_acima_da_faixa_vira_methodology_above_range(leituras):
    # Biceps 1x com 20 series diretas num unico exercicio: acima de 16 (EXG-017).
    slots = [_slot(1, [_eq(1, 104, BICEPS, 20)] + [_eq(i, m, PEITORAL, 3) for i, m in enumerate((100, 110, 113, 112, 107), start=2)])]
    slots += [_slot(seq, [], slot_type="descanso") for seq in range(2, 8)]
    report = validation.ValidationReport()
    validation._check_methodology(_template(slots), report)
    acima = [w for w in report.warnings if w["code"] == validation.METHODOLOGY_ABOVE_RANGE]
    assert [(w["muscle_group_id"], w["projected_sets"], w["max"]) for w in acima] == [(BICEPS, 20.0, 16)]


# ---------------------------------------------------------------------------
# 3. Fora do escopo e falha de leitura
# ---------------------------------------------------------------------------


def test_validate_draft_no_modo_a_nao_chama_a_metodologia(monkeypatch, leituras):
    monkeypatch.setattr(validation, "_check_references", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_check_template_rules", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_check_activation", lambda *a, **k: None)
    saida = {
        "template": {
            "name": "Modo A",
            "description": None,
            "volume_mode": "percent_budget",
            "slots": [
                {"sequence": 1, "name": "A", "slot_type": "normal", "weekday": None, "items": [
                    _item_llm(item_kind="muscle_group", muscle_group_id=PEITORAL, priority_min_pct=40, priority_max_pct=60)
                ]}
            ],
        },
        "rationale": "",
        "source_citations": [],
    }
    _, report, _ = validation.validate_draft(
        object(), PeriodizationDraftIn(volume_mode="percent_budget", days_per_cycle=1), saida
    )
    assert report["warnings"] == [] and leituras == []


def test_slot_de_descanso_e_ignorado_e_cardio_nao_conta_como_exercicio(leituras):
    slots = [
        _slot(1, [], slot_type="descanso"),
        _slot(2, [{"item_order": 1, "item_kind": "cardio", "sets_count": 1}] + [_eq(i, m, PEITORAL, 4) for i, m in enumerate((100, 110, 113, 112, 107), start=2)], name="Peito + cardio"),
    ]
    report = validation.ValidationReport()
    validation._check_methodology(_template(slots), report)
    poucos = [w for w in report.warnings if w["code"] == validation.SLOT_TOO_FEW_EXERCISES]
    assert [(w["slot"], w["items"]) for w in poucos] == [(2, 5)]


def test_falha_de_leitura_vira_aviso_unavailable_e_nao_excecao(monkeypatch):
    def estoura(mapping_ids, counting_mode=None):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(svc, "load_methodology_inputs", estoura)
    report = validation.ValidationReport()
    validation._check_methodology(_template(DENTRO_DA_FAIXA), report)
    assert report.warnings == [
        {"code": "ACTIVATION_COVERAGE", "status": "UNAVAILABLE", "message": "metodologia: banco fora"}
    ]


def test_sem_mapping_id_nenhum_nao_le_o_banco(leituras):
    report = validation.ValidationReport()
    validation._check_methodology(_template([_slot(1, [], slot_type="descanso")]), report)
    assert report.warnings == [] and leituras == []


# ---------------------------------------------------------------------------
# 4. Ponta a ponta: `validate_draft` liga a etapa nova, sem perfil, e nada e erro
# ---------------------------------------------------------------------------


def _item_llm(**overrides) -> dict:
    base = {
        "item_kind": "equipment",
        "muscle_group_id": None,
        "priority_min_pct": None,
        "priority_max_pct": None,
        "prioritize": False,
        "mapping_id": None,
        "equipment_id": None,
        "sets_count": None,
        "reps_target": None,
        "rpe_target": None,
        "advanced_technique": None,
        "advanced_technique_applications": None,
        "advanced_technique_duration_seconds": None,
        "warmup_sets_count": None,
        "warmup_reps": None,
        "warmup_rest_seconds": None,
        "feeler_set_sets_count": None,
        "feeler_set_reps": None,
        "feeler_set_rest_seconds": None,
        "peak_contraction_seconds": None,
    }
    base.update(overrides)
    return base


def test_validate_draft_modo_b_sem_perfil_emite_os_avisos_novos_como_warning(monkeypatch, leituras):
    monkeypatch.setattr(validation, "_check_references", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_check_template_rules", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_apply_safety_auto_fixes", lambda *a, **k: None)
    saida = {
        "template": {
            "name": "Esboço",
            "description": None,
            "volume_mode": "equipment_series",
            "slots": [
                {
                    "sequence": 1,
                    "name": "Peito",
                    "slot_type": "normal",
                    "weekday": None,
                    "items": [
                        _item_llm(muscle_group_id=PEITORAL, mapping_id=m, equipment_id=900 + m, sets_count=3, reps_target="8-12", rpe_target="8")
                        for m in (100, 110, 113)
                    ],
                },
                *[
                    {"sequence": seq, "name": "Descanso", "slot_type": "descanso", "weekday": None, "items": []}
                    for seq in range(2, 8)
                ],
            ],
        },
        "rationale": "teste",
        "source_citations": ["EXERCICIOS_GRUPOS.md"],
    }
    pedido = PeriodizationDraftIn(volume_mode="equipment_series", gym_id=7, days_per_cycle=7)
    draft, report, extras = validation.validate_draft(object(), pedido, saida)

    assert report["errors"] == []
    codigos = [w["code"] for w in report["warnings"]]
    # Fase 6: 9 series na semana tambem ficam abaixo da meta semanal 18-32.
    assert codigos == ["SLOT_TOO_FEW_EXERCISES", "METHODOLOGY_BELOW_RANGE", "METHODOLOGY_WEEKLY_BELOW_RANGE"]
    peito = report["warnings"][1]
    # 1 sessao em 7 dias -> 1x; 9 series por sessao contra 15-20 (EXG-015).
    assert (peito["muscle_group_id"], peito["frequency_bucket"], peito["projected_sets"], peito["min"]) == (
        PEITORAL,
        "1x",
        9.0,
        15,
    )
    assert draft["volume_mode"] == "equipment_series" and extras["rationale"] == "teste"
