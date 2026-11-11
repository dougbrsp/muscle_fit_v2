"""Retrato dos guards antigos (ADR 0011, pre-condicoes do lote 2): ver
`tests/support/access_retrato.py` (regenerar: `cd api && python -m tests.support.access_retrato`).
Este teste nunca regenera o JSON.

Rodar: cd api && python -m pytest tests/unit/test_access_retrato_guards.py -q --tb=line
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI, Header

from app.devices import require_device_credential, require_user_credential
from app.security import (
    Identity,
    get_current_identity,
    require_admin,
    require_admin_or_pt,
    resolve_identity_from_credentials,
)
from app.users import require_session_identity
from tests.support import access_retrato as ar

ALLOWLIST_PATH = Path(__file__).with_name("access_cobertura_allowlist.json")
COMO_REGENERAR = "Regenerar so de proposito: cd api && python -m tests.support.access_retrato"


def _por_chave(app: FastAPI) -> dict:
    out = {}
    for route in ar._iter_api_routes(app):
        for method in sorted(route.methods):
            if method not in ar._IGNORED_METHODS:
                out[f"{method} {route.path}"] = ar.classify_route(route)
    return out


# ---------------------------------------------------------------------------
# Retrato real
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vivas() -> dict:
    return ar.classificar_rotas_vivas()


@pytest.fixture(scope="module")
def retrato() -> dict:
    return ar.carregar_retrato()


def test_conjunto_de_rotas_do_retrato_e_vivas_mais_removidas(vivas, retrato):
    removidas = set(retrato["rotas_removidas_depois_do_retrato"])
    congeladas = set(retrato["rotas"])
    assert removidas <= congeladas, f"Removidas fora do retrato: {sorted(removidas - congeladas)}"
    novas = sorted(set(vivas) - congeladas)
    sumiram = sorted(congeladas - set(vivas) - removidas)
    assert not novas, (
        f"Rotas novas sem retrato: {novas[:10]}. Rota nova nasce com require_permission; o retrato cobre so o "
        "estado antigo. Se for deliberado, " + COMO_REGENERAR
    )
    assert not sumiram, (
        f"Rotas do retrato que sumiram: {sumiram[:10]}. Declare-as em "
        "`rotas_removidas_depois_do_retrato` no JSON (lista explicita)."
    )
    assert not (removidas & set(vivas)), "Rota declarada removida mas ainda existe."


def test_retrato_cobre_as_mesmas_rotas_da_guarda_de_cobertura(retrato):
    allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    cobertura = set(allowlist["divida"]["rotas"]) | {p["rota"] for p in allowlist["publicas"]}
    assert cobertura <= set(retrato["rotas"])


def test_rotas_ainda_em_divida_mantem_a_classificacao_do_retrato(vivas, retrato):
    divida = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))["divida"]["rotas"]
    problemas = []
    for rota in divida:
        if rota not in vivas or rota not in retrato["rotas"]:
            continue
        antigo, atual = retrato["rotas"][rota], vivas[rota]
        for campo in ("classe", "checagem_no_handler"):
            if antigo[campo] != atual[campo]:
                problemas.append(f"{rota}: {campo} era {antigo[campo]!r}, agora {atual[campo]!r}")
    assert not problemas, (
        "O guard de uma rota ainda em divida.rotas mudou sem converte-la. Converta a rota para "
        "require_permission E remova-a de divida.rotas (e do teto) na mesma mudanca; o retrato congelado "
        "so e comparado enquanto a rota esta na divida. Se a mudanca era so refatoracao do guard "
        "antigo, " + COMO_REGENERAR + "\n" + "\n".join(problemas[:15])
    )


def test_nenhuma_outra_ou_identidade_no_corpo_sem_justificativa(retrato):
    sem = [
        r
        for r, e in retrato["rotas"].items()
        if e["classe"] in ar.CLASSES_COM_JUSTIFICATIVA and not str(e.get("justificativa", "")).strip()
    ]
    assert not sem, f"Rotas sem justificativa: {sem[:10]}"


def test_so_classes_conhecidas_e_publicas_sao_as_aprovadas(retrato):
    assert {e["classe"] for e in retrato["rotas"].values()} <= set(ar.CLASSES)
    allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    aprovadas = {p["rota"] for p in allowlist["publicas"]}
    publicas = {r for r, e in retrato["rotas"].items() if e["classe"] == "publica"}
    # Publicas do retrato nao podem exceder as aprovadas na guarda de cobertura.
    assert publicas <= aprovadas, sorted(publicas - aprovadas)


def test_json_e_deterministico_e_bem_formatado(retrato):
    texto = ar.RETRATO_PATH.read_text(encoding="utf-8")
    assert texto.endswith("\n") and not texto.endswith("\n\n")
    assert texto == ar.serializar(retrato)
    chaves = list(retrato["rotas"])
    assert chaves == sorted(chaves)
    assert ar.serializar(retrato) == ar.serializar(json.loads(ar.serializar(retrato)))


def test_geracao_viva_e_reprodutivel(vivas):
    assert ar.serializar(ar.montar_retrato()) == ar.serializar(ar.montar_retrato())


# ---------------------------------------------------------------------------
# Funcao de decisao
# ---------------------------------------------------------------------------

_TABELA = {
    # classe: {(papel, tipo): permitido}  (omitidos = ver abaixo)
    "admin": lambda p, t: p == "admin",
    "admin_ou_pt": lambda p, t: p in ("admin", "personal_trainer"),
    "sessao": lambda p, t: t == "session",
    "usuario_sem_dispositivo": lambda p, t: t != "device",
    "dispositivo": lambda p, t: t == "device",
    "autenticado": lambda p, t: True,
    "publica": lambda p, t: True,
}


@pytest.mark.parametrize("classe", sorted(_TABELA))
@pytest.mark.parametrize("papel", ar.PAPEIS)
@pytest.mark.parametrize("tipo", ar.TIPOS_IDENTIDADE)
def test_decisao_do_guard_antigo(classe, papel, tipo):
    if tipo == "master_token" and papel != "admin":
        with pytest.raises(ValueError, match="token mestre"):
            ar.guard_antigo_permitia(classe, papel, tipo)
        return
    assert ar.guard_antigo_permitia(classe, papel, tipo) is _TABELA[classe](papel, tipo)


@pytest.mark.parametrize("classe", ["outra", "identidade_no_corpo"])
def test_decisao_levanta_para_classe_sem_decisao_automatica(classe):
    with pytest.raises(ar.SemDecisaoAutomatica, match="nao tem decisao automatica"):
        ar.guard_antigo_permitia(classe, "admin", "session")


def test_decisao_rejeita_entradas_invalidas():
    with pytest.raises(ValueError, match="classe desconhecida"):
        ar.guard_antigo_permitia("inventada", "admin", "session")
    with pytest.raises(ValueError, match="papel desconhecido"):
        ar.guard_antigo_permitia("admin", "root", "session")
    with pytest.raises(ValueError, match="tipo de identidade"):
        ar.guard_antigo_permitia("admin", "admin", "cookie")


def test_decisao_bate_com_os_guards_reais():
    """Confere a tabela contra o codigo dos guards (nao so contra a descricao)."""
    from fastapi import HTTPException

    guards = {
        "admin": require_admin,
        "admin_ou_pt": require_admin_or_pt,
        "sessao": require_session_identity,
        "usuario_sem_dispositivo": require_user_credential,
        "dispositivo": require_device_credential,
    }
    for classe, guard in guards.items():
        for papel in ar.PAPEIS:
            for tipo in ar.TIPOS_IDENTIDADE:
                if tipo == "master_token" and papel != "admin":
                    continue
                ident = Identity(
                    user_id=1,
                    role=papel,
                    source=tipo,
                    device_id=7 if tipo == "device" else None,
                )
                try:
                    guard(ident)
                    real = True
                except HTTPException:
                    real = False
                assert real is ar.guard_antigo_permitia(classe, papel, tipo), (classe, papel, tipo)


# ---------------------------------------------------------------------------
# Classificador (app minimo)
# ---------------------------------------------------------------------------


def _helper(token):
    return resolve_identity_from_credentials(token)


def _interno(identity):
    return identity.role


def _classe(app: FastAPI, chave: str) -> dict:
    return _por_chave(app)[chave]


def test_classificador_detecta_cada_classe_no_parametro_do_handler():
    app = FastAPI()

    @app.get("/a")
    def a(_: Identity = Depends(require_admin)):
        return {}

    @app.get("/pt")
    def pt(_: Identity = Depends(require_admin_or_pt)):
        return {}

    @app.get("/s")
    def s(_: Identity = Depends(require_session_identity)):
        return {}

    @app.get("/u")
    def u(_: Identity = Depends(require_user_credential)):
        return {}

    @app.get("/d")
    def d(_: Identity = Depends(require_device_credential)):
        return {}

    @app.get("/auth")
    def auth(_: Identity = Depends(get_current_identity)):
        return {}

    @app.get("/pub")
    def pub():
        return {}

    got = {k: v["classe"] for k, v in _por_chave(app).items()}
    assert got == {
        "GET /a": "admin",
        "GET /pt": "admin_ou_pt",
        "GET /s": "sessao",
        "GET /u": "usuario_sem_dispositivo",
        "GET /d": "dispositivo",
        "GET /auth": "autenticado",
        "GET /pub": "publica",
    }


def test_classificador_ve_guard_no_decorator_e_no_router():
    app = FastAPI()
    router = APIRouter(dependencies=[Depends(require_admin)])

    @router.get("/no-router")
    def no_router():
        return {}

    app.include_router(router, prefix="/v1")

    @app.get("/no-decorator", dependencies=[Depends(require_admin_or_pt)])
    def no_decorator():
        return {}

    got = _por_chave(app)
    assert got["GET /v1/no-router"]["classe"] == "admin"
    assert got["GET /no-decorator"]["classe"] == "admin_ou_pt"


def test_classificador_ve_guard_em_dependencia_aninhada_propria():
    app = FastAPI()

    def propria(identity: Identity = Depends(require_session_identity)) -> Identity:
        return identity

    @app.get("/x")
    def x(_=Depends(propria)):
        return {}

    assert _classe(app, "GET /x")["classe"] == "sessao"


def test_precedencia_do_mais_restritivo_e_combinacao_sem_classe_vira_outra():
    app = FastAPI()

    @app.get("/admin-e-pt", dependencies=[Depends(require_admin_or_pt)])
    def admin_e_pt(_: Identity = Depends(require_admin)):
        return {}

    @app.get("/sessao-e-usuario", dependencies=[Depends(require_user_credential)])
    def sessao_e_usuario(_: Identity = Depends(require_session_identity)):
        return {}

    @app.get("/admin-e-sessao", dependencies=[Depends(require_session_identity)])
    def admin_e_sessao(_: Identity = Depends(require_admin)):
        return {}

    got = _por_chave(app)
    assert got["GET /admin-e-pt"]["classe"] == "admin"
    assert got["GET /sessao-e-usuario"]["classe"] == "sessao"
    assert got["GET /admin-e-sessao"]["classe"] == "outra"
    assert "combinacao" in got["GET /admin-e-sessao"]["detalhe"][0]


def test_identidade_resolvida_no_corpo_nunca_e_publica():
    app = FastAPI()

    @app.get("/corpo")
    def corpo(x_api_token: str | None = Header(default=None)):
        resolve_identity_from_credentials(x_api_token)
        return {}

    @app.get("/via-helper")
    def via_helper(x_api_token: str | None = Header(default=None)):
        _helper(x_api_token)
        return {}

    got = _por_chave(app)
    assert got["GET /corpo"]["classe"] == "identidade_no_corpo"
    assert "resolve_identity_from_credentials" in got["GET /corpo"]["detalhe"][0]
    assert got["GET /via-helper"]["classe"] == "identidade_no_corpo"
    assert "_helper" in got["GET /via-helper"]["detalhe"][0]


def test_credencial_recebida_sem_resolucao_reconhecida_vira_outra_e_nunca_publica():
    app = FastAPI()

    @app.get("/estranha")
    def estranha(x_api_token: str | None = Header(default=None)):
        return {"t": bool(x_api_token)}

    entry = _classe(app, "GET /estranha")
    assert entry["classe"] == "outra" and "x_api_token" in entry["detalhe"][0]


def test_checagem_no_handler_por_ast_ordenada_e_seguindo_helper_do_mesmo_modulo():
    app = FastAPI()

    def is_admin(identity):  # nome reconhecido pela analise
        return identity.role == "admin"

    @app.get("/papel")
    def papel(identity: Identity = Depends(get_current_identity)):
        if identity.role == "regular":
            return {}
        return {"a": is_admin(identity)}

    @app.get("/limpa")
    def limpa(_: Identity = Depends(get_current_identity)):
        return {}

    got = _por_chave(app)
    assert got["GET /papel"]["checagem_no_handler"] == ["identity.role", "is_admin"]
    assert got["GET /limpa"]["checagem_no_handler"] == []


def test_checagem_vista_em_helper_de_um_nivel_no_mesmo_modulo():
    app = FastAPI()

    @app.get("/helper")
    def helper(identity: Identity = Depends(get_current_identity)):
        return {"r": _interno(identity)}

    assert _classe(app, "GET /helper")["checagem_no_handler"] == ["identity.role"]


def test_checagem_nao_segue_funcao_de_outro_modulo_fora_de_app():
    import json as _json  # funcao externa: fora do alcance declarado

    app = FastAPI()

    @app.get("/ext")
    def ext(_: Identity = Depends(get_current_identity)):
        return _json.dumps({})

    assert _classe(app, "GET /ext")["checagem_no_handler"] == []


def test_retrato_marca_logout_como_outra_com_justificativa(retrato):
    entry = retrato["rotas"]["POST /auth/logout"]
    assert entry["classe"] == "outra" and entry["justificativa"].strip()
