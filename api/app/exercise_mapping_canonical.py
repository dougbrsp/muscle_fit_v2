import logging
import re
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from .security import get_current_identity, require_admin

logger = logging.getLogger("fitness.exercise_mapping_canonical")

router = APIRouter(prefix="/mapping-canonical", tags=["mapping-canonical"], dependencies=[Depends(get_current_identity)])


def fix_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value
    for _ in range(4):
        if not any(marker in text for marker in ("Ã", "Â", "â€", "�")):
            break
        changed = False
        for encoding in ("latin1", "cp1252"):
            try:
                candidate = text.encode(encoding).decode("utf-8")
                if candidate and candidate != text:
                    text = candidate
                    changed = True
                    break
            except Exception:
                pass
        if not changed:
            break

    return (
        text.replace("â€“", "–")
        .replace("â€”", "—")
        .replace("â€˜", "‘")
        .replace("â€™", "’")
        .replace("â€œ", "“")
        .replace("â€", "”")
        .replace("Âº", "º")
        .replace("Âª", "ª")
        .replace("Â", "")
    )


def fix_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: fix_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [fix_obj(item) for item in value]
    return fix_text(value)


def normalize(value: Any) -> str:
    value = fix_text(value)
    if value is None:
        return ""

    text = unicodedata.normalize("NFKD", str(value).strip().lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def q(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def connect():
    # Portado para RDS Data API via app/db.py (era pymysql/mysql.connector
    # direto) - ver docs/aws-migration-plan.md, Fase 3, item 1.
    from .db import get_conn

    return get_conn()


def query(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if cur.description is None:
            try:
                cur.close()
            except Exception:
                pass
            return []

        raw = cur.fetchall() or []
        if raw and not isinstance(raw[0], dict):
            names = [desc[0] for desc in cur.description]
            raw = [dict(zip(names, row)) for row in raw]

        try:
            cur.close()
        except Exception:
            pass

        return fix_obj(raw)


def execute(sql: str, params: Tuple[Any, ...] = ()) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        count = getattr(cur, "rowcount", 0) or 0
        try:
            cur.close()
        except Exception:
            pass
        return count


def table_columns(table: str) -> List[str]:
    try:
        return [row.get("Field") or row.get("field") for row in query(f"SHOW COLUMNS FROM {q(table)}")]
    except Exception:
        return []


def first_existing(columns: List[str], names: List[str]) -> Optional[str]:
    lookup = {str(col).lower(): str(col) for col in columns if col}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def split_values(value: Any) -> List[str]:
    if value is None:
        return []
    return [part.strip() for part in re.split(r"[,;/|]+", str(fix_text(value))) if part.strip()]


# exercises.mapping_id referencia exercise_catalog.id (fonte de verdade
# canonica pos-reestruturacao, 2026-08 - antes exercise_zepp_mappings.id).
# Todo o restante da classificação (nome canônico, grupos, músculos) é lido
# via JOIN a partir da tabela de origem, nunca duplicado em exercises.
#
# Schema: a coluna e o indice `idx_exercises_mapping_id` vem de
# 001_schema.sql (baseline) e sao garantidos pela migracao
# 024_exercises_mapping_id_leitura_sem_escrita.py. Nao ha mais DDL em runtime
# (o antigo ensure_canonical_columns() rodava CREATE INDEX a cada GET das
# rotas de grupos musculares - DDL faz commit implicito no MySQL).

QueryFn = Callable[..., List[Dict[str, Any]]]
ExecuteFn = Callable[..., Any]

# Pagina de `exercises` lida por reclassify_exercises: so (id, original_name,
# mapping_id) por linha, bem abaixo do limite de 1 MiB por resultado da RDS
# Data API.
RECLASSIFY_PAGE_SIZE = 1000

# Teto de exercicios sem vinculo reprocessados apos uma escrita no catalogo -
# o mesmo que as rotas GET usavam quando reclassificavam na leitura.
MISSING_RECLASSIFY_LIMIT = 20000

# Teto de exercicios LIDOS por POST /mapping-canonical/associate (o mesmo de
# antes). So os do escopo do alias sao reprocessados - ver `names` em
# reclassify_exercises.
ASSOCIATE_RECLASSIFY_LIMIT = 100000

# Criterio unico de "sem vinculo valido": `mapping_id` nulo OU que nao resolve
# (direto ou via exercise_legacy_id_map) para um exercise_catalog ATIVO. Usado
# por GET /mapping-canonical/unmapped e pelo escopo do associate.
UNRESOLVED_LINK_JOINS = """
    LEFT JOIN `exercise_legacy_id_map` elm ON elm.old_exercise_id = e.mapping_id
    LEFT JOIN `exercise_catalog` ec ON ec.id = COALESCE(elm.canonical_exercise_id, e.mapping_id) AND ec.is_active = 1
"""
UNRESOLVED_LINK_CONDITION = "(e.mapping_id IS NULL OR ec.id IS NULL)"


def exercise_schema() -> Dict[str, Any]:
    cols = table_columns("exercises")

    return {
        "id": first_existing(cols, ["id", "exercise_id"]),
        "original_name": first_existing(cols, ["original_name", "name", "exercise_name", "source_name", "display_name", "nome"]),
        "columns": cols,
    }


def load_mapping_rows(query_fn: Optional[QueryFn] = None) -> List[Dict[str, Any]]:
    """Carrega o catalogo canonico ATIVO (`exercise_catalog`, fonte de
    verdade pos-reestruturacao, 2026-08) com material de matching:
    `exercise_aliases.alias` (419 aliases cadastrados, muito mais rico que o
    antigo `keywords` livre de exercise_zepp_mappings) e nomes externos
    Amazfit (`exercise_external_mappings`, provider='AMAZFIT'). Grupo
    muscular primario/secundario vem de `exercise_muscle_contributions`
    (role PRIMARY/SECONDARY, fonte unica desde a Fase 4 da reestruturacao -
    exercise_catalog nao tem mais colunas primary_group_id/secondary_group_id
    fisicas).

    So considera exercicios `is_active = 1`: garante que nenhum id absorvido
    pela consolidacao do catalogo (14 casos) entre no pool de matching -
    reclassify_exercises() so pode gravar em exercises.mapping_id um id que
    veio deste pool, entao filtrar aqui basta para nunca gravar um id
    absorvido.

    `query_fn` (default: `query`) permite ler pela conexao do runner de
    migracoes."""
    run_query = query_fn or query
    rows = fix_obj(run_query(
        """
        SELECT
            ec.id,
            ec.name AS exercise_name,
            ec.keywords,
            pg.name AS primary_group,
            sg.name AS secondary_group,
            pg.primary_muscles AS primary_muscles,
            pg.secondary_muscles AS secondary_muscles
        FROM `exercise_catalog` ec
        LEFT JOIN `muscle_groups` pg ON pg.id = (
            SELECT emc.muscle_group_id FROM `exercise_muscle_contributions` emc
            WHERE emc.exercise_id = ec.id AND emc.role = 'PRIMARY' AND emc.is_active = 1
            ORDER BY emc.id ASC LIMIT 1
        )
        LEFT JOIN `muscle_groups` sg ON sg.id = (
            SELECT emc.muscle_group_id FROM `exercise_muscle_contributions` emc
            WHERE emc.exercise_id = ec.id AND emc.role = 'SECONDARY' AND emc.is_active = 1
            ORDER BY emc.id ASC LIMIT 1
        )
        WHERE ec.is_active = 1
        """
    ))

    aliases_by_exercise: Dict[int, List[str]] = {}
    for row in fix_obj(run_query("SELECT exercise_id, alias FROM `exercise_aliases`")):
        aliases_by_exercise.setdefault(row["exercise_id"], []).append(row["alias"])

    amazfit_names_by_exercise: Dict[int, List[str]] = {}
    for row in fix_obj(run_query(
        "SELECT exercise_id, external_name FROM `exercise_external_mappings` "
        "WHERE provider = 'AMAZFIT' AND is_active = 1 ORDER BY id ASC"
    )):
        amazfit_names_by_exercise.setdefault(row["exercise_id"], []).append(row["external_name"])

    out = []
    for row in rows:
        primary_group = fix_text(row.get("primary_group"))
        if not primary_group or normalize(primary_group) in ("outros", "other", "others"):
            continue

        exercise_id = row["id"]
        amazfit_names = amazfit_names_by_exercise.get(exercise_id, [])

        strong_keys = set()
        weak_keys = set()

        matching_material = [row.get("exercise_name")]
        matching_material.extend(aliases_by_exercise.get(exercise_id, []))
        matching_material.extend(amazfit_names)
        for value in matching_material:
            if not value:
                continue
            for part in split_values(value):
                key = normalize(part)
                if len(key) >= 3:
                    strong_keys.add(key)

        if row.get("keywords"):
            for value in split_values(row.get("keywords")):
                key = normalize(value)
                if len(key) >= 3:
                    weak_keys.add(key)

        out.append(
            {
                "id": exercise_id,
                "exercise_name": fix_text(row.get("exercise_name")),
                "amazfit_name": fix_text(amazfit_names[0]) if amazfit_names else None,
                "primary_group": fix_text(primary_group),
                "secondary_group": fix_text(row.get("secondary_group")),
                "primary_muscles": fix_text(row.get("primary_muscles")),
                "secondary_muscles": fix_text(row.get("secondary_muscles")),
                "status": "confirmed",
                "source": "exercise_catalog",
                "strong_keys": sorted(strong_keys),
                "weak_keys": sorted(weak_keys),
            }
        )

    return out


def match_mapping(original_name: Any, mapped_name: Any, mappings: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    mappings = mappings if mappings is not None else load_mapping_rows()

    needles = []
    for value in (mapped_name, original_name):
        n = normalize(value)
        if n and n not in needles:
            needles.append(n)

    if not needles:
        return None

    best: Optional[Tuple[int, Dict[str, Any]]] = None

    for mapping in mappings:
        for needle in needles:
            for key in mapping["strong_keys"]:
                if key == needle:
                    return {**mapping, "confidence": 100.0, "match_type": "strong_exact"}
                if len(key) >= 5 and (key in needle or needle in key):
                    score = 80 + min(len(key), len(needle)) / 10
                    if best is None or score > best[0]:
                        best = (int(score * 100), {**mapping, "confidence": round(score, 2), "match_type": "strong_contains"})

            for key in mapping["weak_keys"]:
                if key == needle:
                    score = 70
                    if best is None or score > best[0]:
                        best = (score * 100, {**mapping, "confidence": 70.0, "match_type": "keyword_exact"})
                elif len(key) >= 6 and (key in needle or needle in key):
                    score = 40 + min(len(key), len(needle)) / 10
                    if best is None or score > best[0]:
                        best = (int(score * 100), {**mapping, "confidence": round(score, 2), "match_type": "keyword_contains"})

    return best[1] if best else None


def _same_id(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return int(left) == int(right)


def reclassify_exercises(
    limit: int = 10000,
    only_missing: bool = False,
    *,
    names: Optional[List[str]] = None,
    keep_linked: bool = False,
    query_fn: Optional[QueryFn] = None,
    execute_fn: Optional[ExecuteFn] = None,
    page_size: int = RECLASSIFY_PAGE_SIZE,
) -> Dict[str, Any]:
    """Grava `exercises.mapping_id` casando `original_name` com o catalogo
    canonico ativo (mesmo `match_mapping` do caminho de escrita das sessoes).

    ESCRITA: nunca chamar a partir de rota GET. Pontos de uso: depois de uma
    escrita no catalogo (`reclassify_after_catalog_change`), em
    POST /mapping-canonical/associate e no backfill da migracao 024.

    Escopo (quais exercicios lidos sao reprocessados):
    - `names` informado: SO os exercicios sem vinculo valido (mesmo criterio
      de GET /mapping-canonical/unmapped: `mapping_id` nulo ou que nao resolve
      para exercise_catalog ativo) MAIS os cujo `original_name` normalizado
      (`normalize`, a mesma do matcher) e igual a um dos `names`. Qualquer
      outro vinculo existente fica intocado - inclusive o gravado na sessao
      por `resolve_mapping_id` que o matcher resolveria para outro item.
      `only_missing` e ignorado. Neste escopo um vinculo existente nunca e
      trocado por NULL (sem correspondencia, fica como esta).
      `keep_linked=True` (associate): nem os de mesmo nome com vinculo
      valido sao reprocessados - so os sem vinculo valido.
    - senao, `only_missing`: so `mapping_id IS NULL`.
    - senao: TODOS os exercicios lidos. DESTRUTIVO: sobrescreve vinculos
      corretos que o matcher resolve diferente; so a migracao 024 usa.

    Le os `limit` exercicios mais recentes (so os sem vinculo quando
    `only_missing`) em paginas por id decrescente e so emite UPDATE quando o
    valor muda - reprocessar um exercicio que continua sem correspondencia
    nao gera escrita. `processed`/`updated`/`unresolved` contam os exercicios
    do escopo (reprocessados / com correspondencia / sem correspondencia);
    `written` conta os UPDATEs de fato emitidos.

    `query_fn`/`execute_fn` (default: `query`/`execute`, uma conexao por
    statement) permitem rodar na conexao do runner de migracoes."""
    run_query = query_fn or query
    run_execute = execute_fn or execute
    mappings = load_mapping_rows(run_query)

    target_names = None
    if names is not None:
        target_names = {key for key in (normalize(name) for name in names) if key}

    processed = 0
    updated = 0
    unresolved = 0
    written = 0
    examples = []
    unresolved_examples = []

    remaining = max(0, int(limit))
    page_size = max(1, int(page_size))
    last_id: Any = None

    while remaining > 0:
        conditions = []
        params: List[Any] = []
        batch = min(page_size, remaining)
        if target_names is not None:
            if last_id is not None:
                conditions.append("e.id < %s")
                params.append(last_id)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(batch)
            sql = (
                "SELECT e.id, e.original_name, e.mapping_id, "
                f"CASE WHEN {UNRESOLVED_LINK_CONDITION} THEN 1 ELSE 0 END AS link_unresolved "
                f"FROM `exercises` e {UNRESOLVED_LINK_JOINS} {where} ORDER BY e.id DESC LIMIT %s"
            )
        else:
            if only_missing:
                conditions.append("mapping_id IS NULL")
            if last_id is not None:
                conditions.append("id < %s")
                params.append(last_id)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(batch)
            sql = f"SELECT id, original_name, mapping_id FROM `exercises` {where} ORDER BY id DESC LIMIT %s"
        exercises = fix_obj(run_query(sql, tuple(params)))
        if not exercises:
            break

        for exercise in exercises:
            if target_names is not None and not (
                int(exercise.get("link_unresolved") or 0) == 1
                or (not keep_linked and normalize(exercise.get("original_name")) in target_names)
            ):
                continue
            processed += 1
            match = match_mapping(exercise.get("original_name"), None, mappings)
            target_id = match.get("id") if match else None

            # Escopo `names` nunca apaga um vinculo existente sem ter um novo
            # (ex.: id de catalogo inativo sem correspondencia): continua
            # listado em GET /unmapped, sem perder o dado.
            keep_existing = target_names is not None and target_id is None and exercise.get("mapping_id") is not None
            if not keep_existing and not _same_id(exercise.get("mapping_id"), target_id):
                run_execute(
                    "UPDATE `exercises` SET mapping_id=%s WHERE id=%s",
                    (target_id, exercise["id"]),
                )
                written += 1

            if match:
                updated += 1
                if len(examples) < 20:
                    examples.append(
                        {
                            "id": exercise["id"],
                            "original_name": exercise.get("original_name"),
                            "canonical_exercise_name": match.get("exercise_name"),
                            "canonical_amazfit_name": match.get("amazfit_name"),
                            "canonical_primary_group": match.get("primary_group"),
                            "mapping_id": match.get("id"),
                            "match_type": match.get("match_type"),
                            "confidence": match.get("confidence"),
                        }
                    )
            else:
                unresolved += 1
                if len(unresolved_examples) < 20:
                    unresolved_examples.append({"id": exercise["id"], "original_name": exercise.get("original_name")})

        remaining -= len(exercises)
        if len(exercises) < batch:
            break
        last_id = exercises[-1]["id"]

    return fix_obj(
        {
            "ok": True,
            "processed": processed,
            "updated": updated,
            "unresolved": unresolved,
            "written": written,
            "mapping_count": len(mappings),
            "examples": examples,
            "unresolved_examples": unresolved_examples,
        }
    )


def reclassify_after_catalog_change(source: str) -> Optional[Dict[str, Any]]:
    """Reprocessa os exercicios SEM vinculo (`mapping_id IS NULL`) depois do
    COMMIT de uma escrita no catalogo que pode dar a eles uma correspondencia
    nova (exercicio/nome/keywords/ativacao, alias, nome externo AMAZFIT,
    contribuicao PRIMARY, nome de grupo muscular).

    Substitui a reclassificacao que as rotas GET /analytics/muscle-groups-
    mapped-* faziam a cada leitura: o vinculo de um exercicio so muda quando
    ele e gravado (sessao/importacao ja resolvem via `resolve_mapping_id`, com
    o mesmo matching) ou quando o catalogo muda - aqui.

    A escrita no catalogo ja foi commitada: uma falha aqui e registrada e nao
    derruba a requisicao; a proxima escrita no catalogo (ou
    POST /mapping-canonical/associate) reprocessa os pendentes."""
    try:
        result = reclassify_exercises(limit=MISSING_RECLASSIFY_LIMIT, only_missing=True)
    except Exception:  # noqa: BLE001 - catalogo ja gravado; nao mascarar a resposta
        logger.exception("reclassificacao apos escrita no catalogo falhou (source=%s)", source)
        return None
    summary = {key: result.get(key) for key in ("processed", "updated", "unresolved", "written")}
    logger.info("reclassificacao apos escrita no catalogo (source=%s): %s", source, summary)
    return summary


# Teto de linhas por pagina de GET /mapping-canonical/unmapped?group_by=name.
# Pior caso por linha: original_name VARCHAR(255) em utf8mb4 (ate ~1 KB) + 4
# numeros/datas; 500 linhas ficam bem abaixo do limite de 1 MiB por resultado
# da RDS Data API (coberto em tests/integration/dataapi/test_mapping_canonical_unmapped.py).
UNMAPPED_GROUPED_MAX_LIMIT = 500


def _unmapped_grouped(limit: int, offset: int) -> Dict[str, Any]:
    """Nomes sem vinculo valido agrupados por `original_name`, sem janela de
    dias. SOMENTE LEITURA.

    Agrupamento pela collation da coluna (case/acento-insensitive); o nome
    exibido e o MIN do grupo.
    `sets_count` conta as series nao preparatorias (mesmo filtro das rotas
    -mapped-*); datas vem de `activities.local_date`. Nomes vazios ficam de
    fora (nao ha como associa-los)."""
    from .set_types import sql_not_preparatory, sql_set_confirmed

    base = f"""
        SELECT
            e.id,
            e.original_name,
            a.local_date AS seen_date,
            (
                SELECT COUNT(*) FROM `exercise_sets` s
                WHERE s.exercise_id = e.id AND {sql_not_preparatory('s.set_type')}
                  AND {sql_set_confirmed('s.reps_pending')}
            ) AS set_count
        FROM `exercises` e
        LEFT JOIN `activities` a ON a.id = e.activity_id
        {UNRESOLVED_LINK_JOINS}
        WHERE {UNRESOLVED_LINK_CONDITION}
          AND TRIM(e.original_name) <> ''
    """
    rows = query(
        f"""
        SELECT
            MIN(u.original_name) AS original_name,
            COUNT(*) AS exercises_count,
            COALESCE(SUM(u.set_count), 0) AS sets_count,
            MIN(u.seen_date) AS first_seen,
            MAX(u.seen_date) AS last_seen
        FROM ({base}) u
        GROUP BY u.original_name
        ORDER BY MAX(u.seen_date) DESC, MIN(u.original_name) ASC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
    total_rows = query(f"SELECT COUNT(*) AS total FROM (SELECT 1 FROM ({base}) u GROUP BY u.original_name) g")
    total = int((total_rows[0] if total_rows else {}).get("total") or 0)

    items = [
        {
            "original_name": row.get("original_name"),
            "exercises_count": int(row.get("exercises_count") or 0),
            "sets_count": int(row.get("sets_count") or 0),
            "first_seen": row["first_seen"].isoformat() if row.get("first_seen") else None,
            "last_seen": row["last_seen"].isoformat() if row.get("last_seen") else None,
        }
        for row in rows
    ]
    return fix_obj(
        {
            "ok": True,
            "group_by": "name",
            "rows": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }
    )


@router.get("/unmapped")
def unmapped(
    limit: int = Query(100, ge=1, le=1000),
    group_by: Optional[str] = Query(None, pattern="^name$"),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Exercicios sem vinculo valido com `exercise_catalog` ativo. SOMENTE
    LEITURA (nenhum DDL/DML).

    - sem `group_by` (formato original, inalterado): `{ok, rows: [{id,
      original_name}]}`, uma linha por exercicio, mais recentes primeiro;
      `offset` e ignorado.
    - `group_by=name`: `{ok, group_by, rows: [{original_name,
      exercises_count, sets_count, first_seen, last_seen}], total, limit,
      offset, has_more}`, sem janela de dias, `limit` limitado a
      UNMAPPED_GROUPED_MAX_LIMIT."""
    if group_by == "name":
        return _unmapped_grouped(min(limit, UNMAPPED_GROUPED_MAX_LIMIT), offset)

    rows = query(
        f"""
        SELECT e.id, e.original_name
        FROM `exercises` e
        {UNRESOLVED_LINK_JOINS}
        WHERE {UNRESOLVED_LINK_CONDITION}
        ORDER BY e.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return fix_obj({"ok": True, "rows": rows})

# === CANONICAL MANUAL ALIAS ASSOCIATION V56 START ===
from pydantic import BaseModel


class CanonicalAssociationRequest(BaseModel):
    alias_name: str
    target_mapping_id: int


def _canonical_mapping_options() -> List[Dict[str, Any]]:
    options = []
    for row in load_mapping_rows():
        options.append(
            {
                "id": row.get("id"),
                "exercise_name": row.get("exercise_name"),
                "amazfit_name": row.get("amazfit_name"),
                "primary_group": row.get("primary_group"),
                "secondary_group": row.get("secondary_group"),
                "primary_muscles": row.get("primary_muscles"),
                "secondary_muscles": row.get("secondary_muscles"),
            }
        )

    options.sort(
        key=lambda item: (
            normalize(item.get("amazfit_name")),
            normalize(item.get("exercise_name")),
        )
    )
    return fix_obj(options)


@router.get("/options")
def canonical_options(q: Optional[str] = Query(None)) -> Dict[str, Any]:
    options = _canonical_mapping_options()

    if q:
        needle = normalize(q)
        options = [
            item
            for item in options
            if needle in normalize(item.get("exercise_name"))
            or needle in normalize(item.get("amazfit_name"))
            or needle in normalize(item.get("primary_group"))
            or needle in normalize(item.get("primary_muscles"))
        ]

    return {
        "ok": True,
        "source": "exercise_catalog",
        "items": options[:1000],
    }


@router.post("/associate", dependencies=[Depends(require_admin)])
def canonical_associate(payload: CanonicalAssociationRequest) -> Dict[str, Any]:
    """Associa manualmente um nome sem correspondência (ex.: um
    original_name de exercicio importado que o matching automatico nao
    resolveu) a um exercicio canonico existente em `exercise_catalog`.

    Reescrito na reestruturacao (2026-08): antes criava/atualizava uma linha
    INTEIRA em `exercise_zepp_mappings` copiando os atributos do alvo so pra
    representar o alias (hack pre-existencia de uma tabela de aliases
    dedicada). Agora `exercise_aliases` e a fonte apropriada.

    Apelido repetido em exercicios diferentes e aceito (formato da Aurora, sem
    UNIQUE global por `alias` - migracao 029; decisao do usuario em
    2026-09-14). O associate so garante que o texto exista NO ALVO: INSERT se
    o alvo nao tem, nada se ja tem (`operation` = "updated", contrato
    mantido). Linhas do mesmo texto em outros exercicios ficam intactas.
    Antes: `SELECT ... WHERE alias LIMIT 1` + UPDATE movia uma linha qualquer
    para o alvo e dava 500 (1062 em UNIQUE (exercise_id, alias)) quando o alvo
    ja tinha o apelido.

    Com o texto em varios exercicios o matcher segue ambiguo (primeiro
    `strong_exact` na ordem do catalogo) - aceito pelo usuario."""
    alias_name = str(fix_text(payload.alias_name) or "").strip()

    if len(alias_name) < 2:
        return {"ok": False, "error": "O nome sem correspondência é inválido."}

    target_rows = query(
        "SELECT id, name FROM `exercise_catalog` WHERE id=%s AND is_active=1 LIMIT 1",
        (payload.target_mapping_id,),
    )

    if not target_rows:
        return {"ok": False, "error": "O exercício canônico selecionado não existe ou está inativo."}

    target = target_rows[0]
    manual_source = f"manual_alias:{payload.target_mapping_id}"

    existing = query(
        "SELECT id FROM `exercise_aliases` WHERE exercise_id=%s AND alias=%s LIMIT 1",
        (payload.target_mapping_id, alias_name),
    )

    if existing:
        operation = "updated"
    else:
        try:
            execute(
                "INSERT INTO `exercise_aliases` (exercise_id, alias, source) VALUES (%s, %s, %s)",
                (payload.target_mapping_id, alias_name, manual_source),
            )
            operation = "created"
        except Exception as exc:
            from .db import is_duplicate_key_error

            # Corrida: outra requisicao gravou o mesmo (alvo, texto) entre o SELECT
            # e o INSERT. So nesse caso; qualquer outra chave duplicada (ex.: um
            # banco ainda com o UNIQUE global, sem a 029) nao pode virar sucesso.
            if not is_duplicate_key_error(exc) or not query(
                "SELECT id FROM `exercise_aliases` WHERE exercise_id=%s AND alias=%s LIMIT 1",
                (payload.target_mapping_id, alias_name),
            ):
                raise
            operation = "updated"

    # Escopo restrito (#91) sem tocar vinculos validos (`keep_linked`): com o
    # apelido em varios exercicios, reprocessar um exercicio ja vinculado com
    # esse nome o levaria a um exercicio arbitrario do matcher.
    # Antes do #91: reclassify_exercises(only_missing=False) reescrevia todo
    # vinculo que o matcher resolvia diferente (ex.: o escolhido na sessao).
    reclassification = reclassify_exercises(limit=ASSOCIATE_RECLASSIFY_LIMIT, names=[alias_name], keep_linked=True)

    group_rows = query(
        """
        SELECT mg.name
        FROM `exercise_muscle_contributions` emc
        JOIN `muscle_groups` mg ON mg.id = emc.muscle_group_id
        WHERE emc.exercise_id = %s AND emc.role = 'PRIMARY' AND emc.is_active = 1
        ORDER BY emc.id ASC LIMIT 1
        """,
        (payload.target_mapping_id,),
    )
    target_primary_group = group_rows[0]["name"] if group_rows else None

    amazfit_rows = query(
        "SELECT external_name FROM `exercise_external_mappings` "
        "WHERE exercise_id=%s AND provider='AMAZFIT' AND is_active=1 ORDER BY id ASC LIMIT 1",
        (payload.target_mapping_id,),
    )
    target_amazfit_name = amazfit_rows[0]["external_name"] if amazfit_rows else None

    return fix_obj(
        {
            "ok": True,
            "operation": operation,
            "alias_name": alias_name,
            # Diferente do modelo antigo (uma linha nova em exercise_zepp_mappings
            # com id proprio representando o alias), o alias agora resolve
            # DIRETAMENTE para o id canonico do alvo - nao ha um id separado.
            "alias_mapping_id": payload.target_mapping_id,
            "target_mapping_id": payload.target_mapping_id,
            "target_exercise_name": target.get("name"),
            "target_amazfit_name": target_amazfit_name,
            "target_primary_group": target_primary_group,
            "reclassification": reclassification,
        }
    )
# === CANONICAL MANUAL ALIAS ASSOCIATION V56 END ===
