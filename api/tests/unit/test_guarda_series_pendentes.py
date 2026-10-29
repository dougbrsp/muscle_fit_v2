"""Guarda: nenhuma leitura de `exercise_sets` vê série com reps PENDENTES
(Modo Intensivo, migração 092).

Regra (decisão do usuário de 2026-10-24): a série concluída com um toque no
Modo Intensivo é gravada com `reps` NULL e `reps_pending = 1`. Até a
confirmação (PATCH da série) TODOS os motores e leituras analíticas a ignoram:
volume, e1RM/recorde, comparabilidade, `technique_accounting`, fadiga,
ativação, progressão, relatórios e os agregados de `exercises`. Sem isso o
fallback de dose (`e1rm.dose_inputs`, 8 reps quando falta) contaria uma série
cujas reps ninguém sabe.

Predicado ÚNICO: `app.set_types.sql_set_confirmed` (SQL) e
`is_pending_set`/`confirmed_sets` (Python). Mapa:
`docs/motores/FONTES_DA_VERDADE.md`.

Como funciona: varre `api/app/**/*.py` pela AST. Todo literal de string (com
as concatenações implícitas já unidas pelo parser; docstrings fora) que LÊ
`exercise_sets` (`FROM`/`JOIN exercise_sets`, sem ser DELETE/INSERT/UPDATE)
precisa interpolar `sql_set_confirmed(...)` - direto ou por uma constante de
módulo atribuída a partir dele - ao menos uma vez por ocorrência da tabela.
As exceções são leituras OPERACIONAIS (idempotência, numeração, edição,
listagem que mostra a série pendente ao usuário, posição ordinal da série),
cada uma em `EXCECOES` com o motivo e a contagem exata de literais. A lista
só encolhe: exceção que sumiu do código também falha.

Rodar: cd api && python -m pytest tests/unit/test_guarda_series_pendentes.py
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
APP = RAIZ / "api" / "app"
PREDICADO = "sql_set_confirmed"

_LEITURA = re.compile(r"\b(?:FROM|JOIN)\s+`?exercise_sets`?(?![A-Za-z0-9_])", re.IGNORECASE)
_ESCRITA = re.compile(r"^\s*(?:DELETE|INSERT|UPDATE|REPLACE)\b", re.IGNORECASE)

# (arquivo relativo a api/app, função ou constante de módulo) -> (nº de literais, motivo).
EXCECOES: dict[tuple[str, str], tuple[int, str]] = {
    ("workout_sessions.py", "SESSION_SAVED_SETS_SQL"): (
        1,
        "trava do deload numa sessão já iniciada (M97 F3, `deload.can_toggle_deload`): QUALQUER série "
        "gravada, pendente ou não, trava a troca; não lê reps/carga",
    ),
    ("workout_sessions.py", "_find_set_by_client_key"): (
        1,
        "idempotência por client_key: a repetição devolve a série gravada, pendente ou não",
    ),
    ("workout_sessions.py", "add_session_set"): (
        2,
        "numeração da série nova (MAX(set_number)) e âncora estrutural da aplicação de técnica: a série "
        "pendente ocupa o seu número/posição",
    ),
    ("workout_sessions.py", "_evaluate_set"): (
        1,
        "posição ORDINAL da série entre as de trabalho (rampa planejada): conta a pendente, não lê reps/carga",
    ),
    ("workout_sessions.py", "update_session_set"): (1, "o PATCH lê justamente a série a confirmar/corrigir"),
    ("services.py", "update_exercise_log"): (
        1,
        "edição pelo PUT /exercises/logs recria as séries: lê todas (inclusive pendentes) para preservar "
        "as colunas omitidas e a pendência (exercise_set_update_merge)",
    ),
}


@lru_cache(maxsize=None)
def _arquivos() -> tuple[Path, ...]:
    return tuple(sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts))


def _texto_do_literal(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return ""


def _nomes_do_predicado(tree: ast.Module) -> set[str]:
    """Nomes (constante de módulo ou variável local) atribuídos a partir de
    `sql_set_confirmed(...)` - ex.: o filtro condicional de um schema detectado."""
    nomes: set[str] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
            if PREDICADO in ast.unparse(stmt.value):
                alvos = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                nomes.update(t.id for t in alvos if isinstance(t, ast.Name))
    return nomes


def _usos_do_predicado(node: ast.AST, nomes: set[str]) -> int:
    if not isinstance(node, ast.JoinedStr):
        return 0
    usos = 0
    for valor in node.values:
        if isinstance(valor, ast.FormattedValue):
            codigo = ast.unparse(valor.value)
            if PREDICADO in codigo or (isinstance(valor.value, ast.Name) and valor.value.id in nomes):
                usos += 1
    return usos


def _docstrings(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            primeiro = node.body[0]
            if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant):
                ids.add(id(primeiro.value))
    return ids


def _escopos(tree: ast.Module) -> dict[int, str]:
    """id do nó -> função mais externa, `Classe.metodo` ou constante de módulo que o contém."""
    donos: dict[int, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            for node in ast.walk(stmt):
                donos[id(node)] = stmt.name
            for item in stmt.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(item):
                        donos[id(node)] = f"{stmt.name}.{item.name}"
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nome = stmt.name
        elif isinstance(stmt, ast.Assign) and stmt.targets and isinstance(stmt.targets[0], ast.Name):
            nome = stmt.targets[0].id
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            nome = stmt.target.id
        else:
            nome = "<modulo>"
        for node in ast.walk(stmt):
            donos[id(node)] = nome
    return donos


@lru_cache(maxsize=None)
def _leituras() -> tuple[tuple[str, str, int, int, int], ...]:
    """(arquivo, escopo, linha, ocorrências da tabela, usos do predicado) de cada literal que lê exercise_sets."""
    achados = []
    for caminho in _arquivos():
        tree = ast.parse(caminho.read_text(encoding="utf-8"))
        rel = caminho.relative_to(APP).as_posix()
        docs = _docstrings(tree)
        escopos = _escopos(tree)
        nomes = _nomes_do_predicado(tree)
        dentro_de_fstring: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                dentro_de_fstring.update(id(v) for v in node.values)
        for node in ast.walk(tree):
            if id(node) in docs or id(node) in dentro_de_fstring:
                continue
            texto = _texto_do_literal(node)
            if not texto or _ESCRITA.match(texto):
                continue
            ocorrencias = len(_LEITURA.findall(texto))
            if ocorrencias:
                achados.append(
                    (rel, escopos.get(id(node), "<modulo>"), node.lineno, ocorrencias, _usos_do_predicado(node, nomes))
                )
    return tuple(achados)


def test_toda_leitura_de_series_exclui_as_pendentes():
    violacoes = [
        f"api/app/{rel}:{linha} ({escopo}): {ocorrencias} leitura(s) de exercise_sets, {usos} predicado(s)"
        for rel, escopo, linha, ocorrencias, usos in _leituras()
        if usos < ocorrencias and (rel, escopo) not in EXCECOES
    ]
    assert not violacoes, (
        "Leitura de exercise_sets sem `set_types.sql_set_confirmed` (a série com reps pendentes do Modo "
        "Intensivo vazaria para os motores). Interpole o predicado no ON/WHERE de cada ocorrência ou, se a "
        "leitura for operacional, registre em EXCECOES com o motivo:\n" + "\n".join(violacoes)
    )


def test_excecoes_existem_e_tem_a_contagem_exata():
    """Ratchet: a exceção que sumiu (ou ganhou/perdeu leitura) falha - a lista só encolhe."""
    sem_predicado = Counter((rel, escopo) for rel, escopo, _l, oc, usos in _leituras() if usos < oc)
    divergentes = {
        chave: (esperado, sem_predicado.get(chave, 0))
        for chave, (esperado, _motivo) in EXCECOES.items()
        if sem_predicado.get(chave, 0) != esperado
    }
    assert not divergentes, f"EXCECOES desatualizadas (esperado, encontrado): {divergentes}"


def test_todo_motivo_e_de_verdade():
    for chave, (_n, motivo) in EXCECOES.items():
        assert len(motivo) > 30, chave


def test_a_guarda_enxerga_as_leituras_conhecidas():
    """Sanidade do próprio scanner: acha leituras em arquivos sabidamente analíticos."""
    arquivos = {rel for rel, *_ in _leituras()}
    assert {"analytics.py", "intelligence/volume_service.py", "workout_sessions.py"} <= arquivos


def test_so_as_telas_pedem_as_series_pendentes():
    """`include_pending=True` (série pendente visível) só nas rotas que MOSTRAM a
    série ao usuário; todo consumidor analítico usa o padrão (sem pendentes)."""
    chamadas = set()
    for caminho in _arquivos():
        tree = ast.parse(caminho.read_text(encoding="utf-8"))
        escopos = _escopos(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(
                kw.arg == "include_pending" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                for kw in node.keywords
            ):
                chamadas.add((caminho.relative_to(APP).as_posix(), escopos.get(id(node), "<modulo>")))
    assert chamadas == {("main.py", "get_activity"), ("main.py", "get_exercise_logs")}


def test_predicado_e_o_do_dono():
    from app.set_types import confirmed_sets, is_pending_set, sql_set_confirmed

    assert sql_set_confirmed() == "es.reps_pending = 0"
    assert sql_set_confirmed("reps_pending") == "reps_pending = 0"
    assert is_pending_set({"reps_pending": 1}) and is_pending_set({"reps_pending": True})
    assert not is_pending_set({"reps_pending": 0}) and not is_pending_set({}) and not is_pending_set({"reps": 8})
    linhas = [{"reps": 8, "reps_pending": 0}, {"reps": None, "reps_pending": 1}, {"reps": 6}]
    assert confirmed_sets(linhas) == [linhas[0], linhas[2]]
