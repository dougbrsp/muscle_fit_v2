"""M81: os handlers das Lambdas de IA reivindicam o job (`async_jobs.claim_job`)
ANTES de chamar qualquer servico de IA.

Claim negado (job ja fechado como `worker_lost` pelo poll - entrega atrasada
pela fila de throttling - ou entrega duplicada do invoke assincrono) -> nada
roda, nada e cobrado, nada e gravado (`mark_done`/`mark_failed` nao sao
chamados). Claim aceito -> fluxo de sempre.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pytest

from app import async_jobs, lambda_cardio_agent, lambda_plan_generator, lambda_workout_ai

JOB_ID = "job-m81"


@dataclass(frozen=True)
class Case:
    module: Any
    dispatch_attr: str  # funcao de IA que o handler chama, patchada no modulo do handler
    event: dict[str, Any]

    @property
    def id(self) -> str:
        return self.module.__name__.rsplit(".", 1)[-1]


CASES = [
    Case(
        lambda_plan_generator,
        "dispatch_plan_generation",
        {"job_id": JOB_ID, "payload": {"template_id": 7}, "user_id": 3},
    ),
    Case(
        lambda_cardio_agent,
        "dispatch_cardio_generation",
        {"job_id": JOB_ID, "payload": {"days": 14}, "user_id": 3},
    ),
    Case(
        lambda_workout_ai,
        "dispatch_workout_ai_generation",
        {"job_id": JOB_ID, "job_type": "insights", "payload": {}, "user_id": 3},
    ),
]


@dataclass
class Calls:
    claim: list[str] = field(default_factory=list)
    dispatch: int = 0
    done: list[tuple[str, Any]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _spy(monkeypatch, case: Case, *, claim: bool, ia: Optional[Callable[[], Any]] = None) -> Calls:
    calls = Calls()

    def _claim(job_id: str) -> bool:
        calls.claim.append(job_id)
        return claim

    def _dispatch(*_args, **_kwargs):
        calls.dispatch += 1
        return ia() if ia is not None else {"ok": True}

    monkeypatch.setattr(async_jobs, "claim_job", _claim)
    monkeypatch.setattr(async_jobs, "mark_done", lambda job_id, result: calls.done.append((job_id, result)) or True)
    monkeypatch.setattr(async_jobs, "mark_failed", lambda job_id, error: calls.failed.append((job_id, error)) or True)
    monkeypatch.setattr(case.module, case.dispatch_attr, _dispatch)
    return calls


@pytest.fixture(params=CASES, ids=lambda case: case.id)
def case(request) -> Case:
    return request.param


def test_claim_negado_nao_chama_ia_nem_grava(case: Case, monkeypatch, caplog) -> None:
    calls = _spy(monkeypatch, case, claim=False)

    with caplog.at_level(logging.WARNING, logger=case.module.logger.name):
        case.module.handler(dict(case.event), None)

    assert calls.claim == [JOB_ID]
    assert calls.dispatch == 0
    assert calls.done == [] and calls.failed == []
    assert f"Job {JOB_ID} não está mais pendente" in caplog.text


def test_claim_aceito_roda_a_ia_e_marca_done(case: Case, monkeypatch) -> None:
    calls = _spy(monkeypatch, case, claim=True)

    case.module.handler(dict(case.event), None)

    assert calls.claim == [JOB_ID]
    assert calls.dispatch == 1
    assert calls.done == [(JOB_ID, {"ok": True})]
    assert calls.failed == []


def test_claim_aceito_e_falha_da_ia_marca_failed(case: Case, monkeypatch) -> None:
    def _boom():
        raise RuntimeError("ia caiu")

    calls = _spy(monkeypatch, case, claim=True, ia=_boom)

    case.module.handler(dict(case.event), None)

    assert calls.dispatch == 1
    assert calls.done == []
    assert calls.failed == [(JOB_ID, "ia caiu")]


def test_evento_sem_job_id_nao_tenta_claim(case: Case, monkeypatch) -> None:
    calls = _spy(monkeypatch, case, claim=True)

    case.module.handler({k: v for k, v in case.event.items() if k != "job_id"}, None)

    assert calls == Calls()
