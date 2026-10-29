"""Divida de observabilidade: os tres eventos da secao 14 que nunca eram
emitidos - `generation.fallback`, `progression.transition` e
`progression.blocked`.

O que estes testes cobram, alem de "o evento saiu":

1. **os campos certos** - o contrato da secao 14 e `user_id` (id numerico),
   `plan_id`/`template_id`, `engine_version`, `reason_code`;
2. **o que NAO pode vazar** - "Sem notas livres, dor detalhada ou instrucoes do
   usuario no log (ficam so no banco)". Por isso quase todo teste aqui termina
   conferindo o CONJUNTO EXATO de chaves do payload, e nao so as que interessam:
   um campo novo indevido quebra o teste.

Mesmo padrao dos eventos que ja existiam (`safety.clamp`,
`progression.shadow_evaluated`): `caplog` no logger, uma linha, `json.loads`.
"""

from __future__ import annotations

import inspect
import json
import logging

import pytest

from app import generation
from app import plan_generation_core as core
from app import progression_state as ps
from app.domain.progression_engine import ProgressionDecision
from app.generation import fallback_log
from app.generation import mode_a, mode_b

EVENTS_LOGGER = "musclefit.events"

# Qualquer coisa deste conjunto no log e vazamento: sao os dados que a secao 14
# manda ficar so no banco.
PROIBIDO = (
    "dor",
    "pain",
    "joelho",
    "notes",
    "note",
    "reason\":",  # `reason` livre (o permitido e `reason_code`)
    "evidence",
    "Supino",
    "Peitoral",
)


def _eventos(caplog, nome: str) -> list[dict]:
    saida = []
    for record in caplog.records:
        if record.name != EVENTS_LOGGER:
            continue
        payload = json.loads(record.message)
        if payload.get("event") == nome:
            saida.append(payload)
    return saida


def _sem_dado_proibido(payload: dict) -> None:
    linha = json.dumps(payload, ensure_ascii=False)
    for termo in PROIBIDO:
        assert termo not in linha, f"vazou {termo!r} no log: {linha}"


@pytest.fixture(autouse=True)
def _captura(caplog):
    # UMA chamada: `set_level` mexe tambem no nivel do handler de captura, e
    # uma segunda chamada com WARNING esconderia os eventos, que saem em INFO.
    caplog.set_level(logging.INFO)


@pytest.fixture(autouse=True)
def _env_limpo(monkeypatch):
    monkeypatch.delenv(generation.GENERATION_FLAG_ENV, raising=False)
    monkeypatch.delenv(ps.ENGINE_FLAG_ENV, raising=False)


# ---------------------------------------------------------------------------
# generation.fallback: o formato
# ---------------------------------------------------------------------------


def test_fallback_tem_os_campos_da_secao_14_e_omite_os_vazios(caplog):
    fallback_log.log_generation_fallback(
        reason_code="NO_EQUIPMENT_FOR_GROUP",
        user_id=7,
        template_id=42,
        engine_version="mode-a-1.0.0",
        volume_mode="percent_budget",
        muscle_group_id=3,
    )
    (evento,) = _eventos(caplog, "generation.fallback")
    assert evento == {
        "event": "generation.fallback",
        "user_id": 7,
        "template_id": 42,
        "engine_version": "mode-a-1.0.0",
        "volume_mode": "percent_budget",
        "muscle_group_id": 3,
        "reason_code": "NO_EQUIPMENT_FOR_GROUP",
    }
    # `exercise_id` era None: campo ausente, nao nulo (o metric filter nao
    # precisa distinguir os dois casos).
    assert "exercise_id" not in evento


def test_fallback_leva_id_numerico_do_usuario_nunca_email_ou_nome(caplog):
    """`user_id` e id numerico - a secao 14 e explicita. Um objeto de usuario
    passado por engano vira int ou estoura; nunca vira string no log."""
    fallback_log.log_generation_fallback(reason_code="X", user_id="7")
    (evento,) = _eventos(caplog, "generation.fallback")
    assert evento["user_id"] == 7 and isinstance(evento["user_id"], int)


def test_fallbacks_da_auditoria_viram_uma_linha_por_entrada_sem_texto_livre(caplog):
    """`generation_audit.fallbacks[]` carrega nome de grupo e deficit; o LOG
    leva so id + codigo."""
    fallback_log.log_generation_fallbacks(
        [
            {
                "muscle_group_id": 3,
                "muscle_group_name": "Peitoral",
                "reason": "ACTIVATION_DEFICIT",
                "deficit": 2.5,
            },
            {"muscle_group_id": 5, "exercise_id": 91, "reason": "SETS_REDUCED_FOR_SECONDARY_BUDGET"},
            {"muscle_group_id": 9},  # sem motivo: nao ha o que medir
        ],
        user_id=7,
        template_id=42,
        engine_version="mode-a-1.0.0",
        volume_mode="percent_budget",
    )
    eventos = _eventos(caplog, "generation.fallback")
    assert [e["reason_code"] for e in eventos] == [
        "ACTIVATION_DEFICIT",
        "SETS_REDUCED_FOR_SECONDARY_BUDGET",
    ]
    for evento in eventos:
        _sem_dado_proibido(evento)
        assert set(evento) <= {
            "event",
            "user_id",
            "template_id",
            "engine_version",
            "volume_mode",
            "muscle_group_id",
            "exercise_id",
            "reason_code",
        }
    assert "deficit" not in json.dumps(eventos)


