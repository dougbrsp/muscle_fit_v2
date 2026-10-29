"""Matching (`domain/nutrition/food_text.py`) calibrado contra os nomes REAIS da
TACO 4ª ed. (Lote L3a, mapa `docs/motores/FONTES_DA_VERDADE.md` §48).

O catálogo é o seed da migração 081 (597 nomes). Medição registrada no dono:
nas 49 buscas abaixo (47 inequívocas + 2 ambíguas), acerto no topo com nota de
candidato (>= 0,6) foi de 33 para 47, e a pré-seleção errada de 1 para 0
("peito de frango sem pele cru" pré-selecionava "com pele"). Cada caso é uma
descrição real de refeição -> o número do alimento na TACO.

Rodar: cd api && python -m pytest tests/unit/test_nutrition_food_text_taco.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain.nutrition import food_text as ft


@functools.lru_cache(maxsize=1)
def _catalogo() -> tuple[tuple[int, str], ...]:
    seed = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "081_food_catalog_seed_taco.py").__globals__
    return tuple((int(linha[0]), linha[1]) for linha in seed["SEED_ROWS"])


def _nome(numero: int) -> str:
    return dict(_catalogo())[numero]


@functools.lru_cache(maxsize=None)
def _ranking(busca: str) -> tuple[ft.Match, ...]:
    return ft.rank_candidates(busca, _catalogo())


# Busca inequívoca -> o item tem de vir em PRIMEIRO, com nota de candidato.
INEQUIVOCAS = [
    ("arroz branco cozido", 3),  # §45: era 0,57 e perdia para o integral
    ("arroz integral cozido", 1),
    ("peito de frango grelhado", 410),
    ("peito de frango cozido", 408),
    ("peito de frango sem pele cru", 409),  # antes: pré-selecionava "com pele"
    ("peito de frango com pele assado", 406),
    ("coxa de frango assada", 396),
    ("ovo cozido", 488),
    ("ovo frito", 490),
    ("clara de ovo cozida", 486),
    ("banana prata", 182),
    ("banana nanica", 179),
    ("feijão carioca cozido", 561),
    ("feijão preto cozido", 567),
    ("pão francês", 53),
    ("pão integral", 52),
    ("leite integral", 458),
    ("leite desnatado", 457),
    ("batata doce cozida", 88),
    ("batata inglesa cozida", 91),
    ("batata frita", 93),
    ("aipim cozido", 129),
    ("aveia em flocos", 7),
    ("azeite de oliva", 260),
    ("queijo mussarela", 463),
    ("queijo minas frescal", 461),
    ("requeijão cremoso", 468),
    ("iogurte natural", 448),
    ("maçã fuji", 222),
    ("mamão papaia", 226),
    ("laranja pera", 214),
    ("abacate", 163),
    ("tomate cru", 157),
    ("alface crespa", 78),
    ("brócolis cozido", 100),
    ("cenoura crua", 110),
    ("carne moída cozida", 326),
    ("patinho grelhado", 377),
    ("contra filé sem gordura grelhado", 346),
    ("salmão sem pele grelhado", 317),
    ("atum em conserva", 277),
    ("amendoim torrado", 558),
    ("castanha do pará", 589),
    ("lentilha cozida", 577),
    ("grão de bico", 575),
    ("açúcar refinado", 494),
    ("mel", 507),
    ("chocolate ao leite", 495),
]

# Busca sem o corte/parte: o item esperado fica entre os 3 primeiros e NADA é
# pré-selecionado (folga mínima sobre o segundo).
AMBIGUAS = [
    ("frango grelhado", 410),  # peito × coração × lingüiça de frango
    ("frango com pele assado", 406),  # peito × coxa × sobrecoxa
]

# Alimento que não está na TACO: nenhum candidato (vira cadastro ou USDA, L3b).
FORA_DA_TACO = ["whey protein", "creatina", "barra de proteína", "tilápia grelhada"]


def test_o_catalogo_e_o_seed_inteiro():
    assert len(_catalogo()) == 597
    assert len(INEQUIVOCAS) + len(AMBIGUAS) >= 30


@pytest.mark.parametrize(("busca", "numero"), INEQUIVOCAS, ids=[b for b, _ in INEQUIVOCAS])
def test_busca_inequivoca_vem_em_primeiro(busca, numero):
    ranking = _ranking(busca)
    topo = [(m.candidate_id, m.name, round(m.score, 3)) for m in ranking[:3]]
    assert ranking and ranking[0].candidate_id == numero, (busca, _nome(numero), topo)
    assert ranking[0].score >= ft.MATCH_MIN_SCORE


@pytest.mark.parametrize(("busca", "numero"), AMBIGUAS, ids=[b for b, _ in AMBIGUAS])
def test_busca_ambigua_fica_no_topo_sem_pre_selecao(busca, numero):
    ranking = _ranking(busca)
    assert numero in [m.candidate_id for m in ranking[:3]], (busca, _nome(numero))
    assert not any(m.auto_accept for m in ranking)


@pytest.mark.parametrize("busca", FORA_DA_TACO)
def test_alimento_fora_da_taco_nao_vira_candidato(busca):
    assert _ranking(busca) == ()


def test_pre_selecao_nunca_escolhe_o_item_errado():
    for busca, numero in INEQUIVOCAS + AMBIGUAS:
        ranking = _ranking(busca)
        aceitos = [m for m in ranking if m.auto_accept]
        assert len(aceitos) <= 1
        assert all(m.candidate_id == numero for m in aceitos), (busca, aceitos)


def test_busca_da_tela_mantem_a_mesma_ordem_do_casamento():
    for busca, _numero in INEQUIVOCAS[:10]:
        casamento = [m.candidate_id for m in _ranking(busca)]
        tela = [m.candidate_id for m in ft.order_for_search(busca, _catalogo())]
        assert tela[: len(casamento)] == casamento
