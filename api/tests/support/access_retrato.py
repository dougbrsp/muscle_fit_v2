"""Retrato congelado dos guards ANTIGOS das rotas (ADR 0011, pre-condicoes do lote 2).

Para que os lotes 2B a 2F provem paridade (guard antigo x permissao nova) depois
de trocar os guards, este modulo classifica cada rota FastAPI viva pelo guard
antigo lido da arvore de dependencias e congela o resultado em
`tests/unit/data/access_retrato_guards_antigos.json`.

Enumeracao. Mesma logica da guarda de cobertura
(`tests/unit/test_access_guarda_cobertura_rotas.py`): dois apps (`app.main` e
`app.main_engines`), routers preguicosos expandidos por
`effective_route_contexts()`, chave `"METODO caminho"`, HEAD/OPTIONS ignorados,
falha alto com Mount/Host. O conjunto de chaves e identico ao da guarda.

Classes (precedencia do mais restritivo; `guards` lista todos os encontrados):

- `admin`                    require_admin (admin, qualquer origem: token mestre
                             resolve para admin; dispositivo herda o papel do dono).
- `admin_ou_pt`              require_admin_or_pt.
- `sessao`                   require_session_identity (so login; recusa dispositivo
                             e token mestre).
- `usuario_sem_dispositivo`  require_user_credential (recusa dispositivo).
- `dispositivo`              require_device_credential (exige dispositivo).
- `autenticado`              so get_current_identity (ou dependencia propria que o usa).
- `publica`                  nenhuma identidade na arvore E nenhuma resolucao de
                             identidade no corpo.
- `identidade_no_corpo`      sem identidade na arvore, mas o corpo do handler (ou de
                             uma dependencia propria) resolve a credencial por conta
                             propria (`resolve_identity*`). Nunca vira `publica`.
- `outra`                    nao reconhecido: combinacao de guards sem equivalente,
                             ou handler que recebe `x_api_token`/`musclefit_session`
                             sem resolver identidade reconhecida. Exige justificativa.

`checagem_no_handler`: nomes encontrados por AST entre `identity.role` (qualquer
acesso ao atributo `.role`), `is_admin`, `is_staff`, `is_admin_or_pt`,
`_require_admin_identity`, `_require_self_or_admin`. LIMITE DA ANALISE: le o corpo
do handler e segue chamadas a funcoes de nivel de modulo DEFINIDAS NO MESMO
MODULO por ate 2 niveis (handler -> helper -> helper) e, de outros modulos `app.*`,
so helpers cujo nome comeca com `_require` (guards reexportados, ex.:
`_require_admin_identity` em `muscle_volume_classes`). Nao segue demais funcoes de outros
modulos, metodos, lambdas, chamadas dinamicas nem codigo que passe a identidade a
um objeto que a inspecione adiante; checagens de papel por SQL (ex.: filtro por
`role` em consulta) nao sao vistas. `checagem_em_dependencias` aplica o mesmo
a dependencias proprias (fora do conjunto dos guards conhecidos) da arvore.

Regenerar (so de proposito; o teste nunca regenera sozinho):

    cd api && python -m tests.support.access_retrato

Consumo pelo teste de paridade: `carregar_retrato()`, `classificar_rotas_vivas()`,
`guard_antigo_permitia(classe, papel, tipo_identidade)`.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any, Callable, Optional

from starlette.routing import Host, Mount

RETRATO_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "access_retrato_guards_antigos.json"
_IGNORED_METHODS = {"HEAD", "OPTIONS"}

CLASSES = (
    "admin",
    "admin_ou_pt",
    "sessao",
    "usuario_sem_dispositivo",
    "dispositivo",
    "autenticado",
    "publica",
    "identidade_no_corpo",
    "outra",
)
CLASSES_COM_JUSTIFICATIVA = ("outra", "identidade_no_corpo")
PAPEIS = ("admin", "personal_trainer", "regular")
TIPOS_IDENTIDADE = ("session", "device", "master_token")

CHECAGENS_ORDEM = (
    "identity.role",
    "is_admin",
    "is_staff",
    "is_admin_or_pt",
    "_require_admin_identity",
    "_require_self_or_admin",
)
_PARAMS_CREDENCIAL = ("x_api_token", "musclefit_session")
_SEGUIR_NIVEIS = 2


class SemDecisaoAutomatica(ValueError):
    """Classe do retrato sem decisao automatica (`outra`, `identidade_no_corpo`)."""


# ---------------------------------------------------------------------------
# Enumeracao (mesma logica da guarda de cobertura)
# ---------------------------------------------------------------------------


def _iter_api_routes(app):
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        candidates = expand() if callable(expand) else [route]
        for candidate in candidates:
            if isinstance(candidate, (Mount, Host)):
                raise AssertionError(
                    f"Rota montada nao inspecionavel ({type(candidate).__name__} "
                    f"{getattr(candidate, 'path', '')!r}): estenda o retrato antes de montar sub-apps."
                )
            if hasattr(candidate, "dependant"):
                yield candidate


def _guards_conhecidos() -> dict[Callable, str]:
    from app.devices import require_device_credential, require_user_credential
    from app.security import get_current_identity
    from tests.support.guards_antigos import require_admin, require_admin_or_pt
    from app.users import require_session_identity

    return {
        require_admin: "admin",
        require_admin_or_pt: "admin_ou_pt",
        require_session_identity: "sessao",
        require_user_credential: "usuario_sem_dispositivo",
        require_device_credential: "dispositivo",
        get_current_identity: "autenticado",
    }


def _resolvedores() -> set[Callable]:
    from app.security import get_current_identity, resolve_identity, resolve_identity_from_credentials

    return {resolve_identity, resolve_identity_from_credentials, get_current_identity}


# ---------------------------------------------------------------------------
# Analise AST do corpo
# ---------------------------------------------------------------------------


def _parse(func: Callable) -> Optional[ast.AST]:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, SyntaxError):
        return None


def _scan(func: Callable, resolvers: set[Callable]) -> tuple[set[str], list[str]]:
    """(checagens, funcoes que resolvem identidade) do corpo de `func` e das
    funcoes do mesmo modulo seguidas por ate `_SEGUIR_NIVEIS` niveis."""
    checks: set[str] = set()
    resolve: list[str] = []
    visited: set[Callable] = set()

    def visit(fn: Callable, nivel: int) -> None:
        if fn in visited:
            return
        visited.add(fn)
        tree = _parse(fn)
        if tree is None:
            return
        glb = getattr(fn, "__globals__", {})
        modulo = getattr(fn, "__module__", None)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr == "role":
                    checks.add("identity.role")
                if node.attr in CHECAGENS_ORDEM[1:]:
                    checks.add(node.attr)
            if isinstance(node, ast.Name) and node.id in CHECAGENS_ORDEM[1:]:
                checks.add(node.id)
            if isinstance(node, ast.Call):
                target = node.func
                nome = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
                if nome is None:
                    continue
                obj = glb.get(nome) if isinstance(target, ast.Name) else None
                if obj is None and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    mod = glb.get(target.value.id)
                    obj = getattr(mod, nome, None) if mod is not None else None
                if obj in resolvers:
                    resolve.append(f"{fn.__name__} -> {getattr(obj, '__name__', nome)}")
                elif (
                    nivel < _SEGUIR_NIVEIS
                    and inspect.isfunction(obj)
                    and (obj.__module__ == modulo or (obj.__module__.startswith("app.") and nome.startswith("_require")))
                ):
                    visit(obj, nivel + 1)

    visit(func, 0)
    return checks, resolve


def _ordenar_checagens(checks: set[str]) -> list[str]:
    return [c for c in CHECAGENS_ORDEM if c in checks]


# ---------------------------------------------------------------------------
# Classificacao
# ---------------------------------------------------------------------------


def _nome(call: Callable) -> str:
    return f"{getattr(call, '__module__', '?')}.{getattr(call, '__qualname__', getattr(call, '__name__', repr(call)))}"


def _walk_dependant(dependant) -> list[Any]:
    out, stack = [], [dependant]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(getattr(node, "dependencies", ()) or ())
    return out


def _is_permission_dep(call: Any) -> bool:
    return getattr(call, "access_resource", None) is not None


def classify_route(route) -> dict[str, Any]:
    """Classifica uma rota (APIRoute) pelo guard antigo. Ver docstring do modulo."""
    known = _guards_conhecidos()
    resolvers = _resolvedores()
    guards: set[str] = set()
    proprias: dict[str, Callable] = {}
    for node in _walk_dependant(route.dependant):
        call = getattr(node, "call", None)
        if call is None:
            continue
        if call in known:
            guards.add(known[call])
        elif inspect.isfunction(call) and not _is_permission_dep(call) and call is not getattr(route, "endpoint", None):
            if getattr(call, "__module__", "").startswith("app."):
                proprias[_nome(call)] = call

    endpoint = getattr(route, "endpoint", None) or route.dependant.call
    checks_h, resolve_h = _scan(endpoint, resolvers)
    checks_d: set[str] = set()
    resolve_d: list[str] = []
    for fn in proprias.values():
        c, r = _scan(fn, resolvers)
        checks_d |= c
        resolve_d += r
    resolve_corpo = sorted(set(resolve_h + resolve_d))

    restritivos = guards - {"autenticado"}
    detalhe: list[str] = []
    classe: str
    if restritivos:
        # Implicacoes: admin => admin_ou_pt; sessao => usuario_sem_dispositivo.
        efetivos = set(restritivos)
        if "admin" in efetivos:
            efetivos.discard("admin_ou_pt")
        if "sessao" in efetivos:
            efetivos.discard("usuario_sem_dispositivo")
        if len(efetivos) == 1:
            classe = next(iter(efetivos))
        else:
            classe = "outra"
            detalhe.append("combinacao de guards sem classe propria: " + ", ".join(sorted(restritivos)))
    elif "autenticado" in guards:
        classe = "autenticado"
    else:
        params = set(inspect.signature(endpoint).parameters) if callable(endpoint) else set()
        if resolve_corpo:
            classe = "identidade_no_corpo"
        elif params & set(_PARAMS_CREDENCIAL):
            classe = "outra"
            detalhe.append(
                "handler recebe " + "/".join(sorted(params & set(_PARAMS_CREDENCIAL)))
                + " sem resolver identidade reconhecida"
            )
        else:
            classe = "publica"
    if resolve_corpo and classe not in ("identidade_no_corpo", "publica"):
        detalhe.append("alem disso resolve identidade no corpo: " + "; ".join(resolve_corpo))
    elif resolve_corpo:
        detalhe.append("resolve identidade no corpo: " + "; ".join(resolve_corpo))

    entry: dict[str, Any] = {
        "classe": classe,
        "checagem_no_handler": _ordenar_checagens(checks_h),
        "checagem_em_dependencias": _ordenar_checagens(checks_d),
        "guards": sorted(guards),
        "modulo": getattr(endpoint, "__module__", "?"),
        "dependencias_proprias": sorted(proprias),
        "detalhe": detalhe,
    }
    return entry


def _justificar(rota: str, entry: dict[str, Any]) -> str:
    base = "; ".join(entry["detalhe"]) or "sem detalhe"
    checks = ", ".join(entry["checagem_no_handler"] + entry["checagem_em_dependencias"]) or "nenhuma checagem de papel vista"
    if entry["classe"] == "identidade_no_corpo":
        return (
            f"O guard antigo nao esta na arvore de dependencias: {base}. Checagem de papel no handler: {checks}. "
            "A paridade desta rota depende dessa leitura e deve ser provada a mao."
        )
    return f"Nao reconhecida automaticamente: {base}. Checagem de papel: {checks}. Revisar a mao antes de trocar o guard."


def classificar_rotas_vivas() -> dict[str, dict[str, Any]]:
    """`"METODO caminho" -> entrada` das rotas vivas dos dois apps, ordenado."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    found: dict[str, dict[str, Any]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            entry = classify_route(route)
            for method in sorted(route.methods):
                if method in _IGNORED_METHODS:
                    continue
                key = f"{method} {route.path}"
                previous = found.get(key)
                if previous is not None and previous != entry:
                    # Dois handlers para a mesma chave com guards diferentes: nao escolher em silencio.
                    entry = {
                        **entry,
                        "classe": "outra",
                        "detalhe": entry["detalhe"] + [f"chave duplicada com classificacoes distintas ({previous['classe']} x {entry['classe']})"],
                    }
                found[key] = entry
    return {k: found[k] for k in sorted(found)}


