"""M98 C3b - "Aplicar no Modelo" (rascunho), tela estruturada e Modo Treino.

Sem banco. Prova:

- dono `cardio_prescription`: etapas de exibição, mesma prescrição
  (idempotência) e tempo total em minutos;
- `template_drafts.apply_cardio_sessions` (puro): `replace`/`add` por
  `slot_sequence`, idempotente, recusas com mensagem (sem `plan_link`, outro
  Modelo, slot inexistente, prescrição inválida pelo dono, slot repetido) e
  conflito com o rascunho (o rascunho do usuário nunca é sobrescrito);
- `workout_plan_templates.apply_cardio_sessions_to_draft`: grava SÓ no
  rascunho (nenhuma escrita na versão publicada), 409 quando o Modelo não é o
  ativo ou está inativo, e não grava quando nada mudou;
- a rota `POST /cardio-recommendations/{id}/apply`: papel de quem edita o
  Modelo (admin/PT), recomendação antiga ou sem Modelo = 409, data fora da
  recomendação recusada;
- `GET /cardio-recommendations/{id}` com `recommendation_view` (rótulos
  prontos) e `null` nas antigas;
- `cardio_display` (Modo Treino): etapas com a faixa de bpm das zonas do
  atleta; ficha sem slot/cardio = lista vazia.

O fluxo contra MySQL real está em
`tests/integration/dataapi/test_template_drafts_067.py`
(`test_aplicar_cardio_no_rascunho`).
"""

from __future__ import annotations

import contextlib
import copy
import json
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import cardio_display, cardio_recommendation_v3, template_drafts as td
from app import cardio_recommendations as cr
from app import workout_plan_templates as wpt
from app.domain import cardio_envelope, cardio_prescription as cp
from app.security import Identity, get_current_identity

TEMPLATE_ID = 36
VERSION_ID = 31

NORMAL_30_Z2 = {"cardio_mode": "continuous", "cardio_warmup_minutes": 5, "cardio_duration_minutes": 30,
                "cardio_target_zone": 2, "cardio_cooldown_minutes": 5}
NORMAL_20_Z2 = {**NORMAL_30_Z2, "cardio_duration_minutes": 20}
HIIT = {"cardio_mode": "hiit", "cardio_warmup_minutes": 5, "hiit_low_seconds": 90, "hiit_high_seconds": 30,
        "hiit_low_zone": 2, "hiit_high_zone": 4, "hiit_rounds": 8, "cardio_cooldown_minutes": 5}


def _cardio_item(order, prescription, item_id=None):
    item = {"item_order": order, "item_kind": "cardio", **prescription}
    if item_id is not None:
        item["id"] = item_id
    return item


def _published():
    """Modelo vigente (forma de `_get_template`): slot 6 com cardio, slot 2 sem."""
    return {
        "id": TEMPLATE_ID,
        "name": "ABC",
        "is_active": 1,
        "volume_mode": "percent_budget",
        "template_type": "abc",
        "current_version": {"id": VERSION_ID, "number": 4},
        "slots": [
            {"id": 2001, "sequence": 2, "name": "Upper - SEG", "slot_type": "normal", "weekday": 1,
             "items": [{"id": 9001, "item_order": 1, "item_kind": "muscle_group", "muscle_group_id": 52, "sets_count": 3}]},
            {"id": 2069, "sequence": 6, "name": "Lower - TER", "slot_type": "normal", "weekday": 2,
             "items": [
                 {"id": 9002, "item_order": 1, "item_kind": "muscle_group", "muscle_group_id": 60, "sets_count": 4},
                 _cardio_item(2, NORMAL_20_Z2, item_id=10975),
             ]},
        ],
    }


def _base_payload():
    return wpt.draft_payload_from_template(_published())


def _link(sequence=6, action="replace", item_id=10975, item_order=2, template_id=TEMPLATE_ID):
    return {"template_id": template_id, "template_version_id": VERSION_ID, "slot_id": 2069, "slot_sequence": sequence,
            "slot_name": "Lower - TER", "action": action, "item_id": item_id, "item_order": item_order,
            "source": "weekday"}


