"""RAG seletivo por grupo muscular para geracao de ficha de treino.

`workout_plan_ai.load_rag_text()` concatena os 6 arquivos inteiros de
`workspace/rag/markdown/*.md` (~178KB) em toda chamada de
`POST /workout-plans/generate`, sem nenhum filtro. Dois desses arquivos
(HIPERTROFIA.md, FULLBODY.md) e um terceiro no mesmo estilo (ANTEBRACO.md)
foram explicitamente estruturados em chunks autocontidos com IDs/Tags/Aliases
para permitir recuperacao seletiva — este modulo usa essa estrutura para
injetar no prompt so o conteudo geral + o conteudo especifico dos grupos
musculares candidatos da ficha, em vez do dump completo (que inclui, por
exemplo, chunks de pernas quando a ficha so vai treinar peito/costas, alem de
BIOIMPEDANCIA.md, que nao tem nenhuma relacao com selecao de exercicio).

Se o parsing de QUALQUER arquivo falhar ou nao encontrar nenhum chunk (sinal
de que o regex nao bate mais com o formato do arquivo), este modulo degrada
para incluir o arquivo INTEIRO sem selecao (nunca remove conteudo da ficha
silenciosamente por causa de um bug de parsing) e loga um aviso.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rag_storage import ensure_rag_local

logger = logging.getLogger(__name__)

RAG_MARKDOWN_DIR = Path(os.environ.get("RAG_MARKDOWN_DIR", "/app/rag"))

# Nome (relativo a RAG_MARKDOWN_DIR) do diretorio com subpastas por "Tipo de
# Treinamento" (ex: "1. Full Body", "2. ABC"), cada uma com seus proprios .md
# de metodologia especifica desse tipo de treino. NADA le essas subpastas
# hoje por padrao — apenas quando um training_type tiver `rag_folder`
# explicitamente configurado (ver `training_types.py`/engines v1/v2), via
# `load_rag_folder_text()` abaixo.
TIPOS_TREINO_DIRNAME = "TIPOS_TREINO"

# Tetos do RAG injetado em prompt (dono unico, IA-02). Prompt grande custa
# dinheiro e latencia; o corte e por truncamento explicito, com marca visivel,
# para nunca gerar "meio arquivo" silencioso. Nasceram na Periodizacao
# (`periodization/context.py`, que os reexporta) e valem para todo prompt que
# injeta `select_rag_text`: Insights e Analise por IA usam o MESMO teto da base
# filtrada por grupo (`MAX_RAG_BASE_CHARS`) que a Periodizacao.
MAX_RAG_BASE_CHARS = 30_000
MAX_RAG_FOLDER_CHARS = 20_000
# Teto do RAG INTEIRO da Periodizacao: com 10 fontes escolhidas os limites por
# parte somariam 230 mil caracteres (~58 mil tokens) num unico prompt. Este teto
# mantem a geracao na casa de ~20 mil tokens de entrada, o que decide o custo
# por geracao (secao 7.3) e a chance de caber no timeout.
MAX_RAG_TOTAL_CHARS = 80_000
# Teto do material PRÓPRIO do objetivo (M97 F5, `select_goal_rag_text`). Ele
# abre o texto de `select_rag_text` e soma ao teto da base
# (`rag_base_limit`): o objetivo nunca expulsa a base, e a base nunca corta o
# objetivo. Hipertrofia (default) não tem material próprio: teto e texto de
# sempre. Medido em 2026-10-25: o material de força inteiro (todos os chunks,
# placeholders já interpolados) tem ~27 mil caracteres (~7 mil tokens), e o
# teto cabe ele com folga para qualquer seleção de grupos.
MAX_RAG_GOAL_CHARS = 28_000

# Pasta (relativa a RAG_MARKDOWN_DIR) com uma subpasta por objetivo de treino
# (`training_goal.GOAL_RAG_FOLDERS`). Fora da raiz de propósito: a descoberta
# automática (`discover_additional_files`) só olha `*.md` da raiz.
GOAL_RAG_DIRNAME = "OBJETIVOS"


def truncate_rag(text: str, limit: int, label: str) -> str:
    """Corta `text` em `limit` caracteres com uma marca visivel do corte."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[...{label} truncado em {limit} caracteres]"


def _tipos_treino_dir() -> Path:
    """Resolve `RAG_MARKDOWN_DIR / TIPOS_TREINO_DIRNAME` no momento da
    chamada (nao em import time), para respeitar monkeypatch de
    `rc.RAG_MARKDOWN_DIR` nos testes (mesmo padrao das demais funcoes deste
    modulo, que sempre leem RAG_MARKDOWN_DIR dinamicamente via
    `_read_file_text`)."""
    return RAG_MARKDOWN_DIR / TIPOS_TREINO_DIRNAME


# Extrai o inteiro líder do nome de uma subpasta (ex: "1. Full Body" -> 1,
# "10. Novo Tipo" -> 10), usado para ordenar list_training_type_rag_folders()
# numericamente em vez de como string (senão "10. X" viria antes de "2. Y").
_FOLDER_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)")

