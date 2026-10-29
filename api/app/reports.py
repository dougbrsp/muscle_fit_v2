"""Relatorio em Markdown: `GET /reports/markdown`.

Substitui o relatorio PDF + IA assincrono (Lambda `musclefit-reports`, S3 e
polling). Agora o arquivo e gerado NA HORA, dentro da api-core, so com dados
(sem IA) e sem historico: a resposta ja e o `.md` para download.

A tabela `reports` continua no banco (sem migracao destrutiva); este modulo
simplesmente deixou de usa-la.

Janela no fuso do usuario (`users.timezone`), mesma base das abas do
dashboard:
- `window=30d|90d|180d` - rolling terminando hoje (padrao `90d`);
- `window=custom` com `date_from`/`date_to` - de 7 a 180 dias, sem data
  futura. Fora disso: 422 com a mensagem do que corrigir.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .db import get_conn
from .db_helpers import connect_or_503
from .intelligence.dashboard.contracts import WindowKind
from .intelligence.dashboard.time_windows import resolve_window, user_now, user_timezone
from .intelligence.report.markdown_renderer import render_markdown
from .intelligence.report.markdown_report_service import (
    collect_report_data,
    default_custom_today_window,
    report_filename,
)
from .security import Identity, get_current_identity

router = APIRouter(prefix="/reports", tags=["reports"])

logger = logging.getLogger("fitness.reports")

MIN_REPORT_DAYS = 7
MAX_REPORT_DAYS = 180

ReportWindow = Literal["30d", "90d", "180d", "custom"]

MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"


def _connect():
    return connect_or_503(get_conn)


def resolve_report_window(
    window: str,
    *,
    today: date,
    timezone_name: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    """`WindowInfo` do relatorio. `ValueError` com a mensagem para o usuario
    quando a janela custom e invalida (o endpoint devolve 422)."""
    if window == "90d":
        return resolve_window(WindowKind.ROLLING_90D, today=today, timezone_name=timezone_name)
    if window == "180d":
        return resolve_window(WindowKind.ROLLING_180D, today=today, timezone_name=timezone_name)
    if window == "30d":
        start, end = default_custom_today_window(today, 30)
        return resolve_window(WindowKind.CUSTOM, today=today, date_from=start, date_to=end, timezone_name=timezone_name)

    if date_from is None or date_to is None:
        raise ValueError("Período personalizado exige data inicial e data final.")
    if date_from > date_to:
        raise ValueError("A data inicial não pode ser depois da data final.")
    if date_to > today:
        raise ValueError("A data final não pode estar no futuro.")
    days = (date_to - date_from).days + 1
    if days < MIN_REPORT_DAYS or days > MAX_REPORT_DAYS:
        raise ValueError(
            f"O período deve ter entre {MIN_REPORT_DAYS} e {MAX_REPORT_DAYS} dias (recebido: {days})."
        )
    return resolve_window(
        WindowKind.CUSTOM, today=today, date_from=date_from, date_to=date_to, timezone_name=timezone_name
    )


@router.get("/markdown")
def download_markdown_report(
    window: ReportWindow = Query(default="90d"),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> Response:
    """Relatorio do periodo em Markdown, como anexo
    `relatorio-musclefit-<de>_<ate>.md`.

    So leitura. Toda consulta sai com `user_id` da sessao - nao ha parametro
    que escolha outro usuario."""
    with _connect() as conn:
        timezone_name = user_timezone(conn, identity.user_id)
        now_local = user_now(timezone_name)
        try:
            resolved = resolve_report_window(
                window,
                today=now_local.date(),
                timezone_name=timezone_name,
                date_from=date_from,
                date_to=date_to,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        data = collect_report_data(conn, user_id=identity.user_id, window=resolved, now_local=now_local)

    content = render_markdown(data)
    filename = report_filename(resolved.date_from, resolved.date_to)
    return Response(
        content=content.encode("utf-8"),
        media_type=MARKDOWN_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