def test_observabilidade_nunca_derruba_a_geracao(caplog):
    """Um valor nao serializavel no payload nao pode virar 500 na geracao:
    `log_event` cai em `str()` e a chamada volta normal."""
    fallback_log.log_generation_fallback(reason_code="X", user_id=1, extra_obj=object())
    (evento,) = _eventos(caplog, "generation.fallback")
    assert evento["reason_code"] == "X"


# ---------------------------------------------------------------------------
# generation.fallback: os pontos de emissao reais
# ---------------------------------------------------------------------------


def test_flags_pela_metade_sao_fallback(monkeypatch, caplog):
    """`GENERATION_ENGINE=deterministic` sem `PROGRESSION_ENGINE=v2` cai no
    motor legado - caminho alternativo ao pretendido, portanto fallback."""
    monkeypatch.setenv(generation.GENERATION_FLAG_ENV, "deterministic")
    monkeypatch.setenv(ps.ENGINE_FLAG_ENV, "shadow")

    assert generation.deterministic_enabled() is False

    (evento,) = _eventos(caplog, "generation.fallback")
    assert evento == {
        "event": "generation.fallback",
        "engine_version": "deterministic",
        "reason_code": generation.FALLBACK_FLAG_MISMATCH,
        "progression_engine": "shadow",
    }
    # o evento antigo continua saindo, para nao quebrar quem ja filtra por ele
    assert any("generation.flag_mismatch" in r.message for r in caplog.records)


@pytest.mark.parametrize("progression", ["v2"])
def test_configuracao_correta_nao_emite_fallback(monkeypatch, caplog, progression):
    monkeypatch.setenv(generation.GENERATION_FLAG_ENV, "deterministic")
    monkeypatch.setenv(ps.ENGINE_FLAG_ENV, progression)
    assert generation.deterministic_enabled() is True
    assert _eventos(caplog, "generation.fallback") == []


def test_legado_configurado_nao_e_fallback(monkeypatch, caplog):
    """`GENERATION_ENGINE` ausente e o DEFAULT de producao: o motor legado
    rodando por configuracao nao e degradacao e nao pode poluir a metrica."""
    assert generation.deterministic_enabled() is False
    assert _eventos(caplog, "generation.fallback") == []


def _payload(template_id: int = 42):
    from app.models import WorkoutPlanGenerateIn

    return WorkoutPlanGenerateIn(template_id=template_id)


def test_dispatcher_loga_fallback_no_branch_do_modelo_v1(monkeypatch, caplog):
    template = {"id": 42, "name": "Modelo legado", "volume_mode": "percent_budget"}
    monkeypatch.setattr(core, "resolve_template_and_slot", lambda *a, **k: (template, {"sequence": 1}))
    monkeypatch.setattr(core, "generate_plan_draft_from_template", lambda **kwargs: {"name": "ficha"})

    assert core.dispatch_plan_generation(_payload(), user_id=7) == {"name": "ficha"}

    (evento,) = _eventos(caplog, "generation.fallback")
    assert evento == {
        "event": "generation.fallback",
        "user_id": 7,
        "template_id": 42,
        "reason_code": core.FALLBACK_TEMPLATE_V1,
    }


def test_dispatcher_nao_loga_fallback_no_caminho_pretendido(monkeypatch, caplog):
    """Modelo com `training_type_id` -> Modo A. Nenhum fallback."""
    template = {"id": 42, "volume_mode": "percent_budget", "training_type_id": 3}
    monkeypatch.setattr(core, "resolve_template_and_slot", lambda *a, **k: (template, {"sequence": 1}))
    monkeypatch.setattr(mode_a, "generate_plan_draft_from_template_a", lambda **kwargs: {"name": "ficha"})

    assert core.dispatch_plan_generation(_payload(), user_id=7) == {"name": "ficha"}
    assert _eventos(caplog, "generation.fallback") == []


def test_modo_a_espelha_os_fallbacks_da_auditoria():
    """O Modo A ja montava `fallbacks[]` para a auditoria; o log sai da MESMA
    lista, junto com o `generation.completed`."""
    fonte = inspect.getsource(mode_a.generate_plan_draft_from_template_a)
    assert "log_generation_fallbacks(\n            fallbacks," in fonte
    assert fonte.index("log_generation_fallbacks") < fonte.index('"event": "generation.completed"')


