"""Proteções operacionais do grafo para chamadas visíveis ao Claude Code."""
import json
from pathlib import Path
import re
import subprocess
import sys

from guarda_comandos import segmentos

REVIEWERS = {"code-reviewer", "guardiao-motores", "revisor-impacto-producao", "security-reviewer"}


def evaluate(event):
    data = event["tool_input"]
    agent = event.get("agent_type")
    cwd = Path(event["cwd"]).resolve()
    root = Path(__file__).resolve().parents[2]
    tool = event["tool_name"]
    if tool in {"Bash", "PowerShell"}:
        command = data.get("command", "")
        lower = command.casefold()
        if agent in REVIEWERS:
            return "Revisores usam Read/Grep/Glob e o diff congelado."
        if re.search(r"(?<![\w-])ship_confirmation\.py\b", lower):
            return "A confirmação é registrada pelo Claude Code. Apresente o formulário nativo."
        if "ship.py" in lower and (agent != "tech-lead" or event.get("agent_id") or
                                   re.search(r"\b(decide|cancel)\b", lower)):
            return "Apenas o Tech Lead opera o grafo. Confirme escolhas e encerramento na conversa."
        state_path = root / ".claude/local-state/ship/state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            for words, _ in segmentos(command, tool == "PowerShell"):
                if not words:
                    continue
                name = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
                if name == "gh" and any(op in words for op in ("merge", "workflow", "api")):
                    return "Use o fluxo de promoção separado e com autorização."
                if (name == "git" and any(op in words for op in ("commit", "push"))) or (
                        name == "gh" and "pr" in words and "create" in words):
                    if agent != "tech-lead" or event.get("agent_id"):
                        return "Somente o Tech Lead publica uma entrega /ship."
                    if state["phase"] != "verified":
                        return "Publicação requer /ship verificado."
                    if name == "git" and "push" in words and any(
                            word in {"--force", "-f", "--mirror", "--all", "--tags"} or
                            word.rsplit(":", 1)[-1].removeprefix("refs/heads/") in {"main", "master", "develop"}
                            for word in words[1:]):
                        return "O /ship publica apenas a branch de trabalho."
                    ready = subprocess.run([sys.executable, str(root / ".claude/scripts/ship.py"), "ready"],
                                           cwd=root, capture_output=True, timeout=25)
                    if ready.returncode:
                        return "O diff atual diverge da revisão verificada. Consulte ship.py status."
        return None
    file = data.get("file_path") or data.get("notebook_path")
    if file:
        path = (cwd / file).resolve()
        ledger = root / ".claude/local-state/ship"
        if path.is_relative_to(ledger):
            return "O estado e o diff congelado são escritos apenas pelo script ship.py."
        if agent in REVIEWERS:
            return "Revisor não escreve."
        if agent == "tech-lead" and not event.get("agent_id") and not path.is_relative_to(
                root / ".claude/local-state/ship-input"):
            return "O Tech Lead só escreve arquivos de entrada do grafo. Delegue a implementação."
        state_path = ledger / "state.json"
        if event.get("agent_id") and state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            task = next((task for task in state["plan"]["tasks"] if task["id"] == state["active"]), None)
            if not (task and task["agent"] == agent and
                    any(path == (root / owned).resolve() for owned in task["files"]) and
                    path.is_relative_to(root)):
                return "A edição exige tarefa ativa do agente e caminho previsto no plano."
    return None


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        msg = evaluate(json.load(sys.stdin))
    except (KeyError, ValueError, OSError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Falha na guarda do fluxo: {exc}", file=sys.stderr)
        sys.exit(2)
    if msg:
        print(msg, file=sys.stderr)
        sys.exit(2)
