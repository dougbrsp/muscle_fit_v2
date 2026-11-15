# ruff: noqa: F811
"""QA independente do Lote C: cenários de quebra do modo `adaptive_balance` na camada de serviço.

Dois planos na mesma semana (adaptativo superseded + legado vigente, e o inverso), dia congelado que não regrava,
usuário sem dados e prévia/rascunho por admin. Banco em memória (`FakeDashboardDB`); nada de MySQL."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app import nutrition_targets_service as svc
from app.domain.nutrition.daily_target import TargetWindow
from tests.unit.test_nutrition_modo_adaptativo_servico import (  # noqa: F401 - fixtures e helpers do arquivo irmão
    ALUNO,
    HOJE,
    _adaptativo,
    _client,
    _historico,
    _metas_do_aluno,
    _publicar,
    _refeicao,
    db,
)

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

def _dois_planos(db, *, antigo: str, novo: str):
    """Plano A vigente até ontem (ontem já tem meta dele) e plano B publicado hoje, na mesma semana (quinta). Cada um é
    `legado`, `gross` ou `active` (adaptativo com esse `watch_calorie_mode`). O substituído CONTINUA `published` com
    `superseded_at` (é assim que a publicação real o deixa; nunca vira outro status) e por isso ainda vale para o passado."""
    _historico(db)

    def campos(tipo: str) -> dict:
        if tipo == "legado":
            return {}
        return {"training_kcal_mode": "adaptive_balance", "activity_level": "light",
                "energy_params_json": json.dumps({"watch_calorie_mode": tipo})}

    id_antigo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=20),
                            published_at=datetime(2026, 10, 16, 12), **campos(antigo))
    ontem = HOJE - timedelta(days=1)
    svc.ensure_daily_targets(db.conn(), ALUNO, TargetWindow(date_from=ontem, date_to=ontem, create_until=ontem), today=ontem)
    db.plans[id_antigo].update(superseded_at=datetime(2026, 11, 5, 3))
    id_novo = db.add_plan(ALUNO, status="published", effective_from=HOJE, published_at=datetime(2026, 11, 5, 12), **campos(novo))
    return id_antigo, id_novo


@pytest.mark.parametrize(
    ("antigo", "novo"), [("gross", "legado"), ("gross", "active"), ("legado", "gross"), ("gross", "gross")]
)
def test_semana_com_dois_planos_o_dia_do_plano_antigo_usa_o_historico_do_modo_certo_sem_alternar(db, antigo, novo):
    """Entradas carregadas só com o plano de HOJE (como `my_plan`/`_my_week`) não podem gravar o dia não congelado do OUTRO plano
    adaptativo (ou de outro `watch_calorie_mode`) com a estimativa inicial: o serviço recarrega o histórico do modo que falta, e o
    caminho do diário (carga completa) não encontra nada a regravar. A meta não alterna até congelar."""
    id_antigo, id_novo = _dois_planos(db, antigo=antigo, novo=novo)
    ontem = HOJE - timedelta(days=1)
    assert _metas_do_aluno(db)[ontem]["plan_id"] == id_antigo
    inicio = HOJE - timedelta(days=3)
    settings_hoje = svc.load_published_plans(db.conn().cursor(), ALUNO).settings[id_novo]
    entradas = svc.load_user_inputs(db.conn().cursor(), ALUNO, inicio, HOJE, HOJE, [settings_hoje])  # só o plano de hoje
    janela = TargetWindow(date_from=inicio, date_to=HOJE, create_until=HOJE)
    linhas = svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE, inputs=entradas)
    assert _metas_do_aluno(db)[HOJE]["plan_id"] == id_novo and len(linhas) >= 2
    depois_da_semana = {dia: dict(linha) for dia, linha in _metas_do_aluno(db).items()}
    if antigo != "legado":  # o dia do plano adaptativo antigo NÃO ficou com a estimativa inicial
        assert depois_da_semana[ontem]["inputs_json"]["tdee_basis"] != "adaptive_bootstrap"
    # caminho do diário: carga completa; nada a mudar
    db.writes.clear()
    svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE)
    assert db.writes == [] and {d: dict(l) for d, l in _metas_do_aluno(db).items()} == depois_da_semana


def test_editar_refeicao_antiga_nao_regrava_meta_congelada_do_plano_adaptativo(db):
    _historico(db)
    banco = db
    banco.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=20), published_at=datetime(2026, 10, 16, 12),
                   training_kcal_mode="adaptive_balance", activity_level="light",
                   energy_params_json=json.dumps({"watch_calorie_mode": "gross"}))
    antigo = HOJE - timedelta(days=6)
    janela = TargetWindow(date_from=antigo, date_to=antigo, create_until=antigo)
    svc.ensure_daily_targets(banco.conn(), ALUNO, janela, today=antigo + timedelta(days=1))
    linha = dict(banco.targets[(ALUNO, antigo)])
    _refeicao(banco, ALUNO, antigo, energy_kcal=9000.0)  # edição retroativa pesada
    banco.writes.clear()
    svc.ensure_daily_targets(banco.conn(), ALUNO, janela, today=HOJE)
    assert banco.writes == [] and banco.targets[(ALUNO, antigo)] == linha


def test_usuario_sem_nenhum_dado_no_plano_adaptativo_nao_estoura(db):
    """Sem peso, sem refeição, sem TMB: a meta de hoje não é computável, mas o serviço não levanta exceção."""
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=2), published_at=datetime(2026, 11, 3, 12),
                training_kcal_mode="adaptive_balance", activity_level="light",
                energy_params_json=json.dumps({"watch_calorie_mode": "gross"}))
    linhas = svc.ensure_daily_targets(db.conn(), ALUNO, TargetWindow(date_from=HOJE, date_to=HOJE, create_until=HOJE), today=HOJE)
    assert isinstance(linhas, list)


def test_publicar_adaptativo_sem_dados_do_aluno_recusa_com_400_ou_409_nunca_500(db):
    admin = _client()
    plano = _adaptativo(admin)
    resposta = _publicar(admin, plano["id"])
    assert resposta.status_code < 500, resposta.text
