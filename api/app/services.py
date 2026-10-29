from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import exercise_card_storage
from .db import transaction
from .domain import effective_intensity, equipment_pair
from .domain.calories_source import calories_source_label, resolve_calories_source
from .domain import prescription as prescription_domain
from .domain import technique_accounting
from .domain.equipment_pair import is_pair_coherent
from .domain.recovery_signal_summary import summarize_signals
from .domain.set_measures import normalize_measure
from .domain.set_sides import SIDE_FIELDS, apply_canonical, attach_sides_text
from .domain.deload import sql_not_deload
from .domain.training_goal import goal_value, sql_goal_is
from .domain.training_safety import resolve_training_safety_bulk
from .equipment_recommender import rank_gym_equipment_rows
from .domain.equipment_record import current_mark, record_payload, summarize_equipment_history
from .load_conventions import (
    DEFAULT_LOAD_CONVENTION,
    equipment_exercises_join_sql,
    load_convention_select_expr,
    normalize_load_convention,
)
from .domain.set_measures import format_set_measure, is_rep_based, sql_is_rep_based
from .set_measure_input import ensure_valid_set_measure, set_measure_columns
from .domain import laboral as laboral_domain
from .set_types import (
    confirmed_sets,
    is_preparatory_set,
    is_technique_application,
    normalize_set_type,
    sql_is_working,
    sql_not_preparatory,
    sql_set_confirmed,
)
from .exercise_mapping_canonical import match_mapping
from .exercise_set_update_merge import (
    PRESERVED_SET_FIELDS,
    SERVER_KEPT_SET_FIELDS,
    keeps_protocol_target,
    link_technique_applications,
    merge_sets_for_update,
)
from .workout_plan_exercise_merge import ADVANCED_EXERCISE_FIELDS, merge_unsent_advanced_fields
from .workout_plan_edit_rules import is_plan_editable

logger = logging.getLogger(__name__)


def to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def from_json_list(value: Any) -> list[Any] | None:
    """Decodifica uma coluna MySQL JSON lida via PyMySQL de volta para list.

    PyMySQL não decodifica automaticamente colunas MySQL `JSON` (devolve a
    string crua). Se `value` já vier como list (ex. em testes que montam o
    dict diretamente) ou vazio/None, devolve como está sem quebrar.
    """
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, list) else decoded
    return value


def from_json_dict(value: Any) -> dict[str, Any] | None:
    """Decodifica uma coluna MySQL JSON lida via PyMySQL de volta para dict.

    Mesmo motivo de `from_json_list`: PyMySQL não decodifica automaticamente
    colunas MySQL `JSON`, devolve a string crua. Usado em `workout_plans.raw_payload`
    — sem este parse, `GET /workout-plans/{id}` devolvia `raw_payload` como
    string JSON dupla-codificada, fazendo o frontend (que espera um dict, ex.
    `detail.raw_payload?.template_slot_targets` em WorkoutPlansClient.tsx)
    ler `undefined` silenciosamente. Isso fazia a tabela "Ativação muscular
    (ficha em edição)" perder a coluna Meta e cair no limiar genérico
    (<9 séries) ao abrir uma ficha JÁ SALVA para editar, mesmo depois do fix
    789b0c20/b27d036f — que corrigiu a fórmula de comparação mas não este
    bug de serialização, que só afeta fichas recarregadas do banco (um
    rascunho recém-gerado nunca passa por esta função, pois vem direto da
    resposta JSON de `POST /workout-plans/generate`).
    """
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return value


def source_id(conn, name: str, source_type: str = "api") -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM sources WHERE name=%s", (name,))
        row = cur.fetchone()
        if row:
            return row["id"]

        cur.execute(
            "INSERT INTO sources (name, type, description) VALUES (%s, %s, %s)",
            (name, source_type, f"Fonte criada automaticamente: {name}"),
        )
        return cur.lastrowid


def compute_exercise_stats(sets: list[dict[str, Any]]) -> dict[str, Any]:
    """Agregados de `exercises` (sets_count/reps_total/volume_kg/min/best
    weight/avg_rpe). Series preparatorias (`warmup`/`feeler`) NAO entram nos
    agregados — senao dashboards de volume/series inflam quando o modo treino
    passa a gravar aquecimento/ajuste. Trabalho e aplicacoes de tecnica
    continuam contando (mesmo comportamento anterior). Linhas sem `set_type`
    (legado) contam como `working`.

    M76 (Lote 3a): `best_weight_kg` e `avg_rpe` NAO olham as aplicacoes de
    tecnica (`is_technique_application`: mini-blocos de rest-pause, drops,
    reps forcadas) - a carga do drop nao e a "melhor carga" do exercicio e o
    RPE 10 de cada mini-bloco puxava a media para cima. Contagens, reps,
    volume e `min_weight_kg` continuam com todas as series nao preparatorias.

    Medidas de serie (Lote T3): toda serie conta em `sets_count`; `reps_total`
    soma as reps gravadas (inclui `max_reps`; a serie por tempo nao tem reps);
    `volume_kg` (reps x kg) so das series de repeticoes
    (`set_measures.is_rep_based`; medida ausente = reps)."""
    # Modo Intensivo (092): série com reps pendentes não existe para os
    # agregados até a confirmação (`set_types.is_pending_set`).
    sets = [s for s in confirmed_sets(sets) if not is_preparatory_set(s)]
    reps_total = sum((s.get("reps") or 0) for s in sets) if sets else None

    weights = [Decimal(str(s["weight_kg"])) for s in sets if s.get("weight_kg") is not None]
    main_sets = [s for s in sets if not is_technique_application(s)]
    main_weights = [Decimal(str(s["weight_kg"])) for s in main_sets if s.get("weight_kg") is not None]
    rpes = [Decimal(str(s["rpe"])) for s in main_sets if s.get("rpe") is not None]

    volume = Decimal("0")
    has_volume = False

    for s in sets:
        reps = s.get("reps")
        weight = s.get("weight_kg")
        if reps is not None and weight is not None and is_rep_based(s.get("measure")):
            volume += Decimal(str(reps)) * Decimal(str(weight))
            has_volume = True

    return {
        "sets_count": len(sets),
        "reps_total": reps_total,
        "volume_kg": volume if has_volume else None,
        "min_weight_kg": min(weights) if weights else None,
        "best_weight_kg": max(main_weights) if main_weights else None,
        "avg_rpe": (sum(rpes) / Decimal(len(rpes))) if rpes else None,
    }


# Chaves de série acrescentadas DEPOIS dos hashes de importação (Lote T3,
# 2026-10-20). `ActivityIn.model_dump()` sempre as inclui; com o valor ausente
# (`None`, todo cliente antigo) ficam FORA do texto hasheado, senão o mesmo
# treino reenviado sem `external_id` ganharia outro id e viraria duplicata.
_HASH_OPTIONAL_SET_KEYS: tuple[str, ...] = ("measure", "duration_seconds")


def _hashable_sets(sets: Any) -> Any:
    if not isinstance(sets, list):
        return sets
    return [
        {k: v for k, v in st.items() if not (k in _HASH_OPTIONAL_SET_KEYS and v is None)}
        if isinstance(st, dict)
        else st
        for st in sets
    ]


def _hashable_exercises(exercises: Any) -> Any:
    if not isinstance(exercises, list):
        return exercises
    return [
        {**ex, "sets": _hashable_sets(ex["sets"])} if isinstance(ex, dict) and "sets" in ex else ex
        for ex in exercises
    ]


def _attach_set_measure(set_row: dict[str, Any]) -> dict[str, Any]:
    """Medida normalizada (`reps` quando ausente) e `measure_text` do dono
    (`set_measures.format_set_measure`) numa série lida do banco (Lote T3)."""
    measure = normalize_measure(set_row.get("measure"))
    set_row["measure"] = measure.value
    set_row["measure_text"] = format_set_measure(measure, set_row.get("reps"), set_row.get("duration_seconds"))
    return set_row


def make_external_id(activity: dict[str, Any]) -> str:
    if activity.get("external_id"):
        return activity["external_id"]

    raw = f"{activity.get('source_name')}|{activity.get('local_date')}|{activity.get('name')}|{json.dumps(_hashable_exercises(activity.get('exercises', [])), default=str, ensure_ascii=False)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def row_hash(activity_external_id: str, exercise: dict[str, Any]) -> str:
    raw = f"{activity_external_id}|{exercise.get('sequence')}|{exercise.get('original_name')}|{json.dumps(_hashable_sets(exercise.get('sets', [])), default=str, ensure_ascii=False)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_canonical_exercise_id(exercise_id: int | None, cur: Any = None) -> int | None:
    """Resolve QUALQUER id antigo de exercicio (inclusive um dos 14 ids
    absorvidos pela consolidacao do catalogo canonico) para o id ATIVO
    correspondente em `exercise_catalog`, via `exercise_legacy_id_map`
    (93 linhas, cobre TODO o espaco de ids antigo - para ids que nunca
    mudaram, old_exercise_id == canonical_exercise_id, entao a resolucao e
    um no-op nesses casos).

    Fonte de verdade unica (2026-08): nenhum motor deve mais gravar ou
    comparar um id absorvido de `exercises.mapping_id`/
    `workout_plan_exercises.mapping_id` - sempre o canonico.

    Aceita um cursor ja aberto (`cur`) para reaproveitar a transacao do
    caller (evita abrir uma conexao extra em loops de insercao); sem ele,
    abre uma transacao dedicada e curta."""
    if exercise_id is None:
        return None

    if cur is not None:
        cur.execute(
            "SELECT canonical_exercise_id FROM exercise_legacy_id_map WHERE old_exercise_id = %s",
            (exercise_id,),
        )
        row = cur.fetchone()
        return row["canonical_exercise_id"] if row else exercise_id

    with transaction() as conn:
        with conn.cursor() as inner_cur:
            inner_cur.execute(
                "SELECT canonical_exercise_id FROM exercise_legacy_id_map WHERE old_exercise_id = %s",
                (exercise_id,),
            )
            row = inner_cur.fetchone()
    return row["canonical_exercise_id"] if row else exercise_id


def resolve_mapping_id(
    original_name: Any,
    mapped_name_hint: Any = None,
    explicit_mapping_id: Any = None,
    cur: Any = None,
) -> int | None:
    """Resolve exercises.mapping_id (FK -> exercise_catalog.id, fonte de
    verdade canonica pos-reestruturacao).

    Prioriza um mapping_id explícito enviado pelo cliente (frontend já
    resolvido). Na ausência dele, tenta casar original_name/mapped_name_hint
    contra o catálogo (via match_mapping, que resolve contra exercise_catalog
    + exercise_aliases - compatibilidade com payloads legados do MCP/OpenClaw
    que ainda enviam mapped_name/muscle_*).

    Em qualquer um dos dois caminhos, o id resultante passa por
    `_resolve_canonical_exercise_id` antes de retornar: garante que nunca
    devolve um id absorvido pela consolidacao do catálogo (ex.: um alias que
    hoje aponta para um id antigo que foi fundido em outro canônico).
    """
    if explicit_mapping_id:
        return _resolve_canonical_exercise_id(int(explicit_mapping_id), cur)

    try:
        match = match_mapping(original_name, mapped_name_hint)
    except Exception:
        return None

    if not match or not match.get("id"):
        return None

    return _resolve_canonical_exercise_id(match["id"], cur)


