"""`generation_runs` (M16, migracao 054) contra MySQL real, nos DOIS backends
(PyMySQL de dev e shim da RDS Data API de producao).

A logica pura (hash canonico, o que e promovido a coluna, a excecao engolida)
esta em `tests/unit/test_m16_generation_runs.py`. Aqui se prova o que so o
banco responde:

- a linha grava e VOLTA: `audit_json` sobrevive a ida e volta pela coluna JSON
  com o bloco intacto (chave a chave), nos dois backends;
- a ligacao rascunho -> ficha fecha pelo `audit_hash`, marca **uma** linha e
  nao toca nas outras;
- duas geracoes com auditoria IDENTICA convivem (o hash nao e UNIQUE) e a
  ligacao pega a mais antiga ainda sem ficha - a segunda continua livre;
- apagar a ficha NAO apaga a linha (FK `ON DELETE SET NULL`): o fato de que a
  geracao aconteceu sobrevive a ficha;
- apagar o usuario apaga (FK `ON DELETE CASCADE`);
- a escrita custa 1 statement e a ligacao 1 statement - a contagem, que e
  deterministica, e a medicao do caminho quente da geracao.
"""

from __future__ import annotations

import json

import pytest

# `app.db` e importado como MODULO, nunca `from app.db import transaction`: o
# `use_backend` troca os ATRIBUTOS dos modulos `app.*` para o shim da Data
# API, e este arquivo nao e `app.*` - um `from ... import` aqui ficaria
# preso ao PyMySQL e o teste diria "identico nos dois backends" sem ter
# exercitado o segundo.
from app import db as app_db
from app.generation import generation_runs as gr
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import truncar

AUDIT = {
    "template_id": 7,
    "slot_sequence": 2,
    "volume_mode": "percent_budget",
    "engine_version": "v2.1",
    "window": {"days": 7, "projected_slots": [{"date": "2026-09-20", "slot_id": 3}]},
    "activation": {"1": 12.0},
    "selection": {"1": ["seguranca", "estrelas"]},
    "safety_clamps": [],
    "substitutions": [],
    "fallbacks": [],
    "llm_used": False,
}

DRAFT = {
    "name": "Modelo — Slot A",
    "plan_date": "2026-09-26",
    "raw_payload": {"engine_version": "v2.1", "used_ai": False, "generation_audit": AUDIT},
}


@pytest.fixture
def cenario(raw):
    """Isolamento pelo helper do projeto + DOIS usuarios, de proposito.

    `truncar` (e nao um TRUNCATE a mao das tres tabelas deste arquivo) porque
    o que este teste le pelo GRAFO DE FKs e maior do que o que ele escreve:
    apagar um usuario esbarra em `activities` e `workout_plans`, que tem FK
    `ON DELETE RESTRICT` para `users`. Uma linha de `activities` deixada por
    OUTRO arquivo da suite (o id do usuario recomeca em 1 depois do TRUNCATE)
    bastava para o MySQL recusar o DELETE antes de a cascata desta tabela ter
    vez - foi exatamente o que derrubou o CI. E a licao ja registrada no
    cabecalho de `tests/support/isolamento_v3.py`: **todo arquivo limpa tudo o
    que le**, e "o que le" inclui o que as FKs alcancam.

    - `dono` tem duas fichas: serve a ligacao e ao `ON DELETE SET NULL`;
    - `solitario` nasce SEM ficha, sem atividade, sem nada: e o unico jeito de
      um DELETE em `users` chegar a testar o `ON DELETE CASCADE` desta tabela
      em vez de bater num RESTRICT de outra.
    """
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('dono', 'x', 'regular')")
        user_id = cur.lastrowid
        planos = []
        for nome in ("Ficha A", "Ficha B"):
            cur.execute(
                "INSERT INTO workout_plans (user_id, name, plan_type, status) VALUES (%s, %s, 'full_body', 'planned')",
                (user_id, nome),
            )
            planos.append(cur.lastrowid)
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('solitario', 'x', 'regular')")
        solitario_id = cur.lastrowid
    raw.commit()
    return {"user_id": user_id, "planos": planos, "solitario_id": solitario_id}


