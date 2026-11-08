#!/usr/bin/env python3
"""Inicia o Tech Lead na raiz do checkout, com modelos configuráveis."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model", default=os.getenv("MUSCLEFIT_LEAD_MODEL", "best"))
parser.add_argument("--fallback", default=os.getenv("MUSCLEFIT_LEAD_FALLBACK"))
args = parser.parse_args()
claude = shutil.which("claude")
if not claude:
    parser.error("Instale o Claude Code e inclua o executável no PATH.")
version = subprocess.check_output([claude, "--version"], cwd=root, text=True)
match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
if not match or tuple(map(int, match.groups())) < (2, 1, 319):
    parser.error("Use Claude Code 2.1.319 ou posterior.")
command = [claude, "--agent", "tech-lead", "--model", args.model, "--strict-mcp-config"]
if args.fallback:
    command += ["--fallback-model", args.fallback]
raise SystemExit(subprocess.call(command, cwd=root))
