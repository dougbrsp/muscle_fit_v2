"""M12 (migracao 052) com banco real: a Meta de Ativacao tem vigencia, e
editar o cadastro deixou de apagar o passado.

O que so o banco de verdade prova, e por isso esta aqui e nao no unitario:

1. o `UNIQUE (current_training_type_id, muscle_group_id)` RECUSA uma segunda
   meta vigente para o mesmo grupo - a invariante e do banco, nao da
   aplicacao, entao um INSERT cru tem de bater na trave;
2. o mesmo UNIQUE ACEITA quantas versoes encerradas se queira para esse par -
   sem isso nao haveria historico nenhum (era o `uq_ttmgt_type_group` antigo
   que proibia);
3. `PUT /training-types/{id}` faz a troca inteira num instante so: a versao
   antiga termina exatamente quando a nova comeca, sem janela em que o grupo
   fica sem meta;
4. a leitura por data devolve a meta DAQUELA epoca, e a leitura vigente
   continua devolvendo a de hoje.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import training_types
from app.security import Identity, get_current_identity, require_admin_or_pt
from app.training_type_targets import resolve_targets_at, resolve_targets_now


BASE = training_types.router.prefix


@pytest.fixture
def api(db_conn, catalog):
    # `training_types` nao esta em `CATALOG_TABLES` (o `catalog` nao a
    # escreve), entao um tipo deixado por outro arquivo sobreviveria ao
    # TRUNCATE de `muscle_groups` apontando para grupos que ja nao existem.
    # O CASCADE da FK leva as metas junto.
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM training_types")
    db_conn.commit()

    app = FastAPI()
    app.include_router(training_types.router)
    identidade = Identity(user_id=catalog.user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


@pytest.fixture
def grupos(catalog):
    return catalog.muscle_groups["Peitoral"], catalog.muscle_groups["Costas"]


def _payload(nome, targets):
    return {
        "name": nome,
        "description": None,
        "rag_folder": None,
        "is_active": True,
        "targets": [
            {
                "muscle_group_id": gid,
                "min_weekly_sets": minimo,
                "target_weekly_sets": alvo,
                "max_weekly_sets": maximo,
            }
            for gid, minimo, alvo, maximo in targets
        ],
    }


def _linhas(conn, training_type_id):
    # `commit()` antes de ler: a API escreve na PROPRIA transacao, e esta
    # conexao esta em REPEATABLE READ desde a primeira leitura dela. Sem
    # encerrar o snapshot, o teste leria o banco como ele era ANTES do PUT -
    # e passaria/falharia pelo motivo errado.
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT muscle_group_id, target_weekly_sets, effective_from, superseded_at "
            "FROM training_type_muscle_group_targets WHERE training_type_id = %s "
            "ORDER BY muscle_group_id ASC, id ASC",
            (training_type_id,),
        )
        return list(cur.fetchall() or [])


# ---------------------------------------------------------------------------
# 1. A invariante e do BANCO
# ---------------------------------------------------------------------------


def test_o_banco_recusa_uma_segunda_meta_vigente_para_o_mesmo_grupo(db_conn, api, grupos):
    from app.db_pymysql import DataApiError

    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]

    db_conn.commit()  # encerra o snapshot desta conexao: o INSERT abaixo
    # precisa enxergar a linha que a API acabou de gravar.
    with db_conn.cursor() as cur:
        with pytest.raises(DataApiError):
            cur.execute(
                "INSERT INTO training_type_muscle_group_targets "
                "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets) "
                "VALUES (%s, %s, 1, 2, 3)",
                (tipo, peitoral),
            )
    db_conn.rollback()


def test_o_banco_aceita_varias_versoes_ENCERRADAS_do_mesmo_grupo(db_conn, api, grupos):
    """O `uq_ttmgt_type_group` antigo proibia exatamente isto - e era por isso
    que o unico jeito de salvar era apagando. Duas linhas encerradas nao
    colidem porque `current_training_type_id` e NULL nas duas."""
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    with db_conn.cursor() as cur:
        for alvo in (10, 11):
            cur.execute(
                "INSERT INTO training_type_muscle_group_targets "
                "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, "
                " max_weekly_sets, effective_from, superseded_at) "
                "VALUES (%s, %s, 1, %s, 20, '2026-01-01 00:00:00', '2026-02-01 00:00:00')",
                (tipo, peitoral, alvo),
            )
    db_conn.commit()
    assert len(_linhas(db_conn, tipo)) == 3


# ---------------------------------------------------------------------------
# 2. Editar preserva o passado
# ---------------------------------------------------------------------------


def test_editar_a_meta_encerra_a_anterior_e_abre_a_nova_no_mesmo_instante(db_conn, api, grupos):
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    assert api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 8, 16, 22)])).status_code == 200

    linhas = _linhas(db_conn, tipo)
    assert len(linhas) == 2, "a versao antiga tem de continuar existindo"
    antiga, nova = linhas
    assert float(antiga["target_weekly_sets"]) == 12.0
    assert float(nova["target_weekly_sets"]) == 16.0
    # Intervalo semiaberto SEM buraco: o fim de uma e o inicio da outra.
    assert antiga["superseded_at"] is not None
    assert antiga["superseded_at"] == nova["effective_from"]
    assert nova["superseded_at"] is None


def test_a_linha_que_nasceu_com_o_cadastro_nao_tem_inicio_de_vigencia_inventado(db_conn, api, grupos):
    """A primeira versao escrita pela API TEM `effective_from` (o instante do
    POST). Quem fica com NULL sao so as linhas que a 052 ENCONTROU - o banco
    nao sabe desde quando elas valem, e fingir uma data deixaria todo o
    passado anterior a ela sem meta."""
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    assert _linhas(db_conn, tipo)[0]["effective_from"] is not None


def test_salvar_sem_mudar_a_meta_nao_cria_versao_nova(db_conn, api, grupos):
    """Corrigir o NOME do tipo de treino nao e uma troca de meta. Sem esta
    regra, o historico encheria de fronteiras que nunca aconteceram."""
    peitoral, costas = grupos
    tipo = api.post(
        BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18), (costas, 10, 14, 20)])
    ).json()["id"]
    antes = _linhas(db_conn, tipo)

    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia (2026)", [(peitoral, 8, 12, 18), (costas, 10, 14, 20)]))

    assert _linhas(db_conn, tipo) == antes


def test_remover_um_grupo_do_cadastro_encerra_a_meta_em_vez_de_apaga_la(db_conn, api, grupos):
    peitoral, costas = grupos
    tipo = api.post(
        BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18), (costas, 10, 14, 20)])
    ).json()["id"]
    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)]))

    de_costas = [linha for linha in _linhas(db_conn, tipo) if linha["muscle_group_id"] == costas]
    assert len(de_costas) == 1
    assert de_costas[0]["superseded_at"] is not None


# ---------------------------------------------------------------------------
# 3. O cadastro continua mostrando SO a meta de hoje
# ---------------------------------------------------------------------------


def test_o_GET_do_cadastro_so_lista_as_metas_vigentes(api, grupos):
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 8, 16, 22)]))

    targets = api.get(f"{BASE}/{tipo}").json()["targets"]
    assert [float(t["target_weekly_sets"]) for t in targets] == [16.0]


def test_a_listagem_conta_so_os_grupos_vigentes(api, grupos):
    """`muscle_groups_count` e o numero que a tela do admin mostra. Sem o
    filtro, ele passaria a contar o historico e cresceria a cada edicao."""
    peitoral, costas = grupos
    tipo = api.post(
        BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18), (costas, 10, 14, 20)])
    ).json()["id"]
    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 9, 13, 19)]))

    item = next(i for i in api.get(BASE).json()["items"] if i["id"] == tipo)
    assert int(item["muscle_groups_count"]) == 1


# ---------------------------------------------------------------------------
# 4. A resolucao por data
# ---------------------------------------------------------------------------


TROCA = datetime(2026, 6, 1, 8, 0, 0)
INICIO = datetime(2026, 1, 1, 8, 0, 0)


def _datar_a_troca(conn, training_type_id):
    """Coloca a troca de versao em uma data FIXA, em vez de depender de quando
    o POST e o PUT caíram no relogio.

    `effective_from`/`superseded_at` sao `DATETIME` (sem fracao de segundo),
    e os dois requests deste teste acontecem em milissegundos. Quando caem no
    MESMO segundo - o que acontece na maior parte das vezes, e aconteceu no
    CI -, a primeira versao nasce com `effective_from == superseded_at`: um
    intervalo de duracao ZERO, que nenhum instante resolve (e corretamente:
    ela de fato nao valeu tempo nenhum). O teste nao pode depender disso, e
    nada do que ele quer provar - a fronteira semiaberta - depende.

    Backdatar e a unica parte artificial: a fronteira em si e a que a API
    escreveu, so movida no tempo."""
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE training_type_muscle_group_targets SET effective_from = %s, superseded_at = %s "
            "WHERE training_type_id = %s AND superseded_at IS NOT NULL",
            (INICIO, TROCA, training_type_id),
        )
        cur.execute(
            "UPDATE training_type_muscle_group_targets SET effective_from = %s "
            "WHERE training_type_id = %s AND superseded_at IS NULL",
            (TROCA, training_type_id),
        )
    conn.commit()


def test_a_meta_da_epoca_e_a_de_hoje_sao_respostas_diferentes(db_conn, api, grupos):
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 8, 16, 22)]))
    _datar_a_troca(db_conn, tipo)

    # Um instante ANTES da troca: a meta antiga. No instante da troca (o fim e
    # EXCLUSIVO, o inicio e INCLUSIVO): a nova. Ja a de HOJE ignora a data.
    assert resolve_targets_at(tipo, TROCA - timedelta(seconds=1))[peitoral]["target"] == 12.0
    assert resolve_targets_at(tipo, TROCA)[peitoral]["target"] == 16.0
    assert resolve_targets_now(tipo)[peitoral]["target"] == 16.0


def test_duas_edicoes_no_mesmo_segundo_nao_deixam_duas_vigentes(db_conn, api, grupos):
    """O caso que derrubou este arquivo no CI, agora afirmado em vez de
    evitado: `DATETIME` nao tem fracao de segundo, entao dois PUT no mesmo
    segundo produzem uma versao de duracao ZERO.

    Isso e honesto - aquela versao nao valeu tempo nenhum -, e o que importa
    continua de pe: a vigente e UMA so (o UNIQUE garante), e e a ultima. A
    versao de duracao zero nao governa instante nenhum, que e exatamente o
    que `[effective_from, superseded_at)` com os dois iguais quer dizer."""
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 8, 16, 22)]))
    api.put(f"{BASE}/{tipo}", json=_payload("Hipertrofia", [(peitoral, 8, 20, 26)]))

    linhas = _linhas(db_conn, tipo)
    assert len(linhas) == 3
    assert len([linha for linha in linhas if linha["superseded_at"] is None]) == 1
    assert resolve_targets_now(tipo)[peitoral]["target"] == 20.0
    for linha in linhas:
        if linha["superseded_at"] is not None:
            # Fim EXCLUSIVO: o instante do fim ja pertence a versao seguinte.
            assert linha["effective_from"] <= linha["superseded_at"]


def test_uma_meta_sem_inicio_de_vigencia_vale_desde_sempre(db_conn, api, grupos):
    """O estado em que a 052 deixa as linhas que ja existiam."""
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE training_type_muscle_group_targets SET effective_from = NULL "
            "WHERE training_type_id = %s",
            (tipo,),
        )
    db_conn.commit()

    assert resolve_targets_at(tipo, datetime(2020, 1, 1, 12, 0, 0))[peitoral]["target"] == 12.0


def test_uma_data_anterior_a_primeira_versao_nao_devolve_meta_nenhuma(db_conn, api, grupos):
    """Cair na meta de hoje aqui seria voltar a medir o passado contra um
    objetivo que ainda nao existia - o defeito que o M12 corrige."""
    peitoral, _ = grupos
    tipo = api.post(BASE, json=_payload("Hipertrofia", [(peitoral, 8, 12, 18)])).json()["id"]
    assert resolve_targets_at(tipo, datetime(2020, 1, 1, 12, 0, 0)) == {}
