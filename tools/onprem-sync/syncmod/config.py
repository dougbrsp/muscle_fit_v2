"""Carregamento e validacao da configuracao declarativa (sync_config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "sync_config.json"


@dataclass(frozen=True)
class OnpremConfig:
    container: str
    database: str
    mysql_user: str
    password_env: str


@dataclass(frozen=True)
class AuroraConfig:
    region: str
    cluster_arn: str
    cluster_identifier: str
    database: str
    secret_arn: str | None = None
    profile: str | None = None
    """Perfil nomeado do AWS CLI a usar.

    Este ambiente NAO tem perfil `default`: o login fica no perfil `musclefit`
    (e `musclefit-tf` o consome via `credential_process`). Sem apontar o
    perfil, o boto3 procura o `default`, nao encontra nada e falha com
    "Unable to locate credentials" mesmo com a sessao perfeitamente valida -
    sintoma que ja confundiu na pratica.
    """


@dataclass(frozen=True)
class BatchConfig:
    max_rows_per_batch: int = 200
    max_bytes_per_batch: int = 700_000
    max_bytes_per_read_page: int = 700_000
    max_rows_per_read_page: int = 500


@dataclass(frozen=True)
class EncodingProbe:
    table: str
    pk: dict[str, str]
    column: str
    note: str = ""


@dataclass(frozen=True)
class SyncConfig:
    onprem: OnpremConfig
    aurora: AuroraConfig
    time_zone: str
    excluded_tables: dict[str, str]
    batch: BatchConfig
    encoding_probes: list[EncodingProbe] = field(default_factory=list)
    collation_exceptions: dict[str, str] = field(default_factory=dict)
    source_path: Path | None = None
    legacy_tables: dict[str, str] = field(default_factory=dict)
    """Tabelas LEGADAS que continuam no escopo (ao contrario de
    `excluded_tables`): so sao sinalizadas no relatorio e podem sumir do
    schema sem gerar aviso, para a remocao futura nao exigir mexer no sync
    antes do DROP. Ex.: `equipment_exercise_mappings` (espelho de
    `equipment_exercises`)."""

    @property
    def excluded_table_names(self) -> set[str]:
        return {t for t in self.excluded_tables if not t.startswith("_")}

    @property
    def legacy_table_names(self) -> set[str]:
        return {t for t in self.legacy_tables if not t.startswith("_")}


def _strip_comments(raw: dict[str, Any]) -> dict[str, Any]:
    """Remove chaves `_comment` usadas como documentacao inline no JSON."""
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def load_config(path: str | os.PathLike[str] | None = None) -> SyncConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Arquivo de configuracao nao encontrado: {cfg_path}")

    raw = json.loads(cfg_path.read_text(encoding="utf-8"))

    onprem_raw = _strip_comments(raw["onprem"])
    aurora_raw = _strip_comments(raw["aurora"])
    session_raw = _strip_comments(raw.get("session", {}))
    batch_raw = _strip_comments(raw.get("batch", {}))

    probes = [EncodingProbe(**p) for p in raw.get("encoding_probes", [])]

    excluded = raw.get("excluded_tables", {})
    legacy = raw.get("legacy_tables", {})
    conflict = sorted(t for t in legacy if not t.startswith("_") and t in excluded)
    if conflict:
        raise ValueError(
            "Tabela nao pode ser ao mesmo tempo legacy_tables (sincronizada) e excluded_tables: "
            + ", ".join(conflict)
        )

    return SyncConfig(
        onprem=OnpremConfig(**onprem_raw),
        aurora=AuroraConfig(**aurora_raw),
        time_zone=session_raw.get("time_zone", "+00:00"),
        excluded_tables=raw.get("excluded_tables", {}),
        batch=BatchConfig(**batch_raw),
        encoding_probes=probes,
        collation_exceptions=raw.get("collation_exceptions", {}),
        source_path=cfg_path,
        legacy_tables=legacy,
    )
