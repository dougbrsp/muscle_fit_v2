"""Impacto da carga da PROXIMA serie: dono de REFERENCIA x dono ATUAL (regras de carga, R1).

Instrumento de "Impacto em producao" dos PRs que mudam a regra de carga da
proxima serie (`docs/motores/FONTES_DA_VERDADE.md` §36/§36.1/§59). Para UM
usuario, le TODAS as series `working` com carga do banco DEV local e avalia
cada uma DUAS vezes, com os `SetFacts` reais da serie:

- referencia: o `api/` de `--base-api` (ex.: o checkout da `develop`);
- atual: o `api/` deste checkout (ou `--current-api`), opcionalmente com um
  conjunto de regras cadastrado (`--ruleset-json <arquivo>`, o JSON de
  `ruleset_to_dict`; base do futuro R5).

Cada versao roda num SUBPROCESSO proprio, com o seu `app/` no `sys.path`, e le
os fatos com o SEU codigo: indice da serie entre as `working` do exercicio
(por `set_number`), faixa de fadiga previa do exercicio na sessao real
(`session_fatigue_reader.load_session_prior_fatigue`), rampa planejada
(`next_set_load.planned_ramp_rpe` com o `rpe_target` da ficha congelada ou
da ficha), teto efetivo da serie (`prescription_json.effective_rpe_ceiling`) e
passo/convencao do par (`engine_decision_context.load_pair_step_context`).
NENHUMA regra e calculada aqui; o script so resume o que o dono devolve. Um
dono anterior ao R1 (sem `evaluate_ruleset`) e avaliado pela API antiga
(`suggest_next_set_load`): o diff entre os dois tem que sair vazio com o
conjunto padrao.

SOMENTE LEITURA: a sessao MySQL e aberta com `SET SESSION TRANSACTION READ
ONLY`, nada e persistido e a conexao termina em rollback. So aceita
`DB_BACKEND=pymysql` (DEV local); recusa a Data API para nunca apontar para
producao. Os subprocessos rodam com `PYTHONDONTWRITEBYTECODE=1` para nao
gravar nem `__pycache__` no checkout de referencia.

Uso (de dentro de `api/`, com o MySQL do docker compose de pe e carregado com
`python scripts/prod_to_dev.py --user-id 1`):

    python scripts/replay_next_set_load.py --user-id 1 --base-api C:/muscle_fit/api
    python scripts/replay_next_set_load.py --user-id 1 --ruleset-json conjunto.json

Sem `--base-api`, a referencia e o proprio checkout com o conjunto padrao
(com `--ruleset-json`, o diff mostra o que o conjunto cadastrado mudaria).

Saida: Markdown (stdout ou `--out-md`) e JSON (`--out-json`) com, por versao,
contagens por direcao e por motivo, a tabela informativa de series por faixa
de fadiga x posicao da serie, e a lista de diferencas serie a serie. Codigo
de saida: 0 sem diferencas, 1 com diferencas, 2 erro.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

THIS_API = Path(__file__).resolve().parents[1]

DEV_DB_DEFAULTS = {
    "DB_BACKEND": "pymysql",
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": os.environ.get("MUSCLEFIT_MYSQL_HOST_PORT", "3306"),
    "MYSQL_USER": "musclefit",
    "MYSQL_PASSWORD": "musclefit",
    "MYSQL_DATABASE": "fitness",
}

EXIT_EQUAL, EXIT_DIFF, EXIT_ERROR = 0, 1, 2

OUT_OF_SCOPE = "FORA_DA_REGRA"  # o dono devolveu None (sem carga, assistido, peso corporal...)
UNKNOWN_BAND = "desconhecida"
DIFF_FIELDS = ("direction", "load_kg", "reason")
MAX_DIFF_ROWS_MD = 200


class DiffError(Exception):
    """Falha do instrumento (banco indisponivel, versao que nao roda...)."""


# ---------------------------------------------------------------------------
# Worker: roda DENTRO do subprocesso de uma versao
# ---------------------------------------------------------------------------

SETS_SQL = """
SELECT es.id AS set_id, es.exercise_id AS exercise_row_id, e.activity_id AS activity_id,
       a.started_at AS started_at, es.set_number AS set_number, es.set_type AS set_type,
       es.reps AS reps, es.weight_kg AS weight_kg, es.rpe AS rpe,
       es.joint_pain AS joint_pain, es.muscle_pain AS muscle_pain,
       COALESCE(e.mapping_id, wpe.mapping_id) AS mapping_id, e.equipment_id AS gym_equipment_id,
       wpe.prescription_json AS prescription_json, wpe.rpe_target AS plan_rpe_target
