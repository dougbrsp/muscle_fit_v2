"""Registra a conclusão observada de um revisor nativo durante a rodada atual."""
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".claude/local-state/ship"


def main():
    event = json.load(sys.stdin)
    if not (STATE / "state.json").is_file() or Path(event["cwd"]).resolve() != ROOT:
        return
    state = json.loads((STATE / "state.json").read_text(encoding="utf-8"))
    role = event.get("agent_type")
    agent_id = event.get("agent_id", "")
    if (state["phase"] != "review" or role not in state["required"] or
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", agent_id)):
        return
    receipts = STATE / "receipts"
    receipts.mkdir(exist_ok=True)
    destination = receipts / f'{state["id"]}-{state["round"]}-{role}-{agent_id}.json'
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=receipts,
                                     suffix=".tmp", delete=False) as receipt:
        json.dump({"role": role, "agent_id": agent_id, "revision": state["revision"]}, receipt)
    Path(receipt.name).replace(destination)


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    main()
