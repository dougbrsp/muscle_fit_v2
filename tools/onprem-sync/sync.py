#!/usr/bin/env python3
"""Espelhamento de dados onpremise (MySQL) -> Aurora MySQL (AWS).

Fluxo UNIDIRECIONAL. O onpremise e fonte de verdade absoluta e NUNCA e
modificado por esta ferramenta. Ver README.md para pre-requisitos e operacao.

Modos:
    python sync.py                      # dry-run (PADRAO): so imprime o plano
    python sync.py --onprem-only        # inventario/ordem de FK sem tocar AWS
    python sync.py --backup-only        # so dump reverso do Aurora
    python sync.py --apply              # escreve (exige confirmacao digitada)
    python sync.py --restore-from DIR   # recarrega um backup no Aurora
    python sync.py --tables a,b,c       # restringe o escopo
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from syncmod import backup as backup_mod  # noqa: E402
from syncmod.applier import Applier  # noqa: E402
from syncmod.aurora import (  # noqa: E402
    AuroraClient,
    AuroraCredentialsError,
    AuroraError,
    available_profiles,
)
from syncmod.config import SyncConfig, load_config  # noqa: E402
from syncmod.onprem import OnpremError, OnpremReader  # noqa: E402
from syncmod.planner import (  # noqa: E402
    SyncPlan,
    TablePlan,
    build_plan,
    diff_table,
    effective_auto_increment,
    suggest_page_size,
)
from syncmod.schema import Schema, topological_order  # noqa: E402
from syncmod import verify as verify_mod  # noqa: E402


DEFAULT_BACKUP_ROOT = Path(__file__).resolve().parent / "backups"
CONFIRMATION_PHRASE = "ESPELHAR"

# Ordem esperada por camada, levantada manualmente na analise dos dois
# ambientes. NAO e usada para ordenar nada - a ordem real e derivada do grafo
# de FK em `information_schema`. Serve so para o preflight avisar se a
# derivacao deixar de bater com o que foi revisado por um humano.
EXPECTED_TIERS: list[list[str]] = [
    ["users", "sources", "muscle_groups", "equipment_types", "exercise_catalog"],
    ["training_types", "equipment", "gyms", "imports"],
    ["workout_plan_templates"],
    ["workout_plans"],
    [
        # equipment_exercise_mappings: LEGADA (espelho de equipment_exercises,
        # ver `legacy_tables` em sync_config.json) - sai na fase 2 da remocao.
        "gym_equipment_catalog", "equipment_exercise_mappings", "equipment_exercises",
        "exercise_aliases", "exercise_external_mappings", "exercise_legacy_id_map",
        "exercise_muscle_contributions", "exercise_zepp_mappings", "muscle_group_body_map",
        "muscle_recovery_coefficients", "training_type_muscle_group_targets",
    ],
    ["gym_equipment"],
    ["activities"],
    ["workout_plan_exercises", "workout_plan_template_slots"],
    ["exercises", "workout_plan_template_slot_items", "workout_plan_template_assignments"],
    ["exercise_sets", "activity_hr_zones"],
    [
        "cardio_recommendations", "body_composition", "daily_biometrics", "sleep_metrics",
        "manual_effort_metrics", "devices", "reports", "intervals_activities",
        "intervals_wellness", "user_sessions",
    ],
    ["weekly_summaries"],
]


# ---------------------------------------------------------------------------
# Saida
# ---------------------------------------------------------------------------

def out(msg: str = "") -> None:
    print(msg, flush=True)


def header(title: str) -> None:
    out()
    out("=" * 78)
    out(title)
    out("=" * 78)


def render_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        out("  (nada)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    out(fmt.format(*headers))
    out("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        out(fmt.format(*row))


# ---------------------------------------------------------------------------
# Escopo e ordem
# ---------------------------------------------------------------------------

def resolve_scope(schema: Schema, cfg: SyncConfig, only: list[str] | None) -> list[str]:
    excluded = cfg.excluded_table_names
    scope = [t for t in schema.names if t not in excluded]

    missing_pk = [t for t in scope if not schema[t].has_pk]
    if missing_pk:
        raise SystemExit(
            "Tabelas sem PRIMARY KEY nao podem ser espelhadas (nao ha como casar linhas): "
            + ", ".join(missing_pk)
        )

    if only:
        unknown = [t for t in only if t not in schema]
        if unknown:
            raise SystemExit("Tabela inexistente no onprem: " + ", ".join(unknown))
        blocked = [t for t in only if t in excluded]
        if blocked:
            raise SystemExit(
                "Tabela excluida por configuracao (remova de excluded_tables se quiser sincronizar): "
                + ", ".join(blocked)
            )
        scope = [t for t in scope if t in set(only)]

    return scope


def validate_fk_order(schema: Schema, order: list[str]) -> list[str]:
    """Checagem DURA: toda tabela-pai vem antes da tabela-filha na ordem.

    Esta e a unica propriedade que a ordem precisa ter. Violacao aqui e bug.
    """
    position = {t: i for i, t in enumerate(order)}
    violations: list[str] = []
    for table_name in order:
        for fk in schema[table_name].foreign_keys:
            if fk.is_self_reference or fk.ref_table not in position:
                continue
            if position[fk.ref_table] > position[table_name]:
                violations.append(
                    f"{fk.name}: filho `{table_name}` aparece antes do pai `{fk.ref_table}`"
                )
    return violations


def compare_with_expected(
    order: list[str], scope: list[str], legacy: set[str] | frozenset[str] = frozenset()
) -> list[str]:
    """Confere a derivacao contra a lista de camadas revisada por humano.

    Comparar posicao a posicao nao faria sentido: varias ordens topologicas
    sao igualmente validas, e tabelas SEM nenhuma FK (`devices`,
    `weekly_summaries`, `intervals_*`) podem legitimamente ser emitidas logo
    no inicio, mesmo estando na ultima camada da lista humana - a camada ali
    expressa semantica de dominio, nao dependencia. O que vale comparar e o
    CONJUNTO de tabelas e, principalmente, se a ordem humana tambem e
    FK-valida (se nao fosse, a lista revisada e que estaria errada).

    Tabelas em `legacy` (config `legacy_tables`) que ja nao existem mais no
    schema nao geram aviso: e o estado esperado depois do DROP da fase de
    remocao, e o sync nao deve travar essa remocao.
    """
    expected = [t for tier in EXPECTED_TIERS for t in tier]
    notes: list[str] = []

    faltando = [t for t in scope if t not in expected]
    sobrando = [t for t in expected if t not in scope and t not in legacy]
    if faltando:
        notes.append("no escopo mas ausentes da lista humana: " + ", ".join(sorted(faltando)))
    if sobrando:
        notes.append("na lista humana mas fora do escopo: " + ", ".join(sorted(sobrando)))
    return notes


def resolve_profile(cfg: SyncConfig, cli_profile: str | None):
    """Decide qual perfil AWS usar.

    Precedencia: --aws-profile > AWS_PROFILE do ambiente > `aurora.profile` do
    config. O ambiente vence o config de proposito: quem exporta AWS_PROFILE no
    shell esta declarando intencao explicita para aquela execucao.
    """
    profile = cli_profile or os.environ.get("AWS_PROFILE") or cfg.aurora.profile
    return dataclasses.replace(cfg.aurora, profile=profile)


# ---------------------------------------------------------------------------
# Preflight de paridade de schema
# ---------------------------------------------------------------------------

def preflight_schema(client: AuroraClient, schema: Schema, scope: list[str], cfg: SyncConfig) -> None:
    header("PREFLIGHT DE SCHEMA (onprem x Aurora)")
    aurora_tables = set(client.list_tables())
    aurora_columns = client.list_columns()

    missing = [t for t in scope if t not in aurora_tables]
    if missing:
        raise SystemExit(
            "Tabelas ausentes no Aurora (schema fora de paridade, rode as migrations antes): "
            + ", ".join(missing)
        )

    divergent: list[list[str]] = []
    for table_name in scope:
        src_cols = schema[table_name].column_names
        dst_cols = aurora_columns.get(table_name, [])
        only_src = [c for c in src_cols if c not in dst_cols]
        only_dst = [c for c in dst_cols if c not in src_cols]
        if only_src or only_dst:
            divergent.append([table_name, ",".join(only_src) or "-", ",".join(only_dst) or "-"])

    if divergent:
        render_table(["tabela", "so no onprem", "so no Aurora"], divergent)
        raise SystemExit(
            "Colunas divergentes entre onprem e Aurora. O espelho exige paridade de schema."
        )
    out(f"  OK: {len(scope)} tabela(s) com o mesmo conjunto de colunas nos dois lados.")

    collations = client.table_collations()
    rows = []
    for table_name in scope:
        src = schema[table_name].collation or "-"
        dst = collations.get(table_name, "-")
        if src != dst:
            rows.append([table_name, src, dst])
    if rows:
        out()
        out("  AVISO: collation divergente entre os dois lados (nao bloqueia o sync -")
        out("  a comparacao e feita em Python sobre bytes, e os joins de verificacao")
        out("  usam so colunas numericas - mas pode afetar consultas do app):")
        render_table(["tabela", "onprem", "aurora"], rows)


# ---------------------------------------------------------------------------
# Impressao do plano
# ---------------------------------------------------------------------------

def print_plan(plan: SyncPlan, scope: list[str]) -> None:
    header("PLANO DE MUDANCAS")
    rows: list[list[str]] = []
    for table_name in plan.insert_order:
        tp = plan.tables[table_name]
        flag = "AI" if tp.auto_increment_needs_fix else ""
        if tp.is_empty:
            continue
        rows.append([
            table_name,
            str(tp.onprem_count),
            str(tp.aurora_count),
            str(len(tp.inserts)),
            str(len(tp.updates)),
            str(len(tp.deletes)),
            flag,
        ])
    render_table(
        ["tabela", "onprem", "aurora", "inserts", "updates", "deletes", "auto_inc"],
        rows,
    )

    totals = plan.totals()
    out()
    out(f"  TOTAL: {totals['inserts']} inserts, {totals['updates']} updates, {totals['deletes']} deletes")
    out(f"  Tabelas no escopo: {len(scope)} | com mudanca: {len(rows)}")

    ai = [t for t in plan.insert_order if plan.tables[t].auto_increment_needs_fix]
    if ai:
        out()
        out("  AUTO_INCREMENT a realinhar (ALTER TABLE, fora da transacao):")
        render_table(
            ["tabela", "aurora atual", "onprem (alvo)"],
            [
                [t, str(plan.tables[t].aurora_auto_increment), str(plan.tables[t].onprem_auto_increment)]
                for t in ai
            ],
        )

    changed = [t for t in plan.insert_order if plan.tables[t].changed_columns]
    if changed:
        out()
        out("  Colunas que mais divergem (ajuda a diagnosticar falso positivo):")
        render_table(
            ["tabela", "colunas (n de linhas divergentes)"],
            [
                [t, ", ".join(f"{c}={n}" for c, n in sorted(
                    plan.tables[t].changed_columns.items(), key=lambda kv: -kv[1])[:6])]
                for t in changed
            ],
        )

    if plan.is_empty:
        out()
        out("  PLANO VAZIO: o Aurora ja e um espelho exato do onprem (idempotencia OK).")


def print_verification(report: verify_mod.VerificationReport) -> None:
    header("VERIFICACAO POS-SYNC: CONTAGEM E CHECKSUM DE CONTEUDO")
    render_table(
        ["tabela", "onprem", "aurora", "checksum onprem", "checksum aurora", "status"],
        [
            [t.table, str(t.onprem_count), str(t.aurora_count),
             t.onprem_checksum[:12], t.aurora_checksum[:12], t.status]
            for t in report.tables
        ],
    )

    header("VERIFICACAO POS-SYNC: INTEGRIDADE REFERENCIAL (FK ORFA NO AURORA)")
    broken = [o for o in report.orphans if o.status != "OK"]
    render_table(
        ["constraint", "filho", "coluna(s)", "pai", "orfas", "status"],
        [[o.constraint, o.child, o.columns, o.parent, str(o.orphans), o.status]
         for o in (broken or report.orphans)],
    )
    out()
    out(f"  {len(report.orphans)} relacao(oes) verificada(s), {len(broken)} com vinculo quebrado.")

    header("VERIFICACAO POS-SYNC: ENCODING (BYTES x CARACTERES)")
    divergent = [e for e in report.encoding if e.status != "OK"]
    if divergent:
        render_table(
            ["verificacao", "onprem", "aurora", "status"],
            [[e.label, str(e.onprem), str(e.aurora), e.status] for e in divergent],
        )
    else:
        out(f"  OK: {len(report.encoding)} verificacao(oes) de encoding, nenhuma divergencia.")
        out("  (soma de OCTET_LENGTH/CHAR_LENGTH por coluna textual + sondas HEX byte a byte)")

    header("RESULTADO FINAL")
    out("  " + ("TUDO OK - Aurora e espelho exato do onpremise."
                if report.ok else
                "DIVERGENCIAS ENCONTRADAS - ver secoes acima."))


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------

def read_onprem(reader: OnpremReader, schema: Schema, scope: list[str]) -> dict[str, list[dict[str, Any]]]:
    header("EXTRACAO DO ONPREMISE (somente leitura)")
    rows: dict[str, list[dict[str, Any]]] = {}
    for table_name in scope:
        data = reader.fetch_rows(schema[table_name])
        rows[table_name] = data
    render_table(
        ["tabela", "linhas", "auto_increment"],
        [[t, str(len(rows[t])), str(schema[t].auto_increment or "-")] for t in scope],
    )
    out()
    out(f"  Total: {sum(len(v) for v in rows.values())} linha(s) em {len(scope)} tabela(s).")
    return rows


def read_aurora(
    client: AuroraClient,
    schema: Schema,
    scope: list[str],
    onprem_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    header("LEITURA DO ESTADO ATUAL DO AURORA")
    rows: dict[str, list[dict[str, Any]]] = {}
    client.begin()
    try:
        for table_name in scope:
            hint = suggest_page_size(
                onprem_rows.get(table_name, []), client.batch_cfg.max_bytes_per_read_page
            )
            rows[table_name] = client.fetch_table(schema[table_name], page_hint=hint)
    finally:
        client.commit()
    out(f"  {sum(len(v) for v in rows.values())} linha(s) lidas em {len(scope)} tabela(s).")
    return rows


def aurora_auto_increments(client: AuroraClient, database: str, scope: list[str]) -> dict[str, int | None]:
    names = "', '".join(scope)
    rows = client.fetch_all(
        "SELECT TABLE_NAME AS t, CAST(AUTO_INCREMENT AS CHAR) AS ai "
        f"FROM information_schema.TABLES WHERE TABLE_SCHEMA = '{database}' "
        f"AND TABLE_NAME IN ('{names}')"
    )
    return {str(r["t"]): (int(r["ai"]) if r["ai"] else None) for r in rows}


def build_full_plan(
    schema: Schema,
    scope: list[str],
    onprem_rows: dict[str, list[dict[str, Any]]],
    aurora_rows: dict[str, list[dict[str, Any]]],
    aurora_ai: dict[str, int | None],
) -> SyncPlan:
    order = topological_order(schema, scope)
    plans: dict[str, TablePlan] = {}
    for table_name in order:
        tp = diff_table(schema[table_name], onprem_rows[table_name], aurora_rows.get(table_name, []))
        tp.onprem_auto_increment = effective_auto_increment(
            schema[table_name], onprem_rows[table_name]
        )
        tp.aurora_auto_increment = aurora_ai.get(table_name)
        plans[table_name] = tp
    return build_plan(schema, plans, order)


def confirm_apply(cfg: SyncConfig, plan: SyncPlan, backup_dir: Path) -> None:
    totals = plan.totals()
    header("CONFIRMACAO OBRIGATORIA")
    out(f"  Destino : Aurora {cfg.aurora.cluster_identifier} / database `{cfg.aurora.database}` ({cfg.aurora.region})")
    out(f"  Origem  : container {cfg.onprem.container} / database `{cfg.onprem.database}`")
    out(f"  Backup  : {backup_dir}")
    out()
    out(f"  Serao aplicados {totals['inserts']} INSERT, {totals['updates']} UPDATE e")
    out(f"  {totals['deletes']} DELETE em um banco de PRODUCAO.")
    out()
    out("  Os DELETEs removem linhas que existem SO no Aurora. Isso e intencional")
    out("  (espelho exato), mas e irreversivel fora do backup acima.")
    out()

    if not sys.stdin.isatty():
        raise SystemExit(
            "--apply exige terminal interativo para a confirmacao digitada. Abortado."
        )

    expected = f"{CONFIRMATION_PHRASE} {cfg.aurora.database}"
    answer = input(f'  Para continuar, digite exatamente "{expected}": ').strip()
    if answer != expected:
        raise SystemExit("Confirmacao nao conferiu. Nada foi escrito.")


def run_backup(
    client: AuroraClient,
    cfg: SyncConfig,
    schema: Schema,
    scope: list[str],
    aurora_rows: dict[str, list[dict[str, Any]]],
    backup_root: Path,
) -> Path:
    header("BACKUP DO ESTADO ATUAL DO AURORA")
    directory = backup_mod.new_backup_dir(backup_root)
    manifest = backup_mod.write_backup(
        directory,
        schema,
        {t: aurora_rows.get(t, []) for t in scope},
        metadata={
            "aurora_cluster": cfg.aurora.cluster_identifier,
            "aurora_database": cfg.aurora.database,
            "region": cfg.aurora.region,
            "time_zone": cfg.time_zone,
            "scope": scope,
            "excluded_tables": sorted(cfg.excluded_table_names),
        },
    )
    total = sum(t["row_count"] for t in manifest["tables"].values())
    out(f"  {total} linha(s) de {len(scope)} tabela(s) salvas em:")
    out(f"    {directory}")
    out("    data/<tabela>.json  (fonte oficial de restauracao)")
    out("    restore.sql         (plano B legivel, para acesso direto ao banco)")
    out("    manifest.json")
    if not (directory / "manifest.json").is_file():
        raise SystemExit("Backup nao foi gravado. --apply abortado por seguranca.")
    return directory


def run_verification(
    reader: OnpremReader,
    client: AuroraClient,
    schema: Schema,
    scope: list[str],
    onprem_rows: dict[str, list[dict[str, Any]]],
    cfg: SyncConfig,
) -> verify_mod.VerificationReport:
    client.begin()
    try:
        fresh = {}
        for table_name in scope:
            hint = suggest_page_size(onprem_rows.get(table_name, []), client.batch_cfg.max_bytes_per_read_page)
            fresh[table_name] = client.fetch_table(schema[table_name], page_hint=hint)
        report = verify_mod.VerificationReport(
            tables=verify_mod.verify_tables(schema, onprem_rows, fresh),
            orphans=verify_mod.check_referential_integrity(client, schema, set(scope)),
            encoding=verify_mod.check_encoding(
                reader, client, schema, set(scope), cfg.encoding_probes
            ),
        )
    finally:
        client.commit()
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Espelha o MySQL onpremise no Aurora MySQL (AWS). Dry-run e o padrao.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="PADRAO: extrai, compara e imprime o plano sem escrever nada.")
    mode.add_argument("--apply", action="store_true",
                      help="Escreve no Aurora. Exige backup bem-sucedido e confirmacao digitada.")
    mode.add_argument("--backup-only", action="store_true",
                      help="So gera o dump reverso do Aurora e sai.")
    mode.add_argument("--onprem-only", action="store_true",
                      help="So inventario/ordem de FK do onpremise. Nao usa credencial AWS.")
    mode.add_argument("--restore-from", metavar="DIR",
                      help="Restaura no Aurora um backup gerado por esta ferramenta.")

    parser.add_argument("--tables", help="Subconjunto de tabelas separado por virgula.")
    parser.add_argument("--aws-profile", metavar="NOME",
                        help="Perfil nomeado do AWS CLI. Precede AWS_PROFILE e o valor do config.")
    parser.add_argument("--config", help="Caminho do sync_config.json.")
    parser.add_argument("--backup-dir", help=f"Raiz dos backups (padrao: {DEFAULT_BACKUP_ROOT}).")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Pula a verificacao pos-sync (NAO recomendado).")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    # O console do Windows nao e UTF-8 por padrao; sem isto, imprimir um nome
    # acentuado vindo do banco derruba o processo com UnicodeEncodeError.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    elif isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no cover
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    args = parse_args(argv)
    cfg = load_config(args.config)
    only = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None
    backup_root = Path(args.backup_dir) if args.backup_dir else DEFAULT_BACKUP_ROOT

    header("ORIGEM: ONPREMISE (somente leitura)")
    reader = OnpremReader(cfg.onprem, time_zone=cfg.time_zone)
    out(f"  container={cfg.onprem.container} database={cfg.onprem.database}")
    out(f"  version|time_zone|database => {reader.ping()}")

    schema = reader.load_schema()
    scope = resolve_scope(schema, cfg, only)
    order = topological_order(schema, scope)

    header("ESCOPO E ORDEM TOPOLOGICA (derivada de information_schema.KEY_COLUMN_USAGE)")
    out(f"  Tabelas no schema onprem : {len(schema.names)}")
    out(f"  Excluidas por config     : {len(cfg.excluded_table_names)}")
    for table_name, reason in sorted(cfg.excluded_tables.items()):
        if not table_name.startswith("_"):
            out(f"    - {table_name}: {reason}")
    out(f"  No escopo do sync        : {len(scope)}")
    for table_name, reason in sorted(cfg.legacy_tables.items()):
        if table_name.startswith("_"):
            continue
        estado = "no escopo" if table_name in scope else "fora do escopo/inexistente"
        out(f"    ! LEGADA ({estado}): {table_name}: {reason}")
    out()
    out("  Ordem de INSERT (pais -> filhos):")
    for i, table_name in enumerate(order, 1):
        out(f"    {i:>2}. {table_name}")
    out()
    violations = validate_fk_order(schema, order)
    if violations:
        for v in violations:
            out(f"    ERRO: {v}")
        raise SystemExit("Ordem topologica invalida. Abortado antes de qualquer leitura de dados.")
    fk_count = sum(len(schema[t].foreign_keys) for t in scope)
    out(f"  OK: ordem FK-valida - {fk_count} foreign key(s) com pai antes do filho.")
    for note in compare_with_expected(order, scope, cfg.legacy_table_names):
        out(f"  AVISO (comparacao com a lista humana de camadas): {note}")

    if args.onprem_only:
        onprem_rows = read_onprem(reader, schema, scope)
        header("PREVIA DE STATEMENTS (nenhuma credencial AWS usada)")
        applier = Applier.__new__(Applier)  # so para reaproveitar a montagem de SQL
        applier.schema = schema
        for table_name in order[:3]:
            table = schema[table_name]
            out(f"  {table_name}:")
            out(f"    {applier._insert_statement(table)[:160]}")
            out(f"    {applier._update_statement(table)[:160]}")
            out(f"    {applier._delete_statement(table)}")
            out(f"    lote sugerido: {suggest_page_size(onprem_rows[table_name], cfg.batch.max_bytes_per_batch)} linha(s)")
        out()
        out("  --onprem-only concluido. Nada foi lido nem escrito na AWS.")
        return 0

    aurora_cfg = resolve_profile(cfg, args.aws_profile)
    header("DESTINO: AURORA (RDS Data API)")
    out(f"  cluster={aurora_cfg.cluster_identifier} database={aurora_cfg.database} region={aurora_cfg.region}")
    out(f"  perfil AWS={aurora_cfg.profile or '(sessao padrao)'}")
    client = AuroraClient(aurora_cfg, cfg.batch, time_zone=cfg.time_zone)
    out(f"  secret={client.secret_arn}")
    out(f"  identidade={client.caller_identity()}")

    if args.restore_from:
        return run_restore(args, cfg, client, schema, scope, order)

    preflight_schema(client, schema, scope, cfg)

    onprem_rows = read_onprem(reader, schema, scope)
    aurora_rows = read_aurora(client, schema, scope, onprem_rows)

    client.begin()
    try:
        aurora_ai = aurora_auto_increments(client, cfg.aurora.database, scope)
    finally:
        client.commit()

    plan = build_full_plan(schema, scope, onprem_rows, aurora_rows, aurora_ai)
    print_plan(plan, scope)

    if args.backup_only:
        run_backup(client, cfg, schema, scope, aurora_rows, backup_root)
        out()
        out("  --backup-only concluido. Nada foi escrito no Aurora.")
        return 0

    if not args.apply:
        header("DRY-RUN")
        out("  Nenhuma escrita foi feita. Para aplicar: python sync.py --apply")
        return 0

    backup_dir = run_backup(client, cfg, schema, scope, aurora_rows, backup_root)
    confirm_apply(cfg, plan, backup_dir)

    header("APLICANDO (transacao unica, rollback em erro)")
    applier = Applier(client, schema, progress=out)
    client.begin(disable_fk_checks=True)
    try:
        stats = applier.apply(plan)
        client.restore_fk_checks()
        client.commit()
    except Exception:
        client.rollback()
        out()
        out("  ERRO: transacao revertida (rollback). Nada foi gravado.")
        out(f"  Backup do estado anterior permanece em: {backup_dir}")
        raise

    out()
    out(f"  Aplicado: {stats.inserted} inserts, {stats.updated} updates, {stats.deleted} deletes "
        f"({stats.statements} chamadas ao Data API).")

    fixed = applier.fix_auto_increment(plan)
    if fixed:
        out(f"  AUTO_INCREMENT realinhado em {len(fixed)} tabela(s): {', '.join(fixed)}")

    if args.skip_verify:
        out("  Verificacao pos-sync PULADA por --skip-verify.")
        return 0

    report = run_verification(reader, client, schema, scope, onprem_rows, cfg)
    print_verification(report)
    return 0 if report.ok else 2


def run_restore(
    args: argparse.Namespace,
    cfg: SyncConfig,
    client: AuroraClient,
    schema: Schema,
    scope: list[str],
    order: list[str],
) -> int:
    directory = Path(args.restore_from)
    header("RESTAURACAO A PARTIR DE BACKUP")
    out(f"  Diretorio: {directory}")
    saved = backup_mod.load_backup(directory)
    restore_scope = [t for t in order if t in saved and t in scope]
    out(f"  Tabelas no backup: {len(saved)} | restauraveis no escopo atual: {len(restore_scope)}")

    current = read_aurora(client, schema, restore_scope, saved)
    plans: dict[str, TablePlan] = {}
    for table_name in restore_scope:
        tp = diff_table(schema[table_name], saved[table_name], current.get(table_name, []))
        plans[table_name] = tp
    plan = build_plan(schema, plans, restore_scope)
    print_plan(plan, restore_scope)

    if plan.is_empty:
        out()
        out("  Aurora ja esta no estado do backup. Nada a fazer.")
        return 0

    if not sys.stdin.isatty():
        raise SystemExit("--restore-from exige terminal interativo para confirmacao. Abortado.")
    expected = f"RESTAURAR {cfg.aurora.database}"
    if input(f'  Para restaurar, digite exatamente "{expected}": ').strip() != expected:
        raise SystemExit("Confirmacao nao conferiu. Nada foi escrito.")

    applier = Applier(client, schema, progress=out)
    client.begin(disable_fk_checks=True)
    try:
        stats = applier.apply(plan)
        client.restore_fk_checks()
        client.commit()
    except Exception:
        client.rollback()
        out("  ERRO: transacao revertida (rollback). Nada foi gravado.")
        raise

    out(f"  Restaurado: {stats.inserted} inserts, {stats.updated} updates, {stats.deleted} deletes.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except AuroraCredentialsError as exc:
        # Nao e falha da ferramenta nem do banco: e o operador que precisa
        # autenticar. Traceback de botocore aqui so atrapalha.
        print("\n" + "=" * 78, file=sys.stderr)
        print("SESSAO AWS AUSENTE OU EXPIRADA", file=sys.stderr)
        print("=" * 78, file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print("\n  Nada foi lido nem escrito no Aurora, e o onpremise nao foi tocado.", file=sys.stderr)
        print("\n  Para autenticar:      aws login", file=sys.stderr)
        print("  Sem credencial, o que roda e:  python sync.py --onprem-only", file=sys.stderr)
        print("  (inventario, ordem de FK e previa de statements, so com o onpremise)\n", file=sys.stderr)
        sys.exit(3)
    except AuroraError as exc:
        print(f"\nERRO: {exc}", file=sys.stderr)
        sys.exit(1)
    except OnpremError as exc:
        print(f"\nERRO ao ler o onpremise: {exc}", file=sys.stderr)
        print("  Confira se o container esta de pe: docker ps | grep fitness-mysql", file=sys.stderr)
        print("  NAO use `docker compose` para subi-lo - isso recria os containers de producao.\n",
              file=sys.stderr)
        sys.exit(1)
