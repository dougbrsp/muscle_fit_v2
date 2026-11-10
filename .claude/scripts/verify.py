#!/usr/bin/env python3
"""Gate local da skill /ship — exclusivamente testes de arquivos afetados.

Não executa serviços, Docker Compose, banco MySQL, suites integrais, push ou deploy.
Recebe testes obrigatórios de `.claude/local-state/verification-targets.json`.
Não substitui PR checks nem revisão. Não executa comandos fornecidos em JSON.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 105
# Suite de hooks: ~200-235 s com 189 testes (2026-11-10); limite externo de 900 s no ship.py.
HOOKS_TIMEOUT = 600


def run(argv: list[str], cwd: Path = ROOT, timeout: int = TIMEOUT) -> bool:
    print("[gate]", " ".join(argv), flush=True)
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print("[gate] não executado:", str(e), file=sys.stderr)
        return False
    output = (p.stdout or "") + (p.stderr or "")
    if output.strip():
        print("\n".join(output.splitlines()[-18:]), file=sys.stderr if p.returncode else sys.stdout)
    if p.returncode: print(f"[gate] exit={p.returncode}", file=sys.stderr)
    return p.returncode == 0


def git(*args: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=20)
        return (p.returncode == 0, p.stdout)
    except (OSError, subprocess.TimeoutExpired): return (False, '')


def changed_files() -> set[str] | None:
    ok, branch = git('branch', '--show-current')
    branch = branch.strip()
    if not ok or not branch or branch in {'main','master','develop'}:
        print('[gate] use branch de feature, separada de develop/main.', file=sys.stderr)
        return None
    ok, base = git('merge-base', 'HEAD', 'origin/develop')
    if not ok or not base:
        print('[gate] falta origin/develop no git local. Faça git fetch antes do gate.', file=sys.stderr)
        return None
    ok, names = git('diff', '--name-only', '-z', '--no-renames', base.strip(), '--')
    if not ok: return None
    changed = set(filter(None, names.split('\0')))
    ok, untracked = git('ls-files', '--others', '--exclude-standard', '-z')
    if not ok:
        return None
    changed.update(filter(None, untracked.split('\0')))
    return changed


def load_targets(path: Path) -> dict[str,list[str]] | None:
    try: data=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as e:
        print(f'[gate] configure {path} com alvos reais: {e}', file=sys.stderr)
        return None
    if not isinstance(data,dict): return None
    res={}
    for key, prefix, suffixes in [('backend_tests','api/tests/unit/',('.py',)),
                                  ('frontend_tests','frontend/',('.test.ts','.test.tsx','.spec.ts','.spec.tsx','.test.js','.test.jsx','.spec.js','.spec.jsx'))]:
        v=data.get(key)
        if not isinstance(v,list) or len(v)>30 or any(not isinstance(x,str) for x in v):
            print(f'[gate] {key} deve ser uma lista de caminhos (max 30).',file=sys.stderr);return None
        paths=[]
        for x in v:
            p=Path(x)
            if p.is_absolute() or '..' in p.parts or not x.replace('\\','/').startswith(prefix) or not x.endswith(suffixes) or not (ROOT/p).is_file() or not (ROOT/p).resolve().is_relative_to(ROOT.resolve()):
                print(f'[gate] teste não permitido/inexistente: {x}',file=sys.stderr);return None
            paths.append(p.as_posix())
        res[key]=sorted(set(paths))
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', type=Path,
                        default=ROOT/'.claude'/'local-state'/'verification-targets.json')
    args = parser.parse_args()
    changed=changed_files()
    if changed is None: return 2
    if not changed:
        print('[gate] nenhum diff detectado em relação a origin/develop; não há entrega a validar.',file=sys.stderr)
        return 2
    targets=load_targets(args.targets)
    if targets is None: return 2
    backend=[x for x in changed if x.startswith('api/') and x.endswith('.py')
             and not x.startswith('api/tests/')]
    frontend=[x for x in changed if x.startswith('frontend/') and
              x.endswith(('.ts','.tsx','.js','.jsx')) and
              not x.endswith(('.test.ts','.test.tsx','.spec.ts','.spec.tsx','.test.js','.test.jsx','.spec.js','.spec.jsx'))]
    ok=True
    # Changed backend: fail closed if no targeted tests. We never execute entire suite.
    if backend and not targets['backend_tests']:
        print('[gate] backend modificado sem backend_tests definidos.',file=sys.stderr);ok=False
    if frontend and not targets['frontend_tests']:
        print('[gate] frontend modificado sem frontend_tests definidos.',file=sys.stderr);ok=False
    # .claude changes -> run preservation regression tests for safety hooks.
    if any(x.startswith('.claude/') for x in changed):
        if not run([sys.executable,'-m','pytest','-q','--tb=line','-p','no:cacheprovider',
                    '.claude/hooks/tests'],timeout=HOOKS_TIMEOUT):ok=False
    if backend:
        if shutil.which('ruff') is None:
            print('[gate] ruff não instalado; instale-o ou rode no ambiente do projeto.',file=sys.stderr);ok=False
        else:
            args=['ruff','check','--no-cache']
            if (ROOT/'api'/'ruff.toml').is_file():args+=['--config','api/ruff.toml']
            remaining = sorted(x for x in backend if (ROOT/x).is_file())
            if remaining and not run(args + remaining):ok=False
        for f in backend:
            if (ROOT/f).is_file() and not run([sys.executable,'-m','py_compile',f]):ok=False
        if (ROOT/'api/tests/unit/test_guarda_fontes_da_verdade.py').is_file():
            if not run([sys.executable,'-m','pytest','-q','--tb=line','-p','no:cacheprovider',
                        'tests/unit/test_guarda_fontes_da_verdade.py'],ROOT/'api',180):ok=False
    if targets['backend_tests']:
        tests=[Path(x).relative_to('api').as_posix() for x in targets['backend_tests']]
        if not run([sys.executable,'-m','pytest','-q','--tb=line','-p','no:cacheprovider',*tests],ROOT/'api',90):ok=False
    if frontend or targets['frontend_tests']:
        if not (ROOT/'frontend'/'node_modules').is_dir():
            print('[gate] frontend/node_modules ausente; npm install no checkout ativo antes de validar.',file=sys.stderr);ok=False
        else:
            npx=shutil.which('npx.cmd' if sys.platform=='win32' else 'npx')
            if not npx:print('[gate] npx ausente.',file=sys.stderr);ok=False
            else:
                if not run([npx,'--no-install','tsc','--noEmit'],ROOT/'frontend',120):ok=False
                if targets['frontend_tests']:
                    tests=[Path(x).relative_to('frontend').as_posix() for x in targets['frontend_tests']]
                    if not run([npx,'--no-install','vitest','run',*tests],ROOT/'frontend',120):ok=False
    # Infrastructure schema is checked in pipeline; cheap local terraform syntax/format check.
    tfs=[x for x in changed if x.startswith('infra/') and x.endswith('.tf') and (ROOT/x).is_file()]
    if tfs:
        if not shutil.which('terraform'):
            print('[gate] terraform não instalado (infra alterada).',file=sys.stderr);ok=False
        elif not run(['terraform','fmt','-check',*sorted(tfs)],ROOT,60):ok=False
    print('[gate] OK: verificações locais direcionadas' if ok else '[gate] FALHOU. Corrija antes da publicação.')
    return 0 if ok else 2

if __name__=='__main__': raise SystemExit(main())