def test_modo_b_loga_fallback_quando_o_item_fica_sem_equipamento():
    """Caminho (b) da D13: nenhum equipamento seguro/compativel na academia, o
    item sai exigindo escolha do usuario. A substituicao automatica (caminho
    (a)) NAO e fallback - entrega um par utilizavel e ja e `substitutions[]`."""
    fonte = inspect.getsource(mode_b.generate_plan_draft_from_equipment_template)
    assert "if equipment.requires_user_choice:" in fonte
    trecho = fonte.split("if equipment.requires_user_choice:")[1][:700]
    assert "log_generation_fallback(" in trecho
    assert mode_b.FALLBACK_EQUIPMENT_UNRESOLVED == "EQUIPMENT_UNRESOLVED"
    assert mode_b.FALLBACK_INCOHERENT_PAIR == "INCOHERENT_PAIR_DISCARDED"
    # nada de nome de exercicio/equipamento indo para o log
    assert "exercise_name" not in trecho and "equipment_name" not in trecho


# ---------------------------------------------------------------------------
# progression.transition / progression.blocked
# ---------------------------------------------------------------------------


def _decisao(**kwargs) -> ProgressionDecision:
    base = dict(
        group_state_id=1,
        muscle_group_id=3,
        track_id=None,
        phase_before="F3",
        phase_after="F3",
        decision="HOLD",
        # os dois campos que NAO podem vazar:
        reason="Dor no joelho relatada na serie 2; mantem a prescricao.",
        evidence={"tracks": {"91:5": {"outcome": "NEUTRAL", "reason": "pain_reported"}}},
    )
    base.update(kwargs)
    return ProgressionDecision(**base)


def test_transicao_de_fase_do_grupo_vira_evento(caplog):
    ps.log_decision_events(
        7, 4242, [_decisao(phase_before="F3", phase_after="F4", decision="APPLY_TECHNIQUE")]
    )
    (evento,) = _eventos(caplog, "progression.transition")
    assert evento == {
        "event": "progression.transition",
        "user_id": 7,
        "activity_id": 4242,
        "muscle_group_id": 3,
        "phase_before": "F3",
        "phase_after": "F4",
        "engine_version": ps.ENGINE_VERSION,
        "reason_code": "APPLY_TECHNIQUE",
    }
    _sem_dado_proibido(evento)


def test_sessao_sem_mudanca_de_fase_nao_emite_transicao(caplog):
    ps.log_decision_events(7, 4242, [_decisao()])
    assert _eventos(caplog, "progression.transition") == []


def test_bloqueio_vira_evento_com_o_motivo(caplog):
    ps.log_decision_events(
        7, 4242, [_decisao(blocked_by=("NO_ELIGIBLE_TECHNIQUE", "NEXT_ELIGIBLE_PROGRESSION_AT"))]
    )
    (evento,) = _eventos(caplog, "progression.blocked")
    assert evento == {
        "event": "progression.blocked",
        "user_id": 7,
        "activity_id": 4242,
        "muscle_group_id": 3,
        "phase": "F3",
        "engine_version": ps.ENGINE_VERSION,
        "reason_code": "NO_ELIGIBLE_TECHNIQUE",
        "blocked_by": ["NO_ELIGIBLE_TECHNIQUE", "NEXT_ELIGIBLE_PROGRESSION_AT"],
    }
    _sem_dado_proibido(evento)


def test_decisao_sem_bloqueio_nao_emite_blocked(caplog):
    ps.log_decision_events(7, 4242, [_decisao()])
    assert _eventos(caplog, "progression.blocked") == []


def test_transicao_e_bloqueio_podem_sair_juntos_da_mesma_decisao(caplog):
    """F5 -> F6 sem acao pendente, barrada pelo teto de ativacao: a fase anda E
    o motivo do bloqueio existe. Sao dois fatos e duas linhas."""
    ps.log_decision_events(
        7,
        4242,
        [_decisao(phase_before="F5", phase_after="F6", blocked_by=("ACTIVATION_MAX",))],
    )
    assert len(_eventos(caplog, "progression.transition")) == 1
    assert len(_eventos(caplog, "progression.blocked")) == 1


def test_volume_por_sessao_e_no_maximo_um_par_por_grupo_tocado(caplog):
    """Secao 14/volume: as decisoes sao por GRUPO (D2), nao por trilha nem por
    serie. Um full body agressivo toca 6 grupos; mesmo se TODOS transicionassem
    e bloqueassem - o que nao acontece na pratica -, sao 12 linhas na sessao."""
    decisoes = [
        _decisao(muscle_group_id=gid, phase_before="F1", phase_after="F2", blocked_by=("MAX_SETS_REACHED",))
        for gid in range(1, 7)
    ]
    ps.log_decision_events(7, 4242, decisoes)
    assert len(_eventos(caplog, "progression.transition")) == 6
    assert len(_eventos(caplog, "progression.blocked")) == 6


def test_o_finish_e_quem_emite_e_o_replay_nao(caplog):
    """`run_session_evaluation` (o `finish`) espelha as decisoes no log. O
    replay de `rebuild_for_user` NAO: reprocessar a historia inteira de todos
    os usuarios produziria milhares de linhas sobre o passado."""
    assert "log_decision_events(user_id, session.activity_id, result.decisions)" in inspect.getsource(
        ps.run_session_evaluation
    )
    assert "log_decision_events" not in inspect.getsource(ps.rebuild_for_user)
    assert "log_decision_events" not in inspect.getsource(ps.persist_result)
