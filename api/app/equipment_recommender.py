"""Recomendador de equipamento POR USUARIO (PR-08 da refatoracao dos motores;
PLANO_20260912.md secoes 2.4, 6.1 e 9; corrige G5).

Fonte unica de `GET /gym-equipment/recommend` e `/recommend-all` (rotas em
`app/gyms.py`) e do contexto de geracao de ficha
(`workout_plan_ai.gather_ficha_context`, `_recommend_for_group`), que passam a
chamar estas funcoes em Python com o `user_id` de quem pediu - nunca mais via
HTTP interno com o token mestre (que resolveria como o admin e ranquearia a
ficha de todo mundo pelas estrelas/recencia DELE).

Ranking: `app/domain/equipment_ranking.py`. Seguranca: resolvedor unico
`app/domain/training_safety.py`, lido em lote. Numero de SELECTs FIXO por
chamada (candidatos + recencia + estrelas + 5 de seguranca), independente da
quantidade de equipamentos da academia: sem N+1.

Estrelas = nota do usuario para o PAR exercicio+equipamento do item (PR-08b,
decisao do usuario em 2026-09-17; antes era por equipamento). O item do
recomendador JA e um par: `gym_equipment.equipment_id` + `mapping_id`
(= `exercise_catalog.id`) -> `equipment_exercises`. Item cujo par nao existe
em `equipment_exercises` fica sem nota (3 no ranking), sem erro.

Somente leitura. Sem DDL em runtime: `user_equipment_exercise_preferences` vem
da migracao 035. Se o codigo chegar antes da migracao (deploy com
RUN_DB_MIGRATIONS != true), a leitura de estrelas cai para "ninguem avaliou
nada" (todos = 3) em vez de derrubar o Modo Treino; as rotas de escrita de
preferencia respondem 503 ate a migracao rodar.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from .domain import equipment_ranking as ranking
from .domain.laboral import sql_not_laboral
from .domain import training_safety as ts
from .workout_plan_edit_rules import PLANNED_PLAN_STATUS

logger = logging.getLogger(__name__)

PUBLIC_ASSETS_DOMAIN = "app.musclefit.com.br"
PREFERENCES_TABLE = "user_equipment_exercise_preferences"
_ALIAS_SEPARATOR = "␟"

# Grupo primario do exercicio de `ge.mapping_id` (alias `pg`). Definicao
# unica: o recomendador usa INNER (exercicio sem grupo nao e candidato);
# a tela "Preferencias de Treino" usa a variante LEFT (ainda pode ser avaliado).
_PRIMARY_GROUP_ON = """
    muscle_groups pg ON pg.id = (
        SELECT emc_pri.muscle_group_id
        FROM exercise_muscle_contributions emc_pri
        WHERE emc_pri.exercise_id = ge.mapping_id
          AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
        ORDER BY emc_pri.id ASC LIMIT 1
    )
