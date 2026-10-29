"""M4 - geracao da descricao de execucao do exercicio (`exercise_description_ai`)
e a persistencia dela (`exercise_description_service`).

Nenhuma chamada real a OpenAI e nenhum banco: `call_openai_structured` e
substituido por um duble e o cursor e um duble que guarda os SQLs.
"""

from __future__ import annotations

import pytest

from app import exercise_description_ai as ai
from app import exercise_description_service as svc
from app.openai_client import StructuredResult

EXERCICIO = {
    "id": 7,
    "name": "Supino reto com barra",
    "body_position": "deitado",
    "movement": "empurrar",
    "primary_joint": "ombro",
    "is_unilateral": 0,
    "is_compound": 1,
    "equipment_names": ["Banco supino reto"],
}

MUSCULOS = [
    {"role": "PRIMARY", "muscle_group_name": "Peitoral maior"},
    {"role": "SECONDARY", "muscle_group_name": "Tríceps"},
]

SAIDA_OK = {
    "setup": ["Deite no banco com os pés apoiados no chão."],
    "execution": ["Desça a barra até o peito.", "Empurre a barra até estender os cotovelos."],
    "cues": ["Não deixe os cotovelos abrirem 90 graus."],
}


# ---------------------------------------------------------------------------
# Prompt e schema
# ---------------------------------------------------------------------------


def test_prompt_leva_nome_equipamentos_musculos_e_atributos():
    prompt = ai.build_prompt(EXERCICIO, MUSCULOS)
    assert "Supino reto com barra" in prompt
    assert "Banco supino reto" in prompt
    assert "Peitoral maior" in prompt
    assert "Tríceps" in prompt
    assert "Movimento: empurrar" in prompt


# M103 P1: os asserts sobre a prosa do prompt (sem prescricao; sem equipamento
# ou sem musculos) moram em `api/privado/tests/unit/test_exercise_description_prompt.py`.


def test_exercicio_sem_equipamento_ou_sem_musculos_ainda_gera_prompt():
    """Ramos sem equipamento e sem musculo renderizam (fragmentos do template
    presentes); o texto e conferido no teste privado."""
    sem_nada = ai.build_prompt({"name": "Exercício solto"}, [])
    completo = ai.build_prompt(EXERCICIO, MUSCULOS)
    renderizou = "Exercício solto" in sem_nada and "{{" not in sem_nada and "{{" not in completo
    assert renderizou


def test_schema_e_strict_compativel():
    """`call_openai_structured` manda `strict: true`: todo objeto precisa de
    `additionalProperties: false` e listar em `required` TODAS as chaves."""
    assert ai.OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(ai.OUTPUT_SCHEMA["required"]) == set(ai.OUTPUT_SCHEMA["properties"])


def test_versao_do_prompt_e_gravada_junto_com_o_texto():
    assert ai.EXEC_DESC_PROMPT_VERSION == "exec-desc-v1"


# ---------------------------------------------------------------------------
# Validacao / composicao do texto
# ---------------------------------------------------------------------------


def test_compose_monta_blocos_rotulados_sem_markdown():
    texto = ai.compose_description(SAIDA_OK)
    assert texto.startswith("Preparação:\n- Deite no banco")
    assert "Execução:\n- Desça a barra até o peito.\n- Empurre a barra" in texto
    assert "Atenção:\n- Não deixe os cotovelos" in texto
    assert "**" not in texto and "#" not in texto


def test_compose_limpa_marcador_de_lista_e_espacos_duplicados():
    texto = ai.compose_description({"setup": [], "execution": ["-  Empurre   a  barra. "], "cues": []})
    assert texto == "Execução:\n- Empurre a barra."


def test_compose_trunca_no_maximo_de_itens_de_cada_bloco():
    demais = {
        "setup": [f"s{i}" for i in range(10)],
        "execution": [f"e{i}" for i in range(10)],
        "cues": [f"c{i}" for i in range(10)],
    }
    texto = ai.compose_description(demais)
    assert texto.count("- s") == ai.MAX_SETUP_STEPS
    assert texto.count("- e") == ai.MAX_EXECUTION_STEPS
    assert texto.count("- c") == ai.MAX_CUES


def test_saida_sem_passo_de_execucao_e_recusada():
    with pytest.raises(ai.ExerciseDescriptionError):
        ai.compose_description({"setup": ["a"], "execution": [], "cues": []})


def test_saida_com_item_nao_textual_e_recusada():
    with pytest.raises(ai.ExerciseDescriptionError):
        ai.compose_description({"setup": [], "execution": [{"passo": 1}], "cues": []})


