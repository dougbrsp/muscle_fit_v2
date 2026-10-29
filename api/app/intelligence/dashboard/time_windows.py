"""Janelas de tempo do Novo Dashboard resolvidas no fuso do usuario (plano §9).

Substitui `CURDATE()`/`date.today()` nos servicos novos: a data "hoje" e a do
usuario (`users.timezone`, migracao 044), nao a do servidor (Lambda em UTC).
Um treino feito as 23h em Sao Paulo cai em 2026-09-21, nao em 22 - e
`activities.local_date` ja e gravado assim, entao a janela precisa ser
calculada na mesma base.

Funcoes PURAS: quem chama passa `today` (obtido via `user_today`) - este
modulo nunca consulta o relogio do sistema por conta propria, para os testes
serem deterministicos e para nenhuma query nascer com `CURDATE()`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...observability import log_event
from ...timezones import DEFAULT_TIMEZONE
from .contracts import WindowInfo, WindowKind

# Maior janela aceita para `custom` e `cycle` (plano §15: datas <= 365 dias).
MAX_WINDOW_DAYS = 365

_ROLLING_DAYS: dict[WindowKind, int] = {
    WindowKind.ROLLING_7D: 7,
    WindowKind.ROLLING_28D: 28,
    WindowKind.ROLLING_90D: 90,
    WindowKind.ROLLING_180D: 180,
}


def user_now(timezone_name: str, now_utc: datetime | None = None) -> datetime:
    """Instante "agora" na parede do usuario, NAIVE (sem tzinfo).

    Naive de proposito: as colunas DATETIME do banco (`activities.started_at`,
    `created_at`) sao naive e ja gravadas na hora local - comparar com um
    datetime aware nao casaria com elas. Quem precisa so da data usa
    `user_today`, que e este mesmo calculo com `.date()`."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        log_event("dashboard.timezone_invalid", timezone=timezone_name, fallback=DEFAULT_TIMEZONE)
        zone = ZoneInfo(DEFAULT_TIMEZONE)

    return now_utc.astimezone(zone).replace(tzinfo=None)


def user_today(timezone_name: str, now_utc: datetime | None = None) -> date:
    """Data civil "hoje" para o usuario no fuso `timezone_name`.

    `now_utc` (para testes/reprodutibilidade) e um instante em UTC; naive e
    interpretado como UTC. Fuso desconhecido -> `DEFAULT_TIMEZONE`, com
    evento `dashboard.timezone_invalid` para o valor ser corrigido no perfil
    (a resposta nunca falha por um nome de fuso ruim)."""
    return user_now(timezone_name, now_utc).date()


# ---------------------------------------------------------------------------
# Relogio do banco x dia do usuario (M76 Lote 2b, B10)
# ---------------------------------------------------------------------------
#
# Dois relogios convivem nas tabelas:
# - HORA DE PAREDE do usuario (naive): `activities.started_at`/`ended_at`
#   (vem do app), `local_date`, `plan_date`;
# - RELOGIO DO BANCO, em UTC: tudo que nasce de `NOW()`/`CURRENT_TIMESTAMP`
#   (`created_at`, `effective_from`/`superseded_at` das versoes de Modelo,
#   `assigned_at`, `template_generation_logs.created_at`). O Aurora e o MySQL
#   do docker compose/CI rodam com `time_zone = SYSTEM` = UTC.
#
# Toda conta de DIA/SEMANA/JANELA usa o dia do usuario: o "hoje" e calculado
# aqui (`user_today`) e entra no SQL como PARAMETRO (nunca o dia do banco), e um
# instante do relogio do banco e convertido para a parede do usuario antes de
# virar dia. O deslocamento entra no SQL como literal inteiro (`INTERVAL -180
# MINUTE`): funciona igual no pymysql e no shim da Data API (sem ':' no texto,
# sem depender das tabelas de fuso do MySQL, ausentes no compose/CI).
#
# Limite conhecido: o deslocamento e o do INSTANTE atual. Em fuso com horario
# de verao, linhas antigas do outro lado da troca erram 1h (America/Sao_Paulo
# nao tem horario de verao desde 2019).

DB_CLOCK_TIMEZONE = "UTC"


def utc_offset_minutes(timezone_name: str, now_utc: datetime | None = None) -> int:
    """Deslocamento do fuso do usuario em relacao ao relogio do banco (UTC),
    em minutos, no instante `now_utc` (default: agora). Sao Paulo = -180."""
    # Diferenca entre a parede do usuario e a de UTC no mesmo instante. Sem
    # `now_utc`, cada `user_now` le o relogio (o unico ponto que o le, e o que
    # `congelar_relogio` congela); os microssegundos entre as duas leituras
    # somem no arredondamento para minutos.
    local = user_now(timezone_name, now_utc)
    utc = user_now(DB_CLOCK_TIMEZONE, now_utc)
    return round((local - utc).total_seconds() / 60)


