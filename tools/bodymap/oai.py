"""Passos 1-2: chamadas avulsas a OpenAI Images (generations / edits).

Usa `OPENAI_API_KEY` do ambiente (nunca imprime a chave). Registra cada
chamada em `<pasta de trabalho>/custos.csv` e RECUSA a chamada se o gasto ja
registrado + a estimativa pessimista passar de `--budget` (padrao US$ 3).
So biblioteca padrao (urllib), roda no host ou no container `api` de DEV.

    python oai.py gen  --name base --prompt-file prompts/base.txt --quality high
    python oai.py edit --name segment --prompt-file prompts/segment.txt \\
        --image <pasta de trabalho>/base.png --quality medium --input-fidelity high
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BUDGET_USD = 3.00
MODEL = "gpt-image-1.5"
# Preco por imagem de SAIDA (tabela de referencia) por qualidade/tamanho.
OUT_PRICE = {
    ("low", "1536x1024"): 0.013, ("medium", "1536x1024"): 0.05, ("high", "1536x1024"): 0.20,
    ("low", "1024x1536"): 0.013, ("medium", "1024x1536"): 0.05, ("high", "1024x1536"): 0.20,
    ("low", "1024x1024"): 0.009, ("medium", "1024x1024"): 0.034, ("high", "1024x1024"): 0.133,
}
# Entrada: texto US$ 5/M; imagem US$ 10/M (pessimista; a tabela publica do
# gpt-image-1.5 e US$ 8/M). Saida: o maior entre tabela e tokens * US$ 32/M.
TEXT_IN_PER_TOKEN = 5e-6
IMAGE_IN_PER_TOKEN = 10e-6
OUT_PER_TOKEN = 32e-6
EDIT_INPUT_GUESS = 0.08  # estimativa pessimista da entrada de uma edicao (antes da chamada)

FIELDS = [
    "ts", "name", "endpoint", "model", "quality", "size", "background", "input_fidelity",
    "text_in", "image_in", "output_tokens", "cost_usd_est", "elapsed_s", "status", "note",
]


def default_out_dir() -> Path:
    try:
        from common import WORK  # noqa: PLC0415 - common exige numpy; oai.py roda sem
    except ImportError:
        return Path(os.environ.get("BODYMAP_WORK") or ".")
    return WORK


def spent(csv_path: Path) -> float:
    if not csv_path.exists():
        return 0.0
    total = 0.0
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                total += float(row.get("cost_usd_est") or 0)
            except ValueError:
                pass
    return total


def log(csv_path: Path, row: dict) -> None:
    new = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def multipart(fields: list[tuple[str, str]], files: list[tuple[str, str]]) -> tuple[bytes, str]:
    boundary = "----bodymap" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    for name, path in files:
        data = Path(path).read_bytes()
        out += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{Path(path).name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n"
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def call(url: str, data: bytes, ctype: str, timeout: float) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key or key == "replace_me":
        raise SystemExit("OPENAI_API_KEY ausente")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["gen", "edit"])
    ap.add_argument("--out-dir", type=Path, default=None, help="padrao: pasta de trabalho (BODYMAP_WORK)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--quality", default="high")
    ap.add_argument("--size", default="1536x1024")
    ap.add_argument("--background", default="transparent")
    ap.add_argument("--input-fidelity", default="high")
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--mask")
    ap.add_argument("--note", default="")
    ap.add_argument("--timeout", type=float, default=360)
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD)
    a = ap.parse_args()

    out_dir = a.out_dir or default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "custos.csv"
    prompt = Path(a.prompt_file).read_text(encoding="utf-8").strip()

    out_price = OUT_PRICE.get((a.quality, a.size), 0.25)
    guess = out_price + (EDIT_INPUT_GUESS if a.mode == "edit" else 0.01)
    already = spent(csv_path)
    if already + guess > a.budget:
        print(f"RECUSADO: gasto {already:.3f} + estimativa {guess:.3f} > teto {a.budget:.2f}")
        sys.exit(2)

    if a.mode == "gen":
        url = "https://api.openai.com/v1/images/generations"
        body = {"model": MODEL, "prompt": prompt, "size": a.size, "quality": a.quality, "n": 1,
                "output_format": "png", "background": a.background}
        data, ctype = json.dumps(body).encode(), "application/json"
        fidelity = ""
    else:
        url = "https://api.openai.com/v1/images/edits"
        fields = [("model", MODEL), ("prompt", prompt), ("size", a.size), ("quality", a.quality), ("n", "1"),
                  ("output_format", "png"), ("background", a.background), ("input_fidelity", a.input_fidelity)]
        img_field = "image[]" if len(a.image) > 1 else "image"
        files = [(img_field, p) for p in a.image]
        if a.mask:
            files.append(("mask", a.mask))
        data, ctype = multipart(fields, files)
        fidelity = a.input_fidelity

    started = time.monotonic()
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "name": a.name, "endpoint": a.mode, "model": MODEL,
           "quality": a.quality, "size": a.size, "background": a.background, "input_fidelity": fidelity,
           "note": a.note}
    try:
        resp = call(url, data, ctype, a.timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        row.update(status=f"HTTP {exc.code}", cost_usd_est="0", elapsed_s=f"{time.monotonic() - started:.1f}",
                   note=(a.note + " | " + detail.replace("\n", " "))[:500])
        log(csv_path, row)
        print("ERRO", exc.code, detail)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - timeout pode ter sido cobrado: registra o preco de tabela
        row.update(status=f"ERR {type(exc).__name__}", cost_usd_est=f"{guess:.4f}",
                   elapsed_s=f"{time.monotonic() - started:.1f}", note=(a.note + f" | {exc}")[:500])
        log(csv_path, row)
        print("ERRO", exc)
        sys.exit(1)
    elapsed = time.monotonic() - started
    usage = resp.get("usage") or {}
    det = usage.get("input_tokens_details") or {}
    text_in = int(det.get("text_tokens") or 0)
    image_in = int(det.get("image_tokens") or 0)
    if not det and usage.get("input_tokens"):
        text_in = int(usage["input_tokens"])
    out_tok = int(usage.get("output_tokens") or 0)
    cost = text_in * TEXT_IN_PER_TOKEN + image_in * IMAGE_IN_PER_TOKEN + max(out_price, out_tok * OUT_PER_TOKEN)
    item = (resp.get("data") or [{}])[0]
    (out_dir / f"{a.name}.png").write_bytes(base64.b64decode(item["b64_json"]))
    meta = {k: v for k, v in resp.items() if k != "data"}
    meta["revised_prompt"] = item.get("revised_prompt")
    meta["request"] = {"mode": a.mode, "quality": a.quality, "size": a.size, "background": a.background,
                       "input_fidelity": fidelity, "images": a.image, "mask": a.mask, "prompt_file": a.prompt_file}
    (out_dir / f"{a.name}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    row.update(text_in=text_in, image_in=image_in, output_tokens=out_tok, cost_usd_est=f"{cost:.4f}",
               elapsed_s=f"{elapsed:.1f}", status="ok")
    log(csv_path, row)
    print(f"OK {a.name} {elapsed:.1f}s usage={json.dumps(usage)} custo~{cost:.4f} acumulado~{already + cost:.4f}")


if __name__ == "__main__":
    main()
