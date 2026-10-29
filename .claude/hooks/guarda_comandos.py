"""Hook PreToolUse (Bash|PowerShell): bloqueia comandos que ja causaram incidente.

Bloqueia (exit 2 + mensagem em stderr):
  1. `git worktree remove <wt>` quando `<wt>/frontend/node_modules` existe
     (junction para o node_modules do checkout principal: o remove segue o link
     e apaga o do usuario - 22/09 e 04/10, com ou sem --force).
  2. `terraform apply|destroy|import` e `terraform state rm|mv`
     (infraestrutura so via PR; quem aplica e o pipeline).
  3. `aws <servico> <operacao mutavel>` (create-/delete-/put-/update-/...,
     `s3 cp|mv|sync` com destino s3://, `s3 rm`). Leitura passa.
  4. `gh pr merge` no mesmo comando que `gh pr checks ... |` (o #430 foi
     mesclado com plan vermelho em 08/10: o pipe engole o exit status).

Como casa: o comando e quebrado em segmentos (`;`, `&&`, `||`, `|`, `&`,
nova linha, `(`, `)`, `{`, `}`, `$(`), respeitando aspas e pulando o corpo de
heredocs/here-strings. So a PRIMEIRA palavra de cada segmento conta como
comando, entao texto de mensagem de commit nao dispara. `bash -c`, `sh -c`,
`powershell -Command` e `cmd /c` sao reanalisados.

Qualquer erro interno (JSON inesperado etc.) => exit 0: bug do hook nunca
trava o trabalho.
"""

from __future__ import annotations

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_SEPARADORES = {";", "|", "&", "(", ")", "{", "}"}
_ESCAPAVEIS_BASH = set(" \t\"'$`\\;&|<>(){}#\n")


class _Lexer:
    """Quebra um comando de shell em segmentos [(palavras, separador_depois)]."""

    def __init__(self, texto: str, powershell: bool) -> None:
        self.s = texto
        self.ps = powershell
        self.esc = "`" if powershell else "\\"
        self.i = 0
        self.segmentos: list[tuple[list[str], str]] = []
        self.heredocs_pendentes: list[tuple[str, bool]] = []

    # -- utilidades --------------------------------------------------------
    def _peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def _pular_heredocs(self) -> None:
        """Chamado logo apos uma nova linha: pula o corpo dos heredocs pendentes."""
        while self.heredocs_pendentes:
            delim, tira_tab = self.heredocs_pendentes.pop(0)
            while self.i < len(self.s):
                fim = self.s.find("\n", self.i)
                linha = self.s[self.i : fim if fim != -1 else len(self.s)]
                self.i = fim + 1 if fim != -1 else len(self.s)
                cmp = linha.lstrip("\t") if tira_tab else linha
                if cmp.rstrip("\r") == delim:
                    break

    def _ler_delimitador_heredoc(self) -> None:
        tira_tab = False
        if self._peek() == "-":
            tira_tab = True
            self.i += 1
        while self._peek() in (" ", "\t"):
            self.i += 1
        delim = []
        while self.i < len(self.s) and self.s[self.i] not in " \t\n;&|()<>":
            c = self.s[self.i]
            if c not in "'\"\\":
                delim.append(c)
            self.i += 1
        if delim:
            self.heredocs_pendentes.append(("".join(delim), tira_tab))

    def _subst(self) -> None:
        """Consome `$(` ... `)` analisando o conteudo como comandos."""
        self.i += 2
        self._parse(ate_parentese=True)

    def _aspas_simples(self, buf: list[str]) -> None:
        self.i += 1
        while self.i < len(self.s) and self.s[self.i] != "'":
            buf.append(self.s[self.i])
            self.i += 1
        self.i += 1

    def _aspas_duplas(self, buf: list[str]) -> None:
        self.i += 1
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == self.esc and self.i + 1 < len(self.s) and (self.ps or self.s[self.i + 1] in '$`"\\\n'):
                buf.append(self.s[self.i + 1])
                self.i += 2
                continue
            if c == '"':
                self.i += 1
                return
            if c == "$" and self._peek(1) == "(":
                self._subst()
                buf.append("$(...)")
                continue
            buf.append(c)
            self.i += 1

    def _here_string(self) -> bool:
        """PowerShell @' ... '@ / @" ... "@ (abertura seguida de nova linha)."""
        if self._peek() == "@" and self._peek(1) in ("'", '"') and self._peek(2) in ("\n", "\r"):
            q = self._peek(1)
            fim = re.compile(r"(?m)^" + re.escape(q) + "@")
            m = fim.search(self.s, self.i + 2)
            self.i = m.end() if m else len(self.s)
            return True
        return False

    # -- parser ------------------------------------------------------------
    def _parse(self, ate_parentese: bool = False) -> None:
        palavras: list[str] = []
        buf: list[str] | None = None

        def fecha_palavra() -> None:
            nonlocal buf
            if buf is not None:
                palavras.append("".join(buf))
                buf = None

        def fecha_segmento(sep: str) -> None:
            nonlocal palavras
            fecha_palavra()
            if palavras:
                self.segmentos.append((palavras, sep))
            palavras = []

        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\n":
                self.i += 1
                fecha_segmento("\n")
                self._pular_heredocs()
                continue
            if c in " \t\r":
                fecha_palavra()
                self.i += 1
                continue
            if c == "#" and buf is None:
                fim = self.s.find("\n", self.i)
                self.i = fim if fim != -1 else len(self.s)
                continue
            if c == self.esc:
                nxt = self._peek(1)
                if self.ps or nxt in _ESCAPAVEIS_BASH:
                    if nxt == "\n":  # continuacao de linha
                        self.i += 2
                        continue
                    buf = buf if buf is not None else []
                    buf.append(nxt)
                    self.i += 2
                    continue
                buf = buf if buf is not None else []
                buf.append(c)  # `C:\caminho` fica intacto
                self.i += 1
                continue
            if c == "'":
                buf = buf if buf is not None else []
                self._aspas_simples(buf)
                continue
            if c == '"':
                buf = buf if buf is not None else []
                self._aspas_duplas(buf)
                continue
            if c == "`" and not self.ps:  # substituicao antiga: texto opaco
                fim = self.s.find("`", self.i + 1)
                self.i = fim + 1 if fim != -1 else len(self.s)
                buf = buf if buf is not None else []
                buf.append("`...`")
                continue
            if buf is None and self._here_string():
                palavras.append("@'...'@")
                continue
            if c == "$" and self._peek(1) == "(":
                fecha_segmento("$(")
                self._subst()
                continue
            if c == "$" and self._peek(1) == "{":
                fim = self.s.find("}", self.i)
                fim = fim + 1 if fim != -1 else len(self.s)
                buf = buf if buf is not None else []
                buf.append(self.s[self.i : fim])
                self.i = fim
                continue
            if c == "<" and self._peek(1) == "<" and self._peek(2) != "<":
                fecha_palavra()
                self.i += 2
                self._ler_delimitador_heredoc()
                continue
            if c == ")" and ate_parentese:
                fecha_segmento(")")
                self.i += 1
                return
            if c in _SEPARADORES:
                if c in "|&" and self._peek(1) == c:
                    fecha_segmento(c * 2)
                    self.i += 2
                    continue
                if c == "&" and buf is not None:  # 2>&1, &>
                    buf.append(c)
                    self.i += 1
                    continue
                if c == "&" and palavras and palavras[-1].endswith((">", "<")):
                    palavras[-1] += c
                    self.i += 1
                    continue
                fecha_segmento(c)
                self.i += 1
                continue
            buf = buf if buf is not None else []
            buf.append(c)
            self.i += 1
        fecha_segmento("")