# Raizes normalizadas (minusculo, sem acento) de grupo muscular, usadas tanto
# para classificar um chunk como "geral" vs "especifico de grupo muscular"
# quanto para casar os grupos alvo (vindos de `muscle_recovery`) contra as
# tags/aliases de cada chunk e contra a coluna "Grupamento Muscular" da
# TABELA_EXERCICIOS.md. Deliberadamente NAO usa ranges de ID (ex: "HYP-001 a
# HYP-008") — isso seria fragil a qualquer mudanca futura no conteudo do RAG.
MUSCLE_GROUP_ROOTS: tuple[str, ...] = (
    "peito",
    "peitoral",
    "costas",
    "dorsal",
    "ombro",
    "deltoide",
    "biceps",
    "triceps",
    "perna",
    "quadriceps",
    "posterior de coxa",
    "panturrilha",
    "gluteo",
    "antebraco",
    "abdomen",
    "abdominal",
    "core",
    "trapezio",
    "lombar",
    # As 3 raizes abaixo foram adicionadas porque os nomes REAIS de grupo
    # muscular da tabela `muscle_groups` do banco ("Coxas anterior", "Coxas
    # posterior", "Adutores do quadril", "Flexores do quadril", "Manguito
    # rotador") nao batiam como substring de nenhuma raiz existente acima
    # ("perna"/"quadriceps" nao sao substring de "coxas anterior"; "posterior
    # de coxa" tem ordem de palavras diferente de "coxas posterior"). Sem
    # isso, _target_roots() para um dia de perna do ciclo ABC resolvia so
    # 'panturrilha' e descartava todo o RAG de agachamento/quadriceps/coxa.
    "coxa",
    "quadril",
    "manguito",
)

# Mapa de arquivos especificos de UM exercicio/regiao do corpo (nao
# metodologia geral) para os grupos musculares que aquele conteudo trabalha.
# Usado como fallback em `_chunk_roots()` quando um chunk nao tem, nas
# proprias tags, nenhuma raiz de MUSCLE_GROUP_ROOTS — o que acontece muito
# nesses arquivos porque as tags/titulos usam vocabulario tecnico do
# movimento (ex.: "valgo dinamico", "cavalete") em vez do nome do grupo
# muscular. Sem esse fallback, esses chunks eram tratados como "conteudo
# geral" e injetados em TODA ficha, mesmo quando o grupo nao era treinado.
# Arquivos de metodologia genuinamente geral (HABILIDADES_TREINO.md,
# TREINO_A/B/C.md, TREINO_ABC_PERFEITO.md, COMO_MONTAR_ABC.md) ficam de
# fora deste mapa de proposito — continuam sempre incluidos.
FILE_TOPIC_ROOTS: dict[str, set[str]] = {
    "ABDOMEN.md": {"abdomen", "abdominal", "core"},
    "AGACHAMENTO.md": {"coxa", "quadriceps", "gluteo", "posterior de coxa"},
    "BICEPS.md": {"biceps"},
    "COSTAS.md": {"costas", "dorsal"},
    "PANTURRILHA.md": {"panturrilha"},
    "PEITO.md": {"peito", "peitoral"},
    "PERNA.md": {"coxa", "quadriceps", "posterior de coxa", "gluteo", "panturrilha"},
    "POSTERIOR_COXA.md": {"posterior de coxa", "coxa"},
    "TRAPEZIO.md": {"trapezio"},
    "TRICEPS.md": {"triceps"},
}

# Delimitador de chunk real: "## <PREFIXO>-NNN — titulo", onde <PREFIXO> e
# 2 a 10 letras maiusculas com um digito opcional no final (cobre HYP-NNN,
# HYP2-NNN, ANT-NNN e qualquer prefixo novo na mesma convencao, ex:
# COS-NNN). Cabecalhos de secao/indice (ex: "## Como usar este documento em
# RAG", "## 4.1. Estrutura Geral") nunca batem com este regex.
CHUNK_HEADER_RE = re.compile(r"^## ([A-Z]{2,10}\d?-\d+) — (.+)$", re.MULTILINE)

# Arquivos com tratamento hardcoded proprio em select_rag_text() (nao devem
# ser processados de novo pela descoberta automatica abaixo).
KNOWN_FILES: set[str] = {
    "PROGRESSAO.md",
    "HIPERTROFIA.md",
    "ANTEBRACO.md",
    "TABELA_EXERCICIOS.md",
}

# Arquivos que existem em RAG_MARKDOWN_DIR mas devem ficar de fora da
# selecao mesmo com a descoberta automatica ativa. Edite esta lista pra
# excluir um arquivo novo sem precisar mexer em mais nada.
#
# CARDIO.md fica fora da descoberta automatica da GERACAO DE FICHA porque nao
# tem relacao com selecao de exercicio de musculacao. Ele e o indice do agente
# de cardio (M98 C3a): `select_cardio_rag_text` le so os chunks de
# `CARDIO_AGENT_CHUNK_IDS`, com os placeholders `{{cardio.*}}` interpolados dos
# motores donos (`rag_placeholders`). Como seus chunks nao batem
# com MUSCLE_GROUP_ROOTS, a descoberta automatica tratava o arquivo
# inteiro (~50KB) como "conteudo geral" e injetava ele por completo em
# toda geracao de ficha ABC/Full Body, estourando o limite de contexto
# da OpenAI (erro 400 context_length_exceeded) e forcando o fallback
# deterministico, que perde variedade de equipamento e preenchimento de
# carga/RPE/descanso/warmup.
EXCLUDED_FILES: set[str] = {"BIOIMPEDANCIA.md", "CARDIO.md"}

