"""Sugestão de perfil de resistência por IA (#288, lote 42): cliente de IA simulado, banco em memória."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import prompt_store
from app import resistance_profile_ai as mod
from app.domain import resistance_profile as dono
from tests.support.resistance_profiles_fake import FakeProfilesDB

USER = 7


def _resposta(**sobre):
    """Uma resposta válida montada do vocabulário do dono (sem códigos escritos à mão)."""
    dados = {c: next(iter(v)) for c, v in dono.export_vocabulary().items() if c in mod._COMPONENTS}
    dados.update(flags=[], confidence=sorted(dono.CONFIDENCES)[0], status="suggested", rationale=" Mecânica clara. ")
    dados.update(sobre)
    return SimpleNamespace(data=dados, model="modelo-x")


@pytest.fixture
def db(monkeypatch):
    banco = FakeProfilesDB()
    for pid, (eq, ex) in {100: (1, 10), 101: (1, 11), 102: (2, 10)}.items():
        banco.add_pair(pid, eq, ex)
    monkeypatch.setattr(mod, "transaction", banco.conn)
    monkeypatch.setattr(mod, "_rag_text", lambda: "rag")
    return banco


def _ia(monkeypatch, *respostas):
    """Fila de respostas; uma exceção na fila é levantada na chamada."""
    fila, chamadas = list(respostas), []

    def falso(prompt, model, schema, **kw):
        chamadas.append((prompt, model, schema, kw))
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(mod, "call_openai_structured", falso)
    return chamadas


def test_sucesso_grava_suggested_com_historico_e_model_version(db, monkeypatch):
    chamadas = _ia(monkeypatch, _resposta())
    out = mod.suggest_pairs([100], USER)
    assert (out["processed"], out["suggested"], out["skipped"], out["failed"]) == (1, 1, 0, [])
    perfil = next(iter(db.profiles.values()))
    assert (perfil["status"], perfil["origin"], perfil["model_version"], perfil["rationale"]) == (
        "suggested", "ai", "modelo-x", "Mecânica clara.")
    assert db.history[0]["origin"] == "ai" and db.history[0]["actor_user_id"] == USER
    assert chamadas[0][2] == dono.suggestion_output_schema() and "{{" not in chamadas[0][0]


def test_resposta_fora_do_vocabulario_vira_falha_do_par(db, monkeypatch):
    _ia(monkeypatch, _resposta(mechanism="inventado"), _resposta())
    out = mod.suggest_pairs([100, 101], USER)
    assert out["failed"] == [{"id": 100, "code": mod.ERR_INVALID_RESPONSE}] and out["suggested"] == 1
    assert len(db.profiles) == 1


@pytest.mark.parametrize("status", ["reviewed", "verified", "rejected"])
def test_ia_nao_sobrescreve_revisado_verificado_rejeitado(db, monkeypatch, status):
    db.add_profile(100, status=status, origin="admin")
    chamadas = _ia(monkeypatch)
    out = mod.suggest_pairs([100], USER)
    assert out["skipped"] == 1 and out["suggested"] == 0 and not chamadas and not db.history
    assert next(iter(db.profiles.values()))["status"] == status


def test_corrida_status_mudou_entre_a_leitura_e_a_gravacao_nao_sobrescreve(db, monkeypatch):
    """A leitura inicial viu `suggested`; antes da gravação um admin revisou: `can_transition` no FOR UPDATE barra."""
    db.add_profile(100, status="suggested")

    def ia(*a, **k):
        next(iter(db.profiles.values()))["status"] = "reviewed"
        return _resposta()

    monkeypatch.setattr(mod, "call_openai_structured", ia)
    out = mod.suggest_pairs([100], USER)
    assert out["skipped"] == 1 and out["suggested"] == 0 and not db.history


def test_suggested_existente_e_atualizado_com_historico(db, monkeypatch):
    db.add_profile(100, status="suggested")
    _ia(monkeypatch, _resposta())
    assert mod.suggest_pairs([100], USER)["suggested"] == 1
    assert len(db.profiles) == 1 and next(iter(db.profiles.values()))["model_version"] == "modelo-x"
    assert json.loads(db.history[0]["before_json"]) == {"status": "suggested"}


def test_teto_de_pares_e_ids_invalidos_ou_repetidos(db, monkeypatch):
    for pid in range(200, 200 + dono.SUGGEST_PAIRS_PER_JOB + 5):
        db.add_pair(pid, 1, 10)
    chamadas = _ia(monkeypatch, *[_resposta() for _ in range(dono.SUGGEST_PAIRS_PER_JOB)])
    ids = [True, 0, -1, "x", 200, 200, *range(201, 220)]
    out = mod.suggest_pairs(ids, USER)
    assert out["processed"] == len(chamadas) == dono.SUGGEST_PAIRS_PER_JOB


def test_erro_da_ia_num_par_nao_derruba_os_outros(db, monkeypatch):
    _ia(monkeypatch, RuntimeError("timeout"), _resposta())
    out = mod.suggest_pairs([100, 101, 999], USER)
    assert out["failed"] == [{"id": 100, "code": mod.ERR_AI_FAILED}, {"id": 999, "code": mod.ERR_PAIR_NOT_FOUND}]
    assert out["suggested"] == 1 and [p["equipment_exercise_id"] for p in db.profiles.values()] == [101]


def test_erro_de_gravacao_num_par_nao_derruba_os_outros(db, monkeypatch):
    _ia(monkeypatch, _resposta(), _resposta())
    original = mod._write

    def escreve(pid, *a):
        if pid == 100:
            raise RuntimeError("banco")
        return original(pid, *a)

    monkeypatch.setattr(mod, "_write", escreve)
    out = mod.suggest_pairs([100, 101], USER)
    assert out["failed"] == [{"id": 100, "code": mod.ERR_WRITE_FAILED}] and out["suggested"] == 1


def _util():
    return mod.async_jobs.LAMBDA_TIMEOUT_SECONDS[mod.LAMBDA_FUNCTION] - mod.RESERVE_SECONDS


def test_orcamento_de_tempo_esgotado_marca_not_processed(db, monkeypatch):
    chamadas = _ia(monkeypatch)
    tempos = iter([0, _util() - mod.MIN_CALL_SECONDS + 1, _util() - mod.MIN_CALL_SECONDS + 1])
    monkeypatch.setattr(mod, "_now", lambda: next(tempos))
    out = mod.suggest_pairs([100, 101], USER)
    assert [f["code"] for f in out["failed"]] == [mod.ERR_NOT_PROCESSED] * 2 and not chamadas


def test_chamada_perto_do_fim_recebe_timeout_menor(db, monkeypatch):
    chamadas = _ia(monkeypatch, _resposta(), _resposta())
    tempos = iter([0, 0, _util() - 10])  # inicio, 1o par (todo o orçamento), 2o par (10 s restantes)
    monkeypatch.setattr(mod, "_now", lambda: next(tempos))
    mod.suggest_pairs([100, 101], USER)
    t1, t2 = (c[3]["timeout"] for c in chamadas)
    assert t1 == mod.CALL_TIMEOUT_SECONDS and t2 == 10 < t1


def test_pares_acima_do_teto_viram_not_processed(db, monkeypatch):
    for pid in range(200, 200 + dono.SUGGEST_PAIRS_PER_JOB + 2):
        db.add_pair(pid, 1, 10)
    _ia(monkeypatch, *[_resposta() for _ in range(dono.SUGGEST_PAIRS_PER_JOB)])
    ids = list(range(200, 200 + dono.SUGGEST_PAIRS_PER_JOB + 2))
    out = mod.suggest_pairs(ids, USER)
    assert out["failed"] == [{"id": i, "code": mod.ERR_NOT_PROCESSED} for i in ids[dono.SUGGEST_PAIRS_PER_JOB:]]


def test_rag_garante_o_rag_local_antes_de_ler(monkeypatch, tmp_path):
    from app import rag_chunking, rag_storage

    ordem = []
    (tmp_path / mod.RAG_FILENAME).write_text("trecho do rag", encoding="utf-8")
    monkeypatch.setattr(rag_chunking, "RAG_MARKDOWN_DIR", tmp_path)
    monkeypatch.setattr(rag_storage, "ensure_rag_local", lambda: ordem.append("sync") or tmp_path)
    assert "trecho do rag" in mod._rag_text() and ordem == ["sync"]


def test_job_type_mapeado_na_lambda_de_ia():
    assert mod.async_jobs.JOB_TYPE_LAMBDA[mod.JOB_TYPE_SUGGEST] == mod.LAMBDA_FUNCTION


def test_dispatch_usa_o_payload_e_o_usuario(db, monkeypatch):
    _ia(monkeypatch, _resposta())
    out = mod.dispatch_suggest({"equipment_exercise_ids": [100]}, "7")
    assert out["suggested"] == 1 and db.history[0]["actor_user_id"] == 7
    assert mod.dispatch_suggest({}, 7)["processed"] == 0


def test_prompt_renderizado_sem_placeholders_faltando():
    try:
        prompt = mod.build_prompt({"exercise_name": "Agachamento", "equipment_name": "Leg press"}, rag="trecho")
    except prompt_store.PromptTemplateNotFound:
        pytest.skip("submódulo api/privado ausente")
    assert "{{" not in prompt and "Agachamento" in prompt and "trecho" in prompt
    assert dono.RESISTANCE_VOCABULARY_VERSION in prompt
