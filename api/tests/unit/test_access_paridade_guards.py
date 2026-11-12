"""Paridade entre o guard ANTIGO e a permissao NOVA das rotas convertidas (ADR 0011,
pre-condicoes do lote 2).

Dirigido pelo retrato congelado (`tests/unit/data/access_retrato_guards_antigos.json`,
gerado por `tests/support/access_retrato.py`) e pela enumeracao viva das rotas, de
modo que os lotes 2C a 2F so precisem trocar o guard: a rota convertida entra
sozinha na verificacao.

Escopo. Para cada rota viva que hoje declara `require_permission` ou esta em
`so_autenticacao`, e para papel in {admin, personal_trainer, regular} x identidade in
{session, device, master_token} (token mestre so com admin):

- rota com permissao: decisao antiga (`guard_antigo_permitia` com a classe do retrato)
  == decisao nova = tipo de credencial exigido pela classe (`usuario_sem_dispositivo`
  recusa dispositivo; `dispositivo` exige dispositivo; `sessao` exige sessao; as demais
  nao restringem o tipo) E `has_permission` de TODAS as permissoes declaradas sobre as
  concessoes de `DEFAULT_GRANTS_BY_GROUP` do grupo do papel (admin e token mestre:
  acesso total, resolvido por `merge_grants`/`resolve_permissions`, sem banco);
- rota `so_autenticacao`: o guard nao mudou, entao a classificacao VIVA tem de ser igual
  a do retrato;
- rota convertida cuja classe do retrato nao tem decisao automatica (`identidade_no_corpo`,
  `outra`) falha ate existir um caso manual em `CASOS_MANUAIS` (decisao antiga provada a
  mao) neste arquivo.

Casos explicitos: usuario sem grupo e negado em toda rota com permissao e passa nas de
`so_autenticacao`; papel fora dos tres valores fica sem grupo (negado).

O que este teste NAO prova:
- checagens DENTRO do handler (papel, `authorize_self_or_all`, `require_additional_permission`,
  trava do ultimo admin): sao cobertas pelos testes HTTP de cada rota;
- posse da linha (ex.: dispositivo/job do proprio usuario) e escopo (`proprios` x `alunos`
  x `todos`): `has_permission` aceita qualquer escopo;
- que as concessoes de `DEFAULT_GRANTS_BY_GROUP` sejam as gravadas no banco (a paridade
  da migracao com o catalogo e de `test_migracao_113_concessoes.py`);
- rotas nao convertidas (sem permissao e fora de `so_autenticacao`) e a existencia, no
  retrato, de rota criada depois dele (falha pedindo regeneracao proposital).

Escopo minimo (lote 2F-1): `require_permission(..., min_scope=...)` exige que o escopo
concedido cubra o minimo declarado. `decisao_nova` aceita `declaradas` como pares
`(recurso, acao)` ou triplas `(recurso, acao, escopo_minimo)` (de `declared_permission_scopes`)
e, com escopo minimo, exige `scope_covers(escopo concedido, minimo)` via `permission_scope`.
Pares seguem aceitando qualquer escopo. A paridade geral usa as triplas vivas de todas as
rotas. O 2F-1 prova que ignorar o escopo minimo faria o `regular` (`modelos.treino`/ver so
`proprios`) passar nas GET de gestao, que o guard antigo (`admin_ou_pt`) recusava.

Escopo da paridade: ela cobre o guard de ENTRADA (credencial + permissao declarada). A regra de
terceiros (escopo proprios x alunos x todos, `authorize_self_or_all`) e provada em
`test_access_catalogo_e_resolucao.py` e nos testes HTTP de cada rota.

Rodar: cd api && python -m pytest tests/unit/test_access_paridade_guards.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from app.access import (
    DEFAULT_GRANTS_BY_GROUP,
    ROLE_TO_SYSTEM_GROUP,
    EffectivePermissions,
    declared_permission_scopes,
    has_permission,
    min_scope_of,
    permission_of,
    resolve_permissions,
    scope_covers,
)
from app.access.service import merge_grants, permission_scope
from app.security import Identity
from tests.support.access_retrato import (
    PAPEIS,
    TIPOS_IDENTIDADE,
    SemDecisaoAutomatica,
    carregar_retrato,
    classificar_rotas_vivas,
    guard_antigo_permitia,
)
from tests.unit.test_access_guarda_cobertura_rotas import _all_project_routes, _iter_api_routes

ALLOWLIST_PATH = Path(__file__).with_name("access_cobertura_allowlist.json")

# Lote 2C-2 (academias e catalogos, 72 rotas `identidade_no_corpo`). Decisao do guard de
# ENTRADA ANTIGO provada a mao: o guard de `gyms.py`/`exercise_*`/`muscle_volume_classes.py`/
# `equipment_exercises.py` era `_require_identity` (= `resolve_identity_from_credentials`),
# que aceita qualquer credencial valida (sessao, dispositivo, token mestre) de qualquer papel;
# a checagem de papel vinha depois, no handler, e e lida do campo `checagem_no_handler` do
# retrato congelado:
# - sem checagem no handler, ou so `identity.role` de dono-ou-admin (POST `/gyms`,
#   PUT/DELETE `/gyms/{id}`, escritas de `/gym-equipment` e `/gym-equipment-catalog`): o guard
#   de entrada aceitava qualquer papel -> "qualquer autenticado". A posse da linha (dono ou
#   escopo `todos`) e provada em testes HTTP, nao aqui;
# - `_require_admin_identity` no handler: so admin (token mestre resolve para admin).
# PUT `/gyms/{id}` e o caso misto: o retrato lista `_require_admin_identity` porque campos
# admin-only (name/opens_at/closes_at) a exigiam; a paridade cobre so o guard de entrada
# (qualquer autenticado). A exigencia de `admin.catalogos` para esses campos vive no handler
# (`require_additional_permission`) e e coberta por teste HTTP.
_ROTAS_2C2_QUALQUER_AUTENTICADO = (
    "DELETE /gym-equipment-catalog/{link_id}",
    "DELETE /gym-equipment/{link_id}",
    "DELETE /gyms/{gym_id}",
    "GET /equipment",
    "GET /equipment-exercises",
    "GET /equipment-types",
    "GET /equipment/{equipment_id}/photo",
    "GET /exercise-external-mappings",
    "GET /exercise-external-mappings/providers",
    "GET /exercise-mapping-catalog",
    "GET /exercise-mapping-catalog/export/markdown",
    "GET /exercise-mapping-catalog/groups",
    "GET /exercise-mapping-catalog/health",
    "GET /exercise-mapping-catalog/{mapping_id}",
    "GET /exercise-mapping-catalog/{mapping_id}/aliases",
    "GET /exercises",
    "GET /exercises/execution-descriptions/pending",
    "GET /exercises/{exercise_id}",
    "GET /exercises/{exercise_id}/aliases",
    "GET /exercises/{exercise_id}/external-mappings",
    "GET /gym-equipment",
    "GET /gym-equipment-catalog",
    "GET /gym-equipment/recommend",
    "GET /gym-equipment/recommend-all",
    "GET /gyms",
    "GET /gyms/{gym_id}",
    "GET /gyms/{gym_id}/logo",
    "GET /muscle-contributions/{mapping_id}",
    "GET /muscle-groups",
    "GET /muscle-volume-classes",
    "POST /gym-equipment",
    "POST /gym-equipment-catalog",
    "POST /gyms",
    "PUT /gym-equipment/{link_id}",
    "PUT /gyms/{gym_id}",
)

_ROTAS_2C2_SO_ADMIN = (
    "DELETE /equipment-exercises/{link_id}",
    "DELETE /equipment-types/{type_id}",
    "DELETE /equipment/{equipment_id}",
    "DELETE /equipment/{equipment_id}/photo",
    "DELETE /exercise-mapping-catalog/aliases/{alias_id}",
    "DELETE /exercise-mapping-catalog/{mapping_id}",
    "DELETE /exercises/aliases/{alias_id}",
    "DELETE /exercises/external-mappings/{mapping_row_id}",
    "DELETE /gyms/{gym_id}/logo",
    "DELETE /muscle-contributions/{mapping_id}/{muscle_group_id}",
    "DELETE /muscle-volume-classes/{class_id}",
    "POST /equipment",
    "POST /equipment-exercises",
    "POST /equipment-types",
    "POST /equipment/{equipment_id}/photo",
    "POST /exercise-mapping-catalog",
    "POST /exercise-mapping-catalog/{mapping_id}/aliases",
    "POST /exercises",
    "POST /exercises/execution-descriptions/generate-batch",
    "POST /exercises/{exercise_id}/aliases",
    "POST /exercises/{exercise_id}/execution-description/generate",
    "POST /exercises/{exercise_id}/external-mappings",
    "POST /gyms/{gym_id}/logo",
    "POST /muscle-groups",
    "POST /muscle-volume-classes",
    "POST /muscle-volume-classes/{class_id}/copy-from-default",
    "PUT /equipment-exercises/{link_id}",
    "PUT /equipment-types/{type_id}",
    "PUT /equipment/{equipment_id}",
    "PUT /exercise-mapping-catalog/{mapping_id}",
    "PUT /exercises/external-mappings/{mapping_row_id}",
    "PUT /exercises/{exercise_id}",
    "PUT /muscle-contributions/{mapping_id}/{muscle_group_id}",
    "PUT /muscle-groups/{muscle_group_id}",
    "PUT /muscle-volume-classes/{class_id}",
    "PUT /muscle-volume-classes/{class_id}/groups",
    "PUT /muscle-volume-classes/{class_id}/ranges",
)


def _caso_qualquer_autenticado() -> Callable[[str, str], bool]:
    """Guard antigo de entrada: qualquer papel e qualquer tipo de credencial valida."""
    return lambda papel, tipo: True


def _caso_so_admin() -> Callable[[str, str], bool]:
    """Guard antigo `_require_admin_identity`: so papel admin (token mestre resolve para admin)."""
    return lambda papel, tipo: papel == "admin"


# rota -> funcao(papel, tipo_identidade) -> bool com a decisao do guard ANTIGO provada a
# mao, para rotas convertidas cuja classe do retrato e `identidade_no_corpo`/`outra`.
CASOS_MANUAIS: dict[str, Callable[[str, str], bool]] = {
    **{rota: _caso_qualquer_autenticado() for rota in _ROTAS_2C2_QUALQUER_AUTENTICADO},
    **{rota: _caso_so_admin() for rota in _ROTAS_2C2_SO_ADMIN},
}

_CLASSES_DE_CREDENCIAL = ("usuario_sem_dispositivo", "dispositivo", "sessao")


def _combinacoes() -> list[tuple[str, str]]:
    return [
        (papel, tipo)
        for papel in PAPEIS
        for tipo in TIPOS_IDENTIDADE
        if tipo != "master_token" or papel == "admin"
    ]


def permissoes_do_papel(papel: str, tipo: str) -> EffectivePermissions:
    """Permissoes efetivas SEM banco: token mestre = acesso total; demais = grupo de
    sistema do papel com as concessoes padrao; papel desconhecido = sem grupo."""
    if tipo == "master_token":
        return resolve_permissions(Identity(user_id=1, role="admin", source="master_token"))
    grupo = ROLE_TO_SYSTEM_GROUP.get(papel)
    if grupo is None:
        return EffectivePermissions()
    linhas = [
        {"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s}
        for r, a, s in DEFAULT_GRANTS_BY_GROUP[grupo]
    ] or [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
    return merge_grants(linhas)


def credencial_aceita(classe: str, tipo: str) -> bool:
    """Tipo de credencial exigido pela rota, lido da classe do retrato."""
    if classe == "usuario_sem_dispositivo":
        return tipo != "device"
    if classe == "dispositivo":
        return tipo == "device"
    if classe == "sessao":
        return tipo == "session"
    return True


def _permissao_atendida(permissoes: EffectivePermissions, declarada: tuple) -> bool:
    """Par (recurso, acao): qualquer escopo basta. Tripla com escopo minimo: o escopo
    concedido tem de cobri-lo (`scope_covers`)."""
    recurso, acao = declarada[0], declarada[1]
    minimo = declarada[2] if len(declarada) > 2 else None
    if minimo is None:
        return has_permission(permissoes, recurso, acao)
    concedido = permission_scope(permissoes, recurso, acao)
    return concedido is not None and scope_covers(concedido, minimo)


def decisao_nova(classe: str, declaradas: list[tuple], permissoes: EffectivePermissions, tipo: str) -> bool:
    return credencial_aceita(classe, tipo) and all(_permissao_atendida(permissoes, d) for d in declaradas)


def _decisao_antiga(rota: str, classe: str, papel: str, tipo: str) -> bool:
    if rota in CASOS_MANUAIS:
        return CASOS_MANUAIS[rota](papel, tipo)
    return guard_antigo_permitia(classe, papel, tipo)


def divergencias(
    rota: str,
    classe: str,
    declaradas: list[tuple],
    permissoes_por_papel: Callable[[str, str], EffectivePermissions] = permissoes_do_papel,
) -> list[str]:
    """Combinacoes (papel, tipo) em que guard antigo e permissao nova decidem diferente."""
    if classe in ("identidade_no_corpo", "outra") and rota not in CASOS_MANUAIS:
        return [
            f"{rota}: classe {classe!r} do retrato nao tem decisao automatica; "
            "acrescente um caso manual em CASOS_MANUAIS neste teste (decisao antiga provada a mao)."
        ]
    problemas = []
    for papel, tipo in _combinacoes():
        antigo = _decisao_antiga(rota, classe, papel, tipo)
        novo = decisao_nova(classe, declaradas, permissoes_por_papel(papel, tipo), tipo)
        if antigo != novo:
            problemas.append(
                f"{rota} [{papel}/{tipo}]: guard antigo ({classe}) "
                f"{'permitia' if antigo else 'recusava'}, permissao nova {'permite' if novo else 'recusa'}."
            )
    return problemas


def _so_autenticacao() -> list[str]:
    return [item["rota"] for item in json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))["so_autenticacao"]]


def _rotas_convertidas() -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    todas = _all_project_routes()
    return {r: p for r, p in todas.items() if p}, _so_autenticacao()


def _declaradas_com_escopo() -> dict[str, list[tuple[str, str, str | None]]]:
    """Rota viva -> triplas (recurso, acao, escopo_minimo) declaradas (so rotas com permissao)."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    rotas: dict[str, list[tuple[str, str, str | None]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            triplas = declared_permission_scopes(route)
            if not triplas:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                rotas.setdefault(f"{metodo} {route.path}", []).extend(triplas)
    return rotas


