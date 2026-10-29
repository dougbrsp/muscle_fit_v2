"""Diff de replay da progressao: motor de REFERENCIA x motor ATUAL (Lote 0 RPE 10).

Instrumento de "Impacto em producao" dos lotes que mudam regra do Motor de
Progressao v2 (`docs/motores/FONTES_DA_VERDADE.md` §7). Para UM usuario, le o
historico do banco DEV local e roda `progression_evaluator.evaluate_history`
em memoria DUAS vezes:

- referencia: o `api/` de `--base-api` (ex.: um worktree da `develop`);
- atual: o `api/` deste checkout (ou `--current-api`).

Cada versao roda num SUBPROCESSO proprio, com o seu `app/` no `sys.path`, para
os modulos das duas versoes nunca se misturarem. Cada versao le o historico com
o SEU codigo (`replay_progression.fetch_sessions_paged` +
`progression_state.build_session_inputs`, o mesmo caminho do replay de
producao): se um lote mudar a leitura (ex.: passar a ler a ficha congelada), o
diff mostra isso tambem. NENHUMA regra e calculada aqui; o script so resume o
que os motores devolvem.

SOMENTE LEITURA: a sessao MySQL e aberta com `SET SESSION TRANSACTION READ
ONLY`, nada e persistido e a conexao termina em rollback. So aceita
`DB_BACKEND=pymysql` (DEV local); recusa a Data API para nunca apontar para
producao.

Uso (de dentro de `api/`, com o MySQL do docker compose de pe e carregado com
`python scripts/prod_to_dev.py --user-id 1`):

    git worktree add ../../wt-develop origin/develop        # referencia
    python scripts/replay_diff_progressao.py --user-id 1 --base-api ../../wt-develop/api
    python scripts/replay_diff_progressao.py --user-id 1 --base-api ../../wt-develop/api \
        --out-md impacto.md --out-json impacto.json

Sem `--base-api`, a referencia e o proprio checkout (diff tem que sair vazio;
serve de prova do instrumento). Conexao: variaveis `MYSQL_*` (defaults do
`.env.example` com host 127.0.0.1 e porta `MUSCLEFIT_MYSQL_HOST_PORT` ou 3306).

Saida: Markdown (stdout ou `--out-md`) e JSON (`--out-json`) com, por versao,
contagem de QUALIFY/RESET/NEUTRAL por motivo, decisoes por tipo, fase final por
grupo, `sets_count`/carga por trilha, eventos de seguranca re-derivados, acoes de
carga da trilha (regra de excesso, Lote 3/P4) e expiracoes de aumento do grupo
(Lote 3/P5) registradas na evidencia, e a PROXIMA ficha de cada trilha
(`progression_engine.prescribe` sobre o estado final, recuperacao READY); e a
lista de diferencas sessao a sessao. Codigo de saida: 0 sem diferencas, 1 com
diferencas, 2 erro (ex.: banco indisponivel).
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


class DiffError(Exception):
    """Falha do instrumento (banco indisponivel, versao que nao roda...)."""


# ---------------------------------------------------------------------------
# Worker: roda DENTRO do subprocesso de uma versao
# ---------------------------------------------------------------------------


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


def _kg(value: Any) -> Optional[str]:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def _final_state(pe: Any, group_states: Any, tracks: Any) -> dict[str, Any]:
    return {
        "groups": {
            str(gid): {
                "phase": state.phase,
                "reps": [state.reps_min, state.reps_max],
                "pending_action": state.pending_action,
            }
            for gid, state in sorted(group_states.items())
        },
        "tracks": {
            pe.track_label(key): {
                "muscle_group_id": track.muscle_group_id,
                "load_status": track.load_status,
                "working_load_kg": _kg(track.working_load_kg),
                "sets_count": track.sets_count,
                "technique_code": track.technique_code,
                # Lote 3/P4: aumento de carga pendente DA TRILHA (ausente em motores antigos)
                "pending_load_kg": _kg(getattr(track, "pending_load_kg", None)),
                # Lote 4/P6: origem do aumento pendente (ausente em motores antigos)
                "pending_load_source": getattr(track, "pending_load_source", None),
            }
            for key, track in sorted(tracks.items(), key=lambda item: tuple(str(p) for p in item[0]))
        },
    }


def run_worker(api: Path, user_id: int, window_size: int, page_size: int) -> dict[str, Any]:
    """Le o historico com o codigo de `api` e avalia com o motor de `api`."""
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
    sys.path.insert(0, str(api))
    os.chdir(api)
    if os.environ.get("DB_BACKEND") != "pymysql":
        raise DiffError("o diff de replay so roda contra o MySQL DEV local (DB_BACKEND=pymysql)")

    from app import db  # noqa: PLC0415 - import tardio: depende do sys.path da versao
    from app import progression_state as progression  # noqa: PLC0415
    from app.domain import progression_engine as pe  # noqa: PLC0415
    from app.domain import progression_evaluator as ev  # noqa: PLC0415
    from app.replay_progression import fetch_sessions_paged  # noqa: PLC0415

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
        sessions, _pages = fetch_sessions_paged(conn, user_id, page_size=page_size)
        inputs = progression.build_session_inputs(conn, user_id, sessions)
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    # 1. o replay de producao, inteiro (estado final + decisoes)
    history = ev.evaluate_history(sessions, inputs=inputs, window_size=window_size)

    # 2. o mesmo encadeamento, sessao a sessao, so para expor o veredito de cada
    #    trilha (evaluate_history nao devolve `track_outcomes`). A janela e a de
    #    evaluate_history; o estado final tem que bater (conferido abaixo).
    per_session: list[dict[str, Any]] = []
    group_states: Any = {}
    tracks: Any = {}
    for index, session in enumerate(sessions):
        window = list(sessions[max(0, index + 1 - window_size) : index + 1])
        result = ev.evaluate_session(session, window, group_states=group_states, tracks=tracks, inputs=inputs)
        group_states, tracks = result.group_states, result.tracks
        per_session.append(
            {
                "activity_id": session.activity_id,
                "date": _jsonable(session.local_date),
                "tracks": {
                    pe.track_label(key): {
                        "outcome": outcome.outcome,
                        "reasons": list(outcome.reasons),
                        "safety_events": [str(event.get("type")) for event in outcome.safety_events],
                        "working_load_kg": _kg(outcome.working_load_kg),
                        "working_sets": outcome.working_sets,
                        "intensity_status": outcome.intensity_status,
                    }
                    for key, outcome in sorted(
                        result.track_outcomes.items(), key=lambda item: tuple(str(p) for p in item[0])
                    )
                },
                "decisions": [
                    {
                        "muscle_group_id": decision.muscle_group_id,
                        "decision": decision.decision,
                        "phase_before": decision.phase_before,
                        "phase_after": decision.phase_after,
                    }
                    for decision in result.decisions
                ],
                # Lote 3/P4-P5: o que a evidencia registrou (chaves ausentes em
                # motores antigos = nada)
                "track_load_actions": {
                    label: action.get("status")
                    for decision in result.decisions
                    for label, action in (decision.evidence.get("excess_reps_fast_track") or {}).items()
                },
                "group_pending_expired": [
                    decision.muscle_group_id
                    for decision in result.decisions
                    if decision.evidence.get("pending_action_expired")
                ],
                # Lote 4/P6: aumento pelo plato DA TRILHA e F2 -> F3 pela
                # maioria das trilhas (chaves ausentes em motores antigos)
                "track_plateau_actions": {
                    label: action.get("status")
                    for decision in result.decisions
                    for label, action in (decision.evidence.get("track_plateau_load_increase") or {}).items()
                },
                "group_phase_advance": [
                    decision.muscle_group_id
                    for decision in result.decisions
                    if decision.evidence.get("group_phase_advance_by_tracks")
                    and (decision.phase_before, decision.phase_after) == ("F2", "F3")
                ],
            }
        )

    # 3. a PROXIMA ficha de cada trilha, sobre o estado final (recuperacao READY)
    last_moment = ev.session_moment(sessions[-1]) if sessions else None
    next_prescriptions: dict[str, Any] = {}
    for key, track in history.tracks.items():
        if track.muscle_group_id is None:
            continue
        group = history.group_states.get(track.muscle_group_id) or pe.new_group_state(track.muscle_group_id, last_moment)
        decision = pe.prescribe(track, group, inputs.safety_for(key), now=last_moment)
        next_prescriptions[pe.track_label(key)] = {
            "decision": decision.decision,
            "load_kg": _kg(decision.suggested_load_kg),
        }

    final = _final_state(pe, history.group_states, history.tracks)
    loop_final = _final_state(pe, group_states, tracks)
    return {
        "api": str(api),
        "engine_version": getattr(pe, "ENGINE_VERSION", None),
        "user_id": user_id,
        "sessions_read": len(sessions),
        "decisions_total": len(history.decisions),
        "decision_types": dict(Counter(d.decision for d in history.decisions)),
        "final": final,
        "loop_matches_evaluate_history": final == loop_final,
        "sessions": per_session,
        "next_prescriptions": dict(sorted(next_prescriptions.items())),
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
        raise DiffError(f"DB_BACKEND={backend!r}: o diff de replay so le o MySQL DEV local (pymysql)")
    for key, value in DEV_DB_DEFAULTS.items():
        env.setdefault(key, value)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_version(api: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not (api / "app" / "domain" / "progression_evaluator.py").is_file():
        raise DiffError(f"{api} nao parece um diretorio api/ do muscle_fit (sem app/domain/progression_evaluator.py)")
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--api",
        str(api),
        "--user-id",
        str(args.user_id),
        "--window-size",
        str(args.window_size),
        "--page-size",
        str(args.page_size),
    ]
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
    outcomes: Counter = Counter()
    by_reason: Counter = Counter()
    safety: Counter = Counter()
    load_actions: Counter = Counter()
    for session in report["sessions"]:
        for track in session["tracks"].values():
            outcomes[track["outcome"]] += 1
            reason = track["reasons"][0] if track["reasons"] else "-"
            by_reason[f"{track['outcome']} {reason}"] += 1
            for event in track["safety_events"]:
                safety[event] += 1
        for status in (session.get("track_load_actions") or {}).values():
            load_actions[f"trilha {status}"] += 1
        for status in (session.get("track_plateau_actions") or {}).values():
            load_actions[f"trilha plato {status}"] += 1
        load_actions["grupo INCREASE_LOAD expirado"] += len(session.get("group_pending_expired") or [])
        load_actions["grupo F2->F3 pela maioria das trilhas"] += len(session.get("group_phase_advance") or [])
    next_types = Counter(item["decision"] for item in (report.get("next_prescriptions") or {}).values())
    return {
        "sessions": report["sessions_read"],
        "outcomes": dict(sorted(outcomes.items())),
        "outcomes_by_reason": dict(sorted(by_reason.items())),
        "decision_types": dict(sorted(report["decision_types"].items())),
        "safety_events": dict(sorted(safety.items())),
        "phases": {gid: g["phase"] for gid, g in report["final"]["groups"].items()},
        "load_actions": {k: v for k, v in sorted(load_actions.items()) if v},
        "next_prescription_types": dict(sorted(next_types.items())),
    }


def diff_reports(base: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista de diferencas: por sessao/trilha, decisoes por sessao, estado final."""
    diffs: list[dict[str, Any]] = []
    base_sessions = {s["activity_id"]: s for s in base["sessions"]}
    current_sessions = {s["activity_id"]: s for s in current["sessions"]}
    for activity_id in sorted(set(base_sessions) | set(current_sessions)):
        b, c = base_sessions.get(activity_id), current_sessions.get(activity_id)
        if b is None or c is None:
            diffs.append({"where": f"sessao {activity_id}", "field": "presenca", "base": b is not None, "current": c is not None})
            continue
        where = f"sessao {activity_id} ({c['date']})"
        for label in sorted(set(b["tracks"]) | set(c["tracks"])):
            bt, ct = b["tracks"].get(label), c["tracks"].get(label)
            if bt == ct:
                continue
            if bt is None or ct is None:
                diffs.append({"where": f"{where} trilha {label}", "field": "presenca", "base": bt, "current": ct})
                continue
            for field in bt.keys() | ct.keys():
                if bt.get(field) != ct.get(field):
                    diffs.append({"where": f"{where} trilha {label}", "field": field, "base": bt.get(field), "current": ct.get(field)})
        if b["decisions"] != c["decisions"]:
            diffs.append({"where": where, "field": "decisoes", "base": b["decisions"], "current": c["decisions"]})
        for field in ("track_load_actions", "group_pending_expired", "track_plateau_actions", "group_phase_advance"):
            if (b.get(field) or None) != (c.get(field) or None):
                diffs.append({"where": where, "field": field, "base": b.get(field), "current": c.get(field)})
    for kind in ("groups", "tracks"):
        bmap, cmap = base["final"][kind], current["final"][kind]
        for key in sorted(set(bmap) | set(cmap)):
            if bmap.get(key) != cmap.get(key):
                label = "grupo" if kind == "groups" else "trilha"
                diffs.append({"where": f"estado final {label} {key}", "field": kind, "base": bmap.get(key), "current": cmap.get(key)})
    bnext, cnext = base.get("next_prescriptions") or {}, current.get("next_prescriptions") or {}
    for key in sorted(set(bnext) | set(cnext)):
        if bnext.get(key) != cnext.get(key):
            diffs.append({"where": f"proxima ficha trilha {key}", "field": "prescricao", "base": bnext.get(key), "current": cnext.get(key)})
    return diffs