# Cabecalho de secao de nivel 1 (ex: "# 3. Chunks RAG", "# 4. Regras
# Operacionais Consolidadas"). Usado como limite de fim de chunk quando nao
# ha proximo chunk (evita que o ultimo chunk "engula" secoes inteiras de
# glossario/regras/diagramas que vem depois da area de chunks).
SECTION_HEADER_RE = re.compile(r"^# \S.*$", re.MULTILINE)

TAGS_LINE_RE = re.compile(r"^\*\*Tags:\*\*\s*(.+)$", re.MULTILINE | re.IGNORECASE)
ALIASES_LINE_RE = re.compile(r"^\*\*Aliases:\*\*\s*(.+)$", re.MULTILINE | re.IGNORECASE)

# Marcador a partir do qual FULLBODY.md/ANTEBRACO.md tem chunks de verdade;
# tudo antes disso e indice/vocabulario controlado (meta-documentacao sobre
# como o RAG deveria ser usado), nao deve ser injetado no prompt.
CHUNKS_SECTION_MARKER = "# 3. Chunks RAG"


@dataclass
class RagChunk:
    id: str
    source_file: str
    title: str
    tags: list[str] = field(default_factory=list)
    content: str = ""


def _normalize(text: str) -> str:
    """minusculo + sem acento, para comparacao robusta de substring."""
    text = text.strip().lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _split_tag_list(raw: str) -> list[str]:
    """Quebra uma linha "**Tags:** `a`, `b`, c" (ou "Aliases") em itens,
    removendo backticks/markdown de enfase e o line-break de markdown ("  ")
    no fim da linha, quando presentes."""
    cleaned = raw.strip().rstrip("\\").strip()
    items = [item.strip().strip("`*").strip() for item in cleaned.split(",")]
    return [item for item in items if item]


def _read_file_text(filename: str) -> str:
    path = RAG_MARKDOWN_DIR / filename
    return path.read_text(encoding="utf-8")


def parse_chunks(text: str, source_file: str) -> list[RagChunk]:
    """Parser generico: extrai todos os chunks (delimitados por
    CHUNK_HEADER_RE) de `text`, junto com as Tags/Aliases (combinadas em
    `tags`) declaradas no corpo de cada chunk. O conteudo de cada chunk vai do
    inicio do proprio cabecalho ate o proximo cabecalho de chunk OU o proximo
    cabecalho de secao de nivel 1 (o que vier primeiro) OU o fim do texto."""
    chunk_matches = list(CHUNK_HEADER_RE.finditer(text))
    if not chunk_matches:
        return []

    section_starts = [m.start() for m in SECTION_HEADER_RE.finditer(text)]

    chunks: list[RagChunk] = []
    for index, match in enumerate(chunk_matches):
        chunk_id = match.group(1)
        title = match.group(2).strip()
        start = match.start()

        next_chunk_start = (
            chunk_matches[index + 1].start() if index + 1 < len(chunk_matches) else len(text)
        )
        # Proxima secao de nivel 1 que comeca depois deste chunk (se houver),
        # para nao deixar o ultimo chunk de uma area "engolir" as secoes que
        # vem depois (glossario, diagramas, regras operacionais etc.).
        next_section_start = next(
            (pos for pos in section_starts if pos > start), len(text)
        )

        end = min(next_chunk_start, next_section_start)
        content = text[start:end].rstrip()

        body = text[match.end():end]
        tags: list[str] = []
        tags_match = TAGS_LINE_RE.search(body)
        if tags_match:
            tags.extend(_split_tag_list(tags_match.group(1)))
        aliases_match = ALIASES_LINE_RE.search(body)
        if aliases_match:
            tags.extend(_split_tag_list(aliases_match.group(1)))

        chunks.append(
            RagChunk(id=chunk_id, source_file=source_file, title=title, tags=tags, content=content)
        )

    return chunks


def load_hipertrofia_chunks() -> list[RagChunk]:
    text = _read_file_text("HIPERTROFIA.md")
    return parse_chunks(text, "HIPERTROFIA.md")


def load_antebraco_chunks() -> list[RagChunk]:
    text = _read_file_text("ANTEBRACO.md")
    marker_index = text.find(CHUNKS_SECTION_MARKER)
    if marker_index == -1:
        return []
    return parse_chunks(text[marker_index:], "ANTEBRACO.md")


def _target_roots(target_groups: list[str]) -> set[str]:
    roots: set[str] = set()
    for group in target_groups:
        normalized_group = _normalize(group)
        for root in MUSCLE_GROUP_ROOTS:
            if root in normalized_group:
                roots.add(root)
    return roots


def _chunk_roots(chunk: RagChunk) -> set[str]:
    normalized_tags = _normalize(" ".join(chunk.tags))
    roots = {root for root in MUSCLE_GROUP_ROOTS if root in normalized_tags}
    if roots:
        return roots
    # Fallback: nenhuma tag do chunk bateu com uma raiz de grupo muscular
    # (comum em arquivos especificos de exercicio, cujas tags usam
    # vocabulario tecnico do movimento). Antes de tratar como "conteudo
    # geral", tenta o mapa de topico do ARQUIVO — se o arquivo e sabidamente
    # sobre um grupo muscular especifico (FILE_TOPIC_ROOTS), usa isso em vez
    # de deixar o chunk sempre incluido independente da ficha.
    return FILE_TOPIC_ROOTS.get(chunk.source_file, set())


