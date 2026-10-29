"""Descricao de EXECUCAO de um exercicio do catalogo, gerada pela OpenAI
UMA VEZ e persistida em `exercise_catalog.execution_description` (migracao
039).

Por que persistir em vez de gerar a cada abertura da tela de briefing
(decisao do usuario e do Tech Lead, 18/09):

- custo: sao ~centenas de exercicios no catalogo contra N execucoes por dia
  de cada um; gerar na abertura multiplicaria a conta por cada treino;
- latencia: a tela de briefing abre ANTES da primeira serie, no ginasio, com
  rede ruim - ela nao pode esperar uma chamada de LLM;
- consistencia: o mesmo exercicio precisa ser descrito do mesmo jeito hoje e
  daqui a um mes; texto regerado a cada abertura muda sozinho.

O texto e revisavel/editavel pelo admin em `/dominio-exercicios`
(`execution_description_reviewed_by` / `_reviewed_at`). Uma edicao humana
NUNCA e sobrescrita por uma geracao em lote sem `force` explicito - ver
`app/exercise_description_service.py`.

Padrao arquitetural: usa `app/openai_client.call_openai_structured` (saida
estruturada com JSON Schema `strict`), como o Motor de Periodizacao, em vez
de um POST urllib proprio. Prompt VERSIONADO (`EXEC_DESC_PROMPT_VERSION`):
a versao e gravada junto com o texto, para que uma geracao em lote futura
consiga achar o que saiu de um prompt antigo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from . import prompt_store
from .openai_client import OpenAIGenerationError, call_openai_structured

OPENAI_MODEL = os.environ.get("OPENAI_EXERCISE_DESCRIPTION_MODEL", "gpt-5.4-mini")

# Versao do PAR prompt+schema. Subir sempre que o texto pedido mudar de forma
# (nao a cada ajuste de pontuacao): e o que permite regerar seletivamente.
EXEC_DESC_PROMPT_VERSION = "exec-desc-v1"
# Pasta do template em `api/privado/prompts/` (M103 P1).
PROMPT_FEATURE_DIR = "exercise_description"

# Limites do texto final gravado. Servem a UM proposito concreto: a tela de
# briefing e um celular na mao, antes da serie - o usuario le em pe, entre o
# equipamento e o espelho. Texto longo demais nao e lido.
MAX_SETUP_STEPS = 4
MAX_EXECUTION_STEPS = 6
MAX_CUES = 3
MAX_DESCRIPTION_CHARS = 1800

SCHEMA_NAME = "exercise_execution_description"

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["setup", "execution", "cues"],
    "properties": {
        "setup": {
            "type": "array",
            "description": "Como se posicionar/ajustar o equipamento antes da primeira repeticao.",
            "items": {"type": "string"},
        },
        "execution": {
            "type": "array",
            "description": "Como executar uma repeticao, do inicio ao fim, em ordem.",
            "items": {"type": "string"},
        },
        "cues": {
            "type": "array",
            "description": "Erros comuns a evitar / pontos de atencao curtos.",
            "items": {"type": "string"},
        },
    },
}


class ExerciseDescriptionError(OpenAIGenerationError):
    """Falha ao gerar ou validar a descricao de execucao de um exercicio."""


@dataclass(frozen=True)
class GeneratedDescription:
    """O que a geracao devolve para ser persistido, com a auditoria junto."""

    text: str
    model: str
    prompt_version: str
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _muscles_line(muscles: Sequence[Mapping[str, Any]]) -> str:
    if not muscles:
        return prompt_store.render(PROMPT_FEATURE_DIR, f"{EXEC_DESC_PROMPT_VERSION}.sem_musculos", {})
    by_role: dict[str, list[str]] = {}
    for item in muscles:
        role = str(item.get("role") or "PRIMARY").upper()
        name = str(item.get("muscle_group_name") or "").strip()
        if name:
            by_role.setdefault(role, []).append(name)
    partes = [
        f"{rotulo}: {', '.join(by_role[role])}"
        for role, rotulo in (("PRIMARY", "primarios"), ("SECONDARY", "secundarios"), ("STABILIZER", "estabilizadores"))
        if by_role.get(role)
    ]
    return prompt_store.render(
        PROMPT_FEATURE_DIR,
        f"{EXEC_DESC_PROMPT_VERSION}.musculos",
        {"musculos": "; ".join(partes) if partes else "nao mapeados"},
    )


def _attribute_lines(exercise: Mapping[str, Any]) -> str:
    rotulos = (
        ("body_position", "Posicao do corpo"),
        ("movement", "Movimento"),
        ("trajectory", "Trajetoria"),
        ("biomechanical_pattern", "Padrao biomecanico"),
        ("primary_joint", "Articulacao principal"),
        ("resistance_vector", "Vetor de resistencia"),
    )
    linhas = [f"- {rotulo}: {exercise[campo]}" for campo, rotulo in rotulos if exercise.get(campo)]
    if exercise.get("is_unilateral"):
        linhas.append("- Unilateral: sim (um lado de cada vez)")
    if exercise.get("is_compound") is not None:
        linhas.append(f"- Composto: {'sim' if exercise.get('is_compound') else 'nao (isolador)'}")
    return "\n".join(linhas) if linhas else "- (sem atributos classificados no catalogo)"


def build_prompt(exercise: Mapping[str, Any], muscles: Sequence[Mapping[str, Any]] = ()) -> str:
    """Prompt versionado (`EXEC_DESC_PROMPT_VERSION`). A prosa mora em
    `api/privado/prompts/exercise_description/` (`prompt_store`, M103 P1).

    Recebe SO o que o catalogo sabe do exercicio: nada do usuario, nada do
    historico dele. O texto gerado e do EXERCICIO, nao da pessoa - por isso
    pode ser gerado uma vez e servir a todos os usuarios.
    """
    equipamentos = [str(name).strip() for name in (exercise.get("equipment_names") or []) if str(name).strip()]
    linha_equip = (
        prompt_store.render(
            PROMPT_FEATURE_DIR, f"{EXEC_DESC_PROMPT_VERSION}.equipamentos", {"equipamentos": ", ".join(equipamentos)}
        )
        if equipamentos
        else prompt_store.render(PROMPT_FEATURE_DIR, f"{EXEC_DESC_PROMPT_VERSION}.sem_equipamentos", {})
    )
    return prompt_store.render(
        PROMPT_FEATURE_DIR,
        EXEC_DESC_PROMPT_VERSION,
        {
            "exercicio": f"{exercise.get('name')}",
            "linha_equipamentos": linha_equip,
            "linha_musculos": _muscles_line(muscles),
            "atributos": _attribute_lines(exercise),
            "setup_max": f"{MAX_SETUP_STEPS}",
            "execucao_max": f"{MAX_EXECUTION_STEPS}",
            "cues_max": f"{MAX_CUES}",
        },
    )


# ---------------------------------------------------------------------------
# Validacao + composicao do texto final
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _clean_items(raw: Any, *, field: str, maximum: int, required: bool) -> list[str]:
    if raw is None:
        items: list[Any] = []
    elif not isinstance(raw, list):
        raise ExerciseDescriptionError(f"Campo '{field}' da descricao nao e uma lista.")
    else:
        items = raw
    limpos: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ExerciseDescriptionError(f"Item nao textual em '{field}' da descricao.")
        texto = _WHITESPACE_RE.sub(" ", item).strip().lstrip("-•*").strip()
        if texto:
            limpos.append(texto)
    if required and not limpos:
        raise ExerciseDescriptionError(f"Descricao gerada sem nenhum item em '{field}'.")
    return limpos[:maximum]


def compose_description(data: Mapping[str, Any]) -> str:
    """Valida a saida estruturada e compoe o TEXTO que vai para a coluna.

    Formato deliberadamente simples (rotulo em linha propria + uma frase por
    linha, prefixada por `- `): a coluna e TEXT, nao JSON, e a tela renderiza
    linha a linha. Sem markdown - nada de parser no frontend.
    """
    setup = _clean_items(data.get("setup"), field="setup", maximum=MAX_SETUP_STEPS, required=False)
    execution = _clean_items(data.get("execution"), field="execution", maximum=MAX_EXECUTION_STEPS, required=True)
    cues = _clean_items(data.get("cues"), field="cues", maximum=MAX_CUES, required=False)

    blocos: list[str] = []
    if setup:
        blocos.append("Preparação:\n" + "\n".join(f"- {item}" for item in setup))
    blocos.append("Execução:\n" + "\n".join(f"- {item}" for item in execution))
    if cues:
        blocos.append("Atenção:\n" + "\n".join(f"- {item}" for item in cues))

    texto = "\n\n".join(blocos)
    if len(texto) > MAX_DESCRIPTION_CHARS:
        raise ExerciseDescriptionError(
            f"Descricao gerada com {len(texto)} caracteres, acima do limite de {MAX_DESCRIPTION_CHARS}."
        )
    return texto


def generate_execution_description(
    exercise: Mapping[str, Any],
    muscles: Sequence[Mapping[str, Any]] = (),
    *,
    model: Optional[str] = None,
    timeout: int = 60,
) -> GeneratedDescription:
    """Gera a descricao de execucao de UM exercicio. Nao escreve no banco -
    quem persiste e `app/exercise_description_service.py`."""
    if not str(exercise.get("name") or "").strip():
        raise ExerciseDescriptionError("Exercicio sem nome: nao ha o que descrever.")
    chosen = model or OPENAI_MODEL
    result = call_openai_structured(
        build_prompt(exercise, muscles),
        chosen,
        OUTPUT_SCHEMA,
        schema_name=SCHEMA_NAME,
        timeout=timeout,
        feature="exercise_description",
        prompt_version=EXEC_DESC_PROMPT_VERSION,
    )
    return GeneratedDescription(
        text=compose_description(result.data),
        model=result.model or chosen,
        prompt_version=EXEC_DESC_PROMPT_VERSION,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