def _dependentes_restrict(raw, user_id: int) -> dict[str, int]:
    """Quantas linhas o usuario tem nas tabelas cuja FK para `users` e
    `ON DELETE RESTRICT`. Se alguma for > 0, o DELETE e recusado pelo MySQL e
    o teste de CASCADE nunca chega a testar o que diz testar."""
    contagens = {}
    for tabela in ("activities", "workout_plans", "body_composition", "daily_biometrics"):
        with raw.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {tabela} WHERE user_id = %s", (user_id,))
            contagens[tabela] = int((cur.fetchone() or {}).get("n") or 0)
    return contagens


def _linhas(raw, user_id: int) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, workout_plan_id, job_id, template_id, template_slot_sequence, "
            "volume_mode, engine_version, llm_used, plan_date, audit_hash, audit_json, request_json "
            "FROM generation_runs WHERE user_id = %s ORDER BY id",
            (user_id,),
        )
        return list(cur.fetchall() or [])


def _limpar(raw):
    with raw.cursor() as cur:
        cur.execute("DELETE FROM generation_runs")
    raw.commit()


def test_grava_e_volta_identico_nos_dois_backends(cenario, raw, monkeypatch, fake_rds, mysql_schema):
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)

        run_id = gr.record_generation_run(
            DRAFT, user_id=cenario["user_id"], job_id="1f2e3d4c-0000-1111-2222-333344445555"
        )
        assert run_id, backend

        linhas = _linhas(raw, cenario["user_id"])
        assert len(linhas) == 1, (backend, linhas)
        linha = linhas[0]
        assert linha["workout_plan_id"] is None, backend
        assert linha["template_id"] == 7 and linha["template_slot_sequence"] == 2, backend
        assert linha["volume_mode"] == "percent_budget" and linha["engine_version"] == "v2.1", backend
        assert int(linha["llm_used"]) == 0, backend
        assert str(linha["plan_date"]) == "2026-09-26", backend
        assert linha["audit_hash"] == gr.audit_hash(AUDIT), backend
        assert linha["request_json"] is None, backend

        # O bloco INTEIRO volta - a coluna JSON normaliza a ordem das chaves,
        # nunca o conteudo. E por isso que o hash e calculado em Python.
        bruto = linha["audit_json"]
        voltou = json.loads(bruto) if isinstance(bruto, (str, bytes, bytearray)) else bruto
        assert voltou == AUDIT, backend


def test_a_ligacao_marca_uma_linha_e_so_uma(cenario, raw, monkeypatch, fake_rds, mysql_schema):
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)

        # Duas geracoes com a MESMA auditoria: o hash nao e UNIQUE, entao as
        # duas cabem - uma geracao nunca pode ser reprovada pela auditoria.
        gr.record_generation_run(DRAFT, user_id=cenario["user_id"])
        gr.record_generation_run(DRAFT, user_id=cenario["user_id"])
        antes = _linhas(raw, cenario["user_id"])
        assert len(antes) == 2, backend

        with app_db.transaction() as conn:
            gr.link_run_to_plan(
                conn,
                user_id=cenario["user_id"],
                plan_id=cenario["planos"][0],
                raw_payload=DRAFT["raw_payload"],
            )

        depois = _linhas(raw, cenario["user_id"])
        # A mais ANTIGA levou a ficha; a segunda continua livre para a proxima.
        assert depois[0]["workout_plan_id"] == cenario["planos"][0], backend
        assert depois[1]["workout_plan_id"] is None, backend

        # A segunda ficha pega a linha restante, sem roubar a primeira.
        with app_db.transaction() as conn:
            gr.link_run_to_plan(
                conn,
                user_id=cenario["user_id"],
                plan_id=cenario["planos"][1],
                raw_payload=DRAFT["raw_payload"],
            )
        final = _linhas(raw, cenario["user_id"])
        assert [l["workout_plan_id"] for l in final] == cenario["planos"], backend


def test_auditoria_de_outro_conteudo_nao_e_marcada(cenario, raw, dataapi_db):
    _limpar(raw)
    gr.record_generation_run(DRAFT, user_id=cenario["user_id"])

    outro = {**DRAFT["raw_payload"], "generation_audit": {**AUDIT, "llm_used": True}}
    with app_db.transaction() as conn:
        gr.link_run_to_plan(conn, user_id=cenario["user_id"], plan_id=cenario["planos"][0], raw_payload=outro)

    assert _linhas(raw, cenario["user_id"])[0]["workout_plan_id"] is None


