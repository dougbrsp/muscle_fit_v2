"""IA-14: o resumo de fim de treino nao e regenerado (nem pago) quando a
entrada e a mesma do resumo salvo; IA-17: o prompt nao leva o nome da
academia e toda chamada de texto vai com `store=false`.

Sem rede e sem banco: `activity_detail`, as decisoes do motor, a gravacao e
o `urlopen` da OpenAI sao trocados por dublês.

Rodar: cd api && python -m pytest tests/unit/test_ia14_dedupe_resumo_treino.py
"""

from __future__ import annotations

import copy
import json
from datetime import datetime

import pytest

from app import migrate as m
from app import openai_client
from app import workout_ai_generation_core as core
from app import workout_summary_ai as workout_ai

GERADO_EM = datetime(2026, 10, 10, 9, 30, 0)

ATIVIDADE = {
    "id": 10,
    "name": "Treino A",
    "local_date": "2026-10-01",
    "gym_name": "Academia Secreta do Bairro",
    "duration_seconds": 3600,
    "total_volume_kg": 5000,
    "avg_rpe": 8,
    "notes": "ok",
    "exercises": [
        {
            "id": 1,
            "original_name": "Supino reto",
            "muscle_primary": "Peitoral",
            "sets": [{"reps": 10, "weight_kg": 60, "rpe": 8, "set_type": "normal"}],
        }
    ],
}

SEM_DECISOES = {"exercises": {}, "groups": []}


class _Banco:
    """Estado de `activities` para o dispatch: o que `activity_detail` le e o
    que `save_activity_ai_summary` grava."""

    def __init__(self, atividade: dict):
        self.atividade = copy.deepcopy(atividade)
        self.gravacoes: list[dict] = []

    def detail(self, activity_id, user_id=None):
        return copy.deepcopy(self.atividade) if activity_id == self.atividade["id"] else None

    def save(self, activity_id, summary, user_id=None, input_sha256=None):
        self.gravacoes.append({"summary": summary, "input_sha256": input_sha256})
        self.atividade.update(
            ai_summary=summary, ai_summary_generated_at=GERADO_EM, ai_summary_input_sha256=input_sha256
        )
        return {"ai_summary": summary, "ai_summary_generated_at": GERADO_EM}


@pytest.fixture
def ambiente(monkeypatch):
    banco = _Banco(ATIVIDADE)
    chamadas: list[str] = []

    def fake_call_openai(prompt, user_id=None, prompt_version=None):
        chamadas.append(prompt)
        return openai_client.TextResult(
            text=f"Resumo numero {len(chamadas)}.", model=workout_ai.OPENAI_MODEL + "-2026-03-17",
            requested_model=workout_ai.OPENAI_MODEL,
        )

    monkeypatch.setattr(core, "activity_detail", banco.detail)
    monkeypatch.setattr(core, "save_activity_ai_summary", banco.save)
    monkeypatch.setattr(core, "safe_read", lambda fn, default=None: SEM_DECISOES)
    monkeypatch.setattr(workout_ai, "call_openai", fake_call_openai)
    return banco, chamadas


def test_segunda_chamada_com_a_mesma_entrada_nao_chama_a_openai(ambiente):
    banco, chamadas = ambiente
    primeira = core.dispatch_activity_summary({"activity_id": 10}, user_id=1)
    segunda = core.dispatch_activity_summary({"activity_id": 10}, user_id=1)

    assert len(chamadas) == 1
    assert len(banco.gravacoes) == 1
    assert segunda == primeira == {"summary": "Resumo numero 1.", "generated_at": GERADO_EM.isoformat()}
    assert banco.gravacoes[0]["input_sha256"] == workout_ai.input_fingerprint(chamadas[0])


def test_force_regera_mesmo_com_a_mesma_entrada(ambiente):
    banco, chamadas = ambiente
    core.dispatch_activity_summary({"activity_id": 10}, user_id=1)
    resultado = core.dispatch_activity_summary({"activity_id": 10, "force": True}, user_id=1)

    assert len(chamadas) == 2
    assert resultado["summary"] == "Resumo numero 2."
    assert banco.atividade["ai_summary"] == "Resumo numero 2."


def test_treino_editado_depois_do_resumo_gera_de_novo(ambiente):
    banco, chamadas = ambiente
    core.dispatch_activity_summary({"activity_id": 10}, user_id=1)
    banco.atividade["exercises"][0]["sets"][0]["reps"] = 12
    core.dispatch_activity_summary({"activity_id": 10}, user_id=1)

    assert len(chamadas) == 2
    assert banco.gravacoes[0]["input_sha256"] != banco.gravacoes[1]["input_sha256"]


def test_decisao_do_motor_diferente_gera_de_novo(ambiente, monkeypatch):
    _banco, chamadas = ambiente
    core.dispatch_activity_summary({"activity_id": 10}, user_id=1)
    monkeypatch.setattr(core, "build_prompt", lambda detail, decisions, technique_names=None: "prompt com outra decisao")
    core.dispatch_activity_summary({"activity_id": 10}, user_id=1)

    assert len(chamadas) == 2


