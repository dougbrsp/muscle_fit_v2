from copy import deepcopy
import json
import shutil
import subprocess
import sys

import pytest

from test_ship import CLAUDE, checkout, invoke, start


SESSION = "1a0bcd12-23ab-4ee2-ab33-4bfb83c10834"
OTHER_SESSION = "fdbeea5d-0091-4665-8a7b-7fcf2d003d6b"
GATE = {
    "question": "Qual regra?",
    "impact": "Altera produto.",
    "recommendation": "a",
    "options": {"a": "Uma regra", "b": "Outra regra"},
}


@pytest.fixture
def confirmation_checkout(checkout):
    hook = ".claude/hooks/ship_confirmation.py"
    shutil.copy(CLAUDE / "hooks/ship_confirmation.py", checkout / hook)
    with (checkout / ".git/info/exclude").open("a", encoding="utf-8") as exclude:
        exclude.write(f"\n/{hook}\n")
    return checkout


def event(root, form, tool_id, *, answer=None, **overrides):
    data = {
        "hook_event_name": "PreToolUse" if answer is None else "PostToolUse",
        "tool_name": "AskUserQuestion",
        "cwd": str(root),
        "agent_type": "tech-lead",
        "session_id": SESSION,
        "tool_use_id": tool_id,
        "tool_input": deepcopy(form),
    }
    if answer is not None:
        question = form["questions"][0]["question"]
        data["tool_response"] = {**deepcopy(form), "answers": {question: answer}}
    data.update(overrides)
    return data


def hook(root, data, expected=0):
    result = subprocess.run(
        [sys.executable, "-B", ".claude/hooks/ship_confirmation.py"],
        cwd=root, input=json.dumps(data), capture_output=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == expected, result


def test_native_gate_requires_matching_confirmation_and_releases_work(confirmation_checkout):
    root = confirmation_checkout
    pending = start(root, GATE)
    form = pending["confirmations"]["gate"]
    assert pending["phase"] == "waiting" and pending["ready_tasks"] == []
    assert set(option["label"] for option in form["questions"][0]["options"]) == {"a", "b"}

    tool = "toolu_01Gate"
    hook(root, event(root, form, tool, answer="a"), expected=2)
    hook(root, event(root, form, tool))
    hook(root, event(root, form, tool, answer="a", session_id=OTHER_SESSION), expected=2)
    hook(root, event(root, form, "toolu_01Wrong", answer="a"), expected=2)

    still_pending = invoke(root, "status")
    assert still_pending["phase"] == "waiting" and still_pending["decisions"] == []
    hook(root, event(root, form, tool))
    hook(root, event(root, form, tool, answer="b"))
    hook(root, event(root, form, tool, answer="b"), expected=2)
    complete = invoke(root, "status")
    assert complete["phase"] == "execute" and complete["ready_tasks"] == ["base"]
    assert len(complete["decisions"]) == 1
    recorded = complete["decisions"][0]
    assert recorded["id"] == pending["gate"]["id"]
    assert (recorded["selected"], recorded["source"], recorded["session_id"]) == (
        "b", "claude-code", SESSION,
    )


def test_native_gate_refuses_prefilled_changed_or_ambiguous_answers(confirmation_checkout):
    root = confirmation_checkout
    form = start(root, GATE)["confirmations"]["gate"]
    question = form["questions"][0]["question"]
    tool = "toolu_02Gate"

    prefilled = event(root, form, tool)
    prefilled["tool_input"]["answers"] = {question: "a"}
    hook(root, prefilled, expected=2)
    tampered = event(root, form, tool)
    tampered["tool_input"]["questions"][0]["options"][0]["description"] = "Descrição trocada."
    hook(root, tampered, expected=2)
    hook(root, event(root, form, tool, agent_id="agent_abc"), expected=2)

    for index, modification in enumerate((
        {"answers": {}},
        {"answers": {question: "opção livre"}},
        {"response": "Aguarde antes de continuar."},
        {"annotations": {question: {"notes": "Ainda não confirme."}}},
        {"afkTimeoutMs": 60000},
    )):
        attempt = f"toolu_02Attempt{index}"
        hook(root, event(root, form, attempt))
        result = event(root, form, attempt, answer="a")
        result["tool_response"].update(modification)
        hook(root, result, expected=2)

    unrelated = deepcopy(form)
    unrelated["questions"][0]["question"] = "Em qual formato quer receber o relatório?"
    hook(root, event(root, unrelated, "toolu_02Ordinary"))
    hook(root, event(root, unrelated, "toolu_02Ordinary", answer="a"))
    pending = invoke(root, "status")
    assert pending["phase"] == "waiting" and pending["decisions"] == []


def test_native_cancellation_requires_explicit_choice(confirmation_checkout):
    root = confirmation_checkout
    pending = start(root, GATE)
    cancel = pending["confirmations"]["cancel"]
    hook(root, event(root, cancel, "toolu_03Keep"))
    hook(root, event(root, cancel, "toolu_03Keep", answer="continuar"))
    unchanged = invoke(root, "status")
    assert unchanged["phase"] == "waiting"
    assert unchanged["gate"]["id"] == pending["gate"]["id"]
    assert unchanged["decisions"] == []

    cancel = unchanged["confirmations"]["cancel"]
    hook(root, event(root, cancel, "toolu_03Stop"))
    hook(root, event(root, cancel, "toolu_03Stop", answer="encerrar"))
    finished = invoke(root, "status")
    assert finished["phase"] == "cancelled"
    assert finished["gate"] is None and finished["ready_tasks"] == []
