"""Backup do estado atual do Aurora antes de qualquer escrita.

Salvaguarda inegociavel: `--apply` aborta se o backup nao for concluido com
sucesso. E o que reconcilia "espelhar exatamente" (que apaga linhas que so
existem no Aurora) com "nao pode haver perda de dados".

Formato: um `.json` por tabela com as linhas na forma canonica (todo valor
como texto ou null), mais um `manifest.json`. O JSON e a fonte de restauracao
de verdade - `sync.py --restore-from DIR` recarrega esses arquivos pelo mesmo
caminho de escrita do sync. O `restore.sql` gerado ao lado e um plano B
legivel, para quem tiver acesso direto ao banco (bastion/DBeaver) e quiser
conferir ou aplicar a mao.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import Schema, Table


Row = dict[str, str | None]


def new_backup_dir(base: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def _sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\x00", "\\0")
        .replace("\x1a", "\\Z")
    )
    return f"'{escaped}'"


def write_backup(
    directory: Path,
    schema: Schema,
    aurora_rows: dict[str, list[Row]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    data_dir = directory / "data"
    data_dir.mkdir(exist_ok=True)

    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": "canonical-string-rows/v1",
        "note": (
            "Todo valor esta serializado como TEXTO (ou null), na mesma forma "
            "canonica usada para comparacao. A reconversao para o tipo da "
            "coluna e feita por syncmod.schema.Column.data_api_param na hora "
            "de restaurar."
        ),
        "tables": {},
        **metadata,
    }

    for table_name, rows in sorted(aurora_rows.items()):
        table = schema[table_name]
        payload = {
            "table": table_name,
            "columns": table.column_names,
            "pk_columns": list(table.pk_columns),
            "row_count": len(rows),
            "rows": rows,
        }
        (data_dir / f"{table_name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        manifest["tables"][table_name] = {"row_count": len(rows)}

    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _write_restore_sql(directory / "restore.sql", schema, aurora_rows)
    return manifest


def _write_restore_sql(path: Path, schema: Schema, aurora_rows: dict[str, list[Row]]) -> None:
    lines: list[str] = [
        "-- Restauracao do estado do Aurora capturado por tools/onprem-sync.",
        "-- Plano B legivel. O caminho oficial e `python sync.py --restore-from <dir>`,",
        "-- porque daqui da maquina do dev nao existe conexao TCP com o cluster.",
        "SET SESSION time_zone = '+00:00';",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "START TRANSACTION;",
        "",
    ]

    for table_name, rows in sorted(aurora_rows.items()):
        table: Table = schema[table_name]
        lines.append(f"-- {table_name}: {len(rows)} linha(s)")
        lines.append(f"DELETE FROM `{table_name}`;")
        if rows:
            cols = ", ".join(f"`{c}`" for c in table.column_names)
            for row in rows:
                values = ", ".join(_sql_literal(row.get(c)) for c in table.column_names)
                lines.append(f"INSERT INTO `{table_name}` ({cols}) VALUES ({values});")
        if table.auto_increment is not None:
            lines.append(f"-- AUTO_INCREMENT original do onprem: {table.auto_increment}")
        lines.append("")

    lines += ["COMMIT;", "SET FOREIGN_KEY_CHECKS = 1;", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def load_backup(directory: Path) -> dict[str, list[Row]]:
    data_dir = directory / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Backup invalido: {data_dir} nao existe.")
    out: dict[str, list[Row]] = {}
    for file in sorted(data_dir.glob("*.json")):
        payload = json.loads(file.read_text(encoding="utf-8"))
        out[payload["table"]] = payload["rows"]
    return out
