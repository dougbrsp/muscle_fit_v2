from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

logger = logging.getLogger("fitness.intelligence")


class IntelligenceServiceBase:
    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        # PERF-05: um statement de leitura, sem transacao explicita (na Data API
        # eram BeginTransaction + ExecuteStatement + RollbackTransaction = 3 HTTP
        # por consulta; agora 1). Mesmo resultado: ver `db.fetch_rows`.
        from ..db import fetch_rows

        return [self._jsonable_row(row) for row in fetch_rows(sql, params)]

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else {}

    # ------------------------------------------------------------------
    # Relogio do usuario (M76 Lote 2b): todo servico de motor ancora DIA,
    # SEMANA e JANELA no fuso de `users.timezone`, via `time_windows` (dono).
    # Cache por instancia (o servico vive uma requisicao).
    # ------------------------------------------------------------------

    def _user_timezone_name(self, user_id: int | None) -> str:
        """Fuso do usuario, com o MESMO fallback de `time_windows.user_timezone`
        (`normalize_timezone` -> America/Sao_Paulo). Sem `user_id` (chamadores
        legados sem identidade) ou se a leitura falhar, devolve o default: um
        fuso ruim nunca pode derrubar o overview."""
        from ..timezones import DEFAULT_TIMEZONE, normalize_timezone

        if user_id is None:
            return DEFAULT_TIMEZONE
        cache = self.__dict__.setdefault("_timezone_cache", {})
        cached = cache.get(user_id)
        if cached is not None:
            return cached
        from .. import request_memo
        from .dashboard.time_windows import USER_TIMEZONE_MEMO

        def read() -> str:
            row = self.fetch_one("SELECT timezone FROM users WHERE id = %s", (int(user_id),))
            return normalize_timezone((row or {}).get("timezone"))

        try:
            name = request_memo.memoize((USER_TIMEZONE_MEMO, int(user_id)), read)
        except Exception:  # noqa: BLE001 - banco anterior a 044 / fetch indisponivel
            name = DEFAULT_TIMEZONE
        cache[user_id] = name
        return name

    def _user_now(self, user_id: int | None, now_local: datetime | None = None) -> datetime:
        """"Agora" na parede do usuario, NAIVE - mesma convencao de
        `activities.started_at`/`trained_at` (hora LOCAL sem fuso). Usar
        `datetime.now()` aqui comparava o relogio UTC da Lambda com horarios
        locais e inflava em +3h as horas desde o ultimo estimulo.

        `now_local` (opcional) vem do chamador que ja resolveu o fuso (ex.:
        rotas do dashboard) e evita a consulta."""
        if now_local is not None:
            return now_local
        from .dashboard.time_windows import user_now

        return user_now(self._user_timezone_name(user_id))

    def _user_today(self, user_id: int | None) -> date:
        """"Hoje" do usuario (`time_windows.user_today`) - a ancora de toda
        janela em dias deste servico, passada ao SQL como parametro no lugar
        de `CURDATE()` (dia UTC do Aurora). M76 Lote 2b."""
        return self._user_now(user_id).date()

    def _days_ago(self, user_id: int | None, days: int) -> date:
        """Primeiro dia de uma janela de `days` dias contada do "hoje" do
        usuario - mesma conta de `DATE_SUB(CURDATE(), INTERVAL n DAY)`, no fuso
        certo."""
        return self._user_today(user_id) - timedelta(days=int(days))

    def _trained_at_sql(self, user_id: int | None, alias: str = "a") -> str:
        """`time_windows.trained_at_sql` no fuso do usuario."""
        from .dashboard.time_windows import trained_at_sql, utc_offset_minutes

        return trained_at_sql(alias, offset_minutes=utc_offset_minutes(self._user_timezone_name(user_id)))

    def get_latest_intervals_wellness(self, user_id: int | None = None) -> dict[str, Any] | None:
        """Linha mais recente de `intervals_wellness` (CTL/ATL/TSB/ramp_rate
        sincronizados do Intervals.icu), ou `None` se a tabela ainda nao tem
        nenhuma linha sincronizada. Query compartilhada, reaproveitavel por
        qualquer service que precise de um sinal global de fadiga/prontidao
        (hoje: `MuscleRecoveryService` e o prompt de geracao de ficha).

        A linha vem SEM filtro de idade e com `user_timezone` (fuso do dono):
        quem usa decide com `wellness_freshness.is_row_stale` (M52) - dado com
        mais de `WELLNESS_MAX_AGE_DAYS` dias vale como ausente.

        Multiusuario (Parte 2): `intervals_wellness.user_id` agora e
        NOT NULL (cada linha pertence a exatamente um usuario). Quando
        `user_id` e informado, filtra por ele. Quando `None` (a maioria das
        rotas hoje - dashboard_modules.py/dashboard_intelligence.py/
        workout_plan_ai.py ainda autenticam so com `require_token`, sem
        `Identity`, entao nao tem um user_id para propagar ate aqui), NAO
        filtra e devolve a linha mais recente entre TODOS os usuarios - o
        mesmo comportamento de antes desta entrega. Isso e seguro enquanto
        so o usuario admin tiver Intervals.icu configurado (caso real hoje),
        mas fica incorreto assim que um segundo usuario sincronizar sua
        propria conta - propagar `Identity` por essas rotas fica para uma
        proxima entrega (ver CLAUDE.md / relatorio da Parte 2)."""
        # M52: o fuso do DONO da linha vem no mesmo SELECT (`user_timezone`),
        # para quem decide se o dado esta velho (`wellness_freshness`) saber o
        # "hoje" do usuario sem gastar um statement a mais.
        if user_id is not None:
            row = self.fetch_one(
                "SELECT w.*, u.timezone AS user_timezone FROM intervals_wellness w "
                "JOIN users u ON u.id = w.user_id "
                "WHERE w.user_id = %s ORDER BY w.wellness_date DESC LIMIT 1",
                (user_id,),
            )
        else:
            row = self.fetch_one(
                "SELECT w.*, u.timezone AS user_timezone FROM intervals_wellness w "
                "JOIN users u ON u.id = w.user_id "
                "ORDER BY w.wellness_date DESC LIMIT 1"
            )
        return row or None

    def _jsonable_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {key: self._jsonable(value) for key, value in row.items()}

    def _jsonable(self, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value

    @staticmethod
    def clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))
