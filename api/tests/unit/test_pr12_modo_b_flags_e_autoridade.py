"""PR-12 - Modo B deterministico: flags, autoridade de tecnica, D9 e D14.

O que esta coberto aqui sao as decisoes que NAO precisam de banco:

- a flag `GENERATION_ENGINE` e, principalmente, a **dependencia operacional**
  entre ela e `PROGRESSION_ENGINE` (secao 5 do escopo do PR): ligar a geracao
  sem as trilhas alimentadas faria o motor prescrever sobre estado vazio, e o
  codigo recusa essa combinacao em vez de aceita-la em silencio;
- D9: series sao fixas pelo modelo, a F5 vira recomendacao;
- secao 5.9: hierarquia Modelo > Progressao > nenhuma, e tecnica fixa
  invalida REMOVIDA da prescricao;
- D14: erro so para item novo/alterado; item legado nao tocado vira warning.

A geracao/congelamento ponta a ponta esta em
`tests/integration/test_pr12_modo_b_deterministico.py`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from app import generation
from app import progression_state as ps
from app.domain import progression_engine as pe
from app.template_safety_validation import (
    CODE_RPE_ABOVE_CEILING,
    CODE_TECHNIQUE_INVALID,
    split_report_by_authorship,
)
from tests.support import compose_services
from tests.support import infra_flags
from tests.support import progression_fixtures as fx


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(generation.GENERATION_FLAG_ENV, raising=False)
    monkeypatch.delenv(ps.ENGINE_FLAG_ENV, raising=False)


# ---------------------------------------------------------------------------
# Flag GENERATION_ENGINE (secao 12) e a dependencia com PROGRESSION_ENGINE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor, modo",
    [
        (None, "legacy"),  # producao: variavel ausente -> legado
        ("", "legacy"),
        ("legacy", "legacy"),
        ("deterministic", "deterministic"),
        ("DETERMINISTIC", "deterministic"),
        (" deterministic ", "deterministic"),
        ("v2", "legacy"),  # valor de OUTRA flag nunca liga esta
        ("true", "legacy"),
    ],
)
def test_flag_generation_engine(monkeypatch, valor, modo):
    if valor is not None:
        monkeypatch.setenv(generation.GENERATION_FLAG_ENV, valor)
    assert generation.generation_mode() == modo


@pytest.mark.parametrize(
    "generation_engine, progression_engine, ligado",
    [
        (None, None, False),  # producao hoje
        (None, "v2", False),  # trilhas vivas, geracao ainda legada
        ("deterministic", None, False),  # <- a combinacao perigosa
        ("deterministic", "v1", False),
        ("deterministic", "shadow", False),  # sombra GRAVA, mas nao prescreve
        ("deterministic", "v2", True),  # unico caso ligado
        ("legacy", "v2", False),
    ],
)
def test_deterministico_exige_as_duas_flags(monkeypatch, generation_engine, progression_engine, ligado):
    """A prescricao do Modo B sai das trilhas da progressao v2. Sem
    `PROGRESSION_ENGINE=v2` ninguem as mantem atualizadas, e o motor
    prescreveria sobre estado vazio - toda trilha em calibracao, todo grupo em
    F1. Por isso as duas flags andam juntas."""
    if generation_engine:
        monkeypatch.setenv(generation.GENERATION_FLAG_ENV, generation_engine)
    if progression_engine:
        monkeypatch.setenv(ps.ENGINE_FLAG_ENV, progression_engine)
    assert generation.deterministic_enabled() is ligado


def test_combinacao_pela_metade_e_logada(monkeypatch, caplog):
    """Configuracao incompleta nunca e silenciosa: quem pediu
    `deterministic` e nao ligou a progressao ve `generation.flag_mismatch`."""
    monkeypatch.setenv(generation.GENERATION_FLAG_ENV, "deterministic")
    monkeypatch.setenv(ps.ENGINE_FLAG_ENV, "shadow")
    with caplog.at_level(logging.WARNING, logger="fitness.generation"):
        assert generation.deterministic_enabled() is False
    evento = json.loads(caplog.records[-1].message)
    assert evento["event"] == "generation.flag_mismatch"
    assert evento["progression_engine"] == "shadow"
    # sem PII no log estruturado (secao 14)
    assert set(evento) == {"event", "generation_engine", "progression_engine", "reason"}


def _compose_service_env(nome: str) -> str:
    """Trecho do `docker-compose.yml` que pertence a um servico. O parser
    (textual, sem PyYAML, que nao esta em requirements) virou
    `tests/support/compose_services.py` quando um segundo teste de paridade
    DEV x producao passou a precisar do mesmo recorte."""
    return compose_services.service_block(nome)


@pytest.mark.parametrize("servico", ["api", "lambda-ai"])
def test_dev_liga_as_duas_flags_nos_dois_servicos(servico):
    """Paridade com o mapa de producao, servico por servico.

    `api` e o equivalente da `musclefit-api-core`; `lambda-ai` e o emulador que
    hospeda `app.lambda_plan_generator.handler`, equivalente da
    `musclefit-plan-generator` - e onde a GERACAO roda. Antes do PR-14 o
    `lambda-ai` nao tinha as flags: DEV gerava pelo motor legado e congelava
    pelo deterministico, a configuracao pela metade dentro do proprio DEV."""
    bloco = _compose_service_env(servico)
    assert "PROGRESSION_ENGINE: v2" in bloco
    assert "GENERATION_ENGINE: deterministic" in bloco


# ---------------------------------------------------------------------------
# PR-14: o contrato de `infra/`
#
# Ate o PR-13 estes testes garantiam que as flags NAO estavam em `infra/`
# (producao nos defaults, deploy sem mudanca de comportamento). Com o replay
# ja executado em producao (2026-09-18: 106 trilhas, 19 estados de grupo, 179
# decisoes), o PR-14 liga as duas. A verificacao nao foi apagada - foi
# invertida, e continua sendo o que impede a CONFIGURACAO PELA METADE de
# passar no CI.
# ---------------------------------------------------------------------------


def test_infra_define_as_duas_flags_num_local_unico():
    """As duas saem do MESMO `locals`. Valores separados em arquivos
    diferentes seriam a receita para ligar a geracao e esquecer a
    progressao - exatamente o cenario que `deterministic_enabled()` recusa."""
    if not infra_flags.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    assert infra_flags.flag_values() == {
        "PROGRESSION_ENGINE": "v2",
        "GENERATION_ENGINE": "deterministic",
    }


def test_flags_apenas_nas_lambdas_que_as_leem():
    """Mapa Lambda x flag, verificado pelo CI.

    Recebem: `musclefit-api-core` (hook do `finish` +
    `_freeze_plan_prescriptions`) e `musclefit-plan-generator` (a geracao em
    si, `mode_a`/`mode_b`).

    NAO recebem: engines, workout-ai, cardio-agent, reports e migrations -
    nenhum caminho que elas executam le as flags. Em especial a migrations, que
    roda o replay: ele e independente de flag de proposito.

    Os dois lados importam. Faltar numa Lambda que le a flag e bug silencioso;
    sobrar numa que nao le sugere uma dependencia que nao existe."""
    if not infra_flags.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    assert infra_flags.lambdas_com_flags() == {
        "musclefit_api_core",
        "musclefit_plan_generator",
    }


def test_nenhuma_outra_tf_escreve_o_nome_das_flags():
    """Uma unica fonte de verdade: `lambda_motores_deterministicos.tf`. O
    rollback (trocar para `v1`/`legacy`, ou remover) precisa ser um lugar so."""
    if not infra_flags.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    for flag, arquivos in infra_flags.tf_files_que_escrevem_flag().items():
        assert arquivos == ["lambda_motores_deterministicos.tf"], (
            f"{flag} definida fora do locals unico: {arquivos}"
        )


def test_substituicao_por_grupo_muscular_saiu_do_motor_novo():
    """D13: `_recommend_for_group` (que trocava o EXERCICIO por outro do mesmo
    grupo) nao existe no motor novo. Ele so sobrevive no modulo legado, que
    sai no PR-19."""
    from app.generation import mode_b, mode_b_legacy

    assert not hasattr(mode_b, "_recommend_for_group")
    assert "recommend_for_group" not in Path(mode_b.__file__).read_text(encoding="utf-8").replace(
        "`_recommend_for_group`", ""
    )
    assert hasattr(mode_b_legacy, "_recommend_for_group")


# ---------------------------------------------------------------------------
# D9 - series fixas pelo modelo
# ---------------------------------------------------------------------------


def test_f5_no_modo_b_vira_recomendacao_e_o_grupo_segue_para_f6():
    """Tabela da secao 5.6, linha F5: com series fixas pelo modelo a +1 serie
    nao acontece - `blocked_by=MODEL_FIXED_SETS`, recomendacao de edicao ao PT
    e o grupo vai para F6."""
    step = pe.next_step(
        fx.group(phase="F4"),
        [fx.track(sets_count=3)],
        now=pe.datetime.now(),
        model_fixed_sets=True,
    )
    assert step.phase_after == pe.F6
    assert step.pending_action is None
    assert step.blocked_by == (pe.BLOCKED_MODEL_FIXED_SETS,)
    assert step.recommendation


def test_prescricao_nunca_altera_as_series_do_modelo():
    """Mesmo com `pending_action=ADD_SET` pendente no grupo, o Modo B
    prescreve as series do MODELO."""
    decision = pe.prescribe(
        fx.track(sets_count=3),
        fx.group(phase="F4", pending_action=pe.ADD_SET),
        fx.safety(),
        model_fixed_sets=True,
    )
    assert decision.suggested_sets == 3
    assert decision.decision == pe.HOLD
    assert pe.BLOCKED_MODEL_FIXED_SETS in decision.blocked_by
    assert decision.can_progress is False


def test_sem_modelo_fixo_a_serie_entra():
    """Controle do teste acima: fora do Modo B a mesma pendencia adiciona a
    serie (a diferenca e o modelo, nao a progressao)."""
    decision = pe.prescribe(
        fx.track(sets_count=3), fx.group(phase="F4", pending_action=pe.ADD_SET), fx.safety()
    )
    assert decision.suggested_sets == 4
    assert decision.decision == pe.ADD_SET


# ---------------------------------------------------------------------------
# Secao 5.9 - hierarquia de autoridade da tecnica
# ---------------------------------------------------------------------------


def test_tecnica_do_modelo_vence_a_da_progressao():
    decision = pe.prescribe(
        fx.track(technique_code="rest_pause"),
        # F4 efetivada: a tecnica da trilha e prescricao legitima da progressao
        fx.group(phase="F4", technique_applied_at=datetime(2026, 9, 1)),
        fx.safety(max_safe_rpe=10),
        catalog=fx.CATALOG,
        model_technique="drop_set",
        model_fixed_sets=True,
    )
    assert decision.advanced_technique == "drop_set"
    assert pe.EVIDENCE_TECHNIQUE_OVERRIDDEN_BY_MODEL in decision.evidence["technique_authority"]


def test_tecnica_fixa_incompativel_e_removida_da_prescricao():
    """Tecnica marcada INCOMPATIVEL na matriz do par: sai da prescricao. O
    modelo continua salvo; quem avisa e a ficha."""
    decision = pe.prescribe(
        fx.track(),
        fx.group(phase="F4"),
        fx.safety(max_safe_rpe=10, techniques={"rest_pause": 1, "drop_set": 0}),
        catalog=fx.CATALOG,
        model_technique="drop_set",
        model_fixed_sets=True,
    )
    assert decision.advanced_technique is None
    assert any("MODEL_TECHNIQUE_REMOVED_BY_SAFETY" in item for item in decision.evidence["technique_authority"])


def test_tecnica_fixa_que_exige_falha_em_par_proibido_e_removida():
    """Validacao DEFENSIVA da secao 5.9: `drop_set` esta marcada compativel na
    matriz, mas exige falha, e o par tem teto 9 (falha proibida)."""
    safety = fx.safety(max_safe_rpe=9)
    assert "drop_set" in {item.code for item in safety.blocked_techniques}
    decision = pe.prescribe(
        fx.track(),
        fx.group(phase="F4"),
        safety,
        catalog=fx.CATALOG,
        model_technique="drop_set",
        model_fixed_sets=True,
    )
    assert decision.advanced_technique is None


def test_tecnica_fixa_valida_permanece():
    decision = pe.prescribe(
        fx.track(),
        fx.group(phase="F4"),
        fx.safety(max_safe_rpe=10),
        catalog=fx.CATALOG,
        model_technique="rest_pause",
        model_fixed_sets=True,
    )
    assert decision.advanced_technique == "rest_pause"


# ---------------------------------------------------------------------------
# rpe_target do modelo e INTENCAO (secao 6.2 passo 3)
# ---------------------------------------------------------------------------


def test_rpe_10_do_modelo_em_par_proibido_e_clampado_sem_bloquear():
    decision = pe.prescribe(
        fx.track(), fx.group(), fx.safety(max_safe_rpe=9), model_fixed_sets=True, legacy_rpe_target=10
    )
    assert decision.effective_rpe_target == 9
    assert decision.effective_rpe_ceiling == 9
    assert decision.failure_allowed is False
    clamp = decision.evidence["safety_clamps"][0]
    assert (clamp["field"], clamp["from"], clamp["to"]) == ("rpe_target", 10, 9)
    # e a geracao NAO foi bloqueada por isso
    assert decision.decision != pe.SKIP
    assert decision.suggested_sets == 3


def test_rpe_do_modelo_abaixo_do_teto_e_respeitado():
    """O teto corta para baixo e nunca exige nada (D4 revisada em
    2026-09-18): alvo 8 num par com teto 10 continua 8."""
    decision = pe.prescribe(
        fx.track(), fx.group(), fx.safety(max_safe_rpe=10), model_fixed_sets=True, legacy_rpe_target=8
    )
    assert decision.effective_rpe_target == 8
    assert decision.effective_rpe_ceiling == 10
    assert "safety_clamps" not in decision.evidence


# ---------------------------------------------------------------------------
# D14 - erro para item novo/alterado, warning para legado nao tocado
# ---------------------------------------------------------------------------


def _slot(items):
    return {"sequence": 1, "name": "A", "items": items}


def _item(order, **kwargs):
    base = {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": 10,
        "mapping_id": 1001,
        "equipment_id": 5,
        "advanced_technique": None,
        "rpe_target": None,
    }
    base.update(kwargs)
    return base


def test_d14_item_legado_nao_tocado_so_avisa():
    salvo = [_slot([_item(1, advanced_technique="drop_set")])]
    report = [{"slot_sequence": 1, "item_order": 1, "code": CODE_TECHNIQUE_INVALID, "message": "x"}]
    errors, warnings = split_report_by_authorship(report, salvo, salvo)
    assert errors == []
    assert len(warnings) == 1


def test_d14_item_alterado_bloqueia_o_save():
    salvo = [_slot([_item(1, advanced_technique="drop_set")])]
    novo = [_slot([_item(1, advanced_technique="rest_pause")])]
    report = [{"slot_sequence": 1, "item_order": 1, "code": CODE_TECHNIQUE_INVALID, "message": "x"}]
    errors, warnings = split_report_by_authorship(report, novo, salvo)
    assert len(errors) == 1
    assert warnings == []


def test_d14_troca_de_equipamento_conta_como_alteracao():
    """Mudar o equipamento muda o par - e a seguranca e do PAR."""
    salvo = [_slot([_item(1, rpe_target="10")])]
    novo = [_slot([_item(1, rpe_target="10", equipment_id=9)])]
    report = [{"slot_sequence": 1, "item_order": 1, "code": CODE_RPE_ABOVE_CEILING, "message": "x"}]
    errors, _ = split_report_by_authorship(report, novo, salvo)
    assert len(errors) == 1


def test_d14_na_criacao_tudo_e_novo():
    novo = [_slot([_item(1, advanced_technique="drop_set")])]
    report = [{"slot_sequence": 1, "item_order": 1, "code": CODE_TECHNIQUE_INVALID, "message": "x"}]
    errors, warnings = split_report_by_authorship(report, novo, None)
    assert len(errors) == 1 and warnings == []


def test_d14_editar_outro_item_nao_trava_o_legado():
    """O caso que motivou a D14: o PT renomeia/acrescenta um item e o item
    legado incompativel, intocado, nao pode impedir o save."""
    salvo = [_slot([_item(1, advanced_technique="drop_set")])]
    novo = [_slot([_item(1, advanced_technique="drop_set"), _item(2, mapping_id=1002)])]
    report = [{"slot_sequence": 1, "item_order": 1, "code": CODE_TECHNIQUE_INVALID, "message": "x"}]
    errors, warnings = split_report_by_authorship(report, novo, salvo)
    assert errors == [] and len(warnings) == 1