"""
_PRIMARY_GROUP_JOIN = "\n    JOIN " + _PRIMARY_GROUP_ON.lstrip()
PRIMARY_GROUP_LEFT_JOIN = "\n    LEFT JOIN " + _PRIMARY_GROUP_ON.lstrip()


def is_missing_preferences_table_error(exc: Exception) -> bool:
    """MySQL 1146 para `user_equipment_exercise_preferences` (migracao 035 pendente). A
    mensagem do servidor e a mesma no PyMySQL e na RDS Data API."""
    text = str(exc)
    return PREFERENCES_TABLE in text and "doesn't exist" in text


def _iso(value: Any) -> Any:
    """date/datetime -> ISO 8601 (payload pronto para JSON, igual nas rotas e
    nas chamadas diretas da geracao de ficha)."""
    return value.isoformat() if isinstance(value, (date, datetime)) else value


# ---------------------------------------------------------------------------
# Leituras em lote
# ---------------------------------------------------------------------------


PairKey = tuple[int, int]  # (exercise_catalog.id, equipment.id) - mesma chave do resolvedor de seguranca


def load_user_stars(cursor: Any, user_id: int) -> dict[PairKey, int]:
    """(exercise_id, equipment_id) -> estrelas do usuario para o par. UM SELECT,
    qualquer que seja o numero de pares. Ausencia = sem avaliacao (3 no ranking)."""
    try:
        cursor.execute(
            f"""
            SELECT ee.exercise_id, ee.equipment_id, p.stars
            FROM {PREFERENCES_TABLE} p
            JOIN equipment_exercises ee ON ee.id = p.equipment_exercise_id
            WHERE p.user_id = %s
            """,
            (user_id,),
        )
    except Exception as exc:  # noqa: BLE001 - so o 1146 desta tabela e tolerado
        if not is_missing_preferences_table_error(exc):
            raise
        logger.warning("user_equipment_exercise_preferences ausente (migracao 035 pendente): ranking sem estrelas")
        return {}
    return {(int(row["exercise_id"]), int(row["equipment_id"])): int(row["stars"]) for row in cursor.fetchall() or []}


def pair_key(row: dict[str, Any]) -> Optional[PairKey]:
    """Par do item. `canonical_exercise_id` (quando o chamador ja resolveu
    `gym_equipment.mapping_id` via `exercise_legacy_id_map`) vence o
    `mapping_id` cru; sem exercicio conhecido nao ha par (-> sem nota)."""
    exercise_id = row.get("canonical_exercise_id") or row.get("mapping_id")
    if not exercise_id or not row.get("equipment_id"):
        return None
    return int(exercise_id), int(row["equipment_id"])


def _pair_stars(stars: dict[PairKey, int], row: dict[str, Any]) -> Optional[int]:
    key = pair_key(row)
    return stars.get(key) if key is not None else None


def load_user_recency(
    cursor: Any, user_id: int, gym_id: int, *, include_planned: bool = False
) -> dict[int, dict[str, Any]]:
    """gym_equipment.id -> ultimo uso PELO USUARIO (G5: antes era global).

    `include_planned` (M101b, so o ramo "exercicio livre" do Modo B usa): a
    recencia tambem conta o equipamento das fichas GERADAS e ainda nao
    executadas (`workout_plans.status = PLANNED_PLAN_STATUS`), pela data em que a ficha
    foi criada. `last_used_at` vira o MAX entre executado e planejado, em
    ISO 8601, e duas geracoes seguidas sem treino entre elas alternam o par
    escolhido. Sem a flag, nada muda (mesma consulta, mesmo payload)."""
    cursor.execute(
        """
        SELECT e.equipment_id AS gym_equipment_id,
               MAX(a.local_date) AS last_used_date,
               MAX(COALESCE(a.ended_at, a.started_at, a.created_at)) AS last_used_at
        FROM exercises e
        JOIN activities a ON a.id = e.activity_id
        JOIN gym_equipment ge ON ge.id = e.equipment_id
        WHERE a.user_id = %s AND ge.gym_id = %s
        GROUP BY e.equipment_id
        """,
        (user_id, gym_id),
    )
    executed = {int(row["gym_equipment_id"]): row for row in cursor.fetchall() or []}
    if not include_planned:
        return executed
    cursor.execute(
        """
        SELECT wpe.preferred_equipment_id AS gym_equipment_id,
               MAX(wp.created_at) AS last_planned_at
        FROM workout_plan_exercises wpe
        JOIN workout_plans wp ON wp.id = wpe.plan_id
        JOIN gym_equipment ge ON ge.id = wpe.preferred_equipment_id
        WHERE wp.user_id = %s AND ge.gym_id = %s AND wp.status = %s
        GROUP BY wpe.preferred_equipment_id
        """,
        (user_id, gym_id, PLANNED_PLAN_STATUS),
    )
    planned = {int(row["gym_equipment_id"]): row.get("last_planned_at") for row in cursor.fetchall() or []}
    merged: dict[int, dict[str, Any]] = {}
    for gym_equipment_id in set(executed) | set(planned):
        row = dict(executed.get(gym_equipment_id) or {"gym_equipment_id": gym_equipment_id})
        row["last_used_at"] = _latest_iso(row.get("last_used_at"), planned.get(gym_equipment_id))
        merged[gym_equipment_id] = row
    return merged


def _comparable_iso(value: Any) -> Optional[str]:
    """Data/hora em ISO 8601 com `T` (o driver PyMySQL devolve datetime; a
    RDS Data API, texto com espaco). Sem isso a comparacao de texto do ranking
    misturaria os dois formatos."""
    if value is None:
        return None
    text = str(_iso(value))
    return text.replace(" ", "T", 1) if len(text) > 10 and text[10] == " " else text


def _latest_iso(*values: Any) -> Optional[str]:
    present = [text for text in (_comparable_iso(value) for value in values) if text]
    return max(present) if present else None


def _load_candidates(cursor: Any, gym_id: int, group: Optional[str]) -> list[dict[str, Any]]:
    # M100: exercicio LABORAL nunca e candidato de grupo (Modo A, recomendacao
    # por grupo): ele so entra quando o usuario o poe no Modelo.
    where = ["ge.gym_id = %s", "ge.is_active = 1", sql_not_laboral("zm")]
    params: list[Any] = [gym_id]
    if group is not None:
        where.append("pg.name = %s")
        params.append(group)
    cursor.execute(
        f"""
        SELECT
            ge.id, ge.mapping_id, ge.equipment_id, eq.name AS equipment_name,
            (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path) ELSE NULL END) AS photo_url,
            zm.name AS mapping_exercise_name,
            (
                SELECT zam.external_name
                FROM exercise_external_mappings zam
                WHERE zam.exercise_id = ge.mapping_id AND zam.provider = 'AMAZFIT' AND zam.is_active = 1
                ORDER BY zam.id ASC LIMIT 1
            ) AS mapping_amazfit_name,
            pg.id AS muscle_group_id, pg.name AS primary_group,
            GROUP_CONCAT(DISTINCT ea.alias ORDER BY ea.alias SEPARATOR '{_ALIAS_SEPARATOR}') AS mapping_aliases_raw
        FROM gym_equipment ge
        JOIN exercise_catalog zm ON zm.id = ge.mapping_id
        {_PRIMARY_GROUP_JOIN}
        JOIN equipment eq ON eq.id = ge.equipment_id
        LEFT JOIN exercise_aliases ea ON ea.exercise_id = ge.mapping_id
        WHERE {' AND '.join(where)}
        GROUP BY ge.id, ge.mapping_id, ge.equipment_id, eq.name, eq.photo_path, zm.name, pg.id, pg.name
        ORDER BY ge.id ASC
        """,
        tuple(params),
    )
    return list(cursor.fetchall() or [])


def rank_gym_equipment_rows(
    cursor: Any,
    rows: list[dict[str, Any]],
    *,
    gym_id: int,
    user_id: Optional[int],
    include_planned: bool = False,
) -> list[dict[str, Any]]:
    """Ordena associacoes `gym_equipment` (`id`, `equipment_id`,
    `equipment_name` e o exercicio do par: `canonical_exercise_id` ou
    `mapping_id`) pelo ranking do usuario - para quem so precisa escolher
    entre candidatos ja filtrados (`services.resolve_equipment_id`,
    `_find_gym_equipment_for_exercise`), sem payload de recomendacao. Sem
    `user_id` nao ha estrelas nem recencia: decide por nome e id.
    `include_planned`: ver `load_user_recency` (so o exercicio livre, M101b)."""
    if not rows:
        return []
    recency = (
        load_user_recency(cursor, user_id, gym_id, include_planned=include_planned) if user_id is not None else {}
    )
    stars = load_user_stars(cursor, user_id) if user_id is not None else {}
    return ranking.rank_items(
        {
            **row,
            "user_preference_stars": _pair_stars(stars, row),
            "last_used_by_user_at": _iso((recency.get(int(row["id"])) or {}).get("last_used_at")),
        }
        for row in rows
    )


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def _enrich_and_rank(
    conn: Any, cursor: Any, candidates: list[dict[str, Any]], gym_id: int, user_id: Optional[int]
) -> tuple[list[dict[str, Any]], dict[int, ts.TrainingSafety]]:
    if not candidates:
        return [], {}
    # Sem usuario conhecido nao ha estrelas nem recencia (nunca as de outro).
    recency = load_user_recency(cursor, user_id, gym_id) if user_id is not None else {}
    stars = load_user_stars(cursor, user_id) if user_id is not None else {}
    safeties = ts.resolve_training_safety_bulk(
        conn, [(int(row["mapping_id"]), int(row["equipment_id"])) for row in candidates]
    )
    safety_by_item: dict[int, ts.TrainingSafety] = {}
    items: list[dict[str, Any]] = []
    for row in candidates:
        item = dict(row)
        raw_aliases = item.pop("mapping_aliases_raw", None)
        item["mapping_aliases"] = raw_aliases.split(_ALIAS_SEPARATOR) if raw_aliases else []
        used = recency.get(int(item["id"])) or {}
        # `last_used_date`/`last_used_at` ja existiam no contrato (eram globais);
        # continuam, agora POR USUARIO, e `last_used_by_user_at` e o nome novo.
        item["last_used_date"] = _iso(used.get("last_used_date"))
        item["last_used_at"] = _iso(used.get("last_used_at"))
        item["last_used_by_user_at"] = item["last_used_at"]
        item["gym_equipment_id"] = item["id"]
        item["user_preference_stars"] = _pair_stars(stars, item)  # nota DO PAR; None = sem avaliacao (3 no ranking)
        safety = safeties[(int(item["mapping_id"]), int(item["equipment_id"]))]
        item.update(ranking.safety_fields(safety))
        safety_by_item[item["id"]] = safety
        items.append(item)
    return ranking.rank_items(items), safety_by_item


def recommend_for_group(
    conn: Any,
    *,
    gym_id: int,
    user_id: Optional[int],
    group: str,
    exclude_gym_equipment_id: Optional[int] = None,
    required_technique: Optional[str] = None,
) -> dict[str, Any]:
    """Payload de `/gym-equipment/recommend` (sem `ok`), para UM grupo muscular.
    `required_technique` fora do catalogo ativo exclui todos os itens
    (TECHNIQUE_INCOMPATIBLE); a rota HTTP responde 422 antes de chegar aqui."""
    with conn.cursor() as cursor:
        candidates = _load_candidates(cursor, gym_id, group)
        ranked, safety_by_item = _enrich_and_rank(conn, cursor, candidates, gym_id, user_id)
    has_equipment_for_group = bool(ranked)  # ignora exclude_equipment_id, como antes
    if exclude_gym_equipment_id:
        ranked = [item for item in ranked if item["id"] != exclude_gym_equipment_id]
    items, excluded = ranking.split_by_required_technique(ranked, safety_by_item, required_technique)
    return {
        "gym_id": gym_id,
        "primary_group": group,
        "excluded_equipment_id": exclude_gym_equipment_id,
        "required_technique": required_technique or None,
        "has_equipment_for_group": has_equipment_for_group,
        "recommended": items[0] if items else None,
        "alternatives": items[1:],
        "items": items,
        "excluded": excluded,
    }


def recommend_all(
    conn: Any, *, gym_id: int, user_id: Optional[int], required_technique: Optional[str] = None
) -> dict[str, Any]:
    """Payload de `/gym-equipment/recommend-all` (sem `ok`): todos os grupos."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT mg.id AS muscle_group_id, mg.name AS primary_group
            FROM exercise_muscle_contributions emc
            JOIN exercise_catalog ec ON ec.id = emc.exercise_id AND ec.is_active = 1
            JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
            WHERE emc.role = 'PRIMARY' AND emc.is_active = 1
            ORDER BY mg.name
            """
        )
        all_groups = [(row["muscle_group_id"], row["primary_group"]) for row in cursor.fetchall() or []]
        candidates = _load_candidates(cursor, gym_id, None)
        ranked, safety_by_item = _enrich_and_rank(conn, cursor, candidates, gym_id, user_id)

    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:  # ja ranqueado: a ordem dentro do grupo e preservada
        by_group.setdefault(item["primary_group"], []).append(item)

    groups_out: dict[str, Any] = {}
    groups_by_id_out: dict[int, Any] = {}
    for muscle_group_id, group in all_groups:
        group_ranked = by_group.get(group, [])
        items, excluded = ranking.split_by_required_technique(group_ranked, safety_by_item, required_technique)
        payload = {
            "has_equipment_for_group": bool(group_ranked),
            "recommended": items[0] if items else None,
            "alternatives": items[1:],
            "excluded": excluded,
        }
        groups_out[group] = payload
        # `groups_by_id` (chave = muscle_group_id, estavel a renomeacao): mesmo
        # payload de `groups`, indexado por id (correcao de 2026-08-25).
        groups_by_id_out[muscle_group_id] = payload
    return {
        "gym_id": gym_id,
        "required_technique": required_technique or None,
        "groups": groups_out,
        "groups_by_id": groups_by_id_out,
    }