# ---------------------------------------------------------------------------
# Paridade real
# ---------------------------------------------------------------------------


def test_ha_rotas_com_permissao_e_so_autenticacao_para_verificar():
    com_permissao, so_auth = _rotas_convertidas()
    assert len(com_permissao) >= 9 and len(so_auth) == 9


def test_toda_rota_convertida_esta_no_retrato():
    retrato = carregar_retrato()
    removidas = set(retrato["rotas_removidas_depois_do_retrato"])
    com_permissao, so_auth = _rotas_convertidas()
    faltando = sorted((set(com_permissao) | set(so_auth)) - set(retrato["rotas"]) - removidas)
    assert not faltando, (
        "Rota convertida fora do retrato dos guards antigos (rota criada depois dele?): "
        f"{faltando}. Regenere o retrato de proposito ou prove a paridade a mao."
    )


def test_paridade_guard_antigo_x_permissao_nova_nas_rotas_com_permissao():
    retrato = carregar_retrato()["rotas"]
    com_permissao = _declaradas_com_escopo()
    problemas: list[str] = []
    for rota, declaradas in sorted(com_permissao.items()):
        if rota not in retrato:
            continue  # acusado por test_toda_rota_convertida_esta_no_retrato
        problemas += divergencias(rota, retrato[rota]["classe"], declaradas)
    assert not problemas, "Paridade violada:\n" + "\n".join(problemas[:30])