def select_chunks_for_groups(chunks: list[RagChunk], target_groups: list[str]) -> list[RagChunk]:
    """Devolve os chunks "gerais" (sem nenhuma tag de grupo muscular
    especifico — sempre incluidos) + os chunks "especificos" cujas
    tags/aliases casam com alguma raiz presente em `target_groups`."""
    wanted_roots = _target_roots(target_groups)

    selected: list[RagChunk] = []
    for chunk in chunks:
        chunk_roots = _chunk_roots(chunk)
        if not chunk_roots:
            # Chunk geral (fundamentos, tecnica, seguranca, nutricao,
            # glossario etc.): sempre incluido, independente dos grupos.
            selected.append(chunk)
        elif chunk_roots & wanted_roots:
            selected.append(chunk)

    return selected


# ---------------------------------------------------------------------------
# TABELA_EXERCICIOS.md: tabela markdown simples, filtrada por linha.
# ---------------------------------------------------------------------------

TABLE_GROUP_COLUMN_NAME = "grupamento muscular"


def parse_exercise_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    """Extrai (header_cells, rows) de uma tabela markdown "|a|b|c|". Retorna
    None se nao conseguir identificar cabecalho + separador validos."""
    lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return None

    def _split_row(line: str) -> list[str]:
        stripped = line.strip().strip("|")
        return [cell.strip() for cell in stripped.split("|")]

    header = _split_row(lines[0])
    separator = lines[1]
    if not re.match(r"^\|?[\s:|-]+\|?$", separator.strip()):
        return None

    rows = [_split_row(line) for line in lines[2:]]
    return header, rows


def select_table_rows_for_groups(text: str, target_groups: list[str]) -> str | None:
    """Filtra as linhas da TABELA_EXERCICIOS.md cujo "Grupamento Muscular"
    casa com algum de `target_groups`, devolvendo uma tabela markdown menor
    (cabecalho + so as linhas relevantes). Retorna None se a tabela nao puder
    ser parseada (o caller deve cair para o arquivo inteiro nesse caso)."""
    parsed = parse_exercise_table(text)
    if parsed is None:
        return None

    header, rows = parsed
    try:
        group_col_index = next(
            i for i, name in enumerate(header) if _normalize(name) == TABLE_GROUP_COLUMN_NAME
        )
    except StopIteration:
        return None

    wanted_roots = _target_roots(target_groups)

    selected_rows = []
    for row in rows:
        if group_col_index >= len(row):
            continue
        normalized_cell = _normalize(row[group_col_index])
        cell_roots = {root for root in MUSCLE_GROUP_ROOTS if root in normalized_cell}
        if cell_roots & wanted_roots:
            selected_rows.append(row)

    if not selected_rows:
        return "## Tabela de tecnicas de exercicios por grupo muscular\n\n(nenhuma linha relevante para os grupos musculares desta ficha)"

    header_line = "|" + "|".join(header) + "|"
    separator_line = "|" + "|".join("-" * max(len(cell), 3) for cell in header) + "|"
    row_lines = ["|" + "|".join(row) + "|" for row in selected_rows]

    return "## Tabela de tecnicas de exercicios por grupo muscular\n\n" + "\n".join(
        [header_line, separator_line, *row_lines]
    )


# ---------------------------------------------------------------------------
# Montagem final do texto RAG a injetar no prompt.
# ---------------------------------------------------------------------------


def _render_selected_chunks(filename: str, chunks: list[RagChunk]) -> str:
    if not chunks:
        return f"## Arquivo: {filename} (RAG seletivo)\n\n(nenhum chunk relevante para os grupos musculares desta ficha)"
    body = "\n\n---\n\n".join(chunk.content for chunk in chunks)
    return f"## Arquivo: {filename} (RAG seletivo)\n\n{body}"


def _full_file_fallback(filename: str, reason: str, read_fn=None) -> str:
    """`read_fn`, se informado, e usado no lugar de `_read_file_text(filename)`
    para ler o conteudo (permite reaproveitar esta funcao para arquivos fora
    de RAG_MARKDOWN_DIR/filename direto, ex: arquivos dentro de subpastas de
    TIPOS_TREINO_DIR, onde `filename` e usado so como rotulo/cabecalho)."""
    logger.warning(
        "rag_chunking: fallback para arquivo inteiro de %s (%s) — selecao seletiva "
        "indisponivel, incluindo sem filtro para nao perder conteudo.",
        filename,
        reason,
    )
    read = read_fn or (lambda: _read_file_text(filename))
    try:
        text = read()
    except OSError as exc:
        logger.warning("rag_chunking: nao foi possivel ler %s no fallback: %s", filename, exc)
        return f"## Arquivo: {filename}\n\n(arquivo indisponivel)"
    return f"## Arquivo: {filename}\n\n{text}"


def _select_chunk_file_text(filename: str, loader, target_groups: list[str], read_fn=None) -> str:
    try:
        chunks = loader()
    except Exception as exc:  # noqa: BLE001 - nunca deixar um bug de parsing derrubar a geracao de ficha
        return _full_file_fallback(filename, f"excecao ao parsear: {exc}", read_fn=read_fn)

    if not chunks:
        return _full_file_fallback(filename, "nenhum chunk encontrado pelo parser", read_fn=read_fn)

    selected = select_chunks_for_groups(chunks, target_groups)
    return _render_selected_chunks(filename, selected)


