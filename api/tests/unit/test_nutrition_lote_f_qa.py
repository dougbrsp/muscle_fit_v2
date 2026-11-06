"""QA independente do Lote F (nutrição adaptativa): fuso, bordas, desconfirmação, plano publicado pela rota,
sync sem a 107 e `energy_detail` (soma das parcelas, meta antiga, falha parcial, escopo).

Cenários pelo caminho real (plano publicado pela rota, `load_history`/`ensure_daily_targets`), não por estado atribuído.
Rodar: cd api && python -m pytest tests/unit/test_nutrition_lote_f_qa.py
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

import pytest

from app import intervals_store as istore
from app import nutrition_energy_service as energy_svc
from app import nutrition_targets_service as svc
from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition.activity_energy import SyncRun
from app.nutrition_targets_service import TargetWindow
from tests.unit.test_nutrition_energy_detail import (  # noqa: F401  (db é fixture)
    ALUNO, DESDE, FUSO, HOJE, ONTEM, OUTRO, SYNC_UTC, _client, _dia, _execucao, _gravar, _historico,
    _plano_adaptativo_publicado, _plano_legado_publicado, _primeiro_dia, db,
)

INICIO = date(2026, 9, 1)


def _atividade_recente(db):  # noqa: F811
    """Atividade 3 dias antes de HOJE: a trava de sequência (`MAX_CONFIRMED_REST_STREAK_DAYS`) vale também para dia coberto
    por execução; sem atividade recente os dias seguintes ficariam pendentes por sequência longa, não pelo que o teste mede."""
    dia = HOJE - timedelta(days=3)
    db.add_intervals_row(ALUNO, dia, "Run", 200.0, start=datetime.combine(dia, datetime.min.time()).replace(hour=7),
                         synced_at=datetime.combine(dia, datetime.min.time()).replace(hour=9))


def _hist(db, runs_utc):  # noqa: F811
    _historico(db)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=25), "Run", 200.0, start=datetime(2026, 10, 11, 7, 0),
                         synced_at=datetime(2026, 10, 12, 9, 0))
    _atividade_recente(db)
    _gravar(db, *runs_utc)
    series = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE).series
    h = energy_svc.load_history(db.conn().cursor(), ALUNO, HOJE, series, timezone_name=FUSO)
    return {d.day: d for d in h.days}


# --- (a) dia confirmado ---------------------------------------------------------------------------------------------


def test_execucao_as_02h_utc_e_23h_do_dia_anterior_em_brt_nao_confirma_o_dia_local():
    # 2026-11-05 02:00 UTC = 2026-11-04 23:00 BRT: o dia 04 ainda nem acabou para o aluno.
    fim_local = energy_svc.local_sync_runs(
        [SyncRun(datetime(2026, 11, 5, 2, 0), date(2026, 10, 1), date(2026, 11, 5))], FUSO)
    assert fim_local[0].finished_at == datetime(2026, 11, 4, 23, 0)
    primeira = (SyncRun(datetime(2026, 10, 1, 9, 0), date(2026, 9, 1), date(2026, 10, 1)),)
    runs = primeira + fim_local
    assert not ae.day_confirmed(date(2026, 11, 4), fim_local[0].finished_at, INICIO, runs)
    assert ae.day_confirmed(date(2026, 11, 3), fim_local[0].finished_at, INICIO, runs)  # 03 fecha 04/11 05:00 local


def test_fuso_pelo_caminho_real_dia_04_pendente_com_execucao_das_02h_utc(db):  # noqa: F811
    dias = _hist(db, [_execucao(finished_at=datetime(2026, 10, 20, 9, 0), inicio=HOJE - timedelta(days=30), fim=HOJE),
                      _execucao(finished_at=datetime(2026, 11, 5, 2, 0))])
    assert dias[ONTEM].confirmed is False  # 04/11 23:00 local < fim do dia + folga
    assert dias[HOJE - timedelta(days=2)].confirmed is True  # 03/11 coberto (fim 04/11 05:00 local <= 23:00)


def test_janela_inclusiva_nas_duas_bordas_e_dia_fora_nao_cobre():
    fim = datetime(2026, 10, 20, 12, 0)
    run = SyncRun(fim, date(2026, 10, 5), date(2026, 10, 15))
    ref = SyncRun(datetime(2026, 10, 1, 9, 0), date(2026, 9, 1), date(2026, 9, 2))
    f = lambda d: ae.day_confirmed(d, fim, INICIO, (ref, run))  # noqa: E731
    assert f(date(2026, 10, 5)) and f(date(2026, 10, 15))
    assert not f(date(2026, 10, 4)) and not f(date(2026, 10, 16))


def test_folga_inclusiva_no_instante_exato():
    d = date(2026, 10, 10)
    exato = datetime.combine(d + timedelta(days=1), datetime.min.time()) + timedelta(hours=ae.DAY_CONFIRMATION_LAG_HOURS)
    run = SyncRun(exato, date(2026, 10, 1), date(2026, 10, 12))
    ref = SyncRun(datetime(2026, 10, 1, 9, 0), date(2026, 9, 1), date(2026, 9, 2))  # primeira execução, antes do dia
    assert ae.day_confirmed(d, exato, INICIO, (ref, run))
    run2 = SyncRun(exato - timedelta(seconds=1), date(2026, 10, 1), date(2026, 10, 12))
    assert not ae.day_confirmed(d, exato, INICIO, (ref, run2))


def test_refresh_manual_de_janela_curta_nao_desconfirma_nem_confirma_o_resto():
    longa = SyncRun(datetime(2026, 10, 20, 9, 0), date(2026, 9, 20), date(2026, 10, 20))
    curta = SyncRun(datetime(2026, 10, 25, 9, 0), date(2026, 10, 23), date(2026, 10, 25))
    for d in (date(2026, 10, 1), date(2026, 10, 19)):  # cobertos só pela longa: seguem confirmados
        assert ae.day_confirmed(d, curta.finished_at, INICIO, (longa, curta))
    assert not ae.day_confirmed(date(2026, 10, 22), curta.finished_at, INICIO, (longa, curta))  # nenhum cobre
    assert ae.day_confirmed(date(2026, 10, 23), curta.finished_at, INICIO, (longa, curta))


def test_dia_confirmado_pela_longa_continua_apos_poda_da_primeira_execucao_so_muda_a_origem():
    d = date(2026, 10, 10)
    primeira = SyncRun(datetime(2026, 10, 5, 9, 0), date(2026, 9, 5), date(2026, 10, 5))
    antiga = SyncRun(datetime(2026, 10, 11, 9, 0), date(2026, 10, 1), date(2026, 10, 11))
    nova = SyncRun(datetime(2026, 11, 5, 6, 0), date(2026, 11, 3), date(2026, 11, 5))
    last = nova.finished_at
    com = ae.day_confirmation_source(d, last, INICIO, (primeira, antiga, nova))
    podada = ae.day_confirmation_source(d, last, INICIO, (nova,))  # a 107 podou a antiga: dia < primeira -> regra antiga
    assert com == ae.DAY_CONFIRMATION_SOURCE_RUN
    assert podada == ae.DAY_CONFIRMATION_SOURCE_LAST_SYNC  # nunca None por causa da poda
    assert 120 > 90  # retenção cobre o horizonte
    assert ae.SYNC_RUN_RETENTION_DAYS > 90


def test_usuario_sem_nenhuma_execucao_usa_regra_antiga_e_error_nao_conta(db):  # noqa: F811
    dias = _hist(db, [_execucao(status="error", finished_at=SYNC_UTC)])
    assert all(d.confirmation_source != ae.DAY_CONFIRMATION_SOURCE_RUN for d in dias.values())


def test_execucao_de_outro_usuario_nao_confirma_o_dia_do_aluno(db):  # noqa: F811
    dias = _hist(db, [_execucao(OUTRO, finished_at=SYNC_UTC), _execucao(OUTRO, finished_at=SYNC_UTC - timedelta(days=1))])
    assert dias[ONTEM].confirmed is False


# --- (b) efeito no plano adaptativo publicado pela rota -------------------------------------------------------------


def test_comecar_o_registro_nao_regrava_dia_congelado_e_so_o_aberto_pode_mudar(db):  # noqa: F811
    _historico(db)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=25), "Run", 200.0, start=datetime(2026, 10, 11, 7, 0),
                         synced_at=datetime(2026, 10, 12, 9, 0))
    _plano_adaptativo_publicado(db, "gross", DESDE)
    janela = TargetWindow(date_from=HOJE - timedelta(days=6), date_to=HOJE, create_until=HOJE)
    # primeira leitura "no passado": os dias ainda não congelaram e ganham meta; depois o tempo passa até HOJE
    svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE - timedelta(days=5))
    antes = {d: copy.deepcopy(l) for (u, d), l in db.targets.items() if u == ALUNO}
    assert antes
    _gravar(db, _execucao(finished_at=SYNC_UTC - timedelta(days=1)), _execucao())
    db.writes.clear()
    svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE)
    congelados = [d for d in antes if d <= HOJE - timedelta(days=3)]
    assert congelados
    for d in congelados:
        assert db.targets[(ALUNO, d)] == antes[d]
    gravados = [w for w in db.writes if any(isinstance(x, date) and x in congelados for x in (w if isinstance(w, (list, tuple)) else ()))]
    assert gravados == []


def test_publicacao_e_substituicao_pela_rota_mantem_a_confirmacao_pelo_registro(db):  # noqa: F811
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    _gravar(db, _execucao(finished_at=SYNC_UTC - timedelta(days=1)), _execucao())
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=25), "Run", 200.0, start=datetime(2026, 10, 11, 7, 0),
                         synced_at=datetime(2026, 10, 12, 9, 0))
    _atividade_recente(db)
    from tests.unit.test_nutrition_modo_adaptativo_servico import _publicar, _rascunho
    admin = _client()
    atual = max(db.plans)
    novo = _rascunho(admin, tdee_mode="auto_intervals", activity_level="light")
    assert _publicar(admin, novo["id"], atual).status_code == 200  # o anterior segue 'published' com superseded_at
    db.plans[novo["id"]]["effective_from"] = DESDE
    resp = _dia(_client(), ONTEM)
    assert resp.status_code == 200
    atv = resp.json()["energy_detail"]["activities"]
    assert atv is not None and atv["confirmed"] is True and atv["confirmation_source"] == "sync_run"


# --- (c) sync sem a tabela 107 --------------------------------------------------------------------------------------


class _CursorSemTabela:
    def __init__(self):
        self.sqls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        raise RuntimeError("Table 'intervals_sync_runs' doesn't exist")


class _ConnSemTabela:
    def __init__(self):
        self.cur = _CursorSemTabela()

    def cursor(self):
        return self.cur


def test_record_sync_run_sem_a_tabela_devolve_false_sem_levantar_e_sem_vazar_mensagem(caplog):
    conn = _ConnSemTabela()
    ok = istore.record_sync_run(conn, 2, started_at=datetime(2026, 11, 5, 9), finished_at=datetime(2026, 11, 5, 9, 1),
                                window_start=date(2026, 11, 1), window_end=date(2026, 11, 5), status="ok",
                                activities_count=3, source="scheduled")
    assert ok is False and len(conn.cur.sqls) == 1  # o INSERT falhou; a poda nem tenta
    assert "RuntimeError" in caplog.text and "doesn't exist" not in caplog.text


def test_record_sync_run_poda_com_o_corte_da_retencao_do_motor():
    execs = []

    class Cur(_CursorSemTabela):
        def execute(self, sql, params=None):
            execs.append((sql, params))

    class Conn:
        def cursor(self):
            return Cur()

    fim = datetime(2026, 11, 5, 9, 1)
    assert istore.record_sync_run(Conn(), 7, started_at=fim, finished_at=fim, window_start=date(2026, 11, 1),
                                  window_end=date(2026, 11, 5), status="ok", activities_count=0, source="manual")
    assert execs[0][1][0] == 7 and execs[1][1] == (7, fim - timedelta(days=ae.SYNC_RUN_RETENTION_DAYS))
    assert "DELETE" in execs[1][0] and "user_id = %s" in execs[1][0]


# --- (d) energy_detail ----------------------------------------------------------------------------------------------


def test_soma_das_parcelas_fecha_o_total_no_legado_e_no_adaptativo(db):  # noqa: F811
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    q = _dia(_client(), ONTEM).json()["energy_detail"]["breakdown"]
    assert sum(p["kcal"] for p in q["parts"]) == pytest.approx(q["total_kcal"], abs=1.0)


def test_meta_gravada_antiga_sem_chaves_novas_nao_derruba_o_diario(db):  # noqa: F811
    _historico(db)
    _plano_legado_publicado(db, DESDE)
    janela = TargetWindow(date_from=ONTEM, date_to=ONTEM, create_until=ONTEM)
    svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE)
    linha = db.targets[(ALUNO, ONTEM)]
    ij = linha.get("inputs_json") or {}
    for k in ("energy", "tdee_breakdown", "breakdown", "bmr_kcal", "activity_kcal"):
        ij.pop(k, None)
    linha["inputs_json"] = ij
    resp = _dia(_client(), ONTEM)
    assert resp.status_code == 200
    corpo = resp.json()
    assert "energy_detail" in corpo and corpo["target"] is not None


def test_admin_vendo_outro_usuario_nao_ve_execucoes_do_admin(db):  # noqa: F811
    _historico(db)
    db.add_intervals_row(ALUNO, ONTEM, "Run", 300.0, start=datetime(2026, 11, 4, 18, 0), synced_at=SYNC_UTC)
    _plano_legado_publicado(db, DESDE)
    _gravar(db, _execucao(1, finished_at=SYNC_UTC - timedelta(days=1)), _execucao(1))  # do admin, não do aluno
    atv = _dia(_client(), ONTEM).json()["energy_detail"]["activities"]
    assert atv is not None and atv["confirmation_source"] != "sync_run"