def test_credencial_exigida_pela_classe_do_retrato_continua_na_arvore_da_rota():
    """A classe de credencial lida do retrato so vale se o guard de credencial ainda
    esta na rota viva (nao foi removido na conversao)."""
    retrato = carregar_retrato()["rotas"]
    vivas = classificar_rotas_vivas()
    com_permissao, _ = _rotas_convertidas()
    for rota in sorted(com_permissao):
        classe = retrato.get(rota, {}).get("classe")
        if classe in _CLASSES_DE_CREDENCIAL:
            assert vivas[rota]["classe"] == classe, (
                f"{rota}: o retrato exige credencial {classe!r} mas a rota viva e "
                f"{vivas[rota]['classe']!r}; o guard de credencial foi removido."
            )


def test_so_autenticacao_tem_a_mesma_classificacao_viva_e_do_retrato():
    retrato = carregar_retrato()["rotas"]
    vivas = classificar_rotas_vivas()
    for rota in _so_autenticacao():
        assert rota in retrato, rota
        assert vivas[rota]["classe"] == retrato[rota]["classe"], (
            f"{rota}: guard mudou (retrato {retrato[rota]['classe']!r}, vivo {vivas[rota]['classe']!r}); "
            "so_autenticacao pressupoe guard inalterado."
        )
        assert retrato[rota]["classe"] in ("autenticado", "sessao"), rota


def test_so_autenticacao_decide_so_pelo_tipo_de_credencial_do_retrato():
    retrato = carregar_retrato()["rotas"]
    for rota in _so_autenticacao():
        classe = retrato[rota]["classe"]
        for papel, tipo in _combinacoes():
            assert guard_antigo_permitia(classe, papel, tipo) == credencial_aceita(classe, tipo), (rota, papel, tipo)


# ---------------------------------------------------------------------------
# Casos explicitos
# ---------------------------------------------------------------------------


def test_usuario_sem_grupo_e_negado_em_toda_rota_com_permissao():
    sem_grupo = EffectivePermissions()
    com_permissao, _ = _rotas_convertidas()
    for rota, declaradas in com_permissao.items():
        for tipo in ("session", "device"):
            assert decisao_nova("autenticado", declaradas, sem_grupo, tipo) is False, (rota, tipo)


def test_usuario_sem_grupo_passa_nas_rotas_so_autenticacao_pelo_guard_antigo():
    retrato = carregar_retrato()["rotas"]
    for rota in _so_autenticacao():
        # Sem grupo nao ha consulta de permissao: a decisao e so a da credencial.
        assert guard_antigo_permitia(retrato[rota]["classe"], "regular", "session") is True, rota


@pytest.mark.parametrize("papel", ["guest", "", "ADMIN", "root"])
def test_papel_fora_dos_tres_valores_fica_sem_grupo_e_e_negado(papel):
    assert ROLE_TO_SYSTEM_GROUP.get(papel) is None
    perms = permissoes_do_papel(papel, "session")
    assert perms.grants == {} and not perms.full_access
    com_permissao, _ = _rotas_convertidas()
    for rota, declaradas in com_permissao.items():
        assert decisao_nova("autenticado", declaradas, perms, "session") is False, rota


def test_admin_e_token_mestre_tem_acesso_total_e_pt_e_regular_nao_tem_admin_usuarios():
    assert permissoes_do_papel("admin", "session").full_access
    assert permissoes_do_papel("admin", "master_token").full_access
    for papel in ("personal_trainer", "regular"):
        perms = permissoes_do_papel(papel, "session")
        assert not has_permission(perms, "admin.usuarios", "ver")
        assert not has_permission(perms, "admin.grupos", "editar")
        assert has_permission(perms, "conta.dispositivos", "editar")


# ---------------------------------------------------------------------------
# Provas de que o teste detecta divergencia (dados sinteticos)
# ---------------------------------------------------------------------------


def test_detecta_rota_admin_cuja_permissao_nova_o_pt_tambem_tem():
    # conta.dispositivos e concedida ao PT: se uma rota `admin` fosse convertida para ela, o PT passaria.
    problemas = divergencias("GET /x", "admin", [("conta.dispositivos", "ver")])
    assert any("personal_trainer" in p and "permite" in p for p in problemas)


def test_detecta_permissao_que_o_guard_antigo_dava_e_a_nova_nega():
    # `autenticado` permitia todos; admin.usuarios nega regular e PT.
    problemas = divergencias("GET /x", "autenticado", [("admin.usuarios", "ver")])
    assert any("regular" in p for p in problemas)


def test_credencial_de_dispositivo_e_recusada_pela_classe_nos_dois_lados():
    assert divergencias("POST /devices/pair", "usuario_sem_dispositivo", [("conta.dispositivos", "editar")]) == []
    # Se a classe antiga recusava dispositivo mas a nova nao restringisse o tipo, divergiria:
    # `autenticado` na nova (sem restricao) x `usuario_sem_dispositivo` na antiga.
    assert not credencial_aceita("usuario_sem_dispositivo", "device")
    assert credencial_aceita("autenticado", "device")


def test_detecta_rota_admin_ou_pt_convertida_para_permissao_que_o_pt_nao_tem():
    problemas = divergencias("POST /x", "admin_ou_pt", [("admin.usuarios", "ver")])
    assert any("personal_trainer" in p and "recusa" in p for p in problemas)


def test_paridade_exata_quando_decisoes_coincidem():
    assert divergencias("GET /users", "admin", [("admin.usuarios", "ver")]) == []
    assert divergencias("GET /devices/me", "dispositivo", [("conta.dispositivos", "ver")]) == []


@pytest.mark.parametrize("classe", ["identidade_no_corpo", "outra"])
def test_classe_sem_decisao_automatica_falha_pedindo_caso_manual(classe):
    problemas = divergencias("GET /y", classe, [("admin.usuarios", "ver")])
    assert len(problemas) == 1 and "CASOS_MANUAIS" in problemas[0]
    with pytest.raises(SemDecisaoAutomatica):
        guard_antigo_permitia(classe, "admin", "session")


def test_caso_manual_substitui_a_decisao_automatica(monkeypatch):
    monkeypatch.setitem(CASOS_MANUAIS, "GET /y", lambda papel, tipo: papel == "admin")
    assert divergencias("GET /y", "outra", [("admin.usuarios", "ver")]) == []
    monkeypatch.setitem(CASOS_MANUAIS, "GET /y", lambda papel, tipo: True)
    assert divergencias("GET /y", "outra", [("admin.usuarios", "ver")]) != []


def test_combinacoes_excluem_token_mestre_de_papel_nao_admin():
    combos = _combinacoes()
    assert len(combos) == 7
    assert ("admin", "master_token") in combos
    assert not [c for c in combos if c[1] == "master_token" and c[0] != "admin"]


# ---------------------------------------------------------------------------
# Lote 2C-1: catalogos e administracao (34 rotas)
# ---------------------------------------------------------------------------

ROTAS_2B = {
    "DELETE /devices/{device_id}", "DELETE /users/{user_id}", "GET /devices", "GET /devices/me",
    "GET /users", "POST /devices/pair", "POST /users", "POST /users/{user_id}/reset-password",
    "PUT /users/{user_id}",
}


MODULOS_2D1 = {
    "app.nutrition_plans": "nutricao.plano",
    "app.nutrition_plan_suggestion": "ia.nutricao",
    "app.nutrition_checkins": "nutricao.checkin",
    "app.nutrition_days": "nutricao.diario",
    "app.nutrition_dashboard": "nutricao.diario",
    "app.nutrition_meals": "nutricao.diario",
    "app.nutrition_meal_parse": "ia.nutricao",
    "app.nutrition_meal_photo": "ia.nutricao",
}
ACAO_POR_METODO = {"GET": "ver", "POST": "editar", "PUT": "editar", "PATCH": "editar", "DELETE": "excluir"}


