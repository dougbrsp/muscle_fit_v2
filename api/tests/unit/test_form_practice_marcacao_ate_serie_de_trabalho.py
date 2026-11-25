"""Marcação de "Aprimoramento de técnica" feita/desfeita até a 1ª série de
TRABALHO (#213/#211, ADR 0017): dono em `domain/form_practice.py`, rota só chama.

Rodar: cd api && python -m pytest tests/unit/test_form_practice_marcacao_ate_serie_de_trabalho.py
"""

from __future__ import annotations

from app.domain import form_practice as fp
from tests.unit import test_client_key_sessao_idempotente as base
from tests.unit.test_form_practice_sessao import _FakeDB


class _MarkDB(_FakeDB):
    """+ contagem de séries de trabalho e o UPDATE da marca."""

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        p = tuple(params or ())
        self.flat_sql = getattr(self, "flat_sql", []) + [flat]
        if flat.startswith("SELECT form_practice FROM exercises WHERE id = %s FOR UPDATE"):
            # `fresh_mark`: a marca que outra transacao confirmou depois do nosso snapshot.
            row = next(e for e in self.exercises if e["id"] == p[0])
            self._result = {"form_practice": getattr(self, "fresh_mark", row["form_practice"])}
        elif flat.startswith("SELECT COUNT(*) AS n FROM exercise_sets es") and flat.endswith("FOR SHARE"):
            # `fresh_count`: o que so a leitura corrente enxerga.
            real = sum(1 for s in self.sets if s["exercise_id"] == p[0] and s["set_type"] in ("", "working"))
            self._result = {"n": max(real, getattr(self, "fresh_count", 0))}
        elif flat.startswith("SELECT COUNT(*) AS n FROM exercise_sets es"):
            self._result = {"n": sum(1 for s in self.sets if s["exercise_id"] == p[0] and s["set_type"] in ("", "working"))}
        elif flat.startswith("UPDATE exercises SET form_practice"):
            self.updates = getattr(self, "updates", 0) + 1
            next(e for e in self.exercises if e["id"] == p[1])["form_practice"] = p[0]
            self._result = None
        else:
            super().execute(sql, params)


def _db(monkeypatch):
    return base._install(monkeypatch, _MarkDB())


def _ex(**kw):
    return base._add_exercise(**kw)["exercise_id"]


def _set(ex, **kw):
    return base._add_set(ex, **kw)


def test_dono_travado_so_com_serie_de_trabalho():
    assert not fp.form_practice_locked(0) and not fp.form_practice_locked(None) and fp.form_practice_locked(1)
    assert fp.resolve_form_practice_before_work(True, False, 0) is True
    assert fp.resolve_form_practice_before_work(False, True, 0) is False
    assert fp.resolve_form_practice_before_work(None, True, 0) is True
    assert fp.resolve_form_practice_before_work(False, True, 2) is True  # travado: a gravada vale
    assert "es.set_type" in fp.sql_working_sets_recorded()


def test_marcar_depois_do_aquecimento_muda_e_1a_de_trabalho_ja_sai_neutra(monkeypatch):
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1")
    aq = _set(ex, set_type="warmup", exercise_form_practice=False)
    assert aq["form_practice"] is False and aq["form_practice_locked"] is False
    out = _set(ex, exercise_form_practice=True)
    assert db.exercises[0]["form_practice"] == 1
    assert out["form_practice"] is True and out["form_practice_locked"] is True
    assert out["intensity_status"] is None and out["next_set_load"] is None


def test_desmarcar_antes_da_1a_de_trabalho(monkeypatch):
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1", form_practice=True)
    out = _set(ex, exercise_form_practice=False)
    assert db.exercises[0]["form_practice"] == 0 and out["form_practice"] is False
    assert out["intensity_status"] is not None