def db_to_user_sql(column: str, offset_minutes: int) -> str:
    """Expressao SQL que leva `column` (instante do relogio do banco, UTC) para
    a parede do usuario. `offset_minutes` vem de `utc_offset_minutes`."""
    return f"DATE_ADD({column}, INTERVAL {int(offset_minutes)} MINUTE)"


def trained_at_sql(alias: str = "a", *, offset_minutes: int) -> str:
    """UNICA expressao SQL de "quando o treino aconteceu" (hora de parede do
    usuario, naive): `started_at`, senao `ended_at`, senao `created_at`
    convertido do relogio do banco (UTC) para o fuso do usuario.

    Sem a conversao, uma sessao sem `started_at` lancada as 23h de Brasilia
    caia no dia seguinte (`created_at` = 02h UTC) e as horas desde o treino
    ficavam 3h maiores. `offset_minutes` = `utc_offset_minutes(fuso)`."""
    p = f"{alias}." if alias else ""
    return f"COALESCE({p}started_at, {p}ended_at, {db_to_user_sql(p + 'created_at', offset_minutes)})"


TRAINED_AT_TOKEN = "{trained_at}"


def with_trained_at(sql: str, timezone_name: str, alias: str = "a") -> str:
    """Troca o marcador `{trained_at}` de um SQL pronto (constante de modulo)
    por `trained_at_sql(alias)` no fuso `timezone_name`. `replace` e nao
    `format`: o SQL pode ter outras chaves literais."""
    return sql.replace(
        TRAINED_AT_TOKEN, trained_at_sql(alias, offset_minutes=utc_offset_minutes(timezone_name))
    )


def user_day_start_db(day: date, timezone_name: str) -> datetime:
    """Inicio (00:00) do dia `day` do usuario, no relogio do banco (UTC,
    naive) - para comparar com colunas que nascem de `NOW()`
    (ex.: `template_generation_logs.created_at >= %s`)."""
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    local = datetime(day.year, day.month, day.day, tzinfo=zone)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def user_day_end_db(day: date, timezone_name: str) -> datetime:
    """Ultimo segundo (23:59:59) do dia `day` do usuario, no relogio do banco.
    E o "fim do dia" das vigencias (versao de Modelo, atribuicao, metas):
    `effective_from`/`assigned_at` nascem de `NOW()` (UTC), entao uma edicao
    as 22h de Brasilia (01h UTC do dia seguinte) pertence ao dia do usuario."""
    return user_day_start_db(day + timedelta(days=1), timezone_name) - timedelta(seconds=1)


def user_day_end_db_sql(date_column: str, offset_minutes: int) -> str:
    """Expressao SQL do fim EXCLUSIVO do dia `date_column` (DATE do usuario)
    no relogio do banco: `date_column + 1 dia - deslocamento`. Mesmo instante
    de `user_day_end_db` + 1 s."""
    return f"DATE_ADD({date_column}, INTERVAL {24 * 60 - int(offset_minutes)} MINUTE)"


