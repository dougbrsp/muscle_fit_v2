"""Prompt da ILUSTRACAO do card de exercicio (OpenAI Images) - versionado.

Separacao igual a da descricao de execucao (`exercise_description_ai.py` /
`exercise_description_service.py`): ESTE modulo so sabe montar o contexto do
exercicio a partir do catalogo e transformar esse contexto em texto de
prompt (e decidir o que o card mostra do catalogo: `card_title`,
`card_muscles`). Estados, limites, S3, composicao e rotas moram em outros
modulos.

    load_context(cursor, exercise_id) -> ExerciseCardContext | None
    build_prompt(ctx, text_level, instruction=None, parent_reject_reason=None) -> str
    card_title(ctx) -> str
    card_muscles(ctx) -> (principais, secundarios)

Estrategia B (decisao do usuario em 2026-10-03, depois do checkpoint rodada 1
- Anexo D do plano dos cards): **a IA gera SO a ilustracao**. Nada de texto,
numero, rotulo, logo, moldura, painel ou silhueta anatomica na imagem: na
rodada 1 o modelo ignorou a area reservada ao logo em 4/4 cards e errou acento
em portugues em 4/4. Titulo, rotulos "1 Posicao inicial"/"2 Movimento",
musculos e o logo oficial sao compostos pela APLICACAO
(`exercise_card_composer.py`) logo depois da OpenAI, com fontes versionadas.

O que a ilustracao precisa entregar ao compositor:

- **duas cenas empilhadas** (inicio em cima, movimento embaixo) com uma faixa
  VAZIA entre elas perto da metade da altura - o compositor corta na linha
  mais vazia entre 40 % e 60 % da altura e encaixa cada cena no seu painel;
- **fundo transparente** (`background="transparent"` no pedido, ver
  `exercise_card_jobs.ILLUSTRATION_BACKGROUND`) - com fundo opaco o compositor
  recorta por distancia de cor, pior nas bordas;
- **uma seta laranja** (#FF9500) so na cena 2, unico elemento grafico.

`text_level`: so `minimal` existe nesta versao (o texto do card e fixo e
escrito pela aplicacao). A coluna continua na tabela e o parametro na API;
outros valores respondem 422 `invalid_text_level`.

Qualidade dos dados do catalogo (medida no DEV em 2026-10-03, revisao do
ai_engineer) - o prompt so repassa ao modelo o que ajuda a desenhar certo:

- **Nada de `external_name` do AMAZFIT como "nome em ingles"**: e o tipo de
  exercicio do relogio, nao uma traducao, e muitas vezes outro exercicio
  ("Leg press 45°" -> "Hack Squat", "Supino reto com barra" -> "Supino com
  halteres", "Stiff no Smith" -> "Dumbell Romanian Deadlift") ou so um grupo
  muscular ("Músculos traseiros do antebraço"). `load_context` nao consulta
  mais o mapeamento; `name_en` fica no contexto para uma fonte CURADA futura
  e, se vier preenchido, o prompt usa.
- **Apelidos sao palavras-chave de busca**: ficam fora os cortados
  ("braqui..."), com parenteses (marca/modelo), de uma palavra so ("barra",
  "corda", "quadriceps") e os contidos no proprio nome.
- **Equipamentos cadastrados sao ALTERNATIVAS** (marcas/modelos da mesma
  maquina), nao um conjunto: o prompt pede UM equipamento generico do tipo.
- **Atributos (`body_position` etc.) sao legados e as vezes contradizem o
  nome** (id 1 "Supino reto na maquina" = "Deitado"; id 25 "... sentada ..."
  = "Em pe"): vao como tags secundarias, abaixo de nome, equipamento e passos.
- **Respiracao nao se desenha**: "Expire durante a puxada" sai dos passos.

Consistencia da colecao: `_block_style` fixa estilo de ilustracao, luz e o
MESMO atleta (roupa neutra de treino, rosto simplificado, ninguem real) em
todas as imagens; `_block_final_check` repete no fim as regras que o modelo
mais esquece (zero texto, sem logo/moldura, fundo transparente, duas cenas
separadas) e vale inclusive sobre o pedido de revisao do admin.

ONDE MEXER NO TEXTO (M103 P4): a prosa de cada bloco e um fragmento em
ingles (modelos de imagem seguem melhor instrucoes em ingles) no repositorio
privado, `api/privado/prompts/exercise_card/<PROMPT_TEMPLATE_VERSION>.<parte>.md`;
cada funcao `_block_*` abaixo escolhe os fragmentos, a ordem e os dados (nome,
cores de `BRAND_COLORS`, forma da ilustracao das constantes deste modulo).
Nenhuma string e para ser escrita na imagem. Os testes publicos conferem a
ESTRUTURA (blocos, ordem, cores, ausencia de strings para renderizar,
limites); os que citam a redacao moram no privado. O sha256 do prompt e
travado por `tests/unit/test_m103_p4_hash_prompts.py`. Mudou a FORMA da
ilustracao (o que aparece, onde)? Suba `PROMPT_VERSION` e
`PROMPT_TEMPLATE_VERSION` (e confira se o compositor ainda extrai as cenas).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from . import prompt_store

PROMPT_VERSION = "MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION"
# M103 P4: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/exercise_card/<PROMPT_TEMPLATE_VERSION>.<parte>.md`
# (`prompt_store`); aqui ficam os blocos, a ordem, os ramos e os dados. A
# versao dos templates so nomeia os arquivos (a `PROMPT_VERSION` acima, gravada
# no job, nao cabe no formato do nome) e nao entra no texto.
PROMPT_FEATURE_DIR = "exercise_card"
PROMPT_TEMPLATE_VERSION = "exercise-card-v2"
CARD_TYPE = "exercise_execution"  # secao 46 da especificacao; o unico por ora

TEXT_LEVEL_MINIMAL = "minimal"
# Estrategia B: o texto do card e da aplicacao, fixo; `standard`/`full` (V1,
# a IA escrevia passos na imagem) deixaram de existir.
TEXT_LEVELS = (TEXT_LEVEL_MINIMAL,)
DEFAULT_TEXT_LEVEL = TEXT_LEVEL_MINIMAL

MAX_PROMPT_CHARS = 8000

# Forma pedida a ilustracao (fonte unica; o prompt interpola daqui e o
# compositor le `SCENE_GAP_RANGE` para achar o corte entre as cenas).
ILLUSTRATION_ASPECT_RATIO = "2:3"  # retrato; o card final e 1024x1536
SCENE_GAP_RANGE = (0.40, 0.60)  # faixa da altura com o vao vazio entre as cenas
SCENE_EDGE_MARGIN = 0.05  # margem vazia de cada cena ate a borda da imagem

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

# Blocos (titulos). Os testes localizam os blocos por estes titulos. Ordem no
# prompt: assunto (paragrafo sem titulo) -> FORMAT -> SCENES -> REFERENCE ->
# BIOMECHANICS -> STYLE -> AVOID -> [REVISION] -> FINAL CHECK. O FINAL CHECK
# repete as regras fixas no fim, onde o modelo menos as esquece.
BLOCK_FORMAT = "## FORMAT"
BLOCK_SCENES = "## SCENES"
BLOCK_REFERENCE = "## EXERCISE REFERENCE"
BLOCK_BIOMECHANICS = "## BIOMECHANICS"
BLOCK_STYLE = "## VISUAL STYLE"
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
    """`0.4` -> `40%` (fração da altura/largura da ilustração)."""
    return f"{round(fraction * 100)}%"


def _block_intro(ctx: ExerciseCardContext) -> str:
    return _text(".intro", {"nome": _clean(ctx.name, MAX_NAME_CHARS)})


def _block_format() -> str:
    return _text(
        ".formato",
        {
            "titulo": BLOCK_FORMAT,
            "proporcao": ILLUSTRATION_ASPECT_RATIO,
            "vao_min": _percent(SCENE_GAP_RANGE[0]),
            "vao_max": _percent(SCENE_GAP_RANGE[1]),
            "margem": _percent(SCENE_EDGE_MARGIN),
        },
    )


def _block_scenes() -> str:
    return _text(".cenas", {"titulo": BLOCK_SCENES, "cor_seta": BRAND_COLORS["accent"]})


# Convenções de carga do catálogo que viram dica de desenho (a prosa de cada
# uma é o fragmento `.carga_<convenção>`).
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


def _block_reference(ctx: ExerciseCardContext) -> str:
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
    for part, items, limit in (
        (".passos_inicio", ctx.setup_steps, MAX_REFERENCE_SETUP),
        (".passos_movimento", ctx.execution_steps, MAX_REFERENCE_EXECUTION),
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


def _block_biomechanics() -> str:
    return _text(".biomecanica", {"titulo": BLOCK_BIOMECHANICS})


def _block_style() -> str:
    c = BRAND_COLORS
    return _text(".estilo", {"titulo": BLOCK_STYLE, "cor_luz": c["accent"], "cor_painel": c["panel"]})


def _block_avoid() -> str:
    return _text(".evitar", {"titulo": BLOCK_AVOID})


def _block_revision(instruction: Optional[str], parent_reject_reason: Optional[str]) -> str:
    """Bloco SEPARADO, so quando ha feedback do revisor. O feedback e texto
    livre do admin: vai entre aspas, limpo e cortado. O modelo NAO ve a
    tentativa anterior (geracao nova, sem imagem de referencia): o feedback
    vira requisito desta geracao. Vem antes do FINAL CHECK, que continua
    valendo sobre ele."""
    lines = [_text(".revisao", {"titulo": BLOCK_REVISION})]
    reason = _clean(parent_reject_reason, MAX_REJECT_REASON_CHARS)
    if reason:
        lines.append(_text(".revisao_motivo", {"motivo": reason}))
    wanted = _clean(instruction, MAX_INSTRUCTION_CHARS)
    if wanted:
        lines.append(_text(".revisao_pedido", {"pedido": wanted}))
    lines.append(_text(".revisao_fim"))
    return "\n".join(lines)


def _block_final_check() -> str:
    return _text(".verificacao_final", {"titulo": BLOCK_FINAL_CHECK})


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def normalize_text_level(text_level: Optional[str]) -> str:
    level = (text_level or DEFAULT_TEXT_LEVEL).strip().lower()
    if level not in TEXT_LEVELS:
        raise ExerciseCardPromptError(f"text_level invalido: {text_level!r} (use {', '.join(TEXT_LEVELS)}).")
    return level


def build_prompt(
    ctx: ExerciseCardContext,
    text_level: Optional[str] = DEFAULT_TEXT_LEVEL,
    instruction: Optional[str] = None,
    parent_reject_reason: Optional[str] = None,
) -> str:
    """Prompt final (`PROMPT_VERSION`). Erro se o exercicio nao tem nome, se
    o nivel nao e `minimal` ou se o texto passa de `MAX_PROMPT_CHARS`.
    `text_level` continua na assinatura (coluna e API existentes) - so
    `minimal` e aceito."""
    if not _clean(ctx.name, MAX_NAME_CHARS):
        raise ExerciseCardPromptError("Exercicio sem nome: nao ha o que ilustrar.")
    normalize_text_level(text_level)
    blocks = [
        _block_intro(ctx),
        _block_format(),
        _block_scenes(),
        _block_reference(ctx),
        _block_biomechanics(),
        _block_style(),
        _block_avoid(),
    ]
    if _clean(instruction, MAX_INSTRUCTION_CHARS) or _clean(parent_reject_reason, MAX_REJECT_REASON_CHARS):
        blocks.append(_block_revision(instruction, parent_reject_reason))
    blocks.append(_block_final_check())
    prompt = "\n\n".join(blocks)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ExerciseCardPromptError(f"Prompt com {len(prompt)} caracteres, acima de {MAX_PROMPT_CHARS}.")
    return prompt