def _rotas_2d1() -> dict[str, list[tuple[str, str]]]:
    """Rotas vivas dos 8 modulos de nutricao do 2D-1 que declaram permissao."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    com_permissao, _ = _rotas_convertidas()
    rotas: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") not in MODULOS_2D1:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                if chave in com_permissao:
                    rotas[chave] = com_permissao[chave]
    return rotas


MODULOS_2D2 = {
    "app.nutrition_foods": "nutricao.alimentos",
    "app.nutrition_external_foods": "nutricao.alimentos",
    "app.nutrition_favorites": "nutricao.alimentos",
    "app.nutrition_label_photo": "ia.nutricao",
}
ROTA_PROMOTE = "POST /nutrition/foods/{food_id}/promote"


def _rotas_2d2() -> dict[str, list[tuple[str, str]]]:
    """Rotas vivas dos 4 modulos de alimentos do 2D-2 que declaram permissao."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    com_permissao, _ = _rotas_convertidas()
    rotas: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") not in MODULOS_2D2:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                if chave in com_permissao:
                    rotas[chave] = com_permissao[chave]
    return rotas


MODULOS_2E = (
    "app.analytics", "app.analytics_dashboard", "app.dashboard_modules", "app.dashboard_intelligence",
    "app.engines_extra", "app.progression", "app.effort_metrics", "app.reports", "app.workouts_analysis",
)
# O PUT do mapa corporal e do 2C-1 (`admin.catalogos`), nao do 2E, embora more em `app.dashboard_modules`.
ROTA_PUT_BODY_MAP_2C1 = "PUT /analytics/muscle-group-body-map"
# Tabela rota -> recurso do contrato do 2E (acao: GET = ver; POST = editar).
RECURSO_2E = {
    "GET /analytics/body-composition-trend": "corpo.indicadores",
    "GET /analytics/cardio": "cardio",
    "GET /analytics/cardio-agent-context": "cardio",
    "GET /analytics/dashboard-cycle": "dashboards",
    "GET /analytics/dashboard-muscle-drilldown": "volume.grupamentos",
    "GET /analytics/dashboard-muscle-status": "volume.grupamentos",
    "GET /analytics/dashboard-progression": "progressao",
    "GET /analytics/dashboard-progression-by-muscle-group": "progressao",
    "GET /analytics/dashboard-progression-suggestion": "progressao",
    "GET /analytics/dashboard-progression-trend": "progressao",
    "GET /analytics/dashboard-recovery": "dashboards",
    "GET /analytics/dashboard-stimulus-fatigue": "volume.grupamentos",
    "GET /analytics/dashboard-workout-sessions": "dashboards",
    "GET /analytics/dashboard/adherence": "dashboards",
    "GET /analytics/dashboard/body": "corpo.indicadores",
    "GET /analytics/dashboard/cardio": "cardio",
    "GET /analytics/dashboard/context": "dashboards",
    "GET /analytics/dashboard/data-quality": "dashboards",
    "GET /analytics/dashboard/engine": "dashboards",
    "GET /analytics/dashboard/engine/plans/{plan_id}": "dashboards",
    "GET /analytics/dashboard/exercises/{exercise_id}/progress": "progressao",
    "GET /analytics/dashboard/insights": "dashboards",
    "GET /analytics/dashboard/intelligence-package": "dashboards",
    "GET /analytics/dashboard/muscles": "volume.grupamentos",
    "GET /analytics/dashboard/muscles/{muscle_group_id}": "volume.grupamentos",
    "GET /analytics/dashboard/overview": "dashboards",
    "GET /analytics/dashboard/recovery-signals": "dashboards",
    "GET /analytics/dashboard/strength-panel": "progressao",
    "GET /analytics/dashboard/workouts": "dashboards",
    "GET /analytics/dashboard/workouts/{activity_id}": "dashboards",
    "GET /analytics/exercise-progress": "progressao",
    "GET /analytics/health": "dashboards",
    "GET /analytics/methodology-realized": "volume.grupamentos",
    "GET /analytics/muscle-activation-by-activity": "volume.grupamentos",
    "GET /analytics/muscle-group-body-map": "volume.grupamentos",
    "GET /analytics/muscle-groups-daily-activation": "volume.grupamentos",
    "GET /analytics/muscle-groups-equivalent-sets-by-activity": "volume.grupamentos",
    "GET /analytics/muscle-groups-recent-exercise-volume": "volume.grupamentos",
    "GET /analytics/muscle-groups-slot-comparison": "volume.grupamentos",
    "GET /analytics/muscle-recovery": "dashboards",
    "GET /analytics/muscle-recovery-consecutive-check": "dashboards",
    "GET /analytics/rpe-distribution": "dashboards",
    "GET /analytics/summary": "dashboards",
    "GET /analytics/training-evolution": "progressao",
    "GET /analytics/training-trend": "dashboards",
    "GET /analytics/training-volume": "volume.grupamentos",
    "GET /analytics/training-volume-by-split-day": "volume.grupamentos",
    "GET /effort-metrics": "cardio",
    "GET /exercises/suggested-progression": "progressao",
    "GET /exercises/suggested-set-count-for-substitution": "progressao",
    "GET /progression/tracks": "progressao",
    "GET /progression/tracks/{track_id}/decisions": "progressao",
    "GET /reports/markdown": "relatorios",
    "POST /analytics/muscle-activation-breakdown": "volume.grupamentos",
    "POST /analytics/muscle-activation-preview": "volume.grupamentos",
    "POST /workouts-analysis/generate": "ia.analise_treinos",
}


def _rotas_2e() -> dict[str, list[tuple[str, str]]]:
    """Rotas vivas dos 9 modulos de analytics/engines do 2E que declaram permissao (sem o PUT do 2C-1)."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    com_permissao, _ = _rotas_convertidas()
    rotas: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") not in MODULOS_2E:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                if chave != ROTA_PUT_BODY_MAP_2C1 and chave in com_permissao:
                    rotas[chave] = com_permissao[chave]
    return rotas


MODULO_2F1 = "app.workout_plan_templates"
ROTA_DRAFTS_IA = "POST /workout-plan-templates/periodization-drafts"
ROTAS_2F1_ME = ("GET /workout-plan-templates/assigned-to-me", "GET /workout-plan-templates/hr-zones")


def _rotas_2f1() -> dict[str, list[tuple[str, str]]]:
    """Rotas vivas de `app.workout_plan_templates` que declaram permissao (pares)."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    com_permissao, _ = _rotas_convertidas()
    rotas: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") != MODULO_2F1:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                if chave in com_permissao:
                    rotas[chave] = com_permissao[chave]
    return rotas


