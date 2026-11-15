"""Guarda estatica contra checagem de papel fora de `app.access` (ADR 0011, lote 2).

Depois da troca de guards por `require_permission`, a decisao de acesso e das
concessoes dos grupos, nao do papel (`users.role`). Este teste le por AST todos os
`api/app/**/*.py` (EXCETO `app/security.py`, dono da `Identity` e dos guards
legados, e `app/access/**`) e conta, por arquivo:

1. acessos ao atributo `.role` em objetos de identidade. Heuristica (`_e_identidade`):
   - nome `identity` ou terminado em `_identity` (`session_identity`, `device_identity`);
   - nome de parametro/variavel anotado `Identity` (`x: Identity`, `Optional[Identity]`,
     `"Identity"`, `security.Identity`) na funcao envolvente;
   - atributo encadeado cujo ultimo nome e `identity`/`*_identity`
     (`request.state.identity.role`);
2. chamadas ou definicoes de `is_admin`, `is_staff`, `is_admin_or_pt` e
   `_require_admin_identity` (por nome simples ou atributo, ex. `security.is_admin(...)`).

O que ESCAPA (limite assumido): alias (`me = identity; me.role`), objeto com outro
nome nao anotado (`actor.role`, `user.role`), `getattr(identity, "role")`, linha de
banco (`row["role"]`), filtro de papel em SQL, guards passados por referencia sem
chamada (`Depends(require_admin)`), parametro `identity` nao anotado com nome
diferente e codigo gerado dinamicamente. A guarda e um ratchet contra o padrao
mais comum, nao uma prova de ausencia de checagem de papel.

Ratchet por arquivo em `access_identity_role_allowlist.json`: contagem viva MAIOR
que a registrada falha; MENOR falha pedindo para baixar o numero (assim o teto so
encolhe); `teto_total` e a soma registrada. Arquivo fora da allowlist com ocorrencia
falha com orientacao para usar `app.access`. `users.py` nao aparece (zero).

Rodar: cd api && python -m pytest tests/unit/test_access_guarda_identity_role.py
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = API_ROOT / "app"
ALLOWLIST_PATH = Path(__file__).with_name("access_identity_role_allowlist.json")

FUNCOES_DE_PAPEL = frozenset({"is_admin", "is_staff", "is_admin_or_pt", "_require_admin_identity"})
_EXCLUIDOS_ARQUIVO = {"security.py"}


def _excluido(path: Path) -> bool:
    rel = path.relative_to(APP_DIR)
    return rel.as_posix() in _EXCLUIDOS_ARQUIVO or rel.parts[0] == "access"


def _nome_identidade(nome: str) -> bool:
    return nome == "identity" or nome.endswith("_identity")


def _anotacao_e_identity(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "Identity" in node.value
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "Identity":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "Identity":
            return True
    return False


class _Contador(ast.NodeVisitor):
    def __init__(self) -> None:
        self.role = 0
        self.funcoes = 0
        self._anotados: list[set[str]] = [set()]

    def _funcao(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name in FUNCOES_DE_PAPEL:
            self.funcoes += 1
        args = node.args
        todos = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            todos.append(args.vararg)
        if args.kwarg:
            todos.append(args.kwarg)
        anotados = {a.arg for a in todos if _anotacao_e_identity(a.annotation)}
        self._anotados.append(self._anotados[-1] | anotados)
        self.generic_visit(node)
        self._anotados.pop()

    visit_FunctionDef = _funcao
    visit_AsyncFunctionDef = _funcao

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and _anotacao_e_identity(node.annotation):
            self._anotados[-1].add(node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        alvo = node.func
        nome = alvo.id if isinstance(alvo, ast.Name) else getattr(alvo, "attr", None)
        if nome in FUNCOES_DE_PAPEL:
            self.funcoes += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "role" and self._e_identidade(node.value):
            self.role += 1
        self.generic_visit(node)

    def _e_identidade(self, valor: ast.AST) -> bool:
        if isinstance(valor, ast.Name):
            return _nome_identidade(valor.id) or valor.id in self._anotados[-1]
        if isinstance(valor, ast.Attribute):
            return _nome_identidade(valor.attr)
        return False


def contar_ocorrencias(source: str) -> int:
    """Total de ocorrencias (acessos `.role` em identidade + chamadas/definicoes das funcoes de papel)."""
    contador = _Contador()
    contador.visit(ast.parse(source))
    return contador.role + contador.funcoes


def contar_detalhado(source: str) -> tuple[int, int]:
    contador = _Contador()
    contador.visit(ast.parse(source))
    return contador.role, contador.funcoes


def contagem_viva() -> dict[str, int]:
    """`caminho relativo a api/ (posix) -> ocorrencias`, so arquivos com ocorrencia."""
    vivo: dict[str, int] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if _excluido(path):
            continue
        total = contar_ocorrencias(path.read_text(encoding="utf-8"))
        if total:
            vivo[path.relative_to(API_ROOT).as_posix()] = total
    return vivo


def _allowlist() -> dict:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def comparar(vivo: dict[str, int], allowlist: dict) -> list[str]:
    """Violacoes do ratchet (lista vazia = verde)."""
    registrado: dict[str, int] = allowlist["arquivos"]
    problemas: list[str] = []
    for arquivo, n in sorted(vivo.items()):
        if arquivo not in registrado:
            problemas.append(
                f"{arquivo} tem {n} checagem(ns) de papel (.role em identidade, is_admin, is_staff, "
                "is_admin_or_pt ou _require_admin_identity) e nao esta na allowlist: decida acesso por "
                "`require_permission(recurso, acao)` ou `authorize_target`/`authorize_self_or_all` de "
                "`app.access`, nao pelo papel; NAO adicione o arquivo ao JSON."
            )
        elif n > registrado[arquivo]:
            problemas.append(
                f"{arquivo}: {n} ocorrencias, acima das {registrado[arquivo]} registradas. "
                "Use `app.access` em vez de checar o papel; a allowlist so encolhe."
            )
        elif n < registrado[arquivo]:
            problemas.append(
                f"{arquivo}: {n} ocorrencias, abaixo das {registrado[arquivo]} registradas: "
                f"baixe o numero no JSON para {n} (e o teto_total)."
            )
    for arquivo in sorted(set(registrado) - set(vivo)):
        problemas.append(
            f"{arquivo} esta na allowlist com {registrado[arquivo]} mas ficou com 0 ocorrencias (ou "
            "deixou de existir): remova a entrada do JSON e baixe o teto_total."
        )
    soma = sum(registrado.values())
    if allowlist["teto_total"] != soma:
        problemas.append(
            f"teto_total ({allowlist['teto_total']}) difere da soma das entradas ({soma}): ajuste o JSON."
        )
    return problemas


# ---------------------------------------------------------------------------
# Guarda real
# ---------------------------------------------------------------------------


def test_guarda_checagens_de_papel_nao_crescem_e_o_json_acompanha_o_codigo():
    problemas = comparar(contagem_viva(), _allowlist())
    assert not problemas, "Guarda de checagem de papel:\n" + "\n".join(problemas[:25])


def test_users_py_e_security_e_access_nao_entram_na_contagem():
    vivo = contagem_viva()
    assert "app/users.py" not in vivo and "app/users.py" not in _allowlist()["arquivos"]
    assert not [a for a in vivo if a.startswith("app/access/") or a == "app/security.py"]
    assert not [a for a in _allowlist()["arquivos"] if a.startswith("app/access/") or a == "app/security.py"]


def test_users_py_tem_zero_ocorrencias_pelo_contador():
    assert contar_ocorrencias((APP_DIR / "users.py").read_text(encoding="utf-8")) == 0


def test_allowlist_tem_estrutura_ordenada_e_teto_igual_a_soma():
    allowlist = _allowlist()
    arquivos = allowlist["arquivos"]
    assert list(arquivos) == sorted(arquivos)
    assert all(isinstance(n, int) and n > 0 for n in arquivos.values())
    assert allowlist["teto_total"] == sum(arquivos.values())
    assert all(a.startswith("app/") and a.endswith(".py") for a in arquivos)


def test_allowlist_zerada_nenhum_arquivo_checa_papel():
    allowlist = _allowlist()
    assert allowlist["teto_total"] == 0
    assert allowlist["arquivos"] == {}
    assert contagem_viva() == {}


def test_arquivos_da_allowlist_existem():
    for arquivo in _allowlist()["arquivos"]:
        assert (API_ROOT / arquivo).is_file(), arquivo


# ---------------------------------------------------------------------------
# Provas com codigo sintetico: a guarda detecta cada padrao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [
        ("def f(identity):\n    return identity.role == 'admin'\n", 1),
        ("def f(session_identity):\n    return session_identity.role\n", 1),
        ("def f(request):\n    return request.state.identity.role\n", 1),
        ("def f(x: Identity):\n    return x.role\n", 1),
        ("def f(x: 'Identity'):\n    return x.role\n", 1),
        ("def f(x: Optional[Identity] = None):\n    return x.role\n", 1),
        ("def f(x: security.Identity):\n    return x.role\n", 1),
        ("def f(identity):\n    return identity.role, identity.role\n", 2),
        ("def f(i):\n    return is_admin(i)\n", 1),
        ("def f(i):\n    return security.is_staff(i)\n", 1),
        ("def f(i):\n    return is_admin_or_pt(i)\n", 1),
        ("def f(i):\n    return _require_admin_identity(i)\n", 1),
        ("def is_admin(i):\n    return True\n", 1),
        ("async def _require_admin_identity(i):\n    return i\n", 1),
        ("def is_admin_or_pt(i):\n    return True\n\ndef g(i):\n    return is_admin_or_pt(i)\n", 2),
    ],
    ids=[
        "identity", "sufixo_identity", "atributo_encadeado", "anotado", "anotado_string", "anotado_optional",
        "anotado_attr", "duas_vezes", "call_is_admin", "call_atributo", "call_admin_or_pt",
        "call_require_admin", "def_is_admin", "def_async", "def_e_call",
    ],
)
def test_contador_detecta_cada_padrao(codigo, esperado):
    assert contar_ocorrencias(codigo) == esperado


@pytest.mark.parametrize(
    "codigo",
    [
        "def f(user):\n    return user.role\n",
        "def f(row):\n    return row['role']\n",
        "def f(identity):\n    return identity.user_id\n",
        "def f(x):\n    return x.role\n",
        "def f(identity):\n    return getattr(identity, 'role')\n",
        "def f(identity):\n    me = identity\n    return me.role\n",
        "def f(x):\n    return Depends(require_admin)\n",
    ],
    ids=["outro_nome", "linha_de_banco", "outro_atributo", "nao_anotado", "getattr", "alias", "referencia_guard"],
)
def test_contador_documenta_o_que_escapa_ou_nao_conta(codigo):
    assert contar_ocorrencias(codigo) == 0


def test_anotacao_nao_vaza_para_outra_funcao():
    codigo = "def a(x: Identity):\n    return 1\n\ndef b(x):\n    return x.role\n"
    assert contar_ocorrencias(codigo) == 0


def test_contar_detalhado_separa_role_e_funcoes():
    assert contar_detalhado("def f(identity):\n    return is_admin(identity) and identity.role\n") == (1, 1)


def test_comparar_acusa_arquivo_novo_com_orientacao_para_app_access():
    problemas = comparar({"app/novo.py": 1}, {"teto_total": 0, "arquivos": {}})
    assert len(problemas) == 1
    assert "app/novo.py" in problemas[0] and "app.access" in problemas[0] and "require_permission" in problemas[0]


def test_comparar_acusa_contagem_maior_menor_e_arquivo_zerado():
    base = {"teto_total": 3, "arquivos": {"app/a.py": 3}}
    assert any("acima" in p for p in comparar({"app/a.py": 4}, base))
    menor = comparar({"app/a.py": 2}, base)
    assert any("baixe o numero" in p for p in menor)
    assert len(menor) == 1  # a soma do JSON ainda bate com o teto registrado
    zerado = comparar({}, base)
    assert any("0 ocorrencias" in p for p in zerado)


def test_comparar_acusa_teto_total_diferente_da_soma():
    problemas = comparar({"app/a.py": 3}, {"teto_total": 9, "arquivos": {"app/a.py": 3}})
    assert len(problemas) == 1 and "teto_total" in problemas[0]


def test_comparar_verde_quando_tudo_bate():
    assert comparar({"app/a.py": 3}, {"teto_total": 3, "arquivos": {"app/a.py": 3}}) == []
