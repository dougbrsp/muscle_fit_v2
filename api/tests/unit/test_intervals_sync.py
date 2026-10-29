"""Job de sync automatico do Intervals.icu (`app/intervals_sync.py` e
`app/lambda_intervals_sync.py`, melhoria M2), sem banco real e sem rede.

O banco e um duble em memoria (`FakeDB`) que entende SO os statements que o
job emite, aplicando a MESMA semantica de chave das tabelas reais
(`intervals_activities(user_id, external_id)`,
`intervals_wellness(user_id, wellness_date)`,
`activity_intervals_link(activity_id)` e o `COALESCE` do preenchimento de
`activities`). E isso que permite provar idempotencia aqui: rodar duas vezes e
comparar o estado inteiro.

Cobre:
- so usuarios com a API configurada entram no job;
- associacao de FC: match exato, dentro da janela, multiplos candidatos,
  nenhum candidato;
- idempotencia (duas execucoes -> mesmo estado, sem duplicar);
- falha da API de um usuario nao derruba os demais;
- a chave de API nunca aparece em log, evento ou relatorio.

Rodar: cd api && python -m pytest tests/unit/test_intervals_sync.py
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta

import pytest

from app import intervals_sync as sync
from app import lambda_intervals_sync
from tests.support.fake_db import LoopingExecuteMany

API_KEY = "chave-super-secreta-do-intervals-1234567890"

HOJE = date.today()
TREINO_MANHA = datetime.combine(HOJE, datetime.min.time()) + timedelta(hours=8)


# ---------------------------------------------------------------------------
# Banco em memoria
# ---------------------------------------------------------------------------


class FakeCursor(LoopingExecuteMany):
    def __init__(self, db: "FakeDB"):
        self.db = db
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql: str, params=None):
        self.db.statements.append(sql)
        params = tuple(params or ())
        text = " ".join(sql.split())

        if text.startswith("SELECT id FROM users"):
            self._rows = self.db.eligible_users(params)
        elif text.startswith("SELECT intervals_athlete_id"):
            self._rows = self.db.credentials(int(params[0]))
        elif "FROM activities a" in text:
            self._rows = self.db.strength_sessions(int(params[0]))
        elif "FROM intervals_activities" in text and text.startswith("SELECT"):
            self._rows = self.db.weight_training(int(params[0]))
        elif text.startswith("INSERT INTO intervals_activities"):
            self.db.upsert_intervals_activity(params)
        elif text.startswith("INSERT INTO intervals_wellness"):
            self.db.upsert_wellness(params)
        elif text.startswith("INSERT INTO activity_intervals_link"):
            self.db.upsert_link(params)
        elif text.startswith("UPDATE activities"):
            self.db.fill_activity_hr(params)
        elif text.startswith("INSERT INTO intervals_athlete_hr_zones"):
            self.db.athlete_zones[(int(params[0]), int(params[1]))] = params
        elif text.startswith("DELETE FROM intervals_athlete_hr_zones"):
            keep = {int(v) for v in params[1:]}
            for key in [k for k in self.db.athlete_zones if k[0] == int(params[0]) and k[1] not in keep]:
                del self.db.athlete_zones[key]
        else:  # pragma: no cover - statement novo precisa de duble explicito
            raise AssertionError(f"statement nao previsto no duble: {text}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        return None


class FakeConn:
    def __init__(self, db: "FakeDB"):
        self.db = db
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.committed = True
        self.db.commits += 1

    def rollback(self):
        self.rolled_back = True
        self.db.rollbacks += 1

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


class FakeDB:
    """Estado das quatro tabelas envolvidas, com as chaves naturais reais."""

    def __init__(self, users: dict, activities: list[dict]):
        self.users = users
        self.activities = {int(item["id"]): dict(item) for item in activities}
        self.intervals_activities: dict[tuple[int, str], dict] = {}
        self.intervals_wellness: dict[tuple[int, str], dict] = {}
        self.links: dict[int, dict] = {}
        self.athlete_zones: dict[tuple[int, int], tuple] = {}
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self._next_intervals_id = 1

    # --- conexao ---------------------------------------------------------
    def connect(self) -> FakeConn:
        return FakeConn(self)

    # --- leituras --------------------------------------------------------
    def eligible_users(self, params) -> list[dict]:
        wanted = {int(value) for value in params} if params else None
        return [
            {"id": user_id}
            for user_id, user in sorted(self.users.items())
            if user.get("is_active", 1) == 1
            and user.get("intervals_athlete_id")
            and user.get("intervals_api_key_encrypted")
            and (wanted is None or user_id in wanted)
        ]

    def credentials(self, user_id: int) -> list[dict]:
        user = self.users.get(user_id)
        if not user:
            return []
        return [
            {
                "intervals_athlete_id": user.get("intervals_athlete_id"),
                "intervals_api_key_encrypted": user.get("intervals_api_key_encrypted"),
            }
        ]

    def strength_sessions(self, user_id: int) -> list[dict]:
        return [
            {
                "id": item["id"],
                "started_at": item.get("started_at"),
                "avg_hr_bpm": item.get("avg_hr_bpm"),
                "max_hr_bpm": item.get("max_hr_bpm"),
                "calories_kcal": item.get("calories_kcal"),
            }
            for item in sorted(self.activities.values(), key=lambda a: a["id"])
            if item["user_id"] == user_id and "muscul" in str(item.get("activity_type", "musculacao")).lower()
        ]

    def weight_training(self, user_id: int) -> list[dict]:
        rows = [
            row
            for key, row in self.intervals_activities.items()
            if key[0] == user_id and str(row.get("sport_type", "")).lower().replace(" ", "") == "weighttraining"
        ]
        return [dict(row) for row in sorted(rows, key=lambda r: (str(r.get("start_time")), r["external_id"]))]

    # --- escritas --------------------------------------------------------
    def upsert_intervals_activity(self, params):
        (
            user_id,
            external_id,
            activity_date,
            start_time,
            name,
            sport_type,
            moving_time_seconds,
            _elapsed,
            _distance,
            calories_kcal,
            avg_hr_bpm,
            max_hr_bpm,
            training_load,
            _intensity,
            _raw,
            hr_zone_seconds,
        ) = params
        key = (int(user_id), str(external_id))
        row = self.intervals_activities.get(key)
        row_id = row["id"] if row else self._take_id()
        self.intervals_activities[key] = {
            "id": row_id,
            "external_id": external_id,
            "activity_date": activity_date,
            "start_time": start_time,
            "name": name,
            "sport_type": sport_type,
            "moving_time_seconds": moving_time_seconds,
            "avg_hr_bpm": avg_hr_bpm,
            "max_hr_bpm": max_hr_bpm,
            "calories_kcal": calories_kcal,
            "training_load": training_load,
            "hr_zone_seconds": hr_zone_seconds,
        }

    def _take_id(self) -> int:
        value = self._next_intervals_id
        self._next_intervals_id += 1
        return value

    def upsert_wellness(self, params):
        wellness_date, user_id = params[0], int(params[1])
        self.intervals_wellness[(user_id, str(wellness_date))] = {"fitness_ctl": params[2], "fatigue_atl": params[3]}

    def upsert_link(self, params):
        (
            user_id,
            activity_id,
            intervals_activity_id,
            external_id,
            status,
            delta,
            candidates_count,
            moving_time_seconds,
            avg_hr_bpm,
            max_hr_bpm,
            calories_kcal,
            training_load,
        ) = params
        self.links[int(activity_id)] = {
            "user_id": user_id,
            "intervals_activity_id": intervals_activity_id,
            "intervals_external_id": external_id,
            "match_status": status,
            "match_delta_seconds": delta,
            "candidates_count": candidates_count,
            "moving_time_seconds": moving_time_seconds,
            "avg_hr_bpm": avg_hr_bpm,
            "max_hr_bpm": max_hr_bpm,
            "calories_kcal": calories_kcal,
            "training_load": training_load,
        }

    def fill_activity_hr(self, params):
        avg_hr, max_hr, calories, activity_id, user_id = params
        activity = self.activities[int(activity_id)]
        assert activity["user_id"] == user_id
        for column, value in (("avg_hr_bpm", avg_hr), ("max_hr_bpm", max_hr), ("calories_kcal", calories)):
            if activity.get(column) is None:
                activity[column] = value

    def snapshot(self) -> dict:
        return copy.deepcopy(
            {
                "intervals_activities": self.intervals_activities,
                "intervals_wellness": self.intervals_wellness,
                "links": self.links,
                "activities": self.activities,
            }
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _intervals_activity(external_id: str, start: datetime, sport_type: str = "WeightTraining", avg_hr: float = 141.0):
    return {
        "id": external_id,
        "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "name": "Treino",
        "type": sport_type,
        "moving_time": 3600,
        "calories": 420,
        "average_heartrate": avg_hr,
        "max_heartrate": 175,
        "icu_training_load": 50,
    }


@pytest.fixture
def db():
    return FakeDB(
        users={
            1: {"intervals_athlete_id": "i1", "intervals_api_key_encrypted": "enc-1", "is_active": 1},
            2: {"intervals_athlete_id": None, "intervals_api_key_encrypted": None, "is_active": 1},
            3: {"intervals_athlete_id": "i3", "intervals_api_key_encrypted": "enc-3", "is_active": 0},
        },
        activities=[
            {
                "id": 10,
                "user_id": 1,
                "activity_type": "musculacao",
                "started_at": TREINO_MANHA,
                "avg_hr_bpm": None,
                "max_hr_bpm": None,
                "calories_kcal": None,
            },
            {
                "id": 11,
                "user_id": 2,
                "activity_type": "musculacao",
                "started_at": TREINO_MANHA,
                "avg_hr_bpm": None,
                "max_hr_bpm": None,
                "calories_kcal": None,
            },
        ],
    )


@pytest.fixture
def intervals_api(monkeypatch):
    """Duble do cliente HTTP. Guarda a `api_key` recebida para o teste de
    segredo, e permite programar erro por usuario."""
    state = {"activities": [], "wellness": [], "errors": {}, "keys": [], "calls": [], "athlete": {}, "athlete_error": None}

    def fake_fetch(athlete_id, api_key, oldest, newest):
        state["keys"].append(api_key)
        state["calls"].append(athlete_id)
        error = state["errors"].get(athlete_id)
        if error is not None:
            raise error
        return list(state["activities"]), list(state["wellness"])

    def fake_athlete(athlete_id, api_key):
        state["keys"].append(api_key)
        if state["athlete_error"] is not None:
            raise state["athlete_error"]
        return dict(state["athlete"])

    monkeypatch.setattr(sync.intervals_client, "fetch_activities_and_wellness", fake_fetch)
    monkeypatch.setattr(sync.intervals_client, "fetch_athlete", fake_athlete)
    monkeypatch.setattr(sync.intervals_store, "decrypt_secret", lambda value: API_KEY)
    return state


def _run(db, **kwargs):
    return sync.sync_users(conn_factory=db.connect, **kwargs)


# ---------------------------------------------------------------------------
# Elegibilidade
# ---------------------------------------------------------------------------


def test_somente_usuarios_com_a_api_configurada_entram_no_job(db, intervals_api):
    report = _run(db)
    assert report.users_eligible == 1
    assert [item.user_id for item in report.results] == [1]
    assert intervals_api["calls"] == ["i1"]


def test_usuario_sem_api_configurada_nao_gera_chamada_nem_falha(db, intervals_api):
    report = _run(db)
    assert report.failures == []
    # O usuario 2 tem treino, mas nenhum link e nenhuma leitura do Intervals.
    assert 11 not in db.links


def test_user_ids_restringe_o_job(db, intervals_api):
    db.users[2] = {"intervals_athlete_id": "i2", "intervals_api_key_encrypted": "enc-2", "is_active": 1}
    report = _run(db, user_ids=[2])
    assert [item.user_id for item in report.results] == [2]


# ---------------------------------------------------------------------------
# Associacao de FC
# ---------------------------------------------------------------------------


def test_match_exato_grava_link_e_preenche_a_fc_do_treino(db, intervals_api):
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    report = _run(db)

    assert report.totals["hr_matched"] == 1
    link = db.links[10]
    assert link["match_status"] == "MATCHED"
    assert link["match_delta_seconds"] == 0
    assert link["intervals_external_id"] == "ext-1"
    assert float(link["avg_hr_bpm"]) == 141.0
    # A FC tambem vai para a propria `activities`, que e o que as telas leem.
    assert float(db.activities[10]["avg_hr_bpm"]) == 141.0


def test_match_dentro_da_janela(db, intervals_api):
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA + timedelta(minutes=12))]
    report = _run(db)
    assert report.totals["hr_matched"] == 1
    assert db.links[10]["match_delta_seconds"] == 720


def test_multiplos_candidatos_marca_ambiguo_e_escolhe_o_mais_proximo(db, intervals_api):
    intervals_api["activities"] = [
        _intervals_activity("longe", TREINO_MANHA + timedelta(minutes=45), avg_hr=120.0),
        _intervals_activity("perto", TREINO_MANHA + timedelta(minutes=5), avg_hr=150.0),
    ]
    report = _run(db)

    assert report.totals["hr_ambiguous"] == 1
    assert report.totals["hr_matched"] == 0
    link = db.links[10]
    assert link["match_status"] == "MATCHED_AMBIGUOUS"
    assert link["intervals_external_id"] == "perto"
    assert link["candidates_count"] == 2


def test_nenhum_candidato_nao_grava_link(db, intervals_api):
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA + timedelta(hours=6))]
    report = _run(db)

    assert report.totals["hr_unmatched"] == 1
    assert db.links == {}
    assert db.activities[10]["avg_hr_bpm"] is None


def test_atividade_de_cardio_nao_vira_link(db, intervals_api):
    intervals_api["activities"] = [_intervals_activity("corrida", TREINO_MANHA, sport_type="Run")]
    report = _run(db)
    assert report.totals["hr_unmatched"] == 1
    assert db.links == {}


def test_treino_sem_horario_nao_casa(db, intervals_api):
    db.activities[10]["started_at"] = None
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    report = _run(db)
    assert report.totals["hr_no_start_time"] == 1
    assert db.links == {}


def test_fc_ja_preenchida_no_treino_nunca_e_sobrescrita(db, intervals_api):
    db.activities[10]["avg_hr_bpm"] = 99.0
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    _run(db)
    assert float(db.activities[10]["avg_hr_bpm"]) == 99.0
    # mas o link guarda o valor do Intervals sem mentir sobre a origem
    assert float(db.links[10]["avg_hr_bpm"]) == 141.0


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_rodar_duas_vezes_nao_duplica_nem_muda_nada(db, intervals_api):
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    intervals_api["wellness"] = [{"id": HOJE.isoformat(), "ctl": 40, "atl": 30}]

    primeiro = _run(db)
    estado_1 = db.snapshot()
    segundo = _run(db)
    estado_2 = db.snapshot()

    assert estado_1 == estado_2
    assert len(db.intervals_activities) == 1
    assert len(db.links) == 1
    assert primeiro.totals["hr_matched"] == segundo.totals["hr_matched"] == 1
    assert primeiro.totals["activities_upserted"] == segundo.totals["activities_upserted"] == 1
    # A FC do treino ja foi preenchida na 1a execucao; a 2a nao tem o que fazer.
    assert primeiro.totals["activities_hr_filled"] == 1
    assert segundo.totals["activities_hr_filled"] == 0


def test_resposta_vazia_do_intervals_nao_apaga_cache_nem_link(db, intervals_api):
    """Garantia "nunca sobrescrever com lixo": uma resposta vazia (queda
    parcial da API, atividade ainda nao subida pelo relogio) nao remove nada.
    O cache e o link continuam valendo, e nenhum UPDATE destrutivo acontece."""
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    _run(db)
    estado = db.snapshot()

    intervals_api["activities"] = []
    report = _run(db)

    assert db.snapshot() == estado
    assert db.links[10]["intervals_external_id"] == "ext-1"
    assert report.totals["users_failed"] == 0
    assert not any(statement.strip().upper().startswith("DELETE") for statement in db.statements)


def test_dry_run_nao_comita(db, intervals_api):
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    report = _run(db, dry_run=True)
    assert report.dry_run is True
    assert db.commits == 0 and db.rollbacks >= 1


# ---------------------------------------------------------------------------
# Falha isolada por usuario
# ---------------------------------------------------------------------------


def test_falha_de_um_usuario_nao_derruba_os_demais(db, intervals_api):
    db.users[2] = {"intervals_athlete_id": "i2", "intervals_api_key_encrypted": "enc-2", "is_active": 1}
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    intervals_api["errors"]["i1"] = sync.IntervalsClientError("401 nao autorizado", status_code=401)

    report = _run(db)

    assert [item.user_id for item in report.results] == [2]
    assert [(item.user_id, item.reason_code) for item in report.failures] == [(1, "intervals_unauthorized")]
    assert report.totals["users_failed"] == 1
    # O usuario que funcionou gravou normalmente.
    assert db.links[11]["match_status"] == "MATCHED"


@pytest.mark.parametrize(
    "erro, reason_code",
    [
        (sync.IntervalsClientError("nao autorizado", status_code=401), "intervals_unauthorized"),
        (sync.IntervalsClientError("quota", status_code=429), "intervals_rate_limited"),
        (sync.IntervalsClientError("timeout"), "intervals_network_error"),
        (sync.IntervalsClientError("bad gateway", status_code=502), "intervals_http_error"),
        (RuntimeError("qualquer outra coisa"), "unexpected_error"),
    ],
)
def test_reason_code_por_tipo_de_falha(db, intervals_api, erro, reason_code):
    intervals_api["errors"]["i1"] = erro
    report = _run(db)
    assert [item.reason_code for item in report.failures] == [reason_code]


def test_chave_que_nao_descriptografa_vira_falha_daquele_usuario(db, intervals_api, monkeypatch):
    def boom(value):
        raise ValueError("chave invalida")

    monkeypatch.setattr(sync.intervals_store, "decrypt_secret", boom)
    report = _run(db)
    assert [item.reason_code for item in report.failures] == ["decrypt_failed"]
    assert report.results == []


# ---------------------------------------------------------------------------
# Segredo e observabilidade
# ---------------------------------------------------------------------------


def test_a_chave_de_api_nunca_aparece_em_log_evento_ou_relatorio(db, intervals_api, caplog):
    intervals_api["errors"]["i1"] = sync.IntervalsClientError(
        f"Intervals.icu retornou erro HTTP 401: token {API_KEY} revogado", status_code=401
    )
    with caplog.at_level("INFO"):
        report = _run(db)

    assert intervals_api["keys"] == [API_KEY]  # a chave CHEGOU no cliente HTTP
    texto = json.dumps(report.to_dict(), default=str) + "\n".join(record.getMessage() for record in caplog.records)
    assert API_KEY not in texto


def test_eventos_estruturados_do_job(db, intervals_api, caplog):
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    with caplog.at_level("INFO", logger="musclefit.events"):
        _run(db)

    eventos = {}
    for record in caplog.records:
        if record.name != "musclefit.events":
            continue
        payload = json.loads(record.getMessage())
        eventos[payload["event"]] = payload

    assert set(eventos) == {"intervals_sync.started", "intervals_sync.user_done", "intervals_sync.finished"}
    assert eventos["intervals_sync.started"]["users_eligible"] == 1
    assert eventos["intervals_sync.user_done"]["user_id"] == 1
    assert eventos["intervals_sync.finished"]["users_processed"] == 1
    assert eventos["intervals_sync.finished"]["hr_matched"] == 1


# ---------------------------------------------------------------------------
# Janela e handler da Lambda
# ---------------------------------------------------------------------------


def test_janela_default_e_30_dias_e_valor_invalido_nao_quebra(monkeypatch):
    monkeypatch.delenv(sync.SYNC_DAYS_ENV, raising=False)
    assert sync.sync_days() == 30
    monkeypatch.setenv(sync.SYNC_DAYS_ENV, "abc")
    assert sync.sync_days() == 30
    monkeypatch.setenv(sync.SYNC_DAYS_ENV, "7")
    assert sync.sync_days() == 7
    assert sync.sync_days(99999) == sync.MAX_SYNC_DAYS
    assert sync.sync_days(0) == 30


def test_handler_repassa_o_evento_e_devolve_o_relatorio(monkeypatch):
    capturado = {}

    def fake_sync_users(user_ids=None, *, days=None, dry_run=False):
        capturado.update({"user_ids": user_ids, "days": days, "dry_run": dry_run})
        return sync.SyncReport(days=7, dry_run=dry_run, oldest="2026-09-01", newest="2026-09-08")

    monkeypatch.setattr(lambda_intervals_sync.intervals_sync, "sync_users", fake_sync_users)
    out = lambda_intervals_sync.handler({"user_ids": [1, 2], "days": 7, "dry_run": True}, None)

    assert capturado == {"user_ids": [1, 2], "days": 7, "dry_run": True}
    assert out["status"] == "ok" and out["days"] == 7


def test_handler_sem_evento_roda_para_todos(monkeypatch):
    capturado = {}

    def fake_sync_users(user_ids=None, *, days=None, dry_run=False):
        capturado.update({"user_ids": user_ids, "days": days, "dry_run": dry_run})
        return sync.SyncReport(days=30, dry_run=False, oldest="2026-08-01", newest="2026-09-01")

    monkeypatch.setattr(lambda_intervals_sync.intervals_sync, "sync_users", fake_sync_users)
    lambda_intervals_sync.handler({}, None)
    assert capturado == {"user_ids": None, "days": None, "dry_run": False}


@pytest.mark.parametrize("evento", [{"user_ids": []}, {"user_ids": "1"}, {"user_ids": ["x"]}, {"days": "muitos"}, {"dry_run": "sim"}])
def test_handler_recusa_evento_malformado(evento):
    with pytest.raises(lambda_intervals_sync.IntervalsSyncEventError):
        lambda_intervals_sync.handler(evento, None)


def test_handler_recusa_backend_de_dev_dentro_da_lambda(monkeypatch):
    """Regra anti-vazamento dev/prod: DB_BACKEND=pymysql numa Lambda real e
    configuracao de dev vazada."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-intervals-sync")
    monkeypatch.setenv("DB_BACKEND", "pymysql")
    with pytest.raises(lambda_intervals_sync.IntervalsSyncEventError) as exc:
        lambda_intervals_sync.handler({}, None)
    assert "DB_BACKEND" in str(exc.value)


