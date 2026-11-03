"""Check-in semanal da nutrição (reformulação N7, D7 a D10). Vive na api-core (`{proxy+}`).

Rotas (prefixo `/nutrition`). O usuário vê e responde o PRÓPRIO check-in; admin e PT, o de qualquer aluno (como no
plano); usuário comum nunca alcança o de outro (403 por `user_id`, 404 por id).
- `GET  /checkin?user_id=`                  o check-in da semana FECHADA mais recente que já venceu (gerado aqui,
                                            ao abrir o app, se ainda não existe: D8) ou o motivo de não haver; é a
                                            leitura leve da home: com a linha gravada são 2 SELECTs
- `GET  /checkins?user_id=&limit=`          histórico
- `POST /checkins/{id}/accept`              publica uma NOVA versão do plano com as metas propostas (o mesmo
                                            caminho de `POST /plans/{id}/publish`); repetir é idempotente; proposta
                                            expirada ou plano trocado depois dela = 409
- `POST /checkins/{id}/decline`             mantém as metas atuais
- `GET/PUT /checkin/settings`               o dia da semana do check-in (padrão segunda)

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §83): o veredito, o ajuste, as metas propostas, a janela, o
calendário, a validade e a visibilidade são de `domain/nutrition/weekly_checkin`; esta camada lê as linhas gravadas
(metas materializadas, totais das refeições, composição corporal), chama o motor e grava o que ele devolveu.
Não há regra de nutrição neste arquivo. Aceitar NÃO recalcula nada à mão: aplica ao plano vigente as mudanças que o
motor gravou na proposta e publica por `nutrition_plans.publish_draft`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import ENGINE_VERSION
from .domain.nutrition import dashboard as dash
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition import weekly_checkin as wc
from .domain.nutrition.plan import plan_in_force
from .domain.nutrition.subject import resolve_subject
from .domain.nutrition.weekly_budget import intake_macros, target_macros
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_access import fetch_target_user, is_staff, resolve_target_user
from .nutrition_dashboard import MEAL_TOTALS_SQL, MUSCLE_WINDOW_SQL
from .nutrition_meals import day_totals
from .nutrition_plans import _CURRENT_FOR_UPDATE_SQL, _fetch_plan, insert_draft, publish_draft
from .nutrition_schema import (
    CHECKIN_SETTINGS_TABLE,
    CHECKINS_TABLE,
    has_checkin_tables,
    has_diary_tables,
    has_nutrition_tables,
    require_checkin_tables,
    require_diary_tables,
    require_nutrition_tables,
)
from .nutrition_targets_service import (
    load_published_plans,
    load_user_inputs,
    read_targets,
    settings_from_row,
)
from .request_models import NutritionCheckinSettingsIn
from .security import Identity, get_current_identity
from .template_assignments import db_now

router = APIRouter(prefix="/nutrition", tags=["nutrition"])

PUBLISHED_PLAN = "published"
PROPOSED = wc.CheckinStatus.PROPOSED.value
ACCEPTED = wc.CheckinStatus.ACCEPTED.value
DECLINED = wc.CheckinStatus.DECLINED.value
EXPIRED = wc.CheckinStatus.EXPIRED.value
INFORMATIVE = wc.CheckinStatus.INFORMATIVE.value

_COLUMNS = (
    "id, user_id, week_start, due_date, status, verdict, inputs_json, result_json, adjustment_kcal, "
    "proposed_changes_json, proposed_targets_json, expires_at, engine_version, source_plan_id, generated_plan_id, "
    "responded_at, created_at"
)
# O mais recente do usuário (D8: o dia do check-in pode mudar, mas nunca se volta a uma semana mais antiga).
_LATEST_SQL = f"SELECT {_COLUMNS} FROM {CHECKINS_TABLE} WHERE user_id = %s ORDER BY week_start DESC LIMIT 1"
# Releitura DEPOIS do INSERT: sob REPEATABLE READ um SELECT comum enxerga o snapshot antigo e não veria a linha que outra
# abertura concorrente acabou de gravar (o INSERT vira no-op no UNIQUE); `FOR SHARE` é leitura corrente.
_BY_WEEK_SHARE_SQL = f"SELECT {_COLUMNS} FROM {CHECKINS_TABLE} WHERE user_id = %s AND week_start = %s FOR SHARE"
# O plano vigente (sem trava): a proposta só vale para o plano que a gerou.
_CURRENT_PLAN_SQL = "SELECT id FROM nutrition_plans WHERE user_id = %s AND status = %s AND superseded_at IS NULL"
_BY_ID_SQL = f"SELECT {_COLUMNS} FROM {CHECKINS_TABLE} WHERE id = %s"
_BY_ID_FOR_UPDATE_SQL = _BY_ID_SQL + " FOR UPDATE"
_HISTORY_SQL = f"SELECT {_COLUMNS} FROM {CHECKINS_TABLE} WHERE user_id = %s ORDER BY week_start DESC, id DESC LIMIT %s"
# Idempotente sob aberturas concorrentes: o UNIQUE (user_id, week_start) deixa passar só a primeira inserção; a
# segunda vira no-op e a rota lê a linha da primeira.
_INSERT_ONCE_SQL = (
    f"INSERT INTO {CHECKINS_TABLE} (user_id, week_start, due_date, status, verdict, inputs_json, result_json, "
    "adjustment_kcal, proposed_changes_json, proposed_targets_json, expires_at, engine_version, source_plan_id) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE id = id"
)
_EXPIRE_SQL = f"UPDATE {CHECKINS_TABLE} SET status = %s, responded_at = %s WHERE id = %s AND status = %s"
_ACCEPT_SQL = (
    f"UPDATE {CHECKINS_TABLE} SET status = %s, generated_plan_id = %s, responded_at = %s WHERE id = %s AND status = %s"
)
_DECLINE_SQL = f"UPDATE {CHECKINS_TABLE} SET status = %s, responded_at = %s WHERE id = %s AND status = %s"
_SETTINGS_SQL = f"SELECT weekday FROM {CHECKIN_SETTINGS_TABLE} WHERE user_id = %s"
_UPSERT_SETTINGS_SQL = (
    f"INSERT INTO {CHECKIN_SETTINGS_TABLE} (user_id, weekday) VALUES (%s, %s) ON DUPLICATE KEY UPDATE weekday = VALUES(weekday)"
)


def _connect():
    return connect_or_503(get_conn)


def _today_for(conn: Any, user_id: int) -> date:
    """Dia civil do ALUNO (fuso do perfil). Isolado para os testes congelarem."""
    return user_today_for(conn, user_id)


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value)


def _num(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _require_tables(cur: Any) -> None:
    require_nutrition_tables(cur)
    require_diary_tables(cur)
    require_checkin_tables(cur)


# ---------------------------------------------------------------------------
# Resposta pública
# ---------------------------------------------------------------------------


def checkin_public(row: dict[str, Any], today: date, *, detail: bool = True) -> dict[str, Any]:
    """Uma linha de `nutrition_checkins` para a API: tudo que a tela mostra já vem pronto do motor."""
    entradas = _json(row.get("inputs_json")) or {}
    resultado = _json(row.get("result_json")) or {}
    proposta = _json(row.get("proposed_targets_json"))
    semana = _as_date(row.get("week_start"))
    gerado_em = _as_date(entradas.get("generated_on")) or _as_date(row.get("due_date"))
    status = wc.CheckinStatus(str(row["status"]))
    expirada = status == wc.CheckinStatus.PROPOSED and gerado_em is not None and wc.is_proposal_expired(gerado_em, today)
    if expirada:  # a leitura já mostra como expirada; a linha é regravada na próxima chamada que escreve
        status = wc.CheckinStatus.EXPIRED
    publico: dict[str, Any] = {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "week_start": semana.isoformat() if semana else None,
        "week_end": (semana + timedelta(days=6)).isoformat() if semana else None,
        "due_date": _iso(_as_date(row.get("due_date"))),
        "status": status.value,
        "status_label": nutrition_labels.CHECKIN_STATUS_LABELS[status],
        "verdict": row.get("verdict"),
        "verdict_label": nutrition_labels.CHECKIN_VERDICT_LABELS[wc.CheckinVerdict(str(row["verdict"]))],
        "can_respond": status == wc.CheckinStatus.PROPOSED,
        "show_card": gerado_em is not None and wc.is_card_visible(status, gerado_em, today),
        "expires_at": _iso(row.get("expires_at")),
        "expires_on": _iso(wc.proposal_expires_on(gerado_em)) if gerado_em else None,
        "adjustment_kcal": _num(row.get("adjustment_kcal")),
        "missing_items": nutrition_labels.limitation_items(resultado.get("missing")),
        "reason_items": nutrition_labels.limitation_items(resultado.get("reasons")),
        "proposal": proposta,
        "engine_version": row.get("engine_version"),
        "source_plan_id": int(row["source_plan_id"]) if row.get("source_plan_id") is not None else None,
        "generated_plan_id": int(row["generated_plan_id"]) if row.get("generated_plan_id") is not None else None,
        "responded_at": _iso(row.get("responded_at")),
        "created_at": _iso(row.get("created_at")),
    }
    if detail:
        publico["summary"] = entradas.get("summary")
        publico["days"] = entradas.get("days")
        publico["strategy"] = entradas.get("strategy")
        publico["goal"] = entradas.get("goal")
    else:
        publico["summary"] = {
            chave: (entradas.get("summary") or {}).get(chave)
            for chave in ("mean_intake_kcal", "mean_target_kcal", "mean_tdee_kcal", "weight_weekly_change_kg", "complete_days")
        }
    return publico


def _weekday_payload(weekday: Optional[int]) -> dict[str, Any]:
    efetivo = wc.DEFAULT_CHECKIN_WEEKDAY if weekday is None else int(weekday)
    return {
        "weekday": efetivo,
        "weekday_label": nutrition_labels.WEEKDAY_LABELS[efetivo],
        "is_default": weekday is None,
    }


def _weekday_options() -> list[dict[str, Any]]:
    return [{"value": dia, "label": nome} for dia, nome in sorted(nutrition_labels.WEEKDAY_LABELS.items())]


def _read_weekday(cur: Any, aluno: int) -> Optional[int]:
    cur.execute(_SETTINGS_SQL, (int(aluno),))
    linha = cur.fetchone()
    return int(linha["weekday"]) if linha and linha.get("weekday") is not None else None


# ---------------------------------------------------------------------------
# Geração (ao abrir o app) e expiração
# ---------------------------------------------------------------------------


def _build_inputs(cur: Any, aluno: int, plan_id: int, settings: Any, week_start: date, hoje: date) -> wc.CheckinInputs:
    """Alinha por dia as leituras gravadas da janela (metas, totais das refeições, corpo) para o motor."""
    dias = wc.window_dates(week_start)
    entradas = load_user_inputs(cur, aluno, dias[0], hoje, hoje)
    fim = dias[-1]
    metas = {
        _as_date(linha["local_date"]): linha for linha in read_targets(cur, aluno, dias[0], fim)
    }
    cur.execute(MEAL_TOTALS_SQL, (aluno, dias[0], fim))
    refeicoes = list(cur.fetchall() or [])
    cur.execute(MUSCLE_WINDOW_SQL, (aluno, dias[0], fim))
    musculos = list(cur.fetchall() or [])
    series = entradas.series
    totais = [day_totals(grupo) for grupo in dash.group_by_day(dias, ((_as_date(r["local_date"]), r) for r in refeicoes))]
    pesos = dash.last_by_day(dias, ((r.local_date, r.number) for r in series.weight))
    gorduras = dash.positive_series(dash.last_by_day(dias, ((r.local_date, r.value) for r in series.bioimpedance_body_fat)))
    massas = dash.positive_series(
        dash.last_by_day(dias, ((_as_date(r["local_date"]), _num(r["muscle_mass_kg"])) for r in musculos))
    )
    lista: list[wc.CheckinDay] = []
    for i, dia in enumerate(dias):
        linha = metas.get(dia)
        alvo = (
            None
            if linha is None
            else target_macros(_num(linha.get("kcal_target")), _num(linha.get("protein_g")), _num(linha.get("carbs_g")),
                               _num(linha.get("fat_g")), _num(linha.get("fiber_g")))
        )
        lista.append(
            wc.CheckinDay(
                local_date=dia,
                target=alvo,
                intake=intake_macros(totais[i]),
                tdee_kcal=None if linha is None else _num(linha.get("tdee_kcal")),
                weight_kg=pesos[i],
                body_fat_percent=gorduras[i],
                muscle_mass_kg=massas[i],
                plan_id=None if linha is None or linha.get("plan_id") is None else int(linha["plan_id"]),
            )
        )
    sujeito = resolve_subject(series.readings_on(hoje), hoje, settings.activity_level)
    return wc.CheckinInputs(
        settings=settings,
        subject=sujeito.subject,
        days=tuple(lista),
        reference_day=hoje,
        scale_bmr_kcal=sujeito.scale_bmr_kcal,
        ffm_age_days=sujeito.ffm_age_days,
        plan_id=plan_id,
    )


def _frozen_days(inputs: wc.CheckinInputs) -> list[dict[str, Any]]:
    """Os 7 dias da semana como entrada congelada (o que a tela mostra no detalhe)."""
    return [
        {
            "local_date": dia.local_date.isoformat(),
            "target_kcal": None if dia.target is None else dia.target.kcal,
            "intake_kcal": None if dia.intake is None else dia.intake.kcal,
            "tdee_kcal": dia.tdee_kcal,
            "weight_kg": dia.weight_kg,
            "body_fat_percent": dia.body_fat_percent,
            "muscle_mass_kg": dia.muscle_mass_kg,
        }
        for dia in inputs.week_days
    ]


def _change_items(settings: Any, changes: dict[str, Any]) -> list[dict[str, Any]]:
    """O que muda no plano, campo a campo, com o rótulo do motor (a tela só mostra)."""
    itens: list[dict[str, Any]] = []
    for campo, novo in changes.items():
        if campo == wc.CHANGE_MANUAL_TARGETS and settings.manual_macros is not None:
            antigo = settings.manual_macros
            for grama in ("protein_g", "carb_g", "fat_g"):
                itens.append({"field": grama, "label": nutrition_labels.field_label(grama), "unit": "g",
                              "before": getattr(antigo, grama), "after": novo[grama]})
        elif campo == wc.CHANGE_MAINTENANCE_KCAL_ADJUST:
            itens.append({"field": campo, "label": nutrition_labels.field_label(campo), "unit": "kcal",
                          "before": settings.maintenance_kcal_adjust, "after": novo})
        else:
            itens.append({"field": campo, "label": nutrition_labels.field_label(campo), "unit": "share",
                          "before": getattr(settings, campo, None), "after": novo})
    return itens


def _insert_checkin(
    cur: Any, aluno: int, week_start: date, due: date, hoje: date, plan_id: int, settings: Any, inputs: wc.CheckinInputs
) -> None:
    resultado = wc.evaluate_week(inputs)
    proposta = resultado.proposal
    expira = wc.proposal_expires_at(hoje) if proposta is not None else None
    congelado = {
        "generated_on": hoje.isoformat(),
        "week_start": week_start.isoformat(),
        "strategy": settings.strategy.value,
        "plan_settings": settings.to_dict(),
        "summary": resultado.summary.to_dict(),
        "days": _frozen_days(inputs),
        "goal": {"goal_weight_kg": settings.goal_weight_kg, "goal_body_fat_percent": settings.goal_body_fat_percent},
    }
    guardado = {"verdict": resultado.verdict.value, "missing": list(resultado.missing), "reasons": list(resultado.reasons)}
    alvo = None
    if proposta is not None:
        alvo = {
            **proposta.to_dict(),
            "scope_label": nutrition_labels.CHECKIN_SCOPE_LABELS[proposta.scope],
            "change_items": _change_items(settings, proposta.changes),
        }
    cur.execute(
        _INSERT_ONCE_SQL,
        (
            aluno, week_start, due, resultado.status.value, resultado.verdict.value,
            json.dumps(congelado, default=str), json.dumps(guardado),
            None if proposta is None else proposta.delta_kcal,
            None if proposta is None else json.dumps(proposta.changes),
            None if alvo is None else json.dumps(alvo, default=str),
            expira, ENGINE_VERSION, plan_id,
        ),
    )


def _expire(cur: Any, row: dict[str, Any]) -> dict[str, Any]:
    cur.execute(_EXPIRE_SQL, (EXPIRED, db_now(cur), int(row["id"]), PROPOSED))
    return {**row, "status": EXPIRED}


def _expire_if_due(cur: Any, row: dict[str, Any], hoje: date) -> dict[str, Any]:
    """Proposta sem resposta depois da validade vira `expired` (metas atuais seguem, D7)."""
    entradas = _json(row.get("inputs_json")) or {}
    gerado_em = _as_date(entradas.get("generated_on")) or _as_date(row.get("due_date"))
    if row.get("status") == PROPOSED and gerado_em is not None and wc.is_proposal_expired(gerado_em, hoje):
        return _expire(cur, row)
    return row


def _settle(cur: Any, row: dict[str, Any], aluno: int, hoje: date) -> dict[str, Any]:
    """A linha pronta para mostrar: a proposta fora da validade ou de um plano que não é mais o vigente (publicado ou
    aceito depois dela) expira, em vez de oferecer um "Aceitar" que só daria 409."""
    row = _expire_if_due(cur, row, hoje)
    if row.get("status") != PROPOSED:
        return row
    cur.execute(_CURRENT_PLAN_SQL, (int(aluno), PUBLISHED_PLAN))
    vigente = cur.fetchone()
    if vigente is None or int(vigente["id"]) != int(row["source_plan_id"]):
        return _expire(cur, row)
    return row


def tables_ready(cur: Any) -> bool:
    """Plano, diário e check-in (078/079, 082/083, 103) existem neste banco? Sem DDL."""
    return has_nutrition_tables(cur) and has_diary_tables(cur) and has_checkin_tables(cur)


def ensure_checkin(conn: Any, aluno: int, hoje: date) -> dict[str, Any]:
    """O check-in da semana fechada que já venceu: lê a linha ou, se não existe, avalia e grava UMA vez. Devolve
    `{weekday, due_date, week_start, row, none_reason}`. Se já existe um check-in de semana IGUAL OU MAIS NOVA que a do
    calendário (o usuário trocou o dia), devolve esse, nunca volta a uma semana antiga. Sem plano publicado, ou sem
    plano que já valesse na semana avaliada, `row` é None e `none_reason` diz por quê."""
    with conn.cursor() as cur:
        dia_escolhido = _read_weekday(cur, aluno)
        vencimento = wc.due_day(hoje, wc.DEFAULT_CHECKIN_WEEKDAY if dia_escolhido is None else dia_escolhido)
        semana = wc.closed_week_start(vencimento)
        base = {"weekday": dia_escolhido, "due_date": vencimento, "week_start": semana, "row": None, "none_reason": None}
        cur.execute(_LATEST_SQL, (aluno,))
        ultimo = cur.fetchone()
        if ultimo and wc.should_reuse_latest(_as_date(ultimo["week_start"]), semana):
            return {**base, "week_start": _as_date(ultimo["week_start"]), "due_date": _as_date(ultimo["due_date"]) or vencimento,
                    "row": _settle(cur, ultimo, aluno, hoje)}
        planos = load_published_plans(cur, aluno)
        ref = plan_in_force(planos.refs, hoje)
        if ref is None:
            return {**base, "none_reason": wc.NONE_NO_PLAN}
        fim_da_semana = semana + timedelta(days=6)
        if not any(r.effective_from <= fim_da_semana for r in planos.refs):  # nenhum plano valia nessa semana
            return {**base, "none_reason": wc.NONE_PLAN_AFTER_WEEK}
        configuracao = planos.settings[ref.plan_id]
        entradas = _build_inputs(cur, aluno, ref.plan_id, configuracao, semana, hoje)
        _insert_checkin(cur, aluno, semana, vencimento, hoje, ref.plan_id, configuracao, entradas)
        cur.execute(_BY_WEEK_SHARE_SQL, (aluno, semana))
        return {**base, "row": cur.fetchone()}


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.get("/checkin")
def get_checkin(
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Sem as tabelas (janela do deploy: o código novo atende antes da migração 103) a leitura responde 200 com
    `checkin: null` e o motivo `migration_pending`: a home nunca gera 5xx por causa do check-in. Escrever segue 503."""
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            pronto = tables_ready(cur)
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        if pronto:
            achado = ensure_checkin(conn, aluno, hoje)
        else:
            vencimento = wc.due_day(hoje)
            achado = {
                "weekday": None, "due_date": vencimento, "week_start": wc.closed_week_start(vencimento), "row": None,
                "none_reason": wc.NONE_MIGRATION_PENDING,
            }
    linha = achado["row"]
    return {
        "user": usuario,
        "today": hoje.isoformat(),
        "settings": _weekday_payload(achado["weekday"]),
        "weekday_options": _weekday_options(),
        "due_date": achado["due_date"].isoformat(),
        "week_start": achado["week_start"].isoformat(),
        "week_end": (achado["week_start"] + timedelta(days=6)).isoformat(),
        "checkin": None if linha is None else checkin_public(linha, hoje),
        "none_items": nutrition_labels.limitation_items([achado["none_reason"]] if achado["none_reason"] else []),
    }