FROM exercise_sets es
JOIN exercises e ON e.id = es.exercise_id
JOIN activities a ON a.id = e.activity_id
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE a.user_id = %s AND {is_working} AND {confirmed}
ORDER BY a.id ASC, e.sequence ASC, e.id ASC, es.set_number ASC, es.id ASC
"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value.normalize()) if value == value.to_integral() else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


def _json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    try:
        decoded = json.loads(value) if isinstance(value, str) and value.strip() else None
    except ValueError:
        decoded = None
    return decoded if isinstance(decoded, dict) else {}


def _position_label(index: int) -> str:
    return "3+" if index >= 3 else str(index)


def run_worker(api: Path, user_id: int, ruleset_json: Optional[Path]) -> dict[str, Any]:
    """Le as series com o codigo de `api` e avalia com o dono de `api`."""
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
    sys.path.insert(0, str(api))
    os.chdir(api)
    if os.environ.get("DB_BACKEND") != "pymysql":
        raise DiffError("o replay so roda contra o MySQL DEV local (DB_BACKEND=pymysql)")

    from app import db  # noqa: PLC0415 - import tardio: depende do sys.path da versao
    from app.domain import next_set_load as nsl  # noqa: PLC0415
    from app.domain.effective_intensity import plan_rpe_target  # noqa: PLC0415
    from app.engine_decision_context import load_pair_step_context  # noqa: PLC0415
    from app.intelligence.session_fatigue_reader import load_session_prior_fatigue  # noqa: PLC0415
    from app.set_types import sql_is_working  # noqa: PLC0415

    # Modo Intensivo (092): série com reps pendentes fica fora, como nos motores. Versão
    # anterior à 092 não tem o predicado nem séries pendentes: filtro neutro.
    try:
        from app.set_types import sql_set_confirmed  # noqa: PLC0415

        confirmed = sql_set_confirmed("es.reps_pending")
    except ImportError:
        confirmed = "1 = 1"

    has_ruleset_api = hasattr(nsl, "evaluate_ruleset")
    ruleset = None
    if ruleset_json is not None:
        if not has_ruleset_api:
            raise DiffError("esta versao do dono nao avalia conjunto cadastrado (sem `evaluate_ruleset`)")
        ruleset = nsl.ruleset_from_dict(json.loads(ruleset_json.read_text(encoding="utf-8")))
    elif has_ruleset_api:
        ruleset = nsl.DEFAULT_RULESET

    try:
        conn = db.get_conn()
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexao vira mensagem clara
        raise DiffError(
            f"banco DEV indisponivel ({type(exc).__name__}: {exc}). Suba o MySQL "
            "(`docker compose up -d mysql`) e carregue os dados (`python scripts/prod_to_dev.py --user-id N`)."
        ) from exc
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(SETS_SQL.format(is_working=sql_is_working("es.set_type"), confirmed=confirmed), (int(user_id),))
            rows = list(cur.fetchall() or [])
        activity_ids = sorted({int(row["activity_id"]) for row in rows})
        fatigue = load_session_prior_fatigue(conn, int(user_id), activity_ids)
        pairs: dict[tuple[Any, Any], Any] = {}
        for row in rows:
            key = (row.get("mapping_id"), row.get("gym_equipment_id"))
            if key not in pairs:
                pairs[key] = load_pair_step_context(conn, key[0], key[1])
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    # Indice da serie entre as `working` do exercicio (1-based, por set_number).
    seen_per_exercise: Counter = Counter()
    sets: list[dict[str, Any]] = []
    for row in rows:
        exercise_row_id = int(row["exercise_row_id"])
        # O indice conta TODAS as `working` (com ou sem carga), como o
        # salvamento da serie (`COUNT` das anteriores + 1); so a avaliacao
        # pula as series sem carga.
        seen_per_exercise[exercise_row_id] += 1
        index = seen_per_exercise[exercise_row_id]
        weight = row.get("weight_kg")
        if weight is None or Decimal(str(weight)) <= 0:
            continue
        prior = fatigue.get(exercise_row_id)
        band = prior.band if prior is not None else None
        pair = pairs[(row.get("mapping_id"), row.get("gym_equipment_id"))]
        frozen = _json_dict(row.get("prescription_json"))
        planned_rpe = nsl.planned_ramp_rpe(plan_rpe_target(row.get("prescription_json"), row.get("plan_rpe_target")), index - 1)
        # APROXIMACAO (historico): o teto congelado na ficha daquele dia. Em
        # producao o teto e `effective_intensity.resolve_for_logged_exercise(...)
        # .ceiling` (ficha + overlay de seguranca do dia), que nao pode ser
        # reconstruido para o passado; regras com o token `max` medidas aqui
        # (R5) usam este teto. Serie sem ficha congelada: teto desconhecido.
        ceiling = frozen.get("effective_rpe_ceiling")
        common = dict(
            set_type=row.get("set_type"),
            reps=row.get("reps"),
            weight_kg=row.get("weight_kg"),
            rpe=row.get("rpe"),
            joint_pain=row.get("joint_pain"),
            muscle_pain=row.get("muscle_pain"),
            step_kg=pair.step_kg,
            load_convention=pair.load_convention,
            planned_rpe=planned_rpe,
        )
        if has_ruleset_api:
            result = nsl.evaluate_ruleset(
                nsl.SetFacts(working_set_index=index, fatigue_band=band, rpe_ceiling=ceiling, **common), ruleset
            )
        else:
            result = nsl.suggest_next_set_load(**common)
        sets.append(
            {
                "set_id": int(row["set_id"]),
                "activity_id": int(row["activity_id"]),
                "date": _jsonable(row.get("started_at")),
                "exercise_row_id": exercise_row_id,
                "index": index,
                "band": band.value if band is not None else None,
                "direction": result.direction if result is not None else OUT_OF_SCOPE,
                "load_kg": result.load_kg if result is not None else None,
                "reason": result.reason if result is not None else None,
                "rule_position": getattr(result, "rule_position", None) if result is not None else None,
            }
        )
    return {
        "api": str(api),
        "user_id": int(user_id),
        "ruleset_api": has_ruleset_api,
        "ruleset_version": getattr(ruleset, "version", None) if ruleset is not None else None,
        "ruleset_source": getattr(ruleset, "source", None) if ruleset is not None else None,
        "ruleset_json": str(ruleset_json) if ruleset_json is not None else None,
        "activities_read": len(activity_ids),
        "sets_read": len(sets),
        "sets": sets,
    }