# ---------------------------------------------------------------------------
# M98 C2: zonas de FC do atleta e tempo em zona da atividade
# ---------------------------------------------------------------------------

_ATHLETE = {
    "sportSettings": [
        {"id": 11, "types": ["Run", "VirtualRun"], "hr_zones": [129, 137, 145, 153, 157, 162, 170], "lthr": 154, "max_hr": 170},
        {"id": 12, "types": ["Swim"], "hr_zones": None},
    ]
}


def test_sync_grava_as_zonas_do_atleta_e_o_tempo_em_zona(db, intervals_api):
    intervals_api["athlete"] = _ATHLETE
    corrida = _intervals_activity("run-1", TREINO_MANHA + timedelta(hours=2), sport_type="VirtualRun")
    corrida["icu_hr_zone_times"] = [495, 211, 183, 176, 102, 130, 89]
    intervals_api["activities"] = [corrida]

    report = _run(db)

    assert report.totals["athlete_zones_upserted"] == 1  # o esporte sem zonas fica de fora
    params = db.athlete_zones[(1, 11)]
    assert params[3] == "[129, 137, 145, 153, 157, 162, 170]"
    assert (params[5], params[6]) == (170, 154)
    assert db.intervals_activities[(1, "run-1")]["hr_zone_seconds"] == "[495,211,183,176,102,130,89]"


