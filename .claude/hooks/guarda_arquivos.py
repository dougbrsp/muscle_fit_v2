"""Hook PreToolUse (Edit|Write|MultiEdit|NotebookEdit): arquivos que nao se editam a mao.

Bloqueia (exit 2 + mensagem em stderr):
  - `api/requirements*.txt`: lock gerado pelo pip-compile (com hashes). Edita-se o
    `.in` correspondente e recompila.
  - `frontend/package-lock.json`: o npm do Windows remove as entradas `@emnapi` e
    quebra o `npm ci` do CI; o lock vem de `npm install` e e revisado no diff.
  - qualquer caminho dentro de `C:\\openclaw-dev-agent` (projeto de origem congelado).

Qualquer erro interno => exit 0 (bug do hook nunca trava o trabalho).
"""

from __future__ import annotations

import json
import re
import sys

MSG_REQUIREMENTS = (
    "BLOQUEADO: {arq} e gerado pelo pip-compile (lock com hashes, M47) - nao se edita a mao.\n"
    "Forma correta: edite o `.in` correspondente (ex.: api/requirements.in) e recompile com o "
    "script `sh api/scripts/lock-requirements.sh` (o mesmo citado no cabecalho do .txt); o .txt "
    "entra no PR como saida da compilacao."
)
MSG_LOCKFILE = (
    "BLOQUEADO: frontend/package-lock.json nao se edita a mao.\n"
    "O npm do Windows remove as entradas `@emnapi` e quebra o `npm ci` do CI. Dependencia nova: "
    "`npm install <pacote>` e revisar o diff do lock (as entradas @emnapi precisam continuar la); "
    "sem dependencia nova, `git checkout -- frontend/package-lock.json`."
)
MSG_OPENCLAW = (
    "BLOQUEADO: {arq} esta em C:\\openclaw-dev-agent, projeto de origem CONGELADO.\n"
    "Todo o trabalho e feito exclusivamente em C:\\muscle_fit (versao AWS)."
)

_REQ = re.compile(r"(^|/)api/requirements[^/]*\.txt$")
_LOCK = re.compile(r"(^|/)frontend/package-lock\.json$")


def normaliza(caminho: str, cwd: str | None = None) -> str:
    p = caminho.replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])(/|$)", p)
    if m:  # /c/muscle_fit -> c:/muscle_fit (Git Bash)
        p = f"{m.group(1)}:/{p[3:]}"
    if not re.match(r"^[a-zA-Z]:/", p) and not p.startswith("/") and cwd:
        p = normaliza(cwd).rstrip("/") + "/" + p
    partes: list[str] = []
    for parte in p.split("/"):
        if parte == "..":
            if partes:
                partes.pop()
        elif parte not in (".", ""):
            partes.append(parte)
    prefixo = "/" if p.startswith("/") else ""
    return (prefixo + "/".join(partes)).lower()


def avaliar(caminho: str, cwd: str | None = None) -> str | None:
    p = normaliza(caminho, cwd)
    if p == "c:/openclaw-dev-agent" or p.startswith("c:/openclaw-dev-agent/"):
        return MSG_OPENCLAW.format(arq=caminho)
    if _REQ.search(p):
        return MSG_REQUIREMENTS.format(arq=caminho)
    if _LOCK.search(p):
        return MSG_LOCKFILE
    return None


def main() -> int:
    try:
        dados = json.loads(sys.stdin.read() or "{}")
        entrada = dados.get("tool_input") or {}
        if not isinstance(entrada, dict):
            return 0
        caminho = entrada.get("file_path") or entrada.get("notebook_path")
        if not isinstance(caminho, str) or not caminho:
            return 0
        cwd = dados.get("cwd") if isinstance(dados.get("cwd"), str) else None
        msg = avaliar(caminho, cwd)
    except Exception:
        return 0
    if msg:
        sys.stderr.write(msg + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