# ---------------------------------------------------------------------------
# Orquestrador: um subprocesso por versao + resumo + diff
# ---------------------------------------------------------------------------


def _git_rev(api: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(api), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 - so informativo
        return None


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    backend = env.get("DB_BACKEND")
    if backend and backend != "pymysql":
        raise DiffError(f"DB_BACKEND={backend!r}: o replay so le o MySQL DEV local (pymysql)")
    for key, value in DEV_DB_DEFAULTS.items():
        env.setdefault(key, value)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_version(api: Path, user_id: int, ruleset_json: Optional[Path]) -> dict[str, Any]:
    if not (api / "app" / "domain" / "next_set_load.py").is_file():
        raise DiffError(f"{api} nao parece um diretorio api/ do muscle_fit (sem app/domain/next_set_load.py)")
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--api", str(api), "--user-id", str(user_id)]
    if ruleset_json is not None:
        cmd += ["--ruleset-json", str(ruleset_json.resolve())]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=_worker_env(), cwd=str(api))
    payload: dict[str, Any] = {}
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = {}
    if proc.returncode != 0 or "error" in payload or not payload:
        message = payload.get("error") or (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise DiffError(f"versao {api}: {message}")
    payload["git_rev"] = _git_rev(api)
    return payload


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    directions: Counter = Counter()
    reasons: Counter = Counter()
    band_position: Counter = Counter()
    rule_positions: Counter = Counter()
    for item in report["sets"]:
        directions[item["direction"]] += 1
        reasons[f"{item['direction']} {item['reason'] or '-'}"] += 1
        band_position[(item["band"] or UNKNOWN_BAND, _position_label(item["index"]))] += 1
        if item.get("rule_position") is not None:
            rule_positions[f"regra {item['rule_position']}"] += 1
    return {
        "sets": report["sets_read"],
        "activities": report["activities_read"],
        "directions": dict(sorted(directions.items())),
        "reasons": dict(sorted(reasons.items())),
        "band_position": {f"{band}|{position}": n for (band, position), n in sorted(band_position.items())},
        "rule_positions": dict(sorted(rule_positions.items())),
    }


def diff_reports(base: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Series em que direcao, carga ou motivo mudaram (ou que so existem num lado)."""
    diffs: list[dict[str, Any]] = []
    base_sets = {item["set_id"]: item for item in base["sets"]}
    current_sets = {item["set_id"]: item for item in current["sets"]}
    for set_id in sorted(set(base_sets) | set(current_sets)):
        b, c = base_sets.get(set_id), current_sets.get(set_id)
        if b is None or c is None:
            diffs.append({"set_id": set_id, "field": "presenca", "base": b is not None, "current": c is not None})
            continue
        for field in DIFF_FIELDS:
            if b.get(field) != c.get(field):
                diffs.append(
                    {
                        "set_id": set_id,
                        "activity_id": c["activity_id"],
                        "date": c["date"],
                        "index": c["index"],
                        "band": c["band"],
                        "field": field,
                        "base": b.get(field),
                        "current": c.get(field),
                    }
                )
    return diffs


def _md_counter(title: str, base: dict[str, int], current: dict[str, int]) -> list[str]:
    lines = [f"### {title}", "", "| item | referencia | atual | delta |", "|---|---:|---:|---:|"]
    for key in sorted(set(base) | set(current)):
        b, c = base.get(key, 0), current.get(key, 0)
        lines.append(f"| {key} | {b} | {c} | {c - b:+d} |")
    if len(lines) == 4:
        lines.append("| (nenhum) | 0 | 0 | 0 |")
    return lines + [""]


def _md_band_table(title: str, band_position: dict[str, int]) -> list[str]:
    bands = sorted({key.split("|")[0] for key in band_position})
    positions = ("1", "2", "3+")
    lines = [f"### {title}", "", "| faixa de fadiga | serie 1 | serie 2 | serie 3+ | total |", "|---|---:|---:|---:|---:|"]
    for band in bands:
        counts = [band_position.get(f"{band}|{position}", 0) for position in positions]
        lines.append(f"| {band} | " + " | ".join(str(n) for n in counts) + f" | {sum(counts)} |")
    totals = [sum(band_position.get(f"{band}|{position}", 0) for band in bands) for position in positions]
    lines.append("| **total** | " + " | ".join(str(n) for n in totals) + f" | {sum(totals)} |")
    return lines + [""]


def _version_line(name: str, report: dict[str, Any]) -> str:
    conjunto = (
        f"conjunto {report.get('ruleset_source')} v{report.get('ruleset_version')}"
        if report.get("ruleset_api")
        else "dono anterior ao R1 (API `suggest_next_set_load`)"
    )
    extra = f", json {report['ruleset_json']}" if report.get("ruleset_json") else ""
    return f"- {name}: `{report['api']}` (git {report.get('git_rev')}, {conjunto}{extra})"


def render_markdown(result: dict[str, Any]) -> str:
    base, current = result["base"], result["current"]
    sb, sc = result["summary"]["base"], result["summary"]["current"]
    lines = [
        f"# Impacto da carga da proxima serie - usuario {result['user_id']}",
        "",
        _version_line("referencia", base),
        _version_line("atual", current),
        f"- series `working` com carga lidas: referencia {base['sets_read']}, atual {current['sets_read']} "
        f"(atividades: {current['activities_read']})",
        f"- diferencas: **{len(result['differences'])}**",
        "",
    ]
    lines += _md_counter("Direcao da proxima serie", sb["directions"], sc["directions"])
    lines += _md_counter("Direcao por motivo", sb["reasons"], sc["reasons"])
    lines += _md_counter("Regra que casou (posicao no conjunto)", sb["rule_positions"], sc["rule_positions"])
    lines += _md_band_table("Series por faixa de fadiga previa x posicao da serie (informativa, versao atual)", sc["band_position"])
    lines += ["### Diferencas", ""]
    if not result["differences"]:
        lines.append("Nenhuma diferenca: os dois donos produzem a mesma sugestao em todas as series.")
    else:
        lines += ["| serie | atividade | data | posicao | faixa | campo | referencia | atual |", "|---|---|---|---|---|---|---|---|"]
        for item in result["differences"][:MAX_DIFF_ROWS_MD]:
            lines.append(
                f"| {item['set_id']} | {item.get('activity_id', '-')} | {item.get('date', '-')} | {item.get('index', '-')} "
                f"| {item.get('band') or UNKNOWN_BAND} | {item['field']} | `{json.dumps(item['base'], ensure_ascii=False)}` "
                f"| `{json.dumps(item['current'], ensure_ascii=False)}` |"
            )
        if len(result["differences"]) > MAX_DIFF_ROWS_MD:
            lines.append(f"| ... | | | | | | | mais {len(result['differences']) - MAX_DIFF_ROWS_MD} no JSON |")
    return "\n".join(lines) + "\n"


def compare(args: argparse.Namespace) -> dict[str, Any]:
    base_api = Path(args.base_api).resolve() if args.base_api else THIS_API
    current_api = Path(args.current_api).resolve() if args.current_api else THIS_API
    ruleset_json = Path(args.ruleset_json).resolve() if args.ruleset_json else None
    if ruleset_json is not None and not ruleset_json.is_file():
        raise DiffError(f"--ruleset-json: arquivo nao encontrado: {ruleset_json}")
    base = run_version(base_api, args.user_id, None)
    current = run_version(current_api, args.user_id, ruleset_json)
    return {
        "user_id": args.user_id,
        "base": base,
        "current": current,
        "summary": {"base": summarize(base), "current": summarize(current)},
        "differences": diff_reports(base, current),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Impacto da carga da proxima serie (dono de referencia x dono atual), so leitura do DEV"
    )
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--base-api", help="diretorio api/ do dono de referencia (ex.: checkout da develop)")
    parser.add_argument("--current-api", help="diretorio api/ do dono atual (default: este checkout)")
    parser.add_argument("--ruleset-json", help="JSON de um conjunto cadastrado (`ruleset_to_dict`) para a versao atual")
    parser.add_argument("--out-md", help="grava o Markdown aqui (default: stdout)")
    parser.add_argument("--out-json", help="grava o JSON completo aqui")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--api", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        try:
            payload = run_worker(Path(args.api), args.user_id, Path(args.ruleset_json) if args.ruleset_json else None)
        except Exception as exc:  # noqa: BLE001 - o orquestrador mostra a mensagem
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            return EXIT_ERROR
        print(json.dumps(_jsonable(payload), ensure_ascii=False))
        return EXIT_EQUAL

    try:
        result = compare(args)
    except DiffError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return EXIT_ERROR
    markdown = render_markdown(result)
    if args.out_md:
        Path(args.out_md).write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        print(markdown)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return EXIT_DIFF if result["differences"] else EXIT_EQUAL


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