def test_texto_gigante_e_recusado_em_vez_de_ir_para_a_tela():
    enorme = {"setup": [], "execution": ["x" * (ai.MAX_DESCRIPTION_CHARS + 50)], "cues": []}
    with pytest.raises(ai.ExerciseDescriptionError) as exc:
        ai.compose_description(enorme)
    assert "acima do limite" in str(exc.value)


def test_generate_usa_o_modelo_pedido_e_devolve_auditoria(monkeypatch):
    capturado = {}

    def _fake(prompt, model, schema, *, schema_name, timeout=90, **_kw):
        capturado.update(prompt=prompt, model=model, schema_name=schema_name)
        return StructuredResult(data=SAIDA_OK, model=model, tokens_in=120, tokens_out=80)

    monkeypatch.setattr(ai, "call_openai_structured", _fake)
    resultado = ai.generate_execution_description(EXERCICIO, MUSCULOS, model="gpt-teste")
    assert capturado["model"] == "gpt-teste"
    assert capturado["schema_name"] == ai.SCHEMA_NAME
    assert resultado.prompt_version == ai.EXEC_DESC_PROMPT_VERSION
    assert resultado.tokens_in == 120 and resultado.tokens_out == 80
    assert "Execução:" in resultado.text


def test_exercicio_sem_nome_nao_chega_a_chamar_a_openai(monkeypatch):
    def _explode(*args, **kwargs):  # pragma: no cover - nao deve ser chamado
        raise AssertionError("nao deveria chamar a OpenAI")

    monkeypatch.setattr(ai, "call_openai_structured", _explode)
    with pytest.raises(ai.ExerciseDescriptionError):
        ai.generate_execution_description({"name": "  "}, [])


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


class FakeCursor:
    """Cursor minimo: devolve respostas canned por trecho de SQL e guarda tudo
    o que foi executado."""

    def __init__(self, respostas: dict[str, list[dict]] | None = None):
        self.respostas = respostas or {}
        self.executados: list[tuple[str, tuple]] = []
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        sql_normalizado = " ".join(sql.split())
        self.executados.append((sql_normalizado, params))
        self._rows = []
        for trecho, rows in self.respostas.items():
            if trecho in sql_normalizado:
                self._rows = list(rows)
                break

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def sqls(self, trecho: str) -> list[tuple[str, tuple]]:
        return [item for item in self.executados if trecho in item[0]]


def _cursor_com_exercicio(**overrides):
    linha = {
        "id": 7,
        "name": "Supino reto com barra",
        "execution_description": None,
        "execution_description_prompt_version": None,
        "execution_description_reviewed_by": None,
    }
    linha.update(overrides)
    return FakeCursor(
        {
            "FROM exercise_catalog WHERE id": [linha],
            "FROM exercise_muscle_contributions": MUSCULOS,
            "FROM equipment_exercises": [{"name": "Banco supino reto"}],
            "SELECT COUNT(*) AS n FROM exercise_catalog": [{"n": 3}],
        }
    )


def _fake_geracao(monkeypatch, texto="Execução:\n- Empurre."):
    monkeypatch.setattr(
        svc,
        "generate_execution_description",
        lambda exercise, muscles, model=None: ai.GeneratedDescription(
            text=texto, model="gpt-teste", prompt_version=ai.EXEC_DESC_PROMPT_VERSION
        ),
    )


def test_geracao_grava_texto_modelo_versao_e_limpa_a_revisao_humana(monkeypatch):
    _fake_geracao(monkeypatch)
    cursor = _cursor_com_exercicio()
    resultado = svc.generate_and_store(cursor, 7)
    assert resultado["status"] == svc.STATUS_GENERATED
    update = cursor.sqls("UPDATE exercise_catalog")[0]
    assert "execution_description = %s" in update[0]
    assert "execution_description_reviewed_by = NULL" in update[0]
    assert update[1][0] == "Execução:\n- Empurre."
    assert update[1][1] == "gpt-teste"
    assert update[1][2] == ai.EXEC_DESC_PROMPT_VERSION


def test_exercicio_que_ja_tem_descricao_nao_e_regerado_sem_force(monkeypatch):
    """Protege o trabalho do admin: o texto revisado a mao e a autoridade."""
    _fake_geracao(monkeypatch)
    cursor = _cursor_com_exercicio(execution_description="texto revisado", execution_description_reviewed_by=1)
    resultado = svc.generate_and_store(cursor, 7)
    assert resultado == {"exercise_id": 7, "status": svc.STATUS_SKIPPED, "reason": "already_generated"}
    assert cursor.sqls("UPDATE exercise_catalog") == []