def _session(day, prescription, link):
    return {"date": day, "prescription": prescription, "plan_link": link}


def _slot(payload, sequence):
    return next(s for s in payload["slots"] if s["sequence"] == sequence)


def _apply(payload, sessions, *, has_draft=False, published=None):
    return td.apply_cardio_sessions(
        payload, sessions, template_id=TEMPLATE_ID, published=published or _published(), has_draft=has_draft
    )


# ---------------------------------------------------------------------------
# Dono: etapas, mesma prescrição, total em minutos
# ---------------------------------------------------------------------------


def test_etapas_do_normal_e_do_hiit_vem_do_dono():
    normal = cp.describe_steps(NORMAL_30_Z2)
    assert [s["kind"] for s in normal] == [cp.STEP_WARMUP, cp.STEP_MAIN, cp.STEP_COOLDOWN]
    assert normal[1]["text"] == "30 min Z2" and normal[1]["zones"] == [2] and normal[1]["seconds"] == 1800
    hiit = cp.describe_steps(HIIT)
    assert [s["kind"] for s in hiit] == [cp.STEP_WARMUP, cp.STEP_INTERVALS, cp.STEP_COOLDOWN]
    assert hiit[1]["text"] == "8× [30 s Z4 / 90 s Z2]"
    assert hiit[1]["zones"] == [4, 2] and hiit[1]["rounds"] == 8
    assert hiit[1]["seconds"] == cp.main_seconds(HIIT)
    # Aquecimento/recuperação 0 e zona não definida somem.
    so_tempo = cp.describe_steps({"item_kind": "cardio", "cardio_duration_minutes": 25})
    assert so_tempo == [{"kind": cp.STEP_MAIN, "label": cp.STEP_LABELS[cp.STEP_MAIN], "text": "25 min",
                         "seconds": 1500, "zone": None, "zones": []}]


def test_mesma_prescricao_ignora_campos_do_outro_modo_e_zero_ausente():
    assert cp.same_prescription(NORMAL_30_Z2, {**NORMAL_30_Z2, "hiit_rounds": 9, "item_kind": "cardio"})
    sem_aquec = {k: v for k, v in NORMAL_30_Z2.items() if k != "cardio_cooldown_minutes"}
    assert cp.same_prescription({**sem_aquec, "cardio_cooldown_minutes": 0}, sem_aquec)
    assert not cp.same_prescription(NORMAL_30_Z2, NORMAL_20_Z2)
    assert not cp.same_prescription(NORMAL_30_Z2, HIIT)


def test_total_em_minutos_arredonda_pelo_dono():
    assert cp.total_minutes(NORMAL_30_Z2) == 40
    assert cp.total_minutes(HIIT) == 10 + 16  # 8 × (30 + 90) s = 16 min


# ---------------------------------------------------------------------------
# template_drafts.apply_cardio_sessions (puro)
# ---------------------------------------------------------------------------


def test_replace_troca_o_primeiro_cardio_do_slot_sem_mexer_no_resto():
    base = _base_payload()
    antes = copy.deepcopy(base)
    novo, results = _apply(base, [_session("2026-10-28", NORMAL_30_Z2, _link())])
    assert base == antes  # entrada intocada
    assert [r["status"] for r in results] == [td.APPLY_APPLIED] and results[0]["code"] == td.CODE_REPLACED
    items = _slot(novo, 6)["items"]
    assert len(items) == 2 and items[0] == _slot(antes, 6)["items"][0]
    assert items[1]["item_order"] == 2 and cp.same_prescription(items[1], NORMAL_30_Z2)
    assert items[1]["hiit_rounds"] is None and items[1]["sets_count"] is None
    assert _slot(novo, 2) == _slot(antes, 2)


def test_add_acrescenta_cardio_no_fim_do_slot():
    novo, results = _apply(
        _base_payload(), [_session("2026-10-27", HIIT, _link(sequence=2, action="add", item_id=None, item_order=None))]
    )
    assert results[0]["code"] == td.CODE_ADDED
    items = _slot(novo, 2)["items"]
    assert [i["item_kind"] for i in items] == ["muscle_group", "cardio"]
    assert items[1]["item_order"] == 2 and cp.same_prescription(items[1], HIIT)


