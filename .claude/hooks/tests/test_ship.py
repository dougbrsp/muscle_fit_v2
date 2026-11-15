import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

CLAUDE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CLAUDE / "scripts"))
from ship import MAX_REWORK  # noqa: E402


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


def test_retry_budget_blocks_repair_after_max_rework(checkout):
    review = implemented(checkout)
    for attempt in range(MAX_REWORK):
        verdict(checkout, review, "changes")
        state = invoke(checkout, "rework", "extra")
        assert state["phase"] == "execute" and state["round"] == attempt + 1
        invoke(checkout, "claim", "extra")
        (checkout / "novo.md").write_text(f"tentativa {attempt}\n", encoding="utf-8")
        report(checkout, "extra")
        review = invoke(checkout, "review")
    verdict(checkout, review, "changes")
    blocked = invoke(checkout, "rework", "extra")
    assert blocked["phase"] == "blocked" and str(MAX_REWORK) in blocked["reason"]


def test_rejects_cyclic_graph(checkout):
    plan = {"goal": "Documentar", "tasks": [
        {"id": "a", "agent": "docs-curator", "files": ["base.md"], "deps": ["b"], "acceptance": "a"},
        {"id": "b", "agent": "docs-curator", "files": ["novo.md"], "deps": ["a"], "acceptance": "b"}
    ]}
    invoke(checkout, "start", payload(checkout, "plan.json", plan), error="ciclo")


def test_task_path_accepts_brackets_rejects_wildcards():
    from ship import task_path
    assert task_path("app/[id]/page.tsx") and task_path("app/(app)/x/[id]/C.tsx")
    assert not task_path("app/*/page.tsx") and not task_path("app/?/page.tsx")
    assert not task_path("../x") and not task_path("/x") and not task_path(".git/config")


