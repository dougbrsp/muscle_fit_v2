"""Templates de prompt do repositório privado (M103 P1).

A PROSA dos prompts de sistema mora no submódulo `api/privado`
(`muscle_fit_privado`), em `prompts/<feature>/<nome>.md`; o builder de cada
feature, no código público, decide a ordem, os ramos, os dados (JSON) e a
`PROMPT_VERSION`, e chama `render` com a tabela de valores da feature.

- `<nome>` é a `PROMPT_VERSION` da feature (`cardio-agent-v5.md`); a prosa de um
  ramo do builder é um fragmento `<versão>.<parte>.md`
  (`exec-desc-v1.sem_equipamentos.md`).
- Placeholders no formato de `rag_placeholders` (`{{nome}}`). Regra dos motores
  (mapa `docs/motores/FONTES_DA_VERDADE.md`, princípio 4): nenhum número de
  domínio escrito à mão no template; o valor vem da tabela da feature, que lê o
  motor dono. A guarda `test_guarda_fontes_da_verdade.py` varre os templates.
- Convenção do arquivo: UTF-8, LF; a quebra de linha final do arquivo não faz
  parte do prompt (é removida uma vez na leitura).

Diferente do RAG (`rag_placeholders.interpolate` sem `strict`, que deixa o
placeholder no texto e só loga), aqui tudo FALHA ALTO: template ausente,
placeholder sem valor ou valor sem placeholder levantam `PromptTemplateError`.
Prompt pela metade não sai para a OpenAI.

Onde ficam os arquivos: `MUSCLEFIT_PROMPTS_DIR`, se definida; senão
`<pacote>/../privado/prompts` (`api/privado/prompts` no repositório, e
`/app/privado/prompts` ou `/var/task/privado/prompts` nas imagens, onde os
`api/Dockerfile*` copiam SÓ essa pasta do submódulo).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from . import rag_placeholders

PROMPTS_DIR_ENV = "MUSCLEFIT_PROMPTS_DIR"
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "privado" / "prompts"
TEMPLATE_SUFFIX = ".md"

# Só nomes simples: nada de `..` nem separador vindo de quem chama.
_FEATURE_RE = re.compile(r"[a-z0-9_]+")
_NAME_RE = re.compile(r"[a-z0-9_][a-z0-9_.-]*")


class PromptTemplateError(RuntimeError):
    """Template de prompt inutilizável (ausente, ilegível ou incompleto)."""


class PromptTemplateNotFound(PromptTemplateError):
    """O arquivo do template não existe (submódulo `api/privado` ausente?)."""


class PromptPlaceholderError(PromptTemplateError):
    """Placeholder sem valor, ou valor sem placeholder no template."""


def prompts_dir() -> Path:
    configured = (os.environ.get(PROMPTS_DIR_ENV) or "").strip()
    return Path(configured) if configured else DEFAULT_PROMPTS_DIR


def template_path(feature: str, name: str) -> Path:
    if not _FEATURE_RE.fullmatch(feature or ""):
        raise PromptTemplateError(f"feature de prompt inválida: {feature!r}")
    if not _NAME_RE.fullmatch(name or "") or ".." in name:
        raise PromptTemplateError(f"nome de prompt inválido: {name!r}")
    return prompts_dir() / feature / f"{name}{TEMPLATE_SUFFIX}"


@lru_cache(maxsize=None)
def _read(path: str) -> str:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise PromptTemplateNotFound(
            f"template de prompt ausente: {path} (o submódulo api/privado foi inicializado? "
            f"`git submodule update --init api/privado`; ou defina {PROMPTS_DIR_ENV})"
        ) from exc
    except OSError as exc:
        raise PromptTemplateError(f"template de prompt ilegível: {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptTemplateError(f"template de prompt fora de UTF-8: {path}") from exc
    text = text.replace("\r\n", "\n")
    return text[:-1] if text.endswith("\n") else text


def load(feature: str, name: str) -> str:
    """Texto do template (com os `{{placeholders}}`), lido uma vez por processo."""
    return _read(str(template_path(feature, name)))


def render(feature: str, name: str, values: Mapping[str, str]) -> str:
    """Template com cada `{{nome}}` trocado por `values[nome]` (texto pronto da
    feature). Substituição em uma passada: um valor que contenha `{{...}}`
    (texto do usuário) não é reinterpretado."""
    text = load(feature, name)
    wrong_type = sorted(key for key, value in values.items() if not isinstance(value, str))
    if wrong_type:
        raise PromptPlaceholderError(f"{feature}/{name}: valores não textuais para {', '.join(wrong_type)}")
    unused = sorted(set(values) - set(rag_placeholders.placeholders_in(text)))
    if unused:
        raise PromptPlaceholderError(f"{feature}/{name}: valores sem placeholder no template: {', '.join(unused)}")
    try:
        return rag_placeholders.interpolate(text, strict=True, values=values)
    except rag_placeholders.UnknownPlaceholder as exc:
        raise PromptPlaceholderError(f"{feature}/{name}: placeholder(s) sem valor: {exc.args[0]}") from exc


def clear_cache() -> None:
    _read.cache_clear()


__all__ = [
    "DEFAULT_PROMPTS_DIR",
    "PROMPTS_DIR_ENV",
    "PromptPlaceholderError",
    "PromptTemplateError",
    "PromptTemplateNotFound",
    "clear_cache",
    "load",
    "prompts_dir",
    "render",
    "template_path",
]