@router.get("/checkins")
def list_checkins(
    user_id: Optional[int] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    aluno = resolve_target_user(identity, user_id)
    quantidade = wc.HISTORY_LIMIT_DEFAULT if limit is None else max(1, min(int(limit), wc.HISTORY_LIMIT_MAX))
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            usuario = fetch_target_user(cur, aluno)
            cur.execute(_HISTORY_SQL, (aluno, quantidade))
            linhas = list(cur.fetchall() or [])
        hoje = _today_for(conn, aluno)
    return {"user": usuario, "today": hoje.isoformat(), "items": [checkin_public(linha, hoje, detail=False) for linha in linhas]}


@router.get("/checkin/settings")
def get_checkin_settings(
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            fetch_target_user(cur, aluno)
            dia = _read_weekday(cur, aluno)
    return {"user_id": aluno, **_weekday_payload(dia), "weekday_options": _weekday_options()}


@router.put("/checkin/settings")
def put_checkin_settings(payload: NutritionCheckinSettingsIn, identity: Identity = Depends(get_current_identity)):
    if not wc.checkin_weekday_is_valid(payload.weekday):
        raise _error(400, "invalid_checkin_weekday", "Dia da semana inválido para o check-in.")
    aluno = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            fetch_target_user(cur, aluno)
            cur.execute(_UPSERT_SETTINGS_SQL, (aluno, int(payload.weekday)))
    return {"user_id": aluno, **_weekday_payload(int(payload.weekday)), "weekday_options": _weekday_options()}


def _owned_row(cur: Any, identity: Identity, checkin_id: int, *, lock: bool = False) -> dict[str, Any]:
    """A linha do check-in, só para o dono, admin ou PT; qualquer outro recebe 404 (igual ao que não existe)."""
    cur.execute(_BY_ID_FOR_UPDATE_SQL if lock else _BY_ID_SQL, (int(checkin_id),))
    linha = cur.fetchone()
    if not linha or not (int(linha["user_id"]) == int(identity.user_id) or is_staff(identity)):
        raise _error(404, "checkin_not_found", "Check-in não encontrado.")
    return linha


@router.post("/checkins/{checkin_id}/accept")
def accept_checkin(checkin_id: int, identity: Identity = Depends(get_current_identity)):
    """Aceita a proposta: publica uma nova versão do plano com as mudanças que o motor gravou. A linha é travada
    (`FOR UPDATE`), então duas chamadas simultâneas se enfileiram e a segunda só lê o aceite da primeira. O plano
    vigente também é travado com `FOR UPDATE` ANTES de qualquer leitura `FOR SHARE` (`load_published_plans`): ler com
    trava compartilhada e depois pedir a exclusiva da publicação é o deadlock clássico (1213)."""
    expirou = False
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            linha = _owned_row(cur, identity, checkin_id, lock=True)
            aluno = int(linha["user_id"])
            hoje = _today_for(conn, aluno)
            if linha["status"] == ACCEPTED:  # idempotente: o aceite já foi feito
                return {"checkin": checkin_public(linha, hoje), "already_accepted": True, "plan": None}
            if linha["status"] in (DECLINED, INFORMATIVE):
                raise _error(409, "checkin_not_acceptable", "Este check-in não tem proposta a aceitar.", current_status=linha["status"])
            if linha["status"] == EXPIRED:
                expirou = True  # já gravado como expirado: nada a escrever
            else:
                expirou = _expire_if_due(cur, linha, hoje)["status"] == EXPIRED
            if not expirou:
                cur.execute(_CURRENT_FOR_UPDATE_SQL, (aluno, PUBLISHED_PLAN))
        if not expirou:
            with conn.cursor() as cur:
                planos = load_published_plans(cur, aluno)
                vigente = plan_in_force(planos.refs, hoje)
                if vigente is None or vigente.plan_id != int(linha["source_plan_id"]):
                    raise _error(
                        409, "checkin_plan_changed",
                        "O plano mudou depois desta proposta: ela não vale mais. As metas atuais seguem.",
                        current_plan_id=None if vigente is None else vigente.plan_id,
                    )
                origem = _fetch_plan(cur, int(linha["source_plan_id"]))
                mudancas = _json(linha.get("proposed_changes_json")) or {}
                aplicado = wc.apply_changes(settings_from_row(origem), mudancas)
                if not aplicado.ok or aplicado.settings is None:
                    raise _error(409, "checkin_proposal_invalid", "A proposta não passa mais nas regras do plano.",
                                 errors=nutrition_labels.limitation_items(aplicado.errors))
                semana = _as_date(linha["week_start"])
                rascunho = insert_draft(cur, aluno, identity.user_id, aplicado.settings, f"Check-in semanal da semana de {semana.isoformat() if semana else ''}")
            publicado, janela, metas = publish_draft(conn, rascunho, aluno, identity.user_id, vigente.plan_id)
            with conn.cursor() as cur:
                cur.execute(_ACCEPT_SQL, (ACCEPTED, int(publicado["id"]), db_now(cur), int(checkin_id), PROPOSED))
                if int(cur.rowcount or 0) == 0:  # a linha está travada: só se for apagada no meio
                    raise _error(409, "checkin_not_acceptable", "O check-in mudou durante o aceite; recarregue.")
                cur.execute(_BY_ID_SQL, (int(checkin_id),))
                aceita = cur.fetchone()
            return {
                "checkin": checkin_public(aceita, hoje),
                "already_accepted": False,
                "plan": {"id": int(publicado["id"]), "effective_from": _iso(publicado.get("effective_from"))},
                "window": janela.to_dict(),
                "targets_count": len(metas),
            }
    raise _error(409, "checkin_expired", "A proposta expirou: as metas atuais seguem.")


@router.post("/checkins/{checkin_id}/decline")
def decline_checkin(checkin_id: int, identity: Identity = Depends(get_current_identity)):
    """Recusar mantém as metas atuais. Repetir é idempotente; aceito ou expirado não se recusa."""
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            linha = _owned_row(cur, identity, checkin_id, lock=True)
            aluno = int(linha["user_id"])
            hoje = _today_for(conn, aluno)
            if linha["status"] == DECLINED:
                return {"checkin": checkin_public(linha, hoje), "already_declined": True}
            linha = _expire_if_due(cur, linha, hoje)
            if linha["status"] != PROPOSED:
                raise _error(409, "checkin_not_declinable", "Este check-in não tem proposta a recusar.", current_status=linha["status"])
            cur.execute(_DECLINE_SQL, (DECLINED, db_now(cur), int(checkin_id), PROPOSED))
            cur.execute(_BY_ID_SQL, (int(checkin_id),))
            recusada = cur.fetchone()
    return {"checkin": checkin_public(recusada, hoje), "already_declined": False}


__all__ = ["checkin_public", "ensure_checkin", "router"]