def test_aplicar_duas_vezes_nao_duplica_nem_muda():
    sessions = [
        _session("2026-10-27", HIIT, _link(sequence=2, action="add", item_id=None, item_order=None)),
        _session("2026-10-28", NORMAL_30_Z2, _link()),
    ]
    uma, _ = _apply(_base_payload(), sessions)
    duas, results = _apply(uma, sessions, has_draft=True)
    assert duas == uma
    assert [r["status"] for r in results] == [td.APPLY_ALREADY, td.APPLY_ALREADY]


@pytest.mark.parametrize(
    "session, code",
    [
        (_session("2026-10-29", NORMAL_30_Z2, None), td.CODE_NO_PLAN_LINK),
        (_session("2026-10-29", NORMAL_30_Z2, _link(template_id=99)), td.CODE_OTHER_TEMPLATE),
        (_session("2026-10-29", NORMAL_30_Z2, _link(sequence=7)), td.CODE_SLOT_MISSING),
        (_session("2026-10-29", {**HIIT, "hiit_high_zone": 2}, _link()), td.CODE_INVALID_PRESCRIPTION),
    ],
)
def test_recusas_com_mensagem(session, code):
    novo, results = _apply(_base_payload(), [session])
    assert novo == _base_payload()
    assert results[0]["status"] == td.APPLY_REJECTED and results[0]["code"] == code
    assert results[0]["message"]


def test_prescricao_invalida_traz_a_mensagem_do_dono():
    _, results = _apply(_base_payload(), [_session("2026-10-29", {**HIIT, "hiit_high_zone": 2}, _link())])
    assert cp.validation_error({**HIIT, "hiit_high_zone": 2}) in results[0]["message"]


def test_duas_sessoes_no_mesmo_slot_vale_a_primeira_por_data():
    novo, results = _apply(
        _base_payload(),
        [_session("2026-11-01", NORMAL_20_Z2, _link()), _session("2026-10-28", NORMAL_30_Z2, _link())],
    )
    assert [(r["date"], r["code"]) for r in results] == [
        ("2026-10-28", td.CODE_REPLACED), ("2026-11-01", td.CODE_SLOT_TAKEN)
    ]
    assert cp.same_prescription(_slot(novo, 6)["items"][1], NORMAL_30_Z2)


def test_conflito_rascunho_com_cardio_editado_nao_e_sobrescrito():
    rascunho = _base_payload()
    _slot(rascunho, 6)["items"][1].update(cardio_duration_minutes=45)  # o usuário mexeu no rascunho
    novo, results = _apply(rascunho, [_session("2026-10-28", NORMAL_30_Z2, _link())], has_draft=True)
    assert novo == rascunho
    assert results[0]["code"] == td.CODE_CARDIO_EDITED and "editor" in results[0]["message"]


def test_rascunho_sem_mudanca_no_cardio_aceita_o_replace():
    rascunho = _base_payload()
    rascunho["name"] = "Outro nome no rascunho"
    novo, results = _apply(rascunho, [_session("2026-10-28", NORMAL_30_Z2, _link())], has_draft=True)
    assert results[0]["code"] == td.CODE_REPLACED and novo["name"] == "Outro nome no rascunho"


def test_replace_sem_cardio_no_slot_e_conflito():
    rascunho = _base_payload()
    _slot(rascunho, 6)["items"].pop()
    _, results = _apply(rascunho, [_session("2026-10-28", NORMAL_30_Z2, _link())], has_draft=True)
    assert results[0]["code"] == td.CODE_CARDIO_MISSING


def test_add_com_outro_cardio_no_slot_e_conflito():
    _, results = _apply(_base_payload(), [_session("2026-10-28", HIIT, _link(action="add", item_id=None))])
    assert results[0]["code"] == td.CODE_SLOT_HAS_CARDIO