def discover_additional_files() -> list[str]:
    """Lista (em ordem alfabetica, para saida deterministica) os arquivos
    .md de RAG_MARKDOWN_DIR que nao estao em KNOWN_FILES nem em
    EXCLUDED_FILES — ou seja, arquivos novos que alguem largou na pasta sem
    precisar tocar em nenhum codigo. Se o diretorio nao existir/nao puder
    ser listado, degrada para lista vazia (nunca derruba a geracao de
    ficha por causa disso)."""
    try:
        candidates = RAG_MARKDOWN_DIR.glob("*.md")
        names = [path.name for path in candidates]
    except OSError as exc:
        logger.warning(
            "rag_chunking: nao foi possivel listar %s para descoberta de arquivos novos: %s",
            RAG_MARKDOWN_DIR,
            exc,
        )
        return []

    return sorted(name for name in names if name not in KNOWN_FILES and name not in EXCLUDED_FILES)


def _select_additional_file_text(
    filename: str,
    target_groups: list[str],
    *,
    file_path: Path | None = None,
    display_name: str | None = None,
) -> str:
    """Versao generica de `_select_chunk_file_text` para um arquivo
    descoberto automaticamente (sem loader hardcoded): le o arquivo e tenta
    parsear chunks nele. Se encontrar 1+ chunks, filtra por grupo muscular
    igual aos arquivos chunk-tagged conhecidos. Se nao encontrar nenhum
    chunk, trata como PROGRESSAO.md — inclui o arquivo inteiro (mesma
    filosofia: arquivo de conteudo geral, sem chunking).

    Por padrao le `RAG_MARKDOWN_DIR / filename` (mesmo comportamento de
    sempre, usado por `discover_additional_files()`). Passe `file_path` para
    ler de um caminho arbitrario (ex: arquivos dentro de subpastas de
    TIPOS_TREINO_DIR, descobertos recursivamente) — nesse caso `display_name`
    (tipicamente o caminho relativo a TIPOS_TREINO_DIR) e usado como rotulo
    no cabecalho/logs em vez de `filename`."""
    path = file_path or (RAG_MARKDOWN_DIR / filename)
    label = display_name or filename

    def _read() -> str:
        return path.read_text(encoding="utf-8")

    def _loader() -> list[RagChunk]:
        text = _read()
        return parse_chunks(text, label)

    return _select_chunk_file_text(label, _loader, target_groups, read_fn=_read)


def discover_training_type_files() -> list[Path]:
    """Lista (recursivamente, em ordem determinística pelo caminho relativo a
    TIPOS_TREINO_DIR) todos os arquivos `.md` dentro de TIPOS_TREINO_DIR,
    qualquer que seja a profundidade da subpasta (ex:
    `TIPOS_TREINO/2. ABC/TREINO_A.md`).

    Usado SOMENTE como fallback em `select_rag_text` quando o chamador nao
    tem nenhum `training_type_id` associado (`has_training_type=False`) —
    fluxo legado de geracao direta de ficha (`abc_workout_ai.py`,
    `workout_plan_ai.py`) ou Modelo de Treino sem tipo de treino configurado.
    Quando ha `training_type_id` mas `rag_folder` ainda nao foi configurado,
    este fallback NAO e usado (ver `select_rag_text`), para nao misturar
    conteudo de um tipo de treino errado na ficha.

    Se TIPOS_TREINO_DIR nao existir/nao puder ser listado, degrada para
    lista vazia (mesma filosofia de `discover_additional_files`)."""
    ensure_rag_local()
    tipos_treino_dir = _tipos_treino_dir()
    try:
        candidates = list(tipos_treino_dir.rglob("*.md"))
    except OSError as exc:
        logger.warning(
            "rag_chunking: nao foi possivel listar %s recursivamente para fallback de TIPOS_TREINO: %s",
            tipos_treino_dir,
            exc,
        )
        return []

    return sorted(candidates, key=lambda p: p.relative_to(tipos_treino_dir).as_posix())


def _select_table_text(target_groups: list[str]) -> str:
    filename = "TABELA_EXERCICIOS.md"
    try:
        text = _read_file_text(filename)
    except OSError as exc:
        logger.warning("rag_chunking: nao foi possivel ler %s: %s", filename, exc)
        return f"## Arquivo: {filename}\n\n(arquivo indisponivel)"

    try:
        selected_text = select_table_rows_for_groups(text, target_groups)
    except Exception as exc:  # noqa: BLE001
        selected_text = None
        logger.warning(
            "rag_chunking: excecao ao filtrar %s (%s), caindo para arquivo inteiro.", filename, exc
        )

    if selected_text is None:
        return _full_file_fallback(filename, "parsing de tabela falhou")

    return f"## Arquivo: {filename} (RAG seletivo)\n\n{selected_text}"