MODULOS_2F2 = (
    "app.workout_sessions", "app.workout_plans", "app.exercise_briefing", "app.deload_periods",
    "app.equipment_exercise_preferences", "app.equipment_load_ceiling", "app.advanced_techniques",
    "app.training_goals", "app.muscle_group_equipment", "app.muscle_groups_mapped",
)
_WP = "/workout-plans/{plan_id}"
_WS = "/workout-sessions/{session_id}"
_EP = "/me/equipment-exercise-preferences"
# Tabela rota -> (recurso, acao) do contrato do 2F-2 (GET = ver; POST/PUT/PATCH = editar; DELETE = excluir).
CONTRATO_2F2: dict[str, tuple[str, str]] = {
    # app.main: treino.registro (11)
    "GET /activities": ("treino.registro", "ver"),
    "POST /activities": ("treino.registro", "editar"),
    "GET /activities/{activity_id}": ("treino.registro", "ver"),
    "PUT /activities/{activity_id}": ("treino.registro", "editar"),
    "DELETE /activities/{activity_id}": ("treino.registro", "excluir"),
    "GET /exercises/history": ("treino.registro", "ver"),
    "GET /exercises/last-session-sets": ("treino.registro", "ver"),
    "GET /exercises/logs": ("treino.registro", "ver"),
    "PUT /exercises/logs/{exercise_id}": ("treino.registro", "editar"),
    "DELETE /exercises/logs/{exercise_id}": ("treino.registro", "excluir"),
    "GET /sets/high-rpe": ("treino.registro", "ver"),
    # app.main: IA (4)
    "POST /activities/{activity_id}/ai-summary/generate": ("ia.analise_treinos", "editar"),
    "POST /exercises/{exercise_id}/summarize": ("ia.analise_treinos", "editar"),
    "POST /insights/generate": ("ia.insights", "editar"),
    "POST /exercises/evaluate-set": ("ia.modo_treino", "editar"),
    # app.workout_sessions: treino.modo_treino (7)
    "POST /workout-sessions/start": ("treino.modo_treino", "editar"),
    f"GET {_WS}/deload": ("treino.modo_treino", "ver"),
    f"PATCH {_WS}/meta": ("treino.modo_treino", "editar"),
    f"POST {_WS}/exercises": ("treino.modo_treino", "editar"),
    f"POST {_WS}/exercises/{{exercise_id}}/sets": ("treino.modo_treino", "editar"),
    f"PATCH {_WS}/exercises/{{exercise_id}}/sets/{{set_number}}": ("treino.modo_treino", "editar"),
    f"POST {_WS}/finish": ("treino.modo_treino", "editar"),
    # app.workout_plans (12): treino.planos, menos a geracao por IA
    "GET /workout-plans": ("treino.planos", "ver"),
    "POST /workout-plans": ("treino.planos", "editar"),
    "POST /workout-plans/generate": ("ia.geracao_plano", "editar"),
    f"GET {_WP}": ("treino.planos", "ver"),
    f"PUT {_WP}": ("treino.planos", "editar"),
    f"DELETE {_WP}": ("treino.planos", "excluir"),
    f"POST {_WP}/refresh-prescription": ("treino.planos", "editar"),
    f"GET {_WP}/exercise-swap-options": ("treino.planos", "ver"),
    f"GET {_WP}/exercises/{{exercise_id}}/equipment-options": ("treino.planos", "ver"),
    f"POST {_WP}/exercises/{{exercise_id}}/substitute-equipment": ("treino.planos", "editar"),
    f"GET {_WP}/cardio": ("treino.planos", "ver"),
    f"GET {_WP}/muscle-map.png": ("treino.planos", "ver"),
    # app.exercise_briefing (1) e app.deload_periods (4): treino.planos
    f"GET {_WP}/exercises/{{workout_plan_exercise_id}}/briefing": ("treino.planos", "ver"),
    "GET /deload-periods": ("treino.planos", "ver"),
    "GET /deload-periods/current": ("treino.planos", "ver"),
    "POST /deload-periods": ("treino.planos", "editar"),
    "POST /deload-periods/{period_id}/cancel": ("treino.planos", "editar"),
    # treino.preferencias (7)
    f"GET {_EP}": ("treino.preferencias", "ver"),
    f"PUT {_EP}/{{equipment_exercise_id}}": ("treino.preferencias", "editar"),
    f"DELETE {_EP}/{{equipment_exercise_id}}": ("treino.preferencias", "excluir"),
    "GET /me/training-preferences": ("treino.preferencias", "ver"),
    "GET /me/equipment-load-ceiling": ("treino.preferencias", "ver"),
    "PUT /me/equipment-load-ceiling": ("treino.preferencias", "editar"),
    "DELETE /me/equipment-load-ceiling": ("treino.preferencias", "excluir"),
    # exercicios.catalogo (5) e volume.grupamentos (4): ver
    "GET /advanced-techniques": ("exercicios.catalogo", "ver"),
    "GET /training-goals": ("exercicios.catalogo", "ver"),
    "GET /muscle-groups/{muscle_group_id}/equipment": ("exercicios.catalogo", "ver"),
    "GET /muscle-groups/{muscle_group_id}/equipment/{equipment_id}/exercises": ("exercicios.catalogo", "ver"),
    "GET /muscle-groups/{muscle_group_id}/exercises": ("exercicios.catalogo", "ver"),
    "GET /analytics/muscle-groups-mapped-health": ("volume.grupamentos", "ver"),
    "GET /analytics/muscle-groups-mapped-debug": ("volume.grupamentos", "ver"),
    "GET /analytics/muscle-groups-mapped-trend": ("volume.grupamentos", "ver"),
    "GET /analytics/muscle-groups-mapped-table": ("volume.grupamentos", "ver"),
}
# Rotas de `app.main` do lote: as 15 primeiras da tabela (as demais de main.py, como
# `POST /exercise-mappings` do 2C-1, nao sao).
ROTAS_MAIN_2F2 = set(list(CONTRATO_2F2)[:15])