def db_instant_to_user_date(value: datetime, timezone_name: str) -> date:
    """Dia do usuario em que caiu um instante do relogio do banco (UTC)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return user_now(timezone_name, aware).date()


def user_wall_to_instant(value: datetime, timezone_name: str) -> datetime:
    """Hora de PAREDE do usuario (naive, a mesma base de `user_now` e de
    `activities.started_at`) -> instante com o fuso do usuario anexado.

    Para a API devolver um horario que o navegador nao reinterpreta: um
    `2026-10-18T14:00:00` sem fuso vira 11h em quem le como UTC; com o
    deslocamento (`-03:00`) e o mesmo instante em qualquer lugar, e a tela so
    formata no fuso do usuario (`WindowInfo.timezone`). Ja aware -> devolve
    como esta. Fuso invalido -> `DEFAULT_TIMEZONE`, como em `user_now`."""
    if value.tzinfo is not None:
        return value
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return value.replace(tzinfo=zone)


def resolve_window(
    kind: WindowKind | str,
    *,
    today: date,
    date_from: date | None = None,
    date_to: date | None = None,
    cycle_length_days: int | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> WindowInfo:
    """Resolve `kind` em `[date_from, date_to]` inclusivo, em datas locais.

    - `7d`/`28d`/`90d`/`180d`: rolling terminando HOJE (inclusive). `7d` em
      2026-09-22 = 16..22/09 (7 dias).
    - `cycle`: rolling de `cycle_length_days` dias terminando hoje (o ciclo
      corrente do template; quem chama descobre o tamanho pelo template).
    - `custom`: exige `date_from` e `date_to`, `date_from <= date_to`,
      no maximo `MAX_WINDOW_DAYS` dias.

    `ValueError` com mensagem clara em toda combinacao invalida - o endpoint
    converte em 400 (Pydantic/HTTPException), nunca em 500.
    """
    try:
        window_kind = WindowKind(kind)
    except ValueError as exc:
        valid = ", ".join(k.value for k in WindowKind)
        raise ValueError(f"window invalida: {kind!r}; use uma de {valid}") from exc

    if window_kind in _ROLLING_DAYS:
        days = _ROLLING_DAYS[window_kind]
        start = today - timedelta(days=days - 1)
        return WindowInfo(kind=window_kind, date_from=start, date_to=today, timezone=timezone_name, days=days)

    if window_kind is WindowKind.CYCLE:
        if cycle_length_days is None:
            raise ValueError("window=cycle exige cycle_length_days (tamanho do ciclo do template)")
        if not isinstance(cycle_length_days, int) or isinstance(cycle_length_days, bool) or cycle_length_days < 1:
            raise ValueError(f"cycle_length_days deve ser inteiro >= 1, recebido {cycle_length_days!r}")
        if cycle_length_days > MAX_WINDOW_DAYS:
            raise ValueError(f"cycle_length_days maximo e {MAX_WINDOW_DAYS}, recebido {cycle_length_days}")
        start = today - timedelta(days=cycle_length_days - 1)
        return WindowInfo(
            kind=window_kind, date_from=start, date_to=today, timezone=timezone_name, days=cycle_length_days
        )

    # CUSTOM
    if date_from is None or date_to is None:
        raise ValueError("window=custom exige date_from e date_to")
    if date_from > date_to:
        raise ValueError(f"date_from ({date_from.isoformat()}) nao pode ser depois de date_to ({date_to.isoformat()})")
    days = (date_to - date_from).days + 1
    if days > MAX_WINDOW_DAYS:
        raise ValueError(f"janela custom maxima e {MAX_WINDOW_DAYS} dias, recebido {days}")
    return WindowInfo(kind=window_kind, date_from=date_from, date_to=date_to, timezone=timezone_name, days=days)


# Nome do memo por requisicao (`request_memo`) do fuso do usuario; compartilhado com
# `intelligence.base.IntelligenceService._user_timezone`.
USER_TIMEZONE_MEMO = "user_timezone"


def user_timezone(conn: object, user_id: object) -> str:
    """Fuso do usuario (`users.timezone`, migracao 044), normalizado.

    Vivia como `_user_timezone` dentro de `analytics_dashboard.py`. Subiu para
    ca na F3.2 porque deixou de ser assunto de UM router: `insights_core`
    monta o `intelligence_package` do lado do servidor e precisa resolver a
    MESMA janela, no MESMO fuso, sem importar o modulo de rotas.

    Nunca falha: coluna ausente (banco anterior a 044), conexao que reclama ou
    valor corrompido caem no default de `normalize_timezone`. Um fuso ruim no
    perfil nao pode derrubar um dashboard nem uma geracao de insight."""
    from ... import request_memo
    from ...timezones import DEFAULT_TIMEZONE, normalize_timezone

    def read() -> str:
        with conn.cursor() as cursor:  # type: ignore[attr-defined]
            cursor.execute("SELECT timezone FROM users WHERE id = %s", (int(user_id),))  # type: ignore[arg-type]
            row = cursor.fetchone()
        return normalize_timezone((row or {}).get("timezone"))

    try:
        # Uma leitura por requisicao (PERF-01): o fuso e o mesmo para todos os
        # servicos da chamada. Falha nao e memoizada (cai no default, como antes).
        return request_memo.memoize((USER_TIMEZONE_MEMO, int(user_id)), read)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - banco anterior a 044 nao pode derrubar o dashboard
        return DEFAULT_TIMEZONE


def user_today_for(conn: object, user_id: object) -> date:
    """"Hoje" do usuario `user_id` lendo o fuso do perfil por `conn`:
    `user_today(user_timezone(conn, user_id))`. Nunca falha por fuso (as duas
    funcoes caem no default); a conexao e do chamador (COD-06: era repetido
    como `_user_today` em `intervals_integration` e `generation/mode_a`)."""
    return user_today(user_timezone(conn, user_id))


def user_day_start_db_for(conn: object, user_id: object, now_utc: datetime | None = None) -> datetime:
    """Inicio (00:00) do "hoje" do usuario `user_id` (fuso do perfil, por
    `conn`) no relogio do banco: `user_day_start_db(user_today(fuso), fuso)`.
    Para contar o que o usuario fez HOJE em colunas que nascem de `NOW()`
    (limite diario do Open Food Facts, cota de IA da nutricao). Nunca falha por
    fuso. `now_utc` so para teste."""
    fuso = user_timezone(conn, user_id)
    return user_day_start_db(user_today(fuso, now_utc), fuso)


__all__ = [
    "DB_CLOCK_TIMEZONE",
    "MAX_WINDOW_DAYS",
    "TRAINED_AT_TOKEN",
    "WindowKind",
    "db_instant_to_user_date",
    "db_to_user_sql",
    "resolve_window",
    "trained_at_sql",
    "user_day_end_db",
    "user_day_end_db_sql",
    "user_day_start_db",
    "user_day_start_db_for",
    "user_now",
    "user_timezone",
    "user_today",
    "user_today_for",
    "user_wall_to_instant",
    "utc_offset_minutes",
    "with_trained_at",
]
