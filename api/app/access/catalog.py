"""Catalogo do controle de acesso por grupos (RBAC, ADR 0011): fonte viva.

Python puro, sem banco. Define acoes, escopos (com ordem de abrangencia),
recursos (chave, rotulo em portugues, dominio) e a matriz padrao dos grupos de
sistema. As migracoes guardam retratos congelados desta matriz (nao importam
este modulo): a 111 (matriz inicial), a 113 (concessoes de `conta.dispositivos`,
lote 2B) e a 114 (concessoes de `modelos.tipos_treino`, lote 2C-1). O retrato
congelado e o acumulado 111 + 113 + 114; um teste de paridade compara o catalogo
com esse acumulado.

Sem regra de motor aqui: controle de acesso nao e regra de recuperacao,
progressao, volume etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --- Acoes e escopos -------------------------------------------------------

ACTION_VER: Final = "ver"
ACTION_EDITAR: Final = "editar"
ACTION_EXCLUIR: Final = "excluir"
ACTIONS: Final[tuple[str, ...]] = (ACTION_VER, ACTION_EDITAR, ACTION_EXCLUIR)

SCOPE_PROPRIOS: Final = "proprios"
SCOPE_ALUNOS: Final = "alunos"
SCOPE_TODOS: Final = "todos"
# Ordem de abrangencia: proprios < alunos < todos.
SCOPES: Final[tuple[str, ...]] = (SCOPE_PROPRIOS, SCOPE_ALUNOS, SCOPE_TODOS)
SCOPE_RANK: Final[dict[str, int]] = {scope: rank for rank, scope in enumerate(SCOPES)}


def scope_covers(scope: str, required: str) -> bool:
    """`scope` abrange pelo menos `required`."""
    return SCOPE_RANK[scope] >= SCOPE_RANK[required]


# --- Dominios --------------------------------------------------------------

DOMAIN_TREINO: Final = "Treino"
DOMAIN_CORPO: Final = "Corpo"
DOMAIN_NUTRICAO: Final = "Nutrição"
DOMAIN_MODELOS: Final = "Modelos"
DOMAIN_IA: Final = "IA e Insights"
DOMAIN_CONTA: Final = "Conta"
DOMAIN_ADMIN: Final = "Administração"
DOMAINS: Final[tuple[str, ...]] = (
    DOMAIN_TREINO,
    DOMAIN_CORPO,
    DOMAIN_NUTRICAO,
    DOMAIN_MODELOS,
    DOMAIN_IA,
    DOMAIN_CONTA,
    DOMAIN_ADMIN,
)


# --- Recursos --------------------------------------------------------------


@dataclass(frozen=True)
class Resource:
    key: str
    label: str
    domain: str


RESOURCES: Final[tuple[Resource, ...]] = (
    Resource("dashboards", "Dashboards", DOMAIN_TREINO),
    Resource("treino.registro", "Registro de treino", DOMAIN_TREINO),
    Resource("treino.modo_treino", "Modo Treino", DOMAIN_TREINO),
    Resource("treino.planos", "Planos de treino", DOMAIN_TREINO),
    Resource("treino.preferencias", "Preferências de treino", DOMAIN_TREINO),
    Resource("treino.academias", "Academias", DOMAIN_TREINO),
    Resource("progressao", "Progressão", DOMAIN_TREINO),
    Resource("volume.grupamentos", "Volume por grupamento", DOMAIN_TREINO),
    Resource("cardio", "Cardio", DOMAIN_TREINO),
    Resource("integracoes.intervals", "Integração Intervals", DOMAIN_TREINO),
    Resource("relatorios", "Relatórios", DOMAIN_TREINO),
    Resource("exercicios.catalogo", "Catálogo de exercícios", DOMAIN_TREINO),
    Resource("corpo.indicadores", "Indicadores corporais", DOMAIN_CORPO),
    Resource("corpo.checkin", "Check-in corporal", DOMAIN_CORPO),
    Resource("nutricao.diario", "Diário alimentar", DOMAIN_NUTRICAO),
    Resource("nutricao.plano", "Plano nutricional", DOMAIN_NUTRICAO),
    Resource("nutricao.alimentos", "Catálogo de alimentos", DOMAIN_NUTRICAO),
    Resource("nutricao.checkin", "Check-in nutricional", DOMAIN_NUTRICAO),
    Resource("modelos.treino", "Modelos de treino", DOMAIN_MODELOS),
    Resource("modelos.metas_ativacao", "Metas de ativação", DOMAIN_MODELOS),
    Resource("ia.modo_treino", "IA do Modo Treino", DOMAIN_IA),
    Resource("ia.insights", "Insights", DOMAIN_IA),
    Resource("ia.analise_treinos", "Análise de treinos por IA", DOMAIN_IA),
    Resource("ia.geracao_plano", "Geração de plano por IA", DOMAIN_IA),
    Resource("ia.cardio", "IA de cardio", DOMAIN_IA),
    Resource("ia.nutricao", "IA de nutrição", DOMAIN_IA),
    Resource("ia.modelo_periodizacao", "IA de periodização de Modelo", DOMAIN_IA),
    Resource("modelos.tipos_treino", "Tipos de treino", DOMAIN_MODELOS),
    Resource("ia.cards_exercicio", "Cards ilustrados de exercício", DOMAIN_IA),
    Resource("conta.dispositivos", "Relógio e dispositivos", DOMAIN_CONTA),
    Resource("admin.usuarios", "Usuários", DOMAIN_ADMIN),
    Resource("admin.grupos", "Grupos e acessos", DOMAIN_ADMIN),
    Resource("admin.regras_carga", "Regras de carga", DOMAIN_ADMIN),
    Resource("admin.catalogos", "Catálogos", DOMAIN_ADMIN),
    Resource("admin.seguranca_treino", "Segurança de treino", DOMAIN_ADMIN),
)

RESOURCES_BY_KEY: Final[dict[str, Resource]] = {r.key: r for r in RESOURCES}
RESOURCE_KEYS: Final[tuple[str, ...]] = tuple(r.key for r in RESOURCES)


# Dominios cujos recursos um grupo personalizado nao pode receber.
NON_GRANTABLE_DOMAINS: Final[frozenset[str]] = frozenset({DOMAIN_ADMIN})


def is_grantable(resource_key: str) -> bool:
    """Recurso concedivel a grupo personalizado (fonte unica; validate_grants consome)."""
    return RESOURCES_BY_KEY[resource_key].domain not in NON_GRANTABLE_DOMAINS


# Avisos por recurso para a tela de grupos (o frontend so exibe o texto).
RESOURCE_WARNINGS: Final[dict[str, str]] = {
    "ia.cards_exercicio": (
        "Cada card gera imagens por IA e tem custo cobrado por card. "
        "O limite diário é por usuário e igual para todos, não é um limite próprio do grupo."
    ),
}

# Avisos por escopo para a tela de grupos.
SCOPE_WARNINGS: Final[dict[str, str]] = {
    SCOPE_TODOS: (
        "O membro passa a ver e alterar os dados de todos os usuários neste recurso."
    ),
}


def catalog_payload() -> list[dict[str, object]]:
    """Catalogo serializavel para a futura tela de grupos (listado pela API)."""
    return [
        {
            "key": r.key,
            "label": r.label,
            "domain": r.domain,
            "grantable": is_grantable(r.key),
            "warning": RESOURCE_WARNINGS.get(r.key),
        }
        for r in RESOURCES
    ]


# --- Grupos de sistema -----------------------------------------------------

GROUP_ADMIN: Final = "admin"
GROUP_PERSONAL_TRAINER: Final = "personal_trainer"
GROUP_USUARIO: Final = "usuario"
GROUP_FREE: Final = "free"  # vazio, sem membros, nao e de sistema
# Grupos de sistema que carregam concessoes na tabela (admin e implicito).
SYSTEM_GROUP_KEYS: Final[tuple[str, ...]] = (
    GROUP_USUARIO,
    GROUP_PERSONAL_TRAINER,
)
# Grupo de acesso total: sem linhas de concessao, resolvido no codigo.
FULL_ACCESS_GROUP: Final = GROUP_ADMIN

# Fonte de identidade com acesso total.
MASTER_TOKEN_SOURCE: Final = "master_token"

Grant = tuple[str, str, str]  # (recurso, acao, escopo)

_DADOS_PROPRIOS: Final[tuple[str, ...]] = (
    "dashboards",
    "treino.registro",
    "treino.modo_treino",
    "treino.planos",
    "treino.preferencias",
    "treino.academias",
    "progressao",
    "volume.grupamentos",
    "corpo.indicadores",
    "corpo.checkin",
    "cardio",
    "integracoes.intervals",
    "nutricao.diario",
    "nutricao.plano",
    "nutricao.alimentos",
    "nutricao.checkin",
    "relatorios",
)
_IA_USUARIO: Final[tuple[str, ...]] = (
    "ia.modo_treino",
    "ia.insights",
    "ia.analise_treinos",
    "ia.geracao_plano",
    "ia.cardio",
    "ia.nutricao",
)
# Relogio e dispositivos (lote 2B): usuario e personal trainer, dados proprios.
_CONTA_PROPRIOS: Final[tuple[str, ...]] = ("conta.dispositivos",)
# Usuario: so `ver`.
_USUARIO_SO_VER: Final[tuple[tuple[str, str], ...]] = (
    ("exercicios.catalogo", SCOPE_TODOS),
    ("modelos.treino", SCOPE_PROPRIOS),
)
# Personal trainer: o que `require_admin_or_pt` libera hoje (escopo todos).
_PT_RECURSOS_TODOS: Final[tuple[str, ...]] = (
    "modelos.treino",
    "modelos.metas_ativacao",
    "modelos.tipos_treino",
    "ia.modelo_periodizacao",
)
# Personal trainer: nutricao de terceiros, escopo `alunos` desde o lote 5 (migracao 115);
# os recursos de `_PT_RECURSOS_TODOS` seguem em `todos` ate o lote 6.
_PT_NUTRICAO_TERCEIROS: Final[tuple[str, ...]] = (
    "nutricao.diario",
    "nutricao.plano",
    "nutricao.checkin",
    "ia.nutricao",
)


def _linhas(resources: tuple[str, ...], actions: tuple[str, ...], scope: str) -> list[Grant]:
    return [(resource, action, scope) for resource in resources for action in actions]


def _build_usuario() -> tuple[Grant, ...]:
    rows = _linhas(
        _DADOS_PROPRIOS + _IA_USUARIO + _CONTA_PROPRIOS, ACTIONS, SCOPE_PROPRIOS
    )
    rows += [(resource, ACTION_VER, scope) for resource, scope in _USUARIO_SO_VER]
    return tuple(rows)


def _build_personal_trainer() -> tuple[Grant, ...]:
    # Um escopo por (recurso, acao): a linha `todos` do PT substitui a do usuario.
    rows: dict[tuple[str, str], str] = {(r, a): s for r, a, s in _build_usuario()}
    for resource, action, scope in _linhas(
        _PT_RECURSOS_TODOS, ACTIONS, SCOPE_TODOS
    ) + _linhas(_PT_NUTRICAO_TERCEIROS, ACTIONS, SCOPE_ALUNOS):
        rows[(resource, action)] = scope
    return tuple((resource, action, scope) for (resource, action), scope in rows.items())


# Matriz padrao (mesmas chaves de `GRANTS_BY_GROUP` das migracoes 111, 113 e 114). `admin`
# fica vazio (acesso total implicito) e `free` e criado vazio (nao e de sistema).
DEFAULT_GRANTS_BY_GROUP: Final[dict[str, tuple[Grant, ...]]] = {
    GROUP_ADMIN: (),
    GROUP_PERSONAL_TRAINER: _build_personal_trainer(),
    GROUP_USUARIO: _build_usuario(),
    GROUP_FREE: (),
}
