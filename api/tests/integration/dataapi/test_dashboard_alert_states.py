"""Persistencia do cooldown de alerta (F2.2, `dashboard_alert_states`/migracao
051) contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data
API de producao).

A DECISAO do cooldown e funcao pura e esta coberta em
`tests/unit/test_dashboard_alert_cooldown.py`. Aqui se prova o que so o banco
responde:

- o upsert MULTI-LINHA grava e ATUALIZA pela chave `(user_id, alert_key_hash)`,
  em UM statement, qualquer que seja o numero de insights;
- duas chaves diferentes convivem; a MESMA chave nao duplica;
- a segunda leitura do MESMO dia mostra de novo (mesma ocasiao de exibicao,
  sem contar duas vezes) e a do dia seguinte silencia - o ciclo completo de
  `apply_cooldown`;
- o que PIOROU de severidade fura o cooldown;
- isolamento por usuario: o estado do A nao silencia o alerta do B;
- a varredura fecha os episodios vencidos (`resolved_at`) sem tocar nos vivos;
- o payload de `apply_cooldown` e identico nos dois backends.

Relogio: `agora_do_usuario()` (fuso do usuario de teste), nunca
`datetime.now()` - um relogio vivo dentro de um payload comparado duas vezes
ja floreou o CI antes.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.db import get_conn
from app.intelligence.dashboard import alert_state_service as ass
from app.intelligence.dashboard.contracts import Severity
from app.intelligence.dashboard.insight_rules import alert_key, build_fingerprint
from app.intelligence.dashboard.metrics_registry import threshold
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import agora_do_usuario

AGORA = agora_do_usuario().replace(microsecond=0)
# O cooldown e medido em DIAS: uma segunda leitura do MESMO dia e a MESMA ocasiao
# de exibicao e mostra tudo de novo (sem isso, abrir a home e depois a tela de
# insights deixaria a segunda vazia). Para exercitar a supressao, a leitura
# seguinte tem de cair em outro dia.
AMANHA = AGORA + _dt.timedelta(days=1)


@pytest.fixture
def usuarios(raw):
    ass.reset_alert_states_table_cache()
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("TRUNCATE TABLE dashboard_alert_states")
        cur.execute("TRUNCATE TABLE users")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        ids = {}
        for nome in ("dono", "outro"):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (nome,))
            ids[nome] = cur.lastrowid
    raw.commit()
    return ids


def _insight(muscle_group_id: int, severity: Severity = Severity.ATTENTION, reassess_days: int = 7) -> dict:
    keys = {"muscle_group_id": muscle_group_id}
    return {
        "code": "MUSCLE_BELOW_MIN_TWO_WEEKS",
        "severity": severity,
        "data_quality": "HIGH",
        "fingerprint": build_fingerprint("MUSCLE_BELOW_MIN_TWO_WEEKS", keys),
        "comparison": {"basis": "weekly_target", "window": "28d", "keys": keys},
        "reassess_after": (AGORA + _dt.timedelta(days=reassess_days)).date(),
    }


def _linhas(raw, user_id: int) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT alert_key, rule_code, severity, emitted_count, suppressed_count, "
            "resolved_at, snoozed_until, first_seen_at, last_seen_at, last_emitted_at "
            "FROM dashboard_alert_states WHERE user_id = %s ORDER BY alert_key",
            (user_id,),
        )
        return [dict(row) for row in (cur.fetchall() or [])]


def _aplicar(user_id: int, insights, *, now=AGORA) -> dict:
    with get_conn() as conn:
        return ass.apply_cooldown(
            conn, user_id=user_id, insights=insights, now=now, alert_key_for=alert_key
        )


def test_ciclo_completo_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("DELETE FROM dashboard_alert_states")
        raw.commit()

        insights = [_insight(1), _insight(6)]

        # 1a leitura: tudo aparece (primeira vez) e o estado nasce gravado.
        primeira = _aplicar(usuarios["dono"], insights)
        assert primeira["available"] is True, backend
        assert len(primeira["visible"]) == 2 and primeira["suppressed"] == []
        assert all(item["cooldown"]["reason"] == ass.REASON_FIRST_SEEN for item in primeira["visible"])

        linhas = _linhas(raw, usuarios["dono"])
        assert len(linhas) == 2, (backend, linhas)
        assert {l["alert_key"] for l in linhas} == {
            "MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=1",
            "MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6",
        }
        assert all(l["snoozed_until"] > AGORA for l in linhas)

        # 2a leitura NO MESMO DIA: mesma ocasiao de exibicao - tudo aparece de
        # novo, com a MESMA resposta, e o upsert NAO duplica linha (a chave e
        # `(user_id, alert_key_hash)`).
        mesma_ocasiao = _aplicar(usuarios["dono"], insights)
        assert len(mesma_ocasiao["visible"]) == 2, backend
        assert mesma_ocasiao["suppressed"] == []
        assert [i["cooldown"] for i in mesma_ocasiao["visible"]] == [
            i["cooldown"] for i in primeira["visible"]
        ], "duas leituras da mesma ocasiao devolvem o mesmo bloco de cooldown"
        assert len(_linhas(raw, usuarios["dono"])) == 2, "upsert por chave, nunca INSERT novo"

        # 3a leitura NO DIA SEGUINTE: agora os dois ficam silenciados.
        segunda = _aplicar(usuarios["dono"], insights, now=AMANHA)
        assert segunda["visible"] == []
        assert len(segunda["suppressed"]) == 2
        assert all(i["cooldown"]["reason"] == ass.REASON_COOLDOWN_ACTIVE for i in segunda["suppressed"])
        assert segunda["limitations"] == [ass.SUPPRESSED_LIMITATION]

        linhas = _linhas(raw, usuarios["dono"])
        assert len(linhas) == 2
        assert all(l["suppressed_count"] == 1 and l["emitted_count"] == 1 for l in linhas)

        resultados[backend] = [
            {k: v for k, v in l.items() if k not in ("first_seen_at", "last_seen_at", "last_emitted_at", "snoozed_until")}
            for l in linhas
        ]
    assert resultados["pymysql"] == resultados["data-api"]


def test_piorar_de_severidade_fura_o_cooldown_e_regrava(usuarios, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    _aplicar(usuarios["dono"], [_insight(1, Severity.ATTENTION)])
    assert _linhas(raw, usuarios["dono"])[0]["severity"] == "ATTENTION"

    # Mesmo alvo, condicao pior, no dia seguinte (dentro do cooldown de 7 dias):
    # aparece mesmo assim.
    piorou = _aplicar(usuarios["dono"], [_insight(1, Severity.ACTION)], now=AMANHA)
    assert len(piorou["visible"]) == 1, db_backend
    assert piorou["visible"][0]["cooldown"]["reason"] == ass.REASON_ESCALATED

    linha = _linhas(raw, usuarios["dono"])[0]
    assert linha["severity"] == "ACTION"
    assert linha["emitted_count"] == 2 and linha["suppressed_count"] == 0

    # E melhorar depois NAO faz reaparecer.
    melhorou = _aplicar(usuarios["dono"], [_insight(1, Severity.INFO)], now=AMANHA + _dt.timedelta(days=1))
    assert melhorou["visible"] == []
    assert _linhas(raw, usuarios["dono"])[0]["severity"] == "ACTION"


def test_estado_de_um_usuario_nao_silencia_o_alerta_do_outro(usuarios, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    _aplicar(usuarios["dono"], [_insight(1)])
    # Dia seguinte: o alerta do dono passa a ficar silenciado.
    silenciado = _aplicar(usuarios["dono"], [_insight(1)], now=AMANHA)
    assert silenciado["visible"] == []

    do_outro = _aplicar(usuarios["outro"], [_insight(1)], now=AMANHA)
    assert len(do_outro["visible"]) == 1, db_backend
    assert do_outro["visible"][0]["cooldown"]["reason"] == ass.REASON_FIRST_SEEN
    assert len(_linhas(raw, usuarios["dono"])) == 1
    assert len(_linhas(raw, usuarios["outro"])) == 1


def test_cooldown_vencido_volta_a_aparecer_no_mesmo_episodio(usuarios, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    _aplicar(usuarios["dono"], [_insight(1, reassess_days=7)])
    primeiro = _linhas(raw, usuarios["dono"])[0]

    # Oito dias depois: o cooldown de 7 dias venceu, mas o episodio e o MESMO
    # (menos que o intervalo de resolucao).
    depois = AGORA + _dt.timedelta(days=8)
    volta = _aplicar(usuarios["dono"], [_insight(1)], now=depois)
    assert len(volta["visible"]) == 1, db_backend
    assert volta["visible"][0]["cooldown"]["reason"] == ass.REASON_COOLDOWN_EXPIRED

    linha = _linhas(raw, usuarios["dono"])[0]
    assert linha["first_seen_at"] == primeiro["first_seen_at"], "mesmo episodio"
    assert linha["emitted_count"] == 2


def test_condicao_que_voltou_depois_de_meses_e_noticia_nova(usuarios, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    _aplicar(usuarios["dono"], [_insight(1)])
    gap = int(threshold("alert_state_resolution_gap_days"))
    muito_depois = AGORA + _dt.timedelta(days=gap + 10)

    de_volta = _aplicar(usuarios["dono"], [_insight(1)], now=muito_depois)
    assert len(de_volta["visible"]) == 1, db_backend
    assert de_volta["visible"][0]["cooldown"]["reason"] == ass.REASON_NEW_EPISODE

    linha = _linhas(raw, usuarios["dono"])[0]
    assert linha["first_seen_at"] == muito_depois
    assert (linha["emitted_count"], linha["suppressed_count"]) == (1, 0)
    assert linha["resolved_at"] is None, "a linha descreve o episodio ABERTO"


def test_varredura_fecha_episodio_vencido_que_nao_reapareceu(usuarios, raw, db_backend):
    """O alerta do grupo 1 para de disparar; o do 6 continua. Passado o
    intervalo de resolucao, o estado gravado nao pode continuar afirmando que o
    primeiro esta aberto."""
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    _aplicar(usuarios["dono"], [_insight(1), _insight(6)])
    gap = int(threshold("alert_state_resolution_gap_days"))
    depois = AGORA + _dt.timedelta(days=gap + 1)

    # Só o grupo 6 dispara nesta leitura.
    _aplicar(usuarios["dono"], [_insight(6)], now=depois)

    por_chave = {l["alert_key"]: l for l in _linhas(raw, usuarios["dono"])}
    assert por_chave["MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=1"]["resolved_at"] is not None, db_backend
    assert por_chave["MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6"]["resolved_at"] is None


def test_sem_insight_nenhum_nao_escreve_nada(usuarios, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    resultado = _aplicar(usuarios["dono"], [])
    assert resultado["visible"] == [] and resultado["suppressed"] == []
    assert _linhas(raw, usuarios["dono"]) == [], db_backend


def test_flag_desligada_mostra_tudo_e_nao_escreve(usuarios, raw, monkeypatch, db_backend):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    _aplicar(usuarios["dono"], [_insight(1)])
    _aplicar(usuarios["dono"], [_insight(1)], now=AMANHA)  # silenciado
    antes = _linhas(raw, usuarios["dono"])

    monkeypatch.setenv(ass.COOLDOWN_ENV, "0")
    desligado = _aplicar(usuarios["dono"], [_insight(1)], now=AMANHA)
    assert desligado["available"] is False, db_backend
    assert len(desligado["visible"]) == 1 and desligado["suppressed"] == []
    assert desligado["limitations"] == [ass.COOLDOWN_DISABLED_LIMITATION]
    assert _linhas(raw, usuarios["dono"]) == antes, "flag desligada nao escreve estado"


def test_chave_longa_cabe_na_tabela(usuarios, raw, db_backend):
    """A impressao nao tem teto de tamanho (a mais longa medida hoje tem 95
    caracteres e cresce com a lista de exercicios). A identidade vai no hash de
    64 bytes, e `alert_key`/`fingerprint` tem 500 - o teste grava uma chave
    grande para o limite ser real, nao suposto."""
    with raw.cursor() as cur:
        cur.execute("DELETE FROM dashboard_alert_states")
    raw.commit()

    ids = list(range(1, 121))
    keys = {"exercise_ids": ids}
    longo = {
        "code": "EXERCISES_REGRESSING",
        "severity": Severity.ATTENTION,
        "data_quality": "HIGH",
        "fingerprint": build_fingerprint("EXERCISES_REGRESSING", keys),
        "comparison": {"basis": "b", "window": "28d", "keys": keys},
        "reassess_after": None,
    }
    assert len(longo["fingerprint"]) > 255, "a impressao deste caso passa de 255 de proposito"

    resultado = _aplicar(usuarios["dono"], [longo])
    assert len(resultado["visible"]) == 1, db_backend
    linha = _linhas(raw, usuarios["dono"])[0]
    # A chave de cooldown deste codigo e o CODIGO (o conjunto de ids muda
    # sozinho), e a impressao inteira fica gravada como evidencia.
    assert linha["alert_key"] == "EXERCISES_REGRESSING:"
