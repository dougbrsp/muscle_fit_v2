"""Os criterios do eval pago (`tests/eval/criterios_llm.py`) provados SEM LLM.

Roda o caminho inteiro do eval - caso congelado -> executor -> funcao real
da feature -> criterio - com a OpenAI trocada por respostas escritas a mao:
uma que cumpre o pedido do prompt (tem de passar) e uma que viola um ponto
especifico (tem de ser pega). Assim o eval pago nunca e a primeira vez que
o proprio eval roda, e um criterio quebrado aparece no CI, de graca.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.eval import criterios_llm
from tests.support import llm_casos


class _Resposta:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _rodar(monkeypatch, feature: str, caso: dict, texto: str):
    from app import openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        openai_client.urllib.request, "urlopen", lambda request, timeout=None: _Resposta({"output_text": texto})
    )
    saida = llm_casos.executar(feature, caso, monkeypatch, rag="fixo")
    return criterios_llm.AVALIADORES[feature](saida, caso)


def _caso(feature: str, caso_id: str) -> dict:
    return next(c for c in llm_casos.carregar(feature) if c["id"] == caso_id)


# cardio-agent-v3 (M98 C3a): saida JSON. Sexta 10/07 (dia de puxar, sem perna amanha) com Normal Z2.
CARDIO_OK = json.dumps({
    "summary": "Recuperação parcial nos grupos treinados ontem.",
    "objective": "Preservar aptidão sem aumentar fadiga depois do treino planejado.",
    "confidence": "medium",
    "confidence_reason": "Poucos dias de dados.",
    "sessions": [{"date": "2026-07-10", "cardio_mode": "continuous", "cardio_warmup_minutes": 5,
                  "cardio_duration_minutes": 30, "cardio_target_zone": 2, "cardio_cooldown_minutes": 5,
                  "hiit_low_seconds": None, "hiit_high_seconds": None, "hiit_low_zone": None,
                  "hiit_high_zone": None, "hiit_rounds": None, "rationale": "Carga semanal estável.",
                  "signals": ["TSB"]}],
}, ensure_ascii=False)
# HIIT na sexta (vespera do sabado de perna): o envelope corrige e o eval
# acusa. No proprio dia de perna o HIIT e permitido desde 27/10/2026.
CARDIO_HIIT_NA_VESPERA_DE_PERNA = json.dumps({
    "summary": "Ok.", "objective": "Ok.", "confidence": "high", "confidence_reason": "Ok.",
    "sessions": [{"date": "2026-07-10", "cardio_mode": "hiit", "cardio_warmup_minutes": 5,
                  "cardio_duration_minutes": None, "cardio_target_zone": None, "cardio_cooldown_minutes": 5,
                  "hiit_low_seconds": 120, "hiit_high_seconds": 120, "hiit_low_zone": 2, "hiit_high_zone": 4,
                  "hiit_rounds": 5, "rationale": "Subir carga.", "signals": []}],
})

ANALISE_OK = """## Evolução dos Treinos
Treinos consistentes na janela.
## Volume e Frequência
Frequência estável.
## Destaques de Progressão
Status vindos de exercise_progression.
## Pontos de Atenção
Nenhum dado de sono."""

INSIGHT_OK = """Diagnóstico: a leitura tem confiança média.
Evidências:
- volume (janela 12/06 a 09/07): sem dado comparável.
Ajustes recomendados:
- Nenhuma ação: estado esperado.
Próximo passo: manter o Modelo."""


@pytest.mark.parametrize(
    ("feature", "caso_id", "texto"),
    [
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "HOLD", "note": "Esforço atingido: mantenha a carga.", "next": {"reps": 10, "load_kg": 22.5, "rpe": 10}}'),
        # set-eval-v6: a direcao e a carga da regra da proxima serie do motor.
        ("set_evaluation", "reps_acima_do_limite_rpe_10", '{"category": "INCREASE_LOAD", "note": "Esforço atingido com reps de sobra: aumente a carga um passo.", "next": {"reps": 12, "load_kg": 50, "rpe": 10}}'),
        ("set_evaluation", "falha_antes_de_6_reps", '{"category": "DECREASE_LOAD", "note": "Esforço atingido cedo demais: reduza a carga um passo.", "next": {"reps": 8, "load_kg": 137.5, "rpe": 10}}'),
        # set-eval-v5: dor muscular acima do limiar -> reduzir (ou encerrar, next todo null).
        ("set_evaluation", "dor_muscular_alta_com_nota", '{"category": "DECREASE_LOAD", "note": "Esforço atingido, mas a dor muscular está alta: reduza a carga e priorize a técnica.", "next": {"reps": 8, "load_kg": 15, "rpe": 8}}'),
        ("set_evaluation", "dor_muscular_alta_com_nota", '{"category": "DECREASE_LOAD", "note": "Esforço atingido, mas com essa dor encerre o exercício hoje.", "next": {"reps": null, "load_kg": null, "rpe": null}}'),
        ("exercise_summary", "queda_de_reps", "Houve queda de repetições ao longo das séries. A técnica se manteve. Na próxima vez, siga a decisão do motor."),
        ("workout_summary", "peito_ombro_triceps_queda", "Treino denso de peito e ombros. A fadiga apareceu no fim. Priorize a recuperação antes do próximo treino."),
        ("insights", "volume_peito", INSIGHT_OK),
        ("workouts_analysis", "dias_30", ANALISE_OK),
        ("cardio_agent", "com_plano_planejado", CARDIO_OK),
        ("exercise_description", "hack_squat", '{"setup": ["Ajuste o encosto."], "execution": ["Desça controlando.", "Empurre a plataforma.", "Expire ao subir."], "cues": ["Não trave os joelhos."]}'),
    ],
)
def test_resposta_que_cumpre_o_prompt_passa(monkeypatch, feature, caso_id, texto):
    assert _rodar(monkeypatch, feature, _caso(feature, caso_id), texto) == []


@pytest.mark.parametrize(
    ("feature", "caso_id", "texto", "trecho"),
    [
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "DECREASE_LOAD", "note": "Esforço atingido. Reduza.", "next": {"reps": 8, "load_kg": null, "rpe": 10}}', "fora de"),
        # set-eval-v6: a resposta da v5 neste caso (subir ate o alvo do motor, 30 kg) contraria a regra (manter 22,5 kg).
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "INCREASE_LOAD", "note": "Esforço atingido; aumentar carga para o alvo do motor na próxima série.", "next": {"reps": 10, "load_kg": 30, "rpe": 10}}', "DIRECAO_DIFERENTE_DO_MOTOR"),
        ("set_evaluation", "reps_acima_do_limite_rpe_10", '{"category": "HOLD", "note": "Esforço atingido: mantenha a carga.", "next": {"reps": 12, "load_kg": 47.5, "rpe": 10}}', "DIRECAO_DIFERENTE_DO_MOTOR"),
        # set-eval-v5: a regressao do eval da v4 (HOLD na carga executada com dor 9/10) e pega.
        ("set_evaluation", "dor_muscular_alta_com_nota", '{"category": "HOLD", "note": "Esforço atingido. Mantenha a carga.", "next": {"reps": null, "load_kg": 17.5, "rpe": null}}', "fora de"),
        # set-eval-v5: a saida do 1o eval da v5 (HOLD, "repita a carga", 30 kg com 22,5 executados) e pega.
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "HOLD", "note": "Esforço atingido e reps dentro da faixa; repita a carga na próxima série.", "next": {"reps": 10, "load_kg": 30, "rpe": 10}}', "DIRECAO_DIFERENTE_DO_MOTOR"),
        # set-eval-v5: copiar a linha de status (como a v4 fez) e pego.
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "HOLD", "note": "Esforço atingido · RPE executado 10 · alvo efetivo do dia RPE 10. Mantenha.", "next": {"reps": 10, "load_kg": 22.5, "rpe": 10}}', "copia a linha de status"),
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "HOLD", "note": "Série ruim, mantenha.", "next": {"reps": 10, "load_kg": 22.5, "rpe": 10}}', "status do motor"),
        ("set_evaluation", "progresso_reps_acima_da_faixa", '{"category": "INCREASE_LOAD", "note": "Suba.", "next": {"reps": 10, "load_kg": 60, "rpe": 10}}', "DIRECAO_DIFERENTE_DO_MOTOR"),
        # fora da regra da proxima serie (aplicacao de tecnica): o teto do alvo do motor continua valendo.
        ("set_evaluation", "apos_tecnica_avancada", '{"category": "INCREASE_LOAD", "note": "Suba.", "next": {"reps": 6, "load_kg": 50, "rpe": 8}}', "acima da decisao do motor"),
        ("set_evaluation", "dor_articular", '{"category": "INCREASE_LOAD", "note": "Siga.", "next": {"reps": 12, "load_kg": 42.5, "rpe": 10}}', "fora de"),
        ("set_evaluation", "rpe_10_primeira_serie", '{"category": "HOLD", "note": "Ok.", "next": {"reps": null, "load_kg": null, "rpe": null}}', "sem sugestao numerica"),
        ("exercise_summary", "queda_de_reps", "Suba para 32,5 kg na próxima. Foi bom.", "sugere/repete carga"),
        ("workout_summary", "peito_ombro_triceps_queda", "- item\nTreino com média de 9,13 de RPE. Ok. Fim.", "markdown"),
        ("insights", "dado_ausente_cintura", "Diagnóstico: a cintura caiu 2,37 cm por causa do treino.", "termo proibido"),
        ("workouts_analysis", "dias_30", ANALISE_OK + "\nSe quiser, posso fazer uma versão curta.", "oferta"),
        ("cardio_agent", "avulso_14d", CARDIO_HIIT_NA_VESPERA_DE_PERNA, "hiit_vetoed"),
        ("exercise_description", "hack_squat", '{"setup": [], "execution": ["Faça 3 séries de 12 repetições."], "cues": []}', "prescreve"),
    ],
)
def test_resposta_que_viola_o_prompt_e_pega(monkeypatch, feature, caso_id, texto, trecho):
    problemas = _rodar(monkeypatch, feature, _caso(feature, caso_id), texto)
    assert any(trecho in p for p in problemas), problemas


def _rascunho_valido(caso: dict) -> dict:
    """Rascunho que cumpre as regras duras, montado do proprio contexto."""
    pedido, contexto = caso["input"]["request"], caso["input"]["context"]
    ids = {g["name"]: g["id"] for g in contexto["muscle_groups"]}
    slots = []
    for sequencia in range(1, pedido["days_per_cycle"] + 1):
        itens = []
        if pedido["volume_mode"] == "equipment_series":
            grupo, linhas = next(iter(contexto["gym_catalog"].items()))
            linha = linhas[0]
            itens.append({"item_kind": "equipment", "muscle_group_id": ids[grupo], "mapping_id": linha["mapping_id"],
                          "equipment_id": linha["equipment_id"], "sets_count": 3, "rest_seconds": None, "prioritize": False})
        else:
            itens.append({"item_kind": "muscle_group", "muscle_group_id": contexto["muscle_groups"][0]["id"],
                          "priority_min_pct": 30, "priority_max_pct": 40, "prioritize": True})
        slots.append({"sequence": sequencia, "slot_type": "normal", "items": itens})
    return {"template": {"volume_mode": pedido["volume_mode"], "slots": slots}, "rationale": "", "source_citations": []}


@pytest.mark.parametrize("caso_id", ["modo_b_6_dias_abc", "modo_a_5_dias"])
def test_periodizacao_regras_duras(monkeypatch, caso_id):
    caso = _caso("periodization", caso_id)
    valido = _rascunho_valido(caso)
    assert _rodar(monkeypatch, "periodization", caso, json.dumps(valido)) == []

    invalido = json.loads(json.dumps(valido))
    invalido["template"]["slots"][0]["items"][0]["muscle_group_id"] = 999_999
    invalido["template"]["slots"].pop()
    problemas = _rodar(monkeypatch, "periodization", caso, json.dumps(invalido))
    assert any("sequences" in p for p in problemas) and any("inventado" in p for p in problemas), problemas


def test_numeros_fora_da_entrada_aceita_arredondamento_e_milhar():
    entrada = {"volume": "13894.00", "rpe": 9.78, "carga": "31,5kg"}
    assert criterios_llm.numeros_fora_da_entrada("13.894 kg, RPE 9,8, 31,5 kg", entrada) == []
    assert criterios_llm.numeros_fora_da_entrada("subiu 4,2%", entrada) == ["4,2"]


def test_numeros_fora_da_entrada_le_milhar_com_decimal_pt_br():
    # Eval do #442: "41.112,5" (pt-BR) e 41112.5 no JSON; o criterio antigo
    # reprovava toda resposta da analise por isso.
    entrada = {"tonnage_kg": 41112.5, "media": 26.6, "volume": 13894}
    assert criterios_llm.numeros_fora_da_entrada("Tonelagem 41.112,5 kg, media 26,6 e 13.894", entrada) == []
    assert criterios_llm.numeros_fora_da_entrada("Tonelagem 41,112.5 kg", entrada) == []


def test_numeros_fora_da_entrada_aceita_float_longo_citado_inteiro():
    entrada = {"e1rm": 26.666666666666664, "outro": 173.33333333333331}
    texto = "e1RM 26,666666666666664 e 173,33333333333331; arredondado 26,67 e 173,3"
    assert criterios_llm.numeros_fora_da_entrada(texto, entrada) == []


def test_numeros_fora_da_entrada_reprova_numero_inventado_nos_dois_formatos():
    entrada = {"tonnage_kg": 41112.5, "media": 26.6}
    assert criterios_llm.numeros_fora_da_entrada("41.112,6 kg e 26,7", entrada) == ["26,7", "41.112,6"]
    # Milhar malformado ("41.11,5") nao vira leitura valida.
    assert criterios_llm.numeros_fora_da_entrada("41.11,5", entrada) == ["41.11,5"]


def test_numeros_fora_da_entrada_le_bloco_compartilhado_resolvido():
    # Os casos da analise guardam blocos repetidos em `compartilhado`; o
    # carregador do eval resolve as referencias antes do criterio. Um numero
    # que so existe dentro do bloco compartilhado tem de ser reconhecido,
    # inclusive escrito em pt-BR com milhar e decimal.
    documento = json.loads(llm_casos._documento("workouts_analysis"))
    assert documento["compartilhado"], "a analise deixou de usar blocos compartilhados"
    # O caso escolhido e o primeiro cujos blocos referenciados tenham um
    # decimal >= 1000 (o numero tem de estar na entrada DELE).
    numeros: list[float] = []
    for indice, bruto in enumerate(documento["cases"]):
        chaves = re.findall(r'"\$compartilhado": "([0-9a-f]+)"', json.dumps(bruto["input"]))
        blocos = [documento["compartilhado"][k] for k in chaves]
        numeros = [v for v in criterios_llm._valores(blocos) if v >= 1000 and v != int(v)]
        if numeros:
            break
    assert numeros, "nenhum decimal >= 1000 nos blocos compartilhados"
    caso = llm_casos.carregar("workouts_analysis")[indice]
    assert "$compartilhado" not in json.dumps(caso["input"])
    inteiro, fracao = repr(numeros[0]).split(".")
    pt_br = format(int(inteiro), ",").replace(",", ".") + "," + fracao
    assert criterios_llm.numeros_fora_da_entrada(pt_br, caso["input"]["training"]) == []


# M98 C5: cardio na periodizacao, conferido pelos donos na saida crua.
_HIIT = {"cardio_mode": "hiit", "cardio_warmup_minutes": 5, "hiit_low_seconds": 120, "hiit_high_seconds": 120,
         "hiit_low_zone": 2, "hiit_high_zone": 4, "hiit_rounds": 5, "cardio_cooldown_minutes": 5,
         "cardio_duration_minutes": None, "cardio_target_zone": None}
_NORMAL = {"cardio_mode": "continuous", "cardio_warmup_minutes": 5, "cardio_duration_minutes": 30,
           "cardio_target_zone": 2, "cardio_cooldown_minutes": 5, "hiit_low_seconds": None,
           "hiit_high_seconds": None, "hiit_low_zone": None, "hiit_high_zone": None, "hiit_rounds": None}


def _com_cardio(caso: dict) -> dict:
    rascunho = _rascunho_valido(caso)
    for slot in rascunho["template"]["slots"]:
        slot["cardio_items"] = []
    return rascunho


def test_periodizacao_cardio_hiit_na_vespera_de_perna_e_pego(monkeypatch):
    caso = _caso("periodization", "cardio_hiit_vespera_de_perna")
    quadriceps = next(g["id"] for g in caso["input"]["context"]["muscle_groups"] if g["name"].startswith("Quadr"))
    rascunho = _com_cardio(caso)
    slots = rascunho["template"]["slots"]
    slots[1]["items"][0]["muscle_group_id"] = quadriceps
    slots[0]["cardio_items"] = [_HIIT]
    problemas = _rodar(monkeypatch, "periodization", caso, json.dumps(rascunho))
    assert any("HIIT vetado" in p for p in problemas), problemas
    slots[0]["cardio_items"], slots[3]["cardio_items"] = [], [_HIIT]
    assert _rodar(monkeypatch, "periodization", caso, json.dumps(rascunho)) == []


def test_periodizacao_cardio_descanso_e_recusa(monkeypatch):
    caso = _caso("periodization", "cardio_no_dia_de_descanso")
    rascunho = _com_cardio(caso)
    problemas = _rodar(monkeypatch, "periodization", caso, json.dumps(rascunho))
    assert any("sem nenhum cardio_items" in p for p in problemas) and any("descanso" in p for p in problemas)
    descanso = rascunho["template"]["slots"][-1]
    descanso.update(slot_type="descanso", items=[], cardio_items=[_NORMAL])
    assert _rodar(monkeypatch, "periodization", caso, json.dumps(rascunho)) == []

    recusa = _caso("periodization", "cardio_proibido_pelo_usuario")
    rascunho = _com_cardio(recusa)
    rascunho["template"]["slots"][0]["cardio_items"] = [_NORMAL]
    problemas = _rodar(monkeypatch, "periodization", recusa, json.dumps(rascunho))
    assert any("recusou cardio" in p for p in problemas), problemas
