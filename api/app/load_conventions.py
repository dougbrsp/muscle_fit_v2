"""Convencao de carga do par equipamento<->exercicio
(`equipment_exercises.load_convention`, migracao 045).

Unica fonte do vocabulario e da regra "NULL = total". Compartilhado por:

- `app/equipment_exercises.py` (validacao do POST/PUT) na Lambda api-core;
- `app/intelligence/dashboard/comparability.py` (chave de comparabilidade e
  direcao do progresso) na Lambda engines;
- a tonelagem (relatorio Markdown, comparativos por sessao de `/muscles`,
  progressao por grupo, exposicoes por exercicio): `TONNAGE_EXCLUDED_CONVENTIONS`
  e `sql_counts_in_tonnage` (M58);
- o `volume_kg` GLOBAL (`/analytics/summary`, `/analytics/training-trend`,
  serie semanal movel do contexto da IA e o resumo semanal do
  `TrainingLoadService`): `sql_tonnage_sum`/`sql_tonnage_excluded_sets` (M63);
- `frontend/src/lib/equipment-exercises-types.ts` (rotulos pt-BR do Select),
  cujo espelho e conferido por teste.

Modulo neutro, sem dependencia de `app/intelligence/*`, pelo mesmo motivo de
`app/timezones.py` e `app/weight_units.py`: importar o pacote `intelligence`
a partir da api-core carregaria os dez servicos de `intelligence/__init__.py`
sem necessidade.

Por que a convencao existe: `exercise_sets.weight_kg` guarda SEMPRE carga
total em quilos (decisao D12, ver `app/weight_units.py`), mas o que aquele
numero significa depende do aparelho. 20 kg num graviton (assistido) e o
OPOSTO de 20 kg num leg press - la, menos quilos e progresso.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping

from .domain.set_measures import sql_is_rep_based


class LoadConvention(str, Enum):
    """A ORDEM e a mesma do ENUM da migracao 045 (`LOAD_CONVENTIONS`), e um
    teste compara os dois lados."""

    TOTAL = "total"
    PER_SIDE = "per_side"
    PER_DUMBBELL = "per_dumbbell"
    ASSISTED = "assisted"
    BODYWEIGHT = "bodyweight"
    BODYWEIGHT_PLUS = "bodyweight_plus"

    @property
    def label(self) -> str:
        return LOAD_CONVENTION_LABELS[self]


LOAD_CONVENTION_LABELS: Final[Mapping[LoadConvention, str]] = MappingProxyType(
    {
        LoadConvention.TOTAL: "Carga total",
        LoadConvention.PER_SIDE: "Por lado",
        LoadConvention.PER_DUMBBELL: "Por halter",
        LoadConvention.ASSISTED: "Assistido (menos kg = progresso)",
        LoadConvention.BODYWEIGHT: "Peso corporal",
        LoadConvention.BODYWEIGHT_PLUS: "Peso corporal + carga",
    }
)

# Convencao assumida quando o par nao tem cadastro (coluna NULL). NAO e
# "desconhecido": e o que o sistema ja assume hoje em 100% das linhas (D12).
DEFAULT_LOAD_CONVENTION = LoadConvention.TOTAL


def normalize_load_convention(value: Any) -> LoadConvention:
    """NULL/vazio -> `total`; valor fora do vocabulario -> `ValueError`.

    Estrita de proposito: o endpoint de cadastro converte o `ValueError` em
    400 e a coluna e ENUM, entao um valor invalido so pode vir de entrada
    humana - e engolir isso silenciosamente produziria comparacao errada.
    """
    if value is None:
        return DEFAULT_LOAD_CONVENTION
    if isinstance(value, LoadConvention):
        return value
    text = str(value).strip().lower()
    if not text:
        return DEFAULT_LOAD_CONVENTION
    try:
        return LoadConvention(text)
    except ValueError as exc:
        validos = ", ".join(item.value for item in LoadConvention)
        raise ValueError(f"load_convention invalida: {value!r}; use uma de {validos}") from exc


def inverts_load_direction(convention: LoadConvention) -> bool:
    """`assisted`: menos quilos de assistencia = mais forte (plano §8.2)."""
    return convention is LoadConvention.ASSISTED


def compares_reps_only(convention: LoadConvention) -> bool:
    """`bodyweight`: a serie nao carrega o peso que de fato foi movido, entao
    so as repeticoes sao comparaveis (plano §8.2)."""
    return convention is LoadConvention.BODYWEIGHT


def supports_e1rm(convention: LoadConvention) -> bool:
    """e1RM so faz sentido onde o numero registrado E a carga levantada.

    `assisted` (o peso ALIVIA o movimento) e `bodyweight` (a carga movida nao
    esta na serie) ficam de fora - a limitacao ja documentada no dicionario de
    metricas (plano §6: "nao vale p/ assistido/bodyweight sem carga")."""
    return convention not in (LoadConvention.ASSISTED, LoadConvention.BODYWEIGHT)


# ---------------------------------------------------------------------------
# Tonelagem (reps x kg) - regra UNICA para relatorio e dashboard (M58)
# ---------------------------------------------------------------------------

# Convencoes que NAO entram na tonelagem: o numero gravado nao e a carga
# movida (`assisted` alivia o movimento; em `bodyweight` a carga movida nao
# esta na serie). Todo o resto - inclusive NULL, que e `total` - conta: a carga
# e gravada SEMPRE como total (D12), entao nada e convertido.
TONNAGE_EXCLUDED_CONVENTIONS: Final[frozenset[LoadConvention]] = frozenset(
    {LoadConvention.ASSISTED, LoadConvention.BODYWEIGHT}
)


def counts_in_tonnage(convention: LoadConvention) -> bool:
    """A serie desta convencao entra na tonelagem?"""
    return convention not in TONNAGE_EXCLUDED_CONVENTIONS


def sql_counts_in_tonnage(convention_expr: str, *, measure: str = "es.measure") -> str:
    """Predicado SQL "esta serie entra na tonelagem", espelho de
    `counts_in_tonnage` para quem agrega no banco.

    `convention_expr` e a saida de `load_convention_select_expr` (a coluna da
    045 ou o literal `NULL`). NULL e `total` (conta); valor fora do
    vocabulario tambem conta, igual a leitura em Python dos relatorios
    (`summarize_tonnage`). So literais fixos deste modulo entram no SQL - nada
    vindo de requisicao. Uma expressao so, sem multi-statement: roda igual no
    PyMySQL e na RDS Data API.

    Medidas de serie (Lote T3): so serie de REPETICOES entra
    (`set_measures.sql_is_rep_based(measure)`; NULL = reps) - a serie por tempo
    nao tem reps e o "maximo de reps" nao e a dose de reps x kg da
    tonelagem. `measure` = a coluna `exercise_sets.measure` do FROM. O espelho
    Python desta parte e `set_measures.is_rep_based`, aplicado por quem agrega
    (`counts_in_tonnage` continua so a convencao)."""
    rep_based = sql_is_rep_based(measure)
    expr = convention_expr.strip()
    if expr.upper() == "NULL":
        return rep_based
    excluidas = ", ".join(f"'{c.value}'" for c in sorted(TONNAGE_EXCLUDED_CONVENTIONS, key=lambda c: c.value))
    return f"(({expr} IS NULL OR {expr} NOT IN ({excluidas})) AND {rep_based})"


# Exibicao da tonelagem: a soma de reps x kg sai SEMPRE em kg inteiros, com
# separador de milhar pt-BR ("16.285 kg") - a mesma forma das telas
# (`fmtKgTotal` no FE) e do relatorio (`fmt_unit(..., "kg", 0)`). Unidade
# unica: ninguem converte a tonelagem para outra unidade (guarda
# `tonelagem_convertida`). Prompt que cita a tonelagem interpola
# `format_tonnage`, nunca o float cru.
TONNAGE_DISPLAY_UNIT: Final[str] = "kg"
TONNAGE_DISPLAY_DECIMALS: Final[int] = 0


def format_tonnage(value_kg: Any) -> str:
    """Tonelagem pronta para exibir: `16285.0` -> `"16.285 kg"`. Valor ausente
    ou ilegivel conta como zero (o `COALESCE(..., 0)` das consultas)."""
    try:
        number = Decimal(str(value_kg)) if value_kg is not None else Decimal(0)
    except (InvalidOperation, ValueError):
        number = Decimal(0)
    if not number.is_finite():
        number = Decimal(0)
    quantum = Decimal(1).scaleb(-TONNAGE_DISPLAY_DECIMALS)
    rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
    text = f"{rounded:,.{TONNAGE_DISPLAY_DECIMALS}f}"
    text = text.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")
    return f"{text} {TONNAGE_DISPLAY_UNIT}"


def equipment_exercises_join_sql(
    *, canonical_expr: str, gym_equipment_alias: str = "ge", alias: str = "eqex"
) -> str:
    """LEFT JOIN do par equipamento<->exercicio de onde sai a convencao.

    `equipment_exercises` tem UNIQUE (equipment_id, exercise_id), entao o JOIN
    nunca duplica serie. Exige `gym_equipment` (alias `gym_equipment_alias`)
    ja no FROM: a chave e o equipamento CANONICO (`gym_equipment.equipment_id`),
    nunca o id por academia."""
    return (
        f"LEFT JOIN equipment_exercises {alias}\n"
        f"       ON {alias}.equipment_id = {gym_equipment_alias}.equipment_id\n"
        f"      AND {alias}.exercise_id = {canonical_expr}\n"
        f"      AND {alias}.is_active = 1\n"
    )


def sql_tonnage_sum(
    convention_expr: str, *, reps: str = "es.reps", weight: str = "es.weight_kg", measure: str = "es.measure"
) -> str:
    """`SUM(reps x kg)` so das series que entram na tonelagem - o `volume_kg`
    global (card/series semanais/contexto da IA, M63). Mesmo predicado de
    `sql_counts_in_tonnage` (convencao E serie de repeticoes, Lote T3); sem
    serie, `0` (o `COALESCE` externo de sempre)."""
    counts = sql_counts_in_tonnage(convention_expr, measure=measure)
    return (
        f"COALESCE(SUM(CASE WHEN {counts} THEN COALESCE({reps}, 0) * COALESCE({weight}, 0) ELSE 0 END), 0)"
    )


def sql_tonnage_excluded_sets(convention_expr: str, *, set_id: str = "es.id", measure: str = "es.measure") -> str:
    """Quantas series (cada uma UMA vez) ficaram FORA da tonelagem (convencao
    excluida ou serie que nao e de repeticoes, Lote T3). `COUNT` ignora o
    `NULL` do LEFT JOIN sem serie, entao atividade sem serie nao conta."""
    counts = sql_counts_in_tonnage(convention_expr, measure=measure)
    return f"COUNT(DISTINCT CASE WHEN NOT ({counts}) THEN {set_id} END)"


def exercise_tonnage_joins_sql(*, exercise_alias: str = "e") -> str:
    """LEFT JOINs que levam uma linha de `exercises` ate a convencao de carga:
    id canonico (`exercise_legacy_id_map`, alias `elm`), equipamento canonico
    (`gym_equipment`, alias `ge`) e o par (`equipment_exercises`, alias
    `eqex`). Todos LEFT e todos por chave unica: nao duplicam serie nem somem
    com atividade sem exercicio. Para consultas que ainda NAO tem `elm`/`ge`
    no FROM (as agregacoes globais do M63)."""
    canonical = f"COALESCE(elm.canonical_exercise_id, {exercise_alias}.mapping_id)"
    return (
        f"LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = {exercise_alias}.mapping_id\n"
        f"LEFT JOIN gym_equipment ge ON ge.id = {exercise_alias}.equipment_id\n"
        f"{equipment_exercises_join_sql(canonical_expr=canonical)}"
    )


# ---------------------------------------------------------------------------
# Deteccao da coluna (migracao 045) - a unica parte deste modulo com I/O
# ---------------------------------------------------------------------------

TABLE = "equipment_exercises"
COLUMN = "load_convention"

_CURRENT_SCHEMA_SQL = "SELECT DATABASE() AS db"

_COLUMN_EXISTS_SQL = (
    "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s"
)

# Cache SO DO POSITIVO, chaveado por SCHEMA.
#
# Por que por schema (e nao por processo): um processo pode falar com mais de
# um schema. Em producao isso nao acontece, mas a suite de integracao cria um
# `musclefit_run<token>_<tag>_test` por arquivo e roda tudo no MESMO processo -
# uns com a 045 aplicada, outros (o de `test_migration_023_mapping_fks.py`)
# deliberadamente num estado de schema anterior a ela. Um booleano unico por
# processo fazia o segundo herdar a resposta do primeiro e emitir o SELECT com
# uma coluna que aquele schema nao tem.
#
# Por que SO o positivo: "a coluna existe" e permanente (as migracoes deste
# projeto sao aditivas - a 045 cria a coluna e nada a remove), mas "nao existe"
# e transitorio: e apenas "a migracao ainda nao rodou". Memorizar a ausencia
# envenenava o processo que atendesse uma requisicao na janela entre o deploy
# do codigo novo e a execucao da 045: ele passava a recusar a escrita (503
# citando a 045) e a LER a convencao como NULL/`total` para sempre, mesmo com a
# coluna ja criada e preenchida - so um restart do container recuperava.
#
# Custo deliberado: enquanto a deteccao der negativa, a consulta a
# `information_schema.COLUMNS` se repete A CADA chamada. E temporario (dura so
# ate a migracao rodar) e o preco de nunca ficar preso num "nao existe" falso.
# DEPOIS que a coluna existe, ela volta a acontecer no maximo uma vez por
# schema, que e o caso permanente.
_schemas_with_column: set[str] = set()


def _declared_schema(cursor: Any) -> str:
    """Schema que a CONEXAO abriu, sem ida ao banco - `""` quando nao da para
    saber.

    Os dois backends guardam o nome no objeto de conexao: `Connection.database`
    em `app/db_dataapi.py` (e o `database` de cada `execute_statement`) e em
    `app/db_pymysql.py` (o mesmo que foi passado a `pymysql.connect`). Um
    cursor cru do PyMySQL expoe `connection.db` (bytes). Nenhum caminho deste
    projeto emite `USE outro_schema`, entao o nome declarado na abertura E o
    `DATABASE()` da conexao.

    Um fake de teste que nao tenha nada disso cai no `SELECT DATABASE()` de
    sempre - e por isso que o fallback existe.
    """
    conn = getattr(cursor, "_conn", None)
    nome = getattr(conn, "database", None)
    if not isinstance(nome, (str, bytes)):
        bruto = getattr(conn, "_raw", None) or getattr(cursor, "connection", None)
        nome = getattr(bruto, "db", None)
    if isinstance(nome, bytes):
        nome = nome.decode("utf-8", "replace")
    return nome if isinstance(nome, str) else ""


def current_schema(cursor: Any) -> str:
    """Nome do schema em uso nesta conexao (`DATABASE()`).

    So chega ao banco quando a conexao nao sabe dizer o proprio schema. Esta e
    a unica funcao que as SETE deteccoes "a tabela/coluna X existe neste
    schema?" (`load_conventions`, `body_measurements_schema`,
    `context_events_schema`, `daily_checkins_schema`, `equipment_exercises`,
    `gym_equipment_sync`, `alert_state_service`) chamam para montar a chave do
    cache - e cada uma a chamava A CADA verificacao, inclusive quando o cache
    ja tinha a resposta. Medido no `overview` (M29): 7 `SELECT DATABASE()` por
    requisicao, 11% dos statements da rota, sem nenhum efeito no payload. No
    caminho da RDS Data API cada um era uma ida ao servico."""
    declarado = _declared_schema(cursor)
    if declarado:
        return declarado
    cursor.execute(_CURRENT_SCHEMA_SQL)
    row = cursor.fetchone()
    return str((row or {}).get("db") or "")


def has_load_convention_column(cursor: Any) -> bool:
    """`equipment_exercises.load_convention` ja existe NESTE schema?

    Uma consulta a `information_schema` por schema DEPOIS que a coluna existe;
    enquanto ela nao existe, uma por chamada (ver o comentario do cache). Sem
    DDL: este modulo NUNCA cria a coluna - quem cria e a migracao 045, pelo
    pipeline."""
    schema = current_schema(cursor)
    if schema in _schemas_with_column:
        return True
    cursor.execute(_COLUMN_EXISTS_SQL, (TABLE, COLUMN))
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) > 0
    if exists:
        _schemas_with_column.add(schema)
    return exists


def reset_load_convention_column_cache() -> None:
    """Esquece a deteccao de TODOS os schemas. Como so o positivo e cacheado,
    isto so importa no caminho inverso - um teste que DESFAZ a 045 e volta o
    schema a um estado anterior a ela (e o que `test_migration_023_mapping_fks.py`
    faz entre as duas metades do teste)."""
    _schemas_with_column.clear()


def load_convention_select_expr(cursor: Any, *, alias: str = "eqex") -> str:
    """Expressao SQL para LER a convencao: a coluna quando ela existe, senao
    `NULL` - que `normalize_load_convention` ja interpreta como `total`.

    O campo continua SEMPRE presente no resultado, entao nenhum chamador
    precisa de um caminho alternativo."""
    if has_load_convention_column(cursor):
        return f"{alias}.{COLUMN}"
    return "NULL"


def pair_load_convention(cursor: Any, mapping_id: Any, gym_equipment_id: Any) -> LoadConvention:
    """Convencao de carga de UM par (`exercises.mapping_id` + `gym_equipment.id`),
    pelo mesmo caminho das agregacoes (id canonico via `exercise_legacy_id_map`,
    equipamento canonico via `gym_equipment`). Par sem cadastro, ids ausentes
    ou schema sem a 045 -> `total` (D12). Usada pela regra de carga da proxima
    serie (`domain/next_set_load.py`)."""
    try:
        mapping = int(mapping_id) if mapping_id is not None else None
        gym_equipment = int(gym_equipment_id) if gym_equipment_id is not None else None
    except (TypeError, ValueError):
        return DEFAULT_LOAD_CONVENTION
    if mapping is None or gym_equipment is None:
        return DEFAULT_LOAD_CONVENTION
    expr = load_convention_select_expr(cursor)
    cursor.execute(
        f"SELECT {expr} AS load_convention\n"
        "FROM gym_equipment ge\n"
        "LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = %s\n"
        f"{equipment_exercises_join_sql(canonical_expr='COALESCE(elm.canonical_exercise_id, %s)')}"
        "WHERE ge.id = %s",
        (mapping, mapping, gym_equipment),
    )
    row = cursor.fetchone() or {}
    try:
        return normalize_load_convention(row.get("load_convention"))
    except ValueError:
        return DEFAULT_LOAD_CONVENTION


__all__ = [
    "COLUMN",
    "current_schema",
    "DEFAULT_LOAD_CONVENTION",
    "LOAD_CONVENTION_LABELS",
    "LoadConvention",
    "TABLE",
    "TONNAGE_EXCLUDED_CONVENTIONS",
    "compares_reps_only",
    "counts_in_tonnage",
    "equipment_exercises_join_sql",
    "exercise_tonnage_joins_sql",
    "has_load_convention_column",
    "inverts_load_direction",
    "load_convention_select_expr",
    "normalize_load_convention",
    "pair_load_convention",
    "reset_load_convention_column_cache",
    "sql_counts_in_tonnage",
    "sql_tonnage_excluded_sets",
    "sql_tonnage_sum",
    "supports_e1rm",
]
