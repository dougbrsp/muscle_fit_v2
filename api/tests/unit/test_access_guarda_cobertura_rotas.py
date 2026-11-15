"""Guarda de cobertura de rotas do RBAC (ADR 0011, lote 1): ratchet que so encolhe.

Toda rota dos apps FastAPI (`app.main` e `app.main_engines`; nao ha outro app)
deve declarar `require_permission(...)` (lida por `declared_permissions`) OU
estar em `access_cobertura_allowlist.json`:

- `divida.rotas`: rotas que ainda nao declaram permissao. So encolhe.
  `divida.teto` registra o tamanho; a lista nao pode passar dele.
- `publicas`: rotas legitimamente publicas (login, esqueci a senha, health),
  com motivo. Nao contam como divida.
- `so_autenticacao` (lote 2B): rotas que exigem APENAS identidade autenticada
  (`get_current_identity` na arvore de dependencias), sem `require_permission`:
  perfil da propria conta, troca da propria senha, consulta de job pelo dono e foto
  de perfil (decidida no handler por `authorize_self_or_all`). Cada uma tem motivo, o
  conjunto e pinado em `SO_AUTENTICACAO_APROVADAS` (rota nova exige revisao de
  seguranca), nao pode declarar permissao nem estar em `divida`/`publicas`.

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

# Pino das rotas "so autenticacao" (lote 2B). Acrescentar uma rota aqui e decisao de
# seguranca: exige revisao de seguranca, nao so este teste.
SO_AUTENTICACAO_APROVADAS = frozenset(
    {
        "GET /auth/me",
        "POST /auth/change-password",
        "GET /users/me",
        "GET /users/me/hr-zones",
        "PUT /users/me",
        "GET /jobs/{job_id}",
        "POST /users/{user_id}/photo",
        "GET /users/{user_id}/photo",
        "DELETE /users/{user_id}/photo",
        "GET /access/my-students",
    }
)

# Rota so-autenticacao criada DEPOIS do retrato (lote 5A, `app.access_admin`): fora do retrato e
# fora de `ROTAS_NOVAS_POS_RETRATO` (que lista so as com `admin.grupos`).
ROTA_NOVA_SO_AUTENTICACAO = "GET /access/my-students"


# Rotas criadas DEPOIS do retrato congelado dos guards antigos (lote 3A, `app.access_admin`).
# Unico lugar da lista: os testes que exigem "toda rota viva/convertida esta no retrato" aceitam
# SO estas e continuam falhando para qualquer outra rota nova. O retrato NAO e regenerado.
# rota -> (recurso, acao). Decisao esperada (credencial de sessao ou token mestre): admin e
# token mestre permitidos; usuario (`regular`), `personal_trainer` e sem grupo negados. A recusa
# de dispositivo e feita no handler (403) e coberta por `test_access_admin_rotas.py`.
ROTAS_NOVAS_POS_RETRATO: dict[str, tuple[str, str]] = {
    "GET /access/catalog": ("admin.grupos", "ver"),
    "GET /access/groups": ("admin.grupos", "ver"),
    "GET /access/groups/{group_id}": ("admin.grupos", "ver"),
    "GET /access/groups/{group_id}/members": ("admin.grupos", "ver"),
    "GET /access/audit": ("admin.grupos", "ver"),
    "POST /access/groups": ("admin.grupos", "editar"),
    "PUT /access/groups/{group_id}": ("admin.grupos", "editar"),
    "PUT /access/groups/{group_id}/grants": ("admin.grupos", "editar"),
    "POST /access/groups/{group_id}/members": ("admin.grupos", "editar"),
    "DELETE /access/groups/{group_id}": ("admin.grupos", "excluir"),
    "DELETE /access/groups/{group_id}/members/{user_id}": ("admin.grupos", "excluir"),
    # Lote 5A: vinculos treinador-aluno.
    "GET /access/links": ("admin.grupos", "ver"),
    "POST /access/links": ("admin.grupos", "editar"),
    "DELETE /access/links/{link_id}": ("admin.grupos", "excluir"),
}


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


def check_so_autenticacao_pinada(allowlist: dict) -> list[str]:
    """Violacoes do pino de `so_autenticacao`: conjunto exatamente igual ao aprovado."""
    atuais = [item["rota"] for item in allowlist.get("so_autenticacao", [])]
    problemas: list[str] = []
    if len(atuais) != len(set(atuais)):
        problemas.append("so_autenticacao tem entradas duplicadas.")
    for rota in sorted(set(atuais) - SO_AUTENTICACAO_APROVADAS):
        problemas.append(
            f"{rota} foi adicionada a `so_autenticacao`: rota nova nesta categoria exige "
            "REVISAO DE SEGURANCA e a alteracao explicita de SO_AUTENTICACAO_APROVADAS neste teste."
        )
    for rota in sorted(SO_AUTENTICACAO_APROVADAS - set(atuais)):
        problemas.append(f"{rota} saiu de `so_autenticacao`: confirme a nova guarda e atualize o pino.")
    return problemas


def _load_allowlist() -> dict:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def check_coverage(
    routes: dict[str, list[tuple[str, str]]],
    allowlist: dict,
    identity_routes: set[str] | None = None,
) -> list[str]:
    """Violacoes da guarda (lista vazia = verde).

    `identity_routes`: rotas (`"METODO caminho"`) que tem `get_current_identity` na
    arvore de dependencias; so e usado para validar `so_autenticacao` (None = nao
    verifica a identidade; a guarda real sempre passa o conjunto)."""
    divida = list(allowlist["divida"]["rotas"])
    divida_set = set(divida)
    publicas = {item["rota"] for item in allowlist["publicas"]}
    so_auth_lista = [item["rota"] for item in allowlist.get("so_autenticacao", [])]
    so_auth = set(so_auth_lista)
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
    if len(so_auth_lista) != len(so_auth):
        problemas.append("so_autenticacao tem entradas duplicadas.")
    for rota in sorted(so_auth & divida_set):
        problemas.append(f"{rota} esta em so_autenticacao E em divida: deixe so em uma.")
    for rota in sorted(so_auth & publicas):
        problemas.append(f"{rota} esta em so_autenticacao E em publicas: deixe so em uma.")
    for rota in sorted(so_auth):
        if routes.get(rota):
            problemas.append(
                f"{rota} esta em so_autenticacao mas declara permissao {routes[rota][0]}: "
                "remova-a de so_autenticacao."
            )
        elif identity_routes is not None and rota in routes and rota not in identity_routes:
            problemas.append(
                f"{rota} esta em so_autenticacao mas nao tem get_current_identity na arvore de "
                "dependencias: sem identidade a rota e publica, nao so_autenticacao."
            )

    for rota, permissoes in sorted(routes.items()):
        if permissoes:
            if rota in divida_set:
                problemas.append(
                    f"{rota} ja declara permissao {permissoes[0]}: REMOVA-A de divida.rotas "
                    "e diminua divida.teto no JSON."
                )
            continue
        if rota in divida_set or rota in publicas or rota in so_auth:
            continue
        problemas.append(
            f"{rota} nao declara permissao e nao esta na allowlist. Declare "
            "`Depends(require_permission(recurso, acao))` na rota (catalogo em "
            "app/access/catalog.py); NAO aumente a allowlist."
        )

    existentes = set(routes)
    for rota in sorted((divida_set | publicas | so_auth) - existentes):
        problemas.append(
            f"{rota} esta na allowlist mas a rota nao existe mais (ou o router deixou de "
            "carregar): remova-a do JSON e, se era da divida, diminua divida.teto."
        )
    return problemas


def _depende_de_identidade(route) -> bool:
    stack = [route.dependant]
    while stack:
        node = stack.pop()
        if getattr(node, "call", None) is get_current_identity:
            return True
        stack.extend(getattr(node, "dependencies", ()) or ())
    return False


def enumerate_identity_routes(app: FastAPI) -> set[str]:
    """Rotas (`"METODO caminho"`) com `get_current_identity` na arvore de dependencias."""
    found: set[str] = set()
    for route in _iter_api_routes(app):
        if _depende_de_identidade(route):
            found.update(f"{m} {route.path}" for m in route.methods if m not in _IGNORED_METHODS)
    return found


def _all_identity_routes() -> set[str]:
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    return enumerate_identity_routes(main_app) | enumerate_identity_routes(engines_app)


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
    problemas = check_coverage(_all_project_routes(), _load_allowlist(), _all_identity_routes())
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
    for item in allowlist["so_autenticacao"]:
        assert set(item) == {"rota", "motivo"} and item["motivo"].strip()
    so_auth = {item["rota"] for item in allowlist["so_autenticacao"]}
    assert not so_auth & set(divida) and not so_auth & chaves


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


def _allowlist(divida=(), publicas=(), teto=None, so_autenticacao=()) -> dict:
    return {
        "divida": {"teto": len(divida) if teto is None else teto, "rotas": list(divida)},
        "publicas": [{"rota": r, "motivo": "teste"} for r in publicas],
        "so_autenticacao": [{"rota": r, "motivo": "teste"} for r in so_autenticacao],
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


@pytest.mark.parametrize("campo", ["divida", "publicas", "so_autenticacao"])
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


def test_so_autenticacao_sao_exatamente_as_dez_aprovadas():
    assert len(SO_AUTENTICACAO_APROVADAS) == 10
    problemas = check_so_autenticacao_pinada(_load_allowlist())
    assert not problemas, "\n".join(problemas)


def test_so_autenticacao_real_tem_identidade_e_nao_declara_permissao():
    rotas = _all_project_routes()
    com_identidade = _all_identity_routes()
    for rota in SO_AUTENTICACAO_APROVADAS:
        assert rota in rotas and rotas[rota] == [], rota
        assert rota in com_identidade, rota


def test_divida_real_tem_teto_0_e_nao_contem_as_rotas_convertidas():
    allowlist = _load_allowlist()
    divida = allowlist["divida"]
    assert divida["teto"] == 0 and divida["rotas"] == []
    convertidas = {
        "DELETE /devices/{device_id}", "DELETE /users/{user_id}", "GET /devices", "GET /devices/me",
        "GET /users", "POST /devices/pair", "POST /users", "POST /users/{user_id}/reset-password",
        "PUT /users/{user_id}",
    } | SO_AUTENTICACAO_APROVADAS
    assert len(convertidas) == 19
    assert not convertidas & set(divida["rotas"])
    # Lotes 2C-1 (34 rotas) e 2C-2 (72 rotas de academias e catalogos) saem da divida;
    # 2D-1 (39 rotas de nutricao: plano, check-in, diario e IA) tambem sai da divida:
    # 2D-2 (16 rotas de alimentos: catalogo, externos, favoritos e foto de rotulo) tambem sai:
    # 2E (56 rotas de analytics e engines: dashboards, progressao, cardio, volume, corpo, relatorio e IA de analise)
    # tambem sai: 43 + 72 + 39 + 16 + 56 = 226 rotas com permissao declarada;
    # 2F-1 (29 rotas de `app.workout_plan_templates`: modelos de treino) tambem sai: 226 + 29 = 255;
    # 2F-2 (55 rotas de treino: registro, Modo Treino, planos, preferencias, catalogo, grupamentos e IA)
    # tambem sai (divida 97 -> 42): 255 + 55 = 310;
    # 2F-3 (42 rotas de corpo, check-in, contexto, sono, Intervals e recomendacoes de cardio)
    # fecha o lote 2 (divida 42 -> 0): 310 + 42 = 352. 0 + 6 + 9 + 352 = 367.
    com_permissao = {rota for rota, declaradas in _all_project_routes().items() if declaradas}
    # Lote 3A: + 11 rotas de `/access` (admin.grupos): 352 + 11 = 363. 0 + 6 + 9 + 363 = 378.
    # Lote 5A: + 3 rotas de vinculos (admin.grupos) e + 1 so-autenticacao (my-students):
    # 363 + 3 = 366 com permissao; 0 + 6 + 10 + 366 = 382.
    assert len(com_permissao) == 366
    assert set(ROTAS_NOVAS_POS_RETRATO) <= com_permissao
    assert not com_permissao & set(divida["rotas"])
    assert len(divida["rotas"]) + len(allowlist["publicas"]) + len(allowlist["so_autenticacao"]) + len(com_permissao) == 382
    assert not com_permissao & {item["rota"] for item in allowlist["publicas"] + allowlist["so_autenticacao"]}


def test_com_divida_zerada_toda_rota_viva_esta_em_exatamente_uma_das_tres_categorias():
    """Lote 2F-3 fechou a divida: toda rota viva dos dois apps (`app.main` e `app.main_engines`)
    declara `require_permission`, e publica (com motivo) ou e so autenticacao pinada.
    Nenhuma fica fora nem em duas categorias."""
    allowlist = _load_allowlist()
    assert allowlist["divida"]["rotas"] == [] and allowlist["divida"]["teto"] == 0
    vivas = _all_project_routes()
    com_permissao = {rota for rota, declaradas in vivas.items() if declaradas}
    publicas = {item["rota"] for item in allowlist["publicas"]}
    so_autenticacao = {item["rota"] for item in allowlist["so_autenticacao"]}
    assert so_autenticacao == SO_AUTENTICACAO_APROVADAS
    assert len(publicas) == 6 and len(so_autenticacao) == 10 and len(com_permissao) == 366
    assert not (com_permissao & publicas) and not (com_permissao & so_autenticacao) and not (publicas & so_autenticacao)
    sem_categoria = set(vivas) - com_permissao - publicas - so_autenticacao
    assert sem_categoria == set(), sorted(sem_categoria)
    # e nenhuma categoria lista rota que nao existe mais
    assert (publicas | so_autenticacao) <= set(vivas)
    assert len(vivas) == 382


def test_rotas_novas_pos_retrato_sao_exatamente_as_vivas_de_access_admin_com_admin_grupos():
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") != "app.access_admin":
                continue
            for metodo in route.methods - _IGNORED_METHODS:
                chave = f"{metodo} {route.path}"
                if chave == ROTA_NOVA_SO_AUTENTICACAO:
                    assert declared_permissions(route) == []  # so autenticacao, pinada a parte
                    continue
                vivas[chave] = declared_permissions(route)
    assert len(ROTAS_NOVAS_POS_RETRATO) == 14
    assert set(vivas) == set(ROTAS_NOVAS_POS_RETRATO), sorted(set(vivas) ^ set(ROTAS_NOVAS_POS_RETRATO))
    acao_por_metodo = {"GET": "ver", "POST": "editar", "PUT": "editar", "PATCH": "editar", "DELETE": "excluir"}
    for rota, declaradas in vivas.items():
        esperada = ("admin.grupos", acao_por_metodo[rota.split(" ", 1)[0]])
        assert declaradas == [esperada], (rota, declaradas)
        assert ROTAS_NOVAS_POS_RETRATO[rota] == esperada, rota
    # so existem no app api-core
    assert not [r for r in enumerate_routes(engines_app) if r in ROTAS_NOVAS_POS_RETRATO]


def test_pino_so_autenticacao_acusa_rota_nova_removida_e_duplicada():
    nova = _allowlist(so_autenticacao=sorted(SO_AUTENTICACAO_APROVADAS | {"GET /x"}))
    problemas = check_so_autenticacao_pinada(nova)
    assert len(problemas) == 1 and "GET /x" in problemas[0] and "REVISAO DE SEGURANCA" in problemas[0]
    faltando = _allowlist(so_autenticacao=sorted(SO_AUTENTICACAO_APROVADAS - {"GET /auth/me"}))
    assert any("GET /auth/me" in p and "saiu" in p for p in check_so_autenticacao_pinada(faltando))
    dup = _allowlist(so_autenticacao=sorted(SO_AUTENTICACAO_APROVADAS) + ["GET /auth/me"])
    assert any("duplicadas" in p for p in check_so_autenticacao_pinada(dup))


def test_guarda_verde_quando_rota_so_autenticacao_tem_identidade_e_sem_permissao():
    app = _mini_app()
    assert check_coverage(
        enumerate_routes(app),
        _allowlist(so_autenticacao=["GET /sem-permissao"]),
        enumerate_identity_routes(app),
    ) == []


def test_guarda_acusa_so_autenticacao_que_declara_permissao():
    app = _mini_app()
    problemas = check_coverage(
        enumerate_routes(app),
        _allowlist(["GET /sem-permissao"], so_autenticacao=["GET /protegida"]),
        enumerate_identity_routes(app),
    )
    assert len(problemas) == 1 and "GET /protegida" in problemas[0] and "declara permissao" in problemas[0]


def test_guarda_acusa_so_autenticacao_sem_identidade_na_arvore():
    app = FastAPI()

    @app.get("/aberta")
    def aberta():
        return {}

    problemas = check_coverage(
        enumerate_routes(app), _allowlist(so_autenticacao=["GET /aberta"]), enumerate_identity_routes(app)
    )
    assert len(problemas) == 1 and "GET /aberta" in problemas[0] and "get_current_identity" in problemas[0]


def test_guarda_acusa_so_autenticacao_tambem_em_divida_ou_publicas():
    app = _mini_app()
    ids = enumerate_identity_routes(app)
    em_divida = check_coverage(
        enumerate_routes(app),
        _allowlist(["GET /sem-permissao"], so_autenticacao=["GET /sem-permissao"]),
        ids,
    )
    assert any("so_autenticacao E em divida" in p for p in em_divida)
    em_publicas = check_coverage(
        enumerate_routes(app),
        _allowlist(publicas=["GET /sem-permissao"], so_autenticacao=["GET /sem-permissao"]),
        ids,
    )
    assert any("so_autenticacao E em publicas" in p for p in em_publicas)


def test_guarda_acusa_so_autenticacao_inexistente_e_duplicada():
    app = _mini_app()
    problemas = check_coverage(
        enumerate_routes(app),
        _allowlist(so_autenticacao=["GET /sem-permissao", "GET /fantasma", "GET /sem-permissao"]),
        enumerate_identity_routes(app),
    )
    assert any("GET /fantasma" in p and "nao existe mais" in p for p in problemas)
    assert any("duplicadas" in p for p in problemas)


def test_rota_sem_permissao_fora_de_qualquer_categoria_continua_acusada():
    app = _mini_app()
    problemas = check_coverage(enumerate_routes(app), _allowlist(), enumerate_identity_routes(app))
    assert len(problemas) == 1 and "GET /sem-permissao" in problemas[0]


def test_apps_reais_nao_tem_rota_montada():
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    for app in (main_app, engines_app):
        assert not [r for r in app.routes if isinstance(r, (Mount, Host))]
        enumerate_routes(app)  # nao levanta