def test_resumo_antigo_sem_impressao_digital_gera_como_antes(ambiente):
    """Linhas anteriores a migracao 070 (coluna NULL): mesmo comportamento de
    antes do IA-14 - gera, grava e passa a ter a impressao."""
    banco, chamadas = ambiente
    banco.atividade.update(ai_summary="Resumo velho.", ai_summary_input_sha256=None)
    core.dispatch_activity_summary({"activity_id": 10}, user_id=1)

    assert len(chamadas) == 1
    assert banco.gravacoes[0]["input_sha256"]


def test_modelo_ou_versao_do_prompt_mudam_a_impressao(monkeypatch):
    base = workout_ai.input_fingerprint("p")
    monkeypatch.setattr(workout_ai, "PROMPT_VERSION", "outra-versao")
    assert workout_ai.input_fingerprint("p") != base
    monkeypatch.setattr(workout_ai, "OPENAI_MODEL", "outro-modelo")
    assert workout_ai.input_fingerprint("p") != base
    assert len(base) == 64


# ---------------------------------------------------------------------------
# IA-17
# ---------------------------------------------------------------------------


def test_prompt_do_resumo_nao_leva_o_nome_da_academia():
    prompt = workout_ai.build_prompt(ATIVIDADE, SEM_DECISOES)
    assert "Academia Secreta do Bairro" not in prompt
    assert "Academia:" not in prompt


class _Resposta:
    def read(self):
        return json.dumps({"output_text": "ok", "model": "modelo-falso"}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("feature", [None, "workout_summary", "insights"])
def test_toda_chamada_de_texto_vai_com_store_false(monkeypatch, feature):
    enviados = []
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        openai_client.urllib.request,
        "urlopen",
        lambda request, timeout=None: enviados.append(json.loads(request.data)) or _Resposta(),
    )
    openai_client.call_openai("oi", model="m", feature=feature, prompt_version="v")
    assert enviados[0]["store"] is False


def test_analise_de_treinos_nao_leva_o_id_externo_do_intervals():
    from app import report_ai

    training = {
        "intervals_correlation": {
            "matched_workouts": [{"activity_id": 1, "intervals_external_id": "i123", "hrv_ms": 50}],
            "unmatched_workouts_count": 0,
        }
    }
    payload = report_ai._ai_training_payload(training)
    assert payload["intervals_correlation"]["matched_workouts"] == [{"activity_id": 1, "hrv_ms": 50}]
    # o payload devolvido a tela nao muda
    assert training["intervals_correlation"]["matched_workouts"][0]["intervals_external_id"] == "i123"


def test_ficha_por_modelo_v1_nao_manda_a_lista_de_academias():
    from app import workout_plan_templates_engine as engine

    prompt = engine.build_template_prompt(
        context={"gyms": [{"id": 1, "name": "Outra academia XYZ", "notes": "nota privada"}]},
        rag_text="",
        plan_type="full_body",
        goal=None,
        gym_name="Minha",
        target_groups=[],
    )
    assert "Outra academia XYZ" not in prompt
    assert "nota privada" not in prompt
    assert "academia = Minha" in prompt


# ---------------------------------------------------------------------------
# Migracao 070
# ---------------------------------------------------------------------------

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "070_activities_ai_summary_input_hash.py"


def _mig() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class SchemaDB:
    def __init__(self, column: tuple | None, tabela: bool = True, dry_run: bool = False):
        self.column = column
        self.tabela = tabela
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "activities"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            if self.column is None:
                return []
            tipo, nulo = self.column
            return [{"COLUMN_NAME": "ai_summary_input_sha256", "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo}]
        raise AssertionError(sql)

    def execute(self, sql, params=None):
        raise AssertionError("o plano nao escreve")


def test_070_adiciona_a_coluna_nula():
    assert [sql for sql, _ in _mig()["plan"](SchemaDB(None))] == [
        "ALTER TABLE activities ADD COLUMN ai_summary_input_sha256 CHAR(64) NULL DEFAULT NULL"
    ]


def test_070_convergido_e_zero_statements():
    assert _mig()["plan"](SchemaDB(("char(64)", "YES"))) == []


def test_070_tipo_incompativel_falha_antes_de_executar():
    with pytest.raises(Exception, match="070"):
        _mig()["plan"](SchemaDB(("varchar(64)", "YES")))
    with pytest.raises(Exception, match="070"):
        _mig()["plan"](SchemaDB(("char(64)", "NO")))


def test_070_sem_tabela_falha_na_execucao_real_e_planeja_no_dry_run():
    with pytest.raises(Exception, match="070"):
        _mig()["plan"](SchemaDB(None, tabela=False))
    assert len(_mig()["plan"](SchemaDB(None, tabela=False, dry_run=True))) == 1


def test_070_down_solta_a_coluna():
    assert [sql for sql, _ in _mig()["plan_down"](SchemaDB(("char(64)", "YES")))] == [
        "ALTER TABLE activities DROP COLUMN ai_summary_input_sha256"
    ]
    assert _mig()["plan_down"](SchemaDB(None)) == []
