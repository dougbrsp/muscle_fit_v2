"""Prompts das FOTOS do card de exercicio (OpenAI Images) - versionado.

Separacao igual a da descricao de execucao (`exercise_description_ai.py` /
`exercise_description_service.py`): ESTE modulo so sabe montar o contexto do
exercicio a partir do catalogo e transformar esse contexto em texto de
prompt (e decidir o que o card mostra do catalogo: `card_title`,
`card_muscles`). Estados, limites, S3, composicao e rotas moram em outros
modulos.

    load_context(cursor, exercise_id) -> ExerciseCardContext | None
    build_scene_prompts(ctx, text_level, *, reference_count=0, reference_notes=(),
                        instruction=None, parent_reject_reason=None) -> ScenePrompts
    card_title(ctx) -> str
    card_muscles(ctx) -> (principais, secundarios)

Estrategia B (decisao do usuario em 2026-10-03): **a IA nunca escreve
texto**. Titulo, rotulos "1 Posicao inicial"/"2 Movimento", musculos e o logo
oficial sao compostos pela APLICACAO (`exercise_card_composer.py`).

V3 (decisao do usuario em 2026-11-06): **duas fotos realistas**, uma por
chamada a OpenAI (`exercise_card_jobs`):

- cena 1 (`ScenePrompts.start`): SO a posicao inicial; vai ao `generations`
  ou, com fotos de referencia do admin, ao `edits` com elas anexadas;
- cena 2 (`ScenePrompts.end`): SO a posicao final; vai ao `edits` com a foto
  da cena 1 como PRIMEIRA imagem (continuidade: mesma pessoa, camera,
  enquadramento, academia e luz) + as fotos de referencia.

Cada prompt descreve so a sua posicao (passos e biomecanica da posicao), pede
foto retrato `SCENE_ASPECT_RATIO` com fundo OPACO (academia) e o corpo +
aparelho inteiros na faixa central `SCENE_SUBJECT_WIDTH_FRACTION` da largura:
o compositor encaixa cada foto num painel retrato (cover centralizado) que
corta as laterais. O bloco de fotos de referencia so entra com
`reference_count > 0`, com a nota que o admin escreveu em cada foto (texto
livre, entre aspas); o feedback do revisor e as notas entram nas DUAS cenas.

`text_level`: so `minimal` existe (o texto do card e da aplicacao). A coluna
continua na tabela e o parametro na API; outros valores respondem 422.

Qualidade dos dados do catalogo (medida no DEV em 2026-10-03) - o prompt so
repassa ao modelo o que ajuda a mostrar certo:

- **Nada de `external_name` do AMAZFIT como "nome em ingles"**: e o tipo de
  exercicio do relogio, nao uma traducao, e muitas vezes outro exercicio.
  `name_en` fica no contexto para uma fonte CURADA futura.
- **Apelidos sao palavras-chave de busca**: ficam fora os cortados, com
  parenteses (marca/modelo), de uma palavra so e os contidos no nome.
- **Equipamentos cadastrados sao ALTERNATIVAS** (marcas/modelos da mesma
  maquina): o prompt pede UM equipamento generico do tipo.
- **Atributos (`body_position` etc.) sao legados e as vezes contradizem o
  nome**: vao como tags secundarias, abaixo de nome, equipamento e passos.
- **Respiracao nao se fotografa**: "Expire durante a puxada" sai dos passos.

ONDE MEXER NO TEXTO (M103 P4): a prosa de cada bloco e um fragmento em
ingles no repositorio privado,
`api/privado/prompts/exercise_card/<PROMPT_TEMPLATE_VERSION>.<parte>.md`;
cada funcao `_block_*` abaixo escolhe os fragmentos, a ordem e os dados
(nome, proporcao e faixa central das constantes deste modulo - nenhum numero
escrito a mao nos templates). Os testes publicos conferem a ESTRUTURA; os que
citam a redacao moram no privado. O sha256 dos prompts e travado por
`tests/unit/test_m103_p4_hash_prompts.py`. Mudou a FORMA das fotos? Suba
`PROMPT_VERSION` e `PROMPT_TEMPLATE_VERSION` (e confira o compositor).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from . import prompt_store

PROMPT_VERSION = "MUSCLEFIT_EXERCISE_CARD_V3_PHOTO"  # <= 40 (prompt_version VARCHAR(40)); identifica o layout V3
# M103 P4: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/exercise_card/<PROMPT_TEMPLATE_VERSION>.<parte>.md`
# (`prompt_store`); aqui ficam os blocos, a ordem, os ramos e os dados. A
# versao dos templates so nomeia os arquivos (a `PROMPT_VERSION` acima, gravada
# no job, nao cabe no formato do nome) e nao entra no texto.
PROMPT_FEATURE_DIR = "exercise_card"
PROMPT_TEMPLATE_VERSION = "exercise-card-v3"
CARD_TYPE = "exercise_execution"  # secao 46 da especificacao; o unico por ora

TEXT_LEVEL_MINIMAL = "minimal"
# Estrategia B: o texto do card e da aplicacao, fixo; `standard`/`full` (V1,
# a IA escrevia passos na imagem) deixaram de existir.
TEXT_LEVELS = (TEXT_LEVEL_MINIMAL,)
DEFAULT_TEXT_LEVEL = TEXT_LEVEL_MINIMAL

# Por cena. Pior caso medido (todos os campos no teto + 3 notas de foto no
# teto + revisao) = 8980 caracteres (cena 2, com a estampa do logo) (`test_pior_caso_cabe_no_limite`); a Images
# API aceita ate 32 mil e as duas cenas juntas cabem em `prompt_text` (TEXT).
MAX_PROMPT_CHARS = 9000

# Forma pedida a cada foto (fonte unica; o prompt interpola daqui).
SCENE_ASPECT_RATIO = "2:3"  # retrato, = OPENAI_IMAGE_SIZE 1024x1536
# Corpo + aparelho dentro da faixa central desta fracao da largura: o painel
# do compositor (cover centralizado) mostra pelo menos isso da largura - teste
# `scene_visible_width_fraction(...) >= SCENE_SUBJECT_WIDTH_FRACTION`.
SCENE_SUBJECT_WIDTH_FRACTION = 0.70
SCENE_START = 1
SCENE_END = 2
# Junta os dois prompts em `exercise_card_generations.prompt_text`.
SCENE_PROMPT_SEPARATOR = "=== SCENE 2 ==="

# Tokens de `frontend/src/theme.ts` (`BRAND`) - um teste compara os dois
# lados. Fonte unica das cores do card: o compositor tambem le daqui.
BRAND_COLORS = {
    "background": "#0A0A0A",
    "panel": "#141414",
    "surface": "#1C1C1C",
    "border": "#2A2A2A",
    "accent": "#FF9500",
    "accent_end": "#E8451C",
    "text_primary": "#F5F5F5",
    "text_secondary": "#E0E0E0",
}

# Tetos que mantem o prompt abaixo de MAX_PROMPT_CHARS no pior caso (provado
# por teste com todos os campos no maximo).
MAX_NAME_CHARS = 80
MAX_SHORT_CHARS = 50
MAX_TAG_CHARS = 40  # atributos do catalogo; o mais longo real tem 38
MAX_ITEM_CHARS = 90  # itens reais da descricao gerada ficam abaixo de ~87
MAX_ALIASES = 2
MAX_EQUIPMENT = 3  # alternativas da mesma maquina; 3 bastam para o tipo
MAX_MUSCLES_PER_ROLE = 3
MAX_MUSCLES_ON_CARD = 4  # principais + secundarios escritos no rodape do card
MAX_REFERENCE_SETUP = 4
MAX_REFERENCE_EXECUTION = 5  # a geracao poe 6, sendo 1-2 de respiracao (fora)
MAX_REFERENCE_ATTENTION = 3
MAX_INSTRUCTION_CHARS = 300
MAX_REJECT_REASON_CHARS = 160

# Blocos (titulos). Os testes localizam os blocos por estes titulos. Ordem em
# cada cena: assunto (paragrafo sem titulo) -> FORMAT -> SCENE ->
# [REFERENCE PHOTOS] -> EXERCISE REFERENCE -> BIOMECHANICS -> PHOTO STYLE ->
# AVOID -> [REVISION] -> FINAL CHECK. O FINAL CHECK repete as regras fixas no
# fim, onde o modelo menos as esquece.
BLOCK_FORMAT = "## FORMAT"
BLOCK_SCENE = "## THIS PHOTO"
BLOCK_REFERENCE_PHOTOS = "## REFERENCE PHOTOS"
BLOCK_REFERENCE = "## EXERCISE REFERENCE"
BLOCK_BIOMECHANICS = "## BIOMECHANICS"
BLOCK_STYLE = "## PHOTO STYLE"
BLOCK_AVOID = "## AVOID"
BLOCK_REVISION = "## REVISION REQUEST"
BLOCK_FINAL_CHECK = "## FINAL CHECK"


class ExerciseCardPromptError(ValueError):
    """Contexto insuficiente ou prompt fora do limite."""


@dataclass(frozen=True)
class ExerciseCardContext:
    """Tudo o que o catalogo sabe do exercicio e o prompt usa. Nada do
    usuario: o card e do EXERCICIO e serve a todos."""

    exercise_id: int
    name: str
    # Nome em ingles CURADO. `load_context` nao preenche: o `external_name`
    # do AMAZFIT nao e traducao (ver docstring do modulo).
    name_en: Optional[str] = None
    aliases: tuple[str, ...] = ()
    primary_muscles: tuple[str, ...] = ()
    secondary_muscles: tuple[str, ...] = ()
    stabilizer_muscles: tuple[str, ...] = ()
    equipment: tuple[str, ...] = ()
    load_conventions: tuple[str, ...] = ()
    body_position: Optional[str] = None
    movement: Optional[str] = None
    trajectory: Optional[str] = None
    biomechanical_pattern: Optional[str] = None
    primary_joint: Optional[str] = None
    resistance_vector: Optional[str] = None
    is_unilateral: bool = False
    is_compound: Optional[bool] = None
    setup_steps: tuple[str, ...] = ()
    execution_steps: tuple[str, ...] = ()
    attention_points: tuple[str, ...] = ()
    # Slugs do mapa corporal (`muscle_group_body_map.slugs`) dos principais e
    # secundarios, na ordem dos musculos e sem repetir. So o compositor usa
    # (silhueta do rodape do card); o prompt nao. Estabilizadores nao acendem.
    primary_slugs: tuple[str, ...] = ()
    secondary_slugs: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Contexto (SQL cru, compativel com os dois backends de `app/db.py`)
# ---------------------------------------------------------------------------


def _as_bool(value: Any) -> bool:
    if isinstance(value, (bytes, bytearray)):
        value = value[:1] == b"\x01" or value.decode("utf-8", "ignore") in ("1", "true")
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true")
    return bool(value)


def _strip_accents_lower(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


_SECTION_RE = re.compile(r"^(preparacao|execucao|atencao)\s*:?\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s*")


def split_execution_description(text: Optional[str]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """`execution_description` -> (preparacao, execucao, atencao).

    Formato gravado por `exercise_description_ai.compose_description`
    (rotulo "Preparação:"/"Execução:"/"Atenção:" em linha propria + itens
    "- ..."), mas o admin edita o texto a mao: rotulo com/sem acento ou
    dois-pontos, item numerado ou sem marcador, e texto sem rotulo nenhum
    (tudo vira execucao) sao aceitos."""
    sections: dict[str, list[str]] = {"preparacao": [], "execucao": [], "atencao": []}
    current = "execucao"
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        header = _SECTION_RE.match(_strip_accents_lower(line))
        if header:
            current = header.group(1)
            continue
        item = _BULLET_RE.sub("", line).strip()
        if item:
            sections[current].append(item)
    return tuple(sections["preparacao"]), tuple(sections["execucao"]), tuple(sections["atencao"])


def _texts(rows: Iterable[dict], field: str) -> tuple[str, ...]:
    seen: list[str] = []
    for row in rows:
        value = str(row.get(field) or "").strip()
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def _slugs(rows: Iterable[dict], field: str) -> tuple[str, ...]:
    """Slugs de `muscle_group_body_map.slugs` ("front-deltoids,back-deltoids")
    de varias linhas, na ordem e sem repetir; NULL (grupo sem mapa) = nenhum."""
    seen: list[str] = []
    for row in rows:
        for slug in str(row.get(field) or "").split(","):
            slug = slug.strip()
            if slug and slug not in seen:
                seen.append(slug)
    return tuple(seen)


def load_context(cursor, exercise_id: int) -> Optional[ExerciseCardContext]:
    """Monta o contexto a partir do catalogo; `None` se o exercicio nao
    existe. `cursor` e o de `app.db.get_conn().cursor()` (PyMySQL DictCursor
    ou Data API): so SELECT com `%s`, um statement por `execute`."""
    eid = int(exercise_id)
    cursor.execute(
        """
        SELECT id, name, body_position, movement, trajectory, biomechanical_pattern, primary_joint,
               resistance_vector, is_unilateral, is_compound, execution_description
        FROM exercise_catalog WHERE id = %s
        """,
        (eid,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    cursor.execute(
        "SELECT alias FROM exercise_aliases WHERE exercise_id = %s ORDER BY alias LIMIT 10",
        (eid,),
    )
    aliases = _texts(cursor.fetchall() or [], "alias")

    # Sem `exercise_external_mappings`: o nome AMAZFIT e o tipo de exercicio
    # do relogio, nao o nome em ingles deste exercicio (docstring do modulo).

    # No MESMO SELECT, os slugs do mapa corporal de cada grupo (LEFT JOIN: grupo
    # sem `muscle_group_body_map` vem com NULL; `muscle_group_id` e UNIQUE la,
    # entao continua uma linha por contribuicao).
    cursor.execute(
        """
        SELECT emc.role, mg.name AS muscle_group_name, mgbm.slugs AS body_map_slugs
        FROM exercise_muscle_contributions emc
        JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
        LEFT JOIN muscle_group_body_map mgbm ON mgbm.muscle_group_id = mg.id
        WHERE emc.exercise_id = %s AND emc.is_active = 1
        ORDER BY FIELD(emc.role, 'PRIMARY', 'SECONDARY', 'STABILIZER'), mg.name
        """,
        (eid,),
    )
    muscles = list(cursor.fetchall() or [])

    cursor.execute(
        """
        SELECT eq.name, eqex.load_convention
        FROM equipment_exercises eqex
        JOIN equipment eq ON eq.id = eqex.equipment_id
        WHERE eqex.exercise_id = %s AND eqex.is_active = 1
        ORDER BY eqex.is_preferred DESC, eq.name
        LIMIT 5
        """,
        (eid,),
    )
    equipment_rows = list(cursor.fetchall() or [])

    def of_role(role: str) -> list[dict]:
        return [m for m in muscles if str(m.get("role") or "").upper() == role]

    def by_role(role: str) -> tuple[str, ...]:
        return _texts(of_role(role), "muscle_group_name")

    setup, execution, attention = split_execution_description(row.get("execution_description"))
    compound = row.get("is_compound")
    return ExerciseCardContext(
        exercise_id=eid,
        name=str(row.get("name") or "").strip(),
        aliases=aliases,
        primary_muscles=by_role("PRIMARY"),
        secondary_muscles=by_role("SECONDARY"),
        stabilizer_muscles=by_role("STABILIZER"),
        equipment=_texts(equipment_rows, "name"),
        load_conventions=_texts(equipment_rows, "load_convention"),
        body_position=row.get("body_position"),
        movement=row.get("movement"),
        trajectory=row.get("trajectory"),
        biomechanical_pattern=row.get("biomechanical_pattern"),
        primary_joint=row.get("primary_joint"),
        resistance_vector=row.get("resistance_vector"),
        is_unilateral=_as_bool(row.get("is_unilateral")),
        is_compound=None if compound is None else _as_bool(compound),
        setup_steps=setup,
        execution_steps=execution,
        attention_points=attention,
        primary_slugs=_slugs(of_role("PRIMARY"), "body_map_slugs"),
        secondary_slugs=_slugs(of_role("SECONDARY"), "body_map_slugs"),
    )


# ---------------------------------------------------------------------------
# Higiene do texto que entra no prompt
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _clean(text: Any, limit: int) -> str:
    """Uma linha, sem aspas duplas (o prompt usa aspas para delimitar o texto
    livre do revisor), cortada em `limit` caracteres."""
    value = _WS_RE.sub(" ", str(text or "")).strip().replace('"', "'")
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


def _clean_list(items: Sequence[Any], limit_items: int, limit_chars: int = MAX_SHORT_CHARS) -> list[str]:
    cleaned = [_clean(item, limit_chars) for item in items]
    return [item for item in cleaned if item][:limit_items]


def _useful_aliases(ctx: ExerciseCardContext) -> list[str]:
    """Apelidos que ajudam a reconhecer o exercicio. `exercise_aliases` e
    sobretudo palavra-chave de busca: descarta os cortados, os com parenteses
    (marca/modelo de maquina), os de uma palavra so (musculo/equipamento
    solto) e os contidos no proprio nome."""
    name = _strip_accents_lower(_clean(ctx.name, MAX_NAME_CHARS))
    kept: list[str] = []
    for raw in ctx.aliases:
        alias = _WS_RE.sub(" ", str(raw or "")).strip()
        norm = _strip_accents_lower(alias)
        if (
            not alias
            or alias.endswith(("...", "…"))
            or "(" in alias
            or len(alias.split()) < 2
            or norm in name
        ):
            continue
        kept.append(alias)
    return _clean_list(kept, MAX_ALIASES)


# ---------------------------------------------------------------------------
# O que o CARD mostra do catalogo - fonte unica para o compositor
# (`exercise_card_composer.content_from_context`)
# ---------------------------------------------------------------------------


def card_title(ctx: ExerciseCardContext) -> str:
    """Nome do exercicio como vai no titulo do card (uma linha, sem aspas,
    no maximo `MAX_NAME_CHARS`). A caixa alta e do compositor."""
    return _clean(ctx.name, MAX_NAME_CHARS)


def _card_muscle(name: Any) -> str:
    """Nome do musculo escrito no card: so o primeiro de sinonimos
    ("Ombros / Deltoides" -> "Ombros")."""
    return str(name or "").split(" / ")[0].strip()


def card_muscles(ctx: ExerciseCardContext) -> tuple[list[str], list[str]]:
    """(principais, secundarios) escritos no rodape do card: so o primeiro
    sinonimo, principais primeiro, no maximo `MAX_MUSCLES_PER_ROLE` por papel
    e `MAX_MUSCLES_ON_CARD` no total, sem repetir um principal entre os
    secundarios. Estabilizadores nunca vao para o card."""
    primary = _clean_list([_card_muscle(m) for m in ctx.primary_muscles], MAX_MUSCLES_PER_ROLE, MAX_TAG_CHARS)
    room = max(0, MAX_MUSCLES_ON_CARD - len(primary))
    secondary = [
        m for m in _clean_list([_card_muscle(m) for m in ctx.secondary_muscles], MAX_MUSCLES_PER_ROLE, MAX_TAG_CHARS)
        if m not in primary
    ][:room]
    return primary, secondary


# Respiracao nao se desenha: "Expire durante a puxada.", "Inspire na volta."
# (a geracao da descricao poe 1-2 por exercicio) so ocuparia o prompt.
_BREATH_RE = re.compile(r"\b(inspire|expire|respire|expulse o ar|solte o ar)\b")


def _drawable(items: Sequence[Any]) -> list[Any]:
    return [item for item in items if not _BREATH_RE.search(_strip_accents_lower(str(item or "")))]


# ---------------------------------------------------------------------------
# Blocos do prompt - a PROSA mora no repositorio privado (M103 P4)
# ---------------------------------------------------------------------------


def _text(part: str, values: Optional[dict[str, str]] = None) -> str:
    """Prosa do template `<PROMPT_TEMPLATE_VERSION><part>` (`prompt_store`)."""
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_TEMPLATE_VERSION}{part}", values or {})


def _percent(fraction: float) -> str:
    """`0.7` -> `70%` (fracao da largura da foto)."""
    return f"{round(fraction * 100)}%"


# Fragmentos por cena: (nome da posicao, cena, biomecanica, passos, fotos).
_SCENE_PARTS = {
    SCENE_START: (".posicao_inicial", ".cena_inicial", ".biomecanica_inicial", ".passos_inicio", ".fotos_referencia"),
    SCENE_END: (".posicao_final", ".cena_final", ".biomecanica_final", ".passos_movimento", ".fotos_referencia_final"),
}


def _position(scene: int) -> str:
    return _text(_SCENE_PARTS[scene][0])


def _block_intro(ctx: ExerciseCardContext, scene: int) -> str:
    return _text(".intro", {"nome": _clean(ctx.name, MAX_NAME_CHARS), "posicao": _position(scene)})


def _block_format() -> str:
    return _text(
        ".formato",
        {
            "titulo": BLOCK_FORMAT,
            "proporcao": SCENE_ASPECT_RATIO,
            "largura_sujeito": _percent(SCENE_SUBJECT_WIDTH_FRACTION),
        },
    )


def _block_scene(scene: int) -> str:
    return _text(_SCENE_PARTS[scene][1], {"titulo": BLOCK_SCENE})


def _max_note_chars() -> int:
    """Teto da nota da foto = o da rota (`exercise_card_references`). Import
    tardio: aquele modulo importa o servico, que importa este (ciclo)."""
    from .exercise_card_references import MAX_NOTE_CHARS

    return MAX_NOTE_CHARS


def _block_reference_photos(scene: int, notes: Sequence[str] = ()) -> str:
    """Bloco das fotos de referencia + uma linha por nota NAO vazia, numerada
    pela posicao da foto (1, 2...) na ordem em que entra em `image[]`. A nota
    e texto livre do admin: vai entre aspas, limpa e cortada (`_clean`)."""
    lines = "".join(
        "\n" + _text(".fotos_referencia_nota", {"numero": str(index), "nota": note})
        for index, note in enumerate(notes, start=1)
        if note
    )
    return _text(_SCENE_PARTS[scene][4], {"titulo": BLOCK_REFERENCE_PHOTOS, "notas": lines})


# Convenções de carga do catálogo que viram dica (a prosa de cada uma é o
# fragmento `.carga_<convenção>`).
_LOAD_HINT_CONVENTIONS = ("bodyweight", "bodyweight_plus", "assisted")


def _equipment_line(ctx: ExerciseCardContext) -> str:
    equipment = _clean_list(ctx.equipment, MAX_EQUIPMENT)
    if not equipment:
        return _text(".equipamento_ausente")
    rule = _text(".equipamento_regra")
    if len(equipment) == 1:
        return _text(".equipamento_um", {"equipamento": equipment[0], "regra": rule})
    # Varios equipamentos = alternativas (marcas/modelos da mesma maquina).
    return _text(".equipamento_alternativas", {"equipamentos": "; ".join(equipment), "regra": rule})


def _block_reference(ctx: ExerciseCardContext, scene: int) -> str:
    """Referencia do exercicio. Os passos sao SO os da posicao da cena:
    preparacao na cena 1, execucao (caminho ate a posicao final) na cena 2;
    os pontos de atencao valem para as duas."""
    # O nome ja abre o prompt (`_block_intro`); aqui so os outros nomes.
    name_en = _clean(ctx.name_en, MAX_SHORT_CHARS)
    other = [_text(".nome_ingles", {"nome": name_en})] if name_en else []
    other += _useful_aliases(ctx)
    lines = [_text(".referencia", {"titulo": BLOCK_REFERENCE})]
    if other:
        lines.append(_text(".outros_nomes", {"nomes": "; ".join(other)}))
    lines.append(_equipment_line(ctx))
    hints = [_text(f".carga_{c}") for c in ctx.load_conventions if c in _LOAD_HINT_CONVENTIONS]
    if hints:
        lines.append(_text(".carga", {"dicas": "; ".join(dict.fromkeys(hints))}))
    if ctx.is_unilateral:
        lines.append(_text(".unilateral"))
    steps_part = _SCENE_PARTS[scene][3]
    steps, steps_limit = (
        (ctx.setup_steps, MAX_REFERENCE_SETUP) if scene == SCENE_START else (ctx.execution_steps, MAX_REFERENCE_EXECUTION)
    )
    for part, items, limit in (
        (steps_part, steps, steps_limit),
        (".passos_forma", ctx.attention_points, MAX_REFERENCE_ATTENTION),
    ):
        cleaned = _clean_list(_drawable(items), limit, MAX_ITEM_CHARS)
        if cleaned:
            lines.append(_text(part, {"passos": " / ".join(cleaned)}))
    # `movement` e `resistance_vector` ficam fora: repetem o padrao e o
    # equipamento ("Empurrar", "Maquina", "Gravidade ou maquina").
    tags = [
        f"{label}: {_clean(value, MAX_TAG_CHARS)}"
        for label, value in (
            ("body position", ctx.body_position),
            ("pattern", ctx.biomechanical_pattern),
            ("trajectory", ctx.trajectory),
            ("main joint", ctx.primary_joint),
        )
        if _clean(value, MAX_TAG_CHARS)
    ]
    if tags:
        lines.append(_text(".tags", {"tags": "; ".join(tags)}))
    return "\n".join(lines)


def _block_biomechanics(scene: int) -> str:
    return _text(_SCENE_PARTS[scene][2], {"titulo": BLOCK_BIOMECHANICS})


def _block_style() -> str:
    return _text(".estilo", {"titulo": BLOCK_STYLE})


def _block_avoid() -> str:
    return _text(".evitar", {"titulo": BLOCK_AVOID})


def _block_revision(instruction: Optional[str], parent_reject_reason: Optional[str]) -> str:
    """Bloco SEPARADO, so quando ha feedback do revisor. O feedback e texto
    livre do admin: vai entre aspas, limpo e cortado. O modelo NAO ve a
    tentativa rejeitada: o feedback vira requisito desta geracao (nas duas
    cenas). Vem antes do FINAL CHECK, que continua valendo sobre ele."""
    lines = [_text(".revisao", {"titulo": BLOCK_REVISION})]
    reason = _clean(parent_reject_reason, MAX_REJECT_REASON_CHARS)
    if reason:
        lines.append(_text(".revisao_motivo", {"motivo": reason}))
    wanted = _clean(instruction, MAX_INSTRUCTION_CHARS)
    if wanted:
        lines.append(_text(".revisao_pedido", {"pedido": wanted}))
    lines.append(_text(".revisao_fim"))
    return "\n".join(lines)


def _block_final_check(scene: int) -> str:
    return _text(".verificacao_final", {"titulo": BLOCK_FINAL_CHECK, "posicao": _position(scene)})


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def normalize_text_level(text_level: Optional[str]) -> str:
    level = (text_level or DEFAULT_TEXT_LEVEL).strip().lower()
    if level not in TEXT_LEVELS:
        raise ExerciseCardPromptError(f"text_level invalido: {text_level!r} (use {', '.join(TEXT_LEVELS)}).")
    return level


@dataclass(frozen=True)
class ScenePrompts:
    """Os dois prompts de uma geracao V3 (cena 1 e cena 2)."""

    start: str
    end: str

    def joined(self) -> str:
        """Texto unico gravado em `exercise_card_generations.prompt_text`."""
        return f"{self.start}\n\n{SCENE_PROMPT_SEPARATOR}\n\n{self.end}"


def _scene_prompt(
    ctx: ExerciseCardContext,
    scene: int,
    reference_count: int,
    notes: Sequence[str],
    instruction: Optional[str],
    parent_reject_reason: Optional[str],
) -> str:
    blocks = [_block_intro(ctx, scene), _block_format(), _block_scene(scene)]
    if reference_count > 0:
        blocks.append(_block_reference_photos(scene, notes))
    blocks += [_block_reference(ctx, scene), _block_biomechanics(scene), _block_style(), _block_avoid()]
    if _clean(instruction, MAX_INSTRUCTION_CHARS) or _clean(parent_reject_reason, MAX_REJECT_REASON_CHARS):
        blocks.append(_block_revision(instruction, parent_reject_reason))
    blocks.append(_block_final_check(scene))
    prompt = "\n\n".join(blocks)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ExerciseCardPromptError(
            f"Prompt da cena {scene} com {len(prompt)} caracteres, acima de {MAX_PROMPT_CHARS}."
        )
    return prompt


def build_scene_prompts(
    ctx: ExerciseCardContext,
    text_level: Optional[str] = DEFAULT_TEXT_LEVEL,
    *,
    reference_count: int = 0,
    reference_notes: Sequence[Optional[str]] = (),
    instruction: Optional[str] = None,
    parent_reject_reason: Optional[str] = None,
) -> ScenePrompts:
    """Prompts da cena 1 (posicao inicial) e da cena 2 (posicao final).

    `reference_count` = fotos de referencia do admin que o worker anexa (o
    bloco REFERENCE PHOTOS so entra se > 0). `reference_notes` = a nota de
    cada foto, na MESMA ordem de `image[]` (`None`/vazia = sem nota); quando
    informadas, uma por foto. As notas nao vazias entram, limpas e cortadas em
    `exercise_card_references.MAX_NOTE_CHARS`, no bloco de fotos das DUAS
    cenas. Erro se o exercicio nao tem nome, se o nivel nao e `minimal`, se
    `reference_count < 0`, se ha notas e o numero delas difere de
    `reference_count`, ou se uma cena passa de `MAX_PROMPT_CHARS`.
    `text_level` continua na assinatura (coluna e API existentes) - so
    `minimal` e aceito."""
    if not _clean(ctx.name, MAX_NAME_CHARS):
        raise ExerciseCardPromptError("Exercicio sem nome: nao ha o que fotografar.")
    normalize_text_level(text_level)
    count = int(reference_count)
    if count < 0:
        raise ExerciseCardPromptError(f"reference_count invalido: {reference_count!r}.")
    raw_notes = list(reference_notes or ())
    if raw_notes and len(raw_notes) != count:
        raise ExerciseCardPromptError(
            f"reference_notes com {len(raw_notes)} entrada(s) para {count} foto(s) de referencia."
        )
    limit = _max_note_chars()
    notes = [_clean(note, limit) for note in raw_notes]
    return ScenePrompts(
        start=_scene_prompt(ctx, SCENE_START, count, notes, instruction, parent_reject_reason),
        end=_scene_prompt(ctx, SCENE_END, count, notes, instruction, parent_reject_reason),
    )
