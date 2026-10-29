"""Texto livre do usuario dentro de prompt: delimitador e saneamento (dono unico).

Nasceu na Periodizacao (`periodization/context.py`, instrucoes livres) e passou
a ser compartilhado no IA-10 (avaliacao de serie: anotacao da serie). O texto
digitado pelo usuario entra no prompt como DADO, entre `<dados_do_usuario>` e
`</dados_do_usuario>`, com a regra explicita de que ali dentro nao ha ordens.

O saneamento:
- remove caractere de controle;
- neutraliza qualquer ocorrencia do proprio delimitador (sem isso, um texto
  contendo `</dados_do_usuario>` fecharia o bloco e o resto viraria instrucao
  aos olhos do modelo);
- trunca no teto de cada feature, com marca visivel.

Duas formas de delimitar, o mesmo saneamento:
- `user_data_block`: texto longo e isolado (instrucoes da Periodizacao,
  anotacao da serie), delimitadores em linhas proprias;
- `user_data_inline` / `delimit_user_fields`: texto curto no meio de uma linha
  ou de um JSON (notas de serie, nome de treino/exercicio, notas de sono),
  delimitadores colados ao texto. O conteudo chega INTEIRO (so muda o que o
  saneamento muda); "dor no ombro esquerdo" continua "dor no ombro esquerdo".

`user_data_rule()` e a frase unica que diz ao modelo que o conteudo delimitado
e dado, nao instrucao (texto no repositorio privado, M103 P2). Todo prompt que
usa `user_data_inline` / `delimit_user_fields` interpola essa frase na parte de
instrucoes (o cliente manda um so `input`, sem papel `system`/`developer`).

O prompt e a primeira barreira, nao a unica: a saida de cada feature continua
passando pelos limites do servidor (clamps, validador).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from . import prompt_store
from .request_models import NOTES_MAX

USER_DATA_OPEN = "<dados_do_usuario>"
USER_DATA_CLOSE = "</dados_do_usuario>"

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DELIMITER_RE = re.compile(r"</?\s*dados_do_usuario\s*>", re.IGNORECASE)


def sanitize_user_text(raw: Optional[str], max_chars: int) -> str:
    """Texto do usuario pronto para entrar no prompt como DADO (ver modulo)."""
    if not raw:
        return ""
    text = _CONTROL_CHARS_RE.sub(" ", str(raw))
    text = _DELIMITER_RE.sub("[delimitador removido]", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + " [...truncado]"
    return text


def user_data_block(text: str) -> str:
    """`text` (ja saneado) entre os delimitadores, uma linha cada."""
    return f"{USER_DATA_OPEN}\n{text}\n{USER_DATA_CLOSE}"


# Tetos do saneamento para texto curto, os mesmos da entrada: anotacao =
# `request_models.NOTES_MAX` (o Pydantic ja recusa acima disso) e nome =
# VARCHAR(255) de nome de treino/exercicio. Truncar so pega o que nao passou
# pela API (linha antiga, importacao do relogio).
USER_NAME_MAX_CHARS = 255
USER_NOTES_MAX_CHARS = NOTES_MAX

# Versao do texto da regra (M103 P2): a PROSA mora no repositorio privado,
# `api/privado/prompts/prompt_user_data/<versao>.md` (`prompt_store`); os
# delimitadores entram como valor, daqui. Renderizada sob demanda (nunca no
# import): cada builder interpola `user_data_rule()` nas instrucoes.
USER_DATA_RULE_VERSION = "user-data-v1"
PROMPT_FEATURE_DIR = "prompt_user_data"


def user_data_rule() -> str:
    """A frase unica que diz ao modelo que o conteudo delimitado e DADO."""
    return prompt_store.render(
        PROMPT_FEATURE_DIR, USER_DATA_RULE_VERSION, {"abre": USER_DATA_OPEN, "fecha": USER_DATA_CLOSE}
    )


_LINE_BREAKS_RE = re.compile(r"\s*[\r\n]+\s*")


def user_data_inline(raw: Optional[str], max_chars: int) -> str:
    """`raw` saneado e delimitado na MESMA linha (quebra de linha vira espaco,
    para a nota nao partir a lista em que esta). Vazio quando nao ha texto:
    o chamador decide o que escrever no lugar."""
    text = _LINE_BREAKS_RE.sub(" ", sanitize_user_text(raw, max_chars))
    if not text:
        return ""
    return f"{USER_DATA_OPEN}{text}{USER_DATA_CLOSE}"


def delimit_user_fields(value: Any, fields: Mapping[str, int]) -> Any:
    """Copia de `value` (dict/list aninhados, como vao para `json.dumps`) com
    toda string nao vazia sob uma chave de `fields` trocada por
    `user_data_inline` (teto = valor da chave). O original nao muda: quem
    persiste o contexto (snapshot do cardio, payload da tela) continua com o
    texto cru."""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if key in fields and isinstance(item, str) and item.strip():
                out[key] = user_data_inline(item, fields[key])
            else:
                out[key] = delimit_user_fields(item, fields)
        return out
    if isinstance(value, (list, tuple)):
        return [delimit_user_fields(item, fields) for item in value]
    return value


def delimit_all_strings(value: Any, max_chars: int) -> Any:
    """Copia de `value` com TODA string nao vazia (folha) delimitada. Para
    estrutura montada fora do servidor e sem schema (`proposed_workout` do
    cardio)."""
    if isinstance(value, str):
        return user_data_inline(value, max_chars) if value.strip() else value
    if isinstance(value, Mapping):
        return {key: delimit_all_strings(item, max_chars) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [delimit_all_strings(item, max_chars) for item in value]
    return value


__all__ = [
    "USER_DATA_CLOSE",
    "USER_DATA_OPEN",
    "USER_DATA_RULE_VERSION",
    "USER_NAME_MAX_CHARS",
    "USER_NOTES_MAX_CHARS",
    "delimit_all_strings",
    "delimit_user_fields",
    "sanitize_user_text",
    "user_data_block",
    "user_data_inline",
    "user_data_rule",
]
