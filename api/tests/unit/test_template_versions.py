"""Versoes do Modelo (`app/template_versions.py`, migracao 065) - sem banco.

O que importa provar aqui, rapido e sem MySQL:

- a forma CANONICA: nada estrutural mudou -> nenhuma versao (e nenhuma
  escrita), inclusive quando o banco devolve `Decimal`/`str` (Data API) e o
  payload `int`, ou os slots/itens chegam em outra ordem;
- versao nova so de intencao copia slots e itens da vigente por
  `INSERT ... SELECT` (nada sobe para o Python);
- versao nova fecha a vigente e abre a n+1 com o MESMO `db_now`;
- Modelo sem versao ganha a v1 com `effective_from` = agora;
- concorrencia: a vigente ja encerrada vira `TemplateVersionConflict`;
- leitores `_now`/`_at`/`_for_window` e a janela.

A integracao (v1 -> PUT -> v2 com a v1 intacta, nos dois backends) esta em
`tests/integration/dataapi/test_template_versions_065.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app import template_versions as tv
from app.models import WorkoutPlanTemplateSlotIn, WorkoutPlanTemplateSlotItemIn

AGORA = datetime(2026, 10, 5, 21, 30, 0)


def _version_row(**overrides):
    row = {
        "id": 10,
        "template_id": 7,
        "version_number": 1,
        "training_type_id": 2,
        "volume_mode": "equipment_series",
        "template_type": "abc",
        "grid_rows": 2,
        "grid_cols": 4,
        "methodology_intent": "standard",
        "methodology_target_group_ids": "[]",
        "effective_from": None,
        "superseded_at": None,
        "created_by_user_id": 1,
        "reverted_from_version_id": None,
        "change_summary": None,
        "created_at": AGORA,
    }
    row.update(overrides)
    return row


def _item(**overrides):
    base = {name: None for name in tv.ITEM_FIELDS}
    base.update(item_order=1, item_kind="equipment", muscle_group_id=52, mapping_id=7100, equipment_id=9001,
                sets_count=3, reps_target="8-10", rpe_target="8", prioritize=False, rest_seconds=90)
    base.update(overrides)
    return base


def _slot(sequence=1, items=None, **overrides):
    base = {"sequence": sequence, "name": f"Dia {sequence}", "weekday": None, "slot_type": "normal",
            "priority_min_pct": None, "priority_max_pct": None}
    base.update(overrides)
    base["items"] = items if items is not None else [_item()]
    return base


def _payload(slots=None, **header):
    base = {"training_type_id": 2, "volume_mode": "equipment_series", "template_type": "abc",
            "grid_rows": 2, "grid_cols": 4}
    base.update(header)
    base["slots"] = slots if slots is not None else [_slot(1), _slot(2, [_item(mapping_id=7101)])]
    return base


def _db_slots(slots):
    """O que o banco devolveria para a vigente: ids, `Decimal`/`str` no lugar
    de `int`, `prioritize` como 0/1 - e as linhas fora de ordem."""
    linhas_slot, linhas_item, next_id = [], [], 100
    for slot in reversed(slots):
        next_id += 1
        linhas_slot.append({"id": next_id, **{k: v for k, v in slot.items() if k != "items"}})
        for item in reversed(slot["items"]):
            row = {"slot_id": next_id}
            for name in tv.ITEM_FIELDS:
                value = item.get(name)
                if name == "prioritize":
                    value = int(bool(value))
                elif isinstance(value, int) and not isinstance(value, bool):
                    value = Decimal(value) if name == "sets_count" else str(value)
                row[name] = value
            linhas_item.append(row)
    return linhas_slot, linhas_item


class FakeCur:
    """Cursor dirigido pelo SQL. Registra tudo que e executado."""

    def __init__(self, vigente=None, slots=(), supersede_rows=1, max_number=0):
        self.vigente = vigente
        self.slot_rows, self.item_rows = _db_slots(list(slots)) if slots else ([], [])
        self.supersede_rows = supersede_rows
        self.max_number = max_number
        self.executed: list[tuple[str, tuple]] = []
        self._result: list = []
        self.lastrowid = 0
        self.rowcount = 0
        self._next_id = 500

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self.rowcount = 0
        if sql == tv.CURRENT_VERSION_SQL:
            self._result = [self.vigente] if self.vigente else []
        elif sql == tv.VERSION_SLOTS_SQL:
            self._result = list(self.slot_rows)
        elif sql == tv.VERSION_ITEMS_SQL:
            self._result = list(self.item_rows)
        elif sql.startswith("SELECT NOW()"):
            self._result = [{"agora": AGORA}]
        elif sql == tv.MAX_VERSION_NUMBER_SQL:
            self._result = [{"n": self.max_number}]
        elif sql == tv.SUPERSEDE_SQL:
            self.rowcount = self.supersede_rows
        elif sql.lstrip().startswith("INSERT"):
            self._next_id += 1
            self.lastrowid = self._next_id
            self.rowcount = 1
        else:
            raise AssertionError(f"SQL inesperado: {sql}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def writes(self):
        return [sql for sql, _ in self.executed if sql.split()[0] in {"INSERT", "UPDATE", "DELETE"}]

    def params_of(self, prefix):
        return [params for sql, params in self.executed if sql.startswith(prefix)]


# ---------------------------------------------------------------------------
# Nada estrutural mudou -> nenhuma versao, nenhuma escrita
# ---------------------------------------------------------------------------


def test_payload_igual_a_vigente_nao_cria_versao_nem_escreve():
    payload = _payload()
    cur = FakeCur(vigente=_version_row(), slots=payload["slots"])
    assert tv.write_structural_version(cur, 7, payload, {"intent": "standard", "target_group_ids": []}, actor=1) is None
    assert cur.writes() == []
    assert not any(sql.startswith("SELECT NOW()") for sql, _ in cur.executed)


def test_decimal_e_str_da_data_api_contra_int_do_payload_nao_e_mudanca():
    """Data API devolve inteiro como `str`, pymysql DECIMAL como `Decimal`."""
    payload = _payload()
    vigente = _version_row(training_type_id="2", grid_rows="2", grid_cols=Decimal("4"))
    cur = FakeCur(vigente=vigente, slots=payload["slots"])
    assert tv.write_structural_version(cur, 7, payload, None, actor=1) is None
    assert cur.writes() == []


def test_payload_em_modelos_pydantic_e_dicts_do_banco_comparam_igual():
    slots = [_slot(1), _slot(2, [_item(mapping_id=7101), _item(item_order=2, prioritize=True)])]
    pydantic_slots = [
        WorkoutPlanTemplateSlotIn(**{**s, "items": [WorkoutPlanTemplateSlotItemIn(**i) for i in s["items"]]})
        for s in slots
    ]
    cur = FakeCur(vigente=_version_row(), slots=slots)
    assert tv.write_structural_version(cur, 7, _payload(pydantic_slots), None, actor=1) is None


def test_ordem_diferente_de_slots_e_itens_nao_e_mudanca():
    slots = [_slot(1, [_item(item_order=1), _item(item_order=2, mapping_id=7102)]), _slot(2)]
    embaralhado = [_slot(2), _slot(1, [_item(item_order=2, mapping_id=7102), _item(item_order=1)])]
    cur = FakeCur(vigente=_version_row(), slots=slots)
    assert tv.write_structural_version(cur, 7, _payload(embaralhado), None, actor=1) is None


def test_alvos_da_intencao_em_outra_ordem_nao_sao_mudanca():
    vigente = _version_row(methodology_intent="specialization", methodology_target_group_ids="[14, 6, 52]")
    payload = _payload()
    cur = FakeCur(vigente=vigente, slots=payload["slots"])
    intent = {"intent": "specialization", "target_group_ids": [52, 14, 6]}
    assert tv.write_structural_version(cur, 7, payload, intent, actor=1) is None


# ---------------------------------------------------------------------------
# Mudou -> versao n+1, fechando a vigente no mesmo instante
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mudanca",
    [
        {"slots": [_slot(1), _slot(2, [_item(mapping_id=7101, sets_count=4)])]},  # series
        {"slots": [_slot(1), _slot(2, [_item(mapping_id=7101)], name="Pull")]},  # nome do slot
        {"slots": [_slot(1), _slot(2, [_item(mapping_id=7101)], weekday=3)]},  # weekday
        {"slots": [_slot(1)]},  # slot removido
        {"volume_mode": "percent_budget"},
        {"training_type_id": 3},
        {"template_type": "full_body"},
        {"grid_rows": 1},
    ],
)
def test_mudanca_estrutural_cria_a_versao_seguinte(mudanca):
    atual = _payload()
    novo = _payload(**{k: v for k, v in mudanca.items() if k != "slots"}, slots=mudanca.get("slots"))
    if "slots" not in mudanca:
        novo["slots"] = atual["slots"]
    cur = FakeCur(vigente=_version_row(version_number=3), slots=atual["slots"])

    version_id = tv.write_structural_version(cur, 7, novo, None, actor=42)

    assert version_id == cur._next_id - len(novo["slots"]) - sum(len(s["items"]) for s in novo["slots"])
    assert cur.params_of("UPDATE workout_plan_template_versions SET superseded_at") == [(AGORA, 10)]
    [insert] = cur.params_of("INSERT INTO workout_plan_template_versions")
    assert insert[0:2] == (7, 4)  # template, numero n+1
    assert insert[9] == AGORA  # effective_from == superseded_at da anterior
    assert insert[10] == 42  # autor
    # slots e itens gravados sob a versao NOVA; nenhum DELETE.
    assert all(p[1] == version_id for p in cur.params_of("INSERT INTO workout_plan_template_slots"))
    assert not any(sql.startswith("DELETE") for sql in cur.writes())


def test_so_a_intencao_mudou_copia_slots_e_itens_por_insert_select():
    cur = FakeCur(vigente=_version_row(version_number=2))
    version_id = tv.write_structural_version(
        cur, 7, None, {"intent": "cutting", "target_group_ids": []}, actor=1
    )
    assert version_id is not None
    [insert] = cur.params_of("INSERT INTO workout_plan_template_versions")
    assert insert[1] == 3 and insert[7] == "cutting" and insert[8] == "[]"
    # cabecalho estrutural herdado da vigente
    assert insert[2:7] == (2, "equipment_series", "abc", 2, 4)
    copias = [sql for sql in cur.writes() if "SELECT" in sql]
    assert len(copias) == 2
    assert cur.params_of("INSERT INTO workout_plan_template_slots") == [(version_id, 10)]
    assert cur.params_of("INSERT INTO workout_plan_template_slot_items") == [(version_id, 10)]
    # nada da arvore subiu para o Python
    assert not any(sql == " ".join(tv.VERSION_SLOTS_SQL.split()) for sql, _ in cur.executed)


def test_intencao_repetida_sem_estrutura_nao_cria_versao():
    cur = FakeCur(vigente=_version_row(methodology_intent="cutting"))
    assert tv.write_structural_version(cur, 7, None, {"intent": "cutting", "target_group_ids": []}, actor=1) is None
    assert cur.writes() == []


def test_modelo_sem_versao_ganha_a_v1_com_inicio_agora():
    cur = FakeCur(vigente=None, max_number=0)
    payload = _payload()
    version_id = tv.write_structural_version(cur, 7, payload, None, actor=5)
    assert version_id is not None
    assert cur.params_of("UPDATE") == []
    [insert] = cur.params_of("INSERT INTO workout_plan_template_versions")
    assert insert[1] == 1 and insert[9] == AGORA and insert[7] == "standard"
    assert len(cur.params_of("INSERT INTO workout_plan_template_slots")) == 2


def test_modelo_sem_versao_e_sem_estrutura_e_erro_de_programacao():
    with pytest.raises(ValueError):
        tv.write_structural_version(FakeCur(vigente=None), 7, None, {"intent": "cutting"}, actor=1)


def test_vigente_encerrada_por_outra_escrita_e_conflito():
    payload = _payload()
    cur = FakeCur(vigente=_version_row(), slots=payload["slots"], supersede_rows=0)
    novo = _payload(volume_mode="percent_budget", slots=payload["slots"])
    with pytest.raises(tv.TemplateVersionConflict):
        tv.write_structural_version(cur, 7, novo, None, actor=1)
    assert cur.params_of("INSERT") == []


# ---------------------------------------------------------------------------
# Leitores
# ---------------------------------------------------------------------------


def test_row_to_version_le_alvos_json_como_tupla_ordenada():
    version = tv.row_to_version(_version_row(methodology_target_group_ids='[52, 6]', superseded_at=AGORA))
    assert version.target_group_ids == (6, 52)
    assert not version.is_current
    assert version.intent() == {"intent": "standard", "target_group_ids": [6, 52]}
    assert tv.row_to_version(None) is None


def test_for_window_sem_date_to_le_a_vigente(monkeypatch):
    chamadas = []
    monkeypatch.setattr(tv, "_query_one", lambda sql, params: chamadas.append((sql, params)) or _version_row())
    tv.resolve_version_for_window(7, None)
    assert chamadas == [(tv.CURRENT_VERSION_SQL, (7,))]


def test_for_window_le_a_versao_do_fim_da_janela(monkeypatch):
    chamadas = []
    monkeypatch.setattr(tv, "_query_one", lambda sql, params: chamadas.append((sql, params)) or None)
    assert tv.resolve_version_for_window(7, date(2026, 9, 1)) is None
    fim = datetime(2026, 9, 2, 2, 59, 59)  # fim do dia 01/09 de Brasilia, em UTC (Lote 2b)
    assert chamadas == [(tv.VERSION_AT_SQL, (7, fim, fim))]


def test_version_at_trata_inicio_nulo_como_desde_sempre_e_fim_exclusivo():
    sql = " ".join(tv.VERSION_AT_SQL.split())
    assert "effective_from IS NULL OR v.effective_from <= %s" in sql
    assert "superseded_at IS NULL OR v.superseded_at > %s" in sql
    assert "current_template_id = %s" in tv.CURRENT_VERSION_SQL
