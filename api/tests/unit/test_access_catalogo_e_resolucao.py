"""RBAC (ADR 0011, lote 1): catalogo, resolucao de permissoes e paridade com a migracao 111.

Contrato testado (nao o que a implementacao por acaso faz): negado por padrao,
sem fallback para `users.role`; uniao dos grupos com o escopo mais abrangente
vencendo; admin e token mestre com acesso total; `authorize_target`; recurso ou
acao fora do catalogo levanta erro; concessao desconhecida no banco e ignorada;
falha de consulta vira 503; matriz padrao igual a da migracao e sem conceder
mais do que os guards de papel de hoje (`require_admin`, `require_admin_or_pt`,
`resolve_target_user`).

Rodar: cd api && python -m pytest tests/unit/test_access_catalogo_e_resolucao.py
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import migrate as m
from app.access import catalog, service
from app.access.catalog import DEFAULT_GRANTS_BY_GROUP, RESOURCE_KEYS
from app.nutrition_access import resolve_target_user
from app.security import Identity
from tests.support import guards_antigos

APP_DIR = Path(__file__).resolve().parents[2] / "app"
MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "111_access_groups_rbac.py"
MIGRATION_113 = m.DEFAULT_MIGRATIONS_DIR / "113_access_grants_conta_dispositivos.py"
MIGRATION_114 = m.DEFAULT_MIGRATIONS_DIR / "114_access_grants_modelos_tipos_treino.py"
MIGRATION_115 = m.DEFAULT_MIGRATIONS_DIR / "115_access_grants_pt_nutricao_alunos.py"

# Grupos que a migracao 111 semeia com is_system=1 (free e comum e vem com 0).
GRUPOS_DE_SISTEMA = frozenset({"admin", "personal_trainer", "usuario"})

ROLE_TO_GROUP = {"admin": "admin", "personal_trainer": "personal_trainer", "regular": "usuario"}


@functools.lru_cache(maxsize=1)
def _migration_globals() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


@functools.lru_cache(maxsize=1)
def _migration_113_globals() -> dict:
    return m.load_python_migration(MIGRATION_113).__globals__


@functools.lru_cache(maxsize=1)
def _migration_114_globals() -> dict:
    return m.load_python_migration(MIGRATION_114).__globals__


@functools.lru_cache(maxsize=1)
def _migration_115_globals() -> dict:
    return m.load_python_migration(MIGRATION_115).__globals__


def _matriz_acumulada_das_migracoes() -> dict[str, list[tuple[str, str, str]]]:
    """Retrato congelado acumulado: matriz da 111 + concessoes da 113/114 + escopos da 115."""
    acumulado = {g: list(linhas) for g, linhas in _migration_globals()["GRANTS_BY_GROUP"].items()}
    for globais in (_migration_113_globals(), _migration_114_globals()):
        for grupo, linhas in globais["GRANTS_BY_GROUP"].items():
            acumulado[grupo].extend(linhas)
    # 115: UPDATE de escopo sobre linhas existentes (nao acrescenta pares novos)
    for grupo, linhas in _migration_115_globals()["GRANTS_BY_GROUP"].items():
        novos = {(r, a): sc for r, a, sc in linhas}
        acumulado[grupo] = [(r, a, novos.get((r, a), sc)) for r, a, sc in acumulado[grupo]]
    return acumulado


# ---------------------------------------------------------------------------
# Banco falso: grupos+concessoes por usuario, vinculos por (treinador, aluno)
# ---------------------------------------------------------------------------


class FakeAccessDB:
    def __init__(self, groups=None, links=None, error=None):
        # {user_id: [(group_key, resource|None, action|None, scope|None[, is_system]), ...]}
        # sem o 5o elemento, is_system segue a migracao 111 (admin/PT/usuario = 1).
        self.groups = groups or {}
        self.links = links or {}  # {(trainer, student): 'ativo'|'encerrado'}
        self.error = error
        self.calls: list[tuple[str, tuple]] = []

    def __call__(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if self.error is not None:
            raise self.error
        if "FROM trainer_students" in sql:
            assert "status = 'ativo'" in sql, "o vinculo so vale ATIVO"
            trainer, student = params
            return [{"ok": 1}] if self.links.get((trainer, student)) == "ativo" else []
        if "FROM user_access_groups" in sql:
            return [
                _linha(*item) for item in self.groups.get(params[0], [])
            ]
        raise AssertionError(f"consulta inesperada: {sql}")


@pytest.fixture
def db(monkeypatch):
    def _install(**kwargs) -> FakeAccessDB:
        fake = FakeAccessDB(**kwargs)
        monkeypatch.setattr(service._db, "fetch_rows", fake)
        return fake

    return _install


def _ident(user_id=10, role="regular", source="session") -> Identity:
    return Identity(user_id=user_id, role=role, source=source)


_SEM = object()


def _linha(g, r, a, s, is_system=_SEM) -> dict:
    if is_system is _SEM:
        is_system = 1 if g in GRUPOS_DE_SISTEMA else 0
    return {"group_key": g, "resource": r, "action": a, "scope": s, "is_system": is_system}


def _grants_rows(group: str):
    return [(group, r, a, s) for r, a, s in DEFAULT_GRANTS_BY_GROUP[group]]


def _perms(rows) -> service.EffectivePermissions:
    return service.merge_grants([_linha(*item) for item in rows])


def _status(exc: pytest.ExceptionInfo) -> int:
    assert isinstance(exc.value, HTTPException)
    return exc.value.status_code


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------


def test_escopos_ordenados_proprios_alunos_todos():
    assert catalog.SCOPES == ("proprios", "alunos", "todos")
    assert catalog.ACTIONS == ("ver", "editar", "excluir")
    for i, menor in enumerate(catalog.SCOPES):
        for j, maior in enumerate(catalog.SCOPES):
            assert catalog.scope_covers(maior, menor) is (j >= i)


def test_catalogo_chaves_unicas_dominios_validos_e_rotulos():
    assert len(set(RESOURCE_KEYS)) == len(RESOURCE_KEYS)
    for r in catalog.RESOURCES:
        assert r.domain in catalog.DOMAINS and r.label.strip()
    payload = catalog.catalog_payload()
    assert [p["key"] for p in payload] == list(RESOURCE_KEYS)


def test_matriz_padrao_so_usa_recursos_acoes_e_escopos_do_catalogo():
    for group, rows in DEFAULT_GRANTS_BY_GROUP.items():
        pares = [(r, a) for r, a, _ in rows]
        assert len(pares) == len(set(pares)), f"{group}: um escopo por (recurso, acao)"
        for r, a, s in rows:
            assert r in RESOURCE_KEYS and a in catalog.ACTIONS and s in catalog.SCOPES


# ---------------------------------------------------------------------------
# Negado por padrao, uniao e escopo mais abrangente
# ---------------------------------------------------------------------------


def test_usuario_sem_grupo_nao_tem_permissao_e_nao_cai_para_users_role(db):
    fake = db(groups={})
    for role in ("admin", "personal_trainer", "regular"):
        perms = service.resolve_permissions(_ident(role=role))
        assert perms.grants == {} and perms.group_keys == () and not perms.full_access
        assert service.serialize_permissions(perms) == {"permissions": {}, "groups": []}
        with pytest.raises(HTTPException) as exc:
            service.require(perms, "dashboards", "ver")
        assert _status(exc) == 403
    assert len(fake.calls) == 3


def test_negado_por_padrao_mesmo_com_grupo_vazio_como_free():
    perms = _perms(_grants_rows("free") or [("free", None, None, None)])
    assert perms.group_keys == ("free",) and perms.grants == {}
    assert all(not service.has_permission(perms, r, a) for r in RESOURCE_KEYS for a in catalog.ACTIONS)


def test_uniao_de_grupos_soma_recursos_diferentes():
    perms = _perms(
        [
            ("a", "cardio", "ver", "proprios"),
            ("b", "relatorios", "editar", "proprios"),
        ]
    )
    assert service.permission_scope(perms, "cardio", "ver") == "proprios"
    assert service.permission_scope(perms, "relatorios", "editar") == "proprios"
    assert service.permission_scope(perms, "cardio", "editar") is None
    assert perms.group_keys == ("a", "b")


@pytest.mark.parametrize(
    "escopos,esperado",
    [
        (("proprios", "alunos"), "alunos"),
        (("alunos", "proprios"), "alunos"),
        (("proprios", "todos"), "todos"),
        (("todos", "alunos", "proprios"), "todos"),
        (("alunos", "todos"), "todos"),
        (("proprios", "proprios"), "proprios"),
    ],
)
def test_mesmo_recurso_e_acao_vence_o_escopo_mais_abrangente(escopos, esperado):
    rows = [(f"g{i}", "nutricao.diario", "ver", s) for i, s in enumerate(escopos)]
    assert service.permission_scope(_perms(rows), "nutricao.diario", "ver") == esperado


def test_grupo_sem_concessao_aparece_em_groups_sem_dar_permissao():
    perms = service.merge_grants([_linha("free", None, None, None)])
    assert perms.group_keys == ("free",) and perms.grants == {}


def test_serialize_so_devolve_o_concedido():
    perms = _perms([("usuario", "cardio", "ver", "proprios")])
    assert service.serialize_permissions(perms) == {
        "permissions": {"cardio": {"ver": "proprios"}},
        "groups": ["usuario"],
    }


# ---------------------------------------------------------------------------
# Admin, token mestre e free
# ---------------------------------------------------------------------------


def test_grupo_admin_tem_acesso_total_a_todos_os_recursos_do_catalogo(db):
    db(groups={1: [("admin", None, None, None)]})
    perms = service.resolve_permissions(_ident(user_id=1, role="admin"))
    assert perms.full_access and perms.group_keys == ("admin",)
    for r in RESOURCE_KEYS:
        for a in catalog.ACTIONS:
            assert service.permission_scope(perms, r, a) == "todos"


def test_admin_junto_de_outros_grupos_continua_com_acesso_total():
    perms = _perms([("usuario", "cardio", "ver", "proprios"), ("admin", None, None, None)])
    assert perms.full_access
    assert service.permission_scope(perms, "admin.grupos", "excluir") == "todos"


@pytest.mark.parametrize("valor", [1, True, "1", "true", "TRUE", " true ", b"1"])
def test_is_system_verdadeiro_em_todas_as_formas_da_data_api_e_do_pymysql(valor):
    assert service.normalize_is_system(valor) is True
    perms = service.merge_grants([_linha("admin", None, None, None, valor)])
    assert perms.full_access and perms.group_keys == ("admin",)


@pytest.mark.parametrize("valor", [_SEM, None, 0, False, "0", "false", "", "sim", "2", 2, -1, 1.0, b"0", [], object()])
def test_is_system_ausente_nulo_zero_ou_desconhecido_nao_da_acesso_total(valor):
    if valor is _SEM:
        row = {"group_key": "admin", "resource": None, "action": None, "scope": None}  # coluna ausente
    else:
        row = _linha("admin", None, None, None, valor)
    assert service.normalize_is_system(row.get("is_system")) is False
    perms = service.merge_grants([row])
    assert not perms.full_access and perms.grants == {} and perms.group_keys == ("admin",)
    assert all(not service.has_permission(perms, r, a) for r in RESOURCE_KEYS for a in catalog.ACTIONS)


def test_grupo_admin_nao_sistema_vale_so_pelas_concessoes_gravadas():
    perms = _perms(
        [
            ("admin", "cardio", "ver", "proprios", 0),
            ("admin", "relatorios", "editar", "todos", "0"),
        ]
    )
    assert not perms.full_access
    assert perms.grants == {"cardio": {"ver": "proprios"}, "relatorios": {"editar": "todos"}}
    assert service.permission_scope(perms, "admin.grupos", "excluir") is None
    assert service.permission_scope(perms, "cardio", "editar") is None


@pytest.mark.parametrize("valor", [0, None, "0", "false"])
def test_resolve_permissions_admin_nao_sistema_nao_tem_acesso_total(db, valor):
    db(groups={5: [("admin", None, None, None, valor)]})
    perms = service.resolve_permissions(_ident(user_id=5, role="admin"))
    assert not perms.full_access and perms.grants == {}
    assert service.serialize_permissions(perms) == {"permissions": {}, "groups": ["admin"]}
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(user_id=5, role="admin"), "admin.grupos", "ver")
    assert _status(exc) == 403


@pytest.mark.parametrize("valor", [1, True, "1"])
def test_resolve_permissions_admin_de_sistema_tem_acesso_total(db, valor):
    db(groups={5: [("admin", None, None, None, valor)]})
    perms = service.resolve_permissions(_ident(user_id=5, role="admin"))
    assert perms.full_access
    assert service.permission_scope(perms, "admin.grupos", "excluir") == "todos"


def test_admin_de_sistema_junto_de_outros_grupos_inclusive_nao_sistema_tem_acesso_total():
    perms = _perms(
        [
            ("outro", "cardio", "ver", "proprios", 0),
            ("admin", None, None, None, 1),
            ("free", None, None, None),
        ]
    )
    assert perms.full_access and perms.group_keys == ("outro", "admin", "free")
    assert service.permission_scope(perms, "admin.usuarios", "excluir") == "todos"


def test_dois_grupos_com_chave_admin_um_de_sistema_e_outro_nao_prevalece_o_de_sistema():
    perms = _perms([("admin", "cardio", "ver", "proprios", 0), ("admin", None, None, None, 1)])
    assert perms.full_access


def test_chave_de_acesso_total_so_vale_com_is_system_outra_chave_de_sistema_nao_basta():
    perms = _perms([("usuario", "cardio", "ver", "proprios", 1), ("custom", None, None, None, 1)])
    assert not perms.full_access and perms.grants == {"cardio": {"ver": "proprios"}}


def test_check_catalog_e_publica_e_valida_recurso_e_acao():
    service.check_catalog("cardio", "ver")
    with pytest.raises(ValueError):
        service.check_catalog("nao.existe", "ver")
    with pytest.raises(ValueError):
        service.check_catalog("cardio", "apagar")
    from app.access import check_catalog as reexportada

    assert reexportada is service.check_catalog


def test_consulta_de_grupos_devolve_is_system():
    assert "g.is_system" in service._GRANTS_SQL


def test_token_mestre_tem_acesso_total_sem_consultar_o_banco(db):
    fake = db(error=AssertionError("token mestre nao pode consultar o banco"))
    perms = service.resolve_permissions(_ident(user_id=1, role="admin", source="master_token"))
    assert perms.full_access
    assert all(
        service.permission_scope(perms, r, a) == "todos" for r in RESOURCE_KEYS for a in catalog.ACTIONS
    )
    assert fake.calls == []


def test_token_mestre_autoriza_terceiro_sem_banco(db):
    fake = db(error=AssertionError("sem banco"))
    ident = _ident(user_id=1, role="admin", source="master_token")
    assert service.authorize_target(ident, "nutricao.plano", "editar", 99) == 99
    assert fake.calls == []


def test_grupo_free_nao_tem_concessoes_nem_membros_na_matriz_padrao():
    assert DEFAULT_GRANTS_BY_GROUP["free"] == ()
    assert DEFAULT_GRANTS_BY_GROUP["admin"] == ()
    assert "free" not in catalog.SYSTEM_GROUP_KEYS and "admin" not in catalog.SYSTEM_GROUP_KEYS


@pytest.mark.parametrize("grupo", ["usuario", "personal_trainer"])
def test_matriz_padrao_nao_concede_admin_catalogos_a_usuario_nem_a_personal_em_nenhuma_acao(grupo):
    # Mantem a troca de is_admin por admin.catalogos (promocao e edicao do catalogo publico
    # de alimentos, lote 2D-2) equivalente a is_admin: so o grupo admin (acesso total) passa.
    assert "admin.catalogos" in RESOURCE_KEYS
    concedidas = [(r, a) for r, a, _s in DEFAULT_GRANTS_BY_GROUP[grupo] if r == "admin.catalogos"]
    assert concedidas == [], concedidas


def test_dispositivo_resolve_pelo_user_id_do_dono(db):
    fake = db(groups={7: [("usuario", "cardio", "ver", "proprios")]})
    perms = service.resolve_permissions(_ident(user_id=7, source="device"))
    assert service.permission_scope(perms, "cardio", "ver") == "proprios"
    assert fake.calls[0][1] == (7,)


# ---------------------------------------------------------------------------
# authorize_target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("escopo", ["proprios", "alunos", "todos"])
def test_authorize_target_alvo_ausente_ou_proprio_passa_com_qualquer_escopo(db, escopo):
    fake = db(groups={10: [("g", "cardio", "ver", escopo)]})
    assert service.authorize_target(_ident(), "cardio", "ver") == 10
    assert service.authorize_target(_ident(), "cardio", "ver", 10) == 10
    assert all("trainer_students" not in sql for sql, _ in fake.calls)


def test_authorize_target_terceiro_exige_todos_ou_alunos_com_vinculo_ativo(db):
    db(
        groups={
            10: [("g", "cardio", "ver", "proprios")],
            11: [("g", "cardio", "ver", "alunos")],
            12: [("g", "cardio", "ver", "todos")],
        },
        links={(11, 50): "ativo"},
    )
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(10), "cardio", "ver", 50)
    assert _status(exc) == 403
    assert service.authorize_target(_ident(11), "cardio", "ver", 50) == 50
    assert service.authorize_target(_ident(12), "cardio", "ver", 50) == 50


@pytest.mark.parametrize("vinculo", [{}, {(11, 50): "encerrado"}, {(50, 11): "ativo"}, {(11, 51): "ativo"}])
def test_authorize_target_alunos_sem_vinculo_ativo_nega_403(db, vinculo):
    db(groups={11: [("g", "cardio", "ver", "alunos")]}, links=vinculo)
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(11), "cardio", "ver", 50)
    assert _status(exc) == 403


def test_authorize_target_sem_permissao_nega_403_mesmo_para_o_proprio(db):
    db(groups={10: [("g", "cardio", "ver", "todos")]})
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(), "cardio", "editar")
    assert _status(exc) == 403
    assert exc.value.detail["error"] == "forbidden"
    db(groups={})
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(), "cardio", "ver", 10)
    assert _status(exc) == 403


def test_authorize_target_usa_permissoes_ja_resolvidas_sem_nova_consulta(db):
    fake = db(error=AssertionError("nao deve consultar"))
    perms = _perms([("g", "cardio", "ver", "todos")])
    assert service.authorize_target(_ident(), "cardio", "ver", 99, permissoes=perms) == 99
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Fora do catalogo = erro, nao negacao silenciosa
# ---------------------------------------------------------------------------


def test_recurso_ou_acao_fora_do_catalogo_levanta_erro(db):
    db(groups={10: [("g", "cardio", "ver", "todos")]})
    perms = _perms([("g", "cardio", "ver", "todos")])
    with pytest.raises(ValueError):
        service.has_permission(perms, "recurso.inexistente", "ver")
    with pytest.raises(ValueError):
        service.has_permission(perms, "cardio", "apagar")
    with pytest.raises(ValueError):
        service.permission_scope(perms, "cardio", "VER")
    with pytest.raises(ValueError):
        service.require(perms, "nao.existe", "ver")
    with pytest.raises(ValueError):
        service.authorize_target(_ident(), "nao.existe", "ver")
    with pytest.raises(ValueError):
        service.authorize_target(_ident(), "cardio", "apagar")


def test_require_permission_com_recurso_ou_acao_invalidos_falha_ao_declarar_a_rota():
    from app.access import require_permission

    with pytest.raises(ValueError):
        require_permission("nao.existe", "ver")
    with pytest.raises(ValueError):
        require_permission("cardio", "apagar")


@pytest.mark.parametrize(
    "linha",
    [
        ("g", "recurso.removido", "ver", "todos"),
        ("g", "cardio", "apagar", "todos"),
        ("g", "cardio", "ver", "universo"),
    ],
)
def test_concessao_desconhecida_no_banco_e_ignorada(linha):
    perms = _perms([linha, ("g", "relatorios", "ver", "proprios")])
    assert perms.grants == {"relatorios": {"ver": "proprios"}}
    assert perms.group_keys == ("g",)


def test_concessao_desconhecida_nao_rebaixa_nem_eleva_a_valida():
    perms = _perms([("a", "cardio", "ver", "proprios"), ("b", "cardio", "ver", "universo")])
    assert service.permission_scope(perms, "cardio", "ver") == "proprios"


# ---------------------------------------------------------------------------
# Falha de consulta = 503
# ---------------------------------------------------------------------------


def test_falha_na_consulta_de_grupos_vira_503_e_nao_permissoes_vazias(db):
    db(error=RuntimeError("Aurora indisponivel"))
    with pytest.raises(HTTPException) as exc:
        service.resolve_permissions(_ident())
    assert _status(exc) == 503
    assert "Retry-After" in (exc.value.headers or {})


def test_falha_na_consulta_do_vinculo_vira_503(db):
    fake = db(groups={11: [("g", "cardio", "ver", "alunos")]})
    perms = service.resolve_permissions(_ident(11))
    fake.error = TimeoutError("timeout")
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(11), "cardio", "ver", 50, permissoes=perms)
    assert _status(exc) == 503


def test_falha_de_banco_nunca_e_401_nem_403(db):
    db(error=Exception("qualquer"))
    for chamada in (
        lambda: service.resolve_permissions(_ident()),
        lambda: service.authorize_target(_ident(), "cardio", "ver"),
        lambda: service.has_active_link(1, 2),
    ):
        with pytest.raises(HTTPException) as exc:
            chamada()
        assert _status(exc) == 503


# ---------------------------------------------------------------------------
# Paridade com a migracao 111
# ---------------------------------------------------------------------------


def test_paridade_matriz_do_catalogo_e_das_migracoes_111_mais_113_114_115():
    migracao = _matriz_acumulada_das_migracoes()
    assert set(migracao) == set(DEFAULT_GRANTS_BY_GROUP)
    for grupo, linhas in DEFAULT_GRANTS_BY_GROUP.items():
        assert sorted(linhas) == sorted(migracao[grupo]), f"matriz divergente em {grupo}"
        pares = [(r, a) for r, a, _ in migracao[grupo]]
        assert len(pares) == len(set(pares)), f"{grupo}: 113/114 repete concessao anterior"


def test_tipos_treino_so_o_personal_trainer_recebe_com_escopo_todos():
    assert "modelos.tipos_treino" in RESOURCE_KEYS
    recurso = catalog.RESOURCES_BY_KEY["modelos.tipos_treino"]
    assert recurso.label == "Tipos de treino" and recurso.domain == catalog.DOMAIN_MODELOS
    esperado = {("modelos.tipos_treino", a, "todos") for a in catalog.ACTIONS}
    assert {l for l in DEFAULT_GRANTS_BY_GROUP["personal_trainer"] if l[0] == "modelos.tipos_treino"} == esperado
    for grupo in ("usuario", "free", "admin"):
        assert not [l for l in DEFAULT_GRANTS_BY_GROUP[grupo] if l[0] == "modelos.tipos_treino"]
    assert len(DEFAULT_GRANTS_BY_GROUP["personal_trainer"]) == 85


def test_conta_dispositivos_usuario_e_pt_proprios_free_e_admin_vazios():
    assert "conta.dispositivos" in RESOURCE_KEYS
    recurso = catalog.RESOURCES_BY_KEY["conta.dispositivos"]
    assert recurso.label == "Relógio e dispositivos"
    assert recurso.domain == catalog.DOMAIN_CONTA and catalog.DOMAIN_CONTA in catalog.DOMAINS
    esperado = {("conta.dispositivos", a, "proprios") for a in catalog.ACTIONS}
    for grupo in ("usuario", "personal_trainer"):
        assert {l for l in DEFAULT_GRANTS_BY_GROUP[grupo] if l[0] == "conta.dispositivos"} == esperado
    assert DEFAULT_GRANTS_BY_GROUP["free"] == () and DEFAULT_GRANTS_BY_GROUP["admin"] == ()


def test_paridade_recursos_da_matriz_e_da_migracao_com_o_catalogo():
    g = _migration_globals()
    usados = {r for linhas in _matriz_acumulada_das_migracoes().values() for r, _, _ in linhas}
    assert usados <= set(RESOURCE_KEYS)
    so_admin = set(g["RESOURCES_SO_ADMIN"])
    assert so_admin <= set(RESOURCE_KEYS)
    assert not usados & so_admin, "recursos so-admin nao podem ter concessao de grupo"
    assert (set(RESOURCE_KEYS) - usados) == so_admin | set(), "recurso do catalogo sem dono na matriz"


def test_paridade_vocabulario_acoes_escopos_e_grupos():
    g = _migration_globals()
    assert tuple(g["ACTIONS"]) == catalog.ACTIONS
    assert tuple(g["SCOPES"]) == catalog.SCOPES
    g113 = _migration_113_globals()
    assert tuple(g113["ACTIONS"]) == catalog.ACTIONS
    assert set(g113["GRANTS_BY_GROUP"]) <= {k for k, *_ in g["SEED_GROUPS"]}
    g114 = _migration_114_globals()
    assert tuple(g114["ACTIONS"]) == catalog.ACTIONS
    assert set(g114["GRANTS_BY_GROUP"]) <= {k for k, *_ in g["SEED_GROUPS"]}
    g115 = _migration_115_globals()
    assert tuple(g115["ACTIONS"]) == catalog.ACTIONS
    assert set(g115["GRANTS_BY_GROUP"]) <= {k for k, *_ in g["SEED_GROUPS"]}
    assert set(g115["RESOURCES"]) == set(NUTRICAO_TERCEIROS)
    assert {k for k, *_ in g["SEED_GROUPS"]} == set(DEFAULT_GRANTS_BY_GROUP)


# ---------------------------------------------------------------------------
# Nao regressao por papel (derivada do codigo atual)
# ---------------------------------------------------------------------------

ADMIN_ONLY_RESOURCES = (
    "admin.usuarios",
    "admin.grupos",
    "admin.regras_carga",
    "admin.catalogos",
    "admin.seguranca_treino",
    "ia.cards_exercicio",
)
MODELOS_PT = (
    "modelos.treino",
    "modelos.metas_ativacao",
    "modelos.tipos_treino",
    "ia.modelo_periodizacao",
)
NUTRICAO_TERCEIROS = ("nutricao.diario", "nutricao.plano", "nutricao.checkin", "ia.nutricao")


def _perms_do_papel(role: str) -> service.EffectivePermissions:
    grupo = ROLE_TO_GROUP[role]
    return _perms(_grants_rows(grupo) or [(grupo, None, None, None)])


def _guard_passa(guard, role: str) -> bool:
    try:
        guard(_ident(role=role))
    except HTTPException:
        return False
    return True


def test_papel_do_codigo_atual_mapeia_para_o_grupo_da_migracao():
    assert dict(_migration_globals()["ROLE_TO_GROUP"]) == ROLE_TO_GROUP


@pytest.mark.parametrize("role", ["admin", "personal_trainer", "regular"])
def test_recursos_admin_e_cards_so_para_quem_passa_em_require_admin(role):
    guard_libera = _guard_passa(guards_antigos.require_admin, role)
    perms = _perms_do_papel(role)
    for recurso in ADMIN_ONLY_RESOURCES:
        for acao in catalog.ACTIONS:
            assert service.has_permission(perms, recurso, acao) is guard_libera, (role, recurso, acao)
    assert guard_libera is (role == "admin")


@pytest.mark.parametrize("role", ["admin", "personal_trainer", "regular"])
def test_modelos_metas_e_periodizacao_seguem_require_admin_or_pt(role):
    guard_libera = _guard_passa(guards_antigos.require_admin_or_pt, role)
    perms = _perms_do_papel(role)
    for recurso in MODELOS_PT:
        for acao in catalog.ACTIONS:
            concedido = service.permission_scope(perms, recurso, acao)
            if guard_libera:
                assert concedido == "todos", (role, recurso, acao)
            elif recurso == "modelos.treino" and acao == "ver":
                # me_router: o aluno ve os Modelos atribuidos a ele (so os proprios)
                assert concedido == "proprios"
            else:
                assert concedido is None, (role, recurso, acao)


def test_os_guards_de_papel_continuam_nos_mesmos_modulos():
    """Lote 2F-3: nenhum modulo de `app` usa mais os guards de papel; a matriz e a fonte.

    A regra antiga vive so nas copias congeladas de `tests/support/guards_antigos.py`."""
    usam = sorted(
        p.name
        for p in APP_DIR.rglob("*.py")
        if re.search(
            r"Depends\(require_admin(_or_pt)?\)|require_admin(_or_pt)?\(identity|is_admin_or_pt\(identity"
            r"|import[^\n]*\b(require_admin|require_admin_or_pt|is_admin_or_pt)\b",
            p.read_text(encoding="utf-8"),
        )
    )
    assert usam == []


@pytest.mark.parametrize("vinculo", [True, False])
@pytest.mark.parametrize("role", ["admin", "personal_trainer", "regular"])
@pytest.mark.parametrize("recurso", NUTRICAO_TERCEIROS)
@pytest.mark.parametrize("acao", ["ver", "editar", "excluir"])
def test_nutricao_de_terceiros_admin_todos_pt_so_aluno_com_vinculo_usuario_nunca(
    db, role, recurso, acao, vinculo
):
    """Lote 5A: admin segue em `todos`; o PT so alcanca aluno com vinculo ativo (`alunos`);
    o usuario comum segue sem terceiros."""
    db(links={(10, 99): "ativo"} if vinculo else {})
    ident = _ident(user_id=10, role=role)
    perms = _perms_do_papel(role)

    def _nova_assinatura() -> bool:
        try:
            resolve_target_user(ident, perms, recurso, acao, 99)
        except HTTPException:
            return False
        return True

    def _matriz() -> bool:
        try:
            service.authorize_target(ident, recurso, acao, 99, permissoes=perms)
        except HTTPException:
            return False
        return True

    esperado = {"admin": True, "personal_trainer": vinculo, "regular": False}[role]
    assert _matriz() is _nova_assinatura() is esperado, (role, recurso, acao, vinculo)
    if role == "personal_trainer":
        assert service.permission_scope(perms, recurso, acao) == "alunos"
    elif role == "admin":
        assert service.permission_scope(perms, recurso, acao) == "todos"


@pytest.mark.parametrize("recurso", NUTRICAO_TERCEIROS)
def test_usuario_comum_so_tem_nutricao_proprios(recurso):
    perms = _perms_do_papel("regular")
    for acao in catalog.ACTIONS:
        assert service.permission_scope(perms, recurso, acao) == "proprios"


def test_seguranca_nenhum_grupo_de_sistema_recebe_mais_do_que_os_guards_de_hoje():
    """Achado de seguranca se falhar: escopo `todos` do PT so nos Modelos (ate o lote 6);
    nutricao de terceiros do PT e `alunos` (lote 5A)."""
    permitido_todos_pt = set(MODELOS_PT)
    for acao in catalog.ACTIONS:
        pt = {r for r, a, s in DEFAULT_GRANTS_BY_GROUP["personal_trainer"] if a == acao and s == "todos"}
        assert pt <= permitido_todos_pt | {"exercicios.catalogo"}, pt - permitido_todos_pt
    usuario_todos = {(r, a) for r, a, s in DEFAULT_GRANTS_BY_GROUP["usuario"] if s == "todos"}
    assert usuario_todos == {("exercicios.catalogo", "ver")}
    for grupo in ("usuario", "personal_trainer"):
        assert not [r for r, _, _ in DEFAULT_GRANTS_BY_GROUP[grupo] if r in ADMIN_ONLY_RESOURCES]
    assert not [1 for _, _, s in DEFAULT_GRANTS_BY_GROUP["usuario"] if s == "alunos"]
    assert {(r, a) for r, a, s in DEFAULT_GRANTS_BY_GROUP["personal_trainer"] if s == "alunos"} == {
        (r, a) for r in NUTRICAO_TERCEIROS for a in catalog.ACTIONS
    }


def test_pt_nao_ganha_terceiros_em_recursos_sem_guard_de_terceiro_hoje():
    pt = dict(((r, a), s) for r, a, s in DEFAULT_GRANTS_BY_GROUP["personal_trainer"])
    usuario = dict(((r, a), s) for r, a, s in DEFAULT_GRANTS_BY_GROUP["usuario"])
    for (recurso, acao), escopo in pt.items():
        if recurso in MODELOS_PT or recurso in NUTRICAO_TERCEIROS:
            continue
        assert usuario.get((recurso, acao)) == escopo, (recurso, acao)


# ---------------------------------------------------------------------------
# authorize_target com id estrito; helper "o proprio ou quem tem permissao"
# ---------------------------------------------------------------------------

INVALIDOS = [True, False, 0, -1, 10.0, 1.5, "10", "", b"10", [10]]


@pytest.mark.parametrize("alvo", INVALIDOS)
def test_authorize_target_id_invalido_400_antes_de_qualquer_consulta(db, alvo):
    fake = db(error=AssertionError("nao deve consultar"))
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(), "cardio", "ver", alvo)
    assert _status(exc) == 400
    assert exc.value.detail["error"] == "invalid_target" and exc.value.detail["message"].strip()
    assert fake.calls == []


def test_authorize_target_id_invalido_vem_antes_do_403_de_permissao(db):
    db(groups={})
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(), "cardio", "ver", "10")
    assert _status(exc) == 400


def test_authorize_target_identity_invalida_continua_erro_de_programacao():
    ident = Identity(user_id="x", role="regular", source="session")
    with pytest.raises(ValueError):
        service.authorize_target(ident, "cardio", "ver", 5, permissoes=_perms([("g", "cardio", "ver", "todos")]))


@pytest.mark.parametrize("alvo", INVALIDOS + [None])
def test_self_or_all_alvo_obrigatorio_e_estrito(db, alvo):
    fake = db(error=AssertionError("nao deve consultar"))
    with pytest.raises(HTTPException) as exc:
        service.authorize_self_or_all(_ident(), "admin.usuarios", "editar", alvo)
    assert _status(exc) == 400 and exc.value.detail["error"] == "invalid_target"
    assert fake.calls == []


def test_self_or_all_o_proprio_passa_sem_concessao_e_sem_banco(db):
    fake = db(error=AssertionError("nao deve consultar"))
    assert service.authorize_self_or_all(_ident(10), "admin.usuarios", "editar", 10) == 10
    assert fake.calls == []


def test_self_or_all_terceiro_exige_todos(db):
    db(
        groups={
            10: [("g", "admin.usuarios", "editar", "todos")],
            11: [("g", "admin.usuarios", "editar", "alunos")],
            12: [("g", "admin.usuarios", "editar", "proprios")],
            13: [("g", "admin.usuarios", "ver", "todos")],
        },
        links={(11, 50): "ativo"},
    )
    assert service.authorize_self_or_all(_ident(10), "admin.usuarios", "editar", 50) == 50
    for quem in (11, 12, 13, 14):
        with pytest.raises(HTTPException) as exc:
            service.authorize_self_or_all(_ident(quem), "admin.usuarios", "editar", 50)
        assert _status(exc) == 403 and exc.value.detail == service.FORBIDDEN_DETAIL


def test_self_or_all_alunos_nao_consulta_vinculo(db):
    fake = db(groups={11: [("g", "cardio", "ver", "alunos")]}, links={(11, 50): "ativo"})
    with pytest.raises(HTTPException) as exc:
        service.authorize_self_or_all(_ident(11), "cardio", "ver", 50)
    assert _status(exc) == 403
    assert all("trainer_students" not in sql for sql, _ in fake.calls)


def test_self_or_all_usa_permissoes_resolvidas_e_master_sem_banco(db):
    fake = db(error=AssertionError("nao deve consultar"))
    perms = _perms([("g", "cardio", "ver", "todos")])
    assert service.authorize_self_or_all(_ident(), "cardio", "ver", 99, permissoes=perms) == 99
    master = _ident(user_id=1, role="admin", source="master_token")
    assert service.authorize_self_or_all(master, "admin.usuarios", "editar", 99) == 99
    assert fake.calls == []


def test_self_or_all_fora_do_catalogo_e_erro_e_falha_de_banco_e_503(db):
    db(groups={})
    with pytest.raises(ValueError):
        service.authorize_self_or_all(_ident(), "nao.existe", "ver", 5)
    db(error=RuntimeError("Aurora fora"))
    with pytest.raises(HTTPException) as exc:
        service.authorize_self_or_all(_ident(), "cardio", "ver", 5)
    assert _status(exc) == 503


def test_token_mestre_inclui_conta_dispositivos():
    perms = service.resolve_permissions(_ident(user_id=1, role="admin", source="master_token"))
    assert all(service.permission_scope(perms, "conta.dispositivos", a) == "todos" for a in catalog.ACTIONS)


# ---------------------------------------------------------------------------
# require_additional_permission (deps)
# ---------------------------------------------------------------------------


def _request():
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace())


def test_permissao_adicional_reusa_o_cache_da_requisicao(db):
    from app.access import require_additional_permission

    fake = db(groups={10: [("g", "admin.grupos", "editar", "todos"), ("g", "cardio", "ver", "proprios")]})
    req, ident = _request(), _ident(10)
    require_additional_permission(req, ident, "admin.grupos", "editar")
    require_additional_permission(req, ident, "cardio", "ver")
    assert len(fake.calls) == 1


def test_permissao_adicional_403_se_faltar_e_valueerror_fora_do_catalogo(db):
    from app.access import require_additional_permission

    db(groups={10: [("g", "cardio", "ver", "proprios")]})
    with pytest.raises(HTTPException) as exc:
        require_additional_permission(_request(), _ident(10), "admin.grupos", "editar")
    assert _status(exc) == 403 and exc.value.detail == service.FORBIDDEN_DETAIL
    with pytest.raises(ValueError):
        require_additional_permission(_request(), _ident(10), "nao.existe", "ver")
    with pytest.raises(ValueError):
        require_additional_permission(_request(), _ident(10), "cardio", "apagar")


def test_permissao_adicional_falha_de_banco_e_503(db):
    from app.access import require_additional_permission

    db(error=RuntimeError("Aurora fora"))
    with pytest.raises(HTTPException) as exc:
        require_additional_permission(_request(), _ident(10), "cardio", "ver")
    assert _status(exc) == 503
