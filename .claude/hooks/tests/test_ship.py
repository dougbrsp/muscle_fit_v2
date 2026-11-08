import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

CLAUDE = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkout(tmp_path):
    for name in ("scripts", "hooks"):
        (tmp_path / ".claude" / name).mkdir(parents=True)
    for rel in ("scripts/ship.py", "scripts/verify.py", "hooks/ship_receipt.py"):
        shutil.copy(CLAUDE / rel, tmp_path / ".claude" / rel)
    (tmp_path / ".claude/.gitignore").write_text("local-state/\n", encoding="utf-8")
    (tmp_path / "base.md").write_text("base\n", encoding="utf-8")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "develop")
    git("add", ".")
    git("-c", "user.email=test@example.invalid", "-c", "user.name=Harness Test", "commit", "-m", "base")
    git("update-ref", "refs/remotes/origin/develop", git("rev-parse", "HEAD"))
    git("switch", "-c", "ship/example")
    return tmp_path


def invoke(root, *args, error=None):
    result = subprocess.run([sys.executable, ".claude/scripts/ship.py", *args], cwd=root,
                            input="", capture_output=True, encoding="utf-8", timeout=30)
    if error:
        assert result.returncode == 2 and error in result.stderr, result
        return
    assert result.returncode == 0, result
    return json.loads(result.stdout) if result.stdout else None


def payload(root, name, data):
    file = root / ".claude/local-state" / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data), encoding="utf-8")
    return str(file)


def start(root, gate=None):
    tasks = [
        {"id": "base", "agent": "docs-curator", "files": ["base.md"], "deps": [], "acceptance": "Atualizar base."},
        {"id": "extra", "agent": "docs-curator", "files": ["novo.md"], "deps": ["base"],
         "acceptance": "Acrescentar exemplo."},
    ]
    return invoke(root, "start", payload(root, "plan.json", {"goal": "Documentar", "tasks": tasks, "gate": gate}))


def report(root, task):
    return invoke(root, "report", task, payload(root, "report.json", {"status": "done", "evidence": "Feito."}))


def verdict(root, review, status, receipt=True):
    role = "code-reviewer"
    agent = f"abc-{review['round']}"
    if receipt:
        result = subprocess.run([sys.executable, ".claude/hooks/ship_receipt.py"], cwd=root,
                                input=json.dumps({"cwd": str(root), "agent_type": role, "agent_id": agent}),
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result
    return invoke(root, "verdict", role, payload(root, "verdict.json", {
        "revision": review["revision"], "agent_id": agent, "status": status, "evidence": "Evidência."
    }), error=None if receipt else "sem recibo nativo")


def implemented(root):
    assert start(root)["ready_tasks"] == ["base"]
    invoke(root, "claim", "extra", error="dependências")
    invoke(root, "claim", "base")
    (root / "base.md").write_text("alterado\n", encoding="utf-8")
    assert report(root, "base")["ready_tasks"] == ["extra"]
    invoke(root, "claim", "extra")
    (root / "novo.md").write_text("novo\n", encoding="utf-8")
    report(root, "extra")
    review = invoke(root, "review")
    diff = (root / ".claude/local-state/ship/review.diff").read_text(encoding="utf-8")
    assert "+alterado" in diff and "+novo" in diff
    return review


def test_full_graph_with_retry_and_binding(checkout):
    review = implemented(checkout)
    targets = payload(checkout, "targets.json", {"backend_tests": [], "frontend_tests": []})
    invoke(checkout, "verify", targets, error="Falta aprovação")
    verdict(checkout, review, "pass", receipt=False)
    verdict(checkout, review, "changes")
    invoke(checkout, "rework", "extra")
    invoke(checkout, "claim", "extra")
    (checkout / "novo.md").write_text("corrigido\n", encoding="utf-8")
    report(checkout, "extra")
    fresh = invoke(checkout, "review")
    assert fresh["revision"] != review["revision"]
    verdict(checkout, fresh, "pass")
    assert invoke(checkout, "verify", targets)["phase"] == "verified"
    assert invoke(checkout, "ready")["verify"]["exit"] == 0
    (checkout / "novo.md").write_text("mudou depois\n", encoding="utf-8")
    invoke(checkout, "ready", error="Diff divergiu")


def test_waiting_blocks_execution_and_piped_decision(checkout):
    wait = start(checkout, {"question": "Qual regra?", "impact": "Altera produto",
                            "recommendation": "a", "options": {"a": "Uma", "b": "Outra"}})
    assert wait["phase"] == "waiting" and not wait["ready_tasks"]
    invoke(checkout, "claim", "base", error="fase de execução")
    invoke(checkout, "decide", wait["gate"]["id"], "a", error="terminal interativo")


def test_write_outside_ownership_blocks(checkout):
    start(checkout)
    invoke(checkout, "claim", "base")
    (checkout / "intruso.md").write_text("alterado\n", encoding="utf-8")
    result = report(checkout, "base")
    assert result["phase"] == "blocked" and "intruso.md" in result["reason"]


def test_retry_without_diff_change_blocks(checkout):
    review = implemented(checkout)
    verdict(checkout, review, "changes")
    invoke(checkout, "rework", "extra")
    invoke(checkout, "claim", "extra")
    report(checkout, "extra")
    assert invoke(checkout, "review")["phase"] == "blocked"


def test_retry_budget_blocks_third_repair(checkout):
    review = implemented(checkout)
    for attempt in range(2):
        verdict(checkout, review, "changes")
        invoke(checkout, "rework", "extra")
        invoke(checkout, "claim", "extra")
        (checkout / "novo.md").write_text(f"tentativa {attempt}\n", encoding="utf-8")
        report(checkout, "extra")
        review = invoke(checkout, "review")
    verdict(checkout, review, "changes")
    assert invoke(checkout, "rework", "extra")["phase"] == "blocked"


def test_rejects_cyclic_graph(checkout):
    plan = {"goal": "Documentar", "tasks": [
        {"id": "a", "agent": "docs-curator", "files": ["base.md"], "deps": ["b"], "acceptance": "a"},
        {"id": "b", "agent": "docs-curator", "files": ["novo.md"], "deps": ["a"], "acceptance": "b"}
    ]}
    invoke(checkout, "start", payload(checkout, "plan.json", plan), error="ciclo")