# ---------------------------------------------------------------------------
# Retrato (JSON)
# ---------------------------------------------------------------------------


def montar_retrato(removidas: Optional[list[str]] = None) -> dict[str, Any]:
    rotas = classificar_rotas_vivas()
    for rota, entry in rotas.items():
        if entry["classe"] in CLASSES_COM_JUSTIFICATIVA:
            entry["justificativa"] = _justificar(rota, entry)
    return {
        "_doc": "Retrato congelado dos guards antigos (ADR 0011). Gerado por tests/support/access_retrato.py; nao editar a mao.",
        "rotas_removidas_depois_do_retrato": sorted(removidas or []),
        "rotas": rotas,
    }


def serializar(retrato: dict[str, Any]) -> str:
    return json.dumps(retrato, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def carregar_retrato() -> dict[str, Any]:
    return json.loads(RETRATO_PATH.read_text(encoding="utf-8"))


def regenerar(path: Path = RETRATO_PATH) -> Path:
    """Reescreve o JSON preservando a lista de rotas removidas ja declarada."""
    removidas: list[str] = []
    if path.exists():
        removidas = json.loads(path.read_text(encoding="utf-8")).get("rotas_removidas_depois_do_retrato", [])
    path.write_text(serializar(montar_retrato(removidas)), encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------
# Decisao do guard antigo
# ---------------------------------------------------------------------------


def guard_antigo_permitia(classe: str, papel: str, tipo_identidade: str) -> bool:
    """O guard antigo permitia (True) ou recusava (False) esta identidade?

    Regras lidas do codigo: token mestre resolve para admin (`security.resolve_identity`);
    dispositivo herda o papel do dono; `require_session_identity` exige
    `source == "session"`; `require_user_credential` recusa `source == "device"`;
    `require_device_credential` exige `source == "device"`.
    Levanta `SemDecisaoAutomatica` para `outra` e `identidade_no_corpo`.
    """
    if papel not in PAPEIS:
        raise ValueError(f"papel desconhecido: {papel!r} (esperado um de {PAPEIS})")
    if tipo_identidade not in TIPOS_IDENTIDADE:
        raise ValueError(f"tipo de identidade desconhecido: {tipo_identidade!r} (esperado um de {TIPOS_IDENTIDADE})")
    if tipo_identidade == "master_token" and papel != "admin":
        raise ValueError("token mestre sempre resolve para o papel admin; combinacao invalida")
    if classe in CLASSES_COM_JUSTIFICATIVA:
        raise SemDecisaoAutomatica(
            f"classe {classe!r} nao tem decisao automatica: leia `detalhe`/`justificativa` do retrato e prove a paridade a mao."
        )
    if classe in ("publica", "autenticado"):
        return True
    if classe == "admin":
        return papel == "admin"
    if classe == "admin_ou_pt":
        return papel in ("admin", "personal_trainer")
    if classe == "sessao":
        return tipo_identidade == "session"
    if classe == "usuario_sem_dispositivo":
        return tipo_identidade != "device"
    if classe == "dispositivo":
        return tipo_identidade == "device"
    raise ValueError(f"classe desconhecida: {classe!r} (esperado uma de {CLASSES})")


if __name__ == "__main__":
    print(f"Retrato regenerado em {regenerar()}")
