"""M76 Lote 4a (E1): dono unico da normalizacao semanal.

Regra do usuario (Fase 7, 2026-09-29): a semana de um Modelo e SEMPRE a janela
de 7 dias a partir do 1o slot de treino - ciclo maior corta o excedente, ciclo
menor da a volta; descanso ocupa o dia. `weekly_normalization` converte
"valor por slot" no "planejado da semana"; `calendar_weekly_rate` converte um
acumulado de N dias de calendario em taxa semanal (realizado).

Sem banco, sem relogio.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

from app.intelligence import methodology_volume as mv


@dataclass(frozen=True)
class _Slot:
    sequence: int
    slot_type: Optional[str]


def _ciclo(qtd: int, descanso: tuple[int, ...] = ()) -> list[_Slot]:
    return [_Slot(seq, "descanso" if seq in descanso else "normal") for seq in range(1, qtd + 1)]


def _um_por_slot(slots: list[_Slot], valor: float = 1.0) -> dict[int, dict[str, float]]:
    """Cada slot de treino com `valor` no grupo dele (`g<seq>`) e no total."""
    return {s.sequence: {f"g{s.sequence}": valor, "total": valor} for s in slots if s.slot_type != "descanso"}


# ---------------------------------------------------------------------------
# weekly_normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "qtd, descanso, vezes",
    [
        # 3 slots: 1,2,3,1,2,3,1 -> da a volta
        (3, (), {1: 3, 2: 2, 3: 2}),
        # 4 slots com descanso no 4: 1,2,3,D,1,2,3
        (4, (4,), {1: 2, 2: 2, 3: 2}),
        # 7 slots: uma volta exata
        (7, (4, 7), {1: 1, 2: 1, 3: 1, 5: 1, 6: 1}),
        # 8 slots: corta o 8o dia (descanso)
        (8, (4, 8), {1: 1, 2: 1, 3: 1, 5: 1, 6: 1, 7: 1}),
        # 24 slots: corta 17 dias
        (24, (), {seq: 1 for seq in range(1, 8)}),
    ],
)
def test_cada_slot_soma_as_vezes_que_cai_na_semana(qtd, descanso, vezes):
    slots = _ciclo(qtd, descanso)
    semana = mv.weekly_normalization(slots, _um_por_slot(slots, 2.0))
    assert semana["total"] == pytest.approx(2.0 * sum(vezes.values()))
    for seq in range(1, qtd + 1):
        assert semana.get(f"g{seq}", 0.0) == pytest.approx(2.0 * vezes.get(seq, 0))


def test_janela_comeca_no_primeiro_slot_de_treino():
    """Descanso antes do 1o treino nao abre a semana: 3 slots D,T,T ->
    2,3,1(D),2,3,1(D),2 -> slot 2 cai 3x e o 3, 2x."""
    slots = [_Slot(1, "descanso"), _Slot(2, "normal"), _Slot(3, "normal")]
    semana = mv.weekly_normalization(slots, {2: {"peito": 1.0}, 3: {"costas": 1.0}})
    assert semana == {"peito": 3.0, "costas": 2.0}


def test_ordem_e_por_sequence_e_nao_pela_lista():
    slots = [_Slot(3, "normal"), _Slot(1, "normal"), _Slot(2, "descanso")]
    # Ordem real: 1,2(D),3,1,2(D),3,1 -> slot 1 cai 3x, o 3 cai 2x.
    assert mv.weekly_normalization(slots, {1: {"a": 1.0}, 3: {"b": 1.0}}) == {"a": 3.0, "b": 2.0}


def test_valor_em_slot_de_descanso_nunca_soma():
    slots = _ciclo(2, descanso=(2,))
    assert mv.weekly_normalization(slots, {1: {"a": 1.0}, 2: {"a": 99.0}}) == {"a": 4.0}


def test_sem_slot_de_treino_ou_sem_slots_nao_inventa_semana():
    assert mv.weekly_normalization(_ciclo(3, descanso=(1, 2, 3)), {1: {"a": 5.0}}) == {}
    assert mv.weekly_normalization([], {1: {"a": 5.0}}) == {}


def test_chave_com_total_zero_fica_de_fora():
    slots = _ciclo(1)
    assert mv.weekly_normalization(slots, {1: {"a": 0.0, "b": None, "c": 1.0}}) == {"c": 7.0}


def test_nao_e_mais_x_7_sobre_ciclo():
    """Ciclo de 8 slots, todos de treino: a semana tem 7 sessoes (o 8o slot fica
    de fora), nao `8 x 7/8 = 7` sessoes 'medias' espalhadas pelos 8 slots."""
    slots = _ciclo(8)
    semana = mv.weekly_normalization(slots, _um_por_slot(slots))
    assert "g8" not in semana
    assert semana["g1"] == 1.0


# ---------------------------------------------------------------------------
# weekly_window_days / calendar_weekly_rate
# ---------------------------------------------------------------------------


def test_a_semana_tem_os_dias_da_janela_de_avaliacao():
    assert mv.weekly_window_days() == mv.EVALUATION_WINDOW_DAYS == 7


@pytest.mark.parametrize("total, dias, esperado", [(28.0, 28, 7.0), (24.0, 8, 21.0), (10.0, 7, 10.0), (0.0, 14, 0.0)])
def test_taxa_semanal_de_calendario(total, dias, esperado):
    assert mv.calendar_weekly_rate(total, dias) == pytest.approx(esperado)


@pytest.mark.parametrize("total, dias", [(None, 7), (5.0, 0), (5.0, -3)])
def test_taxa_semanal_sem_total_ou_sem_dias_e_none(total, dias):
    assert mv.calendar_weekly_rate(total, dias) is None


# ---------------------------------------------------------------------------
# Fatia -> bloco (E12)
# ---------------------------------------------------------------------------


def _g(gid, classe=None, pai=None):
    return SimpleNamespace(id=gid, volume_class_id=classe, methodology_parent_group_id=pai)


def test_fatia_usa_o_macro_grupo_da_raiz_mesmo_tendo_classe_propria():
    grupos = {1: _g(1, 10), 2: _g(2, 20, pai=1), 3: _g(3, None, pai=2)}
    assert [mv.block_of_group(g, grupos) for g in (1, 2, 3)] == [1, 1, 1]
    assert [mv.block_volume_class_id(g, grupos) for g in (1, 2, 3)] == [10, 10, 10]


def test_ciclo_de_cadastro_e_pai_desconhecido_nao_travam():
    grupos = {4: _g(4, None, pai=5), 5: _g(5, 30, pai=4), 6: _g(6, 40, pai=99)}
    assert mv.block_volume_class_id(4, grupos) == 30  # para no ultimo visitado (5)
    assert mv.block_volume_class_id(6, grupos) == 40  # pai fora da lista: bloco proprio
    assert mv.block_volume_class_id(77, grupos) is None