def test_com_force_a_descricao_existente_e_substituida(monkeypatch):
    _fake_geracao(monkeypatch)
    cursor = _cursor_com_exercicio(execution_description="texto antigo")
    assert svc.generate_and_store(cursor, 7, force=True)["status"] == svc.STATUS_GENERATED
    assert cursor.sqls("UPDATE exercise_catalog")


def test_falha_da_openai_nao_derruba_o_lote_nem_escreve_no_catalogo(monkeypatch):
    def _falha(exercise, muscles, model=None):
        raise ai.ExerciseDescriptionError("OpenAI fora do ar")

    monkeypatch.setattr(svc, "generate_execution_description", _falha)
    cursor = _cursor_com_exercicio()
    resultado = svc.generate_and_store(cursor, 7)
    assert resultado["status"] == svc.STATUS_FAILED
    assert "OpenAI fora do ar" in resultado["error"]
    assert cursor.sqls("UPDATE exercise_catalog") == []


def test_exercicio_inexistente_e_skipped_e_nao_explode(monkeypatch):
    _fake_geracao(monkeypatch)
    cursor = FakeCursor({})
    assert svc.generate_and_store(cursor, 999)["reason"] == "not_found"


def test_texto_manual_carimba_revisor_e_apaga_a_etiqueta_do_modelo():
    cursor = FakeCursor({})
    svc.store_manual_text(cursor, 7, "  Texto do admin  ", reviewer_user_id=42)
    sql, params = cursor.sqls("UPDATE exercise_catalog")[0]
    assert "execution_description_model = NULL" in sql
    assert "execution_description_prompt_version = NULL" in sql
    assert params[0] == "Texto do admin"
    assert params[1] == 42


def test_texto_manual_vazio_volta_ao_estado_ainda_nao_gerado():
    cursor = FakeCursor({})
    svc.store_manual_text(cursor, 7, "   ", reviewer_user_id=42)
    sql, params = cursor.sqls("UPDATE exercise_catalog")[0]
    assert "execution_description = NULL" in sql
    assert params == (7,)


def test_lote_respeita_o_teto_para_nao_estourar_o_timeout(monkeypatch):
    _fake_geracao(monkeypatch)
    chamados: list[int] = []
    monkeypatch.setattr(
        svc,
        "generate_and_store",
        lambda cursor, exercise_id, **kwargs: chamados.append(exercise_id)
        or {"exercise_id": exercise_id, "status": svc.STATUS_GENERATED},
    )
    cursor = FakeCursor(
        {
            "SELECT id FROM exercise_catalog": [{"id": i} for i in range(1, 51)],
            "SELECT COUNT(*) AS n": [{"n": 40}],
        }
    )
    resultado = svc.generate_batch(cursor, limit=999)
    assert len(chamados) <= svc.MAX_BATCH
    assert resultado["generated"] == len(chamados)
    assert resultado["remaining"] == 40


class RelogioFalso:
    """Relogio monotonico injetavel: cada item "custa" `custo_por_item`
    segundos, avancados pelo duble de geracao. Nenhum sleep."""

    def __init__(self):
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


def _lote_com_relogio(monkeypatch, relogio: RelogioFalso, custo_por_item: float, *, status=None):
    """`generate_and_store` falso que consome tempo no relogio e registra a ordem."""
    chamados: list[int] = []

    def _gera(cursor, exercise_id, **kwargs):
        chamados.append(exercise_id)
        relogio.avancar(custo_por_item)
        return {"exercise_id": exercise_id, "status": status or svc.STATUS_GENERATED}

    monkeypatch.setattr(svc, "generate_and_store", _gera)
    return chamados


def _cursor_com_pendentes(n_ids: int, remaining: int = 40):
    return FakeCursor(
        {
            "SELECT id FROM exercise_catalog": [{"id": i} for i in range(1, n_ids + 1)],
            "SELECT COUNT(*) AS n": [{"n": remaining}],
        }
    )


def test_lote_para_quando_o_orcamento_de_tempo_acaba_e_devolve_os_adiados(monkeypatch):
    """Producao: itens de ~2,5 s cada, orcamento de 20 s. Antes, os 10 itens
    (25 s+) estouravam o gateway de 29 s. Agora o lote para de puxar item novo
    assim que passa do orcamento e diz quantos ficaram para a proxima chamada."""
    relogio = RelogioFalso()
    chamados = _lote_com_relogio(monkeypatch, relogio, custo_por_item=2.5)
    cursor = _cursor_com_pendentes(10)

    resultado = svc.generate_batch(cursor, limit=10, time_budget_seconds=20.0, clock=relogio)

    # 8 itens = 20 s gastos -> antes do 9o o orcamento ja esta esgotado.
    assert chamados == list(range(1, 9))
    assert resultado["requested"] == 8
    assert resultado["generated"] == 8
    assert resultado["time_budget_exhausted"] is True
    assert resultado["deferred"] == 2
    assert resultado["elapsed_seconds"] == 20.0
    assert resultado["time_budget_seconds"] == 20.0
    assert resultado["remaining"] == 40


