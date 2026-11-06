"""Lote F (nutrição adaptativa): execuções do sync na confirmação do dia (E1) e o diário explicável (D1).

Cenários montados pelo caminho real (publicar plano pela rota) em banco em memória (`tests/support/nutrition_fake.py`,
que projeta só as colunas do SQL). Cobre: leitura das execuções com e sem a tabela, dia confirmado pelo registro
(relógio parado não vira descanso), `energy_detail` do diário (meta legada, adaptativa, sem meta, sem Intervals, sem a
106/107), `me/plan` com `energy`, escopo por usuário (comum x admin vendo outro) e a contagem de SELECTs.

Rodar: cd api && python -m pytest tests/unit/test_nutrition_energy_detail.py
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_dashboard as dash_mod
from app import nutrition_days as days_mod
from app import nutrition_energy_service as energy_svc
from app import nutrition_meals as meals_mod
from app import nutrition_plans as plans_mod
from app import nutrition_schema as schema
from app import nutrition_targets_service as svc
from app.domain.nutrition import activity_energy as ae
from app.security import Identity, get_current_identity
from tests.support.nutrition_dashboard_fake import FakeDashboardDB
from tests.unit.test_nutrition_modo_adaptativo_servico import (
    ADMIN,
    ALUNO,
    HOJE,
    OUTRO,
    SYNC_UTC,
    _adaptativo,
    _historico,
    _publicar,
    _rascunho,
)

FUSO = "America/Sao_Paulo"
ONTEM = HOJE - timedelta(days=1)
INICIO_FORCA = datetime(2026, 11, 4, 18, 0)


@pytest.fixture
def db(monkeypatch):
    banco = FakeDashboardDB()
    for modulo in (dash_mod, meals_mod, days_mod, plans_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
    for modulo in (meals_mod, dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    for modulo in (dash_mod, days_mod, plans_mod):
        monkeypatch.setattr(modulo, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(dash_mod, "_timezone_for", lambda conn, user_id: FUSO)
    monkeypatch.setattr(days_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    schema.reset_nutrition_tables_cache()
    yield banco
    schema.reset_nutrition_tables_cache()


def _client(role: str = "admin", user_id: int = ADMIN) -> TestClient:
    app = FastAPI()
    for modulo in (dash_mod, meals_mod, days_mod, plans_mod):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _dia(cliente, dia: date = HOJE, user_id: int = ALUNO):
    return cliente.get(f"/nutrition/days/{dia.isoformat()}?user_id={user_id}")


DESDE = HOJE - timedelta(days=10)


def _vigente_desde(db, plano_id: int, desde: date | None) -> None:
    """A rota publica com vigencia de HOJE; para ler dias passados o plano passa a valer de `desde` (so a data)."""
    if desde is not None:
        db.plans[plano_id]["effective_from"] = desde


def _plano_legado_publicado(db, desde: date | None = None) -> None:
    admin = _client()
    plano = _rascunho(admin, tdee_mode="auto_intervals", activity_level="light")
    assert _publicar(admin, plano["id"]).status_code == 200
    _vigente_desde(db, plano["id"], desde)


def _plano_adaptativo_publicado(db, modo: str = "gross", desde: date | None = None) -> None:
    admin = _client()
    plano = _adaptativo(admin, modo)
    assert _publicar(admin, plano["id"]).status_code == 200
    _vigente_desde(db, plano["id"], desde)


def _execucao(user_id: int = ALUNO, *, finished_at: datetime = SYNC_UTC, inicio: date = HOJE - timedelta(days=30),
              fim: date = HOJE, status: str = "ok") -> dict:
    return {"user_id": user_id, "started_at": finished_at, "finished_at": finished_at, "window_start": inicio,
            "window_end": fim, "status": status, "activities_count": 0, "source": "scheduled"}


def _gravar(db, *linhas: dict) -> None:
    for linha in linhas:
        db.sync_runs.append({"id": len(db.sync_runs) + 1, **linha})


def _primeiro_dia() -> date:
    return energy_svc.horizon_days(HOJE)[0]


# --- E1: leitura das execucoes ------------------------------------------------------------------------------------


def test_leitura_das_execucoes_traz_so_as_ok_do_aluno_que_alcancam_o_horizonte(db):
    _gravar(
        db,
        _execucao(),
        _execucao(status="error"),
        _execucao(OUTRO),
        _execucao(finished_at=SYNC_UTC - timedelta(days=90), inicio=HOJE - timedelta(days=120), fim=HOJE - timedelta(days=100)),
    )
    execucoes = energy_svc.load_sync_runs(db.conn().cursor(), ALUNO, _primeiro_dia())
    assert execucoes == (ae.SyncRun(finished_at=SYNC_UTC, window_start=HOJE - timedelta(days=30), window_end=HOJE),)


def test_sem_a_tabela_a_leitura_devolve_vazio_sem_consultar_a_tabela(db):
    db.sync_runs_table = False
    consultas: list[str] = []
    cursor = db.conn().cursor()
    original = cursor.execute
    cursor.execute = lambda sql, params=None: (consultas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
    assert energy_svc.load_sync_runs(cursor, ALUNO, _primeiro_dia()) == ()
    assert energy_svc.SYNC_RUNS_SQL not in consultas


def test_tabela_que_aparece_depois_passa_a_ser_lida(db):
    db.sync_runs_table = False
    assert energy_svc.load_sync_runs(db.conn().cursor(), ALUNO, _primeiro_dia()) == ()
    db.sync_runs_table = True  # a 107 rodou: "nao existe" nunca foi cacheado
    _gravar(db, _execucao())
    assert len(energy_svc.load_sync_runs(db.conn().cursor(), ALUNO, _primeiro_dia())) == 1


def test_falha_na_leitura_das_execucoes_degrada_para_a_regra_antiga(db, monkeypatch, caplog):
    def quebra(self, sql, params=None):
        raise RuntimeError("segredo-do-banco")

    cursor = db.conn().cursor()
    monkeypatch.setattr(type(cursor), "execute", quebra)
    assert energy_svc.load_sync_runs(cursor, ALUNO, _primeiro_dia()) == ()
    assert "RuntimeError" in caplog.text and "segredo-do-banco" not in caplog.text


def test_o_registro_confirma_o_dia_de_descanso_que_a_regra_antiga_nao_confirmaria(db):
    _historico(db)
    # Uma sincronizacao antiga deixa `last_sync_at` velho: o dia de ontem fica pendente pela regra antiga.
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=25), "Run", 200.0, start=datetime(2026, 10, 11, 7, 0),
                         synced_at=datetime(2026, 10, 12, 9, 0))
    # Atividade recente: a sequencia de descanso que contem o dia 04 fica dentro do teto do motor (7 dias).
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=3), "Run", 150.0, start=datetime(2026, 11, 2, 7, 0),
                         synced_at=datetime(2026, 10, 12, 9, 0))
    series = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE).series
    sem_registro = energy_svc.load_history(db.conn().cursor(), ALUNO, HOJE, series, timezone_name=FUSO)
    assert next(d for d in sem_registro.days if d.day == ONTEM).confirmed is False
    # Execucoes registradas (a regra exata vale a partir do dia da primeira): a de 05/11 06:00 local cobre o dia 04 e
    # terminou depois do fim dele (+ folga): descanso confirmado.
    _gravar(db, _execucao(finished_at=SYNC_UTC - timedelta(days=1)), _execucao())
    com_registro = energy_svc.load_history(db.conn().cursor(), ALUNO, HOJE, series, timezone_name=FUSO)
    ontem = next(d for d in com_registro.days if d.day == ONTEM)
    assert ontem.confirmed is True and ontem.active_kcal == 0.0
    assert ontem.confirmation_source == ae.DAY_CONFIRMATION_SOURCE_RUN


# --- D1: diario explicavel ----------------------------------------------------------------------------------------


def test_dia_com_meta_adaptativa_traz_composicao_atividades_e_retrato(db):
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "WeightTraining", 428.0, start=INICIO_FORCA, name="Musculação", elapsed=3600,
                         synced_at=SYNC_UTC)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    corpo = _dia(_client(), ONTEM).json()
    detalhe = corpo["energy_detail"]
    assert set(detalhe) == {"breakdown", "activities", "energy"}
    quebra = detalhe["breakdown"]
    assert quebra["parts"] and set(quebra) >= {"parts", "notes", "total_kcal"}
    assert all(set(p) == {"code", "label", "kcal"} for p in quebra["parts"])
    assert sum(p["kcal"] for p in quebra["parts"]) == pytest.approx(quebra["total_kcal"], abs=1.0)
    assert detalhe["energy"] == corpo["target"]["energy"] and detalhe["energy"] is not None
    atividades = detalhe["activities"]
    assert atividades["day"] == ONTEM.isoformat() and atividades["confirmed"] is True
    assert atividades["counted_activities"] == 1
    assert atividades["activities"][0]["calorie_mode"] == "gross"  # o modo do plano que vale no dia
    json.dumps(corpo)


def test_dia_com_meta_legada_traz_composicao_sem_retrato_e_sem_atividades_sem_intervals(db):
    _historico(db)
    _plano_legado_publicado(db)
    detalhe = _dia(_client()).json()["energy_detail"]
    assert detalhe["energy"] is None and detalhe["activities"] is None  # sem Intervals importado
    assert detalhe["breakdown"] is not None and detalhe["breakdown"]["parts"]


def test_dia_legado_com_intervals_normaliza_no_modo_padrao_do_dono(db):
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 300.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    _plano_legado_publicado(db, DESDE)
    atividades = _dia(_client(), ONTEM).json()["energy_detail"]["activities"]
    assert atividades["activities"][0]["calorie_mode"] == energy_svc.DEFAULT_CALORIE_MODE


def test_dia_sem_meta_tem_tudo_nulo(db):
    detalhe = _dia(_client(), HOJE - timedelta(days=200)).json()["energy_detail"]
    assert detalhe == {"breakdown": None, "activities": None, "energy": None}


def test_diario_nao_cai_sem_a_107_nem_a_106(db):
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 300.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    _plano_legado_publicado(db, DESDE)
    db.sync_runs_table = False
    db.plan_energy_columns = False
    resposta = _dia(_client(), ONTEM)
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["energy_detail"]["activities"]["confirmed"] is True


def test_falha_na_explicacao_nao_derruba_o_diario(db, monkeypatch, caplog):
    _historico(db)
    _plano_legado_publicado(db)

    def quebra(*args, **kwargs):
        raise RuntimeError("valor-sigiloso")

    monkeypatch.setattr(days_mod, "read_stored_tdee", quebra)
    monkeypatch.setattr(days_mod, "load_day_energy", quebra)
    resposta = _dia(_client())
    assert resposta.status_code == 200
    detalhe = resposta.json()["energy_detail"]
    assert detalhe["breakdown"] is None and detalhe["activities"] is None
    assert "RuntimeError" in caplog.text and "valor-sigiloso" not in caplog.text


def _contando(db):
    """Cursor que anota cada SELECT/escrita (so o que `energy_detail` emite, sem a materializacao da meta)."""
    consultas: list[str] = []
    cursor = db.conn().cursor()
    original = cursor.execute
    cursor.execute = lambda sql, params=None: (consultas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
    return cursor, consultas


def test_explicacao_e_so_leitura_e_traz_as_atividades_do_historico(db):
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 300.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    _plano_adaptativo_publicado(db, desde=DESDE)
    meta = _dia(_client(), ONTEM).json()["target"]
    db.writes.clear()
    cursor, consultas = _contando(db)
    detalhe = days_mod.energy_detail(cursor, ALUNO, ONTEM, HOJE, meta)
    assert detalhe["breakdown"] is not None and detalhe["activities"]["counted_activities"] == 1
    assert db.writes == []  # so leitura
    assert consultas.count(svc._STORED_TDEE_SQL) == 1  # composicao: 1 SELECT
    assert consultas.count(energy_svc.SHADOW_ACTIVITIES_SQL) == 1  # atividades: so a carga do historico
    assert consultas.count(energy_svc.SYNC_RUNS_SQL) == 1


@pytest.mark.parametrize("modo_do_plano", ["gross", "active"])
def test_paridade_o_selo_do_diario_e_o_do_historico_em_varios_dias(db, modo_do_plano):
    """`energy_detail.activities` == o `DayActivity` de `compute_history` para o mesmo dia (confirmacao, origem, kcal)."""
    _historico(db)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=20), "Run", 200.0, start=datetime(2026, 10, 16, 7, 0),
                         synced_at=datetime(2026, 10, 17, 9, 0))
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=3), "WeightTraining", 400.0, start=datetime(2026, 11, 2, 18, 0),
                         elapsed=3600, synced_at=SYNC_UTC)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 250.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    # Medida POSTERIOR aos dias passados lidos (balanca com TMB propria ontem): a TMB recente do desconto do repouso muda
    # com a serie ate HOJE; o diario de um dia passado tem de usar a mesma serie que o historico da meta.
    db.composition.append({"id": 9001, "user_id": ALUNO, "local_date": ONTEM, "weight_kg": None, "body_fat_percent": None,
                           "muscle_mass_kg": None, "fat_free_mass_kg": None, "bmr_kcal": 1234})
    _gravar(db, _execucao(finished_at=SYNC_UTC - timedelta(days=2)), _execucao())
    _plano_adaptativo_publicado(db, modo_do_plano, DESDE)
    series = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE).series
    historico = energy_svc.load_history(db.conn().cursor(), ALUNO, HOJE, series, modo_do_plano, FUSO)
    comparados = 0
    for item in historico.days:
        if item.day < DESDE:
            continue
        corpo = _dia(_client(), item.day).json()
        detalhe = corpo["energy_detail"]["activities"]
        assert detalhe is not None, item.day
        for chave in ("sync_status", "confirmation_source", "active_kcal", "confirmed", "counted_activities"):
            assert detalhe[chave] == item.to_dict()[chave], (item.day, chave)
        comparados += 1
    assert comparados >= 8


def test_dia_fora_do_horizonte_do_historico_nao_traz_atividades(db):
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 250.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    cursor = db.conn().cursor()
    series = svc.load_user_inputs(cursor, ALUNO, HOJE, HOJE, HOJE).series
    antigo, futuro = HOJE - timedelta(days=200), HOJE + timedelta(days=1)  # fora de `REPLAY_HISTORY_DAYS` terminando hoje
    assert energy_svc.load_day_activity(cursor, ALUNO, antigo, HOJE, series, timezone_name=FUSO) is None
    assert energy_svc.load_day_activity(cursor, ALUNO, futuro, HOJE, series, timezone_name=FUSO) is None
    assert energy_svc.load_day_activity(cursor, ALUNO, ONTEM, HOJE, series, timezone_name=FUSO) is not None


def test_execucao_perto_da_meia_noite_local_nao_confirma_o_dia_que_ainda_corre(db):
    """23:30 BRT do dia 05 = 02:30 UTC do dia 06: no fuso local ainda e dia 05 (o 05 segue aberto); a das 05:45 BRT
    do dia 06 (08:45 UTC) passa de 05:00 local e confirma o dia 05. A janela inclusiva cobre os dois dias."""
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=3), "Run", 150.0, start=datetime(2026, 11, 2, 7, 0),
                         synced_at=datetime(2026, 11, 3, 9, 0))
    noite = _execucao(finished_at=datetime(2026, 11, 6, 2, 30), inicio=HOJE - timedelta(days=30), fim=HOJE)
    manha = _execucao(finished_at=datetime(2026, 11, 6, 8, 45), inicio=HOJE - timedelta(days=30), fim=HOJE)
    locais = energy_svc.local_sync_runs(
        [ae.SyncRun(r["finished_at"], r["window_start"], r["window_end"]) for r in (noite, manha)], FUSO
    )
    assert [r.finished_at for r in locais] == [datetime(2026, 11, 5, 23, 30), datetime(2026, 11, 6, 5, 45)]
    primeiro = HOJE - timedelta(days=30)
    so_noite = ae.summarize_days([], 2000.0, [HOJE, ONTEM], datetime(2026, 11, 5, 23, 30), HOJE - timedelta(days=3),
                                 runs=(locais[0],))
    assert [d.confirmed for d in so_noite] == [False, True]  # 05 aberto na ultima execucao; 04 confirmado
    ambas = ae.summarize_days([], 2000.0, [HOJE, ONTEM], datetime(2026, 11, 6, 5, 45), HOJE - timedelta(days=3),
                              runs=locais)
    assert [d.confirmed for d in ambas] == [True, True]  # 05:45 local passa de 05:00 do dia seguinte: confirma o dia 05
    assert primeiro < HOJE


def test_sem_intervals_o_dia_nao_le_atividades_nem_execucoes(db):
    _historico(db)
    _plano_legado_publicado(db)
    _dia(_client())
    consultas: list[str] = []
    classe = type(db.conn().cursor())
    original = classe.execute

    def contar(self, sql, params=None):
        consultas.append(sql)
        return original(self, sql, params)

    with mock.patch.object(classe, "execute", contar):
        assert _dia(_client()).status_code == 200
    assert energy_svc.SHADOW_ACTIVITIES_SQL not in consultas and energy_svc.SYNC_RUNS_SQL not in consultas
    assert consultas.count(svc._STORED_TDEE_SQL) == 1 and consultas.count(energy_svc.INTERVALS_SYNC_SQL) == 1


def test_escopo_por_usuario_comum_e_admin_vendo_outro(db):
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 300.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    db.add_intervals_row(OUTRO, ONTEM, "WeightTraining", 999.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    _plano_legado_publicado(db, DESDE)
    proprio = _client("regular", ALUNO).get(f"/nutrition/days/{ONTEM.isoformat()}").json()["energy_detail"]["activities"]
    assert proprio["counted_activities"] == 1 and proprio["activities"][0]["kind"] != "strength"
    visto_pelo_admin = _dia(_client(), ONTEM).json()["energy_detail"]["activities"]
    assert visto_pelo_admin["activities"] == proprio["activities"]
    assert _client("regular", OUTRO).get(f"/nutrition/days/{ONTEM.isoformat()}?user_id={ALUNO}").status_code == 403
    do_outro = _client("regular", OUTRO).get(f"/nutrition/days/{ONTEM.isoformat()}").json()["energy_detail"]
    assert do_outro["breakdown"] is None  # sem meta propria: nada do ALUNO vaza


# --- D1: me/plan --------------------------------------------------------------------------------------------------


def test_me_plan_traz_o_retrato_de_hoje_no_adaptativo(db):
    _historico(db)
    _plano_adaptativo_publicado(db)
    resposta = _client("regular", ALUNO).get("/nutrition/me/plan")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["energy"] is not None and corpo["energy"] == corpo["today_target"]["energy"]


def test_me_plan_no_legado_traz_energy_nulo(db):
    _historico(db)
    _plano_legado_publicado(db)
    resposta = _client("regular", ALUNO).get("/nutrition/me/plan")
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["energy"] is None


def _total_de_statements(db, dia: date) -> int:
    classe = type(db.conn().cursor())
    original = classe.execute
    consultas: list[str] = []

    def contar(self, sql, params=None):
        consultas.append(sql)
        return original(self, sql, params)

    with mock.patch.object(classe, "execute", contar):
        assert _dia(_client(), dia).status_code == 200
    return len(consultas)


def test_custo_do_dia_com_intervals_sobre_o_dia_sem_intervals_fica_no_teto(db):
    """Medida: quantos SELECTs o Intervals ACRESCENTA ao `GET /nutrition/days/{date}` (perfil e medidas reaproveitados da
    rota; sobram so as leituras do proprio historico)."""
    _historico(db)
    _plano_adaptativo_publicado(db, desde=DESDE)
    _dia(_client(), ONTEM)
    _dia(_client(), ONTEM)  # aquece (meta gravada, deteccao de tabelas cacheada)
    sem_intervals = _total_de_statements(db, ONTEM)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 300.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    _dia(_client(), ONTEM)
    com_intervals = _total_de_statements(db, ONTEM)
    acrescimo = com_intervals - sem_intervals
    print(f"\nstatements do dia: sem Intervals={sem_intervals} com Intervals={com_intervals} (+{acrescimo})")
    assert acrescimo <= 7


def test_dia_passado_usa_a_serie_ate_hoje_como_o_historico_da_meta(db):
    """Medida posterior ao dia (TMB da balanca ontem): a TMB recente do desconto do repouso vem da serie ate HOJE."""
    _historico(db)
    dia = HOJE - timedelta(days=4)
    db.add_intervals_row(ALUNO, dia, "WeightTraining", 500.0, start=datetime.combine(dia, datetime.min.time()) + timedelta(hours=18),
                         elapsed=3600, synced_at=SYNC_UTC)
    db.composition.append({"id": 9001, "user_id": ALUNO, "local_date": ONTEM, "weight_kg": Decimal("120"), "body_fat_percent": None,
                           "muscle_mass_kg": None, "fat_free_mass_kg": None, "bmr_kcal": 3000})
    _gravar(db, _execucao(finished_at=SYNC_UTC - timedelta(days=6)), _execucao())
    cursor = db.conn().cursor()
    ate_hoje = svc.load_user_inputs(cursor, ALUNO, HOJE, HOJE, HOJE)
    esperado = next(d for d in energy_svc.load_history(cursor, ALUNO, HOJE, ate_hoje.series, timezone_name=FUSO).days
                    if d.day == dia)
    da_rota = svc.load_user_inputs(cursor, ALUNO, dia, max(dia, HOJE), HOJE)  # o que a rota do dia carrega: serie ate hoje
    meta = {"energy": None, "bmr_kcal": None, "tdee_basis": "classic", "extra_activity": {"kcal": 0}}
    detalhe = days_mod.energy_detail(cursor, ALUNO, dia, HOJE, meta, da_rota)["activities"]
    assert detalhe["active_kcal"] == esperado.to_dict()["active_kcal"] and detalhe["confirmed"] is esperado.confirmed
    sem_a_posterior = svc.load_user_inputs(cursor, ALUNO, dia, dia, HOJE).series
    errado = energy_svc.load_history(cursor, ALUNO, HOJE, sem_a_posterior, timezone_name=FUSO)
    assert next(d for d in errado.days if d.day == dia).to_dict()["active_kcal"] != detalhe["active_kcal"]  # o teste discrimina
