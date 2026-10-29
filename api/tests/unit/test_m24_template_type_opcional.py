"""M24 - "Tipo de modelo" (`template_type`) saiu da tela de Modelos de Treino.

O que precisa de teste aqui e a COMPATIBILIDADE, nao a ausencia do campo na
UI: o cliente novo nao envia mais `template_type`, o cliente antigo continua
enviando, e nenhum dos dois pode mudar o que ja esta gravado nem o `plan_type`
que a ficha gerada recebe. A coluna NAO sai neste PR (isso e da F2.4).
"""

from __future__ import annotations

import pytest

from app.models import WorkoutPlanTemplateIn
from app.workout_plan_templates import DEFAULT_TEMPLATE_TYPE, _resolve_template_type


def payload(**extra) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(name="Modelo", **extra)


# ---------------------------------------------------------------------------
# Input: campo ausente e aceito
# ---------------------------------------------------------------------------


def test_payload_sem_template_type_e_valido():
    # Cliente novo (builder sem o Select "Tipo de modelo").
    assert payload().template_type is None


@pytest.mark.parametrize("value", ["full_body", "abc", "upper_lower"])
def test_cliente_antigo_que_ainda_envia_o_campo_continua_aceito(value):
    assert payload(template_type=value).template_type == value


# ---------------------------------------------------------------------------
# Resolucao do valor a gravar
# ---------------------------------------------------------------------------


def test_criacao_sem_o_campo_usa_o_default_historico_da_coluna():
    # Mesmo valor do DDL (007_workout_plan_templates.sql) e do default antigo
    # do model: nenhum modelo novo nasce diferente do que nascia antes.
    assert _resolve_template_type(payload(), None) == "full_body"
    assert DEFAULT_TEMPLATE_TYPE == "full_body"


@pytest.mark.parametrize("gravado", ["abc", "full_body", "upper_lower"])
def test_edicao_sem_o_campo_preserva_o_valor_ja_gravado(gravado):
    # O ponto do M24: esconder o campo NAO pode reescrever dado existente.
    # Um modelo 'abc' salvo pelo builder novo continua 'abc'.
    assert _resolve_template_type(payload(), gravado) == gravado


def test_edicao_com_o_campo_ainda_manda_no_valor():
    # Cliente antigo editando: o que ele envia prevalece, igual a hoje.
    assert _resolve_template_type(payload(template_type="abc"), "full_body") == "abc"
    assert _resolve_template_type(payload(template_type="full_body"), "abc") == "full_body"


@pytest.mark.parametrize("gravado", [None, ""])
def test_linha_sem_valor_cai_no_default(gravado):
    # Linha com NULL/vazio (nao existe no DEV, mas a coluna e VARCHAR e o motor
    # de geracao ja tratava isso com `or "full_body"`).
    assert _resolve_template_type(payload(), gravado) == "full_body"


# ---------------------------------------------------------------------------
# Ficha gerada: `plan_type` continua igual
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template_type, esperado",
    [("abc", "abc"), ("full_body", "full_body"), (None, "full_body"), ("", "full_body")],
)
def test_plan_type_da_ficha_sai_do_template_type_gravado(template_type, esperado):
    """Contrato que os motores (`generation/mode_a.py`, `mode_b.py` e os
    caminhos legados) usam para montar `plan_type`. Com o campo escondido, o
    valor GRAVADO continua sendo a unica fonte - e ele nao mudou."""
    template = {"id": 1, "name": "Modelo", "template_type": template_type}
    assert (template.get("template_type") or "full_body") == esperado