def list_training_type_rag_folders() -> list[str]:
    """Lista os nomes das subpastas imediatas de TIPOS_TREINO_DIR (ignora
    arquivos soltos), ordenadas numericamente pelo inteiro líder do nome
    (ex: "1. Full Body", "2. ABC", "10. X" -> ordem 1, 2, 10, nunca ordem de
    string). Pastas sem número líder vão para o fim, em ordem alfabética.
    Se TIPOS_TREINO_DIR não existir/não puder ser listado, devolve []
    (mesma filosofia de degradação de `discover_additional_files`)."""
    ensure_rag_local()
    tipos_treino_dir = _tipos_treino_dir()
    try:
        entries = [entry for entry in tipos_treino_dir.iterdir() if entry.is_dir()]
    except OSError as exc:
        # A degradacao para [] e intencional (o endpoint nunca deve derrubar a
        # tela), mas ela tem DOIS significados muito diferentes e so um deles e
        # normal. Se o proprio RAG_MARKDOWN_DIR nao existe, o processo esta mal
        # CONFIGURADO (bind mount/env var faltando, ou sync de S3 que nao
        # aconteceu) e TUDO que le RAG neste servico esta silenciosamente vazio
        # - nao so esta funcao. Isso sai em ERROR, com o nome da env var, para
        # ser achavel de primeira; a ausencia so da subpasta TIPOS_TREINO segue
        # em WARNING.
        if not tipos_treino_dir.parent.is_dir():
            logger.error(
                "rag_chunking: RAG_MARKDOWN_DIR=%s NAO EXISTE neste processo - "
                "todo o conteudo de RAG esta indisponivel (nao apenas as pastas de "
                "tipo de treino, que ficarao vazias). Verifique RAG_MARKDOWN_DIR e o "
                "bind mount (DEV, docker-compose.yml) ou RAG_S3_BUCKET/RAG_S3_PREFIX "
                "(producao, app/rag_storage.py::ensure_rag_local). Erro original: %s",
                tipos_treino_dir.parent,
                exc,
            )
            return []
        logger.warning(
            "rag_chunking: nao foi possivel listar %s para descoberta de pastas de tipo de treino: %s",
            tipos_treino_dir,
            exc,
        )
        return []

    def _sort_key(entry: Path) -> tuple[int, int, str]:
        match = _FOLDER_LEADING_NUMBER_RE.match(entry.name)
        if match:
            return (0, int(match.group(1)), entry.name)
        return (1, 0, entry.name)

    return [entry.name for entry in sorted(entries, key=_sort_key)]


def load_rag_folder_text(folder_name: str) -> str:
    """Le todos os .md de `TIPOS_TREINO_DIR / folder_name` (ordenados por
    nome), concatenados com um cabecalho por arquivo (mesmo estilo de
    `_full_file_fallback`). `folder_name` e validado contra
    `list_training_type_rag_folders()` antes de qualquer acesso a disco —
    protecao contra path traversal (nunca concatenar o valor recebido direto
    num Path sem essa checagem)."""
    ensure_rag_local()
    valid_folders = list_training_type_rag_folders()
    tipos_treino_dir = _tipos_treino_dir()
    if folder_name not in valid_folders:
        raise ValueError(
            f"Pasta de RAG '{folder_name}' nao encontrada em {tipos_treino_dir} "
            f"(pastas validas: {valid_folders})."
        )

    folder_path = tipos_treino_dir / folder_name
    md_files = sorted(folder_path.glob("*.md"), key=lambda p: p.name)

    parts: list[str] = []
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "rag_chunking: nao foi possivel ler %s da pasta de tipo de treino %s: %s",
                md_file.name,
                folder_name,
                exc,
            )
            continue
        parts.append(f"## Arquivo: {md_file.name}\n\n{text}")

    if not parts:
        return f"## Pasta de tipo de treino: {folder_name}\n\n(nenhum arquivo .md encontrado)"

    return "\n\n---\n\n".join(parts)


def _goal_folders(training_goal: Any) -> list[str]:
    """Pastas de RAG próprio dos objetivos pedidos, na ordem do vocabulário.
    `training_goal`: um objetivo ou uma coleção deles (sessão/Modelo híbrido);
    `None`/default = nenhuma pasta."""
    from .domain.training_goal import goal_rag_folder, goals_in

    if training_goal is None:
        return []
    values = training_goal if isinstance(training_goal, (list, tuple, set, frozenset)) else [training_goal]
    folders: list[str] = []
    for goal in goals_in(values):
        folder = goal_rag_folder(goal)
        if folder and folder not in folders:
            folders.append(folder)
    return folders


def goal_rag_files(training_goal: Any) -> list[Path]:
    """`.md` do material próprio dos objetivos (ordem: objetivo, depois nome do
    arquivo). Pasta ausente = lista vazia (degrada como o resto do módulo)."""
    files: list[Path] = []
    for folder in _goal_folders(training_goal):
        folder_path = RAG_MARKDOWN_DIR / GOAL_RAG_DIRNAME / folder
        try:
            files.extend(sorted(folder_path.glob("*.md"), key=lambda p: p.name))
        except OSError as exc:
            logger.warning("rag_chunking: nao foi possivel listar %s: %s", folder_path, exc)
    return files


def select_goal_rag_text(target_groups: list[str], training_goal: Any = None) -> str:
    """Material de RAG PRÓPRIO do objetivo (M97 F5), já com os placeholders
    interpolados dos motores donos (`rag_placeholders.interpolate`) e com o
    mesmo filtro de chunks por grupo muscular da base. Hipertrofia/`None` ->
    `""` (nenhum arquivo, nenhuma leitura). Com teto `MAX_RAG_GOAL_CHARS`."""
    from .rag_placeholders import interpolate

    ensure_rag_local()
    files = goal_rag_files(training_goal)
    if not files:
        return ""
    target_groups = target_groups or []
    parts: list[str] = []
    for file_path in files:
        label = file_path.relative_to(RAG_MARKDOWN_DIR).as_posix()

        def _read(path: Path = file_path) -> str:
            return interpolate(path.read_text(encoding="utf-8"))

        def _loader(read=_read, name: str = label) -> list[RagChunk]:
            return parse_chunks(read(), name)

        parts.append(_select_chunk_file_text(label, _loader, target_groups, read_fn=_read))
    return truncate_rag("\n\n---\n\n".join(parts), MAX_RAG_GOAL_CHARS, "RAG do objetivo")


