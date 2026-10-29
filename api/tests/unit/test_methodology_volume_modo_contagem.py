"""Modo de contagem do volume da metodologia (Lote 1: motor + loader).

`PRIMARY_ONLY` (default) tem que sair bit a bit igual à v4; `PRIMARY_SECONDARY`
segue a regra do usuário de 2026-10-16:

1. séries: cada item soma `sets x stimulus_weight` no grupo (primário e
   secundário); as frações se somam entre exercícios, sem virar série inteira;
2. frequência: a sessão conta para o grupo se ele tem trabalho PRIMARY nela OU
   se as frações de SECONDARY do grupo naquela sessão somam ao menos
   `SECONDARY_SESSION_MIN_SETS`;
3. `direct_exercises`: só PRIMARY, nos dois modos;
4. estabilizador nunca conta.

Os pesos daqui imitam o cadastro real (`exercise_muscle_contributions`):
SECONDARY de 0,15 a 0,85 em produção, e um PRIMARY com 0,80.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from tests.support import methodology_paridade as paridade
from tests.support.methodology_paridade import grupo, seed_volume_classes

SEED = seed_volume_classes()
PARIDADE_PATH = Path(__file__).resolve().parent / "data" / "methodology_volume_paridade_pre_f6.json"

DORSAL, BICEPS, PEITORAL, TRICEPS, CORE = 6, 5, 52, 14, 55
GROUPS = (
    grupo(DORSAL, "Costas - largura dorsal", "large"),
    grupo(BICEPS, "Biceps", "small"),
    grupo(PEITORAL, "Peitoral", "large"),
    grupo(TRICEPS, "Triceps", "small"),
    grupo(CORE, "Core", "abs"),
)

PUXADA, REMADA, ROSCA, SUPINO, CRUCIFIXO_PRI_080 = 109, 102, 104, 100, 110

P = mv.GroupContribution


def S(weight: float) -> mv.GroupContribution:  # noqa: N802 - SECONDARY, lido como no cadastro
    return mv.GroupContribution(weight, False)


CONTRIBUICOES = {
    PUXADA: {DORSAL: P(1.0, True), BICEPS: S(0.5)},
    REMADA: {DORSAL: P(1.0, True), BICEPS: S(0.5)},
    ROSCA: {BICEPS: P(1.0, True)},
    SUPINO: {PEITORAL: P(1.0, True), TRICEPS: S(0.15)},
    CRUCIFIXO_PRI_080: {PEITORAL: P(0.8, True)},
}


def item(mapping_id: int, sets: int, technique: float = 0.0) -> mv.MethodologyItemInput:
    return mv.MethodologyItemInput(
        mapping_id=mapping_id, canonical_exercise_id=mapping_id * 10, sets_count=sets, technique_equivalent=technique
    )


def treino(seq: int, *items: mv.MethodologyItemInput) -> mv.MethodologySlotInput:
    return mv.MethodologySlotInput(sequence=seq, slot_type="normal", name=f"S{seq}", items=tuple(items))


def semana(*slots: mv.MethodologySlotInput) -> list[mv.MethodologySlotInput]:
    """7 slots: os dados nas posições deles; o resto descanso."""
    by_seq = {s.sequence: s for s in slots}
    return [by_seq.get(seq, mv.MethodologySlotInput(seq, "descanso", "Descanso")) for seq in range(1, 8)]


def avaliar(slots, modo=mv.VolumeCountingMode.PRIMARY_SECONDARY, contribuicoes=CONTRIBUICOES):
    return mv.evaluate_methodology_volume(slots, contribuicoes, GROUPS, "equipment_series", None, SEED, modo)


def bloco(resultado: mv.MethodologyVolumeEvaluation, group_id: int) -> mv.GroupEvaluation:
    return next(g for g in resultado.groups if g.muscle_group_id == group_id)


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------


def test_papeis_por_modo_e_estabilizador_nunca():
    assert mv.counted_roles("primary_only") == frozenset({"PRIMARY"})
    assert mv.counted_roles(mv.VolumeCountingMode.PRIMARY_SECONDARY) == frozenset({"PRIMARY", "SECONDARY"})
    assert all("STABILIZER" not in roles for roles in mv.COUNTED_ROLES_BY_MODE.values())
    assert mv.DEFAULT_VOLUME_COUNTING_MODE is mv.VolumeCountingMode.PRIMARY_ONLY
    assert mv.SECONDARY_SESSION_MIN_SETS == 1
    with pytest.raises(ValueError):
        mv.counted_roles("tudo")


def test_modo_desconhecido_e_recusado():
    with pytest.raises(ValueError):
        avaliar(semana(treino(1, item(PUXADA, 2))), modo="ponderado")


# ---------------------------------------------------------------------------
# PRIMARY_SECONDARY
# ---------------------------------------------------------------------------


def test_puxada_2_series_com_biceps_050_vale_1_serie_de_biceps_e_conta_a_sessao():
    biceps = bloco(avaliar(semana(treino(1, item(PUXADA, 2)))), BICEPS)
    assert biceps.direct_sets_cycle_total == 1
    assert biceps.sessions[0].direct_sets == 1
    # 1,0 série de SECONDARY na sessão >= SECONDARY_SESSION_MIN_SETS: conta.
    assert biceps.frequency_raw == 1 and biceps.frequency_bucket == "1x"
    # ...mas exercício do grupo continua só PRIMARY.
    assert biceps.direct_exercises_cycle_total == 0
    assert biceps.sessions[0].direct_exercises == 0


def test_mesma_puxada_em_primary_only_nao_conta_biceps():
    biceps = bloco(avaliar(semana(treino(1, item(PUXADA, 2))), modo="primary_only"), BICEPS)
    assert biceps.direct_sets_cycle_total == 0
    assert biceps.frequency_raw == 0
    assert biceps.status is mv.MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED


def test_050_mais_050_na_mesma_sessao_somam_1_serie_e_contam_a_frequencia():
    biceps = bloco(avaliar(semana(treino(1, item(PUXADA, 1), item(REMADA, 1)))), BICEPS)
    assert biceps.direct_sets_cycle_total == 1
    assert biceps.frequency_raw == 1
    assert biceps.direct_exercises_cycle_total == 0


def test_045_sozinho_soma_045_e_nao_conta_a_frequencia():
    """Peso não-0,5 do cadastro: 3 séries x 0,15 = 0,45 série, sem arredondar."""
    triceps = bloco(avaliar(semana(treino(1, item(SUPINO, 3)))), TRICEPS)
    assert triceps.direct_sets_cycle_total == 0.45
    assert triceps.sessions[0].direct_sets == 0.45
    assert triceps.frequency_raw == 0 and triceps.frequency_bucket is None
    assert triceps.direct_exercises_cycle_total == 0
    assert triceps.status is mv.MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED


def test_fracoes_em_sessoes_diferentes_somam_na_semana_mas_nenhuma_conta_a_frequencia():
    """0,5 na segunda + 0,5 na quarta: 1,0 série na semana, frequência 0."""
    biceps = bloco(avaliar(semana(treino(1, item(PUXADA, 1)), treino(3, item(REMADA, 1)))), BICEPS)
    assert biceps.direct_sets_cycle_total == 1
    assert [s.direct_sets for s in biceps.sessions] == [0.5, 0.5]
    assert biceps.frequency_raw == 0


def test_secundario_soma_series_mas_nunca_conta_exercicio():
    """Rosca (PRIMARY bíceps) + puxada (SECONDARY 0,5) na mesma sessão: 3 + 2x0,5
    = 4 séries, 1 exercício de bíceps, 1 sessão."""
    biceps = bloco(avaliar(semana(treino(1, item(ROSCA, 3), item(PUXADA, 2)))), BICEPS)
    assert biceps.direct_sets_cycle_total == 4
    assert biceps.direct_exercises_cycle_total == 1
    assert biceps.frequency_raw == 1
    assert biceps.direct_sets_per_session == 4 and biceps.direct_exercises_per_session == 1


def test_frequencia_por_secundario_entra_na_media_por_sessao():
    """Rosca na sessão 1 (3 séries) e puxada 2 séries na sessão 3 (1,0 de
    SECONDARY): 2 sessões contam, 4 séries na semana, média 2."""
    biceps = bloco(avaliar(semana(treino(1, item(ROSCA, 3)), treino(3, item(PUXADA, 2)))), BICEPS)
    assert biceps.frequency_raw == 2 and biceps.frequency_bucket == "2x"
    assert biceps.direct_sets_per_session == 2
    assert biceps.direct_exercises_per_session == 0.5


def test_tecnica_entra_ponderada_pelo_peso():
    """2 séries + 1,0 equivalente de técnica, bíceps 0,5: 1,5 série, sendo 0,5
    de técnica."""
    biceps = bloco(avaliar(semana(treino(1, item(PUXADA, 2, technique=1.0)))), BICEPS)
    assert biceps.direct_sets_cycle_total == 1.5
    assert biceps.technique_equivalent_cycle_total == 0.5
    dorsal = bloco(avaliar(semana(treino(1, item(PUXADA, 2, technique=1.0)))), DORSAL)
    assert dorsal.direct_sets_cycle_total == 3 and dorsal.technique_equivalent_cycle_total == 1


def test_primario_usa_o_peso_cadastrado_so_no_modo_ponderado():
    slots = semana(treino(1, item(CRUCIFIXO_PRI_080, 3)))
    assert bloco(avaliar(slots), PEITORAL).direct_sets_cycle_total == 2.4
    assert bloco(avaliar(slots, modo="primary_only"), PEITORAL).direct_sets_cycle_total == 3
    # A presença PRIMARY conta sessão e exercício mesmo com peso < 1.
    assert bloco(avaliar(slots), PEITORAL).frequency_raw == 1
    assert bloco(avaliar(slots), PEITORAL).direct_exercises_cycle_total == 1


def test_item_so_com_secundario_e_contado_na_qualidade_mas_soma_as_fracoes():
    resultado = avaliar(semana(treino(1, item(999, 2))), contribuicoes={999: {BICEPS: S(0.85)}})
    assert resultado.data_quality.items_without_primary_contribution == 1
    assert bloco(resultado, BICEPS).direct_sets_cycle_total == 1.7


def test_peso_zero_nao_conta():
    resultado = avaliar(semana(treino(1, item(999, 2))), contribuicoes={999: {DORSAL: P(1.0, True), BICEPS: S(0.0)}})
    assert bloco(resultado, BICEPS).sessions == ()


# ---------------------------------------------------------------------------
# PRIMARY_ONLY: paridade com a v4
# ---------------------------------------------------------------------------


def _com_secundarios(primary: dict) -> dict:
    """O formato novo com SECONDARY de peso real junto: em PRIMARY_ONLY eles
    têm de ser ignorados."""
    extra = paridade.ABDOMEN  # grupo avaliado do cenário: em PRIMARY_SECONDARY mudaria o resultado
    return {
        mapping: {**({} if extra in groups else {extra: S(0.35)}), **{g: P(1.0, True) for g in groups}}
        for mapping, groups in primary.items()
    }


def test_paridade_primary_only_formato_novo_identico_ao_congelado():
    groups = tuple(paridade.grupo(i, n, s, p) for i, n, s, p in paridade.GRUPOS)
    novo = _com_secundarios(paridade.PRIMARY)
    atual = paridade.instantaneo(
        lambda slots, modo, intent: mv.evaluate_methodology_volume(
            slots, novo, groups, modo, intent, SEED, mv.VolumeCountingMode.PRIMARY_ONLY
        )
    )
    assert json.loads(json.dumps(atual)) == json.loads(PARIDADE_PATH.read_text(encoding="utf-8"))


def test_primary_only_formato_novo_identico_ao_conjunto_da_v4_payload_inteiro():
    """A avaliação INTEIRA (dataclasses, inclusive sessões e data_quality) é a
    mesma com o conjunto de grupos da v4 e com o formato novo + secundários."""
    groups = tuple(paridade.grupo(i, n, s, p) for i, n, s, p in paridade.GRUPOS)
    novo = _com_secundarios(paridade.PRIMARY)
    for nome, (slots, modo, intent) in paridade.CENARIOS.items():
        v4 = mv.evaluate_methodology_volume(slots, paridade.PRIMARY, groups, modo, intent, SEED)
        assert mv.evaluate_methodology_volume(slots, novo, groups, modo, intent, SEED, "primary_only") == v4, nome
    slots = semana(treino(1, item(PUXADA, 2, 0.4), item(SUPINO, 3)), treino(3, item(CRUCIFIXO_PRI_080, 3)))
    v4_form = {m: frozenset(g for g, c in contribs.items() if c.is_primary) for m, contribs in CONTRIBUICOES.items()}
    assert avaliar(slots, "primary_only") == mv.evaluate_methodology_volume(
        slots, v4_form, GROUPS, "equipment_series", None, SEED
    )
    payload = svc.evaluation_to_payload(avaliar(slots, "primary_only"))
    assert isinstance(bloco(avaliar(slots, "primary_only"), PEITORAL).direct_sets_cycle_total, int)
    assert payload["metric_version"] == "methodology-volume-v5"


# ---------------------------------------------------------------------------
# Loader: papel e peso vêm do cadastro
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))

    def fetchall(self):
        return self.rows


def test_loader_le_papeis_do_modo_e_o_stimulus_weight_cadastrado():
    cur = _Cursor(
        [
            {"exercise_id": PUXADA, "muscle_group_id": DORSAL, "role": "PRIMARY", "stimulus_weight": Decimal("1.00")},
            {"exercise_id": PUXADA, "muscle_group_id": BICEPS, "role": "SECONDARY", "stimulus_weight": "0.35"},
        ]
    )
    contribs = svc.load_volume_contributions(cur, {PUXADA}, "primary_secondary")
    # O loader entrega as linhas CRUAS; a junção é do motor.
    assert contribs == {
        PUXADA: (mv.ContributionRow(DORSAL, "PRIMARY", 1.0), mv.ContributionRow(BICEPS, "SECONDARY", 0.35))
    }
    assert mv.counted_contributions(contribs[PUXADA], "primary_secondary") == {DORSAL: P(1.0, True), BICEPS: S(0.35)}
    assert mv.counted_contributions(contribs[PUXADA], "primary_only") == {DORSAL: P(1, True)}
    sql, params = cur.executed[0]
    assert "role IN (%s,%s)" in sql and "is_active = 1" in sql and "stimulus_weight" in sql
    assert params == (PUXADA, "PRIMARY", "SECONDARY")  # estabilizador nunca é pedido


def test_loader_primary_only_pede_so_primary():
    cur = _Cursor([])
    assert svc.load_volume_contributions(cur, {PUXADA, REMADA}, "primary_only") == {}
    assert cur.executed[0][1] == (REMADA, PUXADA, "PRIMARY")


def test_motor_junta_mesmo_grupo_em_dois_papeis_primario_com_o_maior_peso():
    """A junção mora no motor: o loader devolve as duas linhas como vieram."""
    cur = _Cursor(
        [
            {"exercise_id": 1, "muscle_group_id": BICEPS, "role": "SECONDARY", "stimulus_weight": 0.85},
            {"exercise_id": 1, "muscle_group_id": BICEPS, "role": "PRIMARY", "stimulus_weight": 0.8},
        ]
    )
    rows = svc.load_volume_contributions(cur, {1}, "primary_secondary")[1]
    assert len(rows) == 2
    assert mv.counted_contributions(rows, "primary_secondary") == {BICEPS: P(0.85, True)}
    assert mv.counted_contributions(rows, "primary_only") == {BICEPS: P(1, True)}


def test_motor_ignora_estabilizador_nas_linhas_cruas_e_primary_com_peso_zero_conta_em_primary_only():
    rows = (
        mv.ContributionRow(DORSAL, "PRIMARY", 0.0),
        mv.ContributionRow(BICEPS, "SECONDARY", 0.45),
        mv.ContributionRow(CORE, "STABILIZER", 0.25),
    )
    assert mv.counted_contributions(rows, "primary_only") == {DORSAL: P(1, True)}
    assert mv.counted_contributions(rows, "primary_secondary") == {BICEPS: S(0.45)}
    biceps = bloco(avaliar(semana(treino(1, item(999, 3))), contribuicoes={999: rows}), BICEPS)
    assert biceps.direct_sets_cycle_total == 1.35 and biceps.frequency_raw == 1


def test_loader_sem_ids_nao_le_o_banco():
    cur = _Cursor([])
    assert svc.load_volume_contributions(cur, set(), "primary_secondary") == {}
    assert cur.executed == []