def test_lote_que_cabe_no_orcamento_processa_tudo_e_nao_marca_esgotado(monkeypatch):
    relogio = RelogioFalso()
    chamados = _lote_com_relogio(monkeypatch, relogio, custo_por_item=1.0)
    cursor = _cursor_com_pendentes(10)

    resultado = svc.generate_batch(cursor, limit=10, time_budget_seconds=20.0, clock=relogio)

    assert len(chamados) == 10
    assert resultado["requested"] == 10
    assert resultado["time_budget_exhausted"] is False
    assert resultado["deferred"] == 0


def test_primeiro_item_sempre_roda_mesmo_com_orcamento_zero(monkeypatch):
    """Um lote que nao tenta NADA faria o frontend parar como "stalled" e o
    catalogo nunca terminaria. O orcamento limita quando COMECAR o proximo
    item, nunca impede o primeiro."""
    relogio = RelogioFalso()
    chamados = _lote_com_relogio(monkeypatch, relogio, custo_por_item=5.0)
    cursor = _cursor_com_pendentes(3)

    resultado = svc.generate_batch(cursor, limit=3, time_budget_seconds=0.0, clock=relogio)

    assert chamados == [1]
    assert resultado["requested"] == 1
    assert resultado["time_budget_exhausted"] is True
    assert resultado["deferred"] == 2


def test_orcamento_de_tempo_vale_tambem_para_ids_explicitos(monkeypatch):
    relogio = RelogioFalso()
    chamados = _lote_com_relogio(monkeypatch, relogio, custo_por_item=10.0)
    cursor = FakeCursor({"SELECT COUNT(*) AS n": [{"n": 0}]})

    resultado = svc.generate_batch(cursor, exercise_ids=[7, 8, 9], time_budget_seconds=15.0, clock=relogio)

    assert chamados == [7, 8]
    assert resultado["deferred"] == 1


def test_cada_item_gerado_e_commitado_na_hora(monkeypatch):
    """Se a Lambda morrer no item N+1, os N anteriores ja estao no banco: o
    commit acontece logo depois do UPDATE de cada item, nao no fim do lote."""
    relogio = RelogioFalso()
    ordem: list[str] = []

    def _gera(cursor, exercise_id, **kwargs):
        ordem.append(f"gera:{exercise_id}")
        relogio.avancar(1.0)
        return {"exercise_id": exercise_id, "status": svc.STATUS_GENERATED}

    monkeypatch.setattr(svc, "generate_and_store", _gera)
    cursor = _cursor_com_pendentes(3)

    svc.generate_batch(cursor, limit=3, clock=relogio, commit=lambda: ordem.append("commit"))

    assert ordem == ["gera:1", "commit", "gera:2", "commit", "gera:3", "commit"]


def test_item_pulado_ou_falho_nao_gera_commit(monkeypatch):
    """Nao ha o que persistir: `skipped`/`failed` nao escrevem no catalogo."""
    relogio = RelogioFalso()
    commits: list[int] = []
    _lote_com_relogio(monkeypatch, relogio, custo_por_item=1.0, status=svc.STATUS_FAILED)
    cursor = _cursor_com_pendentes(2)

    resultado = svc.generate_batch(cursor, limit=2, clock=relogio, commit=lambda: commits.append(1))

    assert resultado["failed"] == 2
    assert commits == []


def test_lote_sem_commit_injetado_continua_funcionando(monkeypatch):
    """`commit` e opcional: chamadores que gerenciam a transacao por fora
    (ou testes) nao precisam passar nada."""
    relogio = RelogioFalso()
    _lote_com_relogio(monkeypatch, relogio, custo_por_item=1.0)
    resultado = svc.generate_batch(_cursor_com_pendentes(2), limit=2, clock=relogio)
    assert resultado["generated"] == 2


def test_varredura_do_que_falta_ignora_texto_revisado_por_humano():
    cursor = FakeCursor({"SELECT id FROM exercise_catalog": []})
    svc.pending_exercise_ids(cursor, limit=5, prompt_version="exec-desc-v2")
    sql, _ = cursor.sqls("SELECT id FROM exercise_catalog")[0]
    assert "execution_description_reviewed_by IS NULL" in sql
    assert "is_active = 1" in sql
