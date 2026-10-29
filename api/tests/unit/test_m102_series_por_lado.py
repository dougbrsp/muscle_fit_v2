"""M102 - reps e RPE por lado em exercício unilateral.

- dono `domain/set_sides`: reps oficiais = menor lado, RPE = maior lado, lado
  vazio ignorado, sem lado vale o enviado; texto "E 10 · D 8";
- schemas: `ExerciseSetIn`/`SessionSetIn` recalculam; sem lados (relógio,
  fila offline) nada muda; limites dos lados;
- POST /sets grava os lados e o canônico; PATCH /sets/{n} recalcula;
- mescla da edição (`PUT /exercises/logs/{id}`) preserva os lados omitidos e
  recalcula o canônico.

Rodar: cd api && python -m pytest tests/unit/test_m102_series_por_lado.py
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import workout_sessions as ws
from app.domain import effective_intensity as ei
from app.domain import set_sides as ss
from app.exercise_set_update_merge import PRESERVED_SET_FIELDS, merge_set_patch, merge_sets_for_update
from app.models import ExerciseSetIn, ExerciseSetUpdateIn
from tests.unit.test_client_key_sessao_idempotente import SESSION, _identity, _install
from tests.unit.test_modo_intensivo import FakeIntensiveDB

# ---------------------------------------------------------------------------
# Dono
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entrada, saida",
    [
        ({"reps_left": 10, "reps_right": 8}, (8, None)),
        ({"rpe_left": Decimal(8), "rpe_right": Decimal("9.5")}, (None, Decimal("9.5"))),
        ({"reps_left": 10, "reps_right": None, "rpe_right": Decimal(9)}, (10, Decimal(9))),
        ({"reps_left": 0, "reps_right": 6}, (0, None)),
        ({}, (None, None)),
    ],
)
def test_canonical_from_sides_pior_lado_e_lado_vazio_ignorado(entrada, saida):
    assert ss.canonical_from_sides(None, None, **entrada) == saida


def test_sem_lado_vale_o_valor_enviado_e_cada_medida_e_independente():
    assert ss.canonical_from_sides(12, Decimal(7)) == (12, Decimal(7))
    # Só reps por lado: o RPE enviado continua valendo.
    assert ss.canonical_from_sides(99, Decimal(7), reps_left=10, reps_right=11) == (10, Decimal(7))


def test_apply_canonical_muta_so_com_lados():
    row = {"reps": 12, "rpe": Decimal(8)}
    assert ss.apply_canonical(row) == {"reps": 12, "rpe": Decimal(8)}
    row = {"reps": 12, "rpe": Decimal(8), "reps_left": 10, "reps_right": 9, "rpe_left": Decimal(8), "rpe_right": Decimal(9)}
    ss.apply_canonical(row)
    assert (row["reps"], row["rpe"]) == (9, Decimal(9))


def test_texto_dos_lados():
    assert ss.format_sides(10, 8) == "E 10 · D 8"
    assert ss.format_sides(None, 8) == "D 8"
    assert ss.format_sides(None, None) is None
    assert ss.format_sides(Decimal("8.50"), Decimal("9.0")) == "E 8,5 · D 9"
    row = {"reps_left": 10, "reps_right": 8, "rpe_left": Decimal("8.00"), "rpe_right": Decimal("9.00")}
    assert ss.sides_text(row) == "reps E 10 · D 8; RPE E 8 · D 9"
    assert ss.sides_text({"reps": 10}) is None
    assert ss.attach_sides_text({"reps": 10})["sides_text"] is None


def test_regra_na_frase_da_metodologia():
    assert "pior lado" in ss.WORST_SIDE_RULE_TEXT


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_exercise_set_in_recalcula_e_relogio_sem_lados_nao_muda():
    s = ExerciseSetIn(set_number=1, reps=99, rpe=Decimal(5), reps_left=10, reps_right=8, rpe_left=8, rpe_right=9)
    assert (s.reps, s.rpe) == (8, Decimal(9))
    antigo = ExerciseSetIn(set_number=1, reps=10, rpe=Decimal(8))
    assert (antigo.reps, antigo.rpe) == (10, Decimal(8))
    assert ws.SessionSetIn(reps=10, weight_kg=Decimal(20)).reps == 10


def test_session_set_in_recalcula_antes_da_validacao_da_medida():
    s = ws.SessionSetIn(reps_left=12, reps_right=11, rpe_left=Decimal(8), rpe_right=Decimal("8.5"), weight_kg=Decimal(20))
    assert (s.reps, s.rpe) == (11, Decimal("8.5"))


def test_edicao_parcial_so_marca_a_medida_com_lado():
    sent = ExerciseSetUpdateIn(set_number=1, reps_left=10).model_dump(exclude_unset=True)
    assert sent == {"set_number": 1, "reps": 10, "reps_left": 10}


@pytest.mark.parametrize("campo, valor", [("reps_left", -1), ("rpe_right", Decimal(11)), ("rpe_left", Decimal(-1))])
def test_lados_fora_do_limite_sao_422(campo, valor):
    with pytest.raises(ValidationError):
        ws.SessionSetIn(reps=10, **{campo: valor})
    with pytest.raises(ValidationError):
        ExerciseSetIn(set_number=1, reps=10, **{campo: valor})


# ---------------------------------------------------------------------------
# Rotas: POST e PATCH
# ---------------------------------------------------------------------------


@pytest.fixture
def db(monkeypatch):
    fake = _install(monkeypatch, FakeIntensiveDB())
    monkeypatch.setattr(ws, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    monkeypatch.setattr(ei, "resolve_pair_safety", lambda conn, m, g: SimpleNamespace(max_safe_rpe=10))
    return fake


def test_post_grava_lados_e_canonico_e_devolve_o_gravado(db):
    ex = db.add_exercise()
    out = ws.add_session_set(
        SESSION,
        ex,
        ws.SessionSetIn(reps_left=10, reps_right=8, rpe_left=Decimal(8), rpe_right=Decimal(9), weight_kg=Decimal(20)),
        identity=_identity(),
    )
    row = db.sets[0]
    assert (row["reps"], row["rpe"]) == (8, Decimal(9))
    assert (row["reps_left"], row["reps_right"], row["rpe_left"], row["rpe_right"]) == (10, 8, Decimal(8), Decimal(9))
    assert (out["reps"], out["rpe"], out["reps_left"], out["rpe_right"]) == (8, 9.0, 10, 9.0)
    assert out["sides_text"] == "reps E 10 · D 8; RPE E 8 · D 9"


def test_post_sem_lados_grava_nulos_como_antes(db):
    ex = db.add_exercise()
    out = ws.add_session_set(SESSION, ex, ws.SessionSetIn(reps=10, weight_kg=Decimal(20)), identity=_identity())
    row = db.sets[0]
    assert row["reps"] == 10 and all(row.get(field) is None for field in ss.SIDE_FIELDS)
    assert out["sides_text"] is None


def test_patch_de_um_lado_recalcula_o_canonico(db):
    ex = db.add_exercise()
    ws.add_session_set(
        SESSION, ex, ws.SessionSetIn(reps_left=10, reps_right=8, weight_kg=Decimal(20)), identity=_identity()
    )
    ws.update_session_set(SESSION, ex, 1, ws.SessionSetPatchIn(reps_right=11), identity=_identity())
    row = db.sets[0]
    assert (row["reps_left"], row["reps_right"], row["reps"]) == (10, 11, 10)


# ---------------------------------------------------------------------------
# Mescla da edição
# ---------------------------------------------------------------------------


def test_lados_sao_preservados_na_edicao():
    assert set(ss.SIDE_FIELDS) <= set(PRESERVED_SET_FIELDS)


def test_mescla_preserva_lados_omitidos_e_recalcula():
    stored = [{"set_number": 1, "set_type": "working", "reps": 8, "rpe": Decimal(9), "reps_left": 10, "reps_right": 8,
               "rpe_left": Decimal(8), "rpe_right": Decimal(9)}]
    # Cliente reenvia só o lado direito: o esquerdo vem da gravada.
    merged = merge_sets_for_update([{"set_number": 1, "reps": 12, "reps_right": 12}], stored)[0]
    assert (merged["reps_left"], merged["reps_right"], merged["reps"]) == (10, 12, 10)
    assert merged["rpe"] == Decimal(9)
    # Lados limpos de propósito (null explícito): vale o enviado.
    cleared = merge_sets_for_update(
        [{"set_number": 1, "reps": 9, "rpe": Decimal(7), **{f: None for f in ss.SIDE_FIELDS}}], stored
    )[0]
    assert (cleared["reps"], cleared["rpe"]) == (9, Decimal(7))


def test_mescla_sem_lados_mantem_o_comportamento():
    merged = merge_sets_for_update([{"set_number": 1, "reps": 10, "rpe": Decimal(8)}], [])[0]
    assert (merged["reps"], merged["rpe"]) == (10, Decimal(8))
    assert all(merged.get(field) is None for field in ss.SIDE_FIELDS)


def test_patch_puro_recalcula():
    stored = {"set_number": 1, "reps": 8, "rpe": None, "reps_left": 10, "reps_right": 8, "reps_pending": 0}
    merged = merge_set_patch(stored, {"rpe_left": Decimal(9)})
    assert (merged["reps"], merged["rpe"]) == (8, Decimal(9))


# ---------------------------------------------------------------------------
# Prompts e relatório: interpolam o texto do dono
# ---------------------------------------------------------------------------


def test_resumo_de_exercicio_mostra_os_lados_e_a_regra():
    from app import exercise_summary_ai as es

    com = [{"set_number": 1, "set_type": "working", "reps": 8, "weight_kg": 20, "rpe": 9, "reps_left": 10, "reps_right": 8}]
    texto = es._format_sets(com)
    assert "(por lado: reps E 10 · D 8)" in texto and ss.WORST_SIDE_RULE_TEXT in texto
    sem = es._format_sets([{"set_number": 1, "set_type": "working", "reps": 8, "weight_kg": 20, "rpe": 9}])
    assert "por lado" not in sem and ss.WORST_SIDE_RULE_TEXT not in sem
    assert es.effective_prompt_version({"sets": com}).endswith(ss.PROMPT_VERSION_SUFFIX)
    assert es.effective_prompt_version({"sets": []}) == es.PROMPT_VERSION


def test_resumo_de_treino_e_avaliacao_versionam_so_com_lados():
    from app import exercise_set_evaluation_ai as ev
    from app import workout_summary_ai as wsum

    com = {"exercises": [{"sets": [{"reps": 8, "reps_left": 10, "reps_right": 8}]}]}
    assert wsum.effective_prompt_version(com).endswith(ss.PROMPT_VERSION_SUFFIX)
    assert wsum.effective_prompt_version({"exercises": [{"sets": [{"reps": 8}]}]}) == wsum.PROMPT_VERSION
    assert ev._sides_suffix({"reps": 8}) == ""
    assert "E 10 · D 8" in ev._sides_suffix({"reps": 8, "reps_left": 10, "reps_right": 8})


def test_relatorio_markdown_interpola_a_regra_do_dono():
    import inspect

    from app.intelligence.report import markdown_renderer

    assert "WORST_SIDE_RULE_TEXT" in inspect.getsource(markdown_renderer)
