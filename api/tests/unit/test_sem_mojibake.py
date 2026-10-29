"""Guarda contra mojibake (bytes UTF-8 lidos como cp1252/Latin-1 e regravados)
em arquivos versionados de codigo, RAG, infra, docs e ferramentas.

Contexto (2026-09-14): literais SQL com acento em `app/analytics.py`
(`MUSCLE_GROUP_CASE`, removido no COD-05 em 2026-10-09) e o filtro `LIKE '%força%'` em `app/analytics.py` e
`app/intelligence/*` estavam duplamente codificados. Os rotulos saiam errados
nos dashboards e os padroes com acento nunca casavam.

O padrao e os exemplos quebrados sao GERADOS em tempo de execucao
(`chr()`/`encode()`), para que este proprio arquivo nao contenha as
sequencias que procura.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# `api` inclui o RAG (submodulo privado api/privado, M103 P0).
SCANNED_DIRS = ("api", "frontend/src", "infra", "docs", "tools")
TEXT_SUFFIXES = {
    ".py", ".sql", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json",
    ".md", ".txt", ".yml", ".yaml", ".tf", ".tfvars", ".hcl", ".sh", ".css",
    ".html", ".toml", ".ini", ".cfg",
}
SKIPPED_PARTS = {"node_modules", ".next", "out", "dist", "build", "__pycache__", ".venv", "venv", ".terraform"}
NO_BOM_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".sql"}


def _broken(text: str) -> str:
    """Simula a corrupcao: UTF-8 lido como cp1252."""
    return text.encode("utf-8").decode("cp1252")


def _build_pattern() -> re.Pattern[str]:
    # Continuacao UTF-8 (0x80-0xBF) vista como cp1252 (bytes indefinidos no
    # cp1252 - 0x81, 0x8D, 0x8F, 0x90, 0x9D - viram o proprio code point).
    continuation = sorted({bytes([b]).decode("cp1252", errors="ignore") or chr(b) for b in range(0x80, 0xC0)})
    cont_class = "[" + "".join(re.escape(ch) for ch in continuation) + "]"
    lead_c3, lead_c2, lead_e2 = chr(0xC3), chr(0xC2), chr(0xE2)
    euro = bytes([0x80]).decode("cp1252")
    return re.compile(
        f"{lead_c3}{cont_class}"  # a/e/i/o/u acentuados, c cedilha, til ...
        f"|{lead_e2}{euro}"  # aspas curvas, travessao, reticencias ...
        f"|{lead_c2}{cont_class}"  # nbsp, ordinais, grau ...
    )


MOJIBAKE = _build_pattern()

# Arquivos em que a sequencia e INTENCIONAL. Cada entrada precisa de motivo.
ALLOWLIST: dict[str, str] = {
    "api/app/intelligence/normalization_service.py": (
        "reparo de mojibake em dados de entrada: os marcadores e replace() sao as proprias sequencias"
    ),
    "api/app/exercise_mapping_canonical.py": (
        "reparo de mojibake em nomes de exercicio: os marcadores e replace() sao as proprias sequencias"
    ),
    "api/db/migrations/001_schema.sql": (
        "comentario documenta nomes legados corrompidos que a propria migration limpa; "
        "migration ja aplicada nao deve ser reescrita"
    ),
    "tools/onprem-sync/README.md": "documenta um exemplo de dupla codificacao e um nome como esta gravado no banco",
    "api/privado/docs/PROMPTS_TRANSCRICAO.md": (
        "ex-secao 6.4 do MUSCLE_FIT.md (M103 P1): transcreve um filtro SQL que casa o nome corrompido "
        "de atividades de forca (LIKE com a sequencia dupla-codificada)"
    ),
}


def _scanned_files() -> list[Path]:
    files = []
    for directory in SCANNED_DIRS:
        base = REPO_ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in TEXT_SUFFIXES or not path.is_file():
                continue
            if SKIPPED_PARTS.intersection(path.relative_to(REPO_ROOT).parts):
                continue
            files.append(path)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_varredura_encontra_arquivos():
    files = {_rel(path) for path in _scanned_files()}
    assert "api/app/analytics.py" in files
    assert "api/tests/unit/test_sem_mojibake.py" in files
    assert any(name.startswith("frontend/src/") for name in files)
    assert any(name.startswith("api/privado/rag/") for name in files)


def test_allowlist_sem_entradas_obsoletas():
    """Arquivo da allowlist que ja nao tem a sequencia deve sair dela."""
    for rel in ALLOWLIST:
        path = REPO_ROOT / rel
        assert path.is_file(), f"allowlist obsoleta: {rel} nao existe"
        assert MOJIBAKE.search(path.read_text(encoding="utf-8")), f"allowlist obsoleta: {rel} ja esta limpo"


def test_arquivos_versionados_sem_mojibake_e_sem_bom():
    problems = []
    for path in _scanned_files():
        rel = _rel(path)
        data = path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf") and path.suffix in NO_BOM_SUFFIXES:
            problems.append(f"{rel}: BOM UTF-8")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{rel}: nao e UTF-8 ({exc})")
            continue
        if rel in ALLOWLIST:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if MOJIBAKE.search(line):
                # Conteudo do submodulo privado (M103) nao vai para o log publico do CI.
                if rel.startswith("api/privado/"):
                    problems.append(f"{rel}:{lineno}: (trecho omitido: repositorio privado)")
                    continue
                snippet = line.strip()[:120].encode("ascii", "backslashreplace").decode()
                problems.append(f"{rel}:{lineno}: {snippet}")
    assert not problems, "Mojibake/encoding invalido (corrigir com encode('cp1252').decode('utf-8')):\n" + "\n".join(
        problems
    )


@pytest.mark.parametrize(
    "correto",
    ["Bíceps", "Tríceps", "Quadríceps", "Glúteos/Quadril", "elevação lateral", "força", "inválido", "‘aspas’ – ok"],
)
def test_padrao_detecta_corrupcao_e_aceita_texto_correto(correto):
    quebrado = _broken(correto)
    assert MOJIBAKE.search(quebrado)
    assert not MOJIBAKE.search(correto)
    assert quebrado.encode("cp1252").decode("utf-8") == correto