def test_mesma_versao_com_item_id_diferente_e_conflito():
    _, results = _apply(_base_payload(), [_session("2026-10-28", NORMAL_30_Z2, _link(item_id=1))])
    assert results[0]["code"] == td.CODE_CARDIO_EDITED
    # Versão nova (o id do item mudou com ela): `item_id` não confere mais nada.
    published = _published()
    published["current_version"] = {"id": VERSION_ID + 1, "number": 5}
    _, results = _apply(_base_payload(), [_session("2026-10-28", NORMAL_30_Z2, _link(item_id=1))], published=published)
    assert results[0]["code"] == td.CODE_REPLACED


def test_payload_do_rascunho_a_partir_da_vigente_tem_a_forma_do_editor():
    payload = _base_payload()
    assert "template_type" not in payload and "generation_log_id" not in payload
    assert [s["sequence"] for s in payload["slots"]] == [2, 6]
    assert "id" not in payload["slots"][1]["items"][1]
    assert payload["slots"][1]["items"][1]["cardio_duration_minutes"] == 20
    assert payload["is_active"] is True


# ---------------------------------------------------------------------------
# Escrita no rascunho (banco falso)
# ---------------------------------------------------------------------------

DRAFTS = td.TABLE


class _Cursor:
    def __init__(self, db):
        self.db = db
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.db.sql.append(flat)
        self._rows = []
        if flat.startswith("SELECT id, name, description, is_active FROM workout_plan_templates"):
            self._rows = [{"id": params[0], "name": "ABC", "description": None, "is_active": self.db.is_active}]
        elif flat.startswith(f"INSERT INTO {DRAFTS}"):
            self.db.draft = {"template_id": params[0], "payload": params[1], "updated_by_user_id": params[2]}
        elif flat.startswith("SELECT d.template_id"):
            if self.db.draft:
                self._rows = [dict(self.db.draft, updated_at="2026-10-26 10:00:00", updated_by_username="pt")]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _DB:
    def __init__(self, *, is_active=1, draft=None):
        self.sql = []
        self.is_active = is_active
        self.draft = draft

    @contextlib.contextmanager
    def transaction(self):
        db = self

        class Conn:
            def cursor(self):
                return _Cursor(db)

        yield Conn()


@pytest.fixture
def fake(monkeypatch):
    def make(*, active_template=TEMPLATE_ID, is_active=1, draft=None):
        db = _DB(is_active=is_active, draft=draft)
        monkeypatch.setattr(wpt, "transaction", db.transaction)
        monkeypatch.setattr(wpt, "_get_template", lambda template_id, **_: _published())
        import app.template_assignments as ta

        monkeypatch.setattr(ta, "resolve_active_template_id", lambda user_id: active_template)
        return db

    return make


def test_aplicar_grava_so_no_rascunho(fake):
    db = fake()
    out = wpt.apply_cardio_sessions_to_draft(
        TEMPLATE_ID, [_session("2026-10-28", NORMAL_30_Z2, _link())], owner_user_id=1, actor=1
    )
    assert out["draft_saved"] is True and out["has_draft"] is True and out["draft_updated_at"]
    escritas = [s for s in db.sql if re.match(r"(INSERT|UPDATE|DELETE)\b", s)]
    assert escritas and all(DRAFTS in s for s in escritas), escritas
    gravado = json.loads(db.draft["payload"])
    assert cp.same_prescription(_slot(gravado, 6)["items"][1], NORMAL_30_Z2)
    assert db.draft["updated_by_user_id"] == 1


def test_nada_mudou_nao_grava(fake):
    payload = _base_payload()
    _slot(payload, 6)["items"][1].update(cardio_duration_minutes=30)
    db = fake(draft={"template_id": TEMPLATE_ID, "payload": json.dumps(payload), "updated_by_user_id": 1})
    out = wpt.apply_cardio_sessions_to_draft(
        TEMPLATE_ID, [_session("2026-10-28", NORMAL_30_Z2, _link())], owner_user_id=1, actor=1
    )
    assert out["draft_saved"] is False and out["results"][0]["status"] == td.APPLY_ALREADY
    assert not [s for s in db.sql if re.match(r"(INSERT|UPDATE|DELETE)\b", s)]


