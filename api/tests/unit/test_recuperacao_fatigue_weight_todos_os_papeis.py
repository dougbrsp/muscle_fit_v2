"""Regressão: a dose de RECUPERAÇÃO soma os três papéis (PRIMARY, SECONDARY e
STABILIZER) com o `fatigue_weight` cadastrado, e não depende do modo de
contagem do volume do Modelo (`methodology_volume.VolumeCountingMode`).

Regra do usuário (2026-10-16), registrada no §3.2 de
`docs/motores/FONTES_DA_VERDADE.md`: o modo de contagem é só da METODOLOGIA de
volume; a recuperação continua lendo todos os papéis, estabilizador inclusive.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.intelligence.volume_service import VolumeService

PUXADA = 109
DORSAL, BICEPS, CORE = 6, 5, 55

# Pesos de fadiga diferentes dos de escrita padrão de propósito: o teste prova
# que o valor CADASTRADO é o que entra.
CADASTRO = {
    PUXADA: [
        {"muscle_group_id": DORSAL, "role": "PRIMARY", "stimulus_weight": 1.0, "fatigue_weight": 0.9},
        {"muscle_group_id": BICEPS, "role": "SECONDARY", "stimulus_weight": 0.35, "fatigue_weight": 0.4},
        {"muscle_group_id": CORE, "role": "STABILIZER", "stimulus_weight": 0.2, "fatigue_weight": 0.15},
    ]
}

ITEM = {
    "mapping_id": PUXADA,
    "set_rows": [
        {"reps": 10, "rpe": 8, "set_type": "working"},
        {"reps": 8, "rpe": 9, "set_type": "working"},
    ],
}


def _contribuicoes() -> list[dict]:
    return VolumeService._resolve_contributions(PUXADA, CADASTRO, None, None, None, None, None)


def test_recuperacao_le_os_tres_papeis_com_o_fatigue_weight_cadastrado():
    por_papel = {c["role"]: (c["muscle_group_id"], c["fatigue_weight"]) for c in _contribuicoes()}
    assert por_papel == {"PRIMARY": (DORSAL, 0.9), "SECONDARY": (BICEPS, 0.4), "STABILIZER": (CORE, 0.15)}


def test_dose_de_recuperacao_soma_os_tres_papeis_com_o_fatigue_weight():
    unitaria = VolumeService._session_dose_for_item(ITEM, 1.0, 1.0, 1.0)
    assert unitaria > 0
    doses = {c["role"]: VolumeService._session_dose_for_item(ITEM, c["fatigue_weight"], 1.0, 1.0) for c in _contribuicoes()}
    # Estabilizador inclusive: os três grupos recebem dose.
    assert doses["PRIMARY"] == pytest.approx(unitaria * 0.9)
    assert doses["SECONDARY"] == pytest.approx(unitaria * 0.4)
    assert doses["STABILIZER"] == pytest.approx(unitaria * 0.15)
    assert sum(doses.values()) == pytest.approx(unitaria * (0.9 + 0.4 + 0.15))


_PROIBIDOS = ("VolumeCountingMode", "counting_mode", "counted_roles", "COUNTED_ROLES_BY_MODE", "load_volume_contributions")
# Lote 4 do modo de contagem: o REALIZADO da metodologia (`direct_sets` da
# janela de 7 dias) passou a pedir o peso do modo ao motor dentro do
# `VolumeService`. Só estas funções podem citar o modo, e nenhuma delas lê
# `fatigue_weight` nem a dose de recuperação.
_LEITORES_DO_MODO = {"get_daily_muscle_activation", "_counted_by_group_name"}


_INTELLIGENCE = Path(__file__).resolve().parents[2] / "app" / "intelligence"
# Código de RECUPERAÇÃO fora do `VolumeService`: não lê o modo nem chama os
# leitores do modo.
_MODULOS_DE_RECUPERACAO = (
    "muscle_recovery_service.py",
    "recovery_read_model.py",
    "recovery_projection.py",
    "session_fatigue_reader.py",
)
# Marcas de código de recuperação dentro do `VolumeService`.
_MARCAS_DE_RECUPERACAO = ("fatigue_weight", "_session_dose_for_item", "recovery")


def _volume_service() -> tuple[str, ast.AST]:
    texto = (_INTELLIGENCE / "volume_service.py").read_text(encoding="utf-8")
    return texto, ast.parse(texto)


def _funcoes(texto: str, arvore: ast.AST):
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield no, ast.get_source_segment(texto, no) or ""


def _chamadas(no: ast.AST) -> set[str]:
    nomes = set()
    for filho in ast.walk(no):
        if isinstance(filho, ast.Call):
            alvo = filho.func
            nomes.add(alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", ""))
    return nomes


def test_recuperacao_nao_depende_do_modo_de_contagem_do_volume():
    """O `VolumeService` (dono da dose de recuperação) não lê o modo de
    contagem nem o loader da metodologia fora do realizado: trocar o modo de
    um Modelo nunca muda a recuperação. O modo só aparece no realizado da
    metodologia (`_LEITORES_DO_MODO`), que não toca `fatigue_weight`. A varredura
    cobre o ARQUIVO INTEIRO (módulo, classe e funções) menos o corpo dessas
    duas funções."""
    texto, arvore = _volume_service()
    linhas = texto.splitlines()
    vistos = set()
    for no, codigo in _funcoes(texto, arvore):
        if no.name not in _LEITORES_DO_MODO:
            continue
        vistos.add(no.name)
        assert "fatigue_weight" not in codigo and "_session_dose_for_item" not in codigo, no.name
        for indice in range(no.lineno - 1, no.end_lineno):
            linhas[indice] = ""
    assert vistos == _LEITORES_DO_MODO  # a guarda enxerga as funções que liberou
    resto = "\n".join(linhas)
    citados = [proibido for proibido in _PROIBIDOS if proibido in resto]
    assert not citados, f"modo de contagem fora de {_LEITORES_DO_MODO} em volume_service.py: {citados}"


def test_codigo_de_recuperacao_nao_chama_os_leitores_do_modo():
    """Nenhuma função de recuperação (dose, `fatigue_weight`, `recovery`) chama
    `get_daily_muscle_activation` ou `_counted_by_group_name`, no `VolumeService`
    nem nos módulos de recuperação - que também não citam o modo."""
    texto, arvore = _volume_service()
    for no, codigo in _funcoes(texto, arvore):
        if no.name in _LEITORES_DO_MODO or not any(marca in codigo for marca in _MARCAS_DE_RECUPERACAO):
            continue
        assert not (_chamadas(no) & _LEITORES_DO_MODO), no.name
    for nome in _MODULOS_DE_RECUPERACAO:
        fonte = (_INTELLIGENCE / nome).read_text(encoding="utf-8")
        citados = [proibido for proibido in (*_PROIBIDOS, *_LEITORES_DO_MODO) if proibido in fonte]
        assert not citados, (nome, citados)


def test_realizado_por_modo_nao_muda_a_regua_equivalente(monkeypatch):
    """O modo muda só `direct_sets`; `sets_count` (séries equivalentes P+S+E,
    a régua da ativação) é o mesmo nos dois modos, e o estabilizador nunca
    entra em `direct_sets`."""
    from app.intelligence import muscle_contribution_resolver as resolver
    from app.intelligence.methodology_volume import VolumeCountingMode

    linhas = [
        {"exercise_id": 1, "mapping_id": PUXADA, "sets_count": 4, "local_date": "2026-10-15", "had_rpe_9_10": 0, "technique_rows": 0}
    ]
    nomes = {DORSAL: "Dorsal", BICEPS: "Bíceps", CORE: "Core"}
    cadastro = {PUXADA: [dict(c, muscle_group_name=nomes[c["muscle_group_id"]]) for c in CADASTRO[PUXADA]]}
    monkeypatch.setattr(resolver, "_resolve_muscle_contributions_for_mappings", lambda ids: {k: cadastro[k] for k in ids})

    def ler(modo):
        service = VolumeService()
        service._technique_catalog = {}
        service.fetch_all = lambda sql, params=(): [dict(row) for row in linhas]
        return {i["muscle_group"]: i for i in service.get_daily_muscle_activation(days=7, user_id=1, counting_mode=modo)}

    so_primario, ponderado = ler(VolumeCountingMode.PRIMARY_ONLY), ler(VolumeCountingMode.PRIMARY_SECONDARY)
    assert {g: i["sets_count"] for g, i in so_primario.items()} == {g: i["sets_count"] for g, i in ponderado.items()}
    assert so_primario["Core"]["direct_sets"] == ponderado["Core"]["direct_sets"] == 0
    assert so_primario["Bíceps"]["direct_sets"] == 0
    assert ponderado["Bíceps"]["direct_sets"] == pytest.approx(4 * 0.35)
