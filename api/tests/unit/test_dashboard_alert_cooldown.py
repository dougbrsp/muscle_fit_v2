"""Cooldown de alerta do hub (F2.2, `dashboard_alert_states`/migracao 051).

Tudo aqui e funcao PURA: `decide` recebe o insight, o estado gravado e o
relogio, e devolve a decisao mais a linha a gravar. Nenhum banco, nenhum
`datetime.now()` - o relogio SEMPRE vem de fora (`now_local` do
`overview_service`, no fuso do usuario), porque um teste de dashboard com
relogio vivo ja floreou antes.

O que estes testes protegem, em ordem de importancia:

1. **um alerta que PIORA nao fica calado** - se o cooldown silenciasse uma
   condicao que se agrava, ele deixaria de reduzir ruido e passaria a causar
   dano;
2. **um alerta que melhora nao reaparece** por causa da mudanca;
3. **a identidade do estado e a `alert_key`, nao a impressao** - medido: a
   impressao de tres regras muda sozinha (conjunto de ids, data da janela), e
   cooldown por impressao nunca silenciaria nenhuma delas;
4. **episodio novo e noticia nova** - a condicao que voltou depois de meses
   aparece como primeiro aviso, sem cooldown herdado;
5. **todo codigo emitido tem decisao de chave escrita** (guard, igual ao das
   migracoes: esquecer e silencioso e o erro chega no lugar errado, tarde).

Rodar: cd api && python -m pytest tests/unit/test_dashboard_alert_cooldown.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.intelligence.dashboard.alert_state_service import (
    EMIT_REASONS,
    REASON_COOLDOWN_ACTIVE,
    REASON_COOLDOWN_EXPIRED,
    REASON_ESCALATED,
    REASON_FIRST_SEEN,
    REASON_NEW_EPISODE,
    alert_key_hash,
    cooldown_enabled,
    cooldown_public,
    cooldown_until,
    decide,
    episode_is_closed,
    is_worse,
)
from app.intelligence.dashboard.contracts import Severity
from app.intelligence.dashboard.insight_rules import (
    COLLAPSED_CODES,
    COOLDOWN_KEYS_BY_CODE,
    RULES,
    alert_key,
    build_fingerprint,
)
from app.intelligence.dashboard.metrics_registry import threshold

# Relogio FIXO no fuso do usuario - nunca `datetime.now()`.
AGORA = datetime(2026, 9, 25, 8, 30, 0)


def _insight(
    *,
    code: str = "MUSCLE_BELOW_MIN_TWO_WEEKS",
    severity: Severity = Severity.ATTENTION,
    keys: dict | None = None,
    reassess_after: date | None = None,
) -> dict:
    keys = {"muscle_group_id": 6} if keys is None else keys
    return {
        "code": code,
        "severity": severity,
        "fingerprint": build_fingerprint(code, keys),
        "comparison": {"basis": "b", "window": "28d", "keys": keys},
        "reassess_after": reassess_after,
    }


def _state(**overrides) -> dict:
    base = {
        "alert_key": "MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6",
        "alert_key_hash": alert_key_hash("MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6"),
        "rule_code": "MUSCLE_BELOW_MIN_TWO_WEEKS",
        "fingerprint": "MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6",
        "severity": "ATTENTION",
        "last_reason": REASON_FIRST_SEEN,
        "first_seen_at": AGORA - timedelta(days=5),
        "last_seen_at": AGORA - timedelta(days=1),
        "last_emitted_at": AGORA - timedelta(days=5),
        "emitted_count": 1,
        "suppressed_count": 0,
        "resolved_at": None,
        "snoozed_until": AGORA + timedelta(days=2),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Chave do estado: identidade do ALVO, nao da observacao
# ---------------------------------------------------------------------------


def test_todo_codigo_emitido_tem_decisao_de_chave_escrita():
    """Guard. Um codigo novo sem entrada em `COOLDOWN_KEYS_BY_CODE` nao quebra a
    leitura (cai na impressao inteira, o lado seguro), mas FALHA aqui - a
    decisao de qual e o alvo de um alerta e de produto, nao de omissao."""
    emitidos = {rule.code for rule in RULES} | set(COLLAPSED_CODES)
    faltando = sorted(emitidos - set(COOLDOWN_KEYS_BY_CODE))
    assert not faltando, (
        f"codigos emitidos sem chave de cooldown declarada: {faltando}. "
        "Acrescente em `insight_rules.COOLDOWN_KEYS_BY_CODE` dizendo QUAL e o "
        "alvo do alerta (tupla vazia = alvo global)."
    )


def test_nao_ha_chave_declarada_para_codigo_que_ninguem_emite():
    """Sobra tambem e defeito: uma chave para um codigo que nao existe mais
    esconde que a decisao virou letra morta."""
    emitidos = {rule.code for rule in RULES} | set(COLLAPSED_CODES)
    sobra = sorted(set(COOLDOWN_KEYS_BY_CODE) - emitidos)
    assert not sobra, f"chave de cooldown para codigo inexistente: {sobra}"


def test_chave_de_conjunto_nao_entra_na_identidade():
    """`EXERCISES_REGRESSING` produziu QUATRO impressoes em 8 leituras do
    dataset real (a lista `exercise_ids` muda a cada exercicio que entra ou
    sai) para a MESMA condicao. A chave de cooldown e o CODIGO: se fosse a
    impressao, essa regra nunca seria silenciada."""
    primeiro = _insight(code="EXERCISES_REGRESSING", keys={"exercise_ids": [9, 14, 16]})
    depois = _insight(code="EXERCISES_REGRESSING", keys={"exercise_ids": [9, 14, 16, 30, 36]})
    assert primeiro["fingerprint"] != depois["fingerprint"]
    assert alert_key(primeiro) == alert_key(depois) == "EXERCISES_REGRESSING:"


def test_chave_com_a_data_da_janela_nao_entra_na_identidade():
    """`RECOMPOSITION_COMPATIBLE` e `DATA_QUALITY_UNMAPPED_SETS` levam `date_to`
    na comparacao - impressao nova TODO DIA, por construcao."""
    for code in ("RECOMPOSITION_COMPATIBLE", "DATA_QUALITY_UNMAPPED_SETS"):
        hoje = _insight(code=code, keys={"window": "28d", "date_to": "2026-09-25"})
        amanha = _insight(code=code, keys={"window": "28d", "date_to": "2026-09-26"})
        assert hoje["fingerprint"] != amanha["fingerprint"]
        assert alert_key(hoje) == alert_key(amanha)


def test_chave_estavel_usa_as_chaves_do_alvo():
    """Onde a impressao JA e estavel (o evento de contexto: tipo + data do
    evento sao fatos fixos), a chave do alvo reproduz a impressao.

    A tupla deste codigo era `(exercise_id, event_type, event_date)` enquanto a
    regra emitia um cartao por exercicio afetado. Com o colapso por evento, o
    ALVO passou a ser o EVENTO e a impressao nao carrega mais `exercise_id` -
    entrar ou sair um exercicio afetado nao pode criar um alerta novo."""
    assert COOLDOWN_KEYS_BY_CODE["PERFORMANCE_DROP_WITH_CONTEXT_EVENT"] == (
        "event_type",
        "event_date",
    )
    item = _insight(
        code="PERFORMANCE_DROP_WITH_CONTEXT_EVENT",
        severity=Severity.INFO,
        keys={"event_type": "gym_change", "event_date": "2026-08-30"},
    )
    assert alert_key(item) == item["fingerprint"]


def test_alvos_diferentes_tem_chaves_diferentes():
    """Dois grupos musculares abaixo do minimo sao DOIS alertas: silenciar o
    peito nao pode calar o ombro."""
    peito = _insight(keys={"muscle_group_id": 1})
    ombro = _insight(keys={"muscle_group_id": 6})
    assert alert_key(peito) != alert_key(ombro)
    assert alert_key_hash(alert_key(peito)) != alert_key_hash(alert_key(ombro))


def test_codigo_sem_decisao_cai_na_impressao_inteira():
    desconhecido = _insight(code="CODIGO_QUE_NAO_EXISTE", keys={"x": 1})
    assert alert_key(desconhecido) == desconhecido["fingerprint"]


# ---------------------------------------------------------------------------
# Duracao: o `reassess_after` da propria regra, com piso e teto
# ---------------------------------------------------------------------------


def test_duracao_vem_do_reassess_after_da_regra():
    ate = cooldown_until(now=AGORA, severity=Severity.ATTENTION, reassess_after=AGORA.date() + timedelta(days=21))
    assert ate == AGORA + timedelta(days=21)


def test_reassess_after_no_passado_cai_no_piso_da_severidade():
    """O caso MEDIDO: as 19 associacoes INFO do dataset tem `reassess_after`
    vencido ha 15-19 dias. Sem piso, o cooldown seria zero e nada mudaria."""
    piso_info = int(threshold("alert_cooldown_min_days_info"))
    ate = cooldown_until(
        now=AGORA, severity=Severity.INFO, reassess_after=AGORA.date() - timedelta(days=17)
    )
    assert ate == AGORA + timedelta(days=piso_info)


def test_reassess_after_ausente_cai_no_piso():
    piso = int(threshold("alert_cooldown_min_days_action"))
    assert cooldown_until(now=AGORA, severity=Severity.ACTION, reassess_after=None) == AGORA + timedelta(days=piso)


def test_o_teto_limita_reavaliacao_muito_distante():
    teto = int(threshold("alert_cooldown_max_days"))
    ate = cooldown_until(
        now=AGORA, severity=Severity.INFO, reassess_after=AGORA.date() + timedelta(days=400)
    )
    assert ate == AGORA + timedelta(days=teto)


def test_action_fica_calado_por_menos_tempo_que_info():
    assert int(threshold("alert_cooldown_min_days_action")) < int(
        threshold("alert_cooldown_min_days_attention")
    ) < int(threshold("alert_cooldown_min_days_info"))


# ---------------------------------------------------------------------------
# Decisao
# ---------------------------------------------------------------------------


def test_primeira_vez_sempre_aparece():
    d = decide(_insight(), None, now=AGORA, alert_key="k")
    assert d["emit"] is True
    assert d["reason"] == REASON_FIRST_SEEN
    assert d["first_seen_at"] == AGORA
    assert d["emitted_count"] == 1
    assert d["suppressed_count"] == 0
    assert d["snoozed_until"] > AGORA


def test_dentro_do_cooldown_fica_silenciado():
    """`_state()` foi emitido 5 dias atras: outro DIA, portanto silenciavel (a
    regra do mesmo dia so vale para a ocasiao de exibicao corrente)."""
    d = decide(_insight(), _state(), now=AGORA, alert_key="k")
    assert d["emit"] is False
    assert d["reason"] == REASON_COOLDOWN_ACTIVE
    assert d["suppressed_count"] == 1
    # `last_seen_at` avanca (a condicao FOI observada), `last_emitted_at` nao.
    assert d["last_seen_at"] == AGORA
    assert d["last_emitted_at"] == AGORA - timedelta(days=5)


def test_silenciado_nao_sobrescreve_a_severidade_da_ultima_emissao():
    """A severidade gravada e o degrau contra o qual a proxima leitura compara.
    Gravar a de uma emissao que NAO aconteceu apagaria o degrau e a escalada
    seguinte passaria batida."""
    d = decide(_insight(severity=Severity.INFO), _state(severity="ATTENTION"), now=AGORA, alert_key="k")
    assert d["emit"] is False
    assert d["severity"] == Severity.ATTENTION


def test_a_segunda_leitura_do_mesmo_dia_mostra_a_mesma_resposta():
    """A propriedade que faltava e derrubou dois testes da visao geral: abrir a
    home JA e uma leitura (ela emite `next_actions`); se o cooldown contasse por
    REQUISICAO, a tela de insights aberta em seguida apareceria vazia."""
    estado = _state(last_emitted_at=AGORA - timedelta(hours=3), emitted_count=1, suppressed_count=0)
    d = decide(_insight(), estado, now=AGORA, alert_key="k")
    assert d["emit"] is True
    # Mesmo selo da primeira leitura (o motivo e LEMBRADO, nao recalculado).
    assert d["reason"] == REASON_FIRST_SEEN
    # E nada e contado duas vezes.
    assert (d["emitted_count"], d["suppressed_count"]) == (1, 0)
    assert d["last_emitted_at"] == estado["last_emitted_at"]
    assert d["snoozed_until"] == estado["snoozed_until"]


def test_o_bloco_publico_da_segunda_leitura_do_dia_e_identico_ao_da_primeira():
    """`test_payload_identico_nos_dois_backends` compara duas leituras da visao
    geral; se o bloco `cooldown` mudasse entre elas, o payload divergiria."""
    primeira = decide(_insight(), None, now=AGORA, alert_key="k")
    como_gravado = _state(
        last_reason=primeira["reason"],
        first_seen_at=primeira["first_seen_at"],
        last_seen_at=primeira["last_seen_at"],
        last_emitted_at=primeira["last_emitted_at"],
        emitted_count=primeira["emitted_count"],
        suppressed_count=primeira["suppressed_count"],
        snoozed_until=primeira["snoozed_until"],
        severity=primeira["severity"].value,
    )
    segunda = decide(_insight(), como_gravado, now=AGORA, alert_key="k")
    assert cooldown_public(primeira) == cooldown_public(segunda)


def test_piorar_no_mesmo_dia_ainda_fura():
    """A escalada e checada ANTES da regra do mesmo dia: se a condicao piorou
    entre duas leituras do mesmo dia, o selo tem de mudar."""
    estado = _state(severity="ATTENTION", last_emitted_at=AGORA - timedelta(hours=2))
    d = decide(_insight(severity=Severity.ACTION), estado, now=AGORA, alert_key="k")
    assert d["reason"] == REASON_ESCALATED


def test_o_motivo_gravado_e_sempre_um_motivo_de_exibicao():
    """`last_reason` descreve a ULTIMA EMISSAO. Gravar `cooldown_active` nela
    faria a proxima leitura do dia mostrar o cartao com o selo de "silenciado"."""
    silenciado = decide(_insight(), _state(), now=AGORA, alert_key="k")
    assert silenciado["last_reason"] in EMIT_REASONS
    for estado in (None, _state(snoozed_until=AGORA - timedelta(days=1))):
        assert decide(_insight(), estado, now=AGORA, alert_key="k")["last_reason"] in EMIT_REASONS


def test_cooldown_vencido_volta_a_aparecer():
    estado = _state(snoozed_until=AGORA - timedelta(hours=1))
    d = decide(_insight(), estado, now=AGORA, alert_key="k")
    assert d["emit"] is True
    assert d["reason"] == REASON_COOLDOWN_EXPIRED
    assert d["emitted_count"] == 2
    # O episodio e o MESMO: `first_seen_at` nao se mexe.
    assert d["first_seen_at"] == estado["first_seen_at"]


def test_piorar_de_severidade_fura_o_cooldown():
    """A propriedade mais importante do desenho. O cooldown existe para calar a
    REPETICAO da mesma noticia; "piorou" nao e a mesma noticia."""
    estado = _state(severity="ATTENTION", snoozed_until=AGORA + timedelta(days=10))
    d = decide(_insight(severity=Severity.ACTION), estado, now=AGORA, alert_key="k")
    assert d["emit"] is True
    assert d["reason"] == REASON_ESCALATED
    assert d["severity"] == Severity.ACTION
    # e o cooldown recomeca a contar de agora, com o piso do novo nivel.
    assert d["snoozed_until"] == AGORA + timedelta(days=int(threshold("alert_cooldown_min_days_action")))


def test_melhorar_de_severidade_nao_fura_o_cooldown():
    """Melhora nao e alerta: ela aparece pelo cartao DEIXAR de existir, nao por
    um aviso novo."""
    estado = _state(severity="ACTION", snoozed_until=AGORA + timedelta(days=2))
    d = decide(_insight(severity=Severity.INFO), estado, now=AGORA, alert_key="k")
    assert d["emit"] is False
    assert d["reason"] == REASON_COOLDOWN_ACTIVE


def test_mesma_severidade_nao_fura_o_cooldown():
    estado = _state(severity="ATTENTION", snoozed_until=AGORA + timedelta(days=2))
    d = decide(_insight(severity=Severity.ATTENTION), estado, now=AGORA, alert_key="k")
    assert d["emit"] is False


def test_is_worse_respeita_a_ordem_action_attention_info():
    assert is_worse(Severity.ACTION, Severity.ATTENTION)
    assert is_worse(Severity.ATTENTION, Severity.INFO)
    assert not is_worse(Severity.INFO, Severity.ATTENTION)
    assert not is_worse(Severity.ATTENTION, Severity.ATTENTION)
    # Aceita a string que vem do banco (ENUM da 051).
    assert is_worse("ACTION", "INFO")


# ---------------------------------------------------------------------------
# Episodio
# ---------------------------------------------------------------------------


def test_condicao_que_sumiu_por_muito_tempo_encerra_o_episodio():
    gap = int(threshold("alert_state_resolution_gap_days"))
    estado = _state(last_seen_at=AGORA - timedelta(days=gap + 1))
    assert episode_is_closed(estado, now=AGORA)
    assert not episode_is_closed(_state(last_seen_at=AGORA - timedelta(days=gap - 1)), now=AGORA)


def test_episodio_novo_e_primeiro_aviso_sem_cooldown_herdado():
    """"Seu ombro voltou a ficar abaixo do minimo depois de dois meses em
    ordem" e noticia, nao repeticao - mesmo que o `snoozed_until` antigo ainda
    estivesse no futuro."""
    gap = int(threshold("alert_state_resolution_gap_days"))
    estado = _state(
        last_seen_at=AGORA - timedelta(days=gap + 5),
        snoozed_until=AGORA + timedelta(days=30),
        emitted_count=9,
        suppressed_count=40,
    )
    d = decide(_insight(), estado, now=AGORA, alert_key="k")
    assert d["emit"] is True
    assert d["reason"] == REASON_NEW_EPISODE
    assert d["first_seen_at"] == AGORA
    # As contagens do episodio ANTERIOR nao se somam: elas descreviam outra
    # ocorrencia.
    assert (d["emitted_count"], d["suppressed_count"]) == (1, 0)


def test_estado_resolvido_explicitamente_tambem_abre_episodio_novo():
    estado = _state(resolved_at=AGORA - timedelta(days=2))
    d = decide(_insight(), estado, now=AGORA, alert_key="k")
    assert d["reason"] == REASON_NEW_EPISODE
    assert d["resolved_at"] is None, "a linha gravada descreve um episodio ABERTO"


def test_toda_linha_gravada_reabre_o_episodio():
    """A condicao acabou de ser observada - inclusive quando foi silenciada.
    Deixar `resolved_at` preenchido faria a varredura seguinte tratar um alerta
    vivo como historia."""
    for estado in (None, _state(), _state(snoozed_until=AGORA - timedelta(days=1))):
        assert decide(_insight(), estado, now=AGORA, alert_key="k")["resolved_at"] is None


# ---------------------------------------------------------------------------
# Contrato com a tela
# ---------------------------------------------------------------------------


def test_o_bloco_publico_diz_por_que_o_cartao_esta_ou_nao_esta_la():
    d = decide(_insight(), _state(), now=AGORA, alert_key="k")
    publico = cooldown_public(d)
    assert publico["suppressed"] is True
    assert publico["reason"] == REASON_COOLDOWN_ACTIVE
    assert publico["snoozed_until"] == _state()["snoozed_until"]
    assert publico["metric_version"] == "alert-cooldown-v1"
    # Nada de estado interno na resposta.
    assert "emit" not in publico


def test_o_relogio_da_decisao_tem_a_precisao_do_banco():
    """`DATETIME` guarda SEGUNDOS. Sem truncar, a primeira leitura devolveria
    `first_seen_at` com microssegundos (valor em memoria) e a seguinte o mesmo
    instante sem eles (valor lido do banco) - duas respostas para um estado so."""
    com_micro = AGORA.replace(microsecond=123456)
    d = decide(_insight(), None, now=com_micro, alert_key="k")
    assert d["first_seen_at"].microsecond == 0
    assert d["last_seen_at"].microsecond == 0
    assert d["last_emitted_at"].microsecond == 0
    assert d["snoozed_until"].microsecond == 0
    # E a decisao e a MESMA com ou sem os microssegundos.
    assert cooldown_public(d) == cooldown_public(decide(_insight(), None, now=AGORA, alert_key="k"))


def test_hash_da_chave_e_estavel_e_tem_64_hexadecimais():
    h = alert_key_hash("MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6")
    assert h == alert_key_hash("MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_chave_longa_nao_perde_identidade_no_hash():
    """A impressao nao tem teto de tamanho (95 caracteres medidos hoje, e cresce
    com a lista de exercicios). O hash e o que permite indexar sem truncar - e
    truncar num UNIQUE seria COLIDIR."""
    longa_a = "X:" + ",".join(str(i) for i in range(400))
    longa_b = longa_a + ",400"
    assert len(longa_a) > 500
    assert alert_key_hash(longa_a) != alert_key_hash(longa_b)


def test_a_flag_desliga_o_cooldown(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ALERT_COOLDOWN", raising=False)
    assert cooldown_enabled() is True
    for valor in ("0", "false", "OFF", "no"):
        monkeypatch.setenv("DASHBOARD_ALERT_COOLDOWN", valor)
        assert cooldown_enabled() is False
    monkeypatch.setenv("DASHBOARD_ALERT_COOLDOWN", "1")
    assert cooldown_enabled() is True


@pytest.mark.parametrize("severity", list(Severity))
def test_toda_severidade_tem_piso_registrado(severity):
    assert cooldown_until(now=AGORA, severity=severity, reassess_after=None) > AGORA
