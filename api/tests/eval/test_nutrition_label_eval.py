"""Eval PAGO da leitura da foto do rótulo (nutrição L6) - **fora do CI**.

Marcador `llm_eval` (excluído do `addopts` de `pytest.ini`): cada caso chama a
OpenAI de verdade com a imagem (custa dinheiro; saída não determinística).
Roda sob demanda, antes e depois de mexer no prompt, no schema ou no modelo:

    cd api && OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_nutrition_label_eval.py

Entradas: `tests/eval/casos/nutrition_label.json` + as imagens SINTÉTICAS de
`tests/eval/imagens/nutrition_labels/` (origem e licença no README da pasta).
Cada caso passa pelo MESMO caminho do worker: `nutrition_label_ai.read_label`
(saída estruturada strict com `input_image`, decisão D-E) ->
`label_photo.review_reading` -> `nutrition_label_ai.job_result`. Critérios:
`tests/eval/criterios_llm.avaliar_rotulo`.

O caso `foto_grande` amplia a imagem até perto de 1,5 MiB (a foto do celular),
reduz como a tela reduz (borda e qualidade do dono, Pillow no lugar do canvas)
e mede a latência da chamada contra o aceite do plano (§13: < 30 s); a mesma
foto sem reduzir é medida só para o registro.

Sem `OPENAI_API_KEY` todo caso dá SKIP (nunca falha por falta de chave).
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from app import nutrition_label_ai as ai
from app.domain.nutrition import ai_usage, label_photo
from tests.eval.criterios_llm import avaliar_rotulo

pytestmark = pytest.mark.llm_eval

AQUI = Path(__file__).resolve().parent
IMAGENS = AQUI / "imagens" / "nutrition_labels"
CASOS = json.loads((AQUI / "casos" / "nutrition_label.json").read_text(encoding="utf-8"))["cases"]
# Aceite do L6 (plano §13): foto de 1,5 MB lida em menos de 30 s.
ACEITE_LATENCIA_SEGUNDOS = 30.0


def _stdout_utf8() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


@pytest.fixture(autouse=True)
def _exige_chave():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY não configurada: o eval pago precisa da chave real.")


def _foto_grande(dados: bytes) -> bytes:
    """Amplia e granula a imagem até ficar logo abaixo de `LABEL_PHOTO_MAX_BYTES` (a foto do celular)."""
    img = Image.open(io.BytesIO(dados)).convert("RGB")
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    random.seed(11)
    granulado = Image.effect_noise(img.size, 22).convert("RGB")
    img = Image.blend(img, granulado, 0.05)
    melhor = b""
    for qualidade in range(95, 30, -5):
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=qualidade)
        if buffer.tell() <= label_photo.LABEL_PHOTO_MAX_BYTES:
            melhor = buffer.getvalue()
            break
    assert melhor, "não foi possível gerar a foto grande"
    return melhor


def _reduzir_como_a_tela(dados: bytes) -> bytes:
    """O que `resizeImageForUpload` faz no navegador: maior borda e qualidade do dono, JPEG sem metadados."""
    img = Image.open(io.BytesIO(dados)).convert("RGB")
    escala = min(1.0, label_photo.RESIZE_MAX_EDGE_PX / max(img.size))
    img = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=label_photo.RESIZE_JPEG_QUALITY_PERCENT)
    return buffer.getvalue()


def _ler(dados: bytes) -> tuple[ai.LabelReading, float]:
    inicio = time.monotonic()
    leitura = ai.read_label(dados)
    return leitura, time.monotonic() - inicio


def _relatorio(rotulo: str, dados: bytes, leitura: ai.LabelReading, segundos: float) -> dict:
    custo = ai_usage.estimate_cost_usd(leitura.model, leitura.tokens_in, leitura.tokens_out)
    linha = {
        "caso": rotulo,
        "bytes": len(dados),
        "modelo": leitura.model,
        "tokens_in": leitura.tokens_in,
        "tokens_out": leitura.tokens_out,
        "custo_usd": str(custo) if custo is not None else None,
        "latencia_s": round(segundos, 2),
    }
    print(f"\n[EVAL_ROTULO] {json.dumps(linha, ensure_ascii=False)}")
    return linha


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_leitura_do_rotulo_cumpre_os_criterios(caso):
    _stdout_utf8()
    original = (IMAGENS / caso["image"]).read_bytes()
    if caso.get("foto_grande"):
        grande = _foto_grande(original)
        cru, segundos_cru = _ler(grande)
        _relatorio(f"{caso['id']}/sem_reduzir", grande, cru, segundos_cru)
        enviada = _reduzir_como_a_tela(grande)
    else:
        enviada = original
    leitura, segundos = _ler(enviada)
    _relatorio(caso["id"], enviada, leitura, segundos)
    resultado = ai.job_result(label_photo.review_reading(leitura.data), leitura)
    print(json.dumps({"legibility": resultado["legibility"], "food": resultado["food"],
                      "warnings": [w["code"] for w in resultado["warnings"]],
                      "errors": [e["code"] for e in resultado["errors"]], "ai_notes": resultado["ai_notes"]},
                     ensure_ascii=False))
    problemas = avaliar_rotulo({"bruto": leitura.data, "resultado": resultado}, caso)
    if caso.get("foto_grande") and segundos >= ACEITE_LATENCIA_SEGUNDOS:
        problemas.append(f"latência {segundos:.1f} s (aceite: < {ACEITE_LATENCIA_SEGUNDOS:g} s)")
    for problema in problemas:
        print(f"  - {problema}")
    assert not problemas, "\n".join(problemas)
