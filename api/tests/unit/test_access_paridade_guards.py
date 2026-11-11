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
from tests.unit.test_access_guarda_cobertura_rotas import _all_project_routes

ALLOWLIST_PATH = Path(__file__).with_name("access_cobertura_allowlist.json")

# rota -> funcao(papel, tipo_identidade) -> bool com a decisao do guard ANTIGO provada a
# mao, para rotas convertidas cuja classe do retrato e `identidade_no_corpo`/`outra`.
CASOS_MANUAIS: dict[str, Callable[[str, str], bool]] = {}

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


def _convertidas_2c1() -> dict[str, list[tuple[str, str]]]:
    com_permissao, _ = _rotas_convertidas()
    return {r: p for r, p in com_permissao.items() if r not in ROTAS_2B}


def _permitido(rota: str, papel: str, tipo: str = "session") -> bool:
    classe = carregar_retrato()["rotas"][rota]["classe"]
    return decisao_nova(classe, _rotas_convertidas()[0][rota], permissoes_do_papel(papel, tipo), tipo)


def test_paridade_cobre_as_43_rotas_com_permissao_9_do_2b_mais_34_do_2c1():
    com_permissao, _ = _rotas_convertidas()
    assert len(com_permissao) == 43
    assert ROTAS_2B <= set(com_permissao)
    assert len(_convertidas_2c1()) == 34


def test_as_34_rotas_do_2c1_sao_admin_admin_ou_pt_ou_autenticado_com_decisao_automatica():
    retrato = carregar_retrato()["rotas"]
    for rota in _convertidas_2c1():
        assert retrato[rota]["classe"] in ("admin", "admin_ou_pt", "autenticado"), rota
        assert rota not in CASOS_MANUAIS, rota
        assert divergencias(rota, retrato[rota]["classe"], _convertidas_2c1()[rota]) == [], rota


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