def _md_counter(title: str, base: dict[str, int], current: dict[str, int]) -> list[str]:
    lines = [f"### {title}", "", "| item | referencia | atual | delta |", "|---|---:|---:|---:|"]
    for key in sorted(set(base) | set(current)):
        b, c = base.get(key, 0), current.get(key, 0)
        lines.append(f"| {key} | {b} | {c} | {c - b:+d} |")
    if len(lines) == 4:
        lines.append("| (nenhum) | 0 | 0 | 0 |")
    return lines + [""]


def render_markdown(result: dict[str, Any]) -> str:
    base, current = result["base"], result["current"]
    sb, sc = result["summary"]["base"], result["summary"]["current"]
    lines = [
        f"# Diff de replay da progressao - usuario {result['user_id']}",
        "",
        f"- referencia: `{base['api']}` (git {base.get('git_rev')}, motor {base.get('engine_version')})",
        f"- atual: `{current['api']}` (git {current.get('git_rev')}, motor {current.get('engine_version')})",
        f"- sessoes lidas: referencia {base['sessions_read']}, atual {current['sessions_read']}",
        f"- diferencas: **{len(result['differences'])}**",
        "",
    ]
    for name, report in (("referencia", base), ("atual", current)):
        if not report.get("loop_matches_evaluate_history", True):
            lines.append(f"> AVISO: na versao {name}, o encadeamento sessao a sessao nao bateu com evaluate_history.")
            lines.append("")
    lines += _md_counter("Veredito das trilhas (QUALIFY/RESET/NEUTRAL)", sb["outcomes"], sc["outcomes"])
    lines += _md_counter("Veredito por motivo", sb["outcomes_by_reason"], sc["outcomes_by_reason"])
    lines += _md_counter("Decisoes por tipo", sb["decision_types"], sc["decision_types"])
    lines += _md_counter("Eventos de seguranca re-derivados", sb["safety_events"], sc["safety_events"])
    lines += _md_counter(
        "Acoes de carga (trilha: excesso e plato da trilha; grupo: aumento expirado, F2->F3 pela maioria)",
        sb.get("load_actions", {}),
        sc.get("load_actions", {}),
    )
    lines += _md_counter(
        "Proxima ficha por trilha (decisao, recuperacao READY)",
        sb.get("next_prescription_types", {}),
        sc.get("next_prescription_types", {}),
    )

    lines += ["### Fase final por grupo", "", "| grupo | referencia | atual |", "|---|---|---|"]
    bg, cg = base["final"]["groups"], current["final"]["groups"]
    for gid in sorted(set(bg) | set(cg), key=lambda x: int(x)):
        fmt = lambda g: "-" if g is None else f"{g['phase']} {g['reps'][0]}-{g['reps'][1]} pend={g['pending_action']}"  # noqa: E731
        lines.append(f"| {gid} | {fmt(bg.get(gid))} | {fmt(cg.get(gid))} |")
    lines += [
        "",
        "### Trilhas (carga, series e proxima ficha)",
        "",
        "| trilha | grupo | referencia | atual |",
        "|---|---|---|---|",
    ]
    bt, ct = base["final"]["tracks"], current["final"]["tracks"]
    bn, cn = base.get("next_prescriptions") or {}, current.get("next_prescriptions") or {}

    def fmt_track(t: Optional[dict], nxt: Optional[dict]) -> str:
        if t is None:
            return "-"
        text = f"{t['working_load_kg']} kg x{t['sets_count']} {t['load_status']}"
        if t.get("pending_load_kg"):
            text += f" pend={t['pending_load_kg']} kg"
            if t.get("pending_load_source"):
                text += f" ({t['pending_load_source']})"
        if nxt:
            text += f" -> {nxt['decision']} {nxt['load_kg']} kg"
        return text

    for label in sorted(set(bt) | set(ct)):
        group = (ct.get(label) or bt.get(label))["muscle_group_id"]
        lines.append(f"| {label} | {group} | {fmt_track(bt.get(label), bn.get(label))} | {fmt_track(ct.get(label), cn.get(label))} |")
    lines += ["", "### Diferencas", ""]
    if not result["differences"]:
        lines.append("Nenhuma diferenca: os dois motores produzem o mesmo resultado.")
    else:
        lines += ["| onde | campo | referencia | atual |", "|---|---|---|---|"]
        for item in result["differences"]:
            lines.append(
                f"| {item['where']} | {item['field']} | `{json.dumps(item['base'], ensure_ascii=False)}` "
                f"| `{json.dumps(item['current'], ensure_ascii=False)}` |"
            )
    return "\n".join(lines) + "\n"


def compare(args: argparse.Namespace) -> dict[str, Any]:
    base_api = Path(args.base_api).resolve() if args.base_api else THIS_API
    current_api = Path(args.current_api).resolve() if args.current_api else THIS_API
    base = run_version(base_api, args)
    current = run_version(current_api, args)
    return {
        "user_id": args.user_id,
        "base": base,
        "current": current,
        "summary": {"base": summarize(base), "current": summarize(current)},
        "differences": diff_reports(base, current),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Diff de replay da progressao (referencia x atual), so leitura do DEV")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--base-api", help="diretorio api/ do motor de referencia (ex.: worktree da develop)")
    parser.add_argument("--current-api", help="diretorio api/ do motor atual (default: este checkout)")
    parser.add_argument("--window-size", type=int, default=21, help="janela do replay (default 21, a de producao)")
    parser.add_argument("--page-size", type=int, default=10, help="sessoes por pagina de leitura")
    parser.add_argument("--out-md", help="grava o Markdown aqui (default: stdout)")
    parser.add_argument("--out-json", help="grava o JSON completo aqui")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--api", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        try:
            payload = run_worker(Path(args.api), args.user_id, args.window_size, args.page_size)
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