def _rotas_2f2() -> dict[str, list[tuple[str, str]]]:
    """Rotas vivas do 2F-2 que declaram permissao: 10 modulos inteiros mais as 15 de `app.main` da tabela."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    com_permissao, _ = _rotas_convertidas()
    rotas: dict[str, list[tuple[str, str]]] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                if chave not in com_permissao:
                    continue
                if modulo in MODULOS_2F2 or (modulo == "app.main" and chave in ROTAS_MAIN_2F2):
                    rotas[chave] = com_permissao[chave]
    return rotas


def _convertidas_2c1() -> dict[str, list[tuple[str, str]]]:
    """Rotas com permissao fora do 2B, 2D-1, 2D-2, 2E, 2F-1 e 2F-2: as 34 do 2C-1 mais as 72 do 2C-2 (casos manuais)."""
    com_permissao, _ = _rotas_convertidas()
    d1 = _rotas_2d1()
    d2 = _rotas_2d2()
    e = _rotas_2e()
    f1 = _rotas_2f1()
    f2 = _rotas_2f2()
    return {
        r: p for r, p in com_permissao.items()
        if r not in ROTAS_2B and r not in d1 and r not in d2 and r not in e and r not in f1 and r not in f2
    }


def _permitido(rota: str, papel: str, tipo: str = "session") -> bool:
    classe = carregar_retrato()["rotas"][rota]["classe"]
    return decisao_nova(classe, _rotas_convertidas()[0][rota], permissoes_do_papel(papel, tipo), tipo)


def test_paridade_cobre_as_310_rotas_com_permissao_9_do_2b_mais_34_do_2c1_mais_72_do_2c2_mais_39_do_2d1_mais_16_do_2d2_mais_56_do_2e_mais_29_do_2f1_mais_55_do_2f2():
    com_permissao, _ = _rotas_convertidas()
    assert len(com_permissao) == 310
    assert len(_rotas_2f2()) == 55
    assert len(_rotas_2f1()) == 29
    assert len(_rotas_2e()) == 56
    assert len(_rotas_2d1()) == 39
    assert len(_rotas_2d2()) == 16
    assert ROTAS_2B <= set(com_permissao)
    assert len(_convertidas_2c1()) == 34 + 72  # 2C-1 + 2C-2 (as 72 tem caso manual)
    assert len([r for r in _convertidas_2c1() if r not in CASOS_MANUAIS]) == 34


def test_as_34_rotas_do_2c1_sao_admin_admin_ou_pt_ou_autenticado_com_decisao_automatica():
    retrato = carregar_retrato()["rotas"]
    convertidas = {r: p for r, p in _convertidas_2c1().items() if r not in CASOS_MANUAIS}
    assert len(convertidas) == 34
    for rota in convertidas:
        assert retrato[rota]["classe"] in ("admin", "admin_ou_pt", "autenticado"), rota
        assert divergencias(rota, retrato[rota]["classe"], convertidas[rota]) == [], rota


def test_get_training_types_e_negado_a_regular_e_permitido_a_pt_e_admin():
    rota = "GET /training-types"
    assert rota in _convertidas_2c1()
    assert _permitido(rota, "regular") is False
    assert _permitido(rota, "personal_trainer") is True
    assert _permitido(rota, "admin") is True
    assert _permitido(rota, "admin", "master_token") is True


@pytest.mark.parametrize(
    "rota",
    [
        "GET /next-set-load-rules/published",
        "GET /exercises/{exercise_id}/card",
        "GET /mapping-canonical/options",
        "GET /mapping-canonical/unmapped",
    ],
)
def test_leituras_de_catalogo_sao_permitidas_a_regular(rota):
    assert rota in _convertidas_2c1()
    for papel in ("regular", "personal_trainer", "admin"):
        assert _permitido(rota, papel) is True, (rota, papel)


# ---------------------------------------------------------------------------
# Lote 2C-2: academias e catalogos (72 rotas `identidade_no_corpo`, casos manuais)
# ---------------------------------------------------------------------------


def _convertidas_2c2() -> dict[str, list[tuple[str, str]]]:
    com_permissao, _ = _rotas_convertidas()
    retrato = carregar_retrato()["rotas"]
    return {r: p for r, p in com_permissao.items() if retrato[r]["classe"] == "identidade_no_corpo"}


def test_casos_manuais_sao_exatamente_as_72_rotas_identidade_no_corpo_convertidas():
    assert set(CASOS_MANUAIS) == set(_convertidas_2c2())
    assert len(CASOS_MANUAIS) == 72
    assert not set(_ROTAS_2C2_QUALQUER_AUTENTICADO) & set(_ROTAS_2C2_SO_ADMIN)
    assert len(_ROTAS_2C2_QUALQUER_AUTENTICADO) == 35 and len(_ROTAS_2C2_SO_ADMIN) == 37
    assert len(set(_ROTAS_2C2_QUALQUER_AUTENTICADO) | set(_ROTAS_2C2_SO_ADMIN)) == 72


def test_caso_so_admin_corresponde_a_require_admin_identity_no_retrato_e_nao_o_contradiz():
    retrato = carregar_retrato()["rotas"]
    for rota in _ROTAS_2C2_SO_ADMIN:
        assert "_require_admin_identity" in retrato[rota]["checagem_no_handler"], rota
    for rota in _ROTAS_2C2_QUALQUER_AUTENTICADO:
        if rota == "PUT /gyms/{gym_id}":
            continue  # caso misto documentado: so o guard de entrada e comparado
        assert "_require_admin_identity" not in retrato[rota]["checagem_no_handler"], rota
    # Toda rota do retrato que chamava `_require_admin_identity` e foi convertida e "so admin"
    # (a unica excecao e o PUT de gyms, cujos campos admin-only ficam no handler).
    esperadas = {
        r for r in _convertidas_2c2() if "_require_admin_identity" in retrato[r]["checagem_no_handler"]
    }
    assert esperadas == set(_ROTAS_2C2_SO_ADMIN) | {"PUT /gyms/{gym_id}"}


def test_put_gyms_tem_paridade_so_do_guard_de_entrada_qualquer_autenticado():
    rota = "PUT /gyms/{gym_id}"
    assert rota in _ROTAS_2C2_QUALQUER_AUTENTICADO
    for papel, tipo in _combinacoes():
        assert CASOS_MANUAIS[rota](papel, tipo) is True


def test_paridade_das_72_rotas_do_2c2_sem_divergencia_de_acesso():
    retrato = carregar_retrato()["rotas"]
    problemas: list[str] = []
    for rota, declaradas in sorted(_convertidas_2c2().items()):
        problemas += divergencias(rota, retrato[rota]["classe"], declaradas)
    assert not problemas, "Divergencia de acesso no 2C-2:" + chr(10) + chr(10).join(problemas[:30])


# ---------------------------------------------------------------------------
# Lote 2D-1: nutricao (39 rotas de classe `autenticado`, decisao automatica)
# ---------------------------------------------------------------------------


def test_o_conjunto_convertido_no_2d1_e_exatamente_o_das_39_rotas_dos_8_modulos():
    rotas = _rotas_2d1()
    assert len(rotas) == 39
    contagem = {}
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            if modulo in MODULOS_2D1:
                contagem[modulo] = contagem.get(modulo, 0) + len(route.methods - {"HEAD", "OPTIONS"})
    # Toda rota viva dos 8 modulos declara permissao (nenhuma ficou de fora do lote).
    assert sum(contagem.values()) == 39, contagem
    assert contagem == {
        "app.nutrition_plans": 12, "app.nutrition_plan_suggestion": 2, "app.nutrition_checkins": 6,
        "app.nutrition_days": 7, "app.nutrition_dashboard": 2, "app.nutrition_meals": 6,
        "app.nutrition_meal_parse": 2, "app.nutrition_meal_photo": 2,
    }
    retrato = carregar_retrato()["rotas"]
    for rota in rotas:
        assert retrato[rota]["classe"] == "autenticado", rota
        assert rota not in CASOS_MANUAIS, rota  # decisao automatica, sem caso manual


def test_as_39_rotas_do_2d1_declaram_o_recurso_do_contrato_com_a_acao_do_metodo():
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    problemas: list[str] = []
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            recurso = MODULOS_2D1.get(getattr(route.endpoint, "__module__", ""))
            if recurso is None:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                esperado = [(recurso, ACAO_POR_METODO[metodo])]
                declarado = _rotas_convertidas()[0].get(chave)
                if declarado != esperado:
                    problemas.append(f"{chave}: declara {declarado}, contrato {esperado}")
    assert not problemas, chr(10).join(problemas)


def test_paridade_das_39_rotas_do_2d1_sem_divergencia_de_acesso():
    retrato = carregar_retrato()["rotas"]
    problemas: list[str] = []
    for rota, declaradas in sorted(_rotas_2d1().items()):
        problemas += divergencias(rota, retrato[rota]["classe"], declaradas)
    assert not problemas, "Divergencia de acesso no 2D-1:" + chr(10) + chr(10).join(problemas[:30])


# ---------------------------------------------------------------------------
# Lote 2D-2: alimentos (16 rotas; 15 `autenticado` e `/promote` `admin`, decisao automatica)
#
# A paridade cobre o guard de ENTRADA (classe do retrato x permissao declarada). A troca de
# `is_admin` por `admin.catalogos` dentro dos handlers (edicao/promocao do catalogo publico)
# e provada em `test_nutrition_access.py` e no teste HTTP do lote, nao aqui.
# ---------------------------------------------------------------------------


def test_o_conjunto_convertido_no_2d2_e_exatamente_o_das_16_rotas_dos_4_modulos():
    rotas = _rotas_2d2()
    assert len(rotas) == 16
    contagem: dict[str, int] = {}
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            if modulo in MODULOS_2D2:
                contagem[modulo] = contagem.get(modulo, 0) + len(route.methods - {"HEAD", "OPTIONS"})
    # Toda rota viva dos 4 modulos declara permissao (nenhuma ficou de fora do lote).
    assert sum(contagem.values()) == 16, contagem
    assert contagem == {
        "app.nutrition_foods": 6, "app.nutrition_external_foods": 3,
        "app.nutrition_favorites": 5, "app.nutrition_label_photo": 2,
    }
    retrato = carregar_retrato()["rotas"]
    for rota in rotas:
        esperada = "admin" if rota == ROTA_PROMOTE else "autenticado"
        assert retrato[rota]["classe"] == esperada, rota
        assert rota not in CASOS_MANUAIS, rota  # decisao automatica, sem caso manual
    assert sum(1 for r in rotas if retrato[r]["classe"] == "autenticado") == 15
    assert len(CASOS_MANUAIS) == 72


def test_as_16_rotas_do_2d2_declaram_o_recurso_do_contrato_com_a_acao_do_metodo():
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    problemas: list[str] = []
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            if modulo not in MODULOS_2D2:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                chave = f"{metodo} {route.path}"
                if chave == ROTA_PROMOTE:
                    esperado = [("admin.catalogos", "editar")]
                else:
                    esperado = [(MODULOS_2D2[modulo], ACAO_POR_METODO[metodo])]
                declarado = _rotas_convertidas()[0].get(chave)
                if declarado != esperado:
                    problemas.append(f"{chave}: declara {declarado}, contrato {esperado}")
    assert not problemas, chr(10).join(problemas)


def test_paridade_das_16_rotas_do_2d2_sem_divergencia_de_acesso():
    retrato = carregar_retrato()["rotas"]
    problemas: list[str] = []
    for rota, declaradas in sorted(_rotas_2d2().items()):
        problemas += divergencias(rota, retrato[rota]["classe"], declaradas)
    assert not problemas, "Divergencia de acesso no 2D-2:" + chr(10) + chr(10).join(problemas[:30])


# ---------------------------------------------------------------------------
# Lote 2E: analytics e engines (56 rotas `autenticado` sobre os dados do proprio usuario)
#
# A paridade cobre o guard de ENTRADA (classe do retrato x permissao declarada); nao prova o
# escopo dos dados dentro dos handlers, que continuam filtrando pelo usuario da identidade.
# ---------------------------------------------------------------------------


def test_o_conjunto_convertido_no_2e_e_exatamente_o_das_56_rotas_dos_9_modulos():
    rotas = _rotas_2e()
    assert len(rotas) == 56
    assert set(rotas) == set(RECURSO_2E)
    contagem: dict[str, int] = {}
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vistas: set[tuple[str, str]] = set()
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            if modulo not in MODULOS_2E:
                continue
            for metodo in route.methods - {"HEAD", "OPTIONS"}:
                chave = f"{metodo} {route.path}"
                if chave == ROTA_PUT_BODY_MAP_2C1 or (modulo, chave) in vistas:
                    continue
                vistas.add((modulo, chave))
                contagem[modulo] = contagem.get(modulo, 0) + 1
    assert contagem == {
        "app.analytics": 24, "app.analytics_dashboard": 17, "app.dashboard_modules": 6,
        "app.dashboard_intelligence": 2, "app.engines_extra": 2, "app.progression": 2,
        "app.effort_metrics": 1, "app.reports": 1, "app.workouts_analysis": 1,
    }, contagem
    # O PUT do 2C-1 segue com `admin.catalogos` e fora do conjunto do 2E.
    com_permissao, _ = _rotas_convertidas()
    assert com_permissao[ROTA_PUT_BODY_MAP_2C1] == [("admin.catalogos", "editar")]
    assert ROTA_PUT_BODY_MAP_2C1 not in rotas
    retrato = carregar_retrato()["rotas"]
    for rota in rotas:
        assert retrato[rota]["classe"] == "autenticado", rota
        assert rota not in CASOS_MANUAIS, rota  # decisao automatica, sem caso manual
    assert len(CASOS_MANUAIS) == 72


def test_a_tabela_do_2e_tem_os_totais_por_recurso_do_contrato():
    from collections import Counter

    assert Counter(RECURSO_2E.values()) == {
        "dashboards": 20, "volume.grupamentos": 16, "progressao": 12, "cardio": 4,
        "corpo.indicadores": 2, "relatorios": 1, "ia.analise_treinos": 1,
    }
    editar = {r for r in RECURSO_2E if r.startswith("POST ")}
    assert editar == {
        "POST /analytics/muscle-activation-breakdown", "POST /analytics/muscle-activation-preview",
        "POST /workouts-analysis/generate",
    }


def test_as_56_rotas_do_2e_declaram_o_recurso_da_tabela_com_a_acao_do_metodo():
    problemas: list[str] = []
    declaradas = _rotas_2e()
    for chave, recurso in sorted(RECURSO_2E.items()):
        metodo = chave.split(" ", 1)[0]
        esperado = [(recurso, ACAO_POR_METODO[metodo])]
        if declaradas.get(chave) != esperado:
            problemas.append(f"{chave}: declara {declaradas.get(chave)}, contrato {esperado}")
    assert not problemas, chr(10).join(problemas)


def test_paridade_das_56_rotas_do_2e_sem_divergencia_de_acesso():
    retrato = carregar_retrato()["rotas"]
    problemas: list[str] = []
    for rota, declaradas in sorted(_rotas_2e().items()):
        problemas += divergencias(rota, retrato[rota]["classe"], declaradas)
    assert not problemas, "Divergencia de acesso no 2E:" + chr(10) + chr(10).join(problemas[:30])


# ---------------------------------------------------------------------------
# Lote 2F-1: modelos de treino (29 rotas de `app.workout_plan_templates`)
#
# 27 de gestao (classe `admin_ou_pt` no retrato) exigem escopo minimo `todos`; as 2 do
# me_router (classe `autenticado`) so `modelos.treino`/ver, sem escopo minimo. A paridade
# cobre o guard de ENTRADA incluindo o escopo minimo; a posse/atribuicao do modelo dentro
# dos handlers e coberta pelos testes HTTP de cada rota.
# ---------------------------------------------------------------------------

_T = "/workout-plan-templates"
_TID = _T + "/{template_id}"
_VER = _TID + "/versions/{version_id}"
_GESTAO_2F1_TODOS = {
    # ver (10)
    f"GET {_T}": ("modelos.treino", "ver"),
    f"GET {_T}/users-lookup": ("modelos.treino", "ver"),
    f"GET {_TID}": ("modelos.treino", "ver"),
    f"GET {_TID}/assignments": ("modelos.treino", "ver"),
    f"GET {_TID}/draft": ("modelos.treino", "ver"),
    f"GET {_TID}/methodology-intent": ("modelos.treino", "ver"),
    f"GET {_TID}/methodology-volume": ("modelos.treino", "ver"),
    f"GET {_TID}/methodology-volume/counting-modes": ("modelos.treino", "ver"),
    f"GET {_TID}/versions": ("modelos.treino", "ver"),
    f"GET {_VER}": ("modelos.treino", "ver"),
    # editar (13 de modelos.treino)
    f"PATCH {_TID}/status": ("modelos.treino", "editar"),
    f"POST {_T}": ("modelos.treino", "editar"),
    f"POST {_T}/cardio-prescription-check": ("modelos.treino", "editar"),
    f"POST {_T}/methodology-volume-preview": ("modelos.treino", "editar"),
    f"POST {_T}/preview-activation": ("modelos.treino", "editar"),
    f"POST {_TID}/assignments": ("modelos.treino", "editar"),
    f"POST {_TID}/duplicate": ("modelos.treino", "editar"),
    f"POST {_VER}/archive": ("modelos.treino", "editar"),
    f"POST {_VER}/revert": ("modelos.treino", "editar"),
    f"POST {_VER}/unarchive": ("modelos.treino", "editar"),
    f"PUT {_TID}": ("modelos.treino", "editar"),
    f"PUT {_TID}/draft": ("modelos.treino", "editar"),
    f"PUT {_TID}/methodology-intent": ("modelos.treino", "editar"),
    # IA de periodizacao (editar)
    ROTA_DRAFTS_IA: ("ia.modelo_periodizacao", "editar"),
    # excluir (3)
    f"DELETE {_TID}": ("modelos.treino", "excluir"),
    f"DELETE {_TID}/assignments/{{user_id}}": ("modelos.treino", "excluir"),
    f"DELETE {_TID}/draft": ("modelos.treino", "excluir"),
}
# rota -> (recurso, acao, escopo minimo) do contrato do 2F-1
CONTRATO_2F1: dict[str, tuple[str, str, str | None]] = {
    **{r: (*ra, "todos") for r, ra in _GESTAO_2F1_TODOS.items()},
    **{r: ("modelos.treino", "ver", None) for r in ROTAS_2F1_ME},
}


def test_o_conjunto_convertido_no_2f1_e_exatamente_o_das_29_rotas_do_modulo():
    rotas = _rotas_2f1()
    assert len(rotas) == 29
    assert set(rotas) == set(CONTRATO_2F1)
    assert len(_GESTAO_2F1_TODOS) == 27
    contagem = {"ver": 0, "editar": 0, "excluir": 0}
    for recurso, acao in _GESTAO_2F1_TODOS.values():
        if recurso == "modelos.treino":
            contagem[acao] += 1
    assert contagem == {"ver": 10, "editar": 13, "excluir": 3}, contagem
    # Toda rota viva do modulo declara permissao (nenhuma ficou de fora do lote).
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas: set[str] = set()
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") == MODULO_2F1:
                vivas |= {f"{m} {route.path}" for m in route.methods - {"HEAD", "OPTIONS"}}
    assert vivas == set(CONTRATO_2F1), sorted(vivas ^ set(CONTRATO_2F1))
    retrato = carregar_retrato()["rotas"]
    for rota in rotas:
        esperada = "autenticado" if rota in ROTAS_2F1_ME else "admin_ou_pt"
        assert retrato[rota]["classe"] == esperada, rota
        assert rota not in CASOS_MANUAIS, rota  # decisao automatica, sem caso manual
    assert len(CASOS_MANUAIS) == 72


def test_as_29_rotas_do_2f1_declaram_recurso_acao_e_escopo_minimo_do_contrato():
    vivas = _declaradas_com_escopo()
    problemas: list[str] = []
    for rota, (recurso, acao, minimo) in sorted(CONTRATO_2F1.items()):
        esperado = [(recurso, acao, minimo)]
        if vivas.get(rota) != esperado:
            problemas.append(f"{rota}: declara {vivas.get(rota)}, contrato {esperado}")
        if rota != ROTA_DRAFTS_IA:
            assert ACAO_POR_METODO[rota.split(" ", 1)[0]] == acao, rota
    assert not problemas, chr(10).join(problemas)


def test_helpers_publicos_leem_o_escopo_minimo_das_rotas_do_2f1():
    from app.main import app as main_app

    com_todos = sem_minimo = 0
    for route in _iter_api_routes(main_app):
        if getattr(route.endpoint, "__module__", "") != MODULO_2F1:
            continue
        calls = [d.call for d in route.dependant.dependencies]
        for call in [c for c in calls if permission_of(c) is not None]:
            if min_scope_of(call) == "todos":
                com_todos += 1
            else:
                assert min_scope_of(call) is None
                sem_minimo += 1
    assert com_todos == 27 and sem_minimo == 2


def test_paridade_das_29_rotas_do_2f1_sem_divergencia_de_acesso():
    retrato = carregar_retrato()["rotas"]
    vivas = _declaradas_com_escopo()
    problemas: list[str] = []
    for rota in sorted(_rotas_2f1()):
        problemas += divergencias(rota, retrato[rota]["classe"], vivas[rota])
    assert not problemas, "Divergencia de acesso no 2F-1:" + chr(10) + chr(10).join(problemas[:30])


def test_2f1_regular_negado_nas_27_de_gestao_e_permitido_nas_2_do_me_router_pt_e_admin_em_todas():
    vivas = _declaradas_com_escopo()
    retrato = carregar_retrato()["rotas"]
    for rota in _rotas_2f1():
        classe = retrato[rota]["classe"]
        for papel, tipo in _combinacoes():
            esperado = (papel != "regular" or rota in ROTAS_2F1_ME) and credencial_aceita(classe, tipo)
            assert decisao_nova(classe, vivas[rota], permissoes_do_papel(papel, tipo), tipo) is esperado, (
                rota, papel, tipo,
            )
    assert len([r for r in _rotas_2f1() if r not in ROTAS_2F1_ME]) == 27


def test_2f1_detector_ignorar_o_escopo_minimo_faria_a_paridade_acusar_o_regular_nas_get_de_gestao():
    vivas = _declaradas_com_escopo()
    retrato = carregar_retrato()["rotas"]
    get_gestao = sorted(r for r in _GESTAO_2F1_TODOS if r.startswith("GET "))
    assert len(get_gestao) == 10
    for rota in get_gestao:
        classe = retrato[rota]["classe"]
        # Com o escopo minimo (o real): sem divergencia.
        assert divergencias(rota, classe, vivas[rota]) == [], rota
        # Sem o escopo minimo (so pares): o regular (escopo proprios) passaria e a paridade acusa.
        pares = [(r, a) for r, a, _ in vivas[rota]]
        problemas = divergencias(rota, classe, pares)
        assert any("regular" in p and "permite" in p for p in problemas), (rota, problemas)
    assert not scope_covers("proprios", "todos")
    assert permission_scope(permissoes_do_papel("regular", "session"), "modelos.treino", "ver") == "proprios"
    assert permission_scope(permissoes_do_papel("personal_trainer", "session"), "modelos.treino", "ver") == "todos"


# ---------------------------------------------------------------------------
# Lote 2F-2: treino (55 rotas `autenticado` sem escopo minimo: registro, Modo Treino, planos,
# preferencias, catalogo, grupamentos e IA)
#
# A paridade cobre o guard de ENTRADA; o escopo dos dados segue nos handlers (usuario da identidade).
# ---------------------------------------------------------------------------


def test_o_conjunto_convertido_no_2f2_e_exatamente_o_das_55_rotas_dos_11_modulos():
    rotas = _rotas_2f2()
    assert len(CONTRATO_2F2) == 55
    assert set(rotas) == set(CONTRATO_2F2), sorted(set(rotas) ^ set(CONTRATO_2F2))
    assert len(ROTAS_MAIN_2F2) == 15
    assert {CONTRATO_2F2[r][0] for r in ROTAS_MAIN_2F2} == {
        "treino.registro", "ia.analise_treinos", "ia.insights", "ia.modo_treino",
    }
    # `POST /exercise-mappings` mora em main.py mas e do 2C-1.
    com_permissao, _ = _rotas_convertidas()
    assert com_permissao["POST /exercise-mappings"] == [("admin.catalogos", "editar")]
    assert "POST /exercise-mappings" not in rotas
    # Toda rota viva dos 10 modulos declara permissao (nenhuma ficou de fora).
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas: dict[str, int] = {}
    for app in (main_app, engines_app):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            if modulo in MODULOS_2F2:
                for m in route.methods - {"HEAD", "OPTIONS"}:
                    assert f"{m} {route.path}" in rotas, (modulo, m, route.path)
                    vivas[modulo] = vivas.get(modulo, 0) + 1
    assert vivas == {
        "app.workout_sessions": 7, "app.workout_plans": 12, "app.exercise_briefing": 1,
        "app.deload_periods": 4, "app.equipment_exercise_preferences": 4, "app.equipment_load_ceiling": 3,
        "app.advanced_techniques": 1, "app.training_goals": 1, "app.muscle_group_equipment": 3,
        "app.muscle_groups_mapped": 4,
    }, vivas
    retrato = carregar_retrato()["rotas"]
    for rota in rotas:
        assert retrato[rota]["classe"] == "autenticado", rota
        assert rota not in CASOS_MANUAIS, rota  # decisao automatica, sem caso manual
    assert len(CASOS_MANUAIS) == 72


def test_a_tabela_do_2f2_tem_os_totais_por_recurso_e_acao_do_contrato():
    from collections import Counter

    assert Counter(r for r, _ in CONTRATO_2F2.values()) == {
        "treino.planos": 16, "treino.registro": 11, "treino.modo_treino": 7, "treino.preferencias": 7,
        "exercicios.catalogo": 5, "volume.grupamentos": 4, "ia.analise_treinos": 2, "ia.insights": 1,
        "ia.modo_treino": 1, "ia.geracao_plano": 1,
    }
    por = Counter(CONTRATO_2F2.values())
    assert {k: v for k, v in por.items() if k[0].startswith("treino.")} == {
        ("treino.planos", "ver"): 9, ("treino.planos", "editar"): 6, ("treino.planos", "excluir"): 1,
        ("treino.registro", "ver"): 6, ("treino.registro", "editar"): 3, ("treino.registro", "excluir"): 2,
        ("treino.modo_treino", "ver"): 1, ("treino.modo_treino", "editar"): 6,
        ("treino.preferencias", "ver"): 3, ("treino.preferencias", "editar"): 2,
        ("treino.preferencias", "excluir"): 2,
    }
    assert sum(1 for r, _ in CONTRATO_2F2.values() if r.startswith("ia.")) == 5
    assert all(a == "ver" for r, a in CONTRATO_2F2.values() if r in {"exercicios.catalogo", "volume.grupamentos"})


def test_as_55_rotas_do_2f2_declaram_recurso_e_acao_da_tabela_sem_escopo_minimo():
    vivas = _declaradas_com_escopo()
    problemas: list[str] = []
    for rota, (recurso, acao) in sorted(CONTRATO_2F2.items()):
        esperado = [(recurso, acao, None)]
        if vivas.get(rota) != esperado:
            problemas.append(f"{rota}: declara {vivas.get(rota)}, contrato {esperado}")
        assert ACAO_POR_METODO[rota.split(" ", 1)[0]] == acao, rota
    assert not problemas, chr(10).join(problemas)


def test_paridade_das_55_rotas_do_2f2_sem_divergencia_de_acesso():
    retrato = carregar_retrato()["rotas"]
    problemas: list[str] = []
    for rota, declaradas in sorted(_rotas_2f2().items()):
        problemas += divergencias(rota, retrato[rota]["classe"], declaradas)
    assert not problemas, "Divergencia de acesso no 2F-2:" + chr(10) + chr(10).join(problemas[:30])