def segmentos(comando: str, powershell: bool = False) -> list[tuple[list[str], str]]:
    lx = _Lexer(comando, powershell)
    lx._parse()
    return lx.segmentos


# ---------------------------------------------------------------------------
# Normalizacao de um segmento
# ---------------------------------------------------------------------------

_ATRIBUICAO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PALAVRAS_CHAVE = {"if", "then", "do", "else", "elif", "while", "until", "!", "time", "sudo", "nohup", "exec", "command", "env", "builtin"}


def _exe(palavra: str) -> str:
    nome = re.split(r"[\\/]", palavra)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if nome.endswith(ext):
            nome = nome[: -len(ext)]
    return nome


def _limpa(palavras: list[str]) -> list[str]:
    i = 0
    while i < len(palavras):
        p = palavras[i]
        if _ATRIBUICAO.match(p) or p.lower() in _PALAVRAS_CHAVE:
            i += 1
            continue
        if p.lower() == "timeout" and i + 1 < len(palavras):
            i += 2
            continue
        break
    return palavras[i:]


def _expande(comando: str, powershell: bool, profundidade: int = 0) -> list[tuple[list[str], str]]:
    """Segmentos ja limpos, com `bash -c`/`cmd /c`/`powershell -Command` reanalisados."""
    saida: list[tuple[list[str], str]] = []
    for palavras, sep in segmentos(comando, powershell):
        palavras = _limpa(palavras)
        if not palavras:
            continue
        exe = _exe(palavras[0])
        if profundidade < 3:
            if exe in ("bash", "sh", "zsh") and "-c" in palavras[1:]:
                k = palavras.index("-c")
                if k + 1 < len(palavras):
                    saida.extend(_expande(palavras[k + 1], False, profundidade + 1))
                    continue
            if exe in ("powershell", "pwsh"):
                for k, p in enumerate(palavras[1:], start=1):
                    if p.lower() in ("-c", "-command") and k + 1 < len(palavras):
                        saida.extend(_expande(" ".join(palavras[k + 1 :]), True, profundidade + 1))
                        break
                else:
                    saida.append((palavras, sep))
                continue
            if exe == "cmd":
                for k, p in enumerate(palavras[1:], start=1):
                    if p.lower() in ("/c", "//c", "/k", "//k"):
                        saida.extend(_expande(" ".join(palavras[k + 1 :]), False, profundidade + 1))
                        break
                else:
                    saida.append((palavras, sep))
                continue
        saida.append((palavras, sep))
    return saida


