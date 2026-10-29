"""M4 - geracao ASSINCRONA da descricao de execucao no primeiro uso do
briefing (`app/exercise_description_autogen.py` + a reserva da migracao 042
em `app/exercise_description_service.py`).

Nenhuma chamada real: a OpenAI nunca e tocada (nem por duble - este caminho
nao chega nela dentro da `api-core`), o DynamoDB e o `lambda:Invoke` sao
dubles, e o banco e um cursor de mentira que implementa a SEMANTICA do UPDATE
condicional de reserva sobre um dicionario em memoria. E isso que permite
simular dois usuarios abrindo o mesmo exercicio ao mesmo tempo.

Rodar: cd api && python -m pytest tests/unit/test_m4_descricao_execucao_autogen.py
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from app import exercise_description_autogen as autogen
from app import exercise_description_service as svc

CLAIM_MARK = "SET execution_description_generation_started_at = %s"
RELEASE_MARK = "SET execution_description_generation_started_at = NULL"


class CatalogoFalso:
    """Uma linha de `exercise_catalog`, com o UPDATE de reserva implementado
    de verdade: e o unico jeito de provar que duas chamadas simultaneas nao
    viram duas geracoes."""

    def __init__(self, **campos):
        self.row = {
            "id": 42,
            "execution_description": None,
            "execution_description_reviewed_at": None,
            "execution_description_generation_started_at": None,
        }
        self.row.update(campos)
        self.claims = 0

    def cursor(self):
        return CursorFalso(self)


class CursorFalso:
    def __init__(self, catalogo: CatalogoFalso):
        self.catalogo = catalogo
        self.rowcount = -1
        self.sqls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalizado = " ".join(sql.split())
        self.sqls.append(normalizado)
        row = self.catalogo.row

        if " ".join(RELEASE_MARK.split()) in normalizado:
            row["execution_description_generation_started_at"] = None
            self.rowcount = 1
            return

        if " ".join(CLAIM_MARK.split()) in normalizado:
            stamp, exercise_id, cutoff = params
            atual = row["execution_description_generation_started_at"]
            elegivel = (
                int(exercise_id) == int(row["id"])
                and not str(row["execution_description"] or "").strip()
                and row["execution_description_reviewed_at"] is None
                and (atual is None or atual < cutoff)
            )
            if elegivel:
                row["execution_description_generation_started_at"] = stamp
                self.catalogo.claims += 1
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        raise AssertionError(f"SQL inesperado neste teste: {normalizado}")

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class ConnFalsa:
    def __init__(self, catalogo):
        self.catalogo = catalogo

    def cursor(self):
        return self.catalogo.cursor()


@pytest.fixture
def ambiente(monkeypatch):
    """Ambiente com a Lambda de IA configurada + dubles de DynamoDB/invoke."""
    monkeypatch.setenv(autogen.GENERATOR_FUNCTION_ENV_VAR, "musclefit-plan-generator")
    invocacoes: list[tuple[str, dict]] = []
    jobs: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        autogen.async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: (jobs.append((job_type, request)), "job-1")[1]
    )
    monkeypatch.setattr(
        autogen.async_jobs, "invoke_async", lambda env_var, payload: invocacoes.append((env_var, payload))
    )
    return {"invocacoes": invocacoes, "jobs": jobs}


def _ligar_banco(monkeypatch, catalogo: CatalogoFalso):
    @contextmanager
    def _transaction():
        yield ConnFalsa(catalogo)

    monkeypatch.setattr(autogen, "transaction", _transaction)


# ---------------------------------------------------------------------------
# A reserva (migracao 042)
# ---------------------------------------------------------------------------


def test_exercicio_sem_descricao_e_reservavel():
    catalogo = CatalogoFalso()
    assert svc.claim_generation(catalogo.cursor(), 42) is True
    assert catalogo.row["execution_description_generation_started_at"] is not None


def test_exercicio_que_ja_tem_texto_nunca_e_reservado():
    catalogo = CatalogoFalso(execution_description="Execução:\n- Empurre a barra.")
    assert svc.claim_generation(catalogo.cursor(), 42) is False


def test_texto_revisado_por_humano_nunca_e_reservado():
    """Cinto E suspensorio: mesmo que o texto suma por um bug, a marca de
    revisao humana sozinha ja impede a geracao automatica."""
    catalogo = CatalogoFalso(execution_description_reviewed_at=datetime(2026, 9, 19, 10, 0, 0))
    assert svc.claim_generation(catalogo.cursor(), 42) is False


def test_reserva_valida_bloqueia_a_segunda_tentativa():
    catalogo = CatalogoFalso()
    assert svc.claim_generation(catalogo.cursor(), 42) is True
    assert svc.claim_generation(catalogo.cursor(), 42) is False
    assert catalogo.claims == 1


def test_reserva_vencida_e_tomada_de_novo():
    """Lambda morta no meio nao pode prender o exercicio para sempre."""
    velha = datetime.now() - timedelta(minutes=svc.GENERATION_LOCK_MINUTES + 1)
    catalogo = CatalogoFalso(execution_description_generation_started_at=velha)
    assert svc.claim_generation(catalogo.cursor(), 42) is True


def test_reserva_recente_ainda_segura():
    recente = datetime.now() - timedelta(minutes=svc.GENERATION_LOCK_MINUTES - 1)
    catalogo = CatalogoFalso(execution_description_generation_started_at=recente)
    assert svc.claim_generation(catalogo.cursor(), 42) is False


# ---------------------------------------------------------------------------
# O disparo
# ---------------------------------------------------------------------------


def test_primeira_abertura_enfileira_uma_geracao(monkeypatch, ambiente):
    catalogo = CatalogoFalso()
    _ligar_banco(monkeypatch, catalogo)

    assert autogen.dispatch_if_missing(42, owner_user_id=7) is True

    assert ambiente["jobs"] == [("exercise-description", {"exercise_id": 42, "reason": "briefing"})]
    env_var, payload = ambiente["invocacoes"][0]
    assert env_var == "PLAN_GENERATOR_FUNCTION_NAME"
    assert payload == {"job_id": "job-1", "job_kind": "exercise-description", "exercise_id": 42}


def test_dois_usuarios_ao_mesmo_tempo_geram_UMA_vez_so(monkeypatch, ambiente):
    """O requisito duro: o mesmo exercicio aberto por duas pessoas (ou duas
    vezes pela mesma) nao pode virar duas chamadas de LLM."""
    catalogo = CatalogoFalso()
    _ligar_banco(monkeypatch, catalogo)

    resultados = [autogen.dispatch_if_missing(42, owner_user_id=7) for _ in range(5)]

    assert resultados == [True, False, False, False, False]
    assert len(ambiente["invocacoes"]) == 1
    assert catalogo.claims == 1


def test_exercicio_que_ja_tem_texto_nao_dispara_nada(monkeypatch, ambiente):
    catalogo = CatalogoFalso(execution_description="Execução:\n- Empurre a barra.")
    _ligar_banco(monkeypatch, catalogo)

    assert autogen.dispatch_if_missing(42, owner_user_id=7) is False
    assert ambiente["invocacoes"] == [] and ambiente["jobs"] == []


def test_ambiente_sem_a_lambda_nao_toca_no_banco(monkeypatch):
    """Um uvicorn solto / um script: nada para onde enfileirar, nada a fazer -
    e nenhum UPDATE inutil."""
    monkeypatch.delenv(autogen.GENERATOR_FUNCTION_ENV_VAR, raising=False)

    def _explode():
        raise AssertionError("nao deveria abrir transacao")

    monkeypatch.setattr(autogen, "transaction", _explode)
    assert autogen.dispatch_if_missing(42, owner_user_id=7) is False


def test_exercicio_sem_id_nao_dispara(monkeypatch, ambiente):
    monkeypatch.setattr(autogen, "transaction", lambda: (_ for _ in ()).throw(AssertionError("nao abre")))
    assert autogen.dispatch_if_missing(None, owner_user_id=7) is False


@pytest.mark.parametrize("quebrado", ["create_job", "invoke_async"])
def test_falha_ao_enfileirar_devolve_a_reserva_e_nao_levanta(monkeypatch, ambiente, quebrado):
    """Se o disparo nao sai, a proxima abertura tenta de novo - em vez de
    esperar 15 minutos por uma reserva que nunca virou geracao."""
    catalogo = CatalogoFalso()
    _ligar_banco(monkeypatch, catalogo)

    def _boom(*args, **kwargs):
        raise RuntimeError("AccessDeniedException")

    monkeypatch.setattr(autogen.async_jobs, quebrado, _boom)

    assert autogen.dispatch_if_missing(42, owner_user_id=7) is False
    # Reserva devolvida = a proxima abertura da tela consegue disparar de novo.
    assert catalogo.row["execution_description_generation_started_at"] is None
    assert svc.claim_generation(catalogo.cursor(), 42) is True


def test_falha_do_banco_nao_levanta(monkeypatch, ambiente):
    @contextmanager
    def _transaction():
        raise RuntimeError("database_unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(autogen, "transaction", _transaction)
    assert autogen.dispatch_if_missing(42, owner_user_id=7) is False
    assert ambiente["invocacoes"] == []


def test_disparo_emite_evento_estruturado(monkeypatch, ambiente, caplog):
    catalogo = CatalogoFalso()
    _ligar_banco(monkeypatch, catalogo)
    with caplog.at_level("INFO", logger="musclefit.events"):
        autogen.dispatch_if_missing(42, owner_user_id=7)
    linha = [m for m in caplog.messages if "exec_description.dispatched" in m]
    assert linha and '"exercise_id": 42' in linha[0] and '"job_id": "job-1"' in linha[0]


def test_falha_de_disparo_emite_evento_estruturado(monkeypatch, ambiente, caplog):
    catalogo = CatalogoFalso()
    _ligar_banco(monkeypatch, catalogo)
    monkeypatch.setattr(
        autogen.async_jobs, "invoke_async", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with caplog.at_level("INFO", logger="musclefit.events"):
        autogen.dispatch_if_missing(42, owner_user_id=7)
    assert any("exec_description.dispatch_failed" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# O outro lado: o job dentro da Lambda de IA
# ---------------------------------------------------------------------------


def test_job_nunca_pede_force(monkeypatch):
    """O caminho automatico nao tem humano confirmando nada: ele nao pode ter
    permissao de sobrescrever texto, nunca."""
    catalogo = CatalogoFalso()
    _ligar_banco(monkeypatch, catalogo)
    capturado = {}

    def _fake_generate(cursor, exercise_id, *, force=False, model=None):
        capturado.update({"exercise_id": exercise_id, "force": force, "model": model})
        return {"exercise_id": exercise_id, "status": svc.STATUS_GENERATED, "model": "gpt-5.4-mini"}

    monkeypatch.setattr(autogen.descriptions, "generate_and_store", _fake_generate)

    resultado = autogen.run_generation_job(42, model="gpt-5.4-mini")

    assert capturado == {"exercise_id": 42, "force": False, "model": "gpt-5.4-mini"}
    assert resultado["status"] == svc.STATUS_GENERATED


def test_job_devolve_a_reserva_quando_alguem_chegou_antes(monkeypatch):
    catalogo = CatalogoFalso(execution_description_generation_started_at=datetime.now())
    _ligar_banco(monkeypatch, catalogo)
    monkeypatch.setattr(
        autogen.descriptions,
        "generate_and_store",
        lambda *a, **k: {"exercise_id": 42, "status": svc.STATUS_SKIPPED, "reason": "already_generated"},
    )

    autogen.run_generation_job(42)
    assert catalogo.row["execution_description_generation_started_at"] is None


def test_job_que_falha_MANTEM_a_reserva_como_back_off(monkeypatch):
    """Sem isso, um exercicio que a OpenAI recusa viraria uma chamada de LLM
    por abertura de tela, para sempre."""
    marca = datetime.now()
    catalogo = CatalogoFalso(execution_description_generation_started_at=marca)
    _ligar_banco(monkeypatch, catalogo)
    monkeypatch.setattr(
        autogen.descriptions,
        "generate_and_store",
        lambda *a, **k: {"exercise_id": 42, "status": svc.STATUS_FAILED, "error": "timeout"},
    )

    autogen.run_generation_job(42)
    assert catalogo.row["execution_description_generation_started_at"] == marca


def test_job_sem_exercise_id_falha_alto(monkeypatch):
    """Aqui, sim, levanta: quem chama e o handler da Lambda, que transforma
    isso num job `failed` no DynamoDB - nao a tela do usuario."""
    with pytest.raises(ValueError):
        autogen.run_generation_job(None)


# ---------------------------------------------------------------------------
# A varredura em lote nao repete o que a geracao automatica ja reservou
# ---------------------------------------------------------------------------


class CursorDeVarredura:
    def __init__(self):
        self.sql = ""
        self.params: tuple = ()

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params or ()

    def fetchall(self):
        return []

    def fetchone(self):
        return {"n": 0}


def test_varredura_em_lote_ignora_quem_esta_com_reserva_valida():
    cursor = CursorDeVarredura()
    svc.pending_exercise_ids(cursor, limit=10)
    assert "execution_description_generation_started_at IS NULL" in cursor.sql


def test_varredura_com_force_passa_por_cima_da_reserva():
    cursor = CursorDeVarredura()
    svc.pending_exercise_ids(cursor, limit=10, skip_in_progress=False)
    assert "execution_description_generation_started_at" not in cursor.sql


def test_contagem_de_pendentes_conta_TUDO_que_falta():
    """`remaining` precisa ser honesto: um item reservado ainda nao tem texto,
    e a tela nao pode dizer que o catalogo esta completo por causa disso."""
    cursor = CursorDeVarredura()
    svc.pending_count(cursor)
    assert "execution_description_generation_started_at" not in cursor.sql
