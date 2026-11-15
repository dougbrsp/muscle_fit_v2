#!/usr/bin/env python3
"""Grafo local do /ship. O Tech Lead despacha os agentes nativos do Claude Code."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".claude/local-state/ship"
AUTHORS = {"backend-engineer", "frontend-engineer", "fullstack", "ai_engineer",
           "aws", "zepp", "test-engineer", "docs-curator"}
REVIEWERS = ("code-reviewer", "guardiao-motores", "revisor-impacto-producao", "security-reviewer")
RISKS = {"motores", "producao", "seguranca"}
PROTECTED_BRANCHES = {"develop", "main", "master"}
MAX_TASKS = 6
MAX_REWORK = 5
MAX_GATES = 3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd, timeout=25,
                                   env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"}).decode("utf-8")


def lines_z(value):
    return set(filter(None, value.split("\0")))


def repo_identity():
    branch = git("branch", "--show-current").strip()
    require(branch and branch not in PROTECTED_BRANCHES, "Use branch de trabalho dedicada.")
    return branch, git("rev-parse", "origin/develop").strip()


def snapshot(base):
    paths = lines_z(git("diff", "--name-only", "-z", "--no-renames", base, "--"))
    paths |= lines_z(git("ls-files", "--others", "--exclude-standard", "-z"))
    result = {}
    for name in sorted(paths):
        file = ROOT / name
        require(file.parent.resolve().is_relative_to(ROOT), f"Caminho fora do checkout: {name}")
        if file.is_symlink():
            digest = "link:" + os.readlink(file)
        elif file.is_file():
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            digest = f"{file.stat().st_mode & 0o111}:{digest}"
        elif file.is_dir():
            require((file / ".git").exists(), f"Diretório inesperado no diff: {name}")
            require(not git("status", "--porcelain", cwd=file), f"Submódulo sujo: {name}")
            digest = "submodule:" + git("rev-parse", "HEAD", cwd=file).strip()
        else:
            digest = "absent"
        result[name] = digest
    return result


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def save_review_diff(base):
    # Keep a single read-only review input, including files not yet staged.
    opts = ["--no-ext-diff", "--no-textconv", "--binary", "--no-renames"]
    output = git("diff", *opts, base, "--")
    for name in sorted(lines_z(git("ls-files", "--others", "--exclude-standard", "-z"))):
        result = subprocess.run(["git", "diff", "--no-index", *opts, "--", "/dev/null", name],
                                cwd=ROOT, capture_output=True, timeout=25,
                                env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"})
        require(result.returncode == 1, f"Não foi possível gerar o diff de {name}.")
        output += result.stdout.decode("utf-8")
    (STATE / "review.diff").write_text(output, encoding="utf-8")


def task_path(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return False
    path = PurePosixPath(value)
    return (path.parts and path.parts[0].lower() != ".git" and
            not value.lower().startswith(".claude/local-state/") and
            path.as_posix() == value and not path.is_absolute() and
            ".." not in path.parts and not re.search(r'[\\:*?<>|"]', value))


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def gate_data(gate):
    require(isinstance(gate, dict) and nonempty(gate.get("question")) and nonempty(gate.get("impact"))
            and nonempty(gate.get("recommendation")), "Gate exige question, impact e recommendation.")
    options = gate.get("options")
    require(isinstance(options, dict) and 2 <= len(options) <= 4 and
            all(re.fullmatch(r"[a-z][a-z0-9-]{0,23}", key) and nonempty(value)
                for key, value in options.items()) and gate["recommendation"] in options,
            "Gate exige 2 a 4 opções identificadas e recomendação que exista.")


def validate_plan(plan):
    require(isinstance(plan, dict) and nonempty(plan.get("goal")), "Informe goal no plano.")
    tasks = plan.get("tasks")
    require(isinstance(tasks, list) and 1 <= len(tasks) <= MAX_TASKS, "Plano exige 1 a 6 tarefas.")
    owned = set()
    by_id = {}
    for task in tasks:
        require(isinstance(task, dict), "Cada tarefa deve ser um objeto.")
        ident = task.get("id", "")
        require(isinstance(ident, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,23}", ident)
                and ident not in by_id, "ID de tarefa inválido ou duplicado.")
        require(task.get("agent") in AUTHORS and nonempty(task.get("acceptance")),
                f"{ident}: informe agente autor e critério de aceite.")
        files = task.get("files")
        require(isinstance(files, list) and 1 <= len(files) <= 40 and all(task_path(p) for p in files),
                f"{ident}: informe de 1 a 40 caminhos exatos relativos ao Git.")
        names = {name.casefold() for name in files}
        require(len(names) == len(files) and not owned.intersection(names), f"{ident}: ownership sobreposto.")
        owned.update(names)
        deps = task.get("deps")
        require(isinstance(deps, list) and all(isinstance(dep, str) for dep in deps),
                f"{ident}: informe deps como lista de IDs.")
        by_id[ident] = task
    done = set()
    while len(done) < len(by_id):
        ready = {i for i, task in by_id.items() if i not in done and set(task["deps"]) <= done}
        require(ready, "O grafo contém ciclo ou dependência inexistente.")
        done.update(ready)
    risks = plan.get("risks", [])
    require(isinstance(risks, list) and all(isinstance(r, str) and r in RISKS for r in risks),
            "Riscos aceitos: motores, producao, seguranca.")
    if plan.get("gate") is not None:
        gate_data(plan["gate"])
    return by_id


def reviewers(plan, files):
    paths = [name.lower() for name in files]
    risks = set(plan.get("risks", []))
    if any(p.startswith(("api/app/domain/", "api/app/intelligence/", "api/app/generation/",
                         "api/app/periodization/", "api/privado", "docs/motores/")) for p in paths):
        risks.add("motores")
    if any(p.startswith(("infra/", ".github/workflows/", "api/db/migrations/")) or
           "dockerfile" in p or "lambda" in p or p.endswith(".sql") for p in paths):
        risks.add("producao")
    if any(p.startswith((".claude/", "infra/")) or
           re.search(r"(^|/)[^/]*(auth|iam|secret|permission|upload)[^/]*(/|$)", p) for p in paths):
        risks.add("seguranca")
    return [REVIEWERS[0]] + [role for risk, role in zip(("motores", "producao", "seguranca"),
                                                       REVIEWERS[1:]) if risk in risks]


def json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


@contextmanager
def locked():
    STATE.mkdir(parents=True, exist_ok=True)
    lock = STATE / "lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("Outro comando está ativo. Após uma interrupção, confirme isso antes de remover lock.") from None
    try:
        yield
    finally:
        lock.rmdir()


def wait_for_human(state, gate):
    gate_data(gate)
    require(len(state["decisions"]) < MAX_GATES, "Limite de 3 gates humanos por execução.")
    state["gate"] = {**gate, "id": uuid.uuid4().hex[:12]}
    state["phase"] = "waiting"


def confirmation_forms(state):
    forms = {}
    if state["phase"] == "waiting":
        gate = state["gate"]
        forms["gate"] = {"questions": [{
            "question": f"[ship:{gate['id']}] {gate['question']}",
            "header": "Decisão",
            "multiSelect": False,
            "options": [{"label": option, "description": text} for option, text in gate["options"].items()],
        }]}
    if state["active"] is None and state["phase"] not in {"blocked", "verified", "cancelled"}:
        forms["cancel"] = {"questions": [{
            "question": f"[ship-cancel:{state['id']}] Encerrar a execução do /ship?",
            "header": "Encerrar",
            "multiSelect": False,
            "options": [
                {"label": "encerrar", "description": "Encerra a execução e preserva os arquivos locais."},
                {"label": "continuar", "description": "Mantém a execução e o checkpoint atuais."},
            ],
        }]}
    return forms


def record_choice(state, option, **provenance):
    state["decisions"].append({**state["gate"], "selected": option, **provenance})
    state.update(gate=None, phase="execute")


def show(state, tasks):
    ready = []
    if state["phase"] == "execute" and state["active"] is None:
        ready = [i for i, task in tasks.items()
                 if i not in state["done"] and set(task["deps"]) <= set(state["done"])]
    data = {**state, "ready_tasks": ready, "confirmations": confirmation_forms(state)}
    if state["phase"] == "review":
        prefix = f'{state["id"]}-{state["round"]}-'
        data["receipts"] = [json_file(file) for file in sorted((STATE / "receipts").glob(prefix + "*.json"))]
    print(json.dumps(data, ensure_ascii=False, indent=2))


def run(args):
    state_path = STATE / "state.json"
    command = args.command
    if command == "start":
        require(not state_path.exists(), "Já existe execução. Consulte status ou arquive uma execução encerrada.")
        plan = json_file(args.file)
        tasks = validate_plan(plan)
        branch, base = repo_identity()
        require(git("rev-parse", "HEAD").strip() == base and not git("status", "--porcelain"),
                "Comece em checkout limpo, numa branch dedicada criada de origin/develop.")
        state = {"id": uuid.uuid4().hex[:12], "branch": branch, "base": base, "plan": plan,
                 "phase": "execute", "done": [], "active": None, "round": 0,
                 "known": {}, "reviews": {}, "decisions": [], "history": []}
        if plan.get("gate"):
            wait_for_human(state, plan["gate"])
    else:
        require(state_path.exists(), "Nenhuma execução neste checkout.")
        state = json_file(state_path)
        tasks = state["plan"]["tasks"]
        tasks = {task["id"]: task for task in tasks}
        if command == "status":
            show(state, tasks)
            return
        if command == "archive":
            require(state["phase"] in {"blocked", "verified", "cancelled"}, "Encerre a execução antes de arquivar.")
            state_path.replace(STATE / f'{state["id"]}.json')
            return
        if command != "cancel":
            require(repo_identity() == (state["branch"], state["base"]),
                    "Branch ou origin/develop mudou. Preserve o diff e replaneje em nova execução.")
            advance(state, tasks, args)
        else:
            require(sys.stdin.isatty(), "Cancele no terminal do usuário, após parar os agentes.")
            require(input(f'Confirme digitando o ID {state["id"]}: ').strip() == state["id"], "Cancelamento abortado.")
            state.update(phase="cancelled", active=None)
    write_json(state_path, state)
    show(state, tasks)


def clean_git_env():
    # Remove todas as variáveis GIT_* herdadas (GIT_DIR, GIT_CONFIG_*, GIT_AUTHOR_*...); a identidade vem da config.
    # Caminhos de tarefa são literais (colchetes de rotas dinâmicas não são curinga de pathspec).
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


# Config fixa: sem hooks, sem assinatura, sem fsmonitor (que executaria comando do repositório).
SAFE_GIT = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false"]


def git_out(*args, cwd, timeout=25):
    result = subprocess.run([*SAFE_GIT, *args], cwd=cwd, capture_output=True, encoding="utf-8",
                            env=clean_git_env(), timeout=timeout)
    require(result.returncode == 0, f"git {args[0]} falhou em {Path(cwd).name}: {result.stderr.strip()[-300:]}")
    return result.stdout.strip()


def run_git(*args, cwd, stdin=""):
    result = subprocess.run([*SAFE_GIT, *args], cwd=cwd, input=stdin, capture_output=True, encoding="utf-8",
                            env=clean_git_env(), timeout=60)
    require(result.returncode == 0, f"git {args[0]} falhou em {cwd.name}: {result.stderr.strip()[-300:]}")


def real(path):
    return os.path.normcase(os.path.realpath(path))


def git_try(*args, cwd):
    """Como git_out, mas devolve None em falha (a condição que depende dele vira recusa específica)."""
    try:
        result = subprocess.run([*SAFE_GIT, *args], cwd=cwd, capture_output=True, encoding="utf-8",
                                env=clean_git_env(), timeout=25)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def submodule_name(name, base):
    """Nome do submódulo cujo path é `name` no .gitmodules da BASE; ausente ou ambíguo recusa."""
    listed = git_try("config", "--blob", f"{base}:.gitmodules", "--get-regexp", r"^submodule\..*\.path$", cwd=ROOT)
    wanted = name.replace("\\", "/").rstrip("/")
    names = []
    for line in (listed or "").splitlines():
        key, _, value = line.partition(" ")
        if value.strip().rstrip("/") == wanted:
            names.append(key[len("submodule."):-len(".path")])
    require(len(names) == 1, f"Caminho {name}: nome do submódulo ausente ou ambíguo no .gitmodules da base.")
    return names[0]


def require_registered_submodule(name, path, base):
    """Só é submódulo commitável o gitlink da base, sem links no caminho, com gitfile para o gitdir exato
    <gitdir do principal>/modules/<nome>, sem commondir desviando e com core.worktree apontando para o caminho."""
    why = f"Caminho {name} tem .git mas não é submódulo registrado na base; o report não commita nele."
    entry = git_out("ls-tree", base, "--", name.rstrip("/"), cwd=ROOT)
    require(entry.startswith("160000 commit"), why + " (não é gitlink na base)")
    expected = os.path.normcase(os.path.join(os.path.realpath(ROOT), os.path.normpath(name)))
    require(real(path) == expected, why + " (caminho com link ou junção)")
    dotgit = path / ".git"
    require(not dotgit.is_dir(), why + " (repositório embutido: .git é diretório; não suportado)")
    require(dotgit.is_file() and not dotgit.is_symlink(), why + " (.git não é um gitfile)")
    # (d1) gitdir exato modules/<nome>, lido do gitfile e conferido com o que o git resolve
    main_gitdir = real(git_out("rev-parse", "--absolute-git-dir", cwd=ROOT))
    wanted_dir = real(os.path.join(main_gitdir, "modules", *submodule_name(name, base).split("/")))
    text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
    require(text.startswith("gitdir:"), why + " (gitfile inválido)")
    gitdir = real(os.path.join(path, text[len("gitdir:"):].strip()))
    seen = git_try("rev-parse", "--absolute-git-dir", cwd=path)
    require(gitdir == wanted_dir and seen is not None and real(seen) == wanted_dir,
            why + " (gitdir não é exatamente modules/<nome> do repositório principal)")
    # (d2) sem commondir desviando refs/objetos para outro repositório
    require(not os.path.lexists(os.path.join(gitdir, "commondir")), why + " (commondir presente no gitdir)")
    # (d4) core.worktree ausente ou igual ao caminho
    worktree = git_try("config", "--local", "--get", "core.worktree", cwd=path)
    require(not worktree or real(os.path.join(gitdir, worktree)) == expected,
            why + " (core.worktree diverge do caminho do submódulo)")
    # (c) raiz do repositório é o próprio caminho
    top = git_try("rev-parse", "--show-toplevel", cwd=path)
    require(top is not None and real(top) == expected, why + " (a raiz do repositório não é o próprio caminho)")
    common = git_try("rev-parse", "--git-common-dir", cwd=path)
    require(common is not None and real(os.path.join(path, common)) == gitdir,
            why + " (git-common-dir difere do gitdir)")


def protected_heads(base, skip):
    """HEAD e todas as refs do principal, mais o HEAD dos demais submódulos registrados (fora da tarefa)."""
    main = (git_out("rev-parse", "HEAD", cwd=ROOT), git_out("for-each-ref", cwd=ROOT))
    others = {}
    for line in git_out("ls-tree", "-r", base, cwd=ROOT).splitlines():
        if line.startswith("160000 commit"):
            other = line.split("\t", 1)[1]
            if other not in skip and (ROOT / other / ".git").exists():
                others[other] = git_try("rev-parse", "HEAD", cwd=ROOT / other)
    return main, others


def dirty_submodules(files, base):
    found = []
    for name in files:
        path = ROOT / name
        if path.is_dir() and (path / ".git").exists() and real(path) != real(ROOT):
            require_registered_submodule(name, path, base)
            if git_out("status", "--porcelain", cwd=path):
                found.append((name, path))
    return found


def commit_task_submodules(run_id, task, data, base):
    message = data.get("submodule_commit_message")
    require(message is None or nonempty(message), "submodule_commit_message deve ser texto não vazio.")
    require(message is None or all(c in "\n\t" or (c >= " " and c != "\x7f" and not "\x80" <= c <= "\x9f")
                                   for c in message),
            "submodule_commit_message não pode ter NUL nem caracteres de controle além de nova linha e tab.")
    message = message or f"chore: alterações da tarefa {task['id']} (/ship {run_id})"
    dirty = dirty_submodules(task["files"], base)
    for name, path in dirty:  # todas as checagens antes do primeiro commit
        branch = git_out("branch", "--show-current", cwd=path)
        require(branch and branch not in PROTECTED_BRANCHES,
                f"Submódulo {name} em HEAD destacado ou branch protegida; use uma branch de trabalho própria.")
    if not dirty:
        return
    before = protected_heads(base, {name for name, _ in dirty})
    for name, path in dirty:
        require_registered_submodule(name, path, base)  # revalida logo antes de escrever
        run_git("add", "-A", cwd=path)
        run_git("commit", "-F", "-", cwd=path, stdin=message)
    require(protected_heads(base, {name for name, _ in dirty}) == before,
            "o commit do submódulo alterou refs do repositório principal; investigue antes de continuar")


def advance(state, tasks, args):
    command, phase = args.command, state["phase"]
    base = state["base"]
    data = None
    if command == "report":
        require(phase == "execute" and state["active"] == args.id, "Resultado sem tarefa ativa correspondente.")
        data = json_file(args.file)
        require(isinstance(data, dict) and data.get("status") in {"done", "blocked"}
                and nonempty(data.get("evidence")), "Resultado exige status (done ou blocked) e evidence.")
        commit_task_submodules(state["id"], tasks[args.id], data, base)
    current = snapshot(base)
    if command != "report":
        require(current == state["known"], "Diff divergiu do checkpoint. Investigue antes de continuar.")
    if command == "gate":
        require(phase == "execute" and state["active"] is None, "Gate só entre tarefas, antes da revisão.")
        wait_for_human(state, json_file(args.file))
    elif command == "decide":
        gate = state.get("gate")
        require(phase == "waiting" and gate and args.id == gate["id"] and args.option in gate["options"],
                "Gate ou opção inválida para a execução pendente.")
        require(sys.stdin.isatty(), "Responda em um terminal interativo do usuário.")
        require(input(f'Confirme digitando o ID {args.id}: ').strip() == args.id, "Decisão cancelada.")
        record_choice(state, args.option, source="terminal")
    elif command == "claim":
        require(phase == "execute" and state["active"] is None, "Aguarde a fase de execução sem autor ativo.")
        task = tasks.get(args.id)
        require(task and args.id not in state["done"] and set(task["deps"]) <= set(state["done"]),
                "Tarefa indisponível ou dependências incompletas.")
        state["active"] = args.id
    elif command == "report":
        touched = {key for key in current.keys() | state["known"].keys()
                   if current.get(key) != state["known"].get(key)}
        outside = touched - set(tasks[args.id]["files"])
        state["history"].append({"task": args.id, "round": state["round"], **data, "files": sorted(touched)})
        state["known"] = current
        state["active"] = None
        if outside or data["status"] == "blocked":
            state["phase"] = "blocked"
            state["reason"] = f"Escrita fora do escopo: {sorted(outside)}" if outside else data["evidence"]
        else:
            state["done"].append(args.id)
    elif command == "review":
        require(phase == "execute" and set(state["done"]) == set(tasks) and current,
                "Revisão exige todas as tarefas concluídas e diff não vazio.")
        if fingerprint(current) == state.get("prior_review"):
            state.update(phase="blocked", reason="Correção sem mudança no diff desde a rodada anterior.")
        else:
            save_review_diff(base)
            require(snapshot(base) == current, "O diff mudou durante a preparação da revisão.")
            state.update(phase="review", required=reviewers(state["plan"], current),
                         revision=fingerprint(current), reviews={})
    elif command == "verdict":
        require(phase == "review" and args.role in state["required"] and args.role not in state["reviews"],
                "Revisor não solicitado ou parecer duplicado.")
        data = json_file(args.file)
        require(isinstance(data, dict) and data.get("revision") == state["revision"] and
                data.get("status") in {"pass", "changes", "blocked"} and nonempty(data.get("evidence")),
                "Parecer exige revision do diff, status (pass/changes/blocked) e evidence.")
        agent_id = data.get("agent_id", "")
        require(isinstance(agent_id, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", agent_id),
                "Parecer exige agent_id de recibo observado em status.")
        receipt = STATE / "receipts" / f'{state["id"]}-{state["round"]}-{args.role}-{agent_id}.json'
        require(receipt.is_file() and json_file(receipt) == {
            "role": args.role, "agent_id": agent_id, "revision": state["revision"]
        }, "Parecer sem recibo nativo do revisor para esta rodada.")
        state["reviews"][args.role] = data
        if data["status"] == "blocked":
            state.update(phase="blocked", reason=data["evidence"])
    elif command == "verify":
        require(phase in {"review", "verify_failed"} and all(state["reviews"].get(role, {}).get("status") == "pass"
                                         for role in state["required"]), "Falta aprovação de revisor obrigatório.")
        attempt = state.get("verify", {}).get("attempt", 0) + 1
        require(attempt <= 2, "Uma repetição de verificação por rodada. Corrija a causa ou encerre.")
        if attempt == 2:
            state["history"].append({"verify": state["verify"]})
        targets = STATE / "targets.json"
        write_json(targets, json_file(args.file))
        log = STATE / f'{state["id"]}-verify-{state["round"]}-{attempt}.log'
        with log.open("wb") as output:
            try:
                result = subprocess.run([sys.executable, str(ROOT / ".claude/scripts/verify.py"),
                                         "--targets", str(targets)], cwd=ROOT, timeout=900,
                                        stdout=output, stderr=subprocess.STDOUT)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        state["verify"] = {"attempt": attempt, "exit": code, "log": log.relative_to(ROOT).as_posix()}
        if snapshot(base) != current:
            state.update(phase="blocked", reason="O verificador ou outro processo alterou o diff revisado.")
        else:
            state["phase"] = "verified" if code == 0 else "verify_failed"
    elif command == "rework":
        require(phase == "verify_failed" or (phase == "review" and
                set(state["reviews"]) == set(state["required"]) and
                any(r["status"] == "changes" for r in state["reviews"].values())),
                "Retrabalho exige pareceres completos com achado ou falha de verificação.")
        require(set(args.tasks) <= set(tasks), "IDs de retrabalho desconhecidos.")
        if state["round"] >= MAX_REWORK:
            state.update(phase="blocked", reason=f"Limite de {MAX_REWORK} rodadas de correção atingido.")
        else:
            state["history"].append({"review": state["reviews"], "verify": state.pop("verify", None),
                                     "rework": args.tasks})
            state["prior_review"] = state.pop("revision")
            state["reviews"] = {}
            state["done"] = [i for i in state["done"] if i not in args.tasks]
            state["round"] += 1
            state["phase"] = "execute"
    elif command == "ready":
        require(phase == "verified" and fingerprint(current) == state["revision"],
                "Somente a revisão exata aprovada e testada está pronta para publicar.")
    else:
        raise ValueError("Comando desconhecido.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "gate", "verify"):
        sub.add_parser(name).add_argument("file")
    for name in ("status", "review", "ready", "cancel", "archive"):
        sub.add_parser(name)
    sub.add_parser("claim").add_argument("id")
    for name, first, second in (("report", "id", "file"), ("verdict", "role", "file"),
                                ("decide", "id", "option")):
        command = sub.add_parser(name)
        command.add_argument(first)
        command.add_argument(second)
    sub.add_parser("rework").add_argument("tasks", nargs="+")
    args = parser.parse_args()
    try:
        with locked():
            run(args)
    except (OSError, ValueError, TypeError, EOFError, subprocess.SubprocessError) as exc:
        print(f"[ship] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
