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
    has_permission,
    resolve_permissions,
)
from app.access.service import merge_grants
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


def decisao_nova(classe: str, declaradas: list[tuple[str, str]], permissoes: EffectivePermissions, tipo: str) -> bool:
    return credencial_aceita(classe, tipo) and all(has_permission(permissoes, r, a) for r, a in declaradas)


def _decisao_antiga(rota: str, classe: str, papel: str, tipo: str) -> bool:
    if rota in CASOS_MANUAIS:
        return CASOS_MANUAIS[rota](papel, tipo)
    return guard_antigo_permitia(classe, papel, tipo)


def divergencias(
    rota: str,
    classe: str,
    declaradas: list[tuple[str, str]],
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
    com_permissao, _ = _rotas_convertidas()
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
ACAO_POR_METODO = {"GET": "ver", "POST": "editar", "PUT": "editar", "DELETE": "excluir"}


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


def _convertidas_2c1() -> dict[str, list[tuple[str, str]]]:
    """Rotas com permissao fora do 2B e do 2D-1: as 34 do 2C-1 mais as 72 do 2C-2 (casos manuais)."""
    com_permissao, _ = _rotas_convertidas()
    d1 = _rotas_2d1()
    return {r: p for r, p in com_permissao.items() if r not in ROTAS_2B and r not in d1}


def _permitido(rota: str, papel: str, tipo: str = "session") -> bool:
    classe = carregar_retrato()["rotas"][rota]["classe"]
    return decisao_nova(classe, _rotas_convertidas()[0][rota], permissoes_do_papel(papel, tipo), tipo)


def test_paridade_cobre_as_154_rotas_com_permissao_9_do_2b_mais_34_do_2c1_mais_72_do_2c2_mais_39_do_2d1():
    com_permissao, _ = _rotas_convertidas()
    assert len(com_permissao) == 154
    assert len(_rotas_2d1()) == 39
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
