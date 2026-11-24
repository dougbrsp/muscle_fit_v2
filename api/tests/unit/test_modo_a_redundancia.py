"""#288 fatia 3: redundancia no ranking do modo A. O nivel vem do dono (`domain/exercise_redundancy`); aqui so o
desempate, a auditoria e a queda segura quando nao ha dado."""

from __future__ import annotations

from types import SimpleNamespace

from app.domain import exercise_redundancy as red
from app.generation import mode_a

PERFIL = {"source": "plate_loaded", "mechanism": "lever", "trajectory": "guided_arc", "profile": "mid_peak",
          "line": "joint_axis_relative", "direction_changes": "no", "confidence": "high", "status": "reviewed",
          "origin": "admin", "flags": []}


def _rc(ex, eq, pattern="push", group=5, perfil=PERFIL):
    return red.Candidate(
        exercise_id=ex, equipment_id=eq, contributions={group: "primary", 6: "secondary"},
        biomechanical_pattern=pattern, primary_joint="shoulder", movement="press", body_position="seated",
        trajectory="vertical", unilateral=False, resistance=perfil,
    )


def _cand(ex, eq, name):
    return mode_a.Candidate(exercise_id=ex, equipment_id=eq, gym_equipment_id=eq, exercise_name=name,
                            amazfit_name=None, equipment_name=name, stars=3, last_used_by_user_at=None)


SESSAO = [(10, 1)]
ALFA, BETA = _cand(11, 2, "Alfa"), _cand(12, 3, "Beta")
MAPA = {(10, 1): _rc(10, 1), (11, 2): _rc(11, 2), (12, 3): _rc(12, 3, pattern="pull", group=9)}


def _ids(ranked):
    return [c.exercise_id for c in ranked]


def test_sem_dado_a_ordem_e_identica_a_atual():
    base = _ids(mode_a.rank_candidates([BETA, ALFA], frozenset({10})))
    assert base == [11, 12]
    assert _ids(mode_a.rank_candidates([BETA, ALFA], frozenset({10}), {}, SESSAO)) == base
    # perfis nao confiaveis (sem perfil): nivel abaixo do piso de penalidade, ordem inalterada
    sem_perfil = {k: _rc(k[0], k[1], perfil=None) if k != (12, 3) else v for k, v in MAPA.items()}
    assert _ids(mode_a.rank_candidates([BETA, ALFA], frozenset({10}), sem_perfil, SESSAO)) == base
    # sem escolhidos na sessao: nada a comparar
    assert _ids(mode_a.rank_candidates([BETA, ALFA], frozenset(), MAPA, [])) == base


def test_mesmo_exercicio_e_equipamento_sem_perfil_nao_altera_a_ordem():
    # near_duplicate sem perfil: o mesmo exercicio ja e rebaixado por used_exercise_ids; a redundancia nao soma nada
    igual = _cand(10, 1, "Zeta")
    sem_perfil = {k: _rc(k[0], k[1], perfil=None) for k in MAPA}
    base = _ids(mode_a.rank_candidates([igual, ALFA, BETA], frozenset({10})))
    assert _ids(mode_a.rank_candidates([igual, ALFA, BETA], frozenset({10}), sem_perfil, SESSAO)) == base

def test_perfis_confiaveis_iguais_preferem_a_alternativa_menos_redundante():
    ranked = mode_a.rank_candidates([ALFA, BETA], frozenset({10}), MAPA, SESSAO)
    assert _ids(ranked) == [12, 11]  # Alfa e redundante (high) com o ja escolhido; Beta nao, mesmo vindo depois no nome
    assert red.at_least(red.max_level_against(MAPA[(11, 2)], [MAPA[(10, 1)]])[0], red.GENERATION_PENALTY_MIN_LEVEL)


def test_nunca_remove_candidato_e_registra_redundancia_no_escolhido():
    budget = SimpleNamespace(is_excluded=False, muscle_group_id=1, target_min=999.0)
    presc = {c.key: SimpleNamespace(decision=SimpleNamespace(decision="PROGRESS", suggested_sets=3)) for c in (ALFA,)}
    audit: list = []
    pairs = list(SESSAO)
    chosen = mode_a._allocate_group(
        budget, [ALFA], presc, session_activation={}, used_exercise_ids={10}, headroom_by_group={},
        selection_audit=audit, redundancy=MAPA, session_pairs=pairs,
    )
    assert [c.candidate.exercise_id for c in chosen] == [11]  # unica opcao: continua elegivel
    r = audit[0]["redundancy"]
    assert r["other_exercise_id"] == 10 and r["level"] in (red.HIGH, red.NEAR_DUPLICATE) and "confidence" in r
    assert pairs == [(10, 1), (11, 2)]


def test_falha_ao_carregar_vira_vazio_com_log(caplog):
    class Conn:
        def cursor(self):
            raise RuntimeError("tabela ausente")

    assert mode_a._load_redundancy(Conn(), [(10, 1)]) == {}
    assert "redundancia indisponivel" in caplog.text
