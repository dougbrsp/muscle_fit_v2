"""As respostas REAIS do Modo B do eval pago de 2026-10-16 (prompt
`periodization-v11`, 4 casos x 5 rodadas), com as leituras do DEV congeladas ao lado, para rodar o
`validate_draft` de ponta a ponta SEM banco e SEM OpenAI.

`data/periodizacao_eval_2026_10_16.json` (gerado no container da API do DEV,
so leitura): pedido + as 5 respostas cruas de cada caso, pares ativos das academias
(`_load_gym_pairs`), id e nome de `muscle_groups`, seguranca de cada par
(`resolve_training_safety_bulk`), catalogo de tecnicas e as leituras da
metodologia (`load_methodology_inputs`) nos dois modos de contagem.

Repositorio publico (M103 L2): o arquivo so tem catalogo/metodologia e as
respostas da IA; a unica nota de saude (dor no ombro, nas instrucoes e nas
respostas) foi trocada por "desconforto no ombro" generico.

`run_case` troca so as costuras de banco (as mesmas que os testes do
complemento ja trocam) e devolve o rascunho, o relatorio e a avaliacao do
MOTOR sobre o rascunho final.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain import training_safety as ts
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn

DATA = Path(__file__).resolve().parents[1] / "unit" / "data" / "periodizacao_eval_2026_10_16.json"

# Rodadas do eval pago (0 = a original; 1-4 = as quatro repeticoes do mesmo dia).
ROUNDS = (0, 1, 2, 3, 4)
CASES_MODO_B = ("modo_b_6_dias_abc", "modo_b_6_dias_abc_ponderado", "modo_b_4_dias_instrucoes", "modo_b_5_dias_sem_perfil")


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _source(value: Any) -> Any:
    return mv.RangeSource(value) if value else None


def _volume_classes(raw: dict[str, Any]) -> dict[int, mv.VolumeClassSpec]:
    out = {}
    for key, spec in raw.items():
        ranges = {
            bucket: mv.MethodologyRange(**{**r, "sets_source": _source(r["sets_source"]), "exercises_source": _source(r["exercises_source"])})
            for bucket, r in spec["ranges"].items()
        }
        out[int(key)] = mv.VolumeClassSpec(**{**spec, "ranges": ranges, "weekly_source": _source(spec["weekly_source"])})
    return out


@lru_cache(maxsize=2)
def methodology_inputs(mode: str) -> svc.MethodologyInputs:
    raw = _raw()["methodology_inputs"][mode]
    contributions: dict[int, Any] = {}
    for key, value in raw["contributions"].items():
        if "set" in value:
            contributions[int(key)] = frozenset(value["set"])
        else:
            contributions[int(key)] = tuple(mv.ContributionRow(**row) for row in value["rows"])
    return svc.MethodologyInputs(
        canonical_ids={int(k): v for k, v in raw["canonical_ids"].items()},
        contributions=contributions,
        groups=tuple(mv.MethodologyGroupInput(**g) for g in raw["groups"]),
        volume_classes=_volume_classes(raw["volume_classes"]),
        counting_mode=mv.VolumeCountingMode(raw["counting_mode"]),
    )


@lru_cache(maxsize=1)
def safeties() -> dict[tuple[int, int], ts.TrainingSafety]:
    out = {}
    for mapping_id, equipment_id, s in _raw()["safeties"]:
        out[(mapping_id, equipment_id)] = ts.TrainingSafety(
            **{
                **s,
                "allowed_techniques": tuple(ts.TechniquePermission(**t) for t in s["allowed_techniques"]),
                "blocked_techniques": tuple(ts.BlockedTechnique(**t) for t in s["blocked_techniques"]),
            }
        )
    return out


def gym_pairs(gym_id: int) -> list[dict[str, Any]]:
    return copy.deepcopy(_raw()["gyms"][str(gym_id)]["pairs"])


def request(case_id: str) -> PeriodizationDraftIn:
    return PeriodizationDraftIn(**_raw()["cases"][case_id]["request"])


def response(case_id: str, round_: int = 0) -> dict[str, Any]:
    return copy.deepcopy(_raw()["cases"][case_id]["responses"][round_])


class _Cursor:
    """So a leitura de `muscle_groups` de `_check_references` passa por aqui
    (os pares da academia vem de `_load_gym_pairs`, trocado em `patch_db`)."""

    def __init__(self, groups: list[dict[str, Any]]) -> None:
        self._groups = groups

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        wanted = set(params or ())
        self._rows = [dict(g) for g in self._groups if g["id"] in wanted]

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Conn:
    def __init__(self) -> None:
        self._groups = _raw()["muscle_groups"]

    def cursor(self) -> _Cursor:
        return _Cursor(self._groups)


def patch_db(monkeypatch: Any) -> None:
    """Troca as leituras de banco do `validate_draft` pelas congeladas."""
    from app import advanced_techniques
    from app.intelligence import technique_volume
    from app.periodization import validation

    catalog = _raw()["technique_catalog"]
    codes = set(_raw()["active_codes"])
    saf = safeties()
    monkeypatch.setattr(validation, "_load_gym_pairs", lambda cur, gym_id: gym_pairs(gym_id))
    monkeypatch.setattr(ts, "resolve_training_safety_bulk", lambda conn, pairs: {tuple(map(int, p)): saf[tuple(map(int, p))] for p in pairs if tuple(map(int, p)) in saf})
    monkeypatch.setattr(svc, "load_methodology_inputs", lambda ids, mode=None: methodology_inputs(mv.counting_mode_or_default(mode).value))
    monkeypatch.setattr(technique_volume, "load_catalog", lambda: catalog)
    monkeypatch.setattr(advanced_techniques, "active_codes", lambda: codes)
    monkeypatch.setattr(advanced_techniques, "list_techniques", lambda include_inactive=False: list(catalog.values()))
    # Projecao de ativacao le metas e preview do banco; nao e o que se mede aqui.
    monkeypatch.setattr(validation, "_check_activation", lambda *a, **k: None)


def evaluate(draft: dict[str, Any], mode: str) -> dict[int, mv.GroupEvaluation]:
    """O MOTOR sobre o rascunho final, no modo do pedido."""
    data = methodology_inputs(mode)
    template = WorkoutPlanTemplateIn(**draft)
    evaluation = mv.evaluate_methodology_volume(
        svc.build_slot_inputs(template.slots, "equipment_series", data.canonical_ids, _raw()["technique_catalog"]),
        data.contributions,
        data.groups,
        "equipment_series",
        None,
        data.volume_classes,
        data.counting_mode,
    )
    return {g.muscle_group_id: g for g in evaluation.groups}


def mode_of(case_id: str) -> str:
    return mv.counting_mode_or_default(request(case_id).volume_counting_mode).value


def run_case(case_id: str, monkeypatch: Any, round_: int = 0) -> tuple[dict[str, Any], dict[str, Any], dict[int, mv.GroupEvaluation]]:
    from app.periodization import validation

    patch_db(monkeypatch)
    draft, report, _ = validation.validate_draft(_Conn(), request(case_id), response(case_id, round_))
    assert draft is not None, report
    return draft, report, evaluate(draft, mode_of(case_id))


def status_counts(groups: dict[int, mv.GroupEvaluation]) -> dict[str, int]:
    """Grupos ABOVE/BELOW por eixo de series (por sessao e semanal)."""
    above, below = mv.MethodologyVolumeStatus.ABOVE_RANGE, mv.MethodologyVolumeStatus.BELOW_RANGE
    return {
        "session_above": sum(1 for g in groups.values() if g.direct_sets_axis.status is above),
        "session_below": sum(1 for g in groups.values() if g.direct_sets_axis.status is below),
        "weekly_above": sum(1 for g in groups.values() if g.weekly_sets_axis.status is above),
        "weekly_below": sum(1 for g in groups.values() if g.weekly_sets_axis.status is below),
    }
