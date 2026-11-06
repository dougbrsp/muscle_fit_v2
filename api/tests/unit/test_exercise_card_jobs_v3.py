"""Worker V3 dos cards (`app/exercise_card_jobs.py`): orcamento de tempo das
duas chamadas, soma de custo/tokens e paridade com o timeout da Lambda.

O fluxo completo (duas cenas, referencias, falhas, redelivery) esta em
`test_exercise_card_service.py`; aqui ficam as regras do orcamento, com o
relogio do worker (`jobs._monotonic`) injetado. Sem rede e sem MySQL.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_jobs_v3.py
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app import async_jobs, openai_client
from app import exercise_card_jobs as jobs
from app import exercise_card_prompt as card_prompt
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from tests.support import card_illustrations as ill
from tests.support.cards_sqlite import CardsSqlite
from tests.support.fake_s3 import FakeS3

BUCKET = "musclefit-uploads"
T0 = datetime(2026, 11, 6, 12, 0, 0)
SCENE = ill.scene_photo_png()


def _result(cost, tokens_in=10, tokens_out=20) -> openai_client.ImageResult:
    return openai_client.ImageResult(SCENE, "gpt-image-2.5-sunburst", "high", "1024x1536", tokens_in, tokens_out,
                                     cost, 1, 1.0)


# ---------------------------------------------------------------------------
# Somas
# ---------------------------------------------------------------------------


def test_soma_de_custo_e_none_se_qualquer_chamada_for_desconhecida():
    assert jobs.sum_costs([_result(Decimal("0.18")), _result(Decimal("0.2"))]) == Decimal("0.38")
    assert jobs.sum_costs([_result(Decimal("0.18")), _result(None)]) is None
    assert jobs.sum_costs([_result(None)]) is None
    assert jobs.sum_costs([]) is None


def test_soma_de_tokens_ignora_desconhecidos_e_so_e_none_sem_nenhum():
    assert jobs.sum_tokens([10, 20]) == 30
    assert jobs.sum_tokens([10, None]) == 10
    assert jobs.sum_tokens([None, None]) is None
    assert jobs.sum_tokens([]) is None


# ---------------------------------------------------------------------------
# Paridade com a Lambda e com o sweep
# ---------------------------------------------------------------------------


def test_orcamento_do_job_cabe_no_timeout_da_lambda_com_margem():
    lambda_timeout = async_jobs.LAMBDA_TIMEOUT_SECONDS[jobs.WORKER_FUNCTION]
    assert async_jobs.JOB_TYPE_LAMBDA[jobs.JOB_TYPE] == jobs.WORKER_FUNCTION
    assert jobs.JOB_TIME_BUDGET_SECONDS <= lambda_timeout - jobs.JOB_TIME_BUDGET_MARGIN_SECONDS
    # O sweep (WORKER_LOST) so fecha uma linha depois que a Lambda ja morreu.
    assert svc.DEFAULT_STALE_MINUTES * 60 >= lambda_timeout


def test_duas_chamadas_no_pior_caso_cabem_no_orcamento():
    pior_caso = openai_client.image_call_worst_case_seconds(openai_client.DEFAULT_IMAGE_TIMEOUT_SECONDS)
    assert 2 * pior_caso + jobs.POST_PROCESS_RESERVE_SECONDS <= jobs.JOB_TIME_BUDGET_SECONDS


def test_fundo_opaco_e_jpeg_da_cena_1():
    assert jobs.SCENE_BACKGROUND == "opaque" and jobs.SCENE_BACKGROUND in openai_client.IMAGE_BACKGROUNDS
    assert jobs.SCENE_REFERENCE_JPEG_QUALITY == 92
    assert not hasattr(jobs, "ILLUSTRATION_BACKGROUND")


# ---------------------------------------------------------------------------
# Orcamento estourado depois da cena 1 (relogio injetado)
# ---------------------------------------------------------------------------


class _Relogio:
    def __init__(self) -> None:
        self.agora = 0.0

    def __call__(self) -> float:
        return self.agora


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv(svc.ENV_ENABLED, "true")
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", BUCKET)
    monkeypatch.delenv("OPENAI_IMAGE_TIMEOUT_SECONDS", raising=False)
    banco = CardsSqlite()
    monkeypatch.setattr(svc, "transaction", banco.transaction)
    monkeypatch.setattr(jobs, "transaction", banco.transaction)
    banco.add_exercise(42, "Supino reto")
    s3 = FakeS3()
    monkeypatch.setattr(storage, "s3_client", lambda: s3)
    monkeypatch.setattr(svc, "utcnow", lambda: T0)
    monkeypatch.setattr(
        card_prompt, "load_context",
        lambda cursor, exercise_id: card_prompt.ExerciseCardContext(exercise_id=int(exercise_id), name="Supino reto"),
    )
    relogio = _Relogio()
    monkeypatch.setattr(jobs, "_monotonic", relogio)
    yield banco, s3, relogio
    banco.close()


def _openai_que_demora(monkeypatch, relogio, segundos: float, chamadas: list):
    def gera(prompt, **kwargs):
        chamadas.append(kwargs)
        relogio.agora += segundos
        return _result(Decimal("0.18"), 7, 6000)

    monkeypatch.setattr(openai_client, "generate_image", gera)


def test_sem_orcamento_para_a_cena_2_falha_com_o_custo_da_cena_1(ambiente, monkeypatch):
    banco, s3, relogio = ambiente
    chamadas: list = []
    # Cena 1 lenta: sobra menos que o pior caso da cena 2 + a reserva.
    limite = jobs.JOB_TIME_BUDGET_SECONDS - jobs.POST_PROCESS_RESERVE_SECONDS - (
        openai_client.image_call_worst_case_seconds(openai_client.DEFAULT_IMAGE_TIMEOUT_SECONDS))
    _openai_que_demora(monkeypatch, relogio, limite + 1, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with pytest.raises(jobs.ExerciseCardJobError) as exc:
        jobs.run_generation_job({"generation_id": gid})
    assert str(exc.value).endswith(svc.ERROR_TIME_BUDGET_EXCEEDED)
    assert len(chamadas) == 1  # a cena 2 nem comecou
    row = banco.generation(gid)
    assert (row["status"], row["error_code"]) == ("FAILED", "TIME_BUDGET_EXCEEDED")
    assert (row["cost_usd"], row["tokens_in"], row["tokens_out"]) == (Decimal("0.18"), 7, 6000)
    assert s3.keys(BUCKET) == [storage.scene_key(gid, 1)]  # a cena 1 paga ficou
    assert len(svc.ERROR_TIME_BUDGET_EXCEEDED) <= 40


def test_no_limite_exato_do_orcamento_a_cena_2_ainda_roda(ambiente, monkeypatch):
    banco, _s3, relogio = ambiente
    chamadas: list = []
    limite = jobs.JOB_TIME_BUDGET_SECONDS - jobs.POST_PROCESS_RESERVE_SECONDS - (
        openai_client.image_call_worst_case_seconds(openai_client.DEFAULT_IMAGE_TIMEOUT_SECONDS))
    _openai_que_demora(monkeypatch, relogio, limite, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    jobs.run_generation_job({"generation_id": gid})
    assert len(chamadas) == 2
    row = banco.generation(gid)
    assert row["status"] == "GENERATED"
    assert (row["cost_usd"], row["tokens_in"], row["tokens_out"]) == (Decimal("0.36"), 14, 12000)


def test_timeout_por_chamada_vem_do_ambiente_e_entra_no_orcamento(ambiente, monkeypatch):
    """Com OPENAI_IMAGE_TIMEOUT_SECONDS maior, o pior caso da cena 2 cresce e
    o worker desiste antes de arriscar o timeout da Lambda."""
    banco, _s3, relogio = ambiente
    monkeypatch.setenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "300")
    chamadas: list = []
    _openai_que_demora(monkeypatch, relogio, 60, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    assert [c["timeout"] for c in chamadas] == [300.0]
    assert banco.generation(gid)["error_code"] == svc.ERROR_TIME_BUDGET_EXCEEDED