def rag_base_limit(training_goal: Any = None) -> int:
    """Teto do texto de `select_rag_text`: o da base (`MAX_RAG_BASE_CHARS`)
    mais o do material do objetivo quando o objetivo tem material próprio.
    Hipertrofia/`None` = `MAX_RAG_BASE_CHARS`, o de sempre."""
    return MAX_RAG_BASE_CHARS + (MAX_RAG_GOAL_CHARS if goal_rag_files(training_goal) else 0)


def select_rag_text(
    target_groups: list[str],
    rag_folder: str | None = None,
    has_training_type: bool = False,
    training_goal: Any = None,
) -> str:
    """Monta o texto RAG a injetar no prompt de geracao de ficha, usando so o
    conteudo relevante para `target_groups` (grupos musculares candidatos a
    entrar na ficha, tipicamente READY + REDUCED_DOSE de muscle_recovery):

    - PROGRESSAO.md inteiro (sempre — regra universal de progressao de carga,
      referenciada explicitamente pelo prompt).
    - Chunks gerais + especificos de HIPERTROFIA.md e ANTEBRACO.md
      (FULLBODY.md mora em TIPOS_TREINO/1. Full Body/ e entra por
      `rag_folder` ou pelo fallback recursivo abaixo - IA-16).
    - Linhas relevantes de TABELA_EXERCICIOS.md.
    - BIOIMPEDANCIA.md NUNCA e incluido (sem relacao com selecao de
      exercicio; a interpretacao de bioimpedancia e feita por outro modulo,
      BodyCompositionTrendService).
    - Qualquer outro arquivo .md novo em RAG_MARKDOWN_DIR (fora de
      KNOWN_FILES e EXCLUDED_FILES) e descoberto automaticamente: se tiver
      chunks no formato "## PREFIXO-NNN — titulo", e filtrado por grupo
      muscular como HIPERTROFIA.md/FULLBODY.md/ANTEBRACO.md; senao, entra
      inteiro como PROGRESSAO.md.
    - Se `rag_folder` for informado (nome de subpasta de TIPOS_TREINO_DIR,
      ver `list_training_type_rag_folders()`), o conteudo de
      `load_rag_folder_text(rag_folder)` e ACRESCENTADO ao final do texto
      (sem remover nada do que ja e incluido acima).
    - Senao (rag_folder=None), o comportamento depende de `has_training_type`:
        - `has_training_type=False` (default — fluxo legado sem template,
          `abc_workout_ai.py`/`workout_plan_ai.py`, ou Modelo de Treino cujo
          `training_type_id` e `None`): aplica um fallback recursivo,
          escaneando TODOS os `.md` de `TIPOS_TREINO/**/` (qualquer
          subpasta, qualquer profundidade — ver `discover_training_type_files()`)
          e incluindo cada um (mesmo tratamento seletivo/full-file-fallback
          dos arquivos soltos da raiz). Existe porque alguns arquivos de
          metodologia (ex: TREINO_A.md, TREINO_ABC_PERFEITO.md) moram
          dentro de subpastas de TIPOS_TREINO_DIR em vez de soltos na raiz, e
          o fluxo sem tipo de treino nao tem outra forma de alcancar essas
          pastas.
        - `has_training_type=True` (Modelo de Treino com `training_type_id`
          configurado, mas cujo tipo de treino ainda nao tem `rag_folder`
          definido em `/tipos-treinamento`): NAO aplica esse fallback — a
          geracao fica sem o conteudo extra ate o usuario configurar
          `rag_folder` explicitamente. Evita misturar conteudo de um tipo de
          treino errado (ex: injetar ABC numa ficha de outro tipo) so porque
          a pasta ainda nao foi configurada.
    - `training_goal` (M97 F5): objetivo (ou coleção de objetivos) do
      Modelo/item/sessão. Com material próprio (força), `select_goal_rag_text`
      ABRE o texto: os consumidores cortam pela cauda, com o teto
      `rag_base_limit(training_goal)`. Hipertrofia/`None`: texto byte a byte
      igual ao de antes.
    """
    ensure_rag_local()
    target_groups = target_groups or []

    parts: list[str] = []
    goal_text = select_goal_rag_text(target_groups, training_goal)
    if goal_text:
        parts.append(goal_text)

    try:
        progressao_text = _read_file_text("PROGRESSAO.md")
        parts.append(f"## Arquivo: PROGRESSAO.md\n\n{progressao_text}")
    except OSError as exc:
        logger.warning("rag_chunking: nao foi possivel ler PROGRESSAO.md: %s", exc)

    parts.append(_select_chunk_file_text("HIPERTROFIA.md", load_hipertrofia_chunks, target_groups))
    # FULLBODY.md mora em TIPOS_TREINO/1. Full Body/ e entra por `rag_folder` (ou
    # pelo fallback recursivo de TIPOS_TREINO); nao existe na raiz (IA-16).
    parts.append(_select_chunk_file_text("ANTEBRACO.md", load_antebraco_chunks, target_groups))
    parts.append(_select_table_text(target_groups))

    for filename in discover_additional_files():
        try:
            parts.append(_select_additional_file_text(filename, target_groups))
        except Exception as exc:  # noqa: BLE001 - arquivo novo malformado nao pode derrubar a ficha
            parts.append(_full_file_fallback(filename, f"excecao inesperada ao processar arquivo descoberto: {exc}"))

    if rag_folder:
        parts.append(load_rag_folder_text(rag_folder))
    elif not has_training_type:
        tipos_treino_dir = _tipos_treino_dir()
        for file_path in discover_training_type_files():
            relative_name = file_path.relative_to(tipos_treino_dir).as_posix()
            try:
                parts.append(
                    _select_additional_file_text(
                        file_path.name,
                        target_groups,
                        file_path=file_path,
                        display_name=relative_name,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - arquivo de TIPOS_TREINO malformado nao pode derrubar a ficha
                parts.append(
                    _full_file_fallback(
                        relative_name,
                        f"excecao inesperada ao processar arquivo de TIPOS_TREINO: {exc}",
                        read_fn=lambda p=file_path: p.read_text(encoding="utf-8"),
                    )
                )

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agente de cardio (M98 C3a): indice proprio sobre `CARDIO.md`.
# ---------------------------------------------------------------------------

CARDIO_RAG_FILE = "CARDIO.md"
# Chunks que entram no prompt do agente: relacao com a musculacao, frequencia,
# intensidade (FC/zona), continuo, HIIT, escolha entre os dois e duracao/
# progressao. Fisiologia geral (VO2, mitocondrias), contexto da fonte e
# checklists ficam fora (nao mudam a decisao e custam tokens).
CARDIO_AGENT_CHUNK_IDS: tuple[str, ...] = (
    "CAR-008", "CAR-009", "CAR-012", "CAR-013", "CAR-014", "CAR-016", "CAR-017",
    "CAR-018", "CAR-019", "CAR-021", "CAR-022", "CAR-023", "CAR-024", "CAR-025",
    "CAR-026", "CAR-027", "CAR-028", "CAR-029", "CAR-030", "CAR-031", "CAR-032",
)
# Teto do texto injetado (o recorte acima mede ~9 mil caracteres).
MAX_RAG_CARDIO_CHARS = 14_000
# Periodizacao com cardio (M98 C5): recorte COMPACTO, so o que muda a escolha
# do cardio de um Modelo (cardio como suporte da musculacao, frequencia,
# continuo e a zona dele, estrutura do HIIT, quando favorecer cada um, tempo
# inicial e teto do continuo). Os numeros vem dos placeholders `{{cardio.*}}`.
PERIODIZATION_CARDIO_CHUNK_IDS: tuple[str, ...] = (
    "CAR-009", "CAR-012", "CAR-013", "CAR-017", "CAR-018", "CAR-021",
    "CAR-026", "CAR-027", "CAR-028", "CAR-030",
)
# Teto do recorte da periodizacao (o recorte mede ~2,6 mil caracteres).
MAX_RAG_PERIODIZATION_CARDIO_CHARS = 4_000
_CHUNK_SUBSECTION_RE = re.compile(r"^### (.+)$", re.MULTILINE)
_CHUNK_CONTENT_SECTION = "Conteúdo"


def _cardio_chunk_text(chunk: RagChunk) -> str:
    """Cabecalho do chunk + so a secao "### Conteudo" (tags, aliases e
    "Perguntas que este chunk responde" ficam fora do prompt)."""
    header = chunk.content.split("\n", 1)[0]
    sections = list(_CHUNK_SUBSECTION_RE.finditer(chunk.content))
    for index, match in enumerate(sections):
        if match.group(1).strip() != _CHUNK_CONTENT_SECTION:
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(chunk.content)
        body = chunk.content[match.end():end].strip()
        return f"{header}\n\n{body}"
    return chunk.content


def select_cardio_rag_text(
    chunk_ids: tuple[str, ...] = CARDIO_AGENT_CHUNK_IDS,
    limit: int = MAX_RAG_CARDIO_CHARS,
    label: str = "RAG do agente de cardio",
) -> str:
    """RAG de cardio: os chunks de `chunk_ids` (default: os do agente de
    cardio) de `CARDIO.md`, na ordem do arquivo, com os placeholders
    interpolados dos donos. Arquivo ausente ou sem chunk: `""` e aviso no log
    (quem chama segue sem RAG; nunca injeta o arquivo inteiro). Com teto
    `limit`. A periodizacao (M98 C5) usa `PERIODIZATION_CARDIO_CHUNK_IDS`."""
    from .rag_placeholders import interpolate

    ensure_rag_local()
    try:
        text = interpolate(_read_file_text(CARDIO_RAG_FILE))
        chunks = parse_chunks(text[text.find(CHUNKS_SECTION_MARKER):], CARDIO_RAG_FILE)
    except (OSError, ValueError) as exc:
        logger.warning("rag_chunking: RAG do cardio indisponivel (%s)", exc)
        return ""
    wanted = set(chunk_ids)
    selected = [chunk for chunk in chunks if chunk.id in wanted]
    if not selected:
        logger.warning("rag_chunking: nenhum chunk de cardio (%s) encontrado em %s", label, CARDIO_RAG_FILE)
        return ""
    body = "\n\n---\n\n".join(_cardio_chunk_text(chunk) for chunk in selected)
    return truncate_rag(f"## Arquivo: {CARDIO_RAG_FILE} ({label})\n\n{body}", limit, "RAG do cardio")


def select_periodization_cardio_rag_text() -> str:
    """Recorte compacto de `CARDIO.md` para a periodizacao com cardio (M98 C5)."""
    return select_cardio_rag_text(
        PERIODIZATION_CARDIO_CHUNK_IDS, MAX_RAG_PERIODIZATION_CARDIO_CHARS, "recorte da periodizacao"
    )