def test_bracket_path_is_literal_for_git(checkout):
    for rel in ("app/i/page.tsx", "app/[id]/page.tsx"):
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
    (checkout / "app/i/page.tsx").write_text("i\n", encoding="utf-8")
    (checkout / "app/[id]/page.tsx").write_text("antigo\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "."], cwd=checkout)
    subprocess.check_call(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=T", "commit", "-m", "rotas"],
                          cwd=checkout)
    subprocess.check_call(["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=checkout)
    tasks = [{"id": "rota", "agent": "docs-curator", "files": ["app/[id]/page.tsx"], "deps": [],
              "acceptance": "Editar rota."}]
    invoke(checkout, "start", payload(checkout, "plan.json", {"goal": "Rota", "tasks": tasks}))
    invoke(checkout, "claim", "rota")
    (checkout / "app/[id]/page.tsx").write_text("novo\n", encoding="utf-8")
    (checkout / "app/i/page.tsx").write_text("intruso\n", encoding="utf-8")
    result = report(checkout, "rota")
    assert result["phase"] == "blocked" and "app/i/page.tsx" in result["reason"]


def test_bracket_path_report_and_review(checkout):
    (checkout / "app/i").mkdir(parents=True)
    (checkout / "app/i/page.tsx").write_text("i\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "."], cwd=checkout)
    subprocess.check_call(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=T", "commit", "-m", "rotas"],
                          cwd=checkout)
    subprocess.check_call(["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=checkout)
    tasks = [{"id": "rota", "agent": "docs-curator", "files": ["app/[id]/page.tsx"], "deps": [],
              "acceptance": "Criar rota."}]
    invoke(checkout, "start", payload(checkout, "plan.json", {"goal": "Rota", "tasks": tasks}))
    invoke(checkout, "claim", "rota")
    (checkout / "app/[id]").mkdir(parents=True)
    (checkout / "app/[id]/page.tsx").write_text("rota\n", encoding="utf-8")
    assert report(checkout, "rota")["phase"] != "blocked"
    invoke(checkout, "review")
    diff = (checkout / ".claude/local-state/ship/review.diff").read_text(encoding="utf-8")
    assert "app/[id]/page.tsx" in diff and "+rota" in diff and "app/i/page.tsx" not in diff


# --- report faz o commit local do submódulo da tarefa ---

IDENT = ["-c", "user.email=test@example.invalid", "-c", "user.name=Harness Test"]


def sh(cwd, *args):
    return subprocess.check_output(["git", *IDENT, *args], cwd=cwd, text=True, encoding="utf-8").strip()


@pytest.fixture
def neutral_home(tmp_path_factory, monkeypatch):
    # Config global neutra: HOME/USERPROFILE/XDG vazios (o ship.py remove GIT_* do ambiente do git, então
    # GIT_CONFIG_GLOBAL não serviria). Prova que o commit usa só a identidade local do submódulo.
    # Fixture anterior ao `checkout`, para que todo git do teste veja a mesma config (autocrlf etc.).
    home = tmp_path_factory.mktemp("home")
    for var in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(var, str(home))


@pytest.fixture
def sub_checkout(neutral_home, checkout, tmp_path_factory):
    origin = tmp_path_factory.mktemp("origem")
    sh(origin, "init", "-b", "main")
    (origin / "a.txt").write_text("a\n", encoding="utf-8")
    sh(origin, "add", ".")
    sh(origin, "commit", "-m", "inicio")
    sh(checkout, "-c", "protocol.file.allow=always", "submodule", "add", str(origin), "mod")
    sh(checkout, "commit", "-m", "submodulo")
    sh(checkout, "update-ref", "refs/remotes/origin/develop", "HEAD")
    sh(checkout / "mod", "config", "user.email", "sub@example.invalid")
    sh(checkout / "mod", "config", "user.name", "Sub Test")
    sh(checkout / "mod", "switch", "-c", "trabalho")
    return checkout


def start_sub(root, files=("mod",)):
    tasks = [{"id": "sub", "agent": "backend-engineer", "files": list(files), "deps": [], "acceptance": "Mexer."}]
    invoke(root, "start", payload(root, "plan.json", {"goal": "Submodulo", "tasks": tasks}))
    invoke(root, "claim", "sub")


def report_sub(root, data=None, error=None, task="sub"):
    data = {"status": "done", "evidence": "Feito.", **(data or {})}
    return invoke(root, "report", task, payload(root, "report.json", data), error=error)


def state_of(root):
    return json.loads((root / ".claude/local-state/ship/state.json").read_text(encoding="utf-8"))


def msg(root):
    return sh(root / "mod", "log", "-1", "--format=%B")


def test_report_commits_dirty_submodule(sub_checkout):
    root = sub_checkout
    start_sub(root)
    (root / "mod/a.txt").write_text("alterado\n", encoding="utf-8")
    (root / "mod/novo.txt").write_text("novo\n", encoding="utf-8")
    text = "feat: algo\n\nLinha <a@b> com (parênteses)\n`x` $(y)"
    result = report_sub(root, {"submodule_commit_message": text})
    assert msg(root) == text
    assert sh(root / "mod", "status", "--porcelain") == ""
    assert "novo.txt" in sh(root / "mod", "show", "--name-only", "--format=", "HEAD")
    assert result["done"] == ["sub"] and result["history"][0]["files"] == ["mod"]
    assert state_of(root)["known"]["mod"] == "submodule:" + sh(root / "mod", "rev-parse", "HEAD")
    assert sh(root, "rev-parse", "HEAD") == sh(root, "rev-parse", "origin/develop")


def test_report_refuses_detached_head_and_protected_branches(sub_checkout):
    root = sub_checkout
    start_sub(root)
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    cases = [(["--detach"], "destacado")] + [(["-C", b], "branch protegida") for b in ("main", "master", "develop")]
    for switch, error in cases:
        sh(root / "mod", "switch", *switch)
        head = sh(root / "mod", "rev-parse", "HEAD")
        report_sub(root, error=error)
        assert sh(root / "mod", "rev-parse", "HEAD") == head and state_of(root)["active"] == "sub"


def test_report_refuses_invalid_message(sub_checkout):
    root = sub_checkout
    start_sub(root)
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    head = sh(root / "mod", "rev-parse", "HEAD")
    for bad in ["", "   ", 5, ["x"], "a\x00b", "a\x1bb", "a\rb"]:
        report_sub(root, {"submodule_commit_message": bad}, error="submodule_commit_message")
        assert sh(root / "mod", "rev-parse", "HEAD") == head and state_of(root)["active"] == "sub"


def test_dirty_submodule_outside_task_still_fails(sub_checkout):
    root = sub_checkout
    start_sub(root, files=("base.md",))
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    head = sh(root / "mod", "rev-parse", "HEAD")
    report_sub(root, error="Submódulo sujo: mod")
    assert sh(root / "mod", "rev-parse", "HEAD") == head


def test_clean_submodule_gets_no_commit(sub_checkout):
    root = sub_checkout
    start_sub(root, files=("mod", "base.md"))
    head = sh(root / "mod", "rev-parse", "HEAD")
    (root / "base.md").write_text("alterado\n", encoding="utf-8")
    report_sub(root)
    assert sh(root / "mod", "rev-parse", "HEAD") == head


def test_git_hooks_are_not_executed(sub_checkout):
    root = sub_checkout
    hooks = Path(sh(root / "mod", "rev-parse", "--absolute-git-dir")) / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"):
        (hooks / name).write_text("#!/bin/sh\ntouch \"$(dirname \"$0\")/marcador\"\nexit 1\n", encoding="utf-8")
        os.chmod(hooks / name, 0o755)
    start_sub(root)
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    report_sub(root)
    assert not (hooks / "marcador").exists() and sh(root / "mod", "status", "--porcelain") == ""
    # controle: sem a desativação, o hook plantado roda e barra o commit
    (root / "mod/b.txt").write_text("y\n", encoding="utf-8")
    sh(root / "mod", "add", "-A")
    blocked = subprocess.run(["git", *IDENT, "commit", "-m", "z"], cwd=root / "mod", capture_output=True)
    assert blocked.returncode != 0 and (hooks / "marcador").exists()


def test_report_with_bad_input_does_not_commit(sub_checkout):
    root = sub_checkout
    start_sub(root)
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    head = sh(root / "mod", "rev-parse", "HEAD")
    file = root / ".claude/local-state/ruim.json"
    file.write_text("{nao json", encoding="utf-8")
    result = subprocess.run([sys.executable, ".claude/scripts/ship.py", "report", "sub", str(file)], cwd=root,
                            capture_output=True, encoding="utf-8", input="", timeout=30)
    assert result.returncode != 0
    report_sub(root, {"evidence": ""}, error="evidence")
    report_sub(root, {"status": "x"}, error="status")
    report_sub(root, task="outra", error="tarefa ativa")
    assert sh(root / "mod", "rev-parse", "HEAD") == head


def test_blocked_report_still_commits_and_blocks_run(sub_checkout):
    root = sub_checkout
    start_sub(root)
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    assert report_sub(root, {"status": "blocked"})["phase"] == "blocked"
    # mensagem padrão cita a execução e a tarefa
    assert state_of(root)["id"] in msg(root) and "sub" in msg(root)


# --- o commit nunca cai fora do submódulo registrado da tarefa ---

def main_state(root):
    return sh(root, "rev-parse", "HEAD"), sh(root, "status", "--porcelain")


def other_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("alheio")
    sh(repo, "init", "-b", "trabalho")
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    sh(repo, "add", ".")
    sh(repo, "commit", "-m", "inicio")
    (repo / "x.txt").write_text("sujo\n", encoding="utf-8")
    return repo


def make_link(link, target):
    if os.name == "nt":
        return subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True).returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


def drop_link(link):
    os.unlink(link) if link.is_symlink() else os.rmdir(link)


def test_report_refuses_dotgit_file_pointing_to_main_repo(sub_checkout):
    root = sub_checkout
    start_sub(root, files=("evil",))
    (root / "evil").mkdir()
    (root / "evil/.git").write_text(f"gitdir: {(root / '.git').as_posix()}\n", encoding="utf-8")
    (root / "evil/a.txt").write_text("x\n", encoding="utf-8")
    before, index = main_state(root), sh(root, "diff", "--cached", "--name-only")
    state = state_of(root)
    report_sub(root, error="não é submódulo registrado")
    assert main_state(root) == before and sh(root, "diff", "--cached", "--name-only") == index
    assert state_of(root) == state


def test_report_refuses_link_to_other_dirty_repo(sub_checkout, tmp_path_factory):
    root = sub_checkout
    other = other_repo(tmp_path_factory)
    start_sub(root, files=("link",))
    if not make_link(root / "link", other):
        pytest.skip("não foi possível criar junção/symlink de diretório neste ambiente")
    head = sh(other, "rev-parse", "HEAD")
    report_sub(root, error="não é submódulo registrado")
    assert sh(other, "rev-parse", "HEAD") == head and "x.txt" in sh(other, "status", "--porcelain")
    assert state_of(root)["active"] == "sub"


def test_report_refuses_own_git_repo_not_in_base(sub_checkout):
    root = sub_checkout
    start_sub(root, files=("proprio",))
    (root / "proprio").mkdir()
    sh(root / "proprio", "init", "-b", "trabalho")
    (root / "proprio/y.txt").write_text("y\n", encoding="utf-8")
    report_sub(root, error="não é gitlink na base")
    assert sh(root / "proprio", "status", "--porcelain") == "?? y.txt"
    assert state_of(root)["active"] == "sub"


@pytest.fixture
def two_sub_checkout(sub_checkout, tmp_path_factory):
    root = sub_checkout
    origin = tmp_path_factory.mktemp("origem2")
    sh(origin, "init", "-b", "main")
    (origin / "b.txt").write_text("b\n", encoding="utf-8")
    sh(origin, "add", ".")
    sh(origin, "commit", "-m", "inicio")
    sh(root, "-c", "protocol.file.allow=always", "submodule", "add", str(origin), "mod2")
    sh(root, "commit", "-m", "segundo")
    sh(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    sh(root / "mod2", "config", "user.email", "sub@example.invalid")
    sh(root / "mod2", "config", "user.name", "Sub Test")
    sh(root / "mod2", "switch", "-c", "trabalho")
    return root


def test_report_commits_two_submodules(two_sub_checkout):
    root = two_sub_checkout
    start_sub(root, files=("mod", "mod2"))
    (root / "mod/a.txt").write_text("1\n", encoding="utf-8")
    (root / "mod2/b.txt").write_text("2\n", encoding="utf-8")
    report_sub(root)
    assert sh(root / "mod", "status", "--porcelain") == "" and sh(root / "mod2", "status", "--porcelain") == ""
    assert "sub" in sh(root / "mod2", "log", "-1", "--format=%B")


def test_report_refuses_each_registration_condition(two_sub_checkout, tmp_path_factory):
    """Cada condição de require_registered_submodule falha isolada, com a sub-mensagem própria, e nada muda."""
    root = two_sub_checkout
    mod, modules = root / "mod", root / ".git" / "modules"
    other = other_repo(tmp_path_factory)
    start_sub(root)
    (mod / "a.txt").write_text("x\n", encoding="utf-8")
    gitfile = (mod / ".git").read_text(encoding="utf-8")
    cfg, cfg2 = modules / "mod" / "config", modules / "mod2" / "config"
    worktree, worktree2 = (sh(root, "config", "--file", str(c), "core.worktree") for c in (cfg, cfg2))

    def snap():
        return (sh(root, "rev-parse", "HEAD"), sh(root, "for-each-ref"), sh(root / "mod2", "rev-parse", "HEAD"),
                sh(other, "rev-parse", "HEAD"), sh(other, "status", "--porcelain"), state_of(root))

    def set_cfg(file, key, value):
        sh(root, "config", "--file", str(file), key, value)

    def unset_cfg(file, key):
        sh(root, "config", "--file", str(file), "--unset", key)

    def point_gitfile(target):
        (mod / ".git").unlink()  # no Windows o gitfile pode estar oculto e não aceita escrita direta
        (mod / ".git").write_text(f"gitdir: {target}\n", encoding="utf-8")

    def pre_link():
        mod.rename(root / "mod.bak")
        if not make_link(mod, other):
            (root / "mod.bak").rename(mod)
            return False

    def post_link():
        drop_link(mod)
        (root / "mod.bak").rename(mod)

    def pre_bare():
        unset_cfg(cfg, "core.worktree")
        set_cfg(cfg, "core.bare", "true")

    def post_bare():
        set_cfg(cfg, "core.bare", "false")
        set_cfg(cfg, "core.worktree", worktree)

    def pre_other_module():
        unset_cfg(cfg2, "core.worktree")
        point_gitfile("../.git/modules/mod2")

    def post_other_module():
        set_cfg(cfg2, "core.worktree", worktree2)
        point_gitfile("../.git/modules/mod")

    def pre_evil():
        evil = modules / "evil"
        evil.mkdir()
        (evil / "HEAD").write_text(f"ref: refs/heads/{sh(root, 'branch', '--show-current')}\n", encoding="utf-8")
        (evil / "commondir").write_text("../..\n", encoding="utf-8")
        point_gitfile("../.git/modules/evil")

    def post_evil():
        shutil.rmtree(modules / "evil")
        point_gitfile("../.git/modules/mod")

    def pre_embedded():
        (mod / ".git").unlink()
        shutil.copytree(modules / "mod", mod / ".git")

    def post_embedded():
        shutil.rmtree(mod / ".git", onexc=lambda fn, p, exc: (os.chmod(p, 0o700), fn(p)))
        (mod / ".git").write_text(gitfile, encoding="utf-8")

    commondir = modules / "mod" / "commondir"
    variants = [
        ("caminho com link ou junção", pre_link, post_link),
        ("a raiz do repositório não é o próprio caminho", pre_bare, post_bare),
        ("gitdir não é exatamente modules/<nome>", pre_other_module, post_other_module),
        ("gitdir não é exatamente modules/<nome>", lambda: point_gitfile("../.git"),
         lambda: point_gitfile("../.git/modules/mod")),
        ("gitdir não é exatamente modules/<nome>", pre_evil, post_evil),
        ("commondir presente", lambda: commondir.write_text("../..\n", encoding="utf-8"), commondir.unlink),
        ("repositório embutido", pre_embedded, post_embedded),
        ("core.worktree diverge", lambda: set_cfg(cfg, "core.worktree", str(other)),
         lambda: set_cfg(cfg, "core.worktree", worktree)),
    ]
    for message, pre, post in variants:
        if pre() is False:  # junção/symlink indisponível neste ambiente
            continue
        try:
            before = snap()
            report_sub(root, error=message)
            assert snap() == before
        finally:
            post()
        assert sh(mod, "status", "--porcelain") == "M a.txt" and sh(mod, "branch", "--show-current") == "trabalho"
    assert sh(root, "branch", "--show-current") == "ship/example"
    report_sub(root)  # controle: restaurado o estado legítimo, o commit passa
    assert sh(mod, "status", "--porcelain") == ""


def test_report_detects_main_refs_moved_by_submodule_commit(sub_checkout, monkeypatch):
    import importlib.util
    root = sub_checkout
    spec = importlib.util.spec_from_file_location("script_do_checkout", root / ".claude/scripts/ship.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    (root / "mod/a.txt").write_text("x\n", encoding="utf-8")
    original = script.run_git

    def hostile(*args, cwd, stdin=""):
        original(*args, cwd=cwd, stdin=stdin)
        if args[0] == "commit":
            subprocess.run(["git", "update-ref", "refs/heads/invadida", "HEAD"], cwd=root, check=True)

    monkeypatch.setattr(script, "run_git", hostile)
    base = sh(root, "rev-parse", "origin/develop")
    with pytest.raises(ValueError, match="alterou refs do repositório principal"):
        script.commit_task_submodules("run", {"id": "sub", "files": ["mod"]}, {}, base)
