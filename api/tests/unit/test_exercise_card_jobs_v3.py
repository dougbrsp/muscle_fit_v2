"""Worker V3 dos cards (`app/exercise_card_jobs.py`): orcamento de tempo das
duas chamadas, soma de custo/tokens e paridade com o timeout da Lambda.

O fluxo completo (duas cenas, referencias, falhas, redelivery) esta em
`test_exercise_card_service.py`; aqui ficam as regras do orcamento, com o
relogio do worker (`jobs._monotonic`) injetado. Sem rede e sem MySQL.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_jobs_v3.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal

import pytest

from app import async_jobs, openai_client
from app import exercise_card_jobs as jobs
from app import exercise_card_processor as processor
from app import exercise_card_prompt as card_prompt
from app import exercise_card_references as refs
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from tests.support import card_illustrations as ill
from tests.support.cards_sqlite import CardsSqlite
from tests.support.fake_s3 import FakeS3
from tests.support.privado import exigir

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


# ---------------------------------------------------------------------------
# Foto de referencia excluida durante a geracao (sumiu do S3)
# ---------------------------------------------------------------------------


def _foto(banco, s3, nota=None, *, no_s3: bool = True) -> tuple[int, bytes]:
    ref_id = banco.add_reference(42, note=nota)
    foto = ill.jpeg_photo(color=(20 + ref_id, 40, 60))
    if no_s3:
        s3.put_object(Bucket=BUCKET, Key=banco.reference(ref_id)["s3_key"], Body=foto, ContentType="image/jpeg")
    return ref_id, foto


def _openai_rapido(monkeypatch, chamadas: list):
    def gera(prompt, **kwargs):
        chamadas.append({"prompt": prompt, **kwargs})
        return _result(Decimal("0.18"))

    monkeypatch.setattr(openai_client, "generate_image", gera)


def test_foto_ausente_no_s3_e_pulada_e_o_card_sai_com_as_presentes(ambiente, monkeypatch, caplog):
    banco, s3, _relogio = ambiente
    ref_a, foto_a = _foto(banco, s3, "NOTA-A")
    ref_b, _ = _foto(banco, s3, "NOTA-B", no_s3=False)  # excluida no meio: linha lida, objeto ja removido
    ref_c, foto_c = _foto(banco, s3, None)
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with caplog.at_level(logging.INFO):
        jobs.run_generation_job({"generation_id": gid})
    assert banco.generation(gid)["status"] == "GENERATED"
    logo = processor.load_stamp_logo_png()
    assert chamadas[0]["reference_images"] == [foto_a, foto_c, logo]
    assert chamadas[1]["reference_images"][1:] == [foto_a, foto_c, logo]
    # Notas alinhadas as fotos LIDAS: a da foto ausente nao entra.
    exigir("NOTA-A" in chamadas[0]["prompt"] and "NOTA-B" not in chamadas[0]["prompt"], "notas das fotos lidas")
    gerado = next(
        json.loads(r.getMessage()) for r in caplog.records if '"exercise_card.generated"' in r.getMessage()
    )
    assert gerado["reference_ids"] == [ref_a, ref_c] and gerado["reference_count"] == 2
    aviso = next(r for r in caplog.records if r.levelno == logging.WARNING and "ausentes" in r.getMessage())
    assert f"ids_ausentes=[{ref_b}]" in aviso.getMessage()
    assert storage.REFERENCE_PREFIX not in caplog.text  # nunca a key S3


def test_nenhuma_foto_lida_falha_sem_chamar_a_openai(ambiente, monkeypatch):
    banco, s3, _relogio = ambiente
    _foto(banco, s3, no_s3=False)
    _foto(banco, s3, no_s3=False)
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    row = banco.generation(gid)
    assert (row["status"], row["error_code"]) == ("FAILED", storage.S3_READ_FAILED)
    assert row["cost_usd"] is None and chamadas == []


def test_erro_de_leitura_que_nao_e_ausencia_falha_a_geracao(ambiente, monkeypatch):
    banco, s3, _relogio = ambiente
    _foto(banco, s3)
    ref_b, _ = _foto(banco, s3)
    s3.fail_get_keys.add(banco.reference(ref_b)["s3_key"])
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    assert banco.generation(gid)["error_code"] == storage.S3_READ_FAILED
    assert chamadas == []


def test_sem_fotos_cadastradas_nem_le_o_s3(ambiente, monkeypatch):
    banco, s3, _relogio = ambiente
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    jobs.run_generation_job({"generation_id": gid})
    logo = processor.load_stamp_logo_png()
    assert chamadas[0]["reference_images"] == [logo]  # cena 1 sempre por edicao (logo)
    assert len(chamadas[1]["reference_images"]) == 2
    assert chamadas[1]["reference_images"][0].startswith(b"\xff\xd8\xff")
    assert chamadas[1]["reference_images"][1] == logo
    assert s3.calls_of("get_object") == []


def test_pior_caso_de_imagens_de_entrada_cabe_no_limite_da_openai(ambiente, monkeypatch):
    """Cena 2 com o maximo de fotos do admin: cena 1 + fotos + logo."""
    banco, s3, _relogio = ambiente
    fotos = [_foto(banco, s3)[1] for _ in range(refs.MAX_ACTIVE_REFERENCES)]
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    jobs.run_generation_job({"generation_id": gid})
    logo = processor.load_stamp_logo_png()
    assert chamadas[0]["reference_images"] == [*fotos, logo]
    assert chamadas[1]["reference_images"][1:] == [*fotos, logo]
    assert len(chamadas[1]["reference_images"]) == 1 + refs.MAX_ACTIVE_REFERENCES + 1
    assert len(chamadas[1]["reference_images"]) <= openai_client.IMAGE_MAX_REFERENCE_IMAGES


def test_reference_count_e_reference_ids_nao_contam_o_logo(ambiente, monkeypatch, caplog):
    banco, s3, _relogio = ambiente
    ref_a, _ = _foto(banco, s3)
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    contagens: list = []
    original = card_prompt.build_scene_prompts

    def espia(*args, **kwargs):
        contagens.append(kwargs["reference_count"])
        return original(*args, **kwargs)

    monkeypatch.setattr(card_prompt, "build_scene_prompts", espia)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with caplog.at_level(logging.INFO):
        jobs.run_generation_job({"generation_id": gid})
    assert contagens == [1]
    gerado = next(json.loads(r.getMessage()) for r in caplog.records if '"exercise_card.generated"' in r.getMessage())
    assert gerado["reference_ids"] == [ref_a] and gerado["reference_count"] == 1


def test_sem_asset_do_logo_falha_cedo_sem_chamar_a_openai(ambiente, monkeypatch, tmp_path):
    banco, _s3, _relogio = ambiente
    monkeypatch.setattr(processor, "STAMP_LOGO_PATH", tmp_path / "nao-existe.png")
    processor._load_stamp_logo_png.cache_clear()
    chamadas: list = []
    _openai_rapido(monkeypatch, chamadas)
    gid = banco.insert_generation(exercise_id=42, status="PENDING")
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    row = banco.generation(gid)
    assert (row["status"], row["error_code"]) == ("FAILED", svc.ERROR_CARD_COMPOSITION_FAILED)
    assert row["cost_usd"] is None and chamadas == []