def resolve_gym_id(cur, gym_id_explicit: Any = None, gym_name_hint: Any = None) -> int | None:
    """Resolve activities.gym_id (FK -> gyms.id) a partir de id explicito ou nome."""
    if gym_id_explicit:
        return int(gym_id_explicit)

    if not gym_name_hint:
        return None

    cur.execute(
        "SELECT id FROM gyms WHERE is_active = 1 AND LOWER(name) = LOWER(%s) LIMIT 1",
        (gym_name_hint,),
    )
    row = cur.fetchone()
    if row:
        return row["id"]

    cur.execute(
        "SELECT id FROM gyms WHERE is_active = 1 AND LOWER(name) LIKE LOWER(%s) LIMIT 1",
        (f"%{gym_name_hint}%",),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def resolve_equipment_id(
    cur,
    mapping_id: Any,
    gym_id: Any,
    equipment_id_explicit: Any = None,
    equipment_name_hint: Any = None,
    user_id: int | None = None,
) -> int | None:
    """Resolve exercises.equipment_id (FK -> gym_equipment.id) para o
    exercicio `mapping_id` na academia `gym_id`.

    Prioriza equipment_id explicito. Depois tenta casar equipment_name_hint
    entre os equipamentos que a academia tem para ESSE exercicio. Sem hint,
    escolhe automaticamente pelo ranking de `user_id` (o MESMO de
    GET /gym-equipment/recommend: estrelas, depois o menos usado recentemente
    POR ELE, nome, id), garantindo variacao entre sessoes. Sem `user_id` nao
    ha estrelas nem recencia: decide por nome e id.
    Sem gym_id ou mapping_id conhecidos - ou sem NENHUM equipamento daquele
    exercicio na academia - retorna None.

    **O candidato e sempre do MESMO `exercise_id` (D13).** Ate a correcao do
    bug da ficha 112 os candidatos eram "equipamentos ativos do GRUPO MUSCULAR
    primario do exercicio na academia", e essa era a ultima morada da
    substituicao por grupo que a D13 aboliu: para um item do Modo B que a D13
    marcou como `requires_user_choice` (`preferred_equipment_id` vazio), esta
    funcao "ajudava" devolvendo o melhor equipamento do grupo - e a ficha saia
    com "Remada curvada no orbital row" na Estacao de polia. Um equipamento de
    outro exercicio nunca e uma resposta melhor que None: None e honesto e a
    D13 caminho (b) sabe o que fazer com ele (`requires_user_choice`).
    """
    if equipment_id_explicit:
        return int(equipment_id_explicit)

    if not gym_id or not mapping_id:
        return None

    # `mapping_id` recebido pode ser um id antigo absorvido pela consolidacao
    # do catalogo canonico - resolve para o id ATIVO antes de qualquer JOIN.
    canonical_mapping_id = _resolve_canonical_exercise_id(int(mapping_id), cur)
    if not canonical_mapping_id:
        return None

    # gym_equipment.mapping_id (cache materializado, fora de escopo desta
    # correcao - ver CLAUDE.md) ainda guarda o id historico vindo de
    # equipment_exercise_mappings/exercise_zepp_mappings.id; como o espaco de
    # ids e o mesmo de exercise_catalog, resolve via exercise_legacy_id_map
    # antes da comparacao para tambem cobrir um ge.mapping_id que aponte para
    # um id absorvido. Mesma regra de `is_gym_equipment_pair_active` e de
    # `generation/mode_b._gym_equipment_for_exercise`.
    same_exercise = """
        FROM gym_equipment ge
        LEFT JOIN exercise_legacy_id_map ge_elm ON ge_elm.old_exercise_id = ge.mapping_id
        JOIN equipment eq ON eq.id = ge.equipment_id
        WHERE ge.gym_id = %s
          AND COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id) = %s
          AND ge.is_active = 1
    """

    if equipment_name_hint:
        matches = (
            ("= LOWER(%s)", str(equipment_name_hint)),
            ("LIKE LOWER(%s)", f"%{equipment_name_hint}%"),
        )
        for operator, value in matches:
            cur.execute(
                f"SELECT ge.id {same_exercise} AND LOWER(eq.name) {operator} LIMIT 1",
                (gym_id, canonical_mapping_id, value),
            )
            row = cur.fetchone()
            if row:
                return row["id"]

    # Sem hint (ou hint que nao casou): MESMO ranking por usuario do
    # recomendador (PR-08 - estrelas, recencia DO USUARIO, nome, id), aplicado
    # por `equipment_recommender.rank_gym_equipment_rows`.
    cur.execute(
        f"SELECT ge.id, ge.mapping_id, ge.equipment_id, eq.name AS equipment_name {same_exercise}",
        (gym_id, canonical_mapping_id),
    )
    # Todos os candidatos sao do MESMO exercicio: o par que da a nota
    # (`equipment_exercises`) e (canonical_mapping_id, equipment_id).
    rows = [{**row, "canonical_exercise_id": canonical_mapping_id} for row in cur.fetchall() or []]
    ranked = rank_gym_equipment_rows(cur, rows, gym_id=int(gym_id), user_id=user_id)
    return ranked[0]["id"] if ranked else None


def is_gym_equipment_pair_active(gym_id: int, mapping_id: int, equipment_id: int) -> bool:
    """Confere se o par (mapping_id, equipment_id) esta ATIVO na academia
    `gym_id` no cache materializado `gym_equipment` - mesma query hoje inline
    no upsert de `POST /gym-equipment` (`gyms.py::create_gym_equipment`), aqui
    extraida como consulta reaproveitavel (so leitura, sem upsert) para o
    motor de geracao do modo `equipment_series`
    (`generation/mode_b.py`).

    Adaptacao ao catalogo canonico pos-reestruturacao (mesma regra ja aplicada
    por `resolve_equipment_id` acima, e ausente na versao on-premise desta
    funcao): tanto o `mapping_id` recebido quanto o `ge.mapping_id` gravado no
    cache podem ser ids antigos absorvidos pela consolidacao do catalogo, entao
    os dois lados sao resolvidos via `exercise_legacy_id_map` antes da
    comparacao - sem isso, um par legitimamente ativo seria lido como inativo e
    o motor cairia no fallback de substituicao de equipamento sem necessidade.
    """
    if not gym_id or not mapping_id or not equipment_id:
        return False

    with transaction() as conn:
        with conn.cursor() as cur:
            canonical_mapping_id = _resolve_canonical_exercise_id(int(mapping_id), cur)
            cur.execute(
                """
                SELECT ge.id
                FROM gym_equipment ge
                LEFT JOIN exercise_legacy_id_map ge_elm ON ge_elm.old_exercise_id = ge.mapping_id
                WHERE ge.gym_id = %s
                  AND COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id) = %s
                  AND ge.equipment_id = %s
                  AND ge.is_active = 1
                LIMIT 1
                """,
                (int(gym_id), int(canonical_mapping_id), int(equipment_id)),
            )
            return cur.fetchone() is not None


def upsert_activity(activity: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    """`user_id` (Multiusuario Parte 4): dono do treino. Sempre resolvido a
    partir de `identity.user_id` no router (main.py) - nunca aceito do
    payload do cliente. `None` preserva o comportamento legado (consumidores
    internos que ainda nao propagam identidade)."""
    with transaction() as conn:
        sid = source_id(conn, activity.get("source_name", "openclaw"), "api")
        ext_id = make_external_id(activity)
        payload_hash = hashlib.sha256(json.dumps(activity, default=str, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO imports (source_id, import_type, external_id, status, payload_hash, raw_payload)
                VALUES (%s, 'activity', %s, 'success', %s, CAST(%s AS JSON))
                ON DUPLICATE KEY UPDATE
                  status='success',
                  payload_hash=VALUES(payload_hash),
                  raw_payload=VALUES(raw_payload),
                  imported_at=CURRENT_TIMESTAMP
                """,
                (sid, ext_id, payload_hash, to_json(activity)),
            )
            cur.execute(
                "SELECT id FROM imports WHERE source_id=%s AND external_id=%s AND import_type='activity'",
                (sid, ext_id),
            )
            import_id = cur.fetchone()["id"]

            if user_id is not None:
                cur.execute(
                    "SELECT id FROM activities WHERE source_id=%s AND external_id=%s AND user_id=%s",
                    (sid, ext_id, user_id),
                )
            else:
                cur.execute("SELECT id FROM activities WHERE source_id=%s AND external_id=%s", (sid, ext_id))
            existing = cur.fetchone()
            if existing:
                cur.execute("DELETE FROM activities WHERE id=%s", (existing["id"],))

            exercise_rpes = []
            for ex in activity.get("exercises", []):
                stats = compute_exercise_stats(ex.get("sets", []))
                if stats["avg_rpe"] is not None:
                    exercise_rpes.append(stats["avg_rpe"])

            avg_rpe = (sum(exercise_rpes) / Decimal(len(exercise_rpes))) if exercise_rpes else None
            gym_id = resolve_gym_id(cur, activity.get("gym_id"), activity.get("gym_name"))
            training_type_id = activity.get("training_type_id")

            cur.execute(
                """
                INSERT INTO activities (
                  source_id, import_id, external_id, user_id, gym_id, training_type_id, local_date, started_at, ended_at,
                  activity_type, name, description, duration_seconds, calories_kcal,
                  avg_hr_bpm, max_hr_bpm, avg_rpe, notes, conducted_by, raw_payload
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CAST(%s AS JSON))
                """,
                (
                    sid,
                    import_id,
                    ext_id,
                    user_id,
                    gym_id,
                    training_type_id,
                    activity["local_date"],
                    activity.get("started_at"),
                    activity.get("ended_at"),
                    activity.get("activity_type", "musculacao"),
                    activity["name"],
                    activity.get("description"),
                    activity.get("duration_seconds"),
                    activity.get("calories_kcal"),
                    activity.get("avg_hr_bpm"),
                    activity.get("max_hr_bpm"),
                    avg_rpe,
                    activity.get("notes"),
                    activity.get("conducted_by"),
                    to_json(activity.get("raw_payload") or activity),
                ),
            )
            activity_id = cur.lastrowid

            # COD-12: zonas e series vao em lote (`executemany`: um
            # BatchExecuteStatement na Data API, INSERT multi-linha no PyMySQL),
            # na MESMA transacao e na mesma ordem de antes. So `exercises`
            # segue linha a linha, porque cada serie precisa do `lastrowid`.
            cur.executemany(
                """
                INSERT INTO activity_hr_zones (
                  activity_id, zone_number, zone_name, min_bpm, max_bpm, duration_seconds, percent_time
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        activity_id,
                        zone["zone_number"],
                        zone.get("zone_name"),
                        zone.get("min_bpm"),
                        zone.get("max_bpm"),
                        zone.get("duration_seconds"),
                        zone.get("percent_time"),
                    )
                    for zone in activity.get("hr_zones", [])
                ],
            )

            inserted_exercises = 0
            set_rows: list[tuple[Any, ...]] = []

            for ex in activity.get("exercises", []):
                # M102: com lados, `reps`/`rpe` oficiais = pior lado (dono
                # `set_sides`), antes dos agregados; sem lados nada muda.
                set_dicts = [apply_canonical(dict(st)) for st in ex.get("sets", [])]
                stats = compute_exercise_stats(set_dicts)
                rhash = row_hash(ext_id, ex)
                mapping_id = resolve_mapping_id(ex["original_name"], ex.get("mapped_name"), ex.get("mapping_id"), cur=cur)
                equipment_id = resolve_equipment_id(
                    cur, mapping_id, gym_id, ex.get("equipment_id"), ex.get("equipment_name"), user_id=user_id
                )

                cur.execute(
                    """
                    INSERT INTO exercises (
                      activity_id, sequence, original_name, mapping_id, equipment_id,
                      sets_count, reps_total, volume_kg, min_weight_kg, best_weight_kg, avg_rpe,
                      notes, muscle_pain, joint_pain, source_row_hash
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        activity_id,
                        ex["sequence"],
                        ex["original_name"],
                        mapping_id,
                        equipment_id,
                        stats["sets_count"],
                        stats["reps_total"],
                        stats["volume_kg"],
                        stats["min_weight_kg"],
                        stats["best_weight_kg"],
                        stats["avg_rpe"],
                        ex.get("notes"),
                        ex.get("muscle_pain"),
                        ex.get("joint_pain"),
                        rhash,
                    ),
                )
                exercise_id = cur.lastrowid
                inserted_exercises += 1

                for st in set_dicts:
                    set_type = normalize_set_type(st.get("set_type"))
                    # Fase 5: aplicação de técnica ganha o vínculo derivado (a
                    # importação não traz ficha nem âncora explícita).
                    technique_code = technique_accounting.derive_technique_code(set_type)
                    anchor_set_number = (
                        technique_accounting.derive_anchor_set_number(
                            st["set_number"], ((o["set_number"], o.get("set_type")) for o in set_dicts)
                        )
                        if technique_code
                        else None
                    )
                    # Lote T3: forma validada no `ExerciseSetIn`; ausente = reps.
                    measure, duration_seconds = set_measure_columns(st)
                    set_rows.append(
                        (
                            exercise_id,
                            st["set_number"],
                            set_type,
                            st.get("reps"),
                            st.get("weight_kg"),
                            st.get("rpe"),
                            st.get("rest_seconds"),
                            st.get("hr_avg_bpm"),
                            st.get("hr_max_bpm"),
                            st.get("notes"),
                            technique_code,
                            anchor_set_number,
                            measure,
                            duration_seconds,
                            # M102: lados (o canônico já veio do `ExerciseSetIn`).
                            *(st.get(field) for field in SIDE_FIELDS),
                        )
                    )

            # Mesma ordem do laco (exercicio a exercicio, serie a serie): os
            # ids de `exercise_sets` saem na mesma sequencia de antes.
            cur.executemany(
                """
                INSERT INTO exercise_sets (
                  exercise_id, set_number, set_type, reps, weight_kg, rpe, rest_seconds,
                  hr_avg_bpm, hr_max_bpm, notes, technique_code, anchor_set_number,
                  measure, duration_seconds, reps_left, reps_right, rpe_left, rpe_right
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                set_rows,
            )

            return {
                "activity_id": activity_id,
                "external_id": ext_id,
                "exercises": inserted_exercises,
                "sets": len(set_rows),
            }


def update_activity(activity_id: int, activity: dict[str, Any], user_id: int | None = None) -> dict[str, Any] | None:
    """`user_id` (Multiusuario Parte 4): quando informado, so atualiza uma
    activity que pertenca a esse usuario (retorna None caso contrario, para
    o caller devolver 404 sem vazar existencia do recurso)."""
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT source_id, external_id, user_id FROM activities WHERE id=%s AND user_id=%s",
                    (activity_id, user_id),
                )
            else:
                cur.execute("SELECT source_id, external_id, user_id FROM activities WHERE id=%s", (activity_id,))
            existing = cur.fetchone()

            if not existing:
                return None

            if not activity.get("external_id"):
                activity["external_id"] = existing["external_id"]

            if not activity.get("source_name"):
                cur.execute("SELECT name FROM sources WHERE id=%s", (existing["source_id"],))
                source = cur.fetchone()
                activity["source_name"] = (source or {}).get("name") or "frontend"

    return upsert_activity(activity, user_id=user_id if user_id is not None else existing.get("user_id"))


def delete_activity(activity_id: int, user_id: int | None = None) -> bool:
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute("SELECT id FROM activities WHERE id=%s AND user_id=%s", (activity_id, user_id))
            else:
                cur.execute("SELECT id FROM activities WHERE id=%s", (activity_id,))
            existing = cur.fetchone()
            if not existing:
                return False

            cur.execute(
                "DELETE s FROM exercise_sets s JOIN exercises e ON e.id = s.exercise_id WHERE e.activity_id = %s",
                (activity_id,),
            )
            cur.execute("DELETE FROM exercises WHERE activity_id=%s", (activity_id,))
            cur.execute("DELETE FROM activity_hr_zones WHERE activity_id=%s", (activity_id,))
            cur.execute("DELETE FROM activities WHERE id=%s", (activity_id,))

            return cur.rowcount > 0


def latest_activities(
    limit: int = 10,
    offset: int = 0,
    date_from: Any = None,
    date_to: Any = None,
    gym_id: int | None = None,
    conducted_by: str | None = None,
    q: str | None = None,
    session_status: str | None = None,
    user_id: int | None = None,
    *,
    only_mapping_id: int | None = None,
) -> list[dict[str, Any]]:
    """Registros de exercicio (mais recente primeiro) com as series.

    `only_mapping_id` (PERF-04): devolve so as linhas desse exercicio
    (`mapping_id` ja canonico) DENTRO da mesma janela de linhas que a chamada
    sem o filtro leria (mesmo `WHERE`, mesma ordem, mesmo `LIMIT`) e le as
    series so delas. Para quem so procura a ultima execucao de UM exercicio
    (`prescription_service.last_execution_from_logs`, que ja descarta as outras
    linhas), o resultado e identico e a leitura das series cai de
    ceil(linhas/400) statements - com o payload de TODO o historico - para um.
    """
    with transaction() as conn:
        with conn.cursor() as cur:
            where = []
            params: list[Any] = []

            if user_id is not None:
                where.append("a.user_id = %s")
                params.append(user_id)
            if date_from is not None:
                where.append("a.local_date >= %s")
                params.append(date_from)
            if date_to is not None:
                where.append("a.local_date <= %s")
                params.append(date_to)
            if gym_id is not None:
                where.append("a.gym_id = %s")
                params.append(gym_id)
            if conducted_by is not None:
                where.append("a.conducted_by = %s")
                params.append(conducted_by)
            if q:
                where.append("a.name LIKE %s")
                params.append(f"%{q}%")
            if session_status is not None:
                where.append("a.session_status = %s")
                params.append(session_status)

            where_clause = f"WHERE {' AND '.join(where)}" if where else ""

            cur.execute(
                f"""
                SELECT a.id, a.local_date, a.activity_type, a.name, a.duration_seconds, a.calories_kcal,
                       a.avg_hr_bpm, a.max_hr_bpm, a.avg_rpe, a.notes,
                       a.gym_id, g.name AS gym_name, a.conducted_by,
                       a.training_type_id, tt.name AS training_type_name,
                       a.session_status, a.workout_plan_id, wp.name AS workout_plan_name,
                       COALESCE(vol.total_volume_kg, 0) AS total_volume_kg
                FROM activities a
                LEFT JOIN gyms g ON g.id = a.gym_id
                LEFT JOIN training_types tt ON tt.id = a.training_type_id
                LEFT JOIN workout_plans wp ON wp.id = a.workout_plan_id
                LEFT JOIN (
                    SELECT activity_id, SUM(volume_kg) AS total_volume_kg
                    FROM exercises
                    GROUP BY activity_id
                ) vol ON vol.activity_id = a.id
                {where_clause}
                ORDER BY a.local_date DESC, a.id DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            )
            return cur.fetchall()


def _with_canonical_set_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Series devolvidas pela API com `set_type` CANONICO (M76 Lote 3b):
    NULL/vazio = `working`, apelidos legados (`dropset`, `warm-up`...)
    resolvidos por `set_types.normalize_set_type`. O FE nao precisa conhecer
    as grafias antigas."""
    for row in rows:
        row["set_type"] = normalize_set_type(row.get("set_type"))
    return rows


def activity_detail(
    activity_id: int, user_id: int | None = None, *, include_pending: bool = False
) -> dict[str, Any] | None:
    """Detalhe da atividade com as séries de cada exercício.

    `include_pending` (Modo Intensivo, 092): a TELA (`GET /activities/{id}`)
    mostra também as séries com reps pendentes (`reps_pending` vem no
    `SELECT *`) para a retomada e a revisão; o contexto da IA (padrão) não as
    vê até a confirmação (`set_types.sql_set_confirmed`)."""
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """
                    SELECT a.*, g.name AS gym_name, tt.name AS training_type_name, wp.name AS workout_plan_name,
                           COALESCE(vol.total_volume_kg, 0) AS total_volume_kg,
                           ail.calories_kcal AS intervals_calories_kcal
                    FROM activities a
                    LEFT JOIN activity_intervals_link ail ON ail.activity_id = a.id
                    LEFT JOIN gyms g ON g.id = a.gym_id
                    LEFT JOIN training_types tt ON tt.id = a.training_type_id
                    LEFT JOIN workout_plans wp ON wp.id = a.workout_plan_id
                    LEFT JOIN (
                        SELECT activity_id, SUM(volume_kg) AS total_volume_kg
                        FROM exercises
                        WHERE activity_id = %s
                        GROUP BY activity_id
                    ) vol ON vol.activity_id = a.id
                    WHERE a.id=%s AND a.user_id=%s
                    """,
                    (activity_id, activity_id, user_id),
                )
            else:
                cur.execute(
                    """
                    SELECT a.*, g.name AS gym_name, tt.name AS training_type_name, wp.name AS workout_plan_name,
                           COALESCE(vol.total_volume_kg, 0) AS total_volume_kg,
                           ail.calories_kcal AS intervals_calories_kcal
                    FROM activities a
                    LEFT JOIN activity_intervals_link ail ON ail.activity_id = a.id
                    LEFT JOIN gyms g ON g.id = a.gym_id
                    LEFT JOIN training_types tt ON tt.id = a.training_type_id
                    LEFT JOIN workout_plans wp ON wp.id = a.workout_plan_id
                    LEFT JOIN (
                        SELECT activity_id, SUM(volume_kg) AS total_volume_kg
                        FROM exercises
                        WHERE activity_id = %s
                        GROUP BY activity_id
                    ) vol ON vol.activity_id = a.id
                    WHERE a.id=%s
                    """,
                    (activity_id, activity_id),
                )
            activity = cur.fetchone()
            if not activity:
                return None

            # Nutrição L0: origem da kcal resolvida pelo dono (`domain/calories_source`):
            # a coluna `calories_source` (076) manda; senão `intervals` quando a kcal
            # é a do link do sync; senão desconhecida. A tela só exibe o rótulo.
            intervals_kcal = activity.pop("intervals_calories_kcal", None)
            activity["calories_source"] = resolve_calories_source(
                calories_kcal=activity.get("calories_kcal"),
                stored_source=activity.get("calories_source"),
                intervals_calories_kcal=intervals_kcal,
            )
            activity["calories_source_label"] = calories_source_label(activity["calories_source"])

            cur.execute(
                """
                SELECT e.*,
                       (
                           SELECT exm.external_name
                           FROM exercise_external_mappings exm
                           WHERE exm.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                             AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                           ORDER BY exm.id ASC LIMIT 1
                       ) AS mapped_name,
                       ec.name AS exercise_name,
                       ec.description AS exercise_description,
                       pg.name AS muscle_primary,
                       sg.name AS muscle_secondary,
                       eq.id AS equipment_catalog_id,
                       eq.name AS equipment_name,
                       (eq.photo_path IS NOT NULL) AS equipment_has_photo,
                       (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://app.musclefit.com.br/', eq.photo_path) ELSE NULL END) AS equipment_photo_url,
                       wpe.mapping_id AS planned_mapping_id,
                       wpe.exercise_name AS planned_exercise_name
                FROM exercises e
                LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
                LEFT JOIN exercise_catalog ec ON ec.id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
                LEFT JOIN muscle_groups pg ON pg.id = (
                    SELECT emc_pri.muscle_group_id
                    FROM exercise_muscle_contributions emc_pri
                    WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                      AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
                    ORDER BY emc_pri.id ASC LIMIT 1
                )
                LEFT JOIN muscle_groups sg ON sg.id = (
                    SELECT emc_sec.muscle_group_id
                    FROM exercise_muscle_contributions emc_sec
                    WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                      AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
                    ORDER BY emc_sec.id ASC LIMIT 1
                )
                LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
                LEFT JOIN equipment eq ON eq.id = ge.equipment_id
                WHERE e.activity_id=%s
                ORDER BY e.sequence
                """,
                (activity_id,),
            )
            exercises = cur.fetchall()

            # Uma query para todas as series (antes: 1 SELECT por exercicio,
            # 2+N statements por chamada - PERF-03). `ORDER BY exercise_id,
            # set_number` preserva a ordem das series de cada exercicio.
            sets_by_exercise: dict[Any, list[dict[str, Any]]] = {ex["id"]: [] for ex in exercises}
            if exercises:
                placeholders = ",".join(["%s"] * len(exercises))
                pending_filter = "" if include_pending else f"AND {sql_set_confirmed('reps_pending')} "
                cur.execute(
                    f"SELECT * FROM exercise_sets WHERE exercise_id IN ({placeholders}) {pending_filter}"
                    "ORDER BY exercise_id, set_number",
                    tuple(ex["id"] for ex in exercises),
                )
                for set_row in cur.fetchall():
                    # Lote T3: `measure_text` pronto do dono ("58 s").
                    # M102: `sides_text` pronto do dono ("reps E 10 · D 8").
                    sets_by_exercise[set_row["exercise_id"]].append(attach_sides_text(_attach_set_measure(set_row)))
            for ex in exercises:
                ex["sets"] = _with_canonical_set_type(sets_by_exercise[ex["id"]])

            activity["exercises"] = exercises
            return activity


def exercise_summary_context(exercise_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    """Busca o contexto de UM exercício (nome, alvo da ficha quando vinculado,
    séries executadas, dor/anotação e, desde o M97 F5, o objetivo carimbado na
    linha (`exercises.training_goal`, 093) e a origem do deload da sessão
    (`activities.deload_source`, 094)) para montar o prompt de
    exercise_summary_ai.summarize_exercise, usado pelo endpoint
    POST /exercises/{exercise_id}/summarize. Retorna None quando o exercício
    não existe ou não pertence a `user_id` (o endpoint traduz isso em 404,
    nunca 403 — mesmo padrão do resto do backend pós-migração multiusuário)."""
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """
                    SELECT e.id, e.original_name, e.notes, e.muscle_pain, e.joint_pain,
                           e.training_goal, a.deload_source,
                           wpe.reps_target, wpe.load_target, wpe.rpe_target
                    FROM exercises e
                    JOIN activities a ON a.id = e.activity_id
                    LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
                    WHERE e.id = %s AND a.user_id = %s
                    """,
                    (exercise_id, user_id),
                )
            else:
                cur.execute(
                    """
                    SELECT e.id, e.original_name, e.notes, e.muscle_pain, e.joint_pain,
                           e.training_goal, a.deload_source,
                           wpe.reps_target, wpe.load_target, wpe.rpe_target
                    FROM exercises e
                    JOIN activities a ON a.id = e.activity_id
                    LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
                    WHERE e.id = %s
                    """,
                    (exercise_id,),
                )
            exercise = cur.fetchone()
            if not exercise:
                return None

            cur.execute(
                f"""
                SELECT set_number, set_type, reps, weight_kg, rpe, muscle_pain, joint_pain, notes,
                       technique_code, anchor_set_number, reps_left, reps_right, rpe_left, rpe_right
                FROM exercise_sets WHERE exercise_id = %s AND {sql_set_confirmed('reps_pending')} ORDER BY set_number
                """,
                (exercise_id,),
            )
            # M95b: `technique_code`/`anchor_set_number` (059) rotulam a serie de
            # tecnica pelo nome do catalogo (`set_labels.set_display_label`).
            exercise["sets"] = _with_canonical_set_type(cur.fetchall())
            return exercise


def save_activity_ai_summary(
    activity_id: int,
    summary: str,
    user_id: int | None = None,
    input_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Persiste o resumo de fim de treino gerado por workout_summary_ai em
    activities.ai_summary/ai_summary_generated_at (UPDATE ... WHERE id=%s AND
    user_id=%s, mesmo padrão de isolamento multiusuário do resto do backend).
    Retorna None quando a activity não existe ou não pertence a `user_id`,
    para o endpoint traduzir em 404.

    `input_sha256` (IA-14, migração 070): impressão digital da entrada que
    gerou este resumo (`workout_summary_ai.input_fingerprint`), gravada junto
    para a próxima geração com a mesma entrada reaproveitar o resumo."""
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "UPDATE activities SET ai_summary = %s, ai_summary_generated_at = NOW(), "
                    "ai_summary_input_sha256 = %s WHERE id = %s AND user_id = %s",
                    (summary, input_sha256, activity_id, user_id),
                )
            else:
                cur.execute(
                    "UPDATE activities SET ai_summary = %s, ai_summary_generated_at = NOW(), "
                    "ai_summary_input_sha256 = %s WHERE id = %s",
                    (summary, input_sha256, activity_id),
                )
            if cur.rowcount == 0:
                return None

            cur.execute(
                "SELECT ai_summary, ai_summary_generated_at FROM activities WHERE id = %s",
                (activity_id,),
            )
            return cur.fetchone()


def list_exercise_logs(
    limit: int = 100,
    date_from: Any = None,
    date_to: Any = None,
    user_id: int | None = None,
    *,
    only_mapping_id: int | None = None,
    include_pending: bool = False,
) -> list[dict[str, Any]]:
    """Registros de exercicio (mais recente primeiro) com as series.

    `only_mapping_id` (PERF-04): devolve so as linhas desse exercicio
    (`mapping_id` ja canonico) DENTRO da mesma janela de linhas que a chamada
    sem o filtro leria (mesmo `WHERE`, mesma ordem, mesmo `LIMIT`) e le as
    series so delas. Para quem so procura a ultima execucao de UM exercicio
    (`prescription_service.last_execution_from_logs`, que ja descarta as outras
    linhas), o resultado e identico e a leitura das series cai de
    ceil(linhas/400) statements - com o payload de TODO o historico - para um.
    """
    with transaction() as conn:
        with conn.cursor() as cur:
            where = []
            params: list[Any] = []

            if user_id is not None:
                where.append("a.user_id = %s")
                params.append(user_id)
            if date_from is not None:
                where.append("a.local_date >= %s")
                params.append(date_from)
            if date_to is not None:
                where.append("a.local_date <= %s")
                params.append(date_to)

            where_clause = f"WHERE {' AND '.join(where)}" if where else ""
            # Quando um range de datas é informado, o range já delimita o
            # volume de forma mais precisa que um LIMIT arbitrário — usamos
            # um teto alto em vez do `limit` (pensado para o caso sem filtro
            # de data, tipicamente usado por MCP/consumidores legados).
            effective_limit = 2000 if where else limit

            cur.execute(
                f"""
                SELECT
                    e.id AS exercise_id,
                    a.id AS activity_id,
                    a.external_id,
                    a.local_date,
                    a.started_at,
                    a.ended_at,
                    a.gym_id,
                    g.name AS gym_name,
                    a.conducted_by,
                    e.original_name,
                    -- Resolve mapping_id historico (exercises.mapping_id pode ser um id
                    -- absorvido pela normalizacao de exercicios canonicos) para o id
                    -- canonico atual via exercise_legacy_id_map, com fallback para o
                    -- proprio valor quando nao ha entrada no mapa (nao deveria ocorrer).
                    -- Ver CLAUDE.md > Regras de Seguranca para Evolucao, item 2026-08.
                    CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
                    (
                        SELECT exm.external_name
                        FROM exercise_external_mappings exm
                        WHERE exm.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                          AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                        ORDER BY exm.id ASC LIMIT 1
                    ) AS mapped_name,
                    pg.name AS muscle_primary,
                    sg.name AS muscle_secondary,
                    e.equipment_id,
                    eq.id AS equipment_catalog_id,
                    eq.name AS equipment_name,
                    (eq.photo_path IS NOT NULL) AS equipment_has_photo,
                    (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://app.musclefit.com.br/', eq.photo_path) ELSE NULL END) AS equipment_photo_url,
                    e.muscle_pain,
                    e.joint_pain,
                    e.sequence
                FROM exercises e
                JOIN activities a ON a.id = e.activity_id
                LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
                LEFT JOIN muscle_groups pg ON pg.id = (
                    SELECT emc_pri.muscle_group_id
                    FROM exercise_muscle_contributions emc_pri
                    WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                      AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
                    ORDER BY emc_pri.id ASC LIMIT 1
                )
                LEFT JOIN muscle_groups sg ON sg.id = (
                    SELECT emc_sec.muscle_group_id
                    FROM exercise_muscle_contributions emc_sec
                    WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                      AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
                    ORDER BY emc_sec.id ASC LIMIT 1
                )
                LEFT JOIN gyms g ON g.id = a.gym_id
                LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
                LEFT JOIN equipment eq ON eq.id = ge.equipment_id
                {where_clause}
                ORDER BY a.local_date DESC, a.id DESC, e.sequence ASC
                LIMIT %s
                """,
                (*params, effective_limit),
            )
            rows = cur.fetchall()
            if only_mapping_id is not None:
                rows = [
                    row
                    for row in rows
                    if row.get("mapping_id") is not None and int(row["mapping_id"]) == int(only_mapping_id)
                ]

            # Series de TODOS os exercicios em paginas, em vez de uma query
            # por exercicio. Com `user_id` informado, `effective_limit` e
            # 2000: o laco anterior disparava ate 2000 SELECTs por chamada -
            # custo invisivel no DEV local e dominante em producao, onde cada
            # query e uma chamada HTTP a RDS Data API (medido: 5,1s no
            # PyMySQL e 10,3s na Data API, so aqui, num historico de 4.000
            # exercicios). O resultado por linha e identico: mesmas colunas,
            # mesma ordem por `set_number` e mesma normalizacao de
            # `set_type`.
            sets_by_exercise: dict[Any, list[dict[str, Any]]] = {}
            exercise_ids = [row["exercise_id"] for row in rows]
            page_size = 400
            for start in range(0, len(exercise_ids), page_size):
                page = exercise_ids[start : start + page_size]
                placeholders = ",".join(["%s"] * len(page))
                # Modo Intensivo (092): só a TELA (`GET /exercises/logs`) pede as
                # séries com reps pendentes (para mostrar/editar); os
                # consumidores analíticos (progressão, IA) não as veem.
                pending_filter = "" if include_pending else f"AND {sql_set_confirmed('reps_pending')}"
                cur.execute(
                    f"""
                    SELECT exercise_id, set_number, set_type, reps, weight_kg, rpe, measure, duration_seconds,
                           reps_pending, reps_left, reps_right, rpe_left, rpe_right
                    FROM exercise_sets
                    WHERE exercise_id IN ({placeholders}) {pending_filter}
                    ORDER BY exercise_id ASC, set_number ASC
                    """,
                    tuple(page),
                )
                for set_row in cur.fetchall():
                    exercise_id = set_row.pop("exercise_id")
                    # Normalizacao na leitura (legado `dropset`/NULL), para os
                    # consumidores (progression_service) filtrarem por set_type.
                    set_row["set_type"] = normalize_set_type(set_row.get("set_type"))
                    # M102: lados da serie unilateral, texto pronto do dono.
                    sets_by_exercise.setdefault(exercise_id, []).append(attach_sides_text(set_row))

            for row in rows:
                row["sets"] = sets_by_exercise.get(row["exercise_id"], [])

            return rows


def _recompute_activity_avg_rpe(cur, activity_id: int) -> None:
    cur.execute(
        """
        SELECT ROUND(AVG(avg_rpe), 2) AS avg_rpe
        FROM exercises
        WHERE activity_id=%s AND avg_rpe IS NOT NULL
        """,
        (activity_id,),
    )
    avg_row = cur.fetchone()
    cur.execute(
        "UPDATE activities SET avg_rpe=%s WHERE id=%s",
        ((avg_row or {}).get("avg_rpe"), activity_id),
    )


class WorkoutPlanExerciseNotFoundError(LookupError):
    """workout_plan_exercise_id inexistente ou de ficha de outro usuario (SEG-09)."""


def update_exercise_log(exercise_id: int, payload: dict[str, Any], user_id: int | None = None) -> dict[str, Any] | None:
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT e.id, e.activity_id, e.original_name, e.mapping_id, e.workout_plan_exercise_id, "
                    "e.equipment_id, e.muscle_pain, e.joint_pain FROM exercises e "
                    "JOIN activities a ON a.id = e.activity_id WHERE e.id=%s AND a.user_id=%s",
                    (exercise_id, user_id),
                )
            else:
                cur.execute(
                    "SELECT id, activity_id, original_name, mapping_id, workout_plan_exercise_id, "
                    "equipment_id, muscle_pain, joint_pain FROM exercises WHERE id=%s",
                    (exercise_id,),
                )
            existing = cur.fetchone()
            if not existing:
                return None

            activity_id = existing["activity_id"]
            # As séries são recriadas (DELETE + INSERT) mais abaixo: lê as
            # gravadas ANTES para preservar os campos que o cliente não
            # reenviou (set_type, rest_seconds, notes, hr_*, dor) - ver
            # exercise_set_update_merge. Os agregados usam as séries já
            # mescladas, senão um aquecimento omitido contaria como trabalho.
            cur.execute(
                f"SELECT set_number, reps_pending, {', '.join((*SERVER_KEPT_SET_FIELDS, *PRESERVED_SET_FIELDS))} "
                "FROM exercise_sets WHERE exercise_id=%s",
                (exercise_id,),
            )
            sets = merge_sets_for_update(payload.get("sets") or [], cur.fetchall() or [])
            # Lote T3: a forma da medida vale para a série JÁ mesclada (medida
            # omitida preserva a gravada) -> 422 na rota.
            for st in sets:
                ensure_valid_set_measure(
                    st.get("measure"), st.get("reps"), st.get("duration_seconds"), set_number=st.get("set_number")
                )
            stats = compute_exercise_stats(sets)

            # Telegram/update payloads frequently omit mapping fields; preserve current values.
            original_name = payload.get("original_name") or existing.get("original_name")
            if payload.get("mapping_id") or payload.get("mapped_name") is not None:
                mapping_id = resolve_mapping_id(original_name, payload.get("mapped_name"), payload.get("mapping_id"), cur=cur)
            else:
                mapping_id = existing.get("mapping_id")

            gym_id = resolve_gym_id(cur, payload.get("gym_id"), payload.get("gym_name"))
            if payload.get("equipment_id") or payload.get("equipment_name") is not None:
                equipment_id = resolve_equipment_id(
                    cur, mapping_id, gym_id, payload.get("equipment_id"), payload.get("equipment_name"), user_id=user_id
                )
            else:
                equipment_id = existing.get("equipment_id")

            cur.execute(
                "UPDATE activities SET local_date=%s, gym_id=COALESCE(%s, gym_id), conducted_by=COALESCE(%s, conducted_by), started_at=COALESCE(%s, started_at) WHERE id=%s",
                (payload["local_date"], gym_id, payload.get("conducted_by"), payload.get("started_at"), activity_id),
            )

            # muscle_pain/joint_pain sao opcionais e frequentemente omitidos em
            # updates parciais (Telegram/MCP, que serializam o payload via
            # pydantic model_dump() e sempre incluem a chave com None quando
            # nao informada): preserva o valor existente quando o payload
            # trouxer None, em vez de sobrescrever com null (mesma regra ja
            # seguida para mapping/equipment acima).
            muscle_pain = payload.get("muscle_pain") if payload.get("muscle_pain") is not None else existing.get("muscle_pain")
            joint_pain = payload.get("joint_pain") if payload.get("joint_pain") is not None else existing.get("joint_pain")

            # Diferente dos campos acima, workout_plan_exercise_id precisa
            # permitir desvincular explicitamente (payload enviando null),
            # então usa a flag de presença em vez de checar apenas is not None.
            if payload.get("workout_plan_exercise_id_provided"):
                workout_plan_exercise_id = payload.get("workout_plan_exercise_id")
                # SEG-09: o vinculo so pode apontar para item de ficha do proprio
                # usuario (reenviar o vinculo ja gravado segue valido; null desvincula).
                if (
                    workout_plan_exercise_id is not None
                    and user_id is not None
                    and workout_plan_exercise_id != existing.get("workout_plan_exercise_id")
                ):
                    cur.execute(
                        "SELECT wpe.id FROM workout_plan_exercises wpe "
                        "JOIN workout_plans wp ON wp.id = wpe.plan_id "
                        "WHERE wpe.id=%s AND wp.user_id=%s",
                        (workout_plan_exercise_id, user_id),
                    )
                    if not cur.fetchone():
                        raise WorkoutPlanExerciseNotFoundError(workout_plan_exercise_id)
            else:
                workout_plan_exercise_id = existing.get("workout_plan_exercise_id")

            cur.execute(
                """
                UPDATE exercises
                SET
                    original_name=%s,
                    mapping_id=%s,
                    workout_plan_exercise_id=%s,
                    equipment_id=%s,
                    sets_count=%s,
                    reps_total=%s,
                    volume_kg=%s,
                    min_weight_kg=%s,
                    best_weight_kg=%s,
                    avg_rpe=%s,
                    muscle_pain=%s,
                    joint_pain=%s
                WHERE id=%s
                """,
                (
                    original_name,
                    mapping_id,
                    workout_plan_exercise_id,
                    equipment_id,
                    stats["sets_count"],
                    stats["reps_total"],
                    stats["volume_kg"],
                    stats["min_weight_kg"],
                    stats["best_weight_kg"],
                    stats["avg_rpe"],
                    muscle_pain,
                    joint_pain,
                    exercise_id,
                ),
            )

            cur.execute("DELETE FROM exercise_sets WHERE exercise_id=%s", (exercise_id,))

            # PR-14: este caminho RECRIA as series (delete + insert). Sem
            # recalcular a intensidade efetiva, editar um exercicio pela tela
            # /registro-exercicios APAGARIA o `rpe_target_effective` e o
            # `safety_event` gravados pelo Modo Treino. A fonte e a mesma do
            # Modo Treino (`domain/effective_intensity.py`), com o exercicio ja
            # atualizado acima - inclusive o vinculo com a ficha.
            intensity = effective_intensity.resolve_for_logged_exercise(conn, exercise_id)

            # Fase 5: aplicação de técnica sem vínculo (série nova, legado sem
            # backfill) recebe o derivado, com a técnica do item da ficha JÁ
            # atualizado acima - mesma regra do Modo Treino.
            plan_technique = None
            if any(
                st["set_type"] in technique_accounting.TECHNIQUE_APPLICATION_SET_TYPES
                and (not st.get("technique_code") or st.get("anchor_set_number") is None)
                for st in sets
            ) and workout_plan_exercise_id:
                cur.execute(
                    "SELECT advanced_technique FROM workout_plan_exercises WHERE id=%s", (workout_plan_exercise_id,)
                )
                plan_technique = (cur.fetchone() or {}).get("advanced_technique")
            link_technique_applications(sets, plan_technique)

            # COD-12: series recriadas em lote, na ordem recebida.
            cur.executemany(
                """
                INSERT INTO exercise_sets (
                    exercise_id, set_number, set_type, reps, weight_kg, rpe, rest_seconds,
                    hr_avg_bpm, hr_max_bpm, notes, muscle_pain, joint_pain,
                    rpe_target_effective, safety_event, technique_code, anchor_set_number,
                    measure, duration_seconds,
                    reps_pending, completed_at, planned_weight_kg, load_adjust_source, rpe_target_source,
                    reps_left, reps_right, rpe_left, rpe_right
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        exercise_id,
                        st["set_number"],
                        st["set_type"],
                        st.get("reps"),
                        st.get("weight_kg"),
                        st.get("rpe"),
                        st.get("rest_seconds"),
                        st.get("hr_avg_bpm"),
                        st.get("hr_max_bpm"),
                        st.get("notes"),
                        st.get("muscle_pain"),
                        st.get("joint_pain"),
                        # Modo Intensivo: alvo do PROTOCOLO da técnica é mantido
                        # (`exercise_set_update_merge.keeps_protocol_target`).
                        st.get("rpe_target_effective")
                        if keeps_protocol_target(st)
                        else None if is_preparatory_set(st["set_type"]) else intensity.rpe_target_effective,
                        effective_intensity.safety_event_for(st["set_type"], st.get("rpe"), intensity),
                        st.get("technique_code"),
                        st.get("anchor_set_number"),
                        *set_measure_columns(st),
                        st.get("reps_pending") or 0,
                        st.get("completed_at"),
                        st.get("planned_weight_kg"),
                        st.get("load_adjust_source"),
                        st.get("rpe_target_source"),
                        # M102: lados preservados pela mescla (canônico já recalculado).
                        *(st.get(field) for field in SIDE_FIELDS),
                    )
                    for st in sets
                ],
            )

            _recompute_activity_avg_rpe(cur, activity_id)

            return {
                "exercise_id": exercise_id,
                "activity_id": activity_id,
                "sets": len(sets),
            }


def delete_exercise_log(exercise_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT e.id, e.activity_id FROM exercises e "
                    "JOIN activities a ON a.id = e.activity_id WHERE e.id=%s AND a.user_id=%s",
                    (exercise_id, user_id),
                )
            else:
                cur.execute("SELECT id, activity_id FROM exercises WHERE id=%s", (exercise_id,))
            existing = cur.fetchone()
            if not existing:
                return None

            activity_id = existing["activity_id"]

            cur.execute("DELETE FROM exercise_sets WHERE exercise_id=%s", (exercise_id,))
            cur.execute("DELETE FROM exercises WHERE id=%s", (exercise_id,))

            _recompute_activity_avg_rpe(cur, activity_id)

            # O treino (activity) permanece existindo mesmo esvaziado (0 exercicios):
            # o usuario decide explicitamente quando excluir o treino inteiro, via
            # DELETE /activities/{id}. Antes deste ponto o ultimo exercicio de uma
            # activity apagava a activity junto; mantido para compatibilidade de
            # payload (nenhum consumidor depende do valor, sempre False agora).
            return {
                "exercise_id": exercise_id,
                "activity_id": activity_id,
                "deleted_activity": False,
            }


def high_rpe_sets(min_rpe: float = 9.0, limit: int = 50, user_id: int | None = None) -> list[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            # Aquecimento/serie de ajuste nao sao series de esforco real:
            # ficam fora de /sets/high-rpe. Legado com set_type NULL conta.
            where = f"WHERE s.rpe >= %s AND {sql_not_preparatory('s.set_type')} AND {sql_set_confirmed('s.reps_pending')}"
            params: list[Any] = [min_rpe]
            if user_id is not None:
                where += " AND a.user_id = %s"
                params.append(user_id)
            params.append(limit)
            cur.execute(
                f"""
                SELECT a.local_date, a.name AS activity_name, e.original_name,
                       (
                           SELECT exm.external_name
                           FROM exercise_external_mappings exm
                           WHERE exm.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                             AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                           ORDER BY exm.id ASC LIMIT 1
                       ) AS mapped_name,
                       s.set_number, s.set_type, s.reps, s.weight_kg, s.rpe
                FROM exercise_sets s
                JOIN exercises e ON e.id = s.exercise_id
                JOIN activities a ON a.id = e.activity_id
                LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
                {where}
                ORDER BY a.local_date DESC, a.id DESC, e.sequence, s.set_number
                LIMIT %s
                """,
                tuple(params),
            )
            return cur.fetchall()


def exercise_history(name: str, limit: int = 30, user_id: int | None = None) -> list[dict[str, Any]]:
    # Busca so por e.original_name: zm.amazfit_name (nome do relogio
    # Amazfit/Zepp) foi removido do WHERE de proposito - regra de negocio,
    # amazfit_name nunca deve ser usado como criterio de matching/historico.
    pattern = f"%{name}%"
    with transaction() as conn:
        with conn.cursor() as cur:
            where = "WHERE e.original_name LIKE %s"
            params: list[Any] = [pattern]
            if user_id is not None:
                where += " AND a.user_id = %s"
                params.append(user_id)
            params.append(limit)
            cur.execute(
                f"""
                SELECT a.local_date, a.name AS activity_name, e.original_name,
                       (
                           SELECT exm.external_name
                           FROM exercise_external_mappings exm
                           WHERE exm.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                             AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                           ORDER BY exm.id ASC LIMIT 1
                       ) AS mapped_name,
                       e.sets_count, e.reps_total, e.volume_kg, e.best_weight_kg, e.avg_rpe, e.notes
                FROM exercises e
                JOIN activities a ON a.id = e.activity_id
                LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
                {where}
                ORDER BY a.local_date DESC, a.id DESC, e.sequence
                LIMIT %s
                """,
                tuple(params),
            )
            return cur.fetchall()


# Teto de data do historico do recorde no Modo Treino: o historico e o
# INTEIRO (a sessao em andamento sai por `activity_id`), e `HISTORY_SQL`
# exige um limite superior de `local_date`.
_RECORD_HISTORY_UNTIL = "9999-12-31"


def _previous_session_set_group_fatigue(
    conn: Any, user_id: int, activity_id: int, exercise_row_id: int
) -> dict[int, Any]:
    """`exercise_sets.id` -> indicador de fadiga do grupo (M79). Refinamento
    da tela: falha de leitura nao derruba a sessao anterior (sem indicador)."""
    from .intelligence.session_fatigue_reader import load_set_group_fatigue

    try:
        return load_set_group_fatigue(conn, user_id, activity_id, exercise_row_id)
    except Exception:  # noqa: BLE001 - indicador e refinamento, nunca derruba a leitura
        logger.exception("Falha ao ler a fadiga do grupo da sessao anterior activity_id=%s", activity_id)
        return {}


def get_current_group_fatigue(mapping_id: int, activity_id: int, user_id: int | None) -> dict[str, Any] | None:
    """"Fadiga atual do grupo" no Modo Treino (M79): o indicador do dono
    (`domain/prior_fatigue.group_fatigue_for_sequence`) antes da 1a serie do
    exercicio canonico na sessao EM ANDAMENTO (`activity_id`, do usuario).
    None sem usuario, sem musculo principal ou em falha de leitura (o
    indicador e refinamento; a referencia da sessao anterior segue)."""
    if user_id is None:
        return None
    from .intelligence.session_fatigue_reader import load_current_group_fatigue

    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                canonical_mapping_id = _resolve_canonical_exercise_id(int(mapping_id), cur)
            reading = load_current_group_fatigue(conn, int(user_id), int(activity_id), int(canonical_mapping_id))
    except Exception:  # noqa: BLE001 - indicador e refinamento, nunca derruba a leitura
        logger.exception("Falha ao ler a fadiga atual do grupo activity_id=%s", activity_id)
        return None
    return reading.as_dict() if reading is not None else None


# M97 F1: objetivo de contexto do Modo Treino = o da linha da ficha da sessao
# em andamento para o exercicio (carimbado no congelamento). Sem ficha/linha:
# o default (pelo dono, `training_goal.goal_value(None)`).
SESSION_ITEM_GOAL_SQL = (
    "SELECT wpe.training_goal FROM activities a "
    "JOIN workout_plan_exercises wpe ON wpe.plan_id = a.workout_plan_id "
    "LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = wpe.mapping_id "
    "WHERE a.id = %s AND COALESCE(elm.canonical_exercise_id, wpe.mapping_id) = %s "
    "ORDER BY wpe.sequence ASC, wpe.id ASC LIMIT 1"
)


def session_item_goal(cur: Any, activity_id: Any, canonical_mapping_id: Any) -> str:
    """VALOR do objetivo da linha da ficha da sessao `activity_id` para o
    exercicio canonico (ausente = default)."""
    if activity_id is None or canonical_mapping_id is None:
        return goal_value(None)
    cur.execute(SESSION_ITEM_GOAL_SQL, (int(activity_id), int(canonical_mapping_id)))
    row = cur.fetchone() or {}
    return goal_value(row.get("training_goal"))


def get_last_session_sets(
    mapping_id: int,
    exclude_activity_id: int,
    user_id: int | None = None,
    equipment_id: int | None = None,
    training_goal: str | None = None,
) -> dict[str, Any] | None:
    """Series da sessao anterior mais recente (excluindo a sessao atual) em que
    esse mesmo exercicio canonico (mapping_id) foi executado, usadas como
    referencia historica no modo treino (GET /exercises/last-session-sets).
    Retorna None quando nao ha sessao anterior com esse exercicio.

    Quando `equipment_id` (mesmo valor de exercises.equipment_id, que referencia
    gym_equipment.id) e informado, o match e ESTRITO pelo equipamento GLOBAL
    real (equipment.id via gym_equipment.equipment_id) usado na linha historica,
    para nao misturar variacoes fisicas diferentes do mesmo exercicio canonico
    (ex: barra reta vs barra EZ) mesmo que tenham sido usadas em academias
    diferentes. Se nao houver historico nesse equipamento especifico, retorna
    None em vez de cair para outro equipamento (intencional: melhor "sem
    historico" do que um dado de equipamento errado). Quando `equipment_id`
    nao e informado, mantem o comportamento atual (so por mapping_id).

    M97 F1: so execucoes do MESMO objetivo de treino - `training_goal`
    explicito ou, sem ele, o da linha da ficha da sessao atual
    (`session_item_goal`; sem ficha = default). O recorde e o e1RM abaixo
    continuam unicos (chave fisica)."""
    with transaction() as conn:
        with conn.cursor() as cur:
            # `mapping_id` recebido pode ser um id antigo absorvido pela
            # consolidacao do catalogo - resolve para o canonico ANTES de
            # comparar, e compara contra o mapping_id de exercises tambem
            # resolvido (COALESCE via exercise_legacy_id_map), pra casar
            # historico logado sob qualquer um dos dois lados.
            canonical_mapping_id = _resolve_canonical_exercise_id(int(mapping_id), cur)
            goal = (
                goal_value(training_goal)
                if training_goal is not None
                else session_item_goal(cur, exclude_activity_id, canonical_mapping_id)
            )

            where = (
                "WHERE COALESCE(elm.canonical_exercise_id, e.mapping_id) = %s AND a.id != %s "
                f"AND {sql_goal_is('e')} AND {sql_not_deload('a')}"
            )
            params: list[Any] = [canonical_mapping_id, exclude_activity_id, goal]
            if user_id is not None:
                where += " AND a.user_id = %s"
                params.append(user_id)
            # `eqex` (par equipamento x exercicio canonico) da a convencao de
            # carga da linha historica (migracao 045; NULL = `total`).
            join = (
                "LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id "
                "LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id "
                + equipment_exercises_join_sql(canonical_expr="COALESCE(elm.canonical_exercise_id, e.mapping_id)")
            )
            convention_expr = load_convention_select_expr(cur)
            if equipment_id is not None:
                where += " AND ge.equipment_id = (SELECT equipment_id FROM gym_equipment WHERE id = %s)"
                params.append(equipment_id)
            cur.execute(
                f"""
                SELECT e.id AS exercise_id, a.id AS activity_id, a.local_date,
                       ge.equipment_id AS global_equipment_id,
                       {convention_expr} AS load_convention
                FROM exercises e
                JOIN activities a ON a.id = e.activity_id
                {join}
                {where}
                ORDER BY a.local_date DESC, a.id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            session_row = cur.fetchone()
            if not session_row:
                return None

            cur.execute(
                f"""
                SELECT id, set_number, set_type, reps, weight_kg, rpe, technique_code, anchor_set_number,
                       measure, duration_seconds, reps_left, reps_right, rpe_left, rpe_right
                FROM exercise_sets
                WHERE exercise_id = %s AND {sql_set_confirmed('reps_pending')}
                ORDER BY set_number ASC
                """,
                (session_row["exercise_id"],),
            )
            # Referencia "serie anterior" do modo treino: aquecimento/ajuste
            # nao tem par nas series de trabalho, entao ficam de fora.
            sets = [s for s in cur.fetchall() if not is_preparatory_set(s)]
            for set_row in sets:
                set_row["set_type"] = normalize_set_type(set_row.get("set_type"))
                # Lote T3: a medida da serie e o texto pronto do dono
                # ("10 reps" / "58 s" / "15 reps em 30 s") - a tela nao formata.
                _attach_set_measure(set_row)
                # M102: lados da serie unilateral, texto pronto do dono.
                attach_sides_text(set_row)
                # Fase 5 (PR 4): a aplicacao de tecnica (mini-bloco, drop,
                # reps alem da falha) continua no payload - com a tecnica e a
                # ancora -, mas MARCADA: ela nao e a "Serie N" da sessao
                # anterior e a tela nao a numera como tal.
                is_application = set_row["set_type"] in technique_accounting.TECHNIQUE_APPLICATION_SET_TYPES
                set_row["is_technique_application"] = is_application
                if is_application and not set_row.get("technique_code"):
                    set_row["technique_code"] = technique_accounting.derive_technique_code(set_row["set_type"])

            # Recorde (M76 Lote 3b, decisao 3): a regra do dashboard
            # (`session_records.record_events`) na chave da sessao anterior -
            # exercicio canonico + equipamento global + convencao -, sobre o
            # historico INTEIRO do exercicio (a excecao "sem equipamento" do
            # §8.1 olha todas as exposicoes), sem a sessao atual. Duas
            # consultas: as exposicoes (uma linha cada, ja agregada) e as
            # series de trabalho SO da exposicao recorde (reps informativas).
            record = None
            record_key_has_equipment = session_row.get("global_equipment_id") is not None
            if user_id is not None:
                # Import local: o pacote `intelligence` e pesado e nao deve ser
                # puxado na importacao de `services`.
                from .intelligence.dashboard import session_records

                cur.execute(
                    session_records.history_sql(convention_expr).format(placeholders="%s"),
                    (user_id, _RECORD_HISTORY_UNTIL, canonical_mapping_id),
                )
                mark = current_mark(
                    cur.fetchall(),
                    exercise_id=canonical_mapping_id,
                    equipment_id=session_row.get("global_equipment_id"),
                    load_convention=session_row.get("load_convention"),
                    exclude_activity_id=exclude_activity_id,
                )
                # M76 Lote 3d: sem recorde vigente, a marca e a "Melhor carga"
                # da chave (`kind = "best_load"`); a tela so escolhe o rotulo.
                if mark is not None:
                    kind, event = mark
                    record_key_has_equipment = event.exposure.key.equipment_id is not None
                    cur.execute(
                        session_records.SETS_SQL.format(placeholders="%s"),
                        (user_id, event.exposure.exercise_row_id),
                    )
                    record = record_payload(event, cur.fetchall(), kind)

            # e1RM "nesse equipamento": MESMO escopo (usuario,
            # exercicio canonico, equipamento global, sem a sessao atual) do
            # match acima E a mesma convencao de carga da sessao anterior (a
            # chave de comparabilidade do dashboard: no escopo "exercicio" o
            # historico mistura aparelhos, e halter x barra nao se comparam).
            # Uma unica consulta agregada por (carga, reps, RPE) distintos - o
            # payload nao cresce com o numero de sessoes, e a regra (e1RM
            # confiavel ate 12 reps) fica na funcao pura
            # `summarize_equipment_history`. M76 Lote 3b: sem `equipment_id`
            # pedido, o escopo e o equipamento da sessao anterior (a mesma
            # chave do recorde, e o rotulo "nesse equipamento" da tela) em vez
            # de misturar aparelhos.
            convention = normalize_load_convention(session_row.get("load_convention"))
            e1rm_where, e1rm_params = where, list(params)
            if equipment_id is None and session_row.get("global_equipment_id") is not None:
                e1rm_where += " AND ge.equipment_id = %s"
                e1rm_params.append(session_row["global_equipment_id"])
            cur.execute(
                f"""
                SELECT es.weight_kg, es.reps, es.rpe
                FROM exercises e
                JOIN activities a ON a.id = e.activity_id
                JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
                {join}
                {e1rm_where}
                  AND {sql_is_working("es.set_type")}
                  AND {sql_is_rep_based("es.measure")}
                  AND es.reps > 0
                  AND COALESCE({convention_expr}, %s) = %s
                GROUP BY es.weight_kg, es.reps, es.rpe
                """,
                (*e1rm_params, DEFAULT_LOAD_CONVENTION.value, convention.value),
            )
            history_summary = summarize_equipment_history(cur.fetchall(), convention)

            # M79: indicador de fadiga do grupo ANTES de cada serie da sessao
            # anterior (dono `domain/prior_fatigue.group_fatigue_for_sequence`,
            # sobre a sessao anterior INTEIRA). A tela so mostra `band_label`. Por
            # ultimo: uma falha aqui nao afeta as leituras acima.
            fatigue_by_set_id = (
                _previous_session_set_group_fatigue(
                    conn, user_id, int(session_row["activity_id"]), int(session_row["exercise_id"])
                )
                if user_id is not None
                else {}
            )
            for set_row in sets:
                reading = fatigue_by_set_id.get(set_row.pop("id", None))
                set_row["group_fatigue"] = reading.as_dict() if reading is not None else None

    return {
        "activity_id": session_row["activity_id"],
        "local_date": session_row["local_date"],
        "sets": sets,
        # "equipment" quando a chave do recorde tem equipamento (pedido ou o
        # da sessao anterior); "exercise" so na excecao do §8.1 (historico
        # todo sem equipamento). A tela ajusta o rotulo.
        "record_scope": "equipment" if record_key_has_equipment else "exercise",
        "record": record,
        "e1rm_kg": history_summary["e1rm_kg"],
        # Convencao de carga do recorde (`assisted`: menos kg = melhor).
        "load_convention": history_summary["load_convention"],
    }


def suggested_progression_for_mapping(
    mapping_id: int,
    user_id: int | None = None,
    *,
    gym_equipment_id: int | None = None,
    readiness: Any = None,
    training_goal: str | None = None,
    session_id: int | None = None,
    workout_plan_exercise_id: int | None = None,
    item_training_goal: str | None = None,
) -> dict[str, Any] | None:
    """Sugestao de reps_target/load_target/rpe_target para um exercicio fora
    da geracao de ficha: troca de equipamento e treino livre no Modo Treino,
    exercicio extra em /planos-treino (`GET /exercises/suggested-progression`),
    card de `/dashboard/progressao` e drill-down de musculos.

    M76 (Lote 1a, C1-C3): a decisao e do motor v2
    (`domain/prescription_service.suggest_for_pair` -> `prescribe_pairs`), com a
    seguranca do par e o contrato de recuperacao PROJETADO para agora - o mesmo
    caminho da geracao de ficha. Acabou o motor legado (+5% sem passo, faixa
    `min..max+2` derivada da propria sessao, RPE pela media): reps vem da fase
    do grupo, carga da trilha (+ incremento no passo do equipamento quando o
    motor manda subir), RPE do alvo composto com o teto.

    Contrato de resposta mantido (campos que o FE le); campos novos sao
    aditivos. `gym_equipment_id` (= `gym_equipment.id`) escolhe a trilha; sem
    ele, vale a trilha executada mais recentemente do exercicio.

    `training_goal` (M97, VALOR do dono `domain/training_goal`; None = o
    default, a resposta de sempre): a trilha, a fase e a escada do objetivo
    pedido, decididas pelo motor (`suggest_for_pair(training_goal=...)`).

    Troca de par no Modo Treino (M97-D2): o par novo sai no objetivo do ITEM
    trocado (`_item_goal`: o resolvido do item da ficha da sessao quando ha
    `session_id` + `workout_plan_exercise_id`, senao `item_training_goal` do
    pedido), entao um item de forca nao recebe a escada de hipertrofia. Item de
    hipertrofia ou pedido sem objetivo: a resposta de sempre (objetivo None).
    Numa sessao de deload a sugestao sai com a prescricao de deload desde a 1a
    serie (`deload_periods.apply_session_deload_to_pair_suggestion`, dono
    `domain/deload.py`). Sem `session_id`, nenhuma leitura nova.

    Retorna None quando mapping_id nao resolve para nenhum exercicio do catalogo
    (o endpoint decide o que fazer com isso).
    """
    with transaction() as conn:
        with conn.cursor() as cur:
            # `mapping_id` recebido pode ser um id antigo absorvido pela
            # consolidacao do catalogo canonico - resolve antes de qualquer busca.
            canonical_mapping_id = _resolve_canonical_exercise_id(int(mapping_id), cur)
            cur.execute("SELECT ec.id FROM exercise_catalog ec WHERE ec.id = %s", (canonical_mapping_id,))
            mapping = cur.fetchone()
        if not mapping:
            return None
        in_deload = _session_is_deload(conn, user_id, session_id)
        if training_goal is None:
            training_goal = _item_goal(conn, user_id, session_id, workout_plan_exercise_id, item_training_goal)
        suggestion = _pair_suggestion(
            conn, user_id, canonical_mapping_id, gym_equipment_id, readiness=readiness, training_goal=training_goal
        )
        result = _suggested_progression_payload(suggestion)
        if in_deload:
            from .deload_periods import apply_session_deload_to_pair_suggestion

            result = apply_session_deload_to_pair_suggestion(
                conn,
                result,
                int(user_id),
                mapping_id=canonical_mapping_id,
                gym_equipment_id=gym_equipment_id,
                training_goal=training_goal,
            )
    return result


def _engine_goal(goal: Any) -> str | None:
    """Objetivo repassado ao motor do par: o default vira None (a resposta de
    sempre, byte a byte); outro objetivo, o VALOR do dono."""
    from .domain.training_goal import is_default

    if goal in (None, "") or is_default(goal):
        return None
    return goal_value(goal)


def _item_goal(
    conn: Any, user_id: int | None, session_id: int | None, workout_plan_exercise_id: int | None, requested: Any
) -> str | None:
    """Objetivo do item trocado: o RESOLVIDO do item da ficha da sessao
    (servidor) vence o do pedido; default -> None."""
    resolved = None
    if user_id is not None and session_id is not None and workout_plan_exercise_id is not None:
        from .deload_periods import session_plan_item_goal_safe

        with conn.cursor() as cur:
            resolved = session_plan_item_goal_safe(cur, int(user_id), session_id, workout_plan_exercise_id)
    return _engine_goal(resolved if resolved is not None else requested)


def _pair_suggestion(
    conn: Any,
    user_id: int | None,
    canonical_mapping_id: int,
    gym_equipment_id: int | None,
    *,
    readiness: Any = None,
    training_goal: str | None = None,
) -> Any:
    """Sugestao do motor v2 para o par (`suggest_for_pair`); sem usuario, None."""
    from .domain.prescription_service import suggest_for_pair

    equipment_id = (
        effective_intensity.equipment_id_of_gym_equipment(conn, gym_equipment_id) if gym_equipment_id else None
    )
    if user_id is None:
        return None
    # So as linhas DESTE exercicio (PERF-04): `suggest_for_pair` usa `logs`
    # apenas para a ultima execucao do proprio exercicio
    # (`last_execution_from_logs` filtra por `mapping_id`), entao ler as
    # series do historico inteiro nao mudava nada na resposta.
    logs = list_exercise_logs(limit=300, user_id=user_id, only_mapping_id=canonical_mapping_id)
    return suggest_for_pair(
        conn,
        int(user_id),
        canonical_mapping_id,
        equipment_id,
        readiness=readiness,
        logs=logs,
        training_goal=training_goal,
    )


def swapped_pair_deload_prescription(
    conn: Any, user_id: int, mapping_id: int, gym_equipment_id: int | None, item_goal: Any
) -> Any:
    """Prescricao de deload do PAR TROCADO para a regra da proxima serie
    (M97-D2): a MESMA base da 1a serie (`suggested_progression_for_mapping`
    numa sessao de deload) - sugestao do motor para o par novo no objetivo do
    item + trilha do par novo, pelo dono (`deload_periods.
    pair_deload_prescription_for` -> `domain/deload.pair_deload_prescription`)."""
    from .deload_periods import pair_deload_prescription_for

    goal = _engine_goal(item_goal)
    suggestion = _pair_suggestion(conn, user_id, int(mapping_id), gym_equipment_id, training_goal=goal)
    return pair_deload_prescription_for(
        conn,
        _suggested_progression_payload(suggestion),
        int(user_id),
        mapping_id=int(mapping_id),
        gym_equipment_id=gym_equipment_id,
        training_goal=goal,
    )


def _session_is_deload(conn: Any, user_id: int | None, session_id: int | None) -> bool:
    """A sessao `session_id` do usuario e de deload? Sem sessao: False sem
    nenhuma leitura (dono `domain/deload.is_deload_session`)."""
    if session_id is None or user_id is None:
        return False
    from .deload_periods import session_deload_source_safe
    from .domain.deload import is_deload_session

    with conn.cursor() as cur:
        return is_deload_session(session_deload_source_safe(cur, user_id, session_id))


def _suggested_progression_payload(suggestion: Any) -> dict[str, Any]:
    """Resposta de `suggested_progression_for_mapping` para a sugestao do motor
    (contrato que o FE le; campos novos sao aditivos)."""
    from .domain.prescription_service import (
        PAIR_SUGGESTION_ENGINE_VERSION,
        legacy_indicators,
        progression_note,
    )
    from .workout_plan_ai import normalize_load_target

    if suggestion is None:
        return {
            "reps_target": "",
            "load_target": "",
            "rpe_target": "",
            "progression_note": "Sem histórico registrado.",
            "has_history": False,
            "progression": None,
            "last_load_kg": None,
            "last_reps": None,
            "trend": "unknown",
            "last_date": None,
        }

    decision = suggestion.decision
    last = suggestion.last_execution
    return {
        "reps_target": suggestion.reps_target or "",
        "load_target": normalize_load_target(suggestion.suggested_load_kg) or "",
        "rpe_target": suggestion.rpe_target or "",
        "progression_note": progression_note(suggestion),
        "has_history": last is not None or suggestion.track_load_status is not None,
        # Seta da ficha (increased/decreased/same/None): carga sugerida contra a
        # ultima carga de trabalho registrada.
        "progression": legacy_indicators(suggestion)["progression"],
        # "Ultima vez" (Telegram, Modo Treino): o que foi FEITO, nao a sugestao.
        "last_load_kg": last.last_load_kg if last else None,
        "last_reps": last.last_reps if last else None,
        "trend": suggestion.trend,
        "last_date": last.local_date if last else None,
        # Aditivos (M76 Lote 1a): a decisao e o teto que valem para o par.
        "suggested_load_kg": suggestion.suggested_load_kg,
        # Mesmo campo dos indicadores da ficha (topo da faixa do motor).
        "suggested_reps": legacy_indicators(suggestion)["suggested_reps"],
        "load_source": suggestion.load_source,
        "load_increment_kg": suggestion.load_increment_kg,
        "equipment_id": suggestion.equipment_id,
        "decision": decision.get("decision"),
        "effective_rpe_ceiling": decision.get("effective_rpe_ceiling"),
        "failure_allowed": bool(decision.get("failure_allowed")),
        "clamps": list((decision.get("evidence") or {}).get("safety_clamps") or []),
        "engine_version": PAIR_SUGGESTION_ENGINE_VERSION,
        # Fix M97: faixa de carga de forca por %1RM do motor (so quando existe;
        # hipertrofia sai com a resposta de sempre).
        **({"strength_load": suggestion.strength_load} if suggestion.strength_load else {}),
    }


def suggested_set_count_for_substitution(
    original_mapping_id: int,
    original_sets_count: int,
    new_mapping_id: int,
) -> dict[str, Any] | None:
    """Sugestao de quantas series fazer em um exercicio SUBSTITUTO (ex.:
    equipamento planejado estava ocupado na academia) para se aproximar do
    mesmo estimulo muscular que o exercicio original planejado geraria.

    Usado pelo Modo Treino (PlanExecutionScreen.tsx) quando o usuario troca
    de equipamento/exercicio em pleno treino via EquipmentSubstituteModal.

    Criterio de escolha do "grupo primario" do exercicio original quando ha
    mais de uma linha com role='PRIMARY' em exercise_muscle_contributions
    (caso raro, mas o schema permite): usa a de MAIOR stimulus_weight - e o
    grupo que mais "define" o estimulo do exercicio original, entao e o
    grupo mais relevante para preservar ao trocar de exercicio.

    Formula: suggested_sets_count = round(
        original_sets_count * original_primary_stimulus_weight / new_stimulus_weight_for_same_group
    ), com minimo de 1 serie.

    Retorna None apenas se original_mapping_id nao resolver para nenhum
    exercicio com contribuicao PRIMARY cadastrada (nao ha grupo primario para
    comparar). Se o novo exercicio nao tiver NENHUMA contribuicao para esse
    mesmo grupo, retorna suggested_sets_count=None com
    reason="no_contribution_to_original_primary_group" (fallback silencioso
    no frontend), mas o restante do payload (grupo, pesos, sets original)
    ainda vem preenchido para uso futuro em tooltip.
    """
    with transaction() as conn:
        with conn.cursor() as cur:
            canonical_original_id = _resolve_canonical_exercise_id(int(original_mapping_id), cur)
            canonical_new_id = _resolve_canonical_exercise_id(int(new_mapping_id), cur)

            cur.execute(
                """
                SELECT emc.muscle_group_id, mg.name AS muscle_group_name, emc.stimulus_weight
                FROM exercise_muscle_contributions emc
                JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
                WHERE emc.exercise_id = %s AND emc.role = 'PRIMARY' AND emc.is_active = 1
                ORDER BY emc.stimulus_weight DESC, emc.id ASC
                LIMIT 1
                """,
                (canonical_original_id,),
            )
            original_primary = cur.fetchone()

            if not original_primary:
                return None

            primary_group_id = original_primary["muscle_group_id"]
            primary_group_name = original_primary["muscle_group_name"]
            original_stimulus_weight = float(original_primary["stimulus_weight"])

            cur.execute(
                """
                SELECT stimulus_weight
                FROM exercise_muscle_contributions
                WHERE exercise_id = %s AND muscle_group_id = %s AND is_active = 1
                ORDER BY stimulus_weight DESC
                LIMIT 1
                """,
                (canonical_new_id, primary_group_id),
            )
            new_contribution = cur.fetchone()

    if not new_contribution:
        return {
            "suggested_sets_count": None,
            "reason": "no_contribution_to_original_primary_group",
            "original_primary_muscle_group": primary_group_name,
            "original_stimulus_weight": original_stimulus_weight,
            "new_stimulus_weight": None,
            "original_sets_count": original_sets_count,
        }

    new_stimulus_weight = float(new_contribution["stimulus_weight"])
    if new_stimulus_weight <= 0:
        return {
            "suggested_sets_count": None,
            "reason": "no_contribution_to_original_primary_group",
            "original_primary_muscle_group": primary_group_name,
            "original_stimulus_weight": original_stimulus_weight,
            "new_stimulus_weight": new_stimulus_weight,
            "original_sets_count": original_sets_count,
        }

    raw_suggested = original_sets_count * original_stimulus_weight / new_stimulus_weight
    suggested_sets_count = max(1, round(raw_suggested))

    return {
        "suggested_sets_count": suggested_sets_count,
        "reason": None,
        "original_primary_muscle_group": primary_group_name,
        "original_stimulus_weight": original_stimulus_weight,
        "new_stimulus_weight": new_stimulus_weight,
        "original_sets_count": original_sets_count,
    }


def upsert_body_composition(payload: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    """`user_id` (Multiusuario Parte 4): dono do registro, sempre resolvido a
    partir de `identity.user_id` no router - nunca aceito do payload."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO body_composition (
                  user_id, local_date, measured_at, weight_kg, bmi, body_fat_percent, fat_mass_kg,
                  fat_free_mass_kg, muscle_mass_kg, skeletal_muscle_percent, body_water_percent,
                  visceral_fat_rating, bone_mass_kg, bmr_kcal,
                  subcutaneous_fat_percent, protein_percent, metabolic_age,
                  source, notes, raw_payload
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CAST(%s AS JSON))
                ON DUPLICATE KEY UPDATE
                  measured_at=VALUES(measured_at),
                  weight_kg=VALUES(weight_kg),
                  bmi=VALUES(bmi),
                  body_fat_percent=VALUES(body_fat_percent),
                  fat_mass_kg=VALUES(fat_mass_kg),
                  fat_free_mass_kg=VALUES(fat_free_mass_kg),
                  muscle_mass_kg=VALUES(muscle_mass_kg),
                  skeletal_muscle_percent=VALUES(skeletal_muscle_percent),
                  body_water_percent=VALUES(body_water_percent),
                  visceral_fat_rating=VALUES(visceral_fat_rating),
                  bone_mass_kg=VALUES(bone_mass_kg),
                  bmr_kcal=VALUES(bmr_kcal),
                  subcutaneous_fat_percent=VALUES(subcutaneous_fat_percent),
                  protein_percent=VALUES(protein_percent),
                  metabolic_age=VALUES(metabolic_age),
                  notes=VALUES(notes),
                  raw_payload=VALUES(raw_payload)
                """,
                (
                    user_id,
                    payload["local_date"],
                    payload.get("measured_at"),
                    payload.get("weight_kg"),
                    payload.get("bmi"),
                    payload.get("body_fat_percent"),
                    payload.get("fat_mass_kg"),
                    payload.get("fat_free_mass_kg"),
                    payload.get("muscle_mass_kg"),
                    payload.get("skeletal_muscle_percent"),
                    payload.get("body_water_percent"),
                    payload.get("visceral_fat_rating"),
                    payload.get("bone_mass_kg"),
                    payload.get("bmr_kcal"),
                    payload.get("subcutaneous_fat_percent"),
                    payload.get("protein_percent"),
                    payload.get("metabolic_age"),
                    payload.get("source", "manual"),
                    payload.get("notes"),
                    to_json(payload.get("raw_payload") or payload),
                ),
            )
            return {"status": "ok", "id": cur.lastrowid}


def upsert_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exercise_mappings (
                  original_name, amazfit_name, mapped_name, muscle_primary, muscle_secondary,
                  confidence, is_user_confirmed, notes
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  mapped_name=VALUES(mapped_name),
                  muscle_primary=VALUES(muscle_primary),
                  muscle_secondary=VALUES(muscle_secondary),
                  confidence=VALUES(confidence),
                  is_user_confirmed=VALUES(is_user_confirmed),
                  notes=VALUES(notes)
                """,
                (
                    payload["original_name"],
                    payload["amazfit_name"],
                    payload.get("mapped_name"),
                    payload.get("muscle_primary"),
                    payload.get("muscle_secondary"),
                    payload.get("confidence"),
                    payload.get("is_user_confirmed", False),
                    payload.get("notes"),
                ),
            )
            return {"status": "ok", "id": cur.lastrowid}


def resolve_plan_muscle_slugs(exercises: list[dict[str, Any]]) -> dict[str, int]:
    """Traduz muscle_primary/muscle_secondary dos exercícios do plano para
    slugs do react-body-highlighter com intensidade (2=primário, 1=
    secundário), replicando exatamente buildPlanMuscleMapItems() de
    fitness-frontend/.../WorkoutPlansClient.tsx (match exato de string, sem
    split por vírgula; primário sempre vence secundário)."""
    intensity_by_group: dict[str, int] = {}
    for ex in exercises:
        if ex.get("muscle_primary"):
            intensity_by_group[ex["muscle_primary"]] = 2
    for ex in exercises:
        group = ex.get("muscle_secondary")
        if group and group not in intensity_by_group:
            intensity_by_group[group] = 1

    if not intensity_by_group:
        return {}

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mg.name AS muscle_group, mgbm.slugs
                FROM muscle_group_body_map mgbm
                JOIN muscle_groups mg ON mg.id = mgbm.muscle_group_id
                """
            )
            rows = cur.fetchall()
    group_to_slugs = {row["muscle_group"]: [s.strip() for s in row["slugs"].split(",") if s.strip()] for row in rows}

    slug_intensity: dict[str, int] = {}
    for group, intensity in intensity_by_group.items():
        for slug in group_to_slugs.get(group, []):
            slug_intensity[slug] = max(slug_intensity.get(slug, 0), intensity)
    return slug_intensity


def list_muscle_group_body_map() -> list[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mg.name AS muscle_group, mgbm.slugs
                FROM muscle_group_body_map mgbm
                JOIN muscle_groups mg ON mg.id = mgbm.muscle_group_id
                ORDER BY mg.name
                """
            )
            rows = cur.fetchall()

    return [{"muscle_group": row["muscle_group"], "slugs": row["slugs"].split(",")} for row in rows]


def _format_plan_time(value: Any) -> str | None:
    """Normaliza wp.plan_time para "HH:MM" antes de devolver ao cliente.

    pymysql retorna colunas TIME como datetime.timedelta; sem essa conversão o
    encoder JSON padrão do FastAPI cai no fallback total_seconds() e devolve
    um número (ex.: 66600.0) em vez de string, quebrando o frontend
    (WorkoutPlansClient espera plan_time: string | null)."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        return f"{hours:02d}:{minutes:02d}"
    if isinstance(value, str):
        return value[:5]
    return str(value)


def list_workout_plans(
    status: str | None = None,
    limit: int = 20,
    user_id: int | None = None,
    has_advanced_technique: bool | None = None,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if user_id is not None:
        where.append("wp.user_id = %s")
        params.append(user_id)
    if status:
        where.append("wp.status = %s")
        params.append(status)
    if has_advanced_technique:
        # Elegibilidade para o modo treino avançado: plano com pelo menos 1
        # exercício com QUALQUER um dos 4 campos configurados, não só
        # advanced_technique (mesma regra do onpremise).
        where.append(
            """
            EXISTS (
                SELECT 1 FROM workout_plan_exercises wpe2
                WHERE wpe2.plan_id = wp.id
                  AND (
                    wpe2.advanced_technique IS NOT NULL
                    OR wpe2.warmup_reps IS NOT NULL
                    OR wpe2.feeler_set_reps IS NOT NULL
                    OR wpe2.peak_contraction_seconds IS NOT NULL
                  )
            )
            """
        )
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    wp.id, wp.plan_date, wp.plan_time, wp.gym_id, g.name AS gym_name, wp.name, wp.plan_type,
                    wp.split_day, wp.template_id, wpt.name AS template_name,
                    wp.goal, wp.status, wp.created_at, wp.updated_at,
                    COUNT(wpe.id) AS exercises_count
                FROM workout_plans wp
                LEFT JOIN gyms g ON g.id = wp.gym_id
                LEFT JOIN workout_plan_templates wpt ON wpt.id = wp.template_id
                LEFT JOIN workout_plan_exercises wpe ON wpe.plan_id = wp.id
                {where_sql}
                GROUP BY wp.id, wp.plan_date, wp.plan_time, wp.gym_id, g.name, wp.name, wp.plan_type,
                    wp.split_day, wp.template_id, wpt.name, wp.goal, wp.status, wp.created_at, wp.updated_at
                ORDER BY wp.plan_date DESC, wp.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    for row in rows:
        row["plan_time"] = _format_plan_time(row.get("plan_time"))
    return rows


def _apply_effective_max_sets_fallback(raw_payload: dict[str, Any] | None) -> None:
    """Fallback de compatibilidade retroativa para `effective_max_sets` em
    `raw_payload["template_slot_targets"]`, aplicado na LEITURA (GET
    /workout-plans/{id}), sem migração de dado nem reprocessamento em massa.

    Contexto: o commit 064045c4 adicionou um 4o estado ("excesso", laranja)
    na tabela "Ativação muscular" do frontend (MuscleActivationTable.tsx,
    função activationAssessment), que compara séries reais contra um teto
    `template_slot_targets[].effective_max_sets` (populado em
    generation/mode_a_legacy.py::generate_plan_draft_from_template_v2,
    ver comentário logo acima do `template_slot_targets = [...]` local).

    Fichas SALVAS antes desse commit não têm esse campo em
    `template_slot_targets`, então a Avaliação nunca mostra "excesso" pra
    elas, mesmo com séries muito acima do razoável. Existem 3 formatos de
    `raw_payload` em produção:

    1. Legado/muito antigo: só `template_slot_targets[].sets_count`, sem
       `volume_planning` nenhum -> sem teto disponível, comportamento de 3
       estados preservado (esta função não faz nada).
    2. Intermediário: `template_slot_targets[].sets_count` sem
       `effective_max_sets`, MAS com `volume_planning.entries[].slot_max` no
       mesmo raw_payload (populado por
       GenerationVolumeSummary.as_dict() em
       app/intelligence/template_volume_domain.py, chamado dentro de
       generate_plan_draft_from_template_v2 como
       raw_payload["volume_planning"]).
    3. Atual: `template_slot_targets[].effective_max_sets` já presente
       (gerado depois de 064045c4).

    `volume_planning.entries[].slot_max` e
    `template_slot_targets[].effective_max_sets` são a MESMA origem: em
    generation/mode_a_legacy.py, o Entry do volume_summary usa
    `slot_max=budget.effective_slot_max` e o `template_slot_targets` usa
    `"effective_max_sets": round(b.effective_slot_max, 2)` — ambos lidos do
    mesmo objeto `MuscleSlotBudget`, com o mesmo `round(..., 2)`. Ou seja,
    para planos no formato 2, `volume_planning.entries[].slot_max` (já
    arredondado) É o teto efetivo que faltou em `template_slot_targets`, e
    pode ser copiado para lá sem nenhuma perda de precisão/semântica.

    Muta `raw_payload["template_slot_targets"]` in-place, casando por
    `muscle_group_id`. Nunca sobrescreve um `effective_max_sets` já
    presente e não-None (formato 3, intocado). Não propaga exceção — quem
    chama (get_workout_plan) já envolve em try/except, mas esta função é
    escrita para nunca lançar em payload malformado (só ignora silenciosamente).
    """
    if not isinstance(raw_payload, dict):
        return
    targets = raw_payload.get("template_slot_targets")
    if not isinstance(targets, list) or not targets:
        return
    volume_planning = raw_payload.get("volume_planning")
    if not isinstance(volume_planning, dict):
        return
    entries = volume_planning.get("entries")
    if not isinstance(entries, list) or not entries:
        return

    slot_max_by_group: dict[Any, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        group_id = entry.get("muscle_group_id")
        slot_max = entry.get("slot_max")
        if group_id is None or slot_max is None:
            continue
        slot_max_by_group[group_id] = slot_max

    if not slot_max_by_group:
        return

    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("effective_max_sets") is not None:
            continue
        group_id = target.get("muscle_group_id")
        if group_id in slot_max_by_group:
            target["effective_max_sets"] = slot_max_by_group[group_id]


def get_workout_plan(plan_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """
                    SELECT wp.id, wp.plan_date, wp.plan_time, wp.gym_id, g.name AS gym_name, wp.name, wp.plan_type,
                           wp.split_day, wp.template_id, wpt.name AS template_name,
                           wp.template_slot_sequence, wp.template_version_id, wp.goal, wp.status,
                           wp.raw_payload, wp.created_at, wp.updated_at
                    FROM workout_plans wp
                    LEFT JOIN gyms g ON g.id = wp.gym_id
                    LEFT JOIN workout_plan_templates wpt ON wpt.id = wp.template_id
                    WHERE wp.id = %s AND wp.user_id = %s
                    """,
                    (plan_id, user_id),
                )
            else:
                cur.execute(
                    """
                    SELECT wp.id, wp.plan_date, wp.plan_time, wp.gym_id, g.name AS gym_name, wp.name, wp.plan_type,
                           wp.split_day, wp.template_id, wpt.name AS template_name,
                           wp.template_slot_sequence, wp.template_version_id, wp.goal, wp.status,
                           wp.raw_payload, wp.created_at, wp.updated_at
                    FROM workout_plans wp
                    LEFT JOIN gyms g ON g.id = wp.gym_id
                    LEFT JOIN workout_plan_templates wpt ON wpt.id = wp.template_id
                    WHERE wp.id = %s
                    """,
                    (plan_id,),
                )
            plan = cur.fetchone()
            if not plan:
                return None

            plan["plan_time"] = _format_plan_time(plan.get("plan_time"))
            # Edicao dos exercicios em /planos-treino: a tela so le o booleano.
            plan["editable"] = is_plan_editable(plan.get("status"))
            plan["raw_payload"] = from_json_dict(plan.get("raw_payload"))
            try:
                _apply_effective_max_sets_fallback(plan["raw_payload"])
            except Exception:
                # Nunca deixar um raw_payload malformado/inesperado quebrar a
                # leitura do plano — mesma filosofia defensiva de
                # _expose_frozen_prescriptions logo abaixo.
                logger.warning(
                    "get_workout_plan: _apply_effective_max_sets_fallback falhou (plano %s); segue sem o fallback",
                    plan_id,
                    exc_info=True,
                )

            cur.execute(
                f"""
                SELECT wpe.id AS workout_plan_exercise_id, wpe.sequence, wpe.exercise_name, wpe.amazfit_name,
                       wpe.mapping_id, wpe.muscle_primary,
                       wpe.muscle_secondary, wpe.equipment_name, wpe.preferred_equipment_id, wpe.sets_count,
                       wpe.reps_target, wpe.load_target, wpe.rpe_target, wpe.rest_seconds, wpe.warmup,
                       wpe.substitution_options, wpe.notes,
                       wpe.advanced_technique, wpe.advanced_technique_applications,
                       wpe.advanced_technique_duration_seconds, wpe.warmup_sets_count, wpe.warmup_reps,
                       wpe.warmup_rest_seconds, wpe.feeler_set_sets_count, wpe.feeler_set_reps,
                       wpe.feeler_set_rest_seconds, wpe.peak_contraction_seconds,
                       wpe.measure, wpe.duration_seconds_target, wpe.time_window_seconds,
                       wpe.training_goal,
                       wpe.prescription_json, wpe.progression_decision_id, wpe.engine_version,
                       wpe.requires_user_choice,
                       ec.description AS exercise_description,
                       {laboral_domain.sql_catalog_columns("ec")},
                       ec.is_unilateral AS is_unilateral,
                       ec.id AS card_exercise_id, ec.card_version AS card_version
                FROM workout_plan_exercises wpe
                LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = wpe.mapping_id
                LEFT JOIN exercise_catalog ec ON ec.id = COALESCE(elm.canonical_exercise_id, wpe.mapping_id)
                WHERE wpe.plan_id = %s
                ORDER BY wpe.sequence ASC
                """,
                (plan_id,),
            )
            exercises = cur.fetchall()
            for ex in exercises:
                ex["substitution_options"] = from_json_list(ex.get("substitution_options"))
                # M100: exercicio laboral (catalogo) - o Modo Treino mostra o
                # card so leitura (Concluir/Pular), sem progressao.
                ex["is_laboral"] = laboral_domain.is_laboral_flag(ex.get("is_laboral"))
                # Series, reps e carga prontas do dono (`laboral_prescription`:
                # catalogo > linha > fallback) - a tela nao interpreta texto.
                laboral_defaults = laboral_domain.defaults_from_row(ex)
                for field in laboral_domain.CATALOG_FIELDS[1:]:
                    ex.pop(field, None)
                ex["laboral"] = (
                    laboral_domain.laboral_prescription(laboral_defaults, ex).to_dict() if ex["is_laboral"] else None
                )
                # M102: exercicio unilateral (catalogo) -> o Modo Treino pede E/D.
                ex["is_unilateral"] = bool(ex.get("is_unilateral"))
                # Card ilustrado publicado (PR5 dos cards): do exercicio
                # CANONICO (`ec.id`, ja resolvido pelo COALESCE do legado
                # acima), nunca do `mapping_id` cru da ficha. As duas colunas
                # auxiliares saem da resposta; fica so `exercise_card`
                # (`{url, thumbnail_url, version}` ou `None`).
                ex["exercise_card"] = exercise_card_storage.training_card(
                    ex.pop("card_exercise_id", None), ex.pop("card_version", None)
                )
            plan["exercises"] = exercises

        # D13: as fichas geradas ANTES da correcao do bug da ficha 112 tem
        # itens dos motores determinísticos apontando para um `gym_equipment`
        # de outro exercício. A leitura não entrega esse par: ele é suprimido e
        # o item aparece como o que de fato é — sem equipamento resolvido,
        # exigindo escolha no modo treino. Nada é gravado aqui (a ficha se
        # corrige no próximo `refresh-prescription` ou save).
        _suppress_incoherent_equipment_pairs(conn, exercises, plan_id=plan_id)

        # PR-11: a leitura NAO recalcula progressao nenhuma (era
        # o motor legado aqui). Abrir o mesmo plano
        # duas vezes devolve exatamente a mesma prescricao. Fora do `with` do
        # cursor de cima: abre os proprios cursores, um de cada vez.
        _expose_frozen_prescriptions(conn, exercises)

    return plan


def _suppress_incoherent_equipment_pairs(conn, exercises: list[dict[str, Any]], *, plan_id: int) -> None:
    """Zera, NA RESPOSTA, o vinculo de equipamento que nao serve o exercicio
    da linha (D13). Somente leitura - a correcao no banco acontece no proximo
    save/refresh da ficha.

    Sem isto, uma ficha ja gravada torta continua mostrando a foto e a
    etiqueta do equipamento errado no modo treino (o sintoma reportado:
    "Remada curvada no orbital row" com etiqueta "ESTACAO DE POLIA").

    **Escopo: so os motores DETERMINISTICOS (Modo A/B).** A D13 e o contrato
    deles, e e neles que um par torto e mentira sobre o que a ficha prescreve.
    Fichas antigas do caminho legado carregam pares incoerentes por outro
    motivo (o `mapping_id` gravado pela IA a partir de texto livre nem sempre
    era o exercicio certo) e sao historico ja treinado: reescrever a exibicao
    delas nao corrige nada e apagaria a etiqueta que o usuario viu no dia. A
    fragilidade do legado sai de cena com ele (PR-19/20)."""
    suspects = [
        ex
        for ex in exercises
        if ex.get("mapping_id")
        and ex.get("preferred_equipment_id")
        and str(ex.get("engine_version") or "").startswith(("mode-a-", "mode-b-"))
    ]
    if not suspects:
        return
    try:
        with conn.cursor() as cur:
            found = equipment_pair.exercise_ids_by_gym_equipment(
                cur, [ex["preferred_equipment_id"] for ex in suspects]
            )
            canonical = equipment_pair.canonical_exercise_ids(
                cur,
                [ex["mapping_id"] for ex in suspects] + [value for value in found.values() if value],
            )
    except Exception:
        # Falha de leitura aqui nao pode derrubar a ficha; no pior caso a
        # resposta fica como esta hoje.
        logger.exception("Falha ao checar coerencia dos pares da ficha id=%s", plan_id)
        return

    for ex in suspects:
        served = found.get(int(ex["preferred_equipment_id"]))
        if served is not None and canonical.get(int(ex["mapping_id"])) == canonical.get(int(served)):
            continue
        logger.warning(
            "D13: par incoerente suprimido na leitura plan_id=%s sequence=%s mapping_id=%s gym_equipment_id=%s",
            plan_id,
            ex.get("sequence"),
            ex.get("mapping_id"),
            ex.get("preferred_equipment_id"),
        )
        ex["preferred_equipment_id"] = None
        ex["equipment_name"] = None
        ex["requires_user_choice"] = True


def _safety_by_exercise_row(conn, exercises: list[dict[str, Any]]) -> dict[int, Any]:
    """`workout_plan_exercise_id` -> `TrainingSafety` do par
    (exercicio do catalogo x equipamento do CATALOGO GLOBAL).

    A ficha guarda `preferred_equipment_id` = `gym_equipment.id` (equipamento
    daquela academia); a seguranca e do `equipment.id` por tras dele - dai o
    SELECT de traducao. Linha sem mapping/equipamento resolvido fica de fora
    (o chamador trata como par desconhecido -> default D3)."""
    wanted = {
        int(ex["preferred_equipment_id"])
        for ex in exercises
        if ex.get("mapping_id") and ex.get("preferred_equipment_id")
    }
    if not wanted:
        return {}
    ids = sorted(wanted)
    placeholders = ", ".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, equipment_id FROM gym_equipment WHERE id IN ({placeholders})", tuple(ids))
        equipment_by_gym_equipment = {int(row["id"]): int(row["equipment_id"]) for row in cur.fetchall() or []}

    pairs = {}
    for ex in exercises:
        if not (ex.get("mapping_id") and ex.get("preferred_equipment_id")):
            continue
        equipment_id = equipment_by_gym_equipment.get(int(ex["preferred_equipment_id"]))
        if equipment_id:
            pairs[int(ex["workout_plan_exercise_id"])] = (int(ex["mapping_id"]), equipment_id)
    if not pairs:
        return {}
    safety = resolve_training_safety_bulk(conn, pairs.values())
    return {row_id: safety[key] for row_id, key in pairs.items() if key in safety}


def _load_steps_by_row(conn, exercises: list[dict[str, Any]]) -> dict[int, Any]:
    """`workout_plan_exercise_id` -> `equipment.load_increment_kg` (pode ser
    NULL) do equipamento da linha, numa query so."""
    wanted = sorted({int(ex["preferred_equipment_id"]) for ex in exercises if ex.get("preferred_equipment_id")})
    if not wanted:
        return {}
    placeholders = ", ".join(["%s"] * len(wanted))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ge.id, eq.load_increment_kg FROM gym_equipment ge "
            f"JOIN equipment eq ON eq.id = ge.equipment_id WHERE ge.id IN ({placeholders})",
            tuple(wanted),
        )
        by_gym = {int(row["id"]): row.get("load_increment_kg") for row in cur.fetchall() or []}
    return {
        int(ex["workout_plan_exercise_id"]): by_gym.get(int(ex["preferred_equipment_id"]))
        for ex in exercises
        if ex.get("preferred_equipment_id")
    }


def _load_anchor_kg(shown: dict[str, Any] | None, ex: dict[str, Any]) -> Any:
    """Carga de trabalho prescrita (ancora das cargas de aquecimento, ajuste e
    tecnica): a decisao do motor congelada; senao o `load_target` da linha."""
    from .domain.training_history import parse_decimal

    suggested = (shown or {}).get("suggested_load_kg")
    if suggested is not None:
        return suggested
    value = parse_decimal((shown or {}).get("load_target") or ex.get("load_target"))
    return value if value is not None and value > 0 else None


def _expose_frozen_prescriptions(conn, exercises: list[dict[str, Any]]) -> None:
    """Le o snapshot congelado de cada exercicio e aplica o overlay de
    seguranca ao vivo (secao 6.3: so restringe, nunca amplia, e e auditado).

    Exercicio sem `prescription_json` (ficha anterior ao PR-11) fica com
    `prescription_status = LEGACY` e `prescription = None`: a secao 12 manda
    exibir como esta, sem recalcular. Quem quiser a prescricao nova pede
    `POST /workout-plans/{id}/refresh-prescription` (so para `planned` com
    data >= hoje) - e a UI so oferece o botao nesse caso.

    M76 (Lote 1a, C7): cada exercicio ganha `load_increment_kg` (passo do
    equipamento) e a prescricao exibida ganha o bloco de cargas das series
    que nao sao de trabalho (`domain/session_loads.session_load_plan`:
    `load_anchor_kg`, `warmup_loads_kg`, `feeler_load_kg`, `feeler_loads_kg`,
    `application_loads_kg`) - calculado no backend pelo dono, para o Modo
    Treino so ler."""
    from .domain.session_loads import load_step, session_load_plan

    if not exercises:
        return
    try:
        safety_by_row = _safety_by_exercise_row(conn, exercises)
    except Exception:
        # Falha ao ler a seguranca nao pode derrubar a leitura da ficha: sem
        # overlay a prescricao congelada e exibida como foi congelada.
        logger.exception("Falha ao resolver seguranca para o overlay da ficha")
        safety_by_row = {}
    try:
        steps_by_row = _load_steps_by_row(conn, exercises)
    except Exception:
        logger.exception("Falha ao ler o passo de carga dos equipamentos da ficha")
        steps_by_row = {}

    for ex in exercises:
        row_id = int(ex["workout_plan_exercise_id"])
        frozen = from_json_dict(ex.pop("prescription_json", None))
        safety = safety_by_row.get(row_id)
        shown, overlay = prescription_domain.apply_safety_overlay(frozen, safety)
        step = steps_by_row.get(row_id)
        ex["load_increment_kg"] = float(load_step(step))
        if shown is not None:
            params = shown.get("advanced_technique_parameters") or {}
            shown.update(
                session_load_plan(
                    _load_anchor_kg(shown, ex),
                    load_increment_kg=step,
                    feeler_sets_count=ex.get("feeler_set_sets_count"),
                    application_loads_kg=params.get("application_loads_kg"),
                )
            )
        ex["prescription"] = shown
        ex["prescription_status"] = (
            prescription_domain.STATUS_FROZEN if frozen else prescription_domain.STATUS_LEGACY
        )
        ex["safety_overlay"] = overlay
        ex["requires_user_choice"] = bool(ex.get("requires_user_choice"))
        # Compatibilidade: os 4 indicadores que a UI ja consumia saem do
        # snapshot congelado, nao de um recalculo na leitura.
        for field in ("progression", "progression_note", "suggested_load_kg", "suggested_reps"):
            ex[field] = (shown or {}).get(field)


def _manual_rows_snapshots(
    conn,
    plan_id: int,
    rows: list[dict[str, Any]],
    *,
    user_id: int | None,
    safety_by_row: dict[int, Any],
    source: str,
    frozen_at: datetime,
) -> dict[int, dict[str, Any]]:
    """Snapshot das linhas que os Modos A/B nao cobrem (exercicio acrescentado
    a mao na ficha, ficha sem Modelo): `workout_plan_exercise_id` -> snapshot.

    M76 (Lote 1a):
    - C1: os indicadores (`suggested_load_kg`, `suggested_reps`, `progression`,
      `progression_note`) e a decisao vem do motor v2
      (`domain/prescription_service.suggest_for_pairs`) - acabou o motor legado
      (`suggest_load_and_reps`, +5%) que este caminho usava;
    - B1: o teto/alvo/falha NUNCA compoe com `READY` fixo. Com decisao, vale o
      que ela compos (seguranca do par + contrato de recuperacao PROJETADO
      para o instante do treino); sem decisao (linha sem exercicio do
      catalogo, ou falha do motor), `build_prescription` compoe com o contrato
      projetado do grupo PRIMARY. Linha manual em grupo REDUCED_DOSE nao sai
      mais com teto 10 / `failure_allowed=True`.

    O que a pessoa escreveu na linha (series, reps, carga, RPE) continua sendo
    o prescrito; o motor so informa e limita a intensidade."""
    from . import progression_state as ps
    from .domain.prescription_service import (
        PAIR_SUGGESTION_ENGINE_VERSION,
        PairQuery,
        legacy_indicators,
        suggest_for_pairs,
    )
    from .generation.freeze_mode_b import coherent_equipment_id, equipment_by_gym_equipment, projected_readiness

    if not rows:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, plan_date, plan_time FROM workout_plans WHERE id = %s", (int(plan_id),))
        plan = cur.fetchone() or {}
    owner_id = user_id if user_id is not None else plan.get("user_id")

    readiness: dict[int, Any] = {}
    if owner_id is not None:
        try:
            readiness = projected_readiness(int(owner_id), plan.get("plan_date"), plan.get("plan_time"))
        except Exception:
            # Sem projecao, a composicao cai no par (como o motor sem contrato).
            logger.exception("Falha ao projetar a recuperacao ao congelar linhas manuais do plano id=%s", plan_id)

    mapped = [row for row in rows if row.get("mapping_id")]
    suggestions: dict[int, Any] = {}
    if owner_id is not None and mapped:
        try:
            equipment_by_gym = equipment_by_gym_equipment(conn, [row.get("preferred_equipment_id") for row in mapped])
            logs = list_exercise_logs(limit=300, user_id=owner_id)
            # Uma chamada por alvo de RPE: o motor indexa por par, e o alvo da
            # linha entra na composicao do teto.
            by_rpe: dict[Any, list[tuple[int, PairQuery]]] = {}
            for row in mapped:
                query = PairQuery(
                    exercise_id=int(row["mapping_id"]),
                    equipment_id=coherent_equipment_id(conn, equipment_by_gym, row, int(plan_id)),
                    rpe_target=row.get("rpe_target"),
                    sets_count=row.get("sets_count"),
                    # M97 F1: trilha e fase do objetivo da linha (carimbado).
                    training_goal=goal_value(row.get("training_goal")),
                )
                # Uma chamada por (alvo de RPE, objetivo): o resultado e
                # indexado por par.
                by_rpe.setdefault((row.get("rpe_target"), query.training_goal), []).append(
                    (int(row["workout_plan_exercise_id"]), query)
                )
            for items in by_rpe.values():
                found = suggest_for_pairs(
                    conn, int(owner_id), [query for _, query in items], readiness=readiness, logs=logs, now=frozen_at
                )
                for row_id, query in items:
                    if query.key in found:
                        suggestions[row_id] = found[query.key]
        except Exception:
            logger.exception("Falha no motor v2 ao congelar linhas manuais do plano id=%s", plan_id)
            suggestions = {}

    primary_group: dict[int, int] = {}
    if any(int(row["workout_plan_exercise_id"]) not in suggestions for row in mapped):
        try:
            primary_group, _ = ps.load_primary_groups(conn, [int(row["mapping_id"]) for row in mapped])
        except Exception:
            logger.exception("Falha ao ler o grupo PRIMARY ao congelar o plano id=%s", plan_id)

    snapshots: dict[int, dict[str, Any]] = {}
    for row in rows:
        row_id = int(row["workout_plan_exercise_id"])
        suggestion = suggestions.get(row_id)
        if suggestion is not None:
            snapshots[row_id] = prescription_domain.build_prescription(
                {**row, **legacy_indicators(suggestion)},
                safety=safety_by_row.get(row_id),
                source=source,
                engine_version=PAIR_SUGGESTION_ENGINE_VERSION,
                frozen_at=frozen_at,
                decision=dict(suggestion.decision),
            )
            continue
        group_id = primary_group.get(int(row["mapping_id"])) if row.get("mapping_id") else None
        contract = readiness.get(int(group_id)) if group_id is not None else None
        snapshots[row_id] = prescription_domain.build_prescription(
            row,
            safety=safety_by_row.get(row_id),
            source=source,
            frozen_at=frozen_at,
            readiness=contract.readiness if contract is not None else None,
            recovery_signals=summarize_signals(contract.blocking_signals) if contract is not None else None,
        )
    return snapshots


def _saved_row_of(ex: dict[str, Any], existing_by_id: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """Linha salva DESTA ficha que o cliente identificou pelo id, ou None."""
    raw_id = ex.get("workout_plan_exercise_id")
    try:
        return existing_by_id.get(int(raw_id)) if raw_id is not None else None
    except (TypeError, ValueError):
        return None


def _assert_exercise_keeps_primary_group(cur, persisted_mapping_id: Any, new_mapping_id: Any, exercise_name: Any) -> None:
    """Troca de exercicio numa linha salva: o novo tem de ter o MESMO grupo
    PRIMARY do antigo (dono do grupo: `progression_state`)."""
    from . import progression_state as ps
    from . import workout_plan_edit_rules as edit_rules

    if persisted_mapping_id is None or new_mapping_id is None:
        return
    old_id = _resolve_canonical_exercise_id(int(persisted_mapping_id), cur)
    new_id = _resolve_canonical_exercise_id(int(new_mapping_id), cur)
    if old_id == new_id:
        return
    groups, _weights = ps.primary_groups_from_cursor(cur, [old_id, new_id])
    edit_rules.assert_same_primary_group(
        persisted_group_id=groups.get(int(old_id)),
        new_group_id=groups.get(int(new_id)),
        exercise_name=exercise_name,
    )


def list_exercise_swap_options(plan_id: int, user_id: int | None = None) -> dict[str, Any]:
    """`GET /workout-plans/{id}/exercise-swap-options`: por linha salva da
    ficha, os exercicios do catalogo ATIVO pelos quais ela pode ser trocada na
    edicao. Mesma leitura de grupo (`progression_state.primary_groups_from_cursor`),
    mesma resolucao canonica e mesmo predicado
    (`workout_plan_edit_rules.is_same_primary_group_swap`) da validacao do save
    (`_assert_exercise_keeps_primary_group`, 422): a tela so filtra por id.

    `exercise_ids = None` = troca sem restricao (linha sem exercicio vinculado
    ou cujo exercicio nao tem grupo PRIMARY: o servidor nao compara). Ficha
    finalizada devolve `rows = []` (os exercicios sao somente leitura)."""
    from . import progression_state as ps
    from . import workout_plan_edit_rules as edit_rules

    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT id, status FROM workout_plans WHERE id = %s AND user_id = %s", (int(plan_id), int(user_id))
                )
            else:
                cur.execute("SELECT id, status FROM workout_plans WHERE id = %s", (int(plan_id),))
            plan = cur.fetchone()
            if not plan:
                raise PlanNotFoundError(plan_id)
            if not edit_rules.is_plan_editable(plan.get("status")):
                return {"plan_id": int(plan_id), "editable": False, "rows": []}

            cur.execute(
                "SELECT id, mapping_id FROM workout_plan_exercises WHERE plan_id = %s ORDER BY sequence, id",
                (int(plan_id),),
            )
            plan_rows = cur.fetchall() or []
            cur.execute("SELECT id FROM exercise_catalog WHERE is_active = 1")
            candidate_ids = [int(row["id"]) for row in (cur.fetchall() or [])]
            # Mesma resolucao de `_resolve_canonical_exercise_id` (validacao do
            # save), numa leitura so para todas as linhas.
            canonical = equipment_pair.canonical_exercise_ids(cur, [row.get("mapping_id") for row in plan_rows])
            canonical_by_row = {
                int(row["id"]): canonical.get(int(row["mapping_id"])) if row.get("mapping_id") else None
                for row in plan_rows
            }
            groups, _weights = ps.primary_groups_from_cursor(
                cur, candidate_ids + [eid for eid in canonical_by_row.values() if eid is not None]
            )

    candidate_groups = {eid: groups.get(eid) for eid in candidate_ids}
    rows = []
    for row in plan_rows:
        canonical_id = canonical_by_row[int(row["id"])]
        group_id = groups.get(int(canonical_id)) if canonical_id is not None else None
        rows.append(
            {
                "workout_plan_exercise_id": int(row["id"]),
                "primary_group_id": group_id,
                "exercise_ids": edit_rules.eligible_swap_exercise_ids(
                    persisted_group_id=group_id, candidate_groups=candidate_groups
                ),
            }
        )
    return {"plan_id": int(plan_id), "editable": True, "rows": rows}


def _replace_plan_exercises(
    cur,
    plan_id: int,
    exercises: list[dict[str, Any]],
    gym_id: int | None = None,
    user_id: int | None = None,
    *,
    refreeze_ids: list[int] | None = None,
) -> list[int]:
    """Sincroniza workout_plan_exercises com a lista `exercises` recebida no
    save da ficha (create/update). Devolve os ids das linhas INSERIDAS agora -
    e so nelas que a prescricao e congelada (`_freeze_plan_prescriptions`);
    uma linha que ja existia preserva o snapshot que tem, que e exatamente o
    que torna `prescription_json` read-only para o cliente no PUT.

    IMPORTANTE: preserva o `id` das linhas existentes sempre que possível
    (UPDATE em vez de DELETE+INSERT), em vez de sempre recriar tudo. Motivo:
    `exercises.workout_plan_exercise_id` é uma FK real com
    ON DELETE SET NULL — o comportamento antigo (DELETE de todas as linhas do
    plano seguido de re-INSERT) desvinculava silenciosamente TODOS os
    exercícios já lançados que apontavam para essas linhas a cada save da
    ficha, mesmo edições triviais (nome, reordenação de campos, etc.) que não
    mudavam o conteúdo relevante daquele exercício.

    LIMITAÇÃO CONHECIDA: o casamento entre linha existente e linha nova é
    feito por `sequence` (não há outro identificador estável vindo do
    payload). Isso cobre o caso mais comum — editar a ficha sem reordenar os
    exercícios (trocar nome do plano, ajustar carga/reps-alvo de um
    exercício, etc.). Se o usuário REORDENAR exercícios via drag-and-drop
    (`/planos-treino`, PR#53) de forma que o mesmo exercício passe a ocupar
    outro `sequence`, esta função ainda vai tratar a sequence antiga como
    "saiu" e a nova posição como "entrou", gerando um `id` novo para aquele
    exercício e perdendo o vínculo específico dele. Resolver isso de forma
    robusta exigiria o frontend mandar de volta o `id` de cada exercício no
    payload de save — fora do escopo desta correção.

    Edição da ficha (2026-10-24, `workout_plan_edit_rules`): a linha que chega
    com `workout_plan_exercise_id` de uma linha DESTA ficha tem o grupamento
    muscular travado (texto e grupo PRIMARY do exercício; 422 se mudar). Linha
    existente que mudou de exercício, equipamento ou técnica entra em
    `refreeze_ids` (quando passado): o caller re-congela a prescrição dela,
    porque o snapshot antigo descreve outro par.
    """
    from . import workout_plan_edit_rules as edit_rules

    # Linhas existentes por sequence, com id, para decidir UPDATE vs INSERT e
    # para saber se um exercise_name foi editado. Um mapping_id/
    # preferred_equipment_id explícito ecoado pelo cliente é confiável em
    # DOIS casos: (1) criação nova daquela sequence (existing_row is None —
    # primeira vez que o exercício é salvo, valor já veio resolvido
    # corretamente da geração/edição) e (2) edição sem troca de exercise_name.
    # Só é descartado (forçando resolve_mapping_id a cair no matching fuzzy)
    # quando existing_row existe E o exercise_name mudou, pois nesse caso o
    # id antigo pode não corresponder mais ao novo nome digitado.
    cur.execute(
        "SELECT id, sequence, exercise_name, mapping_id, preferred_equipment_id, muscle_primary, "
        f"{', '.join(ADVANCED_EXERCISE_FIELDS)} "
        "FROM workout_plan_exercises WHERE plan_id = %s",
        (plan_id,),
    )
    existing_rows = cur.fetchall()
    existing_by_sequence = {row["sequence"]: row for row in existing_rows}
    # Identidade estavel da linha salva (o cliente ecoa o id): so serve para a
    # trava do grupamento - o UPDATE continua casando por sequence.
    existing_by_id = {int(row["id"]): row for row in existing_rows if row.get("id") is not None}

    new_sequences = {ex["sequence"] for ex in exercises}
    obsolete_sequences = [seq for seq in existing_by_sequence if seq not in new_sequences]
    if obsolete_sequences:
        placeholders = ", ".join(["%s"] * len(obsolete_sequences))
        cur.execute(
            f"DELETE FROM workout_plan_exercises WHERE plan_id = %s AND sequence IN ({placeholders})",
            (plan_id, *obsolete_sequences),
        )

    inserted_ids: list[int] = []
    for ex in exercises:
        existing_row = existing_by_sequence.get(ex["sequence"])
        # Campo avançado ausente do dict (cliente não enviou — o PUT serializa
        # exercícios com exclude_unset) mantém o valor já persistido; null
        # explícito continua limpando. Casamento por sequence, mesma
        # limitação descrita na docstring.
        ex = merge_unsent_advanced_fields(ex, existing_row)
        name_unchanged = existing_row is not None and existing_row["exercise_name"] == ex["exercise_name"]
        name_changed_on_edit = existing_row is not None and not name_unchanged

        # Auto-resolve o vínculo com o catálogo canônico e com o equipamento da
        # academia quando o cliente não enviou os ids (planos gerados via IA
        # trazem apenas texto livre) ou quando exercise_name mudou numa edição
        # (um mapping_id/preferred_equipment_id explícito só é confiável se
        # ainda se refere ao mesmo exercício). Numa criação nova (existing_row
        # is None) ou numa edição sem troca de nome, o valor explícito do
        # payload é confiável e deve ser repassado. Sem gym_id, o equipamento
        # fica NULL.
        explicit_mapping_id = None if name_changed_on_edit else ex.get("mapping_id")
        mapping_id = resolve_mapping_id(ex["exercise_name"], ex.get("amazfit_name"), explicit_mapping_id, cur=cur)

        saved_row = _saved_row_of(ex, existing_by_id)
        if saved_row is not None:
            edit_rules.assert_muscle_group_text_unchanged(
                persisted_group=saved_row.get("muscle_primary"),
                incoming_group=ex.get("muscle_primary"),
                exercise_name=ex["exercise_name"],
            )
            _assert_exercise_keeps_primary_group(cur, saved_row.get("mapping_id"), mapping_id, ex["exercise_name"])

        preferred_equipment_id = None if name_changed_on_edit else ex.get("preferred_equipment_id")
        if not preferred_equipment_id and gym_id and mapping_id:
            preferred_equipment_id = resolve_equipment_id(
                cur, mapping_id, gym_id, None, ex.get("equipment_name"), user_id=user_id
            )
        # INVARIANTE DA D13 na escrita: o `gym_equipment` gravado tem de servir
        # o exercicio DESTA linha. Um par incoerente e sempre erro de quem o
        # montou (motor ou cliente ecoando um id velho), nunca dado legitimo -
        # a alternativa de gravar assim mesmo foi o bug da ficha 112: "Remada
        # curvada no orbital row" com foto e etiqueta de "Estacao de polia".
        # Aqui degradamos com auditoria (vinculo limpo -> o congelamento marca
        # `requires_user_choice`); quem quiser falhar alto usa
        # `equipment_pair.assert_pair_coherent`.
        if preferred_equipment_id and mapping_id and not is_pair_coherent(cur, mapping_id, preferred_equipment_id):
            logger.error(
                "D13: par incoerente descartado no save da ficha plan_id=%s sequence=%s "
                "mapping_id=%s gym_equipment_id=%s (item volta a exigir escolha do usuario)",
                plan_id,
                ex.get("sequence"),
                mapping_id,
                preferred_equipment_id,
            )
            preferred_equipment_id = None

        values = (
            ex["exercise_name"],
            ex.get("amazfit_name"),
            mapping_id,
            ex.get("muscle_primary"),
            ex.get("muscle_secondary"),
            ex.get("equipment_name"),
            preferred_equipment_id,
            ex.get("sets_count"),
            ex.get("reps_target"),
            ex.get("load_target"),
            ex.get("rpe_target"),
            ex.get("rest_seconds"),
            bool(ex.get("warmup", False)),
            to_json(ex.get("substitution_options")),
            ex.get("notes"),
            # Espelho puro dos 10 campos de técnica avançada/aquecimento
            # configurado/ajuste/pico de contração (modo 'equipment_series')
            # — sem recalcular nada aqui, mesmo padrão dos demais campos
            # opcionais desta função.
            ex.get("advanced_technique"),
            ex.get("advanced_technique_applications"),
            ex.get("advanced_technique_duration_seconds"),
            ex.get("warmup_sets_count"),
            ex.get("warmup_reps"),
            ex.get("warmup_rest_seconds"),
            ex.get("feeler_set_sets_count"),
            ex.get("feeler_set_reps"),
            ex.get("feeler_set_rest_seconds"),
            ex.get("peak_contraction_seconds"),
            # Medida da série (Lote T2, 089): `measure` é NOT NULL - ausente/
            # None vira `reps` pela leitura do dono.
            normalize_measure(ex.get("measure")).value,
            ex.get("duration_seconds_target"),
            ex.get("time_window_seconds"),
            # Objetivo de treino (M97 F0, 093): o resolvido na geracao e ecoado
            # pelo cliente; ausente/desconhecido = default, pela leitura do dono.
            goal_value(ex.get("training_goal")),
        )

        if existing_row is not None:
            if refreeze_ids is not None and edit_rules.refreeze_needed(
                existing_row,
                {
                    "mapping_id": mapping_id,
                    "preferred_equipment_id": preferred_equipment_id,
                    "advanced_technique": ex.get("advanced_technique"),
                },
            ):
                refreeze_ids.append(int(existing_row["id"]))
            cur.execute(
                """
                UPDATE workout_plan_exercises
                SET exercise_name = %s, amazfit_name = %s, mapping_id = %s, muscle_primary = %s,
                    muscle_secondary = %s, equipment_name = %s, preferred_equipment_id = %s,
                    sets_count = %s, reps_target = %s, load_target = %s, rpe_target = %s,
                    rest_seconds = %s, warmup = %s, substitution_options = %s, notes = %s,
                    advanced_technique = %s, advanced_technique_applications = %s,
                    advanced_technique_duration_seconds = %s, warmup_sets_count = %s, warmup_reps = %s,
                    warmup_rest_seconds = %s, feeler_set_sets_count = %s, feeler_set_reps = %s,
                    feeler_set_rest_seconds = %s, peak_contraction_seconds = %s,
                    measure = %s, duration_seconds_target = %s, time_window_seconds = %s,
                    training_goal = %s
                WHERE id = %s
                """,
                (*values, existing_row["id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO workout_plan_exercises (
                    plan_id, sequence, exercise_name, amazfit_name, mapping_id, muscle_primary,
                    muscle_secondary, equipment_name, preferred_equipment_id, sets_count, reps_target,
                    load_target, rpe_target, rest_seconds, warmup, substitution_options, notes,
                    advanced_technique, advanced_technique_applications, advanced_technique_duration_seconds,
                    warmup_sets_count, warmup_reps, warmup_rest_seconds,
                    feeler_set_sets_count, feeler_set_reps, feeler_set_rest_seconds,
                    peak_contraction_seconds, measure, duration_seconds_target, time_window_seconds,
                    training_goal
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (plan_id, ex["sequence"], *values),
            )
            inserted_ids.append(cur.lastrowid)

    return inserted_ids


# Colunas que o congelamento LE de cada linha da ficha (o que foi
# efetivamente prescrito) para montar o snapshot.
_FREEZE_COLUMNS = (
    "id AS workout_plan_exercise_id, sequence, exercise_name, mapping_id, equipment_name, "
    "preferred_equipment_id, sets_count, reps_target, load_target, rpe_target, advanced_technique, "
    "advanced_technique_applications, advanced_technique_duration_seconds, "
    "measure, duration_seconds_target, time_window_seconds, training_goal"
)


def _freeze_plan_prescriptions(
    conn,
    plan_id: int,
    *,
    user_id: int | None,
    plan_type: str | None,
    exercise_ids: list[int] | None = None,
    source: str = prescription_domain.SOURCE_PLAN_CREATED,
    substitution_by_row: dict[int, dict[str, Any]] | None = None,
) -> int:
    """Congela a prescricao das linhas indicadas (`None` = todas as do plano).

    `substitution_by_row` (M8): `workout_plan_exercise_id` -> bloco auditavel
    de uma troca de equipamento feita pelo usuario na ficha
    (`generation/exercise_substitution.equipment_substitution_audit`). E
    aplicado por cima do snapshot re-derivado - qualquer que seja a origem
    dele (Modo A, Modo B ou legado) - com `WARNING_EQUIPMENT_SUBSTITUTED`.

    A FONTE da prescricao e o Modo B/A deterministico (fichas de Modelo) e,
    para as linhas que eles nao cobrem, o motor v2 por par
    (`_manual_rows_snapshots`, M76 Lote 1a - antes, o motor legado). Roda UMA
    vez e o resultado fica gravado: e por isso que abrir o plano duas vezes
    devolve a mesma prescricao. `engine_version` e `source` gravados aqui dizem
    de onde veio cada snapshot, e o contrato do JSON nao muda. `plan_type` nao
    participa mais (era a bifurcacao ABC x Full Body do motor legado).

    Devolve quantas linhas foram congeladas."""
    where = "plan_id = %s"
    params: list[Any] = [plan_id]
    if exercise_ids is not None:
        if not exercise_ids:
            return 0
        where += " AND id IN (" + ", ".join(["%s"] * len(exercise_ids)) + ")"
        params += list(exercise_ids)

    with conn.cursor() as cur:
        cur.execute(f"SELECT {_FREEZE_COLUMNS} FROM workout_plan_exercises WHERE {where} ORDER BY sequence", tuple(params))
        rows = cur.fetchall() or []
    if not rows:
        return 0

    # M97 F1: o objetivo de cada linha e carimbado PELO SERVIDOR a partir do
    # item do Modelo (override > Modelo > default; `plan_goal_stamp`), antes do
    # snapshot e da progressao lerem a linha - o cliente nao precisa ecoar o
    # campo. Falha de leitura nao derruba o save: a linha fica como estava.
    try:
        from .generation.plan_goal_stamp import stamp_plan_row_goals

        rows = stamp_plan_row_goals(conn, plan_id, rows)
    except Exception:
        logger.exception("Falha ao carimbar o objetivo de treino das linhas do plano id=%s", plan_id)

    try:
        safety_by_row = _safety_by_exercise_row(conn, rows)
    except Exception:
        logger.exception("Falha ao resolver seguranca ao congelar a prescricao do plano id=%s", plan_id)
        safety_by_row = {}

    frozen_at = datetime.now()

    # PR-12: com `GENERATION_ENGINE=deterministic` (+ `PROGRESSION_ENGINE=v2`)
    # e ficha de um modelo `equipment_series`, a ORIGEM da prescricao passa a
    # ser o Modo B deterministico. O contrato do snapshot nao muda - so de
    # onde vem cada valor. Qualquer falha cai no caminho legado: uma ficha
    # sem prescricao seria pior que uma prescricao legada.
    # PR-13: o mesmo vale para o Modo A (`percent_budget`). Os dois modos sao
    # mutuamente exclusivos por `volume_mode` do modelo - cada `freeze_*`
    # devolve `None` quando a ficha nao e dele -, entao a ordem aqui nao
    # importa e nunca ha duas origens para a mesma linha.
    deterministic: dict[int, dict[str, Any]] | None = None
    try:
        from .generation.freeze_mode_a import freeze_mode_a
        from .generation.freeze_mode_b import freeze_mode_b

        for freeze in (freeze_mode_b, freeze_mode_a):
            deterministic = freeze(
                conn, plan_id, rows, user_id=user_id, safety_by_row=safety_by_row, source=source, frozen_at=frozen_at
            )
            if deterministic is not None:
                break
    except Exception:
        logger.exception("Falha no congelamento deterministico do plano id=%s; usando o caminho legado", plan_id)
        deterministic = None

    # M100: linha de exercicio LABORAL nao passa por motor nenhum - series,
    # reps e carga sao o padrao do catalogo (`domain/laboral.py`), gravados na
    # linha e no snapshot. Vence qualquer outra origem.
    try:
        laboral = _laboral_rows_snapshots(conn, rows, safety_by_row=safety_by_row, source=source, frozen_at=frozen_at)
    except Exception:
        logger.exception("Falha ao congelar o padrao laboral do plano id=%s", plan_id)
        laboral = {}

    # Linhas que os Modos A/B nao cobriram (acrescentadas a mao, ficha sem
    # Modelo): motor v2 por par + contrato de recuperacao projetado (M76 Lote
    # 1a, C1 e B1). Nunca o motor legado, nunca `READY` fixo.
    manual_rows = [
        row
        for row in rows
        if int(row["workout_plan_exercise_id"]) not in (deterministic or {})
        and int(row["workout_plan_exercise_id"]) not in laboral
    ]
    manual = _manual_rows_snapshots(
        conn, plan_id, manual_rows, user_id=user_id, safety_by_row=safety_by_row, source=source, frozen_at=frozen_at
    )

    with conn.cursor() as cur:
        for row in rows:
            row_id = int(row["workout_plan_exercise_id"])
            snapshot = laboral.get(row_id) or (deterministic or {}).get(row_id) or manual[row_id]
            if substitution_by_row and row_id in substitution_by_row:
                snapshot = prescription_domain.with_substitution(snapshot, substitution_by_row[row_id])
            cur.execute(
                "UPDATE workout_plan_exercises SET prescription_json = %s, engine_version = %s, "
                "requires_user_choice = %s WHERE id = %s",
                (to_json(snapshot), snapshot["engine_version"], int(snapshot["requires_user_choice"]), row_id),
            )
    return len(rows)


def _laboral_rows_snapshots(
    conn,
    rows: list[dict[str, Any]],
    *,
    safety_by_row: dict[int, Any],
    source: str,
    frozen_at: datetime,
) -> dict[int, dict[str, Any]]:
    """M100: `workout_plan_exercise_id` -> snapshot das linhas de exercicio
    LABORAL. Series, reps e carga vem do padrao do catalogo
    (`domain/laboral.laboral_prescription`: catalogo > linha > fallback) e sao
    gravados na propria linha (`sets_count`, `reps_target`, `load_target`) -
    sem progressao, tecnica ou alvo de RPE."""
    from .laboral_catalog import load_laboral_defaults
    from .workout_plan_ai import normalize_load_target

    defaults_by_exercise = load_laboral_defaults(conn, (row.get("mapping_id") for row in rows))
    if not defaults_by_exercise:
        return {}
    snapshots: dict[int, dict[str, Any]] = {}
    with conn.cursor() as cur:
        for row in rows:
            mapping_id = row.get("mapping_id")
            if not mapping_id or int(mapping_id) not in defaults_by_exercise:
                continue
            row_id = int(row["workout_plan_exercise_id"])
            defaults = defaults_by_exercise[int(mapping_id)]
            fields = laboral_domain.laboral_row_fields(defaults, row, normalize_load_target)
            cur.execute(
                "UPDATE workout_plan_exercises SET sets_count = %s, reps_target = %s, load_target = %s, "
                "advanced_technique = NULL WHERE id = %s",
                (fields["sets_count"], fields["reps_target"], fields["load_target"], row_id),
            )
            exercise = {**row, **fields}
            snapshot = prescription_domain.build_prescription(
                exercise,
                safety=safety_by_row.get(row_id),
                source=source,
                engine_version=laboral_domain.LABORAL_ENGINE_VERSION,
                frozen_at=frozen_at,
            )
            snapshot["laboral"] = laboral_domain.laboral_prescription(defaults, row).to_dict()
            snapshots[row_id] = snapshot
    return snapshots


def sync_load_target_from_frozen_suggestion(conn, workout_plan_exercise_id: int) -> None:
    """Grava em `load_target` a carga que o motor congelou para o par da linha
    (`prescription_json.suggested_load_kg`), no formato da geracao (Modo B:
    `normalize_load_target(prescription.suggested_load_kg)`). Sem sugestao
    (par sem historico), a carga fica vazia - nunca a do equipamento antigo.
    Usado pela troca de equipamento na ficha (M8)."""
    from .workout_plan_ai import normalize_load_target

    with conn.cursor() as cur:
        cur.execute(
            "SELECT prescription_json FROM workout_plan_exercises WHERE id = %s", (int(workout_plan_exercise_id),)
        )
        row = cur.fetchone() or {}
        snapshot = from_json_dict(row.get("prescription_json")) or {}
        load_target = normalize_load_target(snapshot.get("suggested_load_kg"))
        cur.execute(
            "UPDATE workout_plan_exercises SET load_target = %s WHERE id = %s",
            (load_target, int(workout_plan_exercise_id)),
        )


VERSION_OF_TEMPLATE_SQL = (
    "SELECT id FROM workout_plan_template_versions WHERE id = %s AND template_id = %s"
)


def _validated_template_version_id(
    cur: Any, template_id: Any, template_version_id: Any, *, user_id: Any, plan_id: Any = None
) -> int | None:
    """`template_version_id` a gravar na ficha (066), ou `None`.

    NAO confia no cliente: a versao so e aceita se for DO `template_id` da
    mesma ficha (1 SELECT, e so quando o cliente mandou uma versao). Sem
    Modelo, sem versao ou versao de outro Modelo -> `None`, e o caso suspeito
    (versao informada que nao pertence ao Modelo) vira um evento de log
    estruturado - a ficha e salva do mesmo jeito, so sem a versao, e o
    congelamento cai na versao vigente, como numa ficha antiga."""
    if template_version_id in (None, "") or template_id in (None, ""):
        return None
    try:
        version_id = int(template_version_id)
        owner_template = int(template_id)
    except (TypeError, ValueError):
        version_id, owner_template = None, None
    if version_id is not None:
        cur.execute(VERSION_OF_TEMPLATE_SQL, (version_id, owner_template))
        if cur.fetchone():
            return version_id
    logger.warning(
        json.dumps(
            {
                "event": "workout_plan.template_version_rejected",
                "user_id": user_id,
                "plan_id": plan_id,
                "template_id": template_id,
                "template_version_id": template_version_id,
                "reason": "version_not_of_template",
            },
            default=str,
        )
    )
    return None


def create_workout_plan(payload: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    """`user_id` (Multiusuario Parte 4): dono do plano, sempre resolvido a
    partir de `identity.user_id` no router - nunca aceito do payload."""
    with transaction() as conn:
        with conn.cursor() as cur:
            gym_id = resolve_gym_id(cur, payload.get("gym_id"), payload.get("gym_name"))
            template_version_id = _validated_template_version_id(
                cur, payload.get("template_id"), payload.get("template_version_id"), user_id=user_id
            )
            cur.execute(
                """
                INSERT INTO workout_plans (
                    user_id, plan_date, plan_time, gym_id, name, plan_type, split_day, goal, status,
                    raw_payload, template_id, template_slot_sequence, template_version_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    payload.get("plan_date"),
                    payload.get("plan_time"),
                    gym_id,
                    payload["name"],
                    payload.get("plan_type", "full_body"),
                    payload.get("split_day"),
                    payload.get("goal"),
                    payload.get("status", "planned"),
                    to_json(payload.get("raw_payload")),
                    payload.get("template_id"),
                    payload.get("template_slot_sequence"),
                    template_version_id,
                ),
            )
            plan_id = cur.lastrowid
            inserted_ids = _replace_plan_exercises(
                cur, plan_id, payload.get("exercises") or [], gym_id, user_id=user_id
            )
        # M16: a ficha acabou de nascer de um rascunho gerado - marca qual
        # linha de `generation_runs` a originou (1 UPDATE, e so quando o
        # `raw_payload` traz `generation_audit`). Import tardio e excecao
        # engolida la dentro, no padrao de `_attach_volume_coverage_warnings`:
        # a ligacao e auditoria, e auditoria nunca derruba um save bom.
        from .generation.generation_runs import link_run_to_plan

        link_run_to_plan(conn, user_id=user_id, plan_id=plan_id, raw_payload=payload.get("raw_payload"))
        # Congelamento (secao 6.3): a prescricao e gravada AQUI, na criacao da
        # ficha, e nunca mais recalculada na leitura.
        _freeze_plan_prescriptions(
            conn, plan_id, user_id=user_id, plan_type=payload.get("plan_type"), exercise_ids=inserted_ids
        )

    return get_workout_plan(plan_id, user_id=user_id)


def _same_id(a: Any, b: Any) -> bool:
    try:
        return a is not None and b is not None and int(a) == int(b)
    except (TypeError, ValueError):
        return False


def update_workout_plan(plan_id: int, payload: dict[str, Any], user_id: int | None = None) -> dict[str, Any] | None:
    """`PUT /workout-plans/{id}`. `payload["exercises"] is None` (cliente nao
    mandou a lista) = os exercicios nao sao tocados. Mandar exercicios para
    uma ficha finalizada levanta `workout_plan_edit_rules.PlanEditError` (409):
    o status GRAVADO decide, entao reabrir a ficha (status -> planned) e um
    save so do cabecalho, e editar os exercicios vem no save seguinte."""
    from . import workout_plan_edit_rules as edit_rules

    exercises = payload.get("exercises")
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT id, user_id, status, template_id, template_version_id FROM workout_plans "
                    "WHERE id = %s AND user_id = %s",
                    (plan_id, user_id),
                )
            else:
                cur.execute(
                    "SELECT id, user_id, status, template_id, template_version_id FROM workout_plans WHERE id = %s",
                    (plan_id,),
                )
            plan_row = cur.fetchone()
            if not plan_row:
                return None
            if exercises is not None:
                edit_rules.assert_plan_editable(plan_row.get("status"))
            # Ranking de equipamento (estrelas/recencia) e sempre o do DONO da ficha.
            owner_user_id = plan_row.get("user_id")

            gym_id = resolve_gym_id(cur, payload.get("gym_id"), payload.get("gym_name"))
            # 066: editar a ficha NAO troca a versao de que ela nasceu. Cliente
            # que nao manda a versao (tela antiga) com o MESMO Modelo mantem a
            # gravada; versao enviada e validada no servidor como no create.
            if payload.get("template_version_id") is None and _same_id(
                payload.get("template_id"), plan_row.get("template_id")
            ):
                template_version_id = plan_row.get("template_version_id")
            else:
                template_version_id = _validated_template_version_id(
                    cur,
                    payload.get("template_id"),
                    payload.get("template_version_id"),
                    user_id=owner_user_id,
                    plan_id=plan_id,
                )
            cur.execute(
                """
                UPDATE workout_plans
                SET plan_date = %s, plan_time = %s, gym_id = %s, name = %s, plan_type = %s, split_day = %s,
                    goal = %s, status = %s, raw_payload = %s, template_id = %s, template_slot_sequence = %s,
                    template_version_id = %s
                WHERE id = %s
                """,
                (
                    payload.get("plan_date"),
                    payload.get("plan_time"),
                    gym_id,
                    payload["name"],
                    payload.get("plan_type", "full_body"),
                    payload.get("split_day"),
                    payload.get("goal"),
                    payload.get("status", "planned"),
                    to_json(payload.get("raw_payload")),
                    payload.get("template_id"),
                    payload.get("template_slot_sequence"),
                    template_version_id,
                    plan_id,
                ),
            )
            refreeze_ids: list[int] = []
            inserted_ids = (
                _replace_plan_exercises(
                    cur, plan_id, exercises, gym_id, user_id=owner_user_id, refreeze_ids=refreeze_ids
                )
                if exercises is not None
                else []
            )
        # `prescription_json` e READ-ONLY para o cliente: o UPDATE acima nem
        # toca na coluna, entao o snapshot das linhas que ja existiam e
        # preservado (bug de classe G1, corrigido no PR-03 para os campos
        # avancados). So exercicio NOVO nesta edicao recebe prescricao - e a
        # linha que trocou de exercicio, equipamento ou tecnica, cujo snapshot
        # descrevia outro par (`workout_plan_edit_rules.REFREEZE_ON_CHANGE_FIELDS`).
        _freeze_plan_prescriptions(
            conn, plan_id, user_id=owner_user_id, plan_type=payload.get("plan_type"), exercise_ids=inserted_ids
        )
        _freeze_plan_prescriptions(
            conn,
            plan_id,
            user_id=owner_user_id,
            plan_type=payload.get("plan_type"),
            exercise_ids=refreeze_ids,
            source=prescription_domain.SOURCE_REFRESH,
        )

    return get_workout_plan(plan_id, user_id=user_id)


