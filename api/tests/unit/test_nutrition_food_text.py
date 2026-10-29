"""Texto de alimento (dono `app/domain/nutrition/food_text.py`, D2): nome
normalizado, sinônimos pt-BR, matching com o catálogo (candidato a partir de
0,6; pré-seleção a partir de 0,85, só do primeiro e com folga sobre o segundo)
e os termos da busca FULLTEXT. A calibração contra os nomes reais da TACO está
em `test_nutrition_food_text_taco.py` (L3a)."""

from __future__ import annotations

import json
import re

import pytest

from app.domain.nutrition import food_text as ft
from app.domain.nutrition.anthropometrics import fold_text


def test_limiares_do_plano():
    assert ft.MATCH_MIN_SCORE == 0.6
    assert ft.MATCH_AUTO_ACCEPT_SCORE == 0.85 == 1.0 - ft.MATCH_AUTO_ACCEPT_MAX_DISTANCE
    assert ft.TOKEN_JACCARD_WEIGHT + ft.SEQUENCE_RATIO_WEIGHT == 1.0
    assert 0.0 < ft.QUERY_COVERAGE_WEIGHT < 1.0
    assert 0.0 < ft.MATCH_AUTO_ACCEPT_MIN_MARGIN < ft.MATCH_AUTO_ACCEPT_MAX_DISTANCE


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Pão Francês", "pao frances"),
        ("pão de sal", "pao frances"),
        ("Cacetinho", "pao frances"),
        ("Pão, trigo, francês", "pao frances"),  # L3a: "pão de trigo" é o pão
        ("Macaxeira", "mandioca"),
        ("Purê de aipim", "pure mandioca"),
        ("Batata-doce", "batata doce"),
        ("Whey Protein", "whey"),
        ("Arroz, tipo 1, cozido", "arroz tipo 1 cozido"),
        ("arroz branco cozido", "arroz tipo 1 cozido"),  # L3a: variedade
        # L3a: "sem" deixou de ser stopword ("sem pele" × "com pele").
        ("Frango, peito, sem pele, grelhado", "frango peito sem pele grelhado"),
        ("Frango, peito, com pele, assado", "frango peito pele assado"),
        # Regressão (2026-10-16): "galinha" não vira "frango". Desde o L3a o ovo
        # de galinha inteiro é "o ovo", e o detalhe "/10minutos" sai.
        ("Ovo, de galinha, inteiro, cozido", "ovo cozido"),
        ("Ovo, de galinha, inteiro, cozido/10minutos", "ovo cozido"),
        ("Ovo, de galinha, clara, cozida/10minutos", "ovo clara cozido"),
        ("Caldo de galinha, tablete", "caldo galinha tablete"),
        ("Leite, de vaca, desnatado, UHT", "leite desnatado"),
        ("Carne, bovina, acém, moído, cozido", "carne acem moido cozido"),
        ("carne moída cozida", "carne moido cozido"),
        ("Queijo, mozarela", "queijo mozarela"),
        ("queijo muçarela", "queijo mozarela"),
        ("Castanha-do-Brasil, crua", "castanha brasil cru"),
        ("castanha do pará", "castanha brasil"),
        ("de", "de"),  # só stopword: mantém o texto
        ("   ", ""),
        (None, ""),
    ],
)
def test_normalize_name(bruto, esperado):
    assert ft.normalize_name(bruto) == esperado


def test_normalize_name_e_idempotente():
    for bruto in ("Ovo, de galinha, inteiro, cozido/10minutos", "Castanha-do-Brasil, crua", "pão de sal"):
        uma = ft.normalize_name(bruto)
        assert ft.normalize_name(uma) == uma


def test_nenhuma_forma_canonica_contem_outra_variante():
    """A troca é numa passada só: forma canônica com variante dentro dela
    dependeria da ordem das trocas (e quebraria a idempotência)."""
    variantes = {fold_text(v) for vs in ft.SYNONYMS.values() for v in vs}
    for canonica in ft.SYNONYMS:
        dentro = [v for v in variantes if v != canonica and re.search(rf"\b{re.escape(v)}\b", canonica)]
        assert dentro == [], (canonica, dentro)


def test_tokens():
    assert ft.tokens("Banana, prata, crua") == frozenset({"banana", "prata", "cru"})


@pytest.mark.parametrize(
    ("consulta", "candidato"),
    [
        ("aipim", "Mandioca"),
        ("pão de sal", "Pão francês"),
        ("Arroz, integral, cozido", "arroz integral cozido"),
        ("aipim cozido", "Mandioca, cozida"),  # L3a: gênero do preparo
        ("arroz branco cozido", "Arroz, tipo 1, cozido"),  # §45: era 0,57
    ],
)
def test_iguais_depois_de_normalizar_valem_1(consulta, candidato):
    assert ft.score(consulta, candidato) == 1.0


@pytest.mark.parametrize(
    ("consulta", "candidato"),
    [("banana", "banana prata"), ("arroz", "feijao"), ("frango grelhado", "Frango, peito, sem pele, grelhado"), ("x", "y")],
)
def test_score_entre_0_e_1(consulta, candidato):
    assert 0.0 <= ft.score(consulta, candidato) < 1.0


@pytest.mark.parametrize(("consulta", "candidato"), [("", "arroz"), ("arroz", None), (None, None)])
def test_score_vazio_e_zero(consulta, candidato):
    assert ft.score(consulta, candidato) == 0.0


def test_cobertura_da_busca_pesa_na_nota():
    """Nome longo do catálogo não derruba uma busca curta que ele cobre."""
    coberta = ft.score("banana prata", "Banana, prata, crua")
    nao_coberta = ft.score("banana prata", "Banana, nanica, crua")
    assert coberta >= ft.MATCH_MIN_SCORE > nao_coberta


