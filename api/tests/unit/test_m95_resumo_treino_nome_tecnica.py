"""M95: o resumo de IA do fim de treino rotula series de tecnica com o NOME do
catalogo `advanced_techniques` (pelo `technique_code` real da linha), nunca
com o codigo cru do `set_type` (`forced_rep`, `drop_set`, `rest_pause`)."""

from __future__ import annotations

import re

from app import advanced_techniques
from app import workout_ai_generation_core as core
from app import workout_summary_ai as mod
from app.set_labels import TECHNIQUE_FALLBACK_LABEL, set_display_label, technique_names

# Itens no formato de `advanced_techniques.list_techniques` (nomes de teste:
# o teste prova que o prompt usa o que o catalogo devolver).
CATALOGO = [
    {"code": "partial_reps", "name": "Parciais", "set_type": "forced_rep", "is_active": True},
    {"code": "forced_reps", "name": "Repetições forçadas", "set_type": "forced_rep", "is_active": True},
    {"code": "drop_set", "name": "Drop-set", "set_type": "drop_set", "is_active": True},
    {"code": "rest_pause", "name": "Rest-pause", "set_type": "rest_pause", "is_active": True},
]
NOMES = technique_names(CATALOGO)

CODIGOS_CRUS = ("forced_rep", "drop_set", "rest_pause", "partial_reps", "forced_reps")


def _activity() -> dict:
    return {
        "id": 143,
        "name": "Ficha 143",
        "local_date": "2026-10-25",
        "exercises": [
            {
                "id": 1,
                "exercise_name": "Supino",
                "sets": [
                    {"set_number": 1, "set_type": "warmup", "reps": 12, "weight_kg": 20},
                    {"set_number": 2, "set_type": "working", "reps": 10, "weight_kg": 60, "rpe": 9},
                    {
                        "set_number": 3,
                        "set_type": "forced_rep",
                        "technique_code": "partial_reps",
                        "anchor_set_number": 2,
                        "reps": 4,
                        "weight_kg": 60,
                    },
                    {"set_number": 4, "set_type": "drop_set", "technique_code": "drop_set", "reps": 8, "weight_kg": 40},
                    # Linha legada sem codigo: deriva pelo `set_type`.
                    {"set_number": 5, "set_type": "rest_pause", "reps": 3, "weight_kg": 60},
                    {"set_number": 6, "set_type": "backoff", "reps": 12, "weight_kg": 50},
                ],
            }
        ],
    }


def _exercicios_do_prompt(prompt: str) -> str:
    """As linhas da lista de exercicios (`_format_exercises`), sem depender do
    texto do template (privado, M103 P2)."""
    trecho = "\n".join(linha for linha in prompt.split("\n") if linha.startswith("- ") and " série(s) — " in linha)
    assert trecho
    return trecho


def test_parciais_aparece_com_o_nome_do_catalogo():
    trecho = _exercicios_do_prompt(mod.build_prompt(_activity(), technique_names=NOMES))
    assert "4x60kg [técnica: Parciais]" in trecho
    assert "8x40kg [técnica: Drop-set]" in trecho
    assert "3x60kg [técnica: Rest-pause]" in trecho
    assert "12x50kg [série reduzida (back-off)]" in trecho
    assert "10x60kg@RPE9," in trecho  # serie de trabalho comum sem rotulo


def test_nenhum_codigo_cru_chega_ao_prompt():
    for nomes in (NOMES, None, {}):
        trecho = _exercicios_do_prompt(mod.build_prompt(_activity(), technique_names=nomes))
        for codigo in CODIGOS_CRUS:
            assert codigo not in trecho, (codigo, nomes)
        assert not re.search(r"\[[a-z]+_[a-z_]+\]", trecho)


def test_sem_catalogo_cai_no_rotulo_generico():
    trecho = _exercicios_do_prompt(mod.build_prompt(_activity()))
    assert f"4x60kg [{TECHNIQUE_FALLBACK_LABEL}]" in trecho


def test_working_vinculada_com_tecnica_recebe_o_nome():
    linha = {"set_type": "working", "technique_code": "partial_reps", "anchor_set_number": 2}
    assert set_display_label(linha, NOMES) == "técnica: Parciais"
    assert set_display_label({"set_type": "working"}, NOMES) is None
    assert set_display_label({"set_type": "algo_novo"}, NOMES) == "série não padrão"


def test_dispatch_passa_os_nomes_do_catalogo_ao_prompt(monkeypatch):
    capturado = {}
    monkeypatch.setattr(core, "activity_detail", lambda *a, **k: _activity())
    monkeypatch.setattr(advanced_techniques, "list_techniques", lambda include_inactive=False: CATALOGO)
    monkeypatch.setattr(core, "safe_read", lambda reader, *a, default=None, **k: _safe(reader, default))

    def _gerar(detail, engine_decisions=None, user_id=None, technique_names=None):
        capturado["prompt"] = mod.build_prompt(detail, engine_decisions, technique_names)
        return {"summary": "ok", "input_sha256": "x"}

    monkeypatch.setattr(core, "generate_workout_summary", _gerar)
    monkeypatch.setattr(core, "save_activity_ai_summary", lambda *a, **k: {"ai_summary_generated_at": None})
    core.dispatch_activity_summary({"activity_id": 143}, user_id=1)
    assert "[técnica: Parciais]" in capturado["prompt"]


def _safe(reader, default):
    try:
        return reader()
    except Exception:  # noqa: BLE001 - decisao do motor sem banco no unit
        return default