class PlanNotFoundError(Exception):
    """Ficha inexistente ou de outro usuario (o router traduz para 404)."""

    def __init__(self, plan_id: int):
        super().__init__(f"Plano de treino {plan_id} não encontrado.")
        self.plan_id = plan_id


class PlanPrescriptionRefreshError(Exception):
    """Estado da ficha impede o refresh (o router traduz para 409)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def assert_plan_prescription_refreshable(plan_row: dict[str, Any], *, require_future_date: bool = True) -> None:
    """Condicoes para RE-prescrever uma ficha (secao 6.3): `status = 'planned'`
    (ficha nao finalizada, `workout_plan_edit_rules.is_plan_editable`) e
    `plan_date >= hoje`. Fora delas levanta `PlanPrescriptionRefreshError`
    (409 no router). Compartilhada pelo `refresh-prescription` e pela troca de
    equipamento na ficha (M8), que tambem recongela a linha.

    `require_future_date=False` (troca de equipamento, 2026-10-24): a edicao
    vale para toda ficha nao finalizada, e uma ficha `planned` de data passada
    nunca foi executada (fechar a sessao marca `done`) - nao ha historico a
    reescrever. O refresh da ficha inteira continua exigindo a data."""
    from .workout_plan_edit_rules import is_plan_editable

    if not is_plan_editable(plan_row.get("status")):
        raise PlanPrescriptionRefreshError(
            "plan_not_planned",
            "Só é possível atualizar a prescrição de uma ficha ainda planejada. "
            f"Esta ficha está com status '{plan_row.get('status')}'.",
        )
    if not require_future_date:
        return
    plan_date = plan_row.get("plan_date")
    if isinstance(plan_date, datetime):
        plan_date = plan_date.date()
    if plan_date is not None and plan_date < date.today():
        raise PlanPrescriptionRefreshError(
            "plan_date_in_past",
            "Só é possível atualizar a prescrição de uma ficha com data de hoje em diante. "
            f"Esta ficha é de {plan_date.isoformat()}.",
        )


def refresh_plan_prescription(plan_id: int, user_id: int | None = None) -> dict[str, Any]:
    """`POST /workout-plans/{id}/refresh-prescription` (secao 6.3): recalcula
    e RECONGELA a prescricao de todos os exercicios da ficha.

    Condicoes, ambas obrigatorias: `status = 'planned'` e `plan_date >= hoje`.
    Fora delas a resposta e **409 Conflict** (nunca 422): o pedido esta
    perfeitamente bem formado - o que impede a operacao e o ESTADO atual da
    ficha (ja executada, ou de uma data que ja passou), e represcrever um
    treino que ja aconteceu reescreveria historico.

    Devolve `{"plan": <ficha ja com a prescricao nova>, "refreshed": n}` ou
    levanta `PlanPrescriptionRefreshError` com `reason` legivel."""
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT id, status, plan_date, plan_type, user_id FROM workout_plans WHERE id = %s AND user_id = %s",
                    (plan_id, user_id),
                )
            else:
                cur.execute(
                    "SELECT id, status, plan_date, plan_type, user_id FROM workout_plans WHERE id = %s", (plan_id,)
                )
            plan_row = cur.fetchone()
        if not plan_row:
            raise PlanNotFoundError(plan_id)

        assert_plan_prescription_refreshable(plan_row)

        refreshed = _freeze_plan_prescriptions(
            conn,
            plan_id,
            user_id=plan_row.get("user_id"),
            plan_type=plan_row.get("plan_type"),
            source=prescription_domain.SOURCE_REFRESH,
        )

    return {"plan": get_workout_plan(plan_id, user_id=user_id), "refreshed": refreshed}


def delete_workout_plan(plan_id: int, user_id: int | None = None) -> bool:
    with transaction() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute("DELETE FROM workout_plans WHERE id = %s AND user_id = %s", (plan_id, user_id))
            else:
                cur.execute("DELETE FROM workout_plans WHERE id = %s", (plan_id,))
            return cur.rowcount > 0


def _find_or_create_muscle_group_id(cur, name: str) -> int:
    """Resolve nome -> muscle_groups.id, criando a linha se não existir.

    Mesma lógica find-or-create usada em
    app/exercise_mapping_catalog.py::_find_or_create_muscle_group — o
    curador aqui também está escolhendo/digitando o grupo deliberadamente
    pela UI, não uma inferência automática de IA.
    """
    cur.execute("SELECT id FROM muscle_groups WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]

    cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (name,))
    return cur.lastrowid


def upsert_muscle_group_body_map(muscle_group: str, slugs: list[str]) -> dict[str, Any]:
    slugs_value = ",".join(slug.strip() for slug in slugs if slug.strip())

    with transaction() as conn:
        with conn.cursor() as cur:
            muscle_group_id = _find_or_create_muscle_group_id(cur, muscle_group)
            cur.execute(
                """
                INSERT INTO muscle_group_body_map (muscle_group_id, slugs)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE slugs = VALUES(slugs)
                """,
                (muscle_group_id, slugs_value),
            )

    return {"muscle_group": muscle_group, "slugs": slugs_value.split(",")}