def test_depois_da_1a_de_trabalho_o_pedido_e_ignorado(monkeypatch):
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1", form_practice=True)
    _set(ex)
    out = _set(ex, exercise_form_practice=False)
    assert db.exercises[0]["form_practice"] == 1 and out["form_practice"] is True
    assert out["form_practice_locked"] is True and getattr(db, "updates", 0) == 0


def test_campo_ausente_nao_muda_e_reenvio_e_idempotente(monkeypatch):
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1")
    _set(ex, set_type="warmup")
    assert db.exercises[0]["form_practice"] == 0 and getattr(db, "updates", 0) == 0
    _set(ex, client_key="s-1", exercise_form_practice=True)
    rep = _set(ex, client_key="s-1", exercise_form_practice=False)
    assert rep["duplicate"] is True and rep["form_practice"] is True and rep["form_practice_locked"] is True
    assert db.exercises[0]["form_practice"] == 1 and db.updates == 1 and len(db.sets) == 2


def test_criacao_valor_explicito_vale_e_nao_enviado_herda(monkeypatch):
    db = _db(monkeypatch)
    _ex(client_key="ex-1", workout_plan_exercise_id=77, form_practice=True)
    assert _ex(client_key="ex-2", workout_plan_exercise_id=77, form_practice=False) and db.exercises[-1]["form_practice"] == 0
    _ex(client_key="ex-3", workout_plan_exercise_id=77)  # não enviado: herda
    assert db.exercises[-1]["form_practice"] == 1
    assert fp.inherit_form_practice(None, None) is False


def test_marca_decidida_e_a_relida_depois_do_lock(monkeypatch):
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1", form_practice=False)
    db.fresh_mark = 1  # outra transacao ja gravou a marca; o snapshot ainda diz 0
    out = _set(ex, exercise_form_practice=True)
    assert out["form_practice"] is True and getattr(db, "updates", 0) == 0  # ja era 1: sem UPDATE


def test_sem_o_campo_a_marca_decidida_tambem_e_a_relida_depois_do_lock(monkeypatch):
    """#284: payload sem `exercise_form_practice` usa a marca da linha travada, nao a do snapshot."""
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1", form_practice=False)
    db.fresh_mark = 1
    out = _set(ex)
    assert out["form_practice"] is True and out["intensity_status"] is None


def test_contagem_usada_e_a_da_leitura_travada(monkeypatch):
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1", form_practice=True)
    db.fresh_count = 1  # a 1a serie de trabalho de outra transacao so a leitura corrente enxerga
    out = _set(ex, exercise_form_practice=False)
    assert out["form_practice"] is True and getattr(db, "updates", 0) == 0


def test_sem_o_campo_so_ha_a_trava_da_linha_do_exercicio_e_uma_unica_contagem_sem_trava(monkeypatch):
    """Sem o campo: a única trava é a da linha do exercício (#241, serializa POSTs); a contagem é UMA, comum
    (sem FOR SHARE; a resposta informa `form_practice_locked`), sem UPDATE da marca."""
    db = _db(monkeypatch)
    ex = _ex(client_key="ex-1")
    out = _set(ex)
    travas = [q for q in db.flat_sql if "FOR UPDATE" in q or "FOR SHARE" in q]
    assert travas == [f"SELECT {fp.FORM_PRACTICE_COLUMN} FROM exercises WHERE id = %s FOR UPDATE"]
    contagens = [q for q in db.flat_sql if q.startswith("SELECT COUNT(*) AS n FROM exercise_sets es")]
    assert len(contagens) == 1 and contagens[0] == " ".join(fp.sql_working_sets_recorded().split())
    assert getattr(db, "updates", 0) == 0 and not any(q.startswith("UPDATE exercises SET form_practice") for q in db.flat_sql)
    assert out["form_practice_locked"] is True  # a série de trabalho recém-gravada trava
    aq = _set(_ex(client_key="ex-2"), set_type="warmup")
    assert aq["form_practice_locked"] is False
    assert fp.sql_working_sets_recorded(locking=True).endswith(" FOR SHARE")
