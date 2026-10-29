"""Equivalente LOCAL do agendamento do Intervals.icu (melhoria M2).

Em producao quem dispara `app/intervals_sync.py` e uma regra do EventBridge
apontando para a Lambda `musclefit-intervals-sync`
(`infra/lambda_intervals_sync.tf`). Em DEV nao existe EventBridge: este script
roda EXATAMENTE a mesma funcao (`sync_users`), no container `api` do compose,
contra o MySQL local.

Nada aqui muda o comportamento do job - e so o gatilho. Nenhuma configuracao
de dev vaza para a infra real: o `DB_BACKEND=pymysql` vem do ambiente do
container de dev, e a Lambda recusa qualquer valor diferente de `data-api`.

Uso (host):
    make intervals-sync
    make intervals-sync ARGS="--days 7 --dry-run"

Uso (dentro do container):
    python scripts/run_intervals_sync.py --days 7 --user 1 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import intervals_sync  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run_intervals_sync.py", description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=None, help="janela em dias (default: INTERVALS_SYNC_DAYS ou 30)")
    parser.add_argument("--user", type=int, action="append", dest="users", help="restringe a um usuario (repetivel)")
    parser.add_argument("--dry-run", action="store_true", help="faz todo o trabalho e da rollback")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = intervals_sync.sync_users(args.users, days=args.days, dry_run=args.dry_run)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