# ---------------------------------------------------------------------------
# Regras
# ---------------------------------------------------------------------------

MSG_WORKTREE = (
    "BLOQUEADO: `git worktree remove` em um worktree que tem `frontend/node_modules` ({nm}).\n"
    "Esse node_modules costuma ser uma junction para o do checkout principal; o remove (com ou sem "
    "--force) segue o link e APAGA o node_modules do usuario (incidentes de 22/09 e 04/10).\n"
    "Forma correta:\n"
    "  1. cmd //c rmdir {wt_win}\\frontend\\node_modules   (remove SO o link)\n"
    "  2. ls -la {wt_posix}/frontend                       (confirmar que node_modules sumiu)\n"
    "  3. git worktree remove {wt_posix}                   (sem --force)"
)

MSG_TERRAFORM = (
    "BLOQUEADO: `terraform {op}` muda estado/infraestrutura real.\n"
    "Neste projeto infraestrutura so entra via PR no GitHub (base develop); quem aplica e o pipeline "
    "(deploy-producao.yml). Localmente so `terraform init|plan|validate|fmt`."
)

MSG_AWS = (
    "BLOQUEADO: `aws {servico} {op}` e uma operacao de ESCRITA na AWS.\n"
    "Escrita na AWS so via PR/pipeline. Leitura (describe-/list-/get-, logs tail, s3 ls, "
    "s3 cp s3://... <local>) continua liberada.\n"
    "Excecao pontual autorizada pelo usuario: ele mesmo roda no prompt com `! <comando>`."
)

MSG_GH = (
    "BLOQUEADO: `gh pr merge` no mesmo comando que `gh pr checks ... |`.\n"
    "O pipe engole o exit status dos checks e o merge acontece com check vermelho (foi assim que o "
    "#430 entrou com plan vermelho em 08/10).\n"
    "Decida pelo exit status, por exemplo:\n"
    "  gh pr checks <n> --json name,bucket --jq 'all(.[]; .bucket==\"pass\" or .bucket==\"skipping\")'\n"
    "  gh pr checks <n> --watch && gh pr merge <n> ...   (sem pipe)"
)

_AWS_GLOBAIS_COM_VALOR = {
    "--profile", "--region", "--output", "--query", "--endpoint-url", "--color", "--ca-bundle",
    "--cli-read-timeout", "--cli-connect-timeout", "--cli-binary-format",
}
_S3_OPCOES_COM_VALOR = _AWS_GLOBAIS_COM_VALOR | {
    "--exclude", "--include", "--acl", "--storage-class", "--sse", "--sse-kms-key-id", "--sse-c",
    "--sse-c-key", "--content-type", "--cache-control", "--expires", "--metadata",
    "--metadata-directive", "--grants", "--website-redirect", "--content-encoding",
    "--content-language", "--content-disposition", "--expected-size", "--source-region",
    "--page-size", "--request-payer", "--checksum-algorithm",
}
_AWS_PREFIXOS_MUTAVEIS = (
    "create-", "delete-", "put-", "update-", "attach-", "detach-", "modify-", "remove-", "add-",
    "tag-", "untag-", "invoke", "start-", "stop-", "terminate-", "run-instances", "publish",
    "set-", "reboot-", "restore-",
)


def _posicionais(args: list[str], com_valor: set[str]) -> list[str]:
    pos: list[str] = []
    pula = False
    for a in args:
        if pula:
            pula = False
            continue
        if a.startswith("-"):
            if "=" not in a and a in com_valor:
                pula = True
            continue
        pos.append(a)
    return pos


def _regra_terraform(palavras: list[str]) -> str | None:
    pos = [p for p in palavras[1:] if not p.startswith("-")]
    if not pos:
        return None
    op = pos[0].lower()
    if op in ("apply", "destroy", "import"):
        return MSG_TERRAFORM.format(op=op)
    if op == "state" and len(pos) > 1 and pos[1].lower() in ("rm", "mv"):
        return MSG_TERRAFORM.format(op=f"state {pos[1].lower()}")
    return None


