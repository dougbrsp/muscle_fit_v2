"""M97 F1 - Impacto em producao: paridade do replay da progressao antes x depois.

O historico de `tests/support/progression_parity_fixture.py` (3 exercicios, 2
grupos, 30 sessoes; progresso, mudanca de faixa, aumento e reducao de carga,
regressao D10 com `pending_payload.track_key` no estado final) e reprocessado
pelo caminho do replay de producao (`progression_state.rebuild_for_user`).

Os valores esperados foram calculados rodando a MESMA fixture sobre o codigo
de `origin/develop` ANTES da F1 (commit fdf23ae, F0 ja mesclada):

    fingerprint   c903dbc05c702f291b69251d6aa903ea  (md5 do `state_snapshot`)
    decisions_md5 2a5bdda6c3e45bdeb8be0bff77325a15  (activity_id + decision.to_dict())
    45 decisoes, 2 estados de grupo, 3 trilhas

Historico so de hipertrofia (todo o de producao) tem, depois da F1, o MESMO
estado, as MESMAS decisoes (com a mesma evidencia e os mesmos rotulos de
trilha) e o MESMO `fingerprint` que o workflow `replay-progression` mostra.
"""

from __future__ import annotations

from tests.support import progression_parity_fixture as parity

FINGERPRINT_ANTES_DA_F1 = "c903dbc05c702f291b69251d6aa903ea"
DECISIONS_MD5_ANTES_DA_F1 = "2a5bdda6c3e45bdeb8be0bff77325a15"


def test_replay_so_hipertrofia_identico_ao_de_antes_da_f1(monkeypatch):
    result = parity.run(monkeypatch)
    assert result["counts"] == {"groups": 2, "tracks": 3, "decisions": 45}
    assert result["fingerprint"] == FINGERPRINT_ANTES_DA_F1
    assert result["decisions_md5"] == DECISIONS_MD5_ANTES_DA_F1


def test_estado_final_tem_o_alvo_no_formato_de_sempre(monkeypatch):
    store = parity.run(monkeypatch)["store"]
    pendentes = [state.pending_payload for state in store.groups.values() if state.pending_action]
    assert pendentes and all(len(payload["track_key"]) == 3 for payload in pendentes)
    assert all(item["training_goal"] == "hypertrophy" for item in store.decisions)
