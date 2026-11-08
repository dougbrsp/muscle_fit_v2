"""Registra decisões do /ship a partir do formulário nativo do Claude Code."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ship


def handle(event):
    data = event.get("tool_input", {})
    questions = data.get("questions", [])
    if event.get("tool_name") != "AskUserQuestion" or not any(
        question.get("question", "").startswith(("[ship:", "[ship-cancel:")) for question in questions
    ):
        return None

    ship.require(event.get("agent_type") == "tech-lead" and not event.get("agent_id")
                 and Path(event["cwd"]).resolve() == ship.ROOT,
                 "Confirme na conversa principal do Tech Lead deste checkout.")
    session_id = event.get("session_id")
    tool_id = event.get("tool_use_id")
    ship.require(ship.nonempty(session_id) and ship.nonempty(tool_id), "O formulário não tem origem identificável.")
    state_path = ship.STATE / "state.json"
    receipt_path = ship.STATE / "confirmation.json"
    with ship.locked():
        ship.require(state_path.is_file(), "Não há execução aguardando esta confirmação.")
        state = ship.json_file(state_path)
        action = next((name for name, form in ship.confirmation_forms(state).items()
                       if questions == form["questions"]), None)
        ship.require(action, "O formulário diverge do checkpoint. Use confirmations do status atual.")
        if action == "gate":
            ship.require(ship.repo_identity() == (state["branch"], state["base"])
                         and ship.snapshot(state["base"]) == state["known"],
                         "O checkout mudou desde o checkpoint. A decisão continua pendente.")

        receipt = {"run": state["id"], "session_id": session_id, "tool_use_id": tool_id,
                   "action": action, "questions": questions}
        if event.get("hook_event_name") == "PreToolUse":
            ship.require(set(data) == {"questions"}, "Apresente o formulário sem respostas pré-preenchidas.")
            ship.write_json(receipt_path, receipt)
            return None

        ship.require(event.get("hook_event_name") == "PostToolUse" and receipt_path.is_file()
                     and ship.json_file(receipt_path) == receipt,
                     "Nenhum formulário correspondente foi aberto nesta conversa.")
        receipt_path.unlink()
        response = event.get("tool_response")
        ship.require(isinstance(response, dict) and response.get("questions") == questions
                     and not response.get("response") and not response.get("annotations")
                     and "afkTimeoutMs" not in response,
                     "Confirmação pendente. Selecione e envie uma das opções do formulário.")
        question = questions[0]
        answers = response.get("answers")
        ship.require(isinstance(answers, dict) and set(answers) == {question["question"]},
                     "Confirmação pendente. Envie exatamente uma opção.")
        choice = answers[question["question"]]
        ship.require(choice in [option["label"] for option in question["options"]],
                     "Confirmação pendente. A resposta não é uma das opções apresentadas.")
        provenance = {"source": "claude-code", "session_id": session_id, "tool_use_id": tool_id}
        if action == "gate":
            ship.record_choice(state, choice, **provenance)
            context = f"Escolha {choice} registrada. Consulte ship status e retome a execução."
        elif choice == "encerrar":
            state.update(phase="cancelled", active=None, gate=None, cancellation=provenance)
            context = "Encerramento confirmado. Preserve os arquivos e arquive a execução."
        else:
            return "A execução permanece no checkpoint anterior."
        ship.write_json(state_path, state)
        return context


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        context = handle(json.load(sys.stdin))
    except (AttributeError, KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[ship] {exc}", file=sys.stderr)
        return 2
    if context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": context,
        }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
