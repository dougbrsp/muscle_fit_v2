"""Hook PostToolUse (Edit|Write|MultiEdit): checagem rapida do .py editado em api/.

1. `ruff check <arquivo>` com o `api/ruff.toml` (cwd = `api/` do MESMO checkout do
   arquivo: a raiz e derivada do caminho do arquivo, nao de CLAUDE_PROJECT_DIR,
   para funcionar em worktrees). Sem ruff no PATH: pula em silencio.
2. Se o arquivo pertence a um dominio de motor, roda SO a guarda
   `tests/unit/test_guarda_fontes_da_verdade.py` (~25 s, sem banco).
   "Dominio de motor" = o arquivo e citado no mapa `docs/motores/FONTES_DA_VERDADE.md`
   (todo token entre crases terminado em `.py`, comparado como sufixo do caminho:
   `domain/e1rm.py`, `volume_service.py`...), lido EM TEMPO DE EXECUCAO para nao
   duplicar a lista; ou e o proprio teste da guarda (onde mora a ALLOWLIST).
   Atualizou o mapa com um dono novo => o hook passa a cobri-lo sem mudar aqui.

Falha de ruff ou da guarda => exit 2 com as ultimas ~30 linhas em stderr (o Claude
corrige). Timeout de 60 s por subprocesso. Qualquer erro interno, dependencia
ausente ou timeout => exit 0 com aviso (nunca trava o trabalho).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 60
LINHAS_SAIDA = 30
MAPA = "docs/motores/FONTES_DA_VERDADE.md"
GUARDA = "tests/unit/test_guarda_fontes_da_verdade.py"
_TOKEN_PY = re.compile(r"`([A-Za-z0-9_./-]+\.py)(?:[:\s][^`]*)?`")


def _local(caminho: str) -> Path:
    p = caminho.replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])(/|$)", p)
    if m and sys.platform == "win32":
        p = f"{m.group(1)}:/{p[3:]}"
    return Path(p)


def raiz_api(arquivo: Path) -> Path | None:
    """O diretorio `api/` (com ruff.toml ou pytest.ini) que contem o arquivo."""
    for d in arquivo.parents:
        if d.name == "api" and ((d / "ruff.toml").is_file() or (d / "pytest.ini").is_file()):
            return d
    return None


def sufixos_do_mapa(texto: str) -> set[str]:
    sufixos: set[str] = set()
    for tok in _TOKEN_PY.findall(texto):
        nome = tok.rsplit("/", 1)[-1]
        if nome.startswith("test_"):
            continue  # testes citados no mapa nao sao dominio; a guarda entra a parte
        sufixos.add(tok.lstrip("./"))
    return sufixos


def e_dominio_de_motor(rel_repo: str, sufixos: set[str]) -> bool:
    """`rel_repo` = caminho relativo a raiz do repo, com `/` (ex.: api/app/domain/e1rm.py)."""
    if rel_repo == f"api/{GUARDA}":
        return True
    return any(rel_repo == s or rel_repo.endswith("/" + s) for s in sufixos)


def _cauda(texto: str) -> str:
    linhas = [ln for ln in texto.splitlines() if ln.strip()]
    return "\n".join(linhas[-LINHAS_SAIDA:])


def _roda(cmd: list[str], cwd: Path) -> tuple[int | None, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, f"timeout de {TIMEOUT_S}s"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def verificar(arquivo: Path) -> tuple[list[str], list[str]]:
    """Devolve (falhas, avisos)."""
    falhas: list[str] = []
    avisos: list[str] = []
    if arquivo.suffix != ".py" or not arquivo.is_file():
        return falhas, avisos
    api = raiz_api(arquivo.resolve())
    if api is None:
        return falhas, avisos
    rel_api = arquivo.resolve().relative_to(api).as_posix()
    if rel_api.startswith("vendor/"):
        return falhas, avisos
    repo = api.parent

    ruff = shutil.which("ruff")
    if ruff:
        cmd = [ruff, "check", "--output-format", "concise", "--no-cache"]
        if (api / "ruff.toml").is_file():
            cmd += ["--config", "ruff.toml"]
        rc, saida = _roda(cmd + [rel_api], api)
        if rc == 1:
            falhas.append(f"ruff check falhou em api/{rel_api} (api/ruff.toml):\n{_cauda(saida)}")
        elif rc not in (0, 1):
            avisos.append(f"ruff nao rodou ({saida.strip()[:200]})")

    mapa = repo / MAPA
    try:
        sufixos = sufixos_do_mapa(mapa.read_text(encoding="utf-8"))
    except OSError:
        sufixos = set()
        avisos.append(f"mapa {MAPA} nao encontrado; guarda dos motores nao verificada")
    if e_dominio_de_motor(f"api/{rel_api}", sufixos):
        if not (api / GUARDA).is_file():
            avisos.append("teste da guarda nao encontrado")
        elif importlib.util.find_spec("pytest") is None:
            avisos.append("pytest nao instalado neste Python; guarda dos motores pulada")
        else:
            rc, saida = _roda(
                [sys.executable, "-m", "pytest", GUARDA, "-q", "-x", "-p", "no:cacheprovider"], api
            )
            if rc == 1:
                falhas.append(
                    "guarda das fontes da verdade falhou (api/" + GUARDA + "; mapa " + MAPA + "). "
                    "Regra nova vai NO motor dono; a allowlist so encolhe:\n" + _cauda(saida)
                )
            elif rc != 0:
                avisos.append(f"guarda dos motores nao rodou (rc={rc}): {_cauda(saida)[-300:]}")
    return falhas, avisos


def main() -> int:
    try:
        dados = json.loads(sys.stdin.read() or "{}")
        entrada = dados.get("tool_input") or {}
        caminho = entrada.get("file_path") if isinstance(entrada, dict) else None
        if not isinstance(caminho, str) or not caminho.endswith(".py"):
            return 0
        arquivo = _local(caminho)
        if not arquivo.is_absolute() and isinstance(dados.get("cwd"), str):
            arquivo = _local(dados["cwd"]) / arquivo
        falhas, avisos = verificar(arquivo)
    except Exception:
        return 0
    for a in avisos:
        sys.stderr.write(f"[pos_edicao_python] aviso: {a}\n")
    if falhas:
        sys.stderr.write("\n\n".join(falhas) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
