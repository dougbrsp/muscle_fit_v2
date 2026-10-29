#!/usr/bin/env python3
"""Calibra os pesos por zona de FC do motor `api/app/domain/cardio_load.py` (M99).

SÓ LEITURA. Compara o EPOC/PAI anotados do relógio (`manual_effort_metrics`)
com o tempo em zona de FC das atividades do Intervals
(`intervals_activities.hr_zone_seconds`). A 1ª rodada (27/10/2026) mostrou que
o anotado é o ACUMULADO DE 7 DIAS (MAPE leave-one-out 11,5% EPOC e 6,4% PAI)
e não o valor do dia (23,9% e 23,9%); o usuário reviu a decisão e o motor usa
a janela `cardio_load.ROLLING_WINDOW_DAYS`.

Uso (da raiz do repositório, depois de `aws sso login --profile musclefit`):

    python tools/calibrar_cardio_load.py --user-id 1               # lê produção
    python tools/calibrar_cardio_load.py --cache dados.json        # relê o cache

O que faz:

- ajuste por mínimos quadrados NÃO NEGATIVOS, dia a dia, com pesos não
  decrescentes por zona (w_z = d_1 + ... + d_z, d >= 0);
- MAPE e erro absoluto médio com leave-one-out (cada dia previsto por pesos
  ajustados sem ele);
- diagnóstico: o mesmo ajuste com outras janelas (1, 3, 5, 10, 14 dias) —
  só para comparar o erro;
- avaliação dos pesos ATUAIS do motor (janela do motor) nos mesmos dias.

Produção é SÓ LEITURA: reaproveita `DataApiReader` de `scripts/prod_to_dev.py`
(todo SQL passa por `assert_read_only_sql`). Nada é escrito na AWS. Este arquivo
fica fora de `api/`: não entra em imagem nenhuma.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

from app.domain import cardio_load, hr_zones  # noqa: E402

ZONES = cardio_load.INTERVALS_ZONE_COUNT
TARGETS = ("epoc", "pai")
DIAGNOSTIC_WINDOWS = (1, 3, 5, 10, 14)


# ---------------------------------------------------------------------------
# Leitura (produção, só SELECT)
# ---------------------------------------------------------------------------


def read_production(user_id: int, profile: str, region: str, cluster_id: str) -> dict[str, Any]:
    import prod_to_dev as p2d

    session = p2d.aws_session(profile, region)
    cluster_arn, secret_arn, database = p2d.discover_cluster(session, cluster_id)
    reader = p2d.DataApiReader(session.client("rds-data"), cluster_arn, secret_arn, database)
    uid = int(user_id)
    manual = reader.query(
        "SELECT local_date, epoc_training_load, pai_score FROM manual_effort_metrics "
        f"WHERE user_id = {uid} ORDER BY local_date"
    )
    activities = reader.query(
        "SELECT activity_date, sport_type, hr_zone_seconds FROM intervals_activities "
        f"WHERE user_id = {uid} AND activity_date IS NOT NULL ORDER BY activity_date, id"
    )
    return {
        "manual": [
            {"local_date": str(r["local_date"])[:10], "epoc": r["epoc_training_load"], "pai": r["pai_score"]}
            for r in manual
        ],
        "activities": [
            {"activity_date": str(r["activity_date"])[:10], "sport_type": r["sport_type"], "hr_zone_seconds": r["hr_zone_seconds"]}
            for r in activities
        ],
    }


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def day_minutes(activities: Sequence[dict[str, Any]]) -> dict[date, list[float]]:
    """Minutos por zona do Intervals, somados por dia (só atividades com tempo em zona)."""
    out: dict[date, list[float]] = {}
    for row in activities:
        raw = row["hr_zone_seconds"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        seconds = hr_zones.parse_intervals_zone_seconds(raw)
        if seconds is None or len(seconds) != ZONES or sum(seconds) <= 0:
            continue
        day = date.fromisoformat(row["activity_date"])
        acc = out.setdefault(day, [0.0] * ZONES)
        for i, s in enumerate(seconds):
            acc[i] += s / 60.0
    return out


def window_minutes(minutes: dict[date, list[float]], day: date, days: int) -> Optional[list[float]]:
    acc = [0.0] * ZONES
    hit = False
    for k in range(days):
        m = minutes.get(day - timedelta(days=k))
        if m:
            hit = True
            acc = [a + b for a, b in zip(acc, m)]
    return acc if hit else None


# ---------------------------------------------------------------------------
# NNLS (Lawson-Hanson) sem numpy
# ---------------------------------------------------------------------------


def _solve(a: list[list[float]], b: list[float]) -> Optional[list[float]]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            return None
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _ls(x: list[list[float]], y: list[float], cols: list[int]) -> Optional[list[float]]:
    ata = [[sum(r[i] * r[j] for r in x) for j in cols] for i in cols]
    aty = [sum(r[i] * yy for r, yy in zip(x, y)) for i in cols]
    return _solve(ata, aty)


def nnls(x: list[list[float]], y: list[float], iters: int = 200) -> list[float]:
    n = len(x[0])
    w = [0.0] * n
    passive: list[int] = []
    for _ in range(iters):
        resid = [yy - sum(a * b for a, b in zip(r, w)) for r, yy in zip(x, y)]
        grad = [sum(r[j] * e for r, e in zip(x, resid)) for j in range(n)]
        cand = [j for j in range(n) if j not in passive and grad[j] > 1e-9]
        if not cand:
            break
        passive.append(max(cand, key=lambda j: grad[j]))
        while True:
            z = _ls(x, y, passive)
            if z is None:
                passive.pop()
                break
            if all(v > 0 for v in z):
                w = [0.0] * n
                for j, v in zip(passive, z):
                    w[j] = v
                break
            alpha = min(
                w[j] / (w[j] - v) for j, v in zip(passive, z) if v <= 0 and (w[j] - v) > 0
            ) if any(v <= 0 for v in z) else 1.0
            for j, v in zip(passive, z):
                w[j] = w[j] + alpha * (v - w[j])
            passive = [j for j in passive if w[j] > 1e-12]
    return w


def fit_monotone(x: list[list[float]], y: list[float]) -> list[float]:
    """Pesos >= 0 e não decrescentes: w_z = sum(d_k, k <= z), d >= 0, ajustado
    pela feature acumulada f_k = sum(minutos_z, z >= k)."""
    cum = [[sum(r[k:]) for k in range(len(r))] for r in x]
    d = nnls(cum, y)
    w, acc = [], 0.0
    for v in d:
        acc += v
        w.append(acc)
    return w


def predict(w: Sequence[float], feats: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(w, feats))


def metrics(pairs: list[tuple[float, float]]) -> dict[str, float]:
    ape = [abs(p - r) / r for r, p in pairs if r]
    ae = [abs(p - r) for r, p in pairs]
    return {"mape": 100.0 * sum(ape) / len(ape), "mae": sum(ae) / len(ae), "n": len(pairs)}


def loo(rows: list[tuple[date, list[float], float]]) -> tuple[list[float], dict[str, float], list[tuple[date, float, float]]]:
    w_all = fit_monotone([r[1] for r in rows], [r[2] for r in rows])
    out = []
    for i, (day, feats, real) in enumerate(rows):
        train = rows[:i] + rows[i + 1:]
        w = fit_monotone([r[1] for r in train], [r[2] for r in train])
        out.append((day, real, predict(w, feats)))
    return w_all, metrics([(r, p) for _, r, p in out]), out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user-id", type=int, default=1)
    ap.add_argument("--profile", default="musclefit")
    ap.add_argument("--region", default="sa-east-1")
    ap.add_argument("--cluster-id", default="musclefit-db")
    ap.add_argument("--cache", type=Path, help="JSON com os dados (lido se existir, gravado senão)")
    args = ap.parse_args(argv)

    if args.cache and args.cache.exists():
        data = json.loads(args.cache.read_text(encoding="utf-8"))
    else:
        data = read_production(args.user_id, args.profile, args.region, args.cluster_id)
        if args.cache:
            args.cache.write_text(json.dumps(data, default=str), encoding="utf-8")

    minutes = day_minutes(data["activities"])
    manual = [m for m in data["manual"] if m["epoc"] is not None or m["pai"] is not None]
    print(f"dias manuais: {len(manual)}; dias com tempo em zona: {len(minutes)}")

    motor_window = cardio_load.ROLLING_WINDOW_DAYS
    for target in TARGETS:
        for window in (motor_window, *DIAGNOSTIC_WINDOWS):
            label = "janela do motor" if window == motor_window else "diagnostico"
            rows = []
            for m in manual:
                if m[target] is None:
                    continue
                day = date.fromisoformat(m["local_date"])
                feats = window_minutes(minutes, day, window)
                if feats is None:
                    continue
                rows.append((day, feats, float(m[target])))
            if len(rows) < ZONES + 2:
                print(f"{target} {window}d: dias insuficientes ({len(rows)})")
                continue
            w, met, detail = loo(rows)
            print(f"\n== {target.upper()} {window} dia(s) ({label}): n={met['n']} LOO MAPE={met['mape']:.1f}% MAE={met['mae']:.1f}")
            print("   pesos/min por zona:", [round(v, 3) for v in w])
            if window == motor_window:
                current = cardio_load.EPOC_ZONE_WEIGHTS if target == "epoc" else cardio_load.PAI_ZONE_WEIGHTS
                feats_by_day = {r[0]: r[1] for r in rows}
                cur = metrics([(r, predict(current, f)) for _, f, r in rows])
                print(f"   pesos do motor: MAPE={cur['mape']:.1f}% MAE={cur['mae']:.1f}")
                for day, real, pred in detail:
                    print(f"   {day} real={real:6.0f} loo={pred:6.0f} motor={predict(current, feats_by_day[day]):6.0f}")
    missing = [m["local_date"] for m in manual if date.fromisoformat(m["local_date"]) not in minutes]
    print(f"\ndias manuais sem tempo em zona: {len(missing)} {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
