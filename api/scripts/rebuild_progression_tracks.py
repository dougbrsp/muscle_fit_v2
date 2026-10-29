"""Replay do Motor de Progressao v2 a partir do historico de treino (PR-10;
PLANO_20260912.md secao 5.11: "Script de replay reconstroi as trilhas a partir
do historico").

    python -m scripts.rebuild_progression_tracks --all
    python -m scripts.rebuild_progression_tracks --user-id 1
    python -m scripts.rebuild_progression_tracks --user-id 1 --dry-run
    python -m scripts.rebuild_progression_tracks --all --cleanup   # retencao D15

Este script e a interface de LINHA DE COMANDO (DEV) do replay; a logica de
operacao esta em `app/replay_progression.py`, compartilhada com a acao
`{"action": "replay-progression"}` da Lambda `musclefit-migrations`
(`app/migrate.py`), que e como o replay roda em PRODUCAO - sem acesso manual a
AWS. As duas entradas executam exatamente o mesmo caminho, inclusive a leitura
paginada do historico, para nao existir "funciona em DEV, diverge em producao".

IDEMPOTENTE: rodar duas vezes seguidas deixa o mesmo estado. O replay apaga o
estado e as decisoes do usuario e reprocessa a historia inteira com janela
deslizante de 21 sessoes; como a sequencia de qualificacao e sempre
recalculada da janela (modelo "inferido + eventos"), nao existe acumulo. O
`fingerprint` de cada usuario no relatorio e a prova: duas execucoes tem que
devolver o mesmo valor.

Nao depende da flag `PROGRESSION_ENGINE`: o replay e uma ferramenta de
bootstrap/diagnostico. Ele tambem NAO toca em ficha, prescricao ou qualquer
tabela fora das tres da migracao 036.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.replay_progression import replay  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconstroi trilhas e fases da progressao a partir do historico")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-id", type=int, action="append", dest="user_ids", help="usuario (repetivel)")
    group.add_argument("--all", action="store_true", help="todos os usuarios com historico de series")
    parser.add_argument("--dry-run", action="store_true", help="calcula e desfaz, sem gravar")
    parser.add_argument("--cleanup", action="store_true", help="tambem aplica a retencao de 18 meses (D15)")
    parser.add_argument("--page-size", type=int, default=None, help="sessoes por pagina de leitura do historico")
    # Na Lambda o orcamento vem do tempo restante da invocacao; na CLI nao ha
    # timeout externo, entao o default e alto e serve so de rede de seguranca.
    parser.add_argument("--time-budget", type=float, default=3600.0, help="segundos (default 3600)")
    args = parser.parse_args(argv)

    report = replay(
        None if args.all else args.user_ids,
        dry_run=args.dry_run,
        cleanup=args.cleanup,
        page_size=args.page_size,
        time_budget_seconds=args.time_budget,
        include_snapshot=True,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
