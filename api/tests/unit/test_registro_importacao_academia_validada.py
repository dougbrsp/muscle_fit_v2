"""#284: a academia do corpo (id ou nome) na Importacao e no Registro precisa existir e
estar ativa (422 `gym_not_found`, antes do banco); treino sem academia com equipamento
explicito adota a academia do equipamento; equipamentos de academias diferentes no mesmo
treino dao 422; inexistente e de outra academia dao o MESMO codigo e mensagem."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import services, workout_sessions
from tests.support.fake_db import fake_transaction
from tests.unit import test_reimportacao_preserva_marca_e_objetivo as reimp

GYMS = {1: True, 2: True, 3: False}  # id -> ativa
EQUIP = {10: 1, 20: 2, 30: 3}  # gym_equipment.id -> gym_id
CODE = "equipment_not_in_session_gym"


class _Cur(reimp._Cur):
    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT id FROM gyms WHERE id"):
            self.calls.append((flat, params))
            self._row = {"id": params[0]} if GYMS.get(params[0]) else None
        elif flat.startswith("SELECT gym_id, is_active FROM gym_equipment"):
            self.calls.append((flat, params))
            self._row = {"gym_id": EQUIP[params[0]], "is_active": 1} if params[0] in EQUIP else None
        elif flat.startswith("SELECT ge.gym_id, ge.is_active FROM gym_equipment ge JOIN gyms g"):
            # equipamento de academia inativa nao e adotado (JOIN ... is_active = 1)
            assert "g.is_active = 1" in flat
            self.calls.append((flat, params))
            ok = params[0] in EQUIP and GYMS[EQUIP[params[0]]]
            self._row = {"gym_id": EQUIP[params[0]], "is_active": 1} if ok else None
        else:
            super().execute(sql, params)

    def writes(self, prefix):
        return [(s, p) for s, p in self.calls if s.startswith(prefix)]


@pytest.fixture(autouse=True)
def _coerente(monkeypatch):
    monkeypatch.setattr(workout_sessions, "is_pair_coherent", lambda *a, **k: True)


def _importar(monkeypatch, exercises, **corpo):
    cur = _Cur([])
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(services, "source_id", lambda conn, name, source_type="api": 1)
    monkeypatch.setattr(services, "resolve_mapping_id", lambda *a, **k: 11)
    monkeypatch.setattr(services, "resolve_equipment_id", lambda *a, **k: None)
    try:
        services.upsert_activity(
            {"external_id": "x", "local_date": "2026-11-01", "name": "T", "exercises": exercises, **corpo}, user_id=7
        )
    finally:
        _importar.cur = cur
    return cur


def _ex(seq, equipment_id=None):
    ex = reimp._ex(seq, f"Ex{seq}")
    ex["equipment_id"] = equipment_id
    return ex


def _codigo(exc):
    return exc.value.status_code, exc.value.detail["code"]


@pytest.mark.parametrize("corpo", [{"gym_id": 99}, {"gym_id": 3}])  # inexistente e inativa
def test_importacao_academia_invalida_422_sem_gravar(monkeypatch, corpo):
    with pytest.raises(HTTPException) as exc:
        _importar(monkeypatch, [_ex(1)], **corpo)
    assert _codigo(exc) == (422, "gym_not_found")
    assert not _importar.cur.writes("INSERT INTO activities")


def test_importacao_nome_de_academia_inexistente_422(monkeypatch):
    monkeypatch.setattr(services, "resolve_gym_id", lambda cur, *a: None)
    with pytest.raises(HTTPException) as exc:
        _importar(monkeypatch, [_ex(1)], gym_name="Nao existe")
    assert _codigo(exc) == (422, "gym_not_found")


def test_importacao_academia_ativa_passa(monkeypatch):
    cur = _importar(monkeypatch, [_ex(1)], gym_id=1)
    assert cur.writes("INSERT INTO activities")[0][1][4] == 1


def test_importacao_sem_academia_adota_a_do_equipamento(monkeypatch):
    cur = _importar(monkeypatch, [_ex(1, 10)])
    assert [p for _, p in cur.writes("UPDATE activities SET gym_id")] == [(1, 501)]


def test_importacao_equipamentos_de_academias_diferentes_422(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _importar(monkeypatch, [_ex(1, 10), _ex(2, 20)])
    assert _codigo(exc) == (422, CODE)


@pytest.mark.parametrize("gym", [None, 1])
def test_equipamento_inexistente_e_de_outra_academia_mesmo_codigo_e_mensagem(gym):
    erros = []
    for eq, g in ((555, gym), (20, 1)):
        if g is None and eq == 20:
            continue
        with pytest.raises(HTTPException) as exc:
            services.validate_explicit_equipment(_Cur([]), g, 11, eq)
        erros.append((_codigo(exc), exc.value.detail["message"]))
    assert len(set(erros)) == 1
    assert erros[0][0] == (422, CODE)
    assert erros[0][1] == "Equipamento não disponível nesta academia."


def test_equipamento_igual_ao_gravado_sem_academia_nao_adota_nem_revalida():
    cur = _Cur([])
    assert services.validate_explicit_equipment(cur, None, 11, 10, previous_equipment_id=10) is None
    assert not cur.calls


def test_sem_academia_devolve_a_do_equipamento():
    assert services.validate_explicit_equipment(_Cur([]), None, 11, 10) == 1


class _CurRegistro(_Cur):
    """Atividade 5 com a academia `act_gym`; exercicio 7 sem equipamento gravado."""

    def __init__(self, act_gym):
        super().__init__([])
        self.act_gym = act_gym

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT id, activity_id"):
            self.calls.append((flat, params))
            self._row = {"id": 7, "activity_id": 5, "original_name": "Supino", "mapping_id": 11, "equipment_id": None}
        elif flat.startswith("SELECT gym_id FROM activities"):
            self.calls.append((flat, params))
            self._row = {"gym_id": self.act_gym}

        elif "FROM exercise_sets" in flat:
            self._rows = []
        else:
            super().execute(sql, params)


class _Parada(Exception):
    pass


def _registrar(monkeypatch, cur, **corpo):
    def _parar(*a, **k):
        raise _Parada

    monkeypatch.setattr(services, "resolve_equipment_id", _parar)
    monkeypatch.setattr(services, "resolve_gym_id", lambda c, *a: None)
    services._update_exercise_log_tx(None, cur, 7, {"local_date": "2026-11-01", "sets": [], **corpo}, None)


def test_registro_academia_do_corpo_inexistente_ou_inativa_422(monkeypatch):
    for gym in (99, 3):
        cur = _CurRegistro(act_gym=1)
        with pytest.raises(HTTPException) as exc:
            _registrar(monkeypatch, cur, gym_id=gym)
        assert _codigo(exc) == (422, "gym_not_found")
        assert not cur.writes("UPDATE activities")


def test_registro_equipamento_valida_na_academia_do_corpo_nao_na_gravada(monkeypatch):
    # corpo troca para a academia 2 (ativa): equipamento da academia gravada (1) e recusado.
    with pytest.raises(HTTPException) as exc:
        _registrar(monkeypatch, _CurRegistro(act_gym=1), gym_id=2, equipment_id=10)
    assert _codigo(exc) == (422, CODE)


def test_registro_treino_sem_academia_adota_a_do_equipamento(monkeypatch):
    cur = _CurRegistro(act_gym=None)
    with pytest.raises(_Parada):  # para em resolve_equipment_id: a decisao ja foi tomada
        _registrar(monkeypatch, cur, equipment_id=10)
    # sem o UPDATE (a parada o precede), prova pelo retorno do helper que alimenta gym_id:
    assert services.validate_explicit_equipment(_Cur([]), None, 11, 10) == 1


# --- Rodada de correcao: academia ja gravada e sempre aceita; escape do LIKE ---


def test_resolve_academia_gravada_inativa_aceita_quando_corpo_repete_o_id():
    assert services.resolve_active_gym_id(_Cur([]), 3, None, current_gym_id=3) == 3


def test_resolve_mudanca_para_inativa_ou_inexistente_422_mesmo_com_academia_gravada():
    for gym in (3, 99):
        with pytest.raises(HTTPException) as exc:
            services.resolve_active_gym_id(_Cur([]), gym, None, current_gym_id=1)
        assert _codigo(exc) == (422, "gym_not_found")


def test_resolve_nome_sem_match_mantem_a_gravada_e_sem_gravada_422(monkeypatch):
    monkeypatch.setattr(services, "resolve_gym_id", lambda cur, *a: None)
    assert services.resolve_active_gym_id(_Cur([]), None, "Antiga", current_gym_id=3) == 3
    with pytest.raises(HTTPException) as exc:
        services.resolve_active_gym_id(_Cur([]), None, "Antiga")
    assert _codigo(exc) == (422, "gym_not_found")


def test_importacao_reenvio_com_academia_inativa_ja_gravada_passa(monkeypatch):
    class Cur(_Cur):
        def execute(self, sql, params=None):
            if " ".join(sql.split()).startswith("SELECT gym_id FROM activities"):
                self._row = {"gym_id": 3}
                return
            super().execute(sql, params)

    cur = Cur([])
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(services, "source_id", lambda conn, name, source_type="api": 1)
    monkeypatch.setattr(services, "resolve_mapping_id", lambda *a, **k: 11)
    monkeypatch.setattr(services, "resolve_equipment_id", lambda *a, **k: None)
    base = {"external_id": "x", "local_date": "2026-11-01", "name": "T", "exercises": [_ex(1)]}
    services.upsert_activity({**base, "gym_id": 3}, user_id=7)
    assert cur.writes("INSERT INTO activities")[0][1][4] == 3
    # corpo sem academia: a gravada e preservada
    services.upsert_activity(base, user_id=7)
    assert cur.writes("INSERT INTO activities")[1][1][4] == 3


def test_reimportacao_preserva_academia_e_exercicio_de_outra_academia_422(monkeypatch):
    class Cur(_Cur):
        def execute(self, sql, params=None):
            if " ".join(sql.split()).startswith("SELECT gym_id FROM activities"):
                self._row = {"gym_id": 1}
                return
            super().execute(sql, params)

    def rodar(exercises):
        cur = Cur([{"sequence": 1, "original_name": "Ex1", "equipment_id": 10, "form_practice": 0, "training_goal": None}])
        monkeypatch.setattr(services, "transaction", fake_transaction(cur))
        monkeypatch.setattr(services, "source_id", lambda conn, name, source_type="api": 1)
        monkeypatch.setattr(services, "resolve_mapping_id", lambda *a, **k: 11)
        monkeypatch.setattr(services, "resolve_equipment_id", lambda *a, **k: None)
        services.upsert_activity(
            {"external_id": "x", "local_date": "2026-11-01", "name": "T", "exercises": exercises}, user_id=7
        )
        return cur

    cur = rodar([_ex(1, 10)])  # equipamento inalterado (ja gravado)
    assert cur.writes("INSERT INTO activities")[0][1][4] == 1
    with pytest.raises(HTTPException) as exc:
        rodar([_ex(1, 10), _ex(2, 20)])  # 2o exercicio de outra academia
    assert _codigo(exc) == (422, CODE)


def test_equipamento_de_academia_inativa_sem_academia_no_treino_422_unificado():
    with pytest.raises(HTTPException) as exc:
        services.validate_explicit_equipment(_Cur([]), None, 11, 30)
    assert _codigo(exc) == (422, CODE)


def test_registro_academia_gravada_inativa_repetida_passa(monkeypatch):
    cur = _CurRegistro(act_gym=3)
    with pytest.raises(_Parada):  # passou do 422 e parou no equipamento
        _registrar(monkeypatch, cur, gym_id=3, equipment_id=30)


def test_resolve_gym_id_escapa_barra_percentual_e_underscore():
    class Cur:
        params = []

        def execute(self, sql, params=None):
            self.params.append(params)

        def fetchone(self):
            return None

    cur = Cur()
    services.resolve_gym_id(cur, None, "a\\b%c_d")  # texto: a\b%c_d
    assert cur.params[-1] == ("%a\\\\b\\%c\\_d%",)  # texto: %a\\b\%c\_d%