def test_apagar_a_ficha_desliga_a_linha_mas_nao_a_apaga(cenario, raw, dataapi_db):
    """A geracao ACONTECEU. Apagar a ficha nao desfaz isso - por isso a FK e
    `ON DELETE SET NULL` e nao CASCADE."""
    _limpar(raw)
    gr.record_generation_run(DRAFT, user_id=cenario["user_id"])
    with app_db.transaction() as conn:
        gr.link_run_to_plan(
            conn, user_id=cenario["user_id"], plan_id=cenario["planos"][0], raw_payload=DRAFT["raw_payload"]
        )
    assert _linhas(raw, cenario["user_id"])[0]["workout_plan_id"] == cenario["planos"][0]

    with raw.cursor() as cur:
        cur.execute("DELETE FROM workout_plans WHERE id = %s", (cenario["planos"][0],))
    raw.commit()

    linhas = _linhas(raw, cenario["user_id"])
    assert len(linhas) == 1
    assert linhas[0]["workout_plan_id"] is None
    assert linhas[0]["audit_hash"] == gr.audit_hash(AUDIT)


def test_apagar_o_usuario_apaga_a_auditoria(cenario, raw, dataapi_db):
    """Sem usuario nao ha o que auditar - FK CASCADE.

    Usa o usuario `solitario`, criado sem NENHUM dependente: `activities` e
    `workout_plans` tem FK `ON DELETE RESTRICT` para `users`, entao um usuario
    com qualquer um dos dois faria o MySQL recusar o DELETE e o teste passaria
    a testar o RESTRICT do vizinho, nao o CASCADE desta tabela."""
    solitario = cenario["solitario_id"]
    _limpar(raw)

    # Guarda explicita: se algum dia este usuario ganhar um dependente, a
    # mensagem diz o que aconteceu em vez de um `IntegrityError` cru.
    assert _dependentes_restrict(raw, solitario) == {
        "activities": 0,
        "workout_plans": 0,
        "body_composition": 0,
        "daily_biometrics": 0,
    }

    gr.record_generation_run(DRAFT, user_id=solitario)
    # A linha EXISTIA antes - senao "sumiu depois" nao prova nada.
    assert len(_linhas(raw, solitario)) == 1

    with raw.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (solitario,))
    raw.commit()

    assert _linhas(raw, solitario) == []


def test_o_request_do_job_sobrevive_ao_ttl_de_seis_horas(cenario, raw, dataapi_db):
    """O item do job no DynamoDB expira em 6h levando junto o payload que
    originou a geracao. `request_json` e a copia que fica."""
    _limpar(raw)

    class Pedido:
        def model_dump(self, mode=None):
            return {"template_id": 7, "gym_id": 3, "plan_date": "2026-09-26", "min_rest_seconds": 30}

    gr.record_generation_run(DRAFT, user_id=cenario["user_id"], job_id="job-1", request=Pedido())
    bruto = _linhas(raw, cenario["user_id"])[0]["request_json"]
    guardado = json.loads(bruto) if isinstance(bruto, (str, bytes)) else bruto
    assert guardado == {"template_id": 7, "gym_id": 3, "plan_date": "2026-09-26", "min_rest_seconds": 30}


def test_a_medicao_do_caminho_quente(cenario, raw, db_backend, monkeypatch, fake_rds):
    """Contagem de statements, nao cronometro (politica de
    `tests/support/medicao.py`): a auditoria acrescenta 1 INSERT a geracao e
    1 UPDATE ao save. Nenhum SELECT nos dois."""
    from tests.support.sql_capture import recorded_sql

    _limpar(raw)
    with recorded_sql(db_backend, monkeypatch, fake_rds) as gravados:
        gr.record_generation_run(DRAFT, user_id=cenario["user_id"])
    escritas = [s for s in gravados if "generation_runs" in s.lower()]
    assert len(escritas) == 1, escritas
    assert escritas[0].strip().upper().startswith("INSERT")

    with recorded_sql(db_backend, monkeypatch, fake_rds) as gravados:
        with app_db.transaction() as conn:
            gr.link_run_to_plan(
                conn,
                user_id=cenario["user_id"],
                plan_id=cenario["planos"][0],
                raw_payload=DRAFT["raw_payload"],
            )
    ligacoes = [s for s in gravados if "generation_runs" in s.lower()]
    assert len(ligacoes) == 1, ligacoes
    assert ligacoes[0].strip().upper().startswith("UPDATE")