def test_modelo_que_nao_e_o_ativo_recusa_tudo(fake):
    db = fake(active_template=99)
    with pytest.raises(HTTPException) as exc:
        wpt.apply_cardio_sessions_to_draft(TEMPLATE_ID, [], owner_user_id=1, actor=1)
    assert exc.value.status_code == 409 and exc.value.detail["reason"] == "template_not_active"
    assert db.sql == []


def test_modelo_inativo_recusa_tudo(fake):
    db = fake(is_active=0)
    with pytest.raises(HTTPException) as exc:
        wpt.apply_cardio_sessions_to_draft(
            TEMPLATE_ID, [_session("2026-10-28", NORMAL_30_Z2, _link())], owner_user_id=1, actor=1
        )
    assert exc.value.status_code == 409 and exc.value.detail["reason"] == "template_inactive"
    assert db.draft is None


# ---------------------------------------------------------------------------
# Rotas da recomendação
# ---------------------------------------------------------------------------


def _recommendation_json():
    return {
        "schema_version": 1,
        "prompt_version": "cardio-agent-v3",
        "summary": "s", "objective": "o", "confidence": "medium", "confidence_reason": "r",
        "sessions": [
            {"date": "2026-10-28", "weekday": 3, "prescription": NORMAL_30_Z2,
             "prescription_text": cp.describe(NORMAL_30_Z2), "prescription_short": cp.describe_short(NORMAL_30_Z2),
             "total_seconds": 2400, "rationale": "base aeróbica", "signals": ["TSB"], "plan_link": _link(),
             "adjustments": [cardio_envelope.ADJ_HIIT_VETOED]},
            {"date": "2026-10-30", "weekday": 5, "prescription": NORMAL_20_Z2,
             "prescription_text": cp.describe(NORMAL_20_Z2), "prescription_short": cp.describe_short(NORMAL_20_Z2),
             "total_seconds": 1800, "rationale": "", "signals": [], "plan_link": None, "adjustments": []},
        ],
        "rejected": [{"date": "2026-11-01", "action": "rejected", "code": cardio_envelope.ADJ_WEEKLY_MINUTES_CAP,
                      "detail": None}],
        "adjustments": [{"date": "2026-10-28", "action": "adjusted", "code": cardio_envelope.ADJ_HIIT_VETOED,
                         "detail": f"{cardio_envelope.REASON_EVE_OF_LEG_DAY},{cardio_envelope.REASON_TSB_LOW}"}],
        "envelope": {},
        "plan": {"template_id": TEMPLATE_ID, "template_name": "ABC", "template_version_id": VERSION_ID},
    }


def _row(structured):
    return {"id": 5, "user_id": 1, "reference_date": "2026-10-27", "recommendation_markdown": "# md",
            "recommendation_json": json.dumps(structured) if structured is not None else None}


@pytest.fixture
def rec_db(monkeypatch):
    state = {"row": _row(_recommendation_json())}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            self.params = params

        def fetchone(self):
            row = state["row"]
            return dict(row) if row and self.params == (row["id"], row["user_id"]) else None

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            pass

    monkeypatch.setattr(cr, "_connect", lambda: Conn())
    return state


def _ident(role="admin", user_id=1):
    return Identity(user_id=user_id, role=role, source="session")


def test_detalhe_traz_a_visao_pronta(rec_db):
    row = cr.get_cardio_recommendation(5, identity=_ident())
    view = row["recommendation_view"]
    assert view["can_apply"] is True
    first, second = view["sessions"]
    assert first["date_label"] == "28/10 (qua)" and first["total_minutes_label"] == "40 min"
    assert first["applicable"] is True and second["applicable"] is False  # sem plan_link
    assert first["adjustment_labels"] == [cardio_recommendation_v3.CODE_LABELS[cardio_envelope.ADJ_HIIT_VETOED]]
    notes = {n["code"]: n for n in view["notes"]}
    assert notes[cardio_envelope.ADJ_HIIT_VETOED]["detail_label"] == "véspera de perna, TSB baixo"
    assert notes[cardio_envelope.ADJ_WEEKLY_MINUTES_CAP]["label"] == "teto semanal de minutos"
    assert view["confidence_label"] == "Médio"


