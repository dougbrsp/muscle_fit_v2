"""Contexto de equipamento da progressao: maquina x peso livre (issue #188).

Dono de duas regras (decisoes do usuario de 2026-11-16):

1. Maquina x peso livre vem de `equipment_types.code` (`equipment.equipment_type_id`
   -> `equipment_types.id`). Maquina (`MACHINE_TYPE_CODES`) tem a progressao
   SEPARADA por academia: a mesma maquina em outra academia nao e a mesma carga
   (regulagem, polia, placa). Peso livre (barra, halter, Smith, banco, sem
   equipamento) continua como antes: a academia nao entra na comparacao. Tipo
   ausente ou codigo desconhecido = peso livre (o comportamento anterior).
2. Calibracao: so DEPOIS de uma troca de aparelho ou de academia, o contexto
   de maquina (maquina + academia) e classificado a partir da
   `CALIBRATION_MIN_COMPARABLE_SESSIONS`-esima sessao comparavel. Quem nunca
   trocou segue o minimo de sempre. O que e "troca" e a sequencia comparavel
   sao do dono `intelligence/dashboard/comparability.py`
   (`in_equipment_calibration`: a troca e local ao segmento atual, procurada
   so na janela do veredito `comparability.VERDICT_LOOKBACK_EXPOSURES`).

Python puro, sem banco. A comparabilidade consome `context_gym_id` (chave) e
`is_calibrating` (veredito); ninguem repete os codigos nem o limiar.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence, TypeVar

T = TypeVar("T")

#: Codigos de `equipment_types.code` que separam a progressao por academia.
MACHINE_TYPE_CODES: frozenset[str] = frozenset({"MACHINE", "PLATE_LOADED", "CABLE_STATION"})

#: Sessoes comparaveis no contexto de maquina antes de classificar a progressao.
CALIBRATION_MIN_COMPARABLE_SESSIONS = 3

#: Codigo da carga de PARTIDA vinda do mesmo aparelho em OUTRA academia.
CALIBRATION_CODE_START_OTHER_GYM = "calibration_start_other_gym"


def latest_in_gym_context(
    items: Sequence[T],
    gym_context: Optional[int],
    context_of: Callable[[T], Optional[int]],
) -> tuple[Optional[T], bool]:
    """REGRA UNICA da carga de partida de outra academia (#188), sobre trilhas,
    logs ou exposicoes. `items` em ordem do mais antigo para o mais recente;
    `context_of(item)` = o `context_gym_id` do item.

    - `gym_context=None` (peso livre / sem recorte): o mais recente, `False`.
    - Ha item NESTE contexto: o mais recente dele, `False`.
    - Senao, o mais recente de OUTRA academia de maquina (contexto nao `None`):
      `(item, True)` = carga de partida (`CALIBRATION_CODE_START_OTHER_GYM`).
    - Nada: `(None, False)`."""
    if gym_context is None:
        return (items[-1] if items else None), False
    gym = int(gym_context)
    other: Optional[T] = None
    for item in reversed(items):
        context = context_of(item)
        if context == gym:
            return item, False
        if other is None and context is not None:
            other = item
    return other, other is not None

def is_machine_type(code: Any) -> bool:
    """`True` so para os codigos de maquina; `None`/desconhecido = peso livre."""
    if code is None:
        return False
    return str(code).strip().upper() in MACHINE_TYPE_CODES


def context_gym_id(type_code: Any, gym_id: Any) -> Optional[int]:
    """Academia que entra na chave de comparabilidade: a do vinculo para
    maquina; `None` para peso livre, tipo ausente/desconhecido ou academia
    ausente (tudo como antes)."""
    if gym_id is None or not is_machine_type(type_code):
        return None
    return int(gym_id)


def is_calibrating(comparable_sessions: int) -> bool:
    """O contexto de maquina ainda esta calibrando com estas sessoes comparaveis?"""
    return int(comparable_sessions) < CALIBRATION_MIN_COMPARABLE_SESSIONS


def context_in_calibration(contexts: Sequence[tuple[Any, Optional[int], Optional[int]]]) -> bool:
    """REGRA UNICA de calibracao (#188): o contexto da ULTIMA exposicao esta
    calibrando? Usada pela comparabilidade (`in_equipment_calibration`) e pelo
    motor de carga (`progression_evaluator`).

    `contexts`: exposicoes do mesmo exercicio, em ordem cronologica, como
    `(chave_fisica, equipment_id, gym_context)`. `chave_fisica` e a identidade
    do aparelho SEM academia (`None` = aparelho nao identificado, nunca casa);
    `gym_context` vem de `context_gym_id` (`None` = peso livre).

    Segmento = exposicoes andando para tras a partir da ultima com a mesma
    chave fisica, pulando as de OUTRA academia. Calibra quando a ultima e de
    maquina, o segmento tem menos sessoes que o minimo e foi ABERTO por troca:
    a exposicao que o interrompeu tem aparelho identificado e e de OUTRO
    aparelho, ou houve exposicao de outra academia pulada. Sem aparelho
    identificado ou o comeco do historico lido NAO sao troca: o primeiro
    contexto, sem troca, nao calibra."""
    if not contexts or contexts[-1][2] is None:
        return False
    last_physical, last_equipment, last_gym = contexts[-1]
    sessions = 0
    other_gym = False
    opened_by_other_equipment = False
    for physical, equipment_id, gym in reversed(contexts):
        if physical is None or physical != last_physical:
            opened_by_other_equipment = equipment_id is not None and equipment_id != last_equipment
            break
        if gym != last_gym:
            other_gym = True
            continue
        sessions += 1
    return is_calibrating(sessions) and (opened_by_other_equipment or other_gym)



# ---------------------------------------------------------------------------
# Fabricante por academia (#188, decisoes do usuario de 16-17/11): a mesma
# maquina do catalogo pode ser de fabricantes diferentes em academias
# diferentes. `gym_equipment_catalog.manufacturer` (migracao 119) e o da
# academia; `equipment.manufacturer` (022) e o PADRAO quando a academia nao
# informou. Sem copia de dados: o efetivo e resolvido na leitura, so aqui.
# ---------------------------------------------------------------------------

MANUFACTURER_SOURCE_GYM = "gym"
MANUFACTURER_SOURCE_CATALOG = "catalog"
#: Mesmo tamanho de `equipment.manufacturer` (VARCHAR(100), migracao 022).
MANUFACTURER_MAX_LENGTH = 100


def manufacturer_join_sql(gym_expr: str, equipment_expr: str, alias: str = "gec") -> str:
    """JOINs do fabricante da academia e do dono da academia (para a posse).
    A linha do catalogo da academia vale mesmo inativa: o fabricante descreve a
    maquina fisica; sem linha -> sem fabricante da academia (vale o padrao)."""
    return (
        f"LEFT JOIN gym_equipment_catalog {alias} ON {alias}.gym_id = {gym_expr} "
        f"AND {alias}.equipment_id = {equipment_expr} "
        f"LEFT JOIN gyms {alias}_g ON {alias}_g.id = {gym_expr}"
    )


def manufacturer_columns_sql(alias: str = "gec", equipment_alias: str = "eq") -> str:
    """Colunas cruas lidas por `manufacturer_fields` (use com `manufacturer_join_sql`)."""
    return (
        f"{alias}.id AS gym_equipment_catalog_id, {alias}.manufacturer AS gym_manufacturer, "
        f"{equipment_alias}.manufacturer AS catalog_manufacturer, {alias}_g.owner_user_id AS gym_owner_user_id"
    )


def _clean(value: Any) -> Optional[str]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    text = str(value).strip() if value is not None else ""
    return text or None


def effective_manufacturer(gym_value: Any, catalog_value: Any) -> tuple[Optional[str], Optional[str]]:
    """`(fabricante, origem)`: o da academia quando informado, senao o do
    catalogo, senao `(None, None)`. Vazio/brancos = nao informado."""
    gym = _clean(gym_value)
    if gym:
        return gym, MANUFACTURER_SOURCE_GYM
    catalog = _clean(catalog_value)
    if catalog:
        return catalog, MANUFACTURER_SOURCE_CATALOG
    return None, None


def manufacturer_fields(row: dict[str, Any], can_edit: Callable[[Optional[int]], bool]) -> dict[str, Any]:
    """Campos aditivos do aparelho numa academia, prontos para a tela.
    `can_edit(owner_user_id)` e a posse do RBAC (o chamador injeta); sem linha
    no catalogo da academia nao ha o que editar."""
    value, source = effective_manufacturer(row.get("gym_manufacturer"), row.get("catalog_manufacturer"))
    link_id = row.get("gym_equipment_catalog_id")
    return {
        "gym_equipment_catalog_id": int(link_id) if link_id is not None else None,
        "manufacturer": value,
        "manufacturer_source": source,
        "can_edit_manufacturer": bool(link_id is not None and can_edit(row.get("gym_owner_user_id"))),
    }


_MANUFACTURER_RAW_COLUMNS = ("gym_manufacturer", "catalog_manufacturer", "gym_owner_user_id")


def with_manufacturer_fields(row: dict[str, Any], can_edit: Callable[[Optional[int]], bool]) -> dict[str, Any]:
    """A linha sem as colunas cruas de `manufacturer_columns_sql` (o dono da
    academia nao sai no payload) e com os campos de `manufacturer_fields`."""
    out = {k: v for k, v in row.items() if k not in _MANUFACTURER_RAW_COLUMNS}
    out.update(manufacturer_fields(row, can_edit))
    return out


__all__ = [
    "with_manufacturer_fields",
    "CALIBRATION_CODE_START_OTHER_GYM",
    "CALIBRATION_MIN_COMPARABLE_SESSIONS",
    "MACHINE_TYPE_CODES",
    "context_gym_id",
    "MANUFACTURER_MAX_LENGTH",
    "MANUFACTURER_SOURCE_CATALOG",
    "MANUFACTURER_SOURCE_GYM",
    "context_in_calibration",
    "effective_manufacturer",
    "manufacturer_columns_sql",
    "manufacturer_fields",
    "manufacturer_join_sql",
    "is_calibrating",
    "is_machine_type",
    "latest_in_gym_context",
]
