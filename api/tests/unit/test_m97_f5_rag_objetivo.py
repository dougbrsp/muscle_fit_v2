"""M97 F5: RAG do objetivo (força) e interpolação dos placeholders (mapa §68).

- O material de força mora em `rag/markdown/OBJETIVOS/FORCA/`, FORA da raiz:
  a descoberta automática não o vê, e o texto de hipertrofia de
  `select_rag_text` é byte a byte o de antes.
- Com objetivo força, o material abre o texto (os consumidores cortam pela
  cauda) e o teto soma (`rag_base_limit`).
- Todo placeholder do repositório resolve num valor de dono; nenhum `{{`
  sobra no texto injetado; nenhum número de prescrição escrito à mão
  (`faixa_reps_prosa` cobre `OBJETIVOS/`).
- RML fica fora dos chunks injetados (contexto conceitual).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app import rag_chunking as rc
from app import rag_placeholders as rp
from app.domain.e1rm import rir_from_rpe
from app.domain.prescription_defaults import defaults_for
from app.domain.training_goal import DEFAULT_GOAL, GOAL_RAG_FOLDERS, GOAL_VALUES, is_default
from app.domain.training_safety import goal_rpe_ceiling, goal_target_rpe
from app.domain.training_history import parse_reps_range
from app.periodization.limits import heavy_compound_range, session_exercises_min

RAIZ = Path(__file__).resolve().parents[3]
RAG_DIR = RAIZ / "api" / "privado" / "rag" / "markdown"
OUTRO = next(goal for goal in GOAL_VALUES if not is_default(goal))
GRUPOS = ["Peito", "Tríceps", "Antebraço"]


@pytest.fixture(autouse=True)
def _rag_do_repo(monkeypatch):
    monkeypatch.delenv("RAG_S3_BUCKET", raising=False)
    monkeypatch.setattr(rc, "RAG_MARKDOWN_DIR", RAG_DIR)


def _arquivos_de_objetivo() -> list[Path]:
    return sorted((RAG_DIR / rc.GOAL_RAG_DIRNAME).rglob("*.md"))


# ---------------------------------------------------------------------------
# Onde o material mora
# ---------------------------------------------------------------------------


def test_cada_pasta_de_objetivo_existe_e_tem_markdown():
    for goal, folder in GOAL_RAG_FOLDERS.items():
        if folder is None:
            assert is_default(goal), "só o default fica sem material próprio"
            continue
        pasta = RAG_DIR / rc.GOAL_RAG_DIRNAME / folder
        assert pasta.is_dir() and list(pasta.glob("*.md")), pasta


def test_material_de_objetivo_fica_fora_da_descoberta_automatica_da_raiz():
    nomes = {p.name for p in _arquivos_de_objetivo()}
    assert nomes
    assert not nomes & set(rc.discover_additional_files())
    assert not any(p.parent == RAG_DIR for p in _arquivos_de_objetivo())


# ---------------------------------------------------------------------------
# Hipertrofia byte a byte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("objetivo", [None, DEFAULT_GOAL.value, DEFAULT_GOAL, [DEFAULT_GOAL.value], ()])
def test_texto_de_hipertrofia_e_identico_ao_de_antes(objetivo):
    antes = rc.select_rag_text(GRUPOS, has_training_type=True)
    assert rc.select_rag_text(GRUPOS, has_training_type=True, training_goal=objetivo) == antes
    assert rc.select_goal_rag_text(GRUPOS, objetivo) == ""
    assert rc.rag_base_limit(objetivo) == rc.MAX_RAG_BASE_CHARS


def test_forca_abre_o_texto_e_a_base_vem_igual_depois():
    base = rc.select_rag_text(GRUPOS, has_training_type=True)
    com = rc.select_rag_text(GRUPOS, has_training_type=True, training_goal=OUTRO)
    material = rc.select_goal_rag_text(GRUPOS, OUTRO)
    assert material
    assert com == material + "\n\n---\n\n" + base
    assert com.startswith("## Arquivo: OBJETIVOS/")
    assert rc.rag_base_limit(OUTRO) == rc.MAX_RAG_BASE_CHARS + rc.MAX_RAG_GOAL_CHARS
    # sessão/Modelo híbrido: a coleção traz o mesmo material uma vez só
    assert rc.select_goal_rag_text(GRUPOS, [DEFAULT_GOAL.value, OUTRO, OUTRO]) == material


def test_material_inteiro_cabe_no_teto_proprio():
    for grupos in ([], GRUPOS, ["Coxas anterior", "Costas", "Peito", "Antebraço", "Tríceps"]):
        texto = rc.select_goal_rag_text(grupos, OUTRO)
        assert "truncado" not in texto
        assert len(texto) <= rc.MAX_RAG_GOAL_CHARS


def test_rml_fica_fora_dos_chunks_injetados_e_o_uso_no_app_entra():
    texto = rc.select_goal_rag_text([], OUTRO)
    assert "FOR-000" in texto
    for rml in ("FOR-008", "FOR-016", "FOR-033", "FOR-034", "FOR-035", "FOR-036", "FOR-037", "FOR-038"):
        assert f"## {rml}" not in texto and f"### {rml}" not in texto


def test_chunk_de_grupo_e_filtrado_como_na_base():
    """FOR-025 (tríceps) e FOR-026 (antebraço) só entram com o grupo."""
    sem = rc.select_goal_rag_text(["Coxas anterior"], OUTRO)
    com = rc.select_goal_rag_text(GRUPOS, OUTRO)
    assert "FOR-026" not in sem and "FOR-026" in com


# ---------------------------------------------------------------------------
# Interpolação
# ---------------------------------------------------------------------------


def test_todo_placeholder_do_repositorio_resolve():
    for arquivo in _arquivos_de_objetivo():
        texto = arquivo.read_text(encoding="utf-8")
        assert rp.placeholders_in(texto), f"{arquivo.name}: sem placeholder (número escrito à mão?)"
        rendered = rp.interpolate(texto, strict=True)
        assert "{{" not in rendered and "}}" not in rendered


def test_texto_injetado_nao_tem_placeholder_cru():
    assert "{{" not in rc.select_goal_rag_text(GRUPOS, OUTRO)


def test_valores_saem_dos_donos():
    valores = rp.placeholder_values()
    for goal in GOAL_VALUES:
        perfil = defaults_for(goal)
        reps_min, reps_max = parse_reps_range(perfil.reps_target)
        rpe_min, rpe_max = parse_reps_range(perfil.rpe_target)
        assert valores[f"{goal}.reps"] == perfil.reps_target
        assert valores[f"{goal}.reps_min"] == str(reps_min)
        assert valores[f"{goal}.reps_max"] == str(reps_max)
        assert valores[f"{goal}.rpe"] == perfil.rpe_target
        assert valores[f"{goal}.rpe_alvo"] == str(goal_target_rpe(goal))
        assert valores[f"{goal}.rir_min"] == rp._number(rir_from_rpe(rpe_max))
        assert valores[f"{goal}.rir_max"] == rp._number(rir_from_rpe(rpe_min))
        assert valores[f"{goal}.series"] == str(perfil.sets_count)
        assert valores[f"{goal}.exercicios_min"] == str(session_exercises_min(goal))
        assert valores[f"{goal}.descanso_min_s"] == str(perfil.min_rest_seconds)
        ceiling = goal_rpe_ceiling(goal)
        assert (f"{goal}.rpe_teto" in valores) is (ceiling is not None)
        compostos = heavy_compound_range(goal)
        assert (f"{goal}.compostos_min" in valores) is (compostos is not None)


def test_placeholder_desconhecido_estrito_levanta_e_em_producao_fica_e_avisa(caplog):
    with pytest.raises(rp.UnknownPlaceholder):
        rp.interpolate("x {{nao.existe}} y", strict=True)
    with caplog.at_level(logging.WARNING, logger=rp.logger.name):
        assert rp.interpolate("x {{nao.existe}} y") == "x {{nao.existe}} y"
    assert any("nao.existe" in r.getMessage() for r in caplog.records)


def test_interpolacao_troca_e_preserva_o_resto():
    valores = {"a.b": "7"}
    assert rp.interpolate("RPE {{a.b}} e {{ a.b }}.", values=valores) == "RPE 7 e 7."


# ---------------------------------------------------------------------------
# Guarda: o material do objetivo está no escopo de `faixa_reps_prosa`
# ---------------------------------------------------------------------------


def test_guarda_de_faixa_de_reps_cobre_o_material_do_objetivo():
    from tests.unit import test_guarda_fontes_da_verdade as guarda

    padrao = next(p for p in guarda.PADROES if p.chave == "faixa_reps_prosa")
    for arquivo in _arquivos_de_objetivo():
        rel = arquivo.relative_to(RAIZ).as_posix()
        assert guarda._no_escopo(padrao, rel), rel
    # a forma do documento-fonte (antes da curadoria) seria barrada
    assert padrao.regex.search("convergem para 3 a 5 repetições como faixa prática")
    assert not padrao.regex.search("de {{strength.reps_min}} a {{strength.reps_max}} repetições")