def test_detalhe_aluno_nao_aplica_e_antiga_sem_visao(rec_db):
    view = cr.get_cardio_recommendation(5, identity=_ident(role="regular"))["recommendation_view"]
    assert view["can_apply"] is False and not any(s["applicable"] for s in view["sessions"])
    rec_db["row"] = _row(None)
    row = cr.get_cardio_recommendation(5, identity=_ident())
    assert row["recommendation_json"] is None and row["recommendation_view"] is None


def test_markdown_continua_com_os_mesmos_rotulos():
    md = cardio_recommendation_v3.render_markdown(_recommendation_json())
    assert "28/10 (qua): HIIT vetado, trocado por Normal (véspera de perna, TSB baixo)" in md
    assert "01/11 (dom): teto semanal de minutos" in md


def test_aplicar_repassa_as_sessoes_escolhidas(rec_db, monkeypatch):
    calls = []

    def fake_apply(template_id, sessions, *, owner_user_id, actor):
        calls.append((template_id, [s["date"] for s in sessions], owner_user_id, actor))
        return {"template_id": template_id, "draft_saved": True, "results": [
            td.apply_result(s["date"], td.APPLY_APPLIED, td.CODE_REPLACED, link=s["plan_link"]) for s in sessions
        ]}

    monkeypatch.setattr(wpt, "apply_cardio_sessions_to_draft", fake_apply)
    out = cr.apply_cardio_recommendation(
        5, cr.CardioApplyIn(dates=["2026-10-28", "2026-10-28", "2026-12-25"]), identity=_ident(role="personal_trainer")
    )
    assert calls == [(TEMPLATE_ID, ["2026-10-28"], 1, 1)]
    assert [(r["date"], r["code"]) for r in out["results"]] == [
        ("2026-10-28", td.CODE_REPLACED), ("2026-12-25", td.CODE_NOT_IN_RECOMMENDATION)
    ]
    assert out["recommendation_id"] == 5


def test_aplicar_recomendacao_antiga_ou_sem_modelo_e_409(rec_db):
    rec_db["row"] = _row(None)
    with pytest.raises(HTTPException) as exc:
        cr.apply_cardio_recommendation(5, cr.CardioApplyIn(dates=["2026-10-28"]), identity=_ident())
    assert exc.value.status_code == 409 and exc.value.detail["reason"] == "legacy_recommendation"
    rec_db["row"] = _row({**_recommendation_json(), "plan": None})
    with pytest.raises(HTTPException) as exc:
        cr.apply_cardio_recommendation(5, cr.CardioApplyIn(dates=["2026-10-28"]), identity=_ident())
    assert exc.value.detail["reason"] == "no_template"


def test_aplicar_recomendacao_de_outro_usuario_e_404(rec_db):
    with pytest.raises(HTTPException) as exc:
        cr.apply_cardio_recommendation(5, cr.CardioApplyIn(dates=["2026-10-28"]), identity=_ident(user_id=2))
    assert exc.value.status_code == 404


def test_aplicar_exige_quem_edita_o_modelo(rec_db):
    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[get_current_identity] = lambda: _ident(role="regular")
    resposta = TestClient(app).post("/cardio-recommendations/5/apply", json={"dates": ["2026-10-28"]})
    assert resposta.status_code == 403


def test_aplicar_limita_as_datas_ao_horizonte():
    with pytest.raises(ValueError):
        cr.CardioApplyIn(dates=[f"2026-11-{d:02d}" for d in range(1, cardio_envelope.RECOMMENDATION_HORIZON_DAYS + 2)])
    with pytest.raises(ValueError):
        cr.CardioApplyIn(dates=[])


# ---------------------------------------------------------------------------
# Modo Treino (cardio_display)
# ---------------------------------------------------------------------------

ZONES = {"source": "intervals", "max_hr_bpm": None, "zones": [
    {"zone": z, "label": f"Z{z}", "lower_bpm": 100 + 15 * (z - 1), "upper_bpm": 114 + 15 * (z - 1)} for z in range(1, 6)
]}


