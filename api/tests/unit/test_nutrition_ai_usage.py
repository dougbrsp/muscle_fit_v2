"""Uso e cota de IA da nutrição (dono `app/domain/nutrition/ai_usage.py`, L5a):
vocabulário, cota por feature, job type, custo por modelo e os textos do 429."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain import nutrition
from app.domain.nutrition import ai_usage as ai
from app.domain.nutrition import labels as lb
from app.domain.nutrition.ai_usage import AiFeature, AiUsageStatus

DONO = Path(ai.__file__)


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------


def test_features_e_status_do_plano():
    assert [f.value for f in AiFeature] == ["meal_parse", "label_photo"]
    assert [s.value for s in AiUsageStatus] == ["pending", "succeeded", "failed"]
    assert ai.TERMINAL_STATUSES == {AiUsageStatus.SUCCEEDED, AiUsageStatus.FAILED}
    assert nutrition.ai_usage is ai  # reexportado pelo pacote


@pytest.mark.parametrize("valor", ["meal_parse", AiFeature.MEAL_PARSE])
def test_normalize_feature_aceita_codigo_e_enum(valor):
    assert ai.normalize_feature(valor) is AiFeature.MEAL_PARSE


@pytest.mark.parametrize("valor", ["MEAL_PARSE", "meal_photo", "", None, 3])
def test_feature_desconhecida_e_erro_de_programacao(valor):
    with pytest.raises(ValueError, match="feature de IA desconhecida"):
        ai.normalize_feature(valor)


def test_worker_fecha_so_com_status_terminal():
    assert ai.normalize_terminal_status("succeeded") is AiUsageStatus.SUCCEEDED
    assert ai.normalize_terminal_status(AiUsageStatus.FAILED) is AiUsageStatus.FAILED
    with pytest.raises(ValueError, match="o worker fecha só com"):
        ai.normalize_terminal_status(AiUsageStatus.PENDING)
    with pytest.raises(ValueError, match="desconhecido"):
        ai.normalize_terminal_status("done")


@pytest.mark.parametrize(
    ("status", "codigo", "esperado"),
    [
        ("pending", None, True),
        (AiUsageStatus.PENDING, "qualquer", True),
        ("failed", "worker_lost", True),  # presunção do sweep: o relato real vence
        (AiUsageStatus.FAILED, "worker_lost", True),
        ("failed", "OPENAI_TIMEOUT", False),  # fechado por um worker
        ("failed", None, False),
        ("succeeded", None, False),
        ("succeeded", "worker_lost", False),
    ],
)
def test_quem_o_worker_pode_fechar(status, codigo, esperado):
    assert ai.worker_may_close(status, codigo, presumed_lost_code="worker_lost") is esperado
    assert ai.OPEN_STATUS is AiUsageStatus.PENDING and ai.SWEEP_PRESUMED_STATUS is AiUsageStatus.FAILED


@pytest.mark.parametrize(("valor", "esperado"), [(0, 0), (1500, 1500), (-1, None), (True, None), ("3", None),
                                                 (2.5, None), (None, None)])
def test_count_or_none(valor, esperado):
    assert ai.count_or_none(valor) == esperado


# ---------------------------------------------------------------------------
# Cota
# ---------------------------------------------------------------------------


def test_cota_por_feature_vem_das_constantes_nomeadas_do_plano():
    # Plano §8.3: texto 30/dia, rótulo 10/dia (defaults de desenho).
    assert ai.MEAL_PARSE_DAILY_QUOTA == 30 and ai.LABEL_PHOTO_DAILY_QUOTA == 10
    assert dict(ai.DAILY_QUOTA) == {AiFeature.MEAL_PARSE: 30, AiFeature.LABEL_PHOTO: 10}
    assert set(ai.DAILY_QUOTA) == set(AiFeature)  # toda feature tem cota
    assert ai.daily_quota("label_photo") == ai.LABEL_PHOTO_DAILY_QUOTA
    with pytest.raises(TypeError):
        ai.DAILY_QUOTA[AiFeature.MEAL_PARSE] = 99  # type: ignore[index]  # imutável


@pytest.mark.parametrize("feature", list(AiFeature))
def test_borda_da_cota_por_feature(feature):
    limite = ai.DAILY_QUOTA[feature]
    antes = ai.quota_status(feature, limite - 1)
    assert antes.allowed and antes.remaining == 1 and antes.limit == limite
    no_limite = ai.quota_status(feature, limite)
    assert not no_limite.allowed and no_limite.remaining == 0
    acima = ai.quota_status(feature, limite + 3)
    assert not acima.allowed and acima.remaining == 0  # nunca negativo
    assert ai.quota_status(feature, 0).to_dict() == {
        "feature": feature.value, "limit": limite, "used": 0, "remaining": limite, "allowed": True,
    }


@pytest.mark.parametrize("usadas", [-1, None, "2", True])
def test_contagem_invalida_recusa(usadas):
    with pytest.raises(ValueError, match="contagem de uso inválida"):
        ai.quota_status(AiFeature.MEAL_PARSE, usadas)


# ---------------------------------------------------------------------------
# Job type de cada feature
# ---------------------------------------------------------------------------


def test_job_type_de_cada_feature_e_o_do_plano():
    assert dict(ai.FEATURE_JOB_TYPE) == {
        AiFeature.MEAL_PARSE: "nutrition-meal-parse",
        AiFeature.LABEL_PHOTO: "nutrition-label-ocr",
    }
    assert set(ai.FEATURE_JOB_TYPE) == set(AiFeature)
    assert len(set(ai.FEATURE_JOB_TYPE.values())) == len(AiFeature)
    for feature, job_type in ai.FEATURE_JOB_TYPE.items():
        assert ai.job_type_for(feature.value) == job_type
        assert ai.feature_for_job_type(job_type) is feature
    assert ai.feature_for_job_type("periodization") is None and ai.feature_for_job_type(None) is None


@pytest.mark.parametrize("nome", ["MEAL_PARSE_JOB_TYPE", "LABEL_PHOTO_JOB_TYPE"])
def test_job_type_e_constante_que_o_teste_do_m73_resolve(nome):
    """`test_m73_async_jobs_worker_lost` resolve `create_job(<CONSTANTE>)` lendo
    `NOME = "valor"` (sem anotação) dos módulos de `api/app`."""
    texto = DONO.read_text(encoding="utf-8")
    achado = re.search(rf'^{nome}\s*=\s*"([^"]*)"', texto, re.MULTILINE)
    assert achado and achado.group(1) == getattr(ai, nome)


# ---------------------------------------------------------------------------
# Custo
# ---------------------------------------------------------------------------


def test_tabela_de_precos_tem_fonte_e_data():
    assert ai.PRICE_SOURCE_URL.startswith("https://")
    assert date.fromisoformat(ai.PRICES_CHECKED_ON)
    for modelo, preco in ai.MODEL_PRICES_USD_CENTS_PER_MTOKEN.items():
        assert modelo == modelo.strip().lower()
        assert 0 < preco.cached_input_cents <= preco.input_cents < preco.output_cents, modelo
        campos = {"input_cents", "cached_input_cents", "output_cents"}
        # Escrita no cache: so quem cobra a parte (Anthropic, 1,25x a entrada).
        if preco.cache_write_cents is not None:
            assert preco.cache_write_cents == preco.input_cents * ai.ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER, modelo
            campos.add("cache_write_cents")
        assert set(preco.to_dict()) == campos


@pytest.mark.parametrize(
    ("modelo", "esperado"),
    [
        ("gpt-5.4-mini", "gpt-5.4-mini"),
        ("gpt-5.4-mini-2026-03-17", "gpt-5.4-mini"),  # snapshot datado que a OpenAI devolve
        ("  GPT-4o-mini-2024-07-18 ", "gpt-4o-mini"),
        ("gpt-5.4", "gpt-5.4"),
        ("gpt-5.4-pro", None),
        ("gpt-5.4-mini-high", None),
        ("", None),
        (None, None),
    ],
)
def test_preco_por_modelo(modelo, esperado):
    preco = ai.price_for_model(modelo)
    assert preco == (ai.MODEL_PRICES_USD_CENTS_PER_MTOKEN[esperado] if esperado else None)


def test_custo_em_usd_pela_tabela():
    # gpt-5.4-mini: 2.000 de entrada a US$ 0,75/1M + 500 de saída a US$ 4,50/1M.
    assert ai.estimate_cost_usd("gpt-5.4-mini", 2000, 500) == Decimal("0.003750")
    # Metade da entrada do cache (US$ 0,075/1M).
    assert ai.estimate_cost_usd("gpt-5.4-mini-2026-03-17", 2000, 500, 1000) == Decimal("0.003075")
    # 1 milhão de tokens de entrada do gpt-5.4 = US$ 2,50 exatos.
    assert ai.estimate_cost_usd("gpt-5.4", 1_000_000, 0) == Decimal("2.500000")
    custo = ai.estimate_cost_usd("gpt-5.4-nano", 1, 0)
    assert isinstance(custo, Decimal) and custo == Decimal("0.000000")  # 6 casas (coluna DECIMAL(10,6))
    assert ai.estimate_cost_usd("gpt-4o-mini", 3, 1).as_tuple().exponent == -ai.COST_USD_DECIMAL_PLACES


def test_cache_maior_que_a_entrada_e_cortado_e_cache_invalido_e_ignorado():
    cheio = ai.estimate_cost_usd("gpt-5.4-mini", 1000, 0, 1000)
    assert ai.estimate_cost_usd("gpt-5.4-mini", 1000, 0, 5000) == cheio
    assert ai.estimate_cost_usd("gpt-5.4-mini", 1000, 0, -5) == ai.estimate_cost_usd("gpt-5.4-mini", 1000, 0)


@pytest.mark.parametrize(
    ("modelo", "entrada", "saida"),
    [("modelo-sem-preco", 100, 100), ("gpt-5.4-mini", None, 100), ("gpt-5.4-mini", 100, None), (None, 1, 1),
     ("gpt-5.4-mini", -1, 1)],
)
def test_sem_preco_ou_sem_token_o_custo_e_desconhecido(modelo, entrada, saida):
    assert ai.estimate_cost_usd(modelo, entrada, saida) is None  # nunca um preço inventado


# ---------------------------------------------------------------------------
# Textos (dono: labels)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feature", list(AiFeature))
def test_texto_do_429_leva_a_cota_do_dono(feature):
    texto = lb.ai_quota_exceeded_label(feature.value)
    assert str(ai.DAILY_QUOTA[feature]) in texto and texto.endswith("Tente de novo amanhã.")
    assert lb.AI_FEATURE_LABELS[feature]


def test_rotulos_de_feature_e_codigo_do_erro():
    assert set(lb.AI_FEATURE_LABELS) == set(AiFeature)
    assert len(set(lb.AI_FEATURE_LABELS.values())) == len(AiFeature)
    assert lb.limitation_label(ai.ERR_AI_QUOTA_EXCEEDED) != ai.ERR_AI_QUOTA_EXCEEDED