def _regra_aws(palavras: list[str]) -> str | None:
    pos = _posicionais(palavras[1:], _AWS_GLOBAIS_COM_VALOR)
    if len(pos) < 2:
        return None
    servico, op = pos[0].lower(), pos[1].lower()
    if servico == "s3":
        if op in ("rm", "mb", "rb"):
            return MSG_AWS.format(servico=servico, op=op)
        if op in ("cp", "mv", "sync"):
            # posicionais reais do subcomando (sem os valores de opcoes)
            k = palavras.index(pos[1]) if pos[1] in palavras else 0
            caminhos = _posicionais(palavras[k + 1 :], _S3_OPCOES_COM_VALOR)
            if len(caminhos) >= 2:
                origem, destino = caminhos[0], caminhos[1]
                if destino.lower().startswith("s3://"):
                    return MSG_AWS.format(servico=servico, op=op)
                if op == "mv" and origem.lower().startswith("s3://"):  # mv apaga a origem no S3
                    return MSG_AWS.format(servico=servico, op=op)
        return None
    if op.startswith(_AWS_PREFIXOS_MUTAVEIS):
        return MSG_AWS.format(servico=servico, op=op)
    return None


def _para_caminho_local(p: str) -> str:
    m = re.match(r"^/([a-zA-Z])(/.*)?$", p)
    if m and os.name == "nt":
        return f"{m.group(1).upper()}:{m.group(2) or '/'}"
    return p


def _regra_worktree(palavras: list[str], cwd: str | None, projeto: str | None) -> str | None:
    base_c: str | None = None
    resto = palavras[1:]
    # opcoes globais do git antes do subcomando (-C <dir>, -c k=v, --git-dir=...)
    while resto and resto[0].startswith("-"):
        if resto[0] == "-C" and len(resto) > 1:
            base_c = resto[1]
            resto = resto[2:]
        elif resto[0] == "-c" and len(resto) > 1:
            resto = resto[2:]
        else:
            resto = resto[1:]
    if len(resto) < 2 or resto[0] != "worktree" or resto[1] != "remove":
        return None
    alvos = [a for a in resto[2:] if not a.startswith("-")]
    if not alvos:
        return None
    alvo = _para_caminho_local(alvos[0])
    if "$" in alvo or "`" in alvo:
        return None  # variavel: nao da para resolver, deixa passar
    raizes = [_para_caminho_local(b) for b in (cwd, projeto) if b]
    bases: list[str] = []
    if base_c:  # `git -C <dir>`: o alvo relativo e resolvido a partir de <dir>
        c = _para_caminho_local(base_c)
        bases.extend([c] if os.path.isabs(c) else [os.path.join(r, c) for r in raizes])
    bases.extend(raizes)
    candidatos = [alvo] if os.path.isabs(alvo) else [os.path.join(b, alvo) for b in bases]
    for wt in candidatos:
        nm = os.path.join(wt, "frontend", "node_modules")
        if os.path.lexists(nm) or os.path.isdir(nm):
            wt_norm = os.path.normpath(wt)
            return MSG_WORKTREE.format(
                nm=nm,
                wt_win=wt_norm.replace("/", "\\"),
                wt_posix=wt_norm.replace("\\", "/"),
            )
    return None


def avaliar(comando: str, powershell: bool = False, cwd: str | None = None, projeto: str | None = None) -> str | None:
    """Devolve a mensagem de bloqueio, ou None se o comando pode seguir."""
    segs = _expande(comando, powershell)
    tem_merge = False
    checks_com_pipe = False
    for palavras, sep in segs:
        exe = _exe(palavras[0])
        if exe == "terraform":
            msg = _regra_terraform(palavras)
            if msg:
                return msg
        elif exe == "aws":
            msg = _regra_aws(palavras)
            if msg:
                return msg
        elif exe == "git":
            msg = _regra_worktree(palavras, cwd, projeto)
            if msg:
                return msg
        elif exe == "gh":
            pos = [p for p in palavras[1:] if not p.startswith("-")]
            if pos[:2] == ["pr", "merge"]:
                tem_merge = True
            if pos[:2] == ["pr", "checks"] and sep == "|":
                checks_com_pipe = True
    if tem_merge and checks_com_pipe:
        return MSG_GH
    return None


def main() -> int:
    try:
        dados = json.loads(sys.stdin.read() or "{}")
        nome = str(dados.get("tool_name") or "")
        entrada = dados.get("tool_input") or {}
        comando = entrada.get("command") if isinstance(entrada, dict) else None
        if not isinstance(comando, str) or not comando.strip():
            return 0
        msg = avaliar(
            comando,
            powershell=nome.lower() == "powershell",
            cwd=dados.get("cwd") if isinstance(dados.get("cwd"), str) else None,
            projeto=os.environ.get("CLAUDE_PROJECT_DIR"),
        )
    except Exception:  # bug do hook nunca trava o trabalho
        return 0
    if msg:
        sys.stderr.write(msg + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
