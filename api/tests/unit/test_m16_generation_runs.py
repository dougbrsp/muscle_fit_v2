"""M16 - a auditoria da geracao (`generation_audit`) passa a ser persistida em
`generation_runs` (`app/generation/generation_runs.py`, migracao 054).

Sem MySQL e sem OpenAI: a conexao e um duble que so REGISTRA os statements.
E isso que permite provar as tres coisas que importam neste PR:

1. o caminho quente da geracao ganha **exatamente um statement** (um INSERT,
   nenhum SELECT) - a contagem e deterministica, nao depende da maquina
   (politica de `tests/support/medicao.py`);
2. **falhar a escrita nao quebra a geracao** - exigencia 1 do M16;
3. a ligacao rascunho -> ficha fecha pelo hash do bloco, **sem mudar um byte
   do `generation_audit`** - exigencia 2.

Rodar: cd api && python -m pytest tests/unit/test_m16_generation_runs.py
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.generation import generation_runs as gr

# Recorte fiel do bloco do Modo A (`generation/mode_a.py`), com as chaves fora
# de ordem de proposito: o hash canonico nao pode depender da ordem.
AUDIT_MODO_A = {
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

# Recorte do Modo B (`generation/mode_b.py`): SUBCONJUNTO do anterior - nao ha
# `window`, `activation` nem `selection`. E por isso que o bloco vai num JSON
# e nao em colunas.
AUDIT_MODO_B = {
    "template_id": 7,
    "slot_sequence": 1,
    "volume_mode": "equipment_series",
    "engine_version": "v2.1",
    "safety_clamps": [{"exercise_id": 9, "from_rpe": 10, "to_rpe": 8}],
    "substitutions": [],
    "fallbacks": [],
    "llm_used": False,
}


def _draft(audit, **extra):
    rascunho = {
        "name": "Modelo — Slot A",
        "plan_date": "2026-09-26",
        "template_id": audit.get("template_id"),
        "template_slot_sequence": audit.get("slot_sequence"),
        "exercises": [],
        "raw_payload": {"engine_version": "v2.1", "used_ai": False, "generation_audit": audit},
    }
    rascunho.update(extra)
    return rascunho


class CursorFalso:
    def __init__(self, conexao):
        self.conexao = conexao
        self.lastrowid = 4242
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        if self.conexao.explode:
            raise RuntimeError("banco fora do ar")
        self.conexao.statements.append((" ".join(sql.split()), params))


class ConexaoFalsa:
    def __init__(self, explode: bool = False):
        self.statements: list[tuple[str, tuple | None]] = []
        self.explode = explode

    def cursor(self):
        return CursorFalso(self)


@pytest.fixture
def conexao(monkeypatch):
    conn = ConexaoFalsa()

    @contextmanager
    def _transaction():
        yield conn

    import app.db as db

    monkeypatch.setattr(db, "transaction", _transaction)
    return conn


# ---------------------------------------------------------------------------
# Identidade do bloco (a ligacao rascunho -> ficha)
# ---------------------------------------------------------------------------


def test_o_hash_nao_depende_da_ordem_das_chaves():
    """Os dois lados da ligacao serializam o bloco em momentos diferentes (a
    Lambda, e depois o save). Se a ordem mudasse o hash, a ficha nunca
    encontraria a geracao que a originou."""
    embaralhado = dict(reversed(list(AUDIT_MODO_A.items())))
    assert gr.audit_hash(embaralhado) == gr.audit_hash(AUDIT_MODO_A)


def test_blocos_diferentes_tem_hashes_diferentes():
    assert gr.audit_hash(AUDIT_MODO_A) != gr.audit_hash(AUDIT_MODO_B)


def test_o_hash_muda_quando_o_conteudo_muda():
    outro = {**AUDIT_MODO_A, "llm_used": True}
    assert gr.audit_hash(outro) != gr.audit_hash(AUDIT_MODO_A)


def test_extract_audit_so_aceita_bloco_de_verdade():
    assert gr.extract_audit({"generation_audit": AUDIT_MODO_A}) == AUDIT_MODO_A
    # Ficha criada a mao, ficha antiga e slot de descanso: nada a persistir.
    assert gr.extract_audit(None) is None
    assert gr.extract_audit({}) is None
    assert gr.extract_audit({"generation_audit": None}) is None
    assert gr.extract_audit({"generation_audit": []}) is None
    assert gr.extract_audit("nem dict") is None


# ---------------------------------------------------------------------------
# Escrita na geracao: 1 statement, nenhum SELECT
# ---------------------------------------------------------------------------


def test_gravar_custa_exatamente_um_insert_e_nenhuma_consulta(conexao):
    """Medicao do caminho quente: a auditoria acrescenta UM statement, e ele e
    um INSERT. Zero SELECT - tudo o que a linha grava ja esta no rascunho em
    memoria."""
    assert gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1, job_id="job-1") == 4242
    assert len(conexao.statements) == 1
    sql, _params = conexao.statements[0]
    assert sql.upper().startswith("INSERT INTO GENERATION_RUNS")
    assert "SELECT" not in sql.upper()


def test_as_colunas_promovidas_saem_do_bloco(conexao):
    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=5, job_id="job-abc")
    _sql, params = conexao.statements[0]
    user_id, job_id, template_id, slot_sequence, volume_mode, engine_version, llm_used, plan_date, *_ = params
    assert (user_id, job_id, template_id, slot_sequence) == (5, "job-abc", 7, 2)
    assert (volume_mode, engine_version, llm_used, plan_date) == ("percent_budget", "v2.1", 0, "2026-09-26")


def test_o_json_gravado_e_o_bloco_inteiro_sem_campo_novo(conexao):
    """Exigencia 2 do M16: persistir NAO muda o formato do bloco. Nenhuma
    chave acrescentada (um `generation_run_id` carimbado dentro dele seria
    justamente a mudanca proibida) e nenhuma removida."""
    import json

    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1)
    _sql, params = conexao.statements[0]
    gravado = json.loads(params[9])
    assert gravado == AUDIT_MODO_A


def test_o_bloco_do_modo_b_cabe_na_mesma_linha(conexao):
    """O Modo B grava um SUBCONJUNTO das chaves do Modo A. A mesma tabela
    aceita os dois porque o bloco vai num JSON, nao em colunas."""
    import json

    gr.record_generation_run(_draft(AUDIT_MODO_B), user_id=1)
    _sql, params = conexao.statements[0]
    assert json.loads(params[9]) == AUDIT_MODO_B
    assert params[4] == "equipment_series"


def test_o_request_do_job_e_guardado_antes_de_expirar(conexao):
    """O item do job no DynamoDB expira em 6h e leva junto o payload que
    originou a geracao. `request_json` e a copia que sobrevive."""
    import json

    class PedidoPydanticFalso:
        def model_dump(self, mode=None):
            assert mode == "json"
            return {"template_id": 7, "plan_date": "2026-09-26", "min_rest_seconds": 30}

    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1, request=PedidoPydanticFalso())
    _sql, params = conexao.statements[0]
    assert json.loads(params[10])["template_id"] == 7


def test_sem_request_a_coluna_fica_nula(conexao):
    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1)
    assert conexao.statements[0][1][10] is None


def test_rascunho_sem_auditoria_nao_escreve_nada(conexao):
    """Slot de descanso (`_empty_rest_day_draft`) nao produz auditoria - e nao
    ha o que persistir. Um statement aqui seria custo puro."""
    assert gr.record_generation_run({"raw_payload": {}}, user_id=1) is None
    assert gr.record_generation_run({}, user_id=1) is None
    assert not conexao.statements


def test_sem_usuario_nao_escreve(conexao):
    """`user_id` e NOT NULL com FK: sem dono, a linha nao existe."""
    assert gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=None) is None
    assert not conexao.statements


def test_o_bloco_manda_e_o_rascunho_e_so_o_reserva(conexao):
    """`template_id`/`slot_sequence` saem do BLOCO; o rascunho so responde
    quando o bloco nao traz. A escolha e por `is not None`, nao por `or`:
    um `0` legitimo cairia para o reserva."""
    draft = _draft({k: v for k, v in AUDIT_MODO_A.items() if k not in ("template_id", "slot_sequence")})
    draft["template_id"] = 11
    draft["template_slot_sequence"] = 0
    gr.record_generation_run(draft, user_id=1)
    _sql, params = conexao.statements[0]
    assert (params[2], params[3]) == (11, 0)


def test_job_id_longo_demais_e_cortado_e_nao_derruba(conexao):
    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1, job_id="x" * 80)
    assert conexao.statements[0][1][1] == "x" * 36


# ---------------------------------------------------------------------------
# Exigencia 1: a escrita nunca quebra a geracao
# ---------------------------------------------------------------------------


def test_banco_fora_do_ar_nao_quebra_a_geracao(monkeypatch, caplog):
    """A ficha e o produto; a auditoria e sobre ela. Se a persistencia
    falhar, a geracao segue - e a falha vira log."""

    @contextmanager
    def _transaction():
        yield ConexaoFalsa(explode=True)

    import app.db as db

    monkeypatch.setattr(db, "transaction", _transaction)
    with caplog.at_level("ERROR"):
        assert gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1, job_id="job-1") is None
    assert "generation_runs" in caplog.text


def test_transaction_indisponivel_nao_quebra_a_geracao(monkeypatch, caplog):
    """Ate o proprio `transaction()` falhando (banco inalcancavel na Lambda)
    e engolido: nenhum caminho desta funcao levanta."""

    def _explode():
        raise RuntimeError("sem conexao")

    import app.db as db

    monkeypatch.setattr(db, "transaction", _explode)
    with caplog.at_level("ERROR"):
        assert gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1) is None


def test_o_dispatcher_persiste_uma_vez_e_devolve_o_rascunho(monkeypatch):
    """A escrita mora no dispatcher porque ele e o UNICO ponto por onde passam
    os quatro caminhos de geracao - uma chamada, nao quatro."""
    from app import plan_generation_core as core

    rascunho = _draft(AUDIT_MODO_A)
    chamadas: list[dict] = []

    monkeypatch.setattr(core, "_dispatch_plan_generation", lambda payload, user_id: rascunho)
    monkeypatch.setattr(
        gr,
        "record_generation_run",
        lambda draft, **kwargs: chamadas.append({"draft": draft, **kwargs}),
    )

    devolvido = core.dispatch_plan_generation(object(), 9)
    assert devolvido is rascunho
    assert len(chamadas) == 1
    assert chamadas[0]["user_id"] == 9


def test_a_assinatura_do_dispatcher_nao_mudou_com_a_auditoria(monkeypatch):
    """Regressao do CI do PR #231. A primeira versao deste PR acrescentou um
    `job_id` a `dispatch_plan_generation`; um duble com a aridade antiga virou
    `TypeError` dentro do handler da Lambda, e o handler marcou como `failed`
    um job cuja GERACAO tinha corrido bem. Auditoria que muda o contrato de
    quem ela audita quebra a geracao pela porta da frente - exatamente o que o
    M16 existe para impedir."""
    import inspect

    from app import plan_generation_core as core

    assert list(inspect.signature(core.dispatch_plan_generation).parameters) == ["payload", "user_id"]


def test_ate_o_import_da_auditoria_falhando_devolve_a_ficha(monkeypatch):
    """O `try` do dispatcher cobre o proprio import do modulo de auditoria,
    nao so a escrita."""
    import builtins

    from app import plan_generation_core as core

    rascunho = _draft(AUDIT_MODO_A)
    monkeypatch.setattr(core, "_dispatch_plan_generation", lambda payload, user_id: rascunho)

    original = builtins.__import__

    def _import(name, *args, **kwargs):
        if name.endswith("generation.generation_runs"):
            raise ImportError("modulo indisponivel")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)
    assert core.dispatch_plan_generation(object(), 9) is rascunho


# ---------------------------------------------------------------------------
# `job_id`: chega pelo lado, nunca pela assinatura da geracao
# ---------------------------------------------------------------------------


def test_o_job_id_chega_pelo_contexto_e_e_gravado(conexao):
    gr.set_current_job_id("job-do-dynamo")
    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1)
    assert conexao.statements[0][1][1] == "job-do-dynamo"


def test_o_job_id_e_CONSUMIDO_e_nao_vaza_para_a_geracao_seguinte(conexao):
    """Container quente da Lambda: sem consumir, a invocacao seguinte herdaria
    o `job_id` da anterior e a linha nasceria com procedencia falsa - que e
    pior que nenhuma."""
    gr.set_current_job_id("job-1")
    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1)
    gr.record_generation_run(_draft(AUDIT_MODO_B), user_id=1)
    assert [s[1][1] for s in conexao.statements] == ["job-1", None]


def test_o_job_id_explicito_ganha_do_contexto(conexao):
    gr.set_current_job_id("do-contexto")
    gr.record_generation_run(_draft(AUDIT_MODO_A), user_id=1, job_id="explicito")
    assert conexao.statements[0][1][1] == "explicito"


def test_a_lambda_registra_o_job_id_sem_tocar_na_chamada_da_geracao(monkeypatch):
    """O handler continua chamando `dispatch_plan_generation(payload, user_id)`
    - dois argumentos, como sempre - e o `job_id` vai por fora."""
    from app import lambda_plan_generator as lpg

    vistos = {}
    monkeypatch.setattr(lpg, "dispatch_plan_generation", lambda payload, user_id: {"ok": True})
    monkeypatch.setattr(gr, "set_current_job_id", lambda job_id: vistos.setdefault("job_id", job_id))
    monkeypatch.setattr("app.async_jobs.mark_done", lambda job_id, result: vistos.setdefault("done", result))
    monkeypatch.setattr(
        "app.async_jobs.mark_failed", lambda job_id, error: pytest.fail(f"job nao podia falhar: {error}")
    )

    monkeypatch.setattr("app.async_jobs.claim_job", lambda job_id: True)  # M81
    lpg.handler({"job_id": "j", "payload": {"template_id": 7}, "user_id": 3}, None)
    assert vistos["done"] == {"ok": True}
    assert vistos["job_id"] == "j"


def test_falha_ao_registrar_o_job_id_nao_derruba_o_job(monkeypatch):
    from app import lambda_plan_generator as lpg

    vistos = {}

    def _explode(job_id):
        raise RuntimeError("sem contexto")

    monkeypatch.setattr(lpg, "dispatch_plan_generation", lambda payload, user_id: {"ok": True})
    monkeypatch.setattr(gr, "set_current_job_id", _explode)
    monkeypatch.setattr("app.async_jobs.mark_done", lambda job_id, result: vistos.setdefault("done", result))
    monkeypatch.setattr(
        "app.async_jobs.mark_failed", lambda job_id, error: pytest.fail(f"job nao podia falhar: {error}")
    )

    monkeypatch.setattr("app.async_jobs.claim_job", lambda job_id: True)  # M81
    lpg.handler({"job_id": "j", "payload": {"template_id": 7}, "user_id": 3}, None)
    assert vistos["done"] == {"ok": True}


def test_falha_da_auditoria_no_dispatcher_nao_vira_502(monkeypatch):
    """Redundancia deliberada com o teste da funcao: quem le o dispatcher
    precisa ver que a garantia vale NESTE ponto de chamada."""
    from app import plan_generation_core as core

    rascunho = _draft(AUDIT_MODO_A)

    @contextmanager
    def _transaction():
        yield ConexaoFalsa(explode=True)

    import app.db as db

    monkeypatch.setattr(db, "transaction", _transaction)
    monkeypatch.setattr(core, "_dispatch_plan_generation", lambda payload, user_id: rascunho)
    assert core.dispatch_plan_generation(object(), 9) is rascunho


# ---------------------------------------------------------------------------
# Ligacao rascunho -> ficha no save
# ---------------------------------------------------------------------------


def test_a_ligacao_custa_um_update_e_usa_o_hash_do_bloco():
    conn = ConexaoFalsa()
    gr.link_run_to_plan(
        conn, user_id=3, plan_id=77, raw_payload={"generation_audit": AUDIT_MODO_A, "used_ai": False}
    )
    assert len(conn.statements) == 1
    sql, params = conn.statements[0]
    assert sql.upper().startswith("UPDATE GENERATION_RUNS")
    assert params == (77, 3, gr.audit_hash(AUDIT_MODO_A))


def test_a_ligacao_so_pega_linha_ainda_sem_ficha_e_a_mais_antiga():
    """`workout_plan_id IS NULL` + `ORDER BY id ASC LIMIT 1`: o hash NAO e
    unico, entao duas geracoes de auditoria identica nao podem disputar a
    mesma linha nem sobrescrever uma ligacao ja feita."""
    assert "workout_plan_id IS NULL" in gr.LINK_SQL
    assert "ORDER BY id ASC" in gr.LINK_SQL
    assert "LIMIT 1" in gr.LINK_SQL


def test_ficha_sem_auditoria_no_payload_nao_gasta_statement():
    """Ficha criada a mao ou cliente que nao ecoa o `raw_payload`: nada a
    ligar, nenhum statement."""
    conn = ConexaoFalsa()
    gr.link_run_to_plan(conn, user_id=3, plan_id=77, raw_payload={"engine_version": "v2.1"})
    gr.link_run_to_plan(conn, user_id=3, plan_id=77, raw_payload=None)
    gr.link_run_to_plan(conn, user_id=None, plan_id=77, raw_payload={"generation_audit": AUDIT_MODO_A})
    assert not conn.statements


def test_falha_da_ligacao_nao_derruba_o_save(caplog):
    """A ficha ja esta gravada quando a ligacao roda. Ela nao pode virar 500."""
    with caplog.at_level("ERROR"):
        gr.link_run_to_plan(
            ConexaoFalsa(explode=True), user_id=3, plan_id=77, raw_payload={"generation_audit": AUDIT_MODO_A}
        )
    assert "generation_runs" in caplog.text
