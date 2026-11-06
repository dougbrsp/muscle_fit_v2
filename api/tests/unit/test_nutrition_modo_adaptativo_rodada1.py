# ruff: noqa: F811
"""Rodada de correção 1 do Lote C (serviço): falha transitória do histórico, fallback sem a migração 106, prévia gravada
na publicação (teto, tamanho real, gravação protegida), uma carga de histórico por prévia e `energy_params` nulo no padrão."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

import pytest

from app import nutrition_plans as plans_mod
from app import nutrition_schema as schema
from app import nutrition_targets_service as svc
from app.domain.nutrition.daily_target import TargetWindow
from tests.unit.test_nutrition_modo_adaptativo_servico import (  # noqa: F401 - fixtures e helpers do arquivo irmão
    ALUNO,
    HOJE,
    _adaptativo,
    _client,
    _db_com_plano_adaptativo,
    _historico,
    _metas_do_aluno,
    _publicar,
    _refeicao,
    _rascunho,
    db,
)


@pytest.fixture
def banco(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    schema.reset_nutrition_tables_cache()
    return _db_com_plano_adaptativo()


def _quebra(*args, **kwargs):
    raise RuntimeError("segredo=1")


def _janela(de: date, ate: date) -> TargetWindow:
    return TargetWindow(date_from=de, date_to=ate, create_until=ate)


# --- falha transitória do histórico -------------------------------------------------------------------------------


def test_falha_do_historico_com_meta_gravada_nao_regrava_nada(banco, monkeypatch, caplog):
    ontem = HOJE - timedelta(days=1)
    svc.ensure_daily_targets(banco.conn(), ALUNO, _janela(ontem, HOJE), today=HOJE)
    antes = {dia: dict(linha) for dia, linha in _metas_do_aluno(banco).items()}
    assert antes[HOJE]["inputs_json"]["tdee_basis"] == "adaptive"
    _refeicao(banco, ALUNO, HOJE - timedelta(days=2), energy_kcal=500.0)  # com histórico sadio isto mudaria a meta de ontem
    monkeypatch.setattr(svc, "load_history", _quebra)
    banco.writes.clear()
    with caplog.at_level(logging.WARNING):
        svc.ensure_daily_targets(banco.conn(), ALUNO, _janela(ontem, HOJE), today=HOJE)
    assert banco.writes == []  # 0 escritas
    assert {dia: dict(linha) for dia, linha in _metas_do_aluno(banco).items()} == antes  # linhas idênticas
    assert "RuntimeError" in caplog.text and "segredo" not in caplog.text


def test_falha_do_historico_sem_meta_grava_a_estimativa_inicial_e_a_recuperacao_corrige(banco, monkeypatch):
    monkeypatch.setattr(svc, "load_history", _quebra)
    linhas = svc.ensure_daily_targets(banco.conn(), ALUNO, _janela(HOJE, HOJE), today=HOJE)
    assert linhas and linhas[0]["tdee_basis"] == "adaptive_bootstrap"
    assert float(banco.targets[(ALUNO, HOJE)]["tdee_kcal"]) > 0
    marcada = banco.targets[(ALUNO, HOJE)]["inputs_json"]["energy"]
    assert "history_unavailable" in marcada["data_quality_flags"]  # a estimativa inicial sai MARCADA
    monkeypatch.undo()  # o histórico volta
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    recuperadas = svc.ensure_daily_targets(banco.conn(), ALUNO, _janela(HOJE, HOJE), today=HOJE)
    assert recuperadas[0]["tdee_basis"] == "adaptive"  # a meta se corrige quando o histórico volta
    assert "history_unavailable" not in banco.targets[(ALUNO, HOJE)]["inputs_json"]["energy"]["data_quality_flags"]


def test_entradas_registram_o_modo_que_falhou_e_a_cobertura(banco, monkeypatch):
    settings = svc.settings_from_row(next(iter(banco.plans.values())))
    monkeypatch.setattr(svc, "load_history", _quebra)
    entradas = svc.load_user_inputs(banco.conn().cursor(), ALUNO, HOJE, HOJE, HOJE, [settings])
    assert entradas.energy == {} and entradas.energy_failed == frozenset({("gross", "auto")})
    assert entradas.covers([settings])  # a falha registrada conta como "tentado": não recarrega em loop
    vazias = svc.UserInputs()
    assert not vazias.covers([settings]) and vazias.covers([])


def test_dashboard_mostra_error_e_nao_no_data_quando_a_leitura_falha(db, monkeypatch):
    _historico(db)
    admin = _client()
    assert _publicar(admin, _adaptativo(admin)["id"]).status_code == 200
    monkeypatch.setattr(svc, "load_history", _quebra)
    bloco = _client("regular", ALUNO).get("/nutrition/dashboard").json()["adaptive_energy"]
    assert bloco["available"] is False and bloco["applied"] is True
    assert bloco["unavailable_reason"] == "error" and bloco["unavailable_reason_label"]


# --- fallback sem a migração 106 ----------------------------------------------------------------------------------


def test_sem_a_106_a_leitura_dos_planos_publicados_usa_a_lista_sem_a_coluna(db):
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=5), published_at=datetime(2026, 10, 31, 12))
    consultas: list[str] = []

    def ler(com_coluna: bool):
        db.plan_energy_columns = com_coluna
        schema.reset_nutrition_tables_cache()
        cursor = db.conn().cursor()
        original = cursor.execute
        cursor.execute = lambda sql, params=None: (consultas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
        return svc.load_published_plans(cursor, ALUNO)

    sem = ler(False)
    assert svc._PUBLISHED_PLANS_WITHOUT_ENERGY_SQL in consultas and svc._PUBLISHED_PLANS_SQL not in consultas
    assert "energy_params_json" not in svc._PUBLISHED_PLANS_WITHOUT_ENERGY_SQL and len(sem.refs) == 1
    assert sem.settings[next(iter(sem.settings))].energy_params.is_default  # legado segue como hoje
    consultas.clear()
    com = ler(True)
    assert svc._PUBLISHED_PLANS_SQL in consultas and len(com.refs) == 1


def test_sem_a_106_diario_e_metas_continuam_e_as_rotas_de_plano_respondem_503(db):
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=5), published_at=datetime(2026, 10, 31, 12))
    db.add_body(ALUNO, HOJE - timedelta(days=3), weight=80.0, body_fat=20.0)
    db.plan_energy_columns = False
    schema.reset_nutrition_tables_cache()
    aluno = _client("regular", ALUNO)
    dia = aluno.get(f"/nutrition/days/{HOJE.isoformat()}")
    assert dia.status_code == 200, dia.text  # o diário não cai por falta da coluna
    assert dia.json()["target"]["kcal"] > 0
    assert aluno.get("/nutrition/dashboard").status_code == 200
    plano = aluno.get("/nutrition/me/plan")
    assert plano.status_code == 503 and plano.json()["detail"]["error"] == "nutrition_plan_migration_pending"
    assert plano.json()["detail"]["migration"] == "106"
    db.plan_energy_columns = True
    schema.reset_nutrition_tables_cache()
    assert aluno.get("/nutrition/me/plan").status_code == 200  # "não existe" nunca fica em cache


# --- prévia gravada na publicação ---------------------------------------------------------------------------------


MODOS = {
    "explicit_training": {},
    "included_in_factor": {},
    "auto_intervals": {"activity_level": "light"},
    "adaptive_balance": {"energy_params": {"watch_calorie_mode": "gross"}},
}


def test_tamanho_real_da_previa_nos_quatro_modos_cabe_folgado_no_teto(db, capsys):
    _historico(db)
    admin = _client()
    tamanhos = {}
    for modo, extra in MODOS.items():
        plano = _rascunho(admin, tdee_mode=modo, **extra)
        resposta = _publicar(admin, plano["id"], esperado=next(
            (r["id"] for r in db.plans.values() if r["user_id"] == ALUNO and r["status"] == "published" and r["superseded_at"] is None),
            None))
        assert resposta.status_code == 200, (modo, resposta.text)
        gravada = db.plans[plano["id"]]["published_preview_json"]
        assert gravada is not None, modo
        tamanhos[modo] = len(gravada.encode("utf-8"))
    print("tamanho da prévia gravada (bytes):", tamanhos)
    assert all(t < plans_mod.PUBLISHED_PREVIEW_MAX_BYTES // 4 for t in tamanhos.values()), tamanhos


def test_previa_acima_do_teto_grava_null_registra_so_o_tamanho_e_publica(db, monkeypatch, caplog):
    _historico(db)
    monkeypatch.setattr(plans_mod, "PUBLISHED_PREVIEW_MAX_BYTES", 10)
    admin = _client()
    plano = _rascunho(admin)
    with caplog.at_level(logging.WARNING):
        resposta = _publicar(admin, plano["id"])
    assert resposta.status_code == 200, resposta.text
    assert db.plans[plano["id"]]["published_preview_json"] is None
    assert "published_preview_too_large" in caplog.text and "bytes=" in caplog.text and '"target"' not in caplog.text


def test_falha_ao_gravar_a_previa_nao_derruba_a_publicacao(db, monkeypatch, caplog):
    _historico(db)
    from tests.support.nutrition_fake import FakeCursor

    original = FakeCursor.execute

    def execute(self, sql, params=None):
        if sql == plans_mod._PUBLISHED_PREVIEW_SQL:
            raise RuntimeError("segredo=2")
        return original(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", execute)
    admin = _client()
    plano = _rascunho(admin)
    with caplog.at_level(logging.WARNING):
        resposta = _publicar(admin, plano["id"])
    assert resposta.status_code == 200, resposta.text
    assert db.plans[plano["id"]]["status"] == "published" and db.plans[plano["id"]]["published_preview_json"] is None
    assert "published_preview_write_failed" in caplog.text and "segredo" not in caplog.text


# --- uma carga de histórico por prévia -----------------------------------------------------------------------------


def _contar_cargas(monkeypatch) -> list[str]:
    cargas: list[str] = []
    original = svc.load_history

    def contado(cur, user_id, today, series, calorie_mode="unknown", *args, **kwargs):
        cargas.append(calorie_mode)
        return original(cur, user_id, today, series, calorie_mode, *args, **kwargs)

    monkeypatch.setattr(svc, "load_history", contado)
    return cargas


def test_previa_do_adaptativo_carrega_o_historico_uma_vez(db, monkeypatch):
    _historico(db)
    admin = _client()
    plano = _adaptativo(admin)
    cargas = _contar_cargas(monkeypatch)
    assert admin.post(f"/nutrition/plans/{plano['id']}/preview").status_code == 200
    assert cargas == ["gross"]  # semana + gasto do treino passado compartilham UMA carga
    cargas.clear()
    corpo = {"user_id": ALUNO, "strategy": "maintenance", "tdee_mode": "adaptive_balance"}
    assert admin.post("/nutrition/plan-preview", json=corpo).status_code == 200
    assert len(cargas) == 1


def test_publicar_o_adaptativo_carrega_o_historico_o_minimo(db, monkeypatch):
    _historico(db)
    admin = _client()
    plano = _adaptativo(admin)
    cargas = _contar_cargas(monkeypatch)
    assert _publicar(admin, plano["id"]).status_code == 200
    print("cargas de histórico na publicação do adaptativo:", len(cargas))
    assert len(cargas) <= 3  # checagens do plano + janela de publicação + prévia gravada


def test_energy_params_e_nulo_no_padrao_e_aparece_fora_dele(db):
    admin = _client()
    assert _rascunho(admin)["energy_params"] is None
    padrao = _rascunho(admin, tdee_mode="adaptive_balance", energy_params={"watch_calorie_mode": "unknown"})
    assert padrao["energy_params"] is None and db.plans[padrao["id"]]["energy_params_json"] is None
    ativo = _adaptativo(admin, "active")
    assert ativo["energy_params"] == {"watch_calorie_mode": "active"}
    assert json.loads(db.plans[ativo["id"]]["energy_params_json"]) == {"watch_calorie_mode": "active"}


# --- TMB do histórico = fórmula do plano em jogo --------------------------------------------------------------------


def test_historico_usa_a_formula_da_tmb_do_plano_e_a_chave_separa_formulas(db):
    _historico(db)  # com %G: a fórmula automática escolhe Katch-McArdle; o plano pode fixar Mifflin
    admin = _client()
    auto = svc.settings_from_row(db.plans[_adaptativo(admin)["id"]])
    mifflin = svc.settings_from_row(db.plans[_adaptativo(admin, bmr_formula="mifflin_st_jeor")["id"]])
    assert svc.energy_key(auto) == ("gross", "auto") and svc.energy_key(mifflin) == ("gross", "mifflin_st_jeor")
    entradas = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE, [auto, mifflin])
    assert set(entradas.energy) == {("gross", "auto"), ("gross", "mifflin_st_jeor")}
    tmb_auto = entradas.energy[("gross", "auto")].states[-1].bmr_kcal
    tmb_mifflin = entradas.energy[("gross", "mifflin_st_jeor")].states[-1].bmr_kcal
    assert tmb_auto != tmb_mifflin  # a TMB do dia sem gravada vem da fórmula do plano, a mesma da estimativa inicial


# --- rodada 2: troca de plano com o histórico falhando, backfill, dashboard e leituras duplas --------------------------


def _sinalizadas(db, plano_id: int) -> list[date]:
    """Dias com meta do plano dado e a flag `history_unavailable` no retrato."""
    return sorted(dia for dia, m in _metas_do_aluno(db).items()
                  if m["plan_id"] == plano_id and "history_unavailable" in (m["inputs_json"].get("energy") or {}).get("data_quality_flags", []))


def test_publicar_adaptativo_sobre_o_classico_com_o_historico_falhando_nao_deixa_meta_com_o_plano_antigo(db, monkeypatch):
    _historico(db)
    admin = _client()
    classico = _rascunho(admin)
    assert _publicar(admin, classico["id"]).status_code == 200
    antigas = {dia: m for dia, m in _metas_do_aluno(db).items()}
    assert antigas and all(m["plan_id"] == classico["id"] for m in antigas.values())
    adaptativo = _adaptativo(admin)
    monkeypatch.setattr(svc, "load_history", _quebra)
    resposta = _publicar(admin, adaptativo["id"], esperado=classico["id"])
    assert resposta.status_code == 200, resposta.text
    metas = _metas_do_aluno(db)
    nao_congeladas = [dia for dia in metas if dia >= HOJE - timedelta(days=1)]
    assert nao_congeladas and all(metas[dia]["plan_id"] == adaptativo["id"] for dia in nao_congeladas)  # nenhuma ficou no plano antigo
    assert _sinalizadas(db, adaptativo["id"]) == sorted(dia for dia in metas if metas[dia]["plan_id"] == adaptativo["id"])


def test_republicar_adaptativo_com_outros_parametros_e_historico_falhando_regrava_com_o_plano_novo(db, monkeypatch):
    _historico(db)
    admin = _client()
    primeiro = _adaptativo(admin, "gross")
    assert _publicar(admin, primeiro["id"]).status_code == 200
    novo = _adaptativo(admin, "active")  # outros parâmetros => outro plano (outra chave de histórico)
    monkeypatch.setattr(svc, "load_history", _quebra)
    assert _publicar(admin, novo["id"], esperado=primeiro["id"]).status_code == 200
    metas = _metas_do_aluno(db)
    assert metas[HOJE]["plan_id"] == novo["id"] and _sinalizadas(db, novo["id"])
    assert all(m["plan_id"] == novo["id"] for dia, m in metas.items() if dia >= HOJE)


def test_backfill_de_dia_congelado_sem_meta_nao_grava_a_estimativa_inicial_quando_o_historico_falha(banco, monkeypatch):
    antigo = HOJE - timedelta(days=6)
    monkeypatch.setattr(svc, "load_history", _quebra)
    banco.writes.clear()
    resultado = svc.ensure_diary_targets(banco.conn(), ALUNO, [antigo], today=HOJE)
    assert banco.writes == [] and (ALUNO, antigo) not in banco.targets and resultado.rows[antigo] is None
    monkeypatch.undo()
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    recuperado = svc.ensure_diary_targets(banco.conn(), ALUNO, [antigo], today=HOJE)  # a próxima leitura tenta de novo
    assert recuperado.rows[antigo] is not None
    assert "history_unavailable" not in (banco.targets[(ALUNO, antigo)]["inputs_json"]["energy"]["data_quality_flags"])


def _gravar_consultas(monkeypatch) -> list[str]:
    from tests.support.nutrition_fake import FakeCursor

    consultas: list[str] = []
    original = FakeCursor.execute

    def execute(self, sql, params=None):
        consultas.append(sql)
        return original(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", execute)
    return consultas


def test_dashboard_so_carrega_historico_para_o_plano_vigente_nao_para_adaptativo_antigo_substituido(db, monkeypatch):
    _historico(db)
    adaptativo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=40),
                             published_at=datetime(2026, 9, 26, 12), training_kcal_mode="adaptive_balance",
                             activity_level="light", energy_params_json=json.dumps({"watch_calorie_mode": "gross"}))
    db.plans[adaptativo]["superseded_at"] = datetime(2026, 10, 26, 3)
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10), published_at=datetime(2026, 10, 26, 12))
    cargas = _contar_cargas(monkeypatch)
    consultas = _gravar_consultas(monkeypatch)
    resposta = _client("regular", ALUNO).get("/nutrition/dashboard")
    assert resposta.status_code == 200, resposta.text
    assert cargas == []  # nenhuma carga de histórico pela meta (o plano vigente é clássico)
    from app import nutrition_energy_service as energia

    assert consultas.count(energia.TARGET_BMR_SQL) == 1  # só a leitura da sombra; antes eram 2 (meta do plano antigo + sombra)
    assert resposta.json()["adaptive_energy"]["applied"] is False


def test_semana_da_troca_de_plano_carrega_cada_chave_de_historico_uma_vez(db, monkeypatch):
    _historico(db)
    antigo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=20), published_at=datetime(2026, 10, 16, 12),
                         training_kcal_mode="adaptive_balance", activity_level="light",
                         energy_params_json=json.dumps({"watch_calorie_mode": "gross"}))
    svc.ensure_daily_targets(db.conn(), ALUNO, _janela(HOJE - timedelta(days=1), HOJE - timedelta(days=1)), today=HOJE - timedelta(days=1))
    db.plans[antigo]["superseded_at"] = datetime(2026, 11, 5, 3)
    db.add_plan(ALUNO, status="published", effective_from=HOJE, published_at=datetime(2026, 11, 5, 12),
                training_kcal_mode="adaptive_balance", activity_level="light",
                energy_params_json=json.dumps({"watch_calorie_mode": "active"}))
    cargas = _contar_cargas(monkeypatch)
    resposta = _client("regular", ALUNO).get("/nutrition/me/plan")
    assert resposta.status_code == 200, resposta.text
    assert sorted(cargas) == ["active", "gross"]  # uma por chave (antes: a do plano de hoje era lida duas vezes)