def test_falha_nas_zonas_nao_derruba_o_sync_nem_apaga_zonas(db, intervals_api):
    db.athlete_zones[(1, 11)] = ("antigo",)
    intervals_api["athlete_error"] = sync.IntervalsClientError("boom", status_code=500)
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]

    report = _run(db)

    assert report.failures == []
    assert report.totals["athlete_zones_failed"] == 1
    assert report.totals["hr_matched"] == 1
    assert db.athlete_zones[(1, 11)] == ("antigo",)


def test_erro_inesperado_nas_zonas_tambem_nao_derruba_o_usuario(db, intervals_api):
    intervals_api["athlete_error"] = TimeoutError("rede")
    intervals_api["activities"] = [_intervals_activity("ext-1", TREINO_MANHA)]
    report = _run(db)
    assert report.failures == []
    assert report.totals["athlete_zones_failed"] == 1 and report.totals["hr_matched"] == 1


def test_atleta_sem_esporte_valido_nao_apaga_as_zonas_gravadas(db, intervals_api):
    db.athlete_zones[(1, 11)] = ("antigo",)
    intervals_api["athlete"] = {"sportSettings": [{"id": 12, "types": ["Swim"], "hr_zones": None}]}
    _run(db)
    assert db.athlete_zones[(1, 11)] == ("antigo",)