def test_view_do_hiit_tem_faixas_de_bpm_por_zona():
    view = cardio_display.prescription_view({"item_kind": "cardio", **HIIT}, ZONES)
    assert view["mode_label"] == "HIIT" and view["text"] == cp.describe(HIIT)
    assert view["total_minutes_label"] == f"{cp.total_minutes(HIIT)} min"
    intervals = next(s for s in view["steps"] if s["kind"] == cp.STEP_INTERVALS)
    assert [(z["zone"], z["lower_bpm"], z["upper_bpm"]) for z in intervals["zones"]] == [(4, 145, 159), (2, 115, 129)]


def test_view_sem_zonas_do_atleta_mostra_so_a_zona():
    view = cardio_display.prescription_view({"item_kind": "cardio", **NORMAL_30_Z2}, {"source": "none", "zones": []})
    main = next(s for s in view["steps"] if s["kind"] == cp.STEP_MAIN)
    assert main["zones"] == [{"zone": 2, "label": "Z2", "lower_bpm": None, "upper_bpm": None}]


def test_cardio_do_slot_da_ficha(monkeypatch):
    import app.athlete_hr_zones as ahz
    import app.workout_plan_templates_engine as engine

    pedidos = []
    monkeypatch.setattr(engine, "get_template_if_assigned", lambda tid, uid: pedidos.append((tid, uid)) or _published())

    @contextlib.contextmanager
    def tx():
        class Conn:
            def cursor(self):
                return contextlib.nullcontext(None)

        yield Conn()

    import app.db as db

    monkeypatch.setattr(db, "transaction", tx)
    monkeypatch.setattr(ahz, "load_athlete_zones", lambda cur, uid: None)
    monkeypatch.setattr(ahz, "zones_payload", lambda athlete: ZONES)
    out = cardio_display.slot_cardio_for_plan({"id": 9, "template_id": TEMPLATE_ID, "template_slot_sequence": 6}, 1)
    assert pedidos == [(TEMPLATE_ID, 1)]
    assert out["slot_name"] == "Lower - TER" and out["zones_source"] == "intervals"
    assert [i["text"] for i in out["items"]] == [cp.describe(NORMAL_20_Z2)]
    sem_cardio = cardio_display.slot_cardio_for_plan({"id": 9, "template_id": TEMPLATE_ID, "template_slot_sequence": 2}, 1)
    assert sem_cardio["items"] == [] and sem_cardio["slot_name"] == "Upper - SEG"
    assert cardio_display.slot_cardio_for_plan({"id": 9, "template_id": None, "template_slot_sequence": None}, 1)["items"] == []


def test_modelo_ilegivel_nao_quebra_o_fim_do_treino(monkeypatch):
    import app.workout_plan_templates_engine as engine

    def boom(tid, uid):
        raise RuntimeError("Unknown column")

    monkeypatch.setattr(engine, "get_template_if_assigned", boom)
    out = cardio_display.slot_cardio_for_plan({"id": 9, "template_id": TEMPLATE_ID, "template_slot_sequence": 6}, 1)
    assert out["items"] == []


def test_rotulo_de_minutos_e_do_dono():
    assert cp.minutes_label(2400) == "40 min"
    assert cp.minutes_label(89) == "1 min" and cp.minutes_label(None) == "0 min"
    assert cp.minutes_from_seconds(90) == 2  # meio para cima
    assert cp.total_minutes(NORMAL_30_Z2) == cp.minutes_from_seconds(cp.total_seconds(NORMAL_30_Z2))


def test_modelo_que_nao_vira_payload_e_409_com_mensagem(fake, monkeypatch):
    fake()
    quebrado = _published()
    quebrado["slots"][0]["items"][0]["item_kind"] = None  # forma que o PUT do Modelo não aceita
    monkeypatch.setattr(wpt, "_get_template", lambda template_id, **_: quebrado)
    with pytest.raises(HTTPException) as exc:
        wpt.apply_cardio_sessions_to_draft(
            TEMPLATE_ID, [_session("2026-10-28", NORMAL_30_Z2, _link())], owner_user_id=1, actor=1
        )
    assert exc.value.status_code == 409 and exc.value.detail["reason"] == "template_unreadable"
