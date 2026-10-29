"""Checkpoint de calibracao visual dos cards ilustrados - SO DEV.

Dois modos:

1. **Gerar** (`--exercises`, custa ~US$ 0,05 por imagem em
   `gpt-image-1.5`/`medium`): roda o fluxo REAL, um exercicio por vez, contra
   o MySQL do DEV, o S3 do LocalStack e a OpenAI DE VERDADE:

       cria a geracao (mesmo servico da rota: flag, limites, idempotencia)
       -> executa o worker EM PROCESSO (`exercise_card_jobs.run_generation_job`,
          o mesmo codigo do `job_kind="exercise-card"` da plan-generator: OpenAI
          -> ilustracao no S3 -> composicao do card -> preview no S3)
       -> baixa a ilustracao crua e o card composto do LocalStack
       -> resumo (exercicio, status, latencia, custo, tamanho, erro)

2. **So recompor** (`--compose-only <generation_ids>`, SEM custo e sem gravar
   nada no banco nem no S3): le a ilustracao ja paga de cada geracao
   (`exercise-card-drafts/{gid}/illustration.png` no LocalStack) e o catalogo
   atual (musculos E slugs do mapa corporal, `load_context`), e recompoe o
   card localmente com o `exercise_card_composer` do checkout - no layout
   atual (`LAYOUT_VERSION`, hoje CARD_LAYOUT_V2: rodape com a silhueta do
   mapa corporal v1). E assim que os cards do checkpoint passam do V1 para o
   V2 sem pagar a OpenAI de novo; tambem serve para iterar o LAYOUT (medidas,
   fontes, extracao das cenas) de graca: edite `app/exercise_card_composer.py`
   e rode de novo.

NAO aprova nada: a revisao e humana (tela de admin ou
`POST /exercises/{id}/card-generations/{gid}/approve`). Cada geracao do modo 1
fica no banco do DEV como qualquer outra (historico do exercicio) e conta no
limite diario do admin (`EXERCISE_CARD_MAX_GENERATIONS_PER_DAY`).

Uso (host, com o compose de DEV no ar e OPENAI_API_KEY real no .env):

    docker compose exec api python scripts/exercise_card_calibration.py \\
        --exercises 53 22 18 112 58 1 --background transparent
    docker compose exec api python scripts/exercise_card_calibration.py \\
        --exercises 12 --instruction "pegada mais aberta"
    docker compose exec api python scripts/exercise_card_calibration.py \\
        --compose-only 41 42 43 44

Saida: `api/scripts/calibration-out/<AAAAMMDD-HHMMSS>/` no host (o container
monta `./api/scripts` em `/app/scripts`), por geracao:
`<id>-<nome>-g<geracao>-illustration.png` (crua da OpenAI) e
`<id>-<nome>-g<geracao>-card.png` (o card composto = o preview que o admin
revisa); `prancha.png` (os cards lado a lado, reduzidos); no modo 1 tambem o
prompt (`.prompt.txt`) e `summary.csv` (a mesma tabela sai no terminal).

`--background transparent|opaque` (modo 1): fundo pedido a OpenAI. O worker
usa `exercise_card_jobs.ILLUSTRATION_BACKGROUND` ("transparent"); o script
troca essa constante SO neste processo, para comparar.

Guarda de ambiente - recusa rodar se QUALQUER condicao falhar:
- `DB_BACKEND=pymysql` (producao e `data-api`; a Lambda recusa `pymysql`);
- `AWS_ENDPOINT_URL` apontando para o LocalStack (`localstack`, `localhost`
  ou `127.0.0.1`) - nada vai para (nem vem de) um bucket real;
- nenhum sinal de runtime Lambda (`AWS_LAMBDA_FUNCTION_NAME`,
  `AWS_EXECUTION_ENV`);
- `OPENAI_API_KEY` configurada (so no modo 1; `--compose-only` nao chama a
  OpenAI).
Nada disto existe em `infra/`, nos Dockerfiles de producao ou nos workflows.

Flag: o servico exige `EXERCISE_CARD_GENERATION_ENABLED=true` (padrao do
compose de DEV). Para desligar, `EXERCISE_CARD_GENERATION_ENABLED=false` no
`.env` + `docker compose up -d api`.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import statistics
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_OUT_ROOT = Path(__file__).resolve().parent / "calibration-out"
LOCAL_HOSTS = ("localstack", "localhost", "127.0.0.1")
BACKGROUNDS = ("transparent", "opaque")
SUMMARY_FIELDS = (
    "exercise_id",
    "exercise_name",
    "text_level",
    "generation_id",
    "status",
    "latency_s",
    "cost_usd",
    "bytes",
    "width",
    "height",
    "tokens_in",
    "tokens_out",
    "error_code",
    "error_message",
    "illustration_file",
    "card_file",
)

# Prancha: os cards lado a lado, reduzidos, no fundo da marca.
BOARD_CARD_WIDTH = 400
BOARD_GAP = 16
BOARD_BACKGROUND = (10, 10, 10)


class NotDevEnvironment(RuntimeError):
    pass


def check_dev_environment(env: Optional[dict[str, str]] = None, *, require_openai: bool = True) -> None:
    """Levanta `NotDevEnvironment` com o motivo se nao for o DEV local."""
    env = dict(os.environ if env is None else env)
    problems: list[str] = []
    if env.get("DB_BACKEND") != "pymysql":
        problems.append("DB_BACKEND precisa ser 'pymysql' (MySQL do compose de DEV).")
    endpoint = (env.get("AWS_ENDPOINT_URL") or "").strip()
    host = (urlparse(endpoint).hostname or "") if endpoint else ""
    if host not in LOCAL_HOSTS:
        problems.append("AWS_ENDPOINT_URL precisa apontar para o LocalStack (localstack/localhost/127.0.0.1).")
    if env.get("AWS_LAMBDA_FUNCTION_NAME") or env.get("AWS_EXECUTION_ENV"):
        problems.append("runtime Lambda detectado (AWS_LAMBDA_FUNCTION_NAME/AWS_EXECUTION_ENV).")
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if require_openai and (not key or key == "replace_me"):
        problems.append("OPENAI_API_KEY nao configurada (o checkpoint usa a OpenAI real).")
    if problems:
        raise NotDevEnvironment(" ".join(problems))


def _slug(text: str, limit: int = 40) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return (re.sub(r"[^a-zA-Z0-9]+", "-", plain).strip("-").lower() or "exercicio")[:limit]


def _stem(exercise_id: int, name: str, generation_id: int) -> str:
    return f"{exercise_id}-{_slug(name)}-g{generation_id}"


def build_board(cards: Sequence[bytes]) -> bytes:
    """Prancha PNG com os cards lado a lado (cada um reduzido a
    `BOARD_CARD_WIDTH` de largura). Puro: sem banco nem S3."""
    from PIL import Image

    if not cards:
        raise ValueError("Nenhum card para a prancha.")
    thumbs = []
    for data in cards:
        with Image.open(io.BytesIO(data)) as raw:
            image = raw.convert("RGB")
        height = round(BOARD_CARD_WIDTH * image.height / image.width)
        thumbs.append(image.resize((BOARD_CARD_WIDTH, height), Image.Resampling.LANCZOS))
    width = BOARD_GAP + len(thumbs) * (BOARD_CARD_WIDTH + BOARD_GAP)
    height = BOARD_GAP * 2 + max(t.height for t in thumbs)
    board = Image.new("RGB", (width, height), BOARD_BACKGROUND)
    for index, thumb in enumerate(thumbs):
        board.paste(thumb, (BOARD_GAP + index * (BOARD_CARD_WIDTH + BOARD_GAP), BOARD_GAP))
    buffer = io.BytesIO()
    board.save(buffer, format="PNG")
    return buffer.getvalue()


def _first_admin_id() -> Optional[int]:
    from app.db import transaction

    with transaction() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id LIMIT 1")
            row = cursor.fetchone()
    return int(row["id"]) if row else None


def _exercise_name(exercise_id: int) -> str:
    from app.db import transaction

    with transaction() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM exercise_catalog WHERE id = %s", (int(exercise_id),))
            row = cursor.fetchone()
    return str(row["name"]) if row else ""


def _save_drafts(generation_id: int, stem: str, out_dir: Path, summary: dict[str, Any]) -> Optional[bytes]:
    """Baixa ilustracao e card do LocalStack; devolve os bytes do card."""
    from app import exercise_card_storage as storage

    card: Optional[bytes] = None
    for field, key, suffix in (
        ("illustration_file", storage.illustration_key(generation_id), "illustration"),
        ("card_file", storage.draft_key(generation_id), "card"),
    ):
        try:
            data = storage.read_draft(key)
        except storage.ExerciseCardStorageError:
            continue
        target = out_dir / f"{stem}-{suffix}.png"
        target.write_bytes(data)
        summary[field] = target.name
        if suffix == "card":
            card = data
    return card


def run_one(
    exercise_id: int,
    *,
    user_id: int,
    text_level: str,
    instruction: Optional[str],
    out_dir: Path,
) -> dict[str, Any]:
    from app import exercise_card_jobs as jobs
    from app import exercise_card_service as svc

    name = _exercise_name(exercise_id)
    summary: dict[str, Any] = {"exercise_id": exercise_id, "exercise_name": name, "text_level": text_level}
    try:
        row, _ = svc.create_generation(
            exercise_id=exercise_id,
            user_id=user_id,
            idempotency_key=str(uuid.uuid4()),
            instruction=instruction,
            text_level=text_level,
        )
    except svc.ExerciseCardError as exc:
        summary.update(status="NOT_CREATED", error_code=exc.code, error_message=exc.message)
        return summary

    generation_id = int(row["id"])
    summary["generation_id"] = generation_id
    started = time.monotonic()
    try:
        jobs.run_generation_job({"generation_id": generation_id, "exercise_id": exercise_id})
    except jobs.ExerciseCardJobError:
        pass  # a linha ja esta FAILED com error_code/error_message
    summary["latency_s"] = round(time.monotonic() - started, 1)

    row = svc.load_generation(generation_id) or {}
    summary.update(
        status=row.get("status"),
        cost_usd=row.get("cost_usd"),
        bytes=row.get("bytes"),
        width=row.get("width"),
        height=row.get("height"),
        tokens_in=row.get("tokens_in"),
        tokens_out=row.get("tokens_out"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
    )
    stem = _stem(exercise_id, name, generation_id)
    if row.get("prompt_text"):
        (out_dir / f"{stem}.prompt.txt").write_text(row["prompt_text"], encoding="utf-8")
    # FAILED por composicao tambem tem a ilustracao paga: baixa o que houver.
    summary["_card"] = _save_drafts(generation_id, stem, out_dir, summary)
    return summary


def compose_one(generation_id: int, *, out_dir: Path) -> dict[str, Any]:
    """Recompoe o card de uma geracao a partir da ilustracao guardada, SEM
    OpenAI e sem gravar no banco/S3 (so le)."""
    from app import exercise_card_composer as composer
    from app import exercise_card_prompt as card_prompt
    from app import exercise_card_service as svc
    from app import exercise_card_storage as storage
    from app.db import transaction

    summary: dict[str, Any] = {"generation_id": generation_id}
    row = svc.load_generation(generation_id, include_prompt=False)
    if not row:
        summary.update(status="NOT_FOUND", error_message="geracao inexistente")
        return summary
    exercise_id = int(row["exercise_id"])
    with transaction() as conn:
        with conn.cursor() as cursor:
            context = card_prompt.load_context(cursor, exercise_id)
    name = context.name if context else ""
    summary.update(exercise_id=exercise_id, exercise_name=name, text_level=row.get("text_level"))
    if context is None:
        summary.update(status="EXERCISE_NOT_FOUND")
        return summary
    stem = _stem(exercise_id, name, generation_id)
    try:
        illustration = storage.read_draft(storage.illustration_key(generation_id))
    except storage.ExerciseCardStorageError as exc:
        summary.update(status="NO_ILLUSTRATION", error_message=f"{exc} (geracao anterior a Estrategia B?)")
        return summary
    (out_dir / f"{stem}-illustration.png").write_bytes(illustration)
    summary["illustration_file"] = f"{stem}-illustration.png"
    started = time.monotonic()
    try:
        card = composer.compose_card(illustration, composer.content_from_context(context), composer.load_card_assets())
    except composer.CardCompositionError as exc:
        summary.update(status="COMPOSITION_FAILED", error_code=exc.code, error_message=f"{exc.reason}: {exc}")
        return summary
    summary["latency_s"] = round(time.monotonic() - started, 2)
    (out_dir / f"{stem}-card.png").write_bytes(card)
    summary.update(status="COMPOSED", bytes=len(card), card_file=f"{stem}-card.png", _card=card)
    return summary


def _print_table(rows: list[dict[str, Any]]) -> None:
    columns = ("exercise_id", "exercise_name", "generation_id", "status", "latency_s", "cost_usd", "bytes",
               "error_code")
    widths = {c: max(len(c), *(len(str(r.get(c) if r.get(c) is not None else "")) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for row in rows:
        print("  ".join(str(row.get(c) if row.get(c) is not None else "").ljust(widths[c]) for c in columns))


def _print_totals(rows: list[dict[str, Any]]) -> None:
    generated = [r for r in rows if r.get("status") == "GENERATED"]
    latencies = sorted(float(r["latency_s"]) for r in generated if r.get("latency_s") is not None)
    cost = sum(float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None)
    print(f"\n{len(generated)}/{len(rows)} gerada(s); custo estimado US$ {cost:.3f}")
    if latencies:
        p95 = latencies[min(len(latencies) - 1, max(0, int(round(0.95 * len(latencies))) - 1))]
        print(f"latencia p50 {statistics.median(latencies):.1f}s | p95 {p95:.1f}s | max {latencies[-1]:.1f}s")


def _write_board(rows: list[dict[str, Any]], out_dir: Path) -> None:
    cards = [r["_card"] for r in rows if r.get("_card")]
    if cards:
        (out_dir / "prancha.png").write_bytes(build_board(cards))
        print(f"prancha.png com {len(cards)} card(s)")


def main(argv: Optional[list[str]] = None) -> int:
    from app import exercise_card_prompt as card_prompt

    parser = argparse.ArgumentParser(
        prog="exercise_card_calibration.py", description=__doc__.splitlines()[0]
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--exercises", type=int, nargs="+", help="ids de exercise_catalog (gera: CUSTA)")
    mode.add_argument(
        "--compose-only", type=int, nargs="+", metavar="GENERATION_ID",
        help="so recompoe o card das geracoes a partir da ilustracao guardada (sem custo)",
    )
    parser.add_argument("--text-level", default=card_prompt.DEFAULT_TEXT_LEVEL, choices=card_prompt.TEXT_LEVELS)
    parser.add_argument("--background", default="transparent", choices=BACKGROUNDS,
                        help="fundo pedido a OpenAI para a ilustracao (so ao gerar)")
    parser.add_argument("--instruction", default=None, help="pedido de revisao (vai ao prompt como o do admin)")
    parser.add_argument("--user-id", type=int, default=None, help="admin dono das geracoes (padrao: 1o admin ativo)")
    parser.add_argument("--out", type=Path, default=None, help=f"pasta de saida (padrao: {DEFAULT_OUT_ROOT}/<timestamp>)")
    args = parser.parse_args(argv)
    compose_only = bool(args.compose_only)

    try:
        check_dev_environment(require_openai=not compose_only)
    except NotDevEnvironment as exc:
        print(f"RECUSADO: este script so roda no DEV local. {exc}", file=sys.stderr)
        return 2

    out_dir = args.out or (DEFAULT_OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saida: {out_dir}")

    rows: list[dict[str, Any]] = []
    if compose_only:
        from app.exercise_card_composer import LAYOUT_VERSION

        print(f"Layout: {LAYOUT_VERSION}")
        for generation_id in args.compose_only:
            print(f"-> recompondo a geracao {generation_id} (sem OpenAI)...", flush=True)
            result = compose_one(generation_id, out_dir=out_dir)
            print(f"   {result.get('status')} {result.get('error_message') or ''}", flush=True)
            rows.append(result)
        print()
        _print_table(rows)
        _write_board(rows, out_dir)
        print(f"\nCards em {out_dir}. Nada foi gravado no banco nem no S3.")
        return 0 if all(r.get("status") == "COMPOSED" for r in rows) else 1

    user_id = args.user_id or _first_admin_id()
    if not user_id:
        print("Nenhum admin ativo no banco; informe --user-id.", file=sys.stderr)
        return 2

    from app import exercise_card_jobs as jobs

    jobs.ILLUSTRATION_BACKGROUND = args.background  # so neste processo de DEV
    for exercise_id in args.exercises:
        print(f"-> exercicio {exercise_id} (fundo {args.background})...", flush=True)
        result = run_one(
            exercise_id, user_id=user_id, text_level=args.text_level, instruction=args.instruction, out_dir=out_dir
        )
        print(f"   {result.get('status')} em {result.get('latency_s', '-')}s {result.get('error_code') or ''}", flush=True)
        rows.append(result)

    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print()
    _print_table(rows)
    _print_totals(rows)
    _write_board(rows, out_dir)
    print(f"\nImagens e summary.csv em {out_dir}. Nada foi aprovado.")
    return 0 if all(r.get("status") == "GENERATED" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