CATALOGO = [
    (1, "Banana, prata, crua"),
    (2, "Mandioca cozida"),
    (3, "Feijão, preto, cozido"),
    (4, "Arroz, tipo 1, cozido"),
    (5, "Frango cozido"),
    (6, "Ovo, de galinha, inteiro, cozido"),
]


def test_rank_candidates_com_o_catalogo():
    exato = ft.rank_candidates("banana prata crua", CATALOGO)
    assert exato[0].candidate_id == 1 and exato[0].score == 1.0 and exato[0].auto_accept is True

    sinonimo = ft.rank_candidates("aipim cozido", CATALOGO)
    assert sinonimo[0].candidate_id == 2 and sinonimo[0].score == 1.0 and sinonimo[0].auto_accept is True

    parcial = ft.rank_candidates("banana", CATALOGO)
    assert all(m.auto_accept is False for m in parcial)

    assert ft.rank_candidates("chocolate", CATALOGO) == ()


def test_busca_por_ovo_nao_casa_com_frango():
    ranking = ft.rank_candidates("ovo de galinha cozido", CATALOGO)
    assert ranking[0].candidate_id == 6
    assert 5 not in [m.candidate_id for m in ranking]  # "frango" abaixo do mínimo


def test_limiares_sao_inclusivos_e_ordenam_por_nota_e_nome(monkeypatch):
    notas = {"a": 0.6, "b": 0.85, "c": 0.5999, "d": 0.8499, "e": 0.85, "f": 1.0}
    monkeypatch.setattr(ft, "score", lambda _consulta, nome: notas[nome])
    ranking = ft.rank_candidates("q", [(nome, nome) for nome in "abcdef"])
    assert [m.name for m in ranking] == ["f", "b", "e", "d", "a"]  # "c" abaixo de 0,6; empate b/e pelo nome
    # Só o primeiro é pré-selecionado, e só com folga >= MATCH_AUTO_ACCEPT_MIN_MARGIN (1,0 − 0,85).
    assert {m.name: m.auto_accept for m in ranking} == {"f": True, "b": False, "e": False, "d": False, "a": False}
    json.dumps([m.to_dict() for m in ranking])


@pytest.mark.parametrize(
    ("notas", "aceita"),
    [
        ({"a": 0.85}, True),  # sozinho, no limiar
        ({"a": 0.95, "b": 0.85}, True),  # folga 0,10
        ({"a": 0.9, "b": 0.87}, False),  # folga 0,03 < 0,05: ambíguo
        ({"a": 0.8499, "b": 0.1}, False),  # abaixo do limiar
    ],
)
def test_pre_selecao_exige_folga_sobre_o_segundo(monkeypatch, notas, aceita):
    monkeypatch.setattr(ft, "MATCH_AUTO_ACCEPT_MIN_MARGIN", 0.05)
    monkeypatch.setattr(ft, "score", lambda _consulta, nome: notas[nome])
    primeiro = ft.rank_candidates("q", [(nome, nome) for nome in notas])[0]
    assert primeiro.name == "a" and primeiro.auto_accept is aceita


def test_order_for_search_traz_todos_ordenados_pela_mesma_nota():
    resultado = ft.order_for_search("banana", CATALOGO)
    assert len(resultado) == len(CATALOGO)  # o limiar de candidato não corta a lista da tela
    assert resultado[0].candidate_id == 1
    notas = [m.score for m in resultado]
    assert notas == sorted(notas, reverse=True)
    assert [m.candidate_id for m in ft.rank_candidates("banana", CATALOGO)] == [
        m.candidate_id for m in resultado if m.score >= ft.MATCH_MIN_SCORE
    ]


@pytest.mark.parametrize(
    ("busca", "termos"),
    [
        ("Arroz branco cozido", ("arroz", "tipo", "cozido")),  # "1" é curto para o FULLTEXT
        ("ovo", ("ovo",)),
        ("ov", ()),
        ("pão de sal", ("pao", "frances")),
        ("frango frango", ("frango",)),
        ("", ()),
        (None, ()),
    ],
)
def test_search_terms(busca, termos):
    assert ft.search_terms(busca) == termos
    assert all(len(t) >= ft.FULLTEXT_MIN_TOKEN_CHARS for t in ft.search_terms(busca))


@pytest.mark.parametrize(
    ("busca", "fulltext", "prefixo"),
    [
        ("Arroz branco cozido", "arroz* tipo* cozido*", "arroz tipo 1 cozido%"),
        ("ov", None, "ov%"),
        ("de", None, "de%"),  # só stopword: o texto fica
        ("!!!", None, None),
        (None, None, None),
    ],
)
def test_expressoes_da_busca(busca, fulltext, prefixo):
    assert ft.fulltext_boolean_query(busca) == fulltext
    assert ft.prefix_pattern(busca) == prefixo
    # Só [a-z0-9 ] da normalização + o operador: nada que o usuário digita vira operador/curinga.
    for expressao in (ft.fulltext_boolean_query(busca), ft.prefix_pattern(busca)):
        assert expressao is None or re.fullmatch(r"[a-z0-9 *%]+", expressao)


@pytest.mark.parametrize(
    ("pedido", "limite"),
    [(None, ft.SEARCH_RESULT_LIMIT_DEFAULT), ("x", ft.SEARCH_RESULT_LIMIT_DEFAULT), (0, 1), (5, 5), (10_000, ft.SEARCH_RESULT_LIMIT_MAX)],
)
def test_search_limit(pedido, limite):
    assert ft.search_limit(pedido) == limite
    assert ft.SEARCH_RESULT_LIMIT_DEFAULT <= ft.SEARCH_RESULT_LIMIT_MAX <= ft.SEARCH_CANDIDATE_POOL
