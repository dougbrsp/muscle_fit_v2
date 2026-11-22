"""Vinculo geracao -> ficha com o formato REAL da tela (auditoria em `generation_context`)."""

from __future__ import annotations

from app.generation import generation_runs as gr

AUDIT = {"template_id": 3, "slot_sequence": 1, "engine_version": "v", "volume_mode": "x"}


class _Cur:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))


class _Conn:
    def __init__(self):
        self.cur = _Cur()

    def cursor(self):
        return self.cur


def test_extract_audit_formatos_da_tela_e_do_nivel_de_cima():
    assert gr.extract_audit({"generation_context": {"generation_audit": AUDIT}}) == AUDIT
    assert gr.extract_audit({"generation_audit": AUDIT}) == AUDIT
    assert gr.extract_audit({"generation_context": {"x": 1}}) is None
    assert gr.extract_audit({"generation_context": None}) is None


def test_link_com_formato_da_tela_faz_update_com_o_hash_certo():
    conn = _Conn()
    gr.link_run_to_plan(conn, user_id=4, plan_id=9, raw_payload={"generation_context": {"generation_audit": AUDIT}})
    assert conn.cur.calls == [(gr.LINK_SQL, (9, 4, gr.audit_hash(AUDIT)))]


def test_link_nivel_de_cima_continua_e_sem_auditoria_nada_acontece():
    conn = _Conn()
    gr.link_run_to_plan(conn, user_id=4, plan_id=9, raw_payload={"generation_audit": AUDIT})
    assert len(conn.cur.calls) == 1
    vazio = _Conn()
    gr.link_run_to_plan(vazio, user_id=4, plan_id=9, raw_payload={"generation_context": {}})
    gr.link_run_to_plan(vazio, user_id=4, plan_id=9, raw_payload=None)
    assert vazio.cur.calls == []


def test_audit_sql_expr_espelha_extract_audit_nas_mesmas_chaves_e_ordem():
    expr = gr.audit_sql_expr("wp.raw_payload")
    assert expr == (
        "(CASE WHEN JSON_TYPE(JSON_EXTRACT(wp.raw_payload, '$.generation_audit')) = 'OBJECT' "
        "THEN JSON_EXTRACT(wp.raw_payload, '$.generation_audit') "
        "ELSE JSON_EXTRACT(wp.raw_payload, '$.generation_context.generation_audit') END)"
    )
    assert expr.index(gr.AUDIT_JSON_PATH) < expr.index(gr.AUDIT_CONTEXT_JSON_PATH)
    assert gr.AUDIT_JSON_PATH == "$.generation_audit"
    assert gr.AUDIT_CONTEXT_JSON_PATH == "$.generation_context.generation_audit"


def test_extract_audit_preferencia_topo_depois_contexto_e_null_nao_conta():
    outro = {"x": 1}
    assert gr.extract_audit({"generation_audit": AUDIT, "generation_context": {"generation_audit": outro}}) == AUDIT
    assert gr.extract_audit({"generation_context": {"generation_audit": AUDIT}}) == AUDIT
    assert gr.extract_audit({"generation_audit": None, "generation_context": {"generation_audit": AUDIT}}) == AUDIT
    assert gr.extract_audit({"generation_audit": None}) is None
    assert gr.extract_audit({}) is None
