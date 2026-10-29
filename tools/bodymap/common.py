"""Utilitarios do pipeline do mapa corporal (roda no HOST, num venv com
`requirements-tools.txt`; NUNCA nas imagens Docker da API).

Pasta de trabalho (imagens da OpenAI, intermediarios .npy, mascaras brutas):
`BODYMAP_WORK` no ambiente, ou `api/scripts/calibration-out/bodymap-work`
(ignorada pelo git). Saida final: `api/app/assets/bodymap/v1/`.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent.parent
WORK = Path(os.environ.get("BODYMAP_WORK") or REPO_ROOT / "api" / "scripts" / "calibration-out" / "bodymap-work")
ASSETS = REPO_ROOT / "api" / "app" / "assets" / "bodymap" / "v1"
PROMPTS = TOOLS_DIR / "prompts"
ASSIGNMENT = TOOLS_DIR / "assignment.json"

W, H = 1536, 1024
SPLIT_X = W // 2  # figura da frente a esquerda, costas a direita

# Nomes dos arquivos na pasta de trabalho.
BASE_NAME = "base"  # arte base (gpt-image-1.5 high, transparente)
SEG_NAME = "segment"  # edicao de segmentacao (cores chapadas)

# Slugs do banco (muscle_group_body_map) + extras.
DB_SLUGS = ["abs", "obliques", "adductor", "forearm", "quadriceps", "biceps", "upper-back", "trapezius",
            "gluteal", "lower-back", "front-deltoids", "back-deltoids", "chest", "hamstring", "triceps", "calves"]
EXTRA_SLUGS = ["neck"]
ALIASES = {"abductors": "adductor"}

# Paleta pedida na edicao de segmentacao (prompts/segment.txt) - so para
# visualizar; a IA nao a respeita fielmente, por isso a rotulagem e humana.
SEG_PALETTE = {
    "chest": (255, 0, 0), "abs": (255, 255, 0), "obliques": (0, 255, 255), "biceps": (0, 0, 255),
    "triceps": (255, 0, 255), "forearm": (0, 255, 0), "front-deltoids": (255, 128, 0),
    "back-deltoids": (128, 0, 255), "quadriceps": (0, 128, 255), "adductor": (255, 0, 128),
    "calves": (128, 255, 0), "trapezius": (0, 255, 128), "upper-back": (128, 64, 0),
    "lower-back": (160, 160, 255), "gluteal": (0, 128, 128), "hamstring": (128, 0, 0), "neck": (128, 128, 0),
}


def work(name: str) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK / name


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)


def save_mask(mask: np.ndarray, path: Path) -> None:
    """Mascara 1 bit: PNG modo '1' (0/255)."""
    Image.fromarray((mask > 0).astype(np.uint8) * 255).convert("1").save(path, optimize=True)
