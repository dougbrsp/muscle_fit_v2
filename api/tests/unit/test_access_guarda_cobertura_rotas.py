"""Guarda de cobertura de rotas do RBAC (ADR 0011, lote 1): ratchet que so encolhe.

Toda rota dos apps FastAPI (`app.main` e `app.main_engines`; nao ha outro app)
deve declarar `require_permission(...)` (lida por `declared_permissions`) OU
estar em `access_cobertura_allowlist.json`:

- `divida.rotas`: rotas que ainda nao declaram permissao. So encolhe.
  `divida.teto` registra o tamanho; a lista nao pode passar dele.
- `publicas`: rotas legitimamente publicas (login, esqueci a senha, health),
  com motivo. Nao contam como divida.

Determinismo da enumeracao. `main.py` inclui cada router dentro de
`try/except Exception` (falha de import vira log e a rota some) e nenhuma
inclusao depende de variavel de ambiente; so `/docs`, `/redoc` e
`/openapi.json` dependem de `API_DOCS_ENABLED`, mas sao rotas Starlette, nao
`APIRoute`, e portanto nao entram na enumeracao (vale com a variavel ligada ou
desligada). Um router que deixe de carregar faz suas rotas sumirem da lista e
a verificacao "allowlist com rota que nao existe mais" falha alto.

Esta versao do FastAPI guarda routers incluidos de forma preguicosa
(`_IncludedRouter`); a enumeracao os expande por `effective_route_contexts()`
quando existe e usa a propria rota quando nao.

Rodar: cd api && python -m pytest tests/unit/test_access_guarda_cobertura_rotas.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.routing import Host, Mount

from app.access import declared_permissions, require_permission
from app.security import Identity, get_current_identity

ALLOWLIST_PATH = Path(__file__).with_name("access_cobertura_allowlist.json")
_IGNORED_METHODS = {"HEAD", "OPTIONS"}

# Pino das rotas publicas (sem `require_permission` e fora da divida). Acrescentar
# uma rota aqui e decisao de seguranca: exige revisao de seguranca, nao so este teste.
PUBLICAS_APROVADAS = frozenset(
    {
        "GET /health",
        "GET /ready",
        "POST /auth/login",
        "POST /auth/password-reset/request",
        "POST /auth/password-reset/confirm",
        "POST /auth/logout",
    }
)


def _iter_api_routes(app: FastAPI):
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        candidates = expand() if callable(expand) else [route]
        for candidate in candidates:
            if isinstance(candidate, (Mount, Host)):
                # Sub-app montada: a enumeracao nao a inspeciona; ignorar em silencio
                # deixaria rotas sem guarda fora da cobertura.
                raise AssertionError(
                    f"Rota montada nao inspecionavel ({type(candidate).__name__} "
                    f"{getattr(candidate, 'path', '')!r}): estenda enumerate_routes "
                    "para entrar nela antes de montar sub-apps."
                )
            if hasattr(candidate, "dependant"):
                yield candidate


def enumerate_routes(app: FastAPI) -> dict[str, list[tuple[str, str]]]:
    """`"METODO caminho" -> permissoes declaradas` (lista vazia = sem permissao)."""
    found: dict[str, list[tuple[str, str]]] = {}
    for route in _iter_api_routes(app):
        declared = declared_permissions(route)
        for method in sorted(route.methods):
            if method in _IGNORED_METHODS:
                continue
            found.setdefault(f"{method} {route.path}", []).extend(declared)
    return found


def check_publicas_pinadas(allowlist: dict) -> list[str]:
    """Violacoes do pino: o conjunto de rotas publicas tem de ser exatamente o aprovado."""
    atuais = [item["rota"] for item in allowlist["publicas"]]
    problemas: list[str] = []
    if len(atuais) != len(set(atuais)):
        problemas.append("publicas tem entradas duplicadas.")
    for rota in sorted(set(atuais) - PUBLICAS_APROVADAS):
        problemas.append(
            f"{rota} foi adicionada a `publicas`: rota publica nova exige REVISAO DE "
            "SEGURANCA e a alteracao explicita de PUBLICAS_APROVADAS neste teste."
        )
    for rota in sorted(PUBLICAS_APROVADAS - set(atuais)):
        problemas.append(f"{rota} saiu de `publicas`: confirme que ganhou require_permission e atualize o pino.")
    return problemas


def _load_allowlist() -> dict:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def check_coverage(routes: dict[str, list[tuple[str, str]]], allowlist: dict) -> list[str]:
    """Violacoes da guarda (lista vazia = verde)."""
    divida = list(allowlist["divida"]["rotas"])
    divida_set = set(divida)
    publicas = {item["rota"] for item in allowlist["publicas"]}
    teto = allowlist["divida"]["teto"]
    problemas: list[str] = []

    if len(divida) != len(divida_set):
        problemas.append("A allowlist (divida.rotas) tem entradas duplicadas.")
    if len(divida) > teto:
        problemas.append(
            f"A allowlist tem {len(divida)} rotas, acima do teto registrado ({teto}). "
            "A allowlist so encolhe: declare require_permission na rota nova em vez de lista-la."
        )
    for rota in sorted(divida_set & publicas):
        problemas.append(f"{rota} esta em divida E em publicas: deixe so em uma.")

    for rota, permissoes in sorted(routes.items()):
        if permissoes:
            if rota in divida_set:
                problemas.append(
                    f"{rota} ja declara permissao {permissoes[0]}: REMOVA-A de divida.rotas "
                    "e diminua divida.teto no JSON."
                )
            continue
        if rota in divida_set or rota in publicas:
            continue
        problemas.append(
            f"{rota} nao declara permissao e nao esta na allowlist. Declare "
            "`Depends(require_permission(recurso, acao))` na rota (catalogo em "
            "app/access/catalog.py); NAO aumente a allowlist."
        )

    existentes = set(routes)
    for rota in sorted((divida_set | publicas) - existentes):
        problemas.append(
            f"{rota} esta na allowlist mas a rota nao existe mais (ou o router deixou de "
            "carregar): remova-a do JSON e diminua divida.teto."
        )
    return problemas


def _all_project_routes() -> dict[str, list[tuple[str, str]]]:
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    merged: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for rota, permissoes in enumerate_routes(app).items():
            merged.setdefault(rota, []).extend(permissoes)
    return merged


# ---------------------------------------------------------------------------
# Guarda real
# ---------------------------------------------------------------------------


def test_guarda_todas_as_rotas_declaram_permissao_ou_estao_na_allowlist():
    problemas = check_coverage(_all_project_routes(), _load_allowlist())
    assert not problemas, "Guarda de cobertura RBAC:\n" + "\n".join(problemas[:25])


def test_so_existem_dois_apps_fastapi_no_projeto():
    apps = sorted(
        p.name
        for p in (Path(__file__).resolve().parents[2] / "app").glob("*.py")
        if "FastAPI(" in p.read_text(encoding="utf-8")
    )
    assert apps == ["main.py", "main_engines.py"], (
        "Novo app FastAPI: inclua-o em _all_project_routes() desta guarda."
    )


def test_enumeracao_cobre_os_dois_apps_e_expande_routers_incluidos():
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    principal = enumerate_routes(main_app)
    engines = enumerate_routes(engines_app)
    assert "POST /auth/login" in principal
    assert "GET /me/training-preferences" in principal
    assert any(rota.startswith("GET /analytics/") for rota in engines)
    assert len(principal) > 200 and len(engines) > 20


def test_allowlist_registra_o_proprio_tamanho_e_categorias_estao_separadas():
    allowlist = _load_allowlist()
    divida = allowlist["divida"]["rotas"]
    assert allowlist["divida"]["teto"] == len(divida)
    assert divida == sorted(divida) and len(divida) == len(set(divida))
    publicas = allowlist["publicas"]
    assert publicas, "login, esqueci a senha e health ficam na categoria publica"
    for item in publicas:
        assert set(item) == {"rota", "motivo"} and item["motivo"].strip()
    chaves = {item["rota"] for item in publicas}
    assert {"POST /auth/login", "GET /health"} <= chaves
    assert not chaves & set(divida)


def test_enumeracao_nao_depende_da_variavel_das_docs(monkeypatch):
    base = set(_all_project_routes())
    monkeypatch.setenv("API_DOCS_ENABLED", "1")
    assert set(_all_project_routes()) == base
    assert not any(r.split(" ", 1)[1] in {"/docs", "/redoc", "/openapi.json"} for r in base)


# ---------------------------------------------------------------------------
# Prova de que a guarda detecta (app minimo)
# ---------------------------------------------------------------------------


def _mini_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protegida")
    def protegida(_: Identity = Depends(require_permission("dashboards", "ver"))):
        return {}

    @app.get("/sem-permissao")
    def sem_permissao(_: Identity = Depends(get_current_identity)):
        return {}

    return app


def _allowlist(divida=(), publicas=(), teto=None) -> dict:
    return {
        "divida": {"teto": len(divida) if teto is None else teto, "rotas": list(divida)},
        "publicas": [{"rota": r, "motivo": "teste"} for r in publicas],
    }


def test_enumeracao_le_a_permissao_declarada_no_app_minimo():
    rotas = enumerate_routes(_mini_app())
    assert rotas["GET /protegida"] == [("dashboards", "ver")]
    assert rotas["GET /sem-permissao"] == []


def test_guarda_acusa_rota_nova_sem_permissao_e_orienta_o_dev():
    problemas = check_coverage(enumerate_routes(_mini_app()), _allowlist())
    assert len(problemas) == 1
    msg = problemas[0]
    assert "GET /sem-permissao" in msg and "require_permission" in msg
    assert "NAO aumente a allowlist" in msg


def test_guarda_verde_quando_rota_sem_permissao_esta_na_divida():
    assert check_coverage(enumerate_routes(_mini_app()), _allowlist(["GET /sem-permissao"])) == []


def test_guarda_verde_quando_rota_sem_permissao_e_publica():
    assert check_coverage(enumerate_routes(_mini_app()), _allowlist(publicas=["GET /sem-permissao"])) == []


def test_guarda_acusa_allowlist_com_rota_inexistente():
    problemas = check_coverage(
        enumerate_routes(_mini_app()), _allowlist(["GET /sem-permissao", "GET /fantasma"])
    )
    assert len(problemas) == 1 and "GET /fantasma" in problemas[0] and "nao existe mais" in problemas[0]


def test_guarda_acusa_publica_inexistente():
    problemas = check_coverage(
        enumerate_routes(_mini_app()), _allowlist(["GET /sem-permissao"], publicas=["POST /fantasma"])
    )
    assert any("POST /fantasma" in p for p in problemas)


def test_guarda_acusa_allowlist_acima_do_teto():
    problemas = check_coverage(
        enumerate_routes(_mini_app()), _allowlist(["GET /sem-permissao"], teto=0)
    )
    assert len(problemas) == 1 and "teto" in problemas[0]


def test_guarda_acusa_rota_na_allowlist_que_ja_declara_permissao():
    problemas = check_coverage(
        enumerate_routes(_mini_app()), _allowlist(["GET /sem-permissao", "GET /protegida"])
    )
    assert len(problemas) == 1
    assert "GET /protegida" in problemas[0] and "REMOVA" in problemas[0]


def test_guarda_acusa_rota_em_divida_e_publica_ao_mesmo_tempo():
    problemas = check_coverage(
        enumerate_routes(_mini_app()),
        _allowlist(["GET /sem-permissao"], publicas=["GET /sem-permissao"]),
    )
    assert any("divida E em publicas" in p for p in problemas)


def test_permissao_em_dependencia_aninhada_e_em_router_incluido_conta():
    app = FastAPI()
    router = APIRouter(dependencies=[Depends(require_permission("cardio", "editar"))])

    def _interna(_: Identity = Depends(require_permission("cardio", "ver"))):
        return None

    @router.post("/x")
    def x(_=Depends(_interna)):
        return {}

    app.include_router(router, prefix="/v1")
    assert sorted(enumerate_routes(app)["POST /v1/x"]) == [("cardio", "editar"), ("cardio", "ver")]


@pytest.mark.parametrize("campo", ["divida", "publicas"])
def test_json_tem_a_estrutura_esperada(campo):
    assert campo in _load_allowlist()


# ---------------------------------------------------------------------------
# Pino das rotas publicas e rotas montadas
# ---------------------------------------------------------------------------


def test_publicas_sao_exatamente_as_seis_aprovadas():
    assert len(PUBLICAS_APROVADAS) == 6
    problemas = check_publicas_pinadas(_load_allowlist())
    assert not problemas, "\n".join(problemas)


def test_pino_acusa_rota_publica_nova_com_pedido_de_revisao_de_seguranca():
    allowlist = _allowlist(publicas=sorted(PUBLICAS_APROVADAS | {"POST /admin/perigosa"}))
    problemas = check_publicas_pinadas(allowlist)
    assert len(problemas) == 1
    assert "POST /admin/perigosa" in problemas[0] and "REVISAO DE SEGURANCA" in problemas[0]


def test_pino_acusa_rota_publica_removida_e_duplicada():
    assert any("GET /ready" in p for p in check_publicas_pinadas(_allowlist(publicas=sorted(PUBLICAS_APROVADAS - {"GET /ready"}))))
    assert any("duplicadas" in p for p in check_publicas_pinadas(_allowlist(publicas=sorted(PUBLICAS_APROVADAS) + ["GET /health"])))


def test_rotas_publicas_aprovadas_existem_e_nao_declaram_permissao():
    rotas = _all_project_routes()
    for rota in PUBLICAS_APROVADAS:
        assert rota in rotas and rotas[rota] == [], rota


def test_enumeracao_falha_com_rota_montada_que_nao_sabe_inspecionar():
    app = _mini_app()
    app.mount("/sub", FastAPI())
    with pytest.raises(AssertionError, match="montada nao inspecionavel"):
        enumerate_routes(app)


def test_enumeracao_falha_com_mount_dentro_de_router_incluido():
    app = FastAPI()
    router = APIRouter()
    router.mount("/sub", FastAPI())
    app.include_router(router, prefix="/v1")
    try:
        enumerate_routes(app)
    except AssertionError as exc:
        assert "montada nao inspecionavel" in str(exc)
    else:  # esta versao do FastAPI nao propaga mounts de router: nada a inspecionar
        assert not [r for r in app.routes if isinstance(r, (Mount, Host))]


def test_apps_reais_nao_tem_rota_montada():
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    for app in (main_app, engines_app):
        assert not [r for r in app.routes if isinstance(r, (Mount, Host))]
        enumerate_routes(app)  # nao levanta
