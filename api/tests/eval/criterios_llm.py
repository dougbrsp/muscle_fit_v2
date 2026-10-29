"""Criterios OBJETIVOS do eval pago das features de LLM (IA-11).

Funcoes puras: recebem a saida da feature (o que `tests/support/llm_casos.
executar` devolve) e o caso congelado, e devolvem a LISTA DE PROBLEMAS (vazia
= aprovado). Nada aqui julga estilo; so o que da para verificar sem
opiniao: formato, secoes obrigatorias, categoria esperada, numero que nao
existe na entrada, sugestao de carga fora do que o motor decidiu.

Separadas do teste de proposito (mesmo desenho de `test_llm_insights_eval.
avaliar`): o eval pago e o teste unitario dos proprios criterios
(`tests/unit/test_llm_eval_criterios.py`, sem LLM) usam a MESMA funcao.

Nenhum limiar de dominio e escrito aqui: categorias vem de
`SET_EVALUATION_CATEGORIES`, o teto de RPE do `IntensityComposition` do caso,
o teto de priorizados de `periodization.limits`, os limites da descricao de
`exercise_description_ai`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable, Mapping

# Decimal ("45,36", "9.5") ou milhar ("13.894"). Inteiros soltos ficam de fora
# de proposito: contagens aparecem legitimamente em texto corrido.
NUMERO = re.compile(r"\d+(?:[.,]\d+)+")
FRASE = re.compile(r"[^.!?]+[.!?]+(?:\s|$)")
CAUSALIDADE = ("por causa de", "por causa do", "por causa da", "causou", "isso causa")
FORA_DO_ESCOPO = ("dieta", "suplement", "medicament", "exame de sangue")
OFERTA_DE_ACAO = ("se quiser", "posso ", "quer que eu", "deseja que", "gostaria que eu")
# set-eval-v5: trechos fixos da linha de status do prompt da IA por serie (v4
# e v5), ja sem acento e em minusculas. Aparecer na nota = copia literal.
COPIA_DA_LINHA_DE_STATUS = ("rpe executado", "alvo efetivo do dia", " · ")
# set-eval-v6: codigo do criterio objetivo "categoria/carga da IA != direcao/carga
# da regra da proxima serie do motor".
DIRECAO_DIFERENTE_DO_MOTOR = "DIRECAO_DIFERENTE_DO_MOTOR"
DADO_AUSENTE = ("não há dado", "sem dado", "não disponível", "não foi registrad", "indisponível", "não há registro")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def frases(texto: str) -> int:
    """Quantidade de frases. Ponto entre digitos ("9.5") nao fecha frase."""
    limpo = re.sub(r"(?<=\d)[.,](?=\d)", "", texto.strip())
    achadas = FRASE.findall(limpo + ("" if limpo.endswith((".", "!", "?")) else "."))
    return len([f for f in achadas if f.strip(" .!?\n")])


def tem_markdown(texto: str) -> bool:
    for linha in texto.splitlines():
        crua = linha.strip()
        if crua.startswith(("#", "- ", "* ", "• ")) or re.match(r"^\d+[.)]\s", crua) or "**" in crua:
            return True
    return False


def _valores(obj: Any) -> set[float]:
    """Todo numero que a entrada contem (inclusive dentro de strings como
    "45.36" ou "31,5kg"), como float SEM arredondar: a comparacao arredonda os
    dois lados ao numero de casas que a resposta usou, entao um float longo
    citado inteiro ("26,666666666666664") tambem casa."""
    texto = json.dumps(obj, ensure_ascii=False, default=str)
    valores: set[float] = set()
    for bruto in re.findall(r"\d+(?:[.,]\d+)?", texto):
        try:
            valores.add(float(bruto.replace(",", ".")))
        except ValueError:
            continue
    return valores


def _leituras(token: str) -> list[tuple[float, int]]:
    """Leituras possiveis de um numero da resposta, como (valor, casas).

    - ponto E virgula: o ultimo separador e o decimal ("41.112,5" pt-BR =
      41112.5; "41,112.5" = 41112.5);
    - o mesmo separador mais de uma vez: so milhar ("1.234.567");
    - um separador so: decimal ("26,6", "9.5") e, se a parte final tiver 3
      digitos, tambem milhar ("13.894" = 13894).
    """
    seps = [c for c in token if c in ".,"]
    if not seps:
        return [(float(token), 0)]
    if len(set(seps)) == 2:
        decimal = seps[-1]
        milhar = "," if decimal == "." else "."
        inteiro, _, fracao = token.rpartition(decimal)
        partes = inteiro.split(milhar)
        if any(len(p) != 3 for p in partes[1:]):
            return []
        return [(float("".join(partes) + "." + fracao), len(fracao))]
    partes = re.split(r"[.,]", token)
    milhar_ok = all(len(p) == 3 for p in partes[1:])
    leituras = []
    if len(partes) == 2:
        leituras.append((float(f"{partes[0]}.{partes[1]}"), len(partes[1])))
    if milhar_ok:
        leituras.append((float("".join(partes)), 0))
    return leituras


def numeros_fora_da_entrada(resposta: str, entrada: Any) -> list[str]:
    """Numeros decimais/milhar da resposta que NAO existem na entrada, nem
    arredondados ao mesmo numero de casas, em nenhuma leitura possivel
    (decimal pt-BR "26,6", milhar "13.894", milhar com decimal pt-BR
    "41.112,5"). E o sinal de conta feita pelo modelo - o que todos os prompts
    proibem.

    `entrada` e a entrada JA RESOLVIDA (`llm_casos.carregar` troca os
    `{"$compartilhado": ...}` pelo bloco real antes de o eval rodar)."""
    conhecidos = _valores(entrada)
    fora = []
    for token in NUMERO.findall(resposta):
        if any(any(round(v, casas) == round(valor, casas) for v in conhecidos) for valor, casas in _leituras(token)):
            continue
        fora.append(token)
    return sorted(set(fora))


def _secoes_markdown(texto: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual = None
    for linha in texto.splitlines():
        cabecalho = re.match(r"^\s*##\s+(.+?)\s*$", linha)
        if cabecalho:
            atual = _sem_acento(cabecalho.group(1))
            secoes[atual] = ""
        elif atual is not None:
            secoes[atual] += linha + "\n"
    return secoes


# ---------------------------------------------------------------------------
# Por feature
# ---------------------------------------------------------------------------


def avaliar_set_evaluation(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    from app.exercise_set_evaluation_ai import SET_EVALUATION_CATEGORIES
    from app.intelligence.muscle_recovery_service import muscle_pain_blocks_direct_work

    problemas: list[str] = []
    bruto, final = saida["bruto"], saida["final"]
    expect = caso.get("expect") or {}
    payload = caso["input"]["payload"]
    # set-eval-v2 (IA-09): saida estruturada. O formato e garantido pelo schema
    # strict; categoria e sugestao ja chegam parseadas em `bruto`.
    categoria = bruto["category"]
    if categoria not in SET_EVALUATION_CATEGORIES:
        problemas.append(f"categoria fora do enum: {categoria}")
    if expect.get("categorias_aceitas") and categoria not in expect["categorias_aceitas"]:
        problemas.append(f"categoria {categoria} fora de {expect['categorias_aceitas']}")
    if categoria in (expect.get("categorias_proibidas") or []):
        problemas.append(f"categoria proibida: {categoria}")
    sugestao = {
        "reps": bruto.get("suggested_next_reps"),
        "load_kg": bruto.get("suggested_next_load_kg"),
        "rpe": bruto.get("suggested_next_rpe"),
    }
    # set-eval-v5: com dor muscular acima do limiar do motor de recuperacao a
    # IA pode mandar ENCERRAR o exercicio (next todo null).
    pode_encerrar = muscle_pain_blocks_direct_work(payload.get("muscle_pain"))
    if all(v is None for v in sugestao.values()) and not pode_encerrar:
        problemas.append("sem sugestao numerica para a proxima serie")
    teto = saida["composition"].ceiling
    if sugestao["rpe"] is not None and sugestao["rpe"] > teto:
        problemas.append(f"RPE sugerido {sugestao['rpe']} acima do teto do par ({teto}) - o clamp corrigiu")
    if any(a.get("reason") == "ENGINE_TARGET_LOAD" for a in final.get("load_adjustments") or []):
        problemas.append("carga sugerida acima da decisao do motor - o servidor corrigiu")
    executada = payload.get("weight_kg")
    if expect.get("carga_sugerida_ate_a_executada") and sugestao["load_kg"] is not None and executada is not None:
        if sugestao["load_kg"] > float(executada):
            problemas.append(f"carga sugerida {sugestao['load_kg']} acima da executada {executada} com dor")
    n = frases(bruto.get("note") or "")
    if not 1 <= n <= 3:
        problemas.append(f"orientacao com {n} frases (pedido: 1 a 3)")
    # set-eval-v4 (RPE 10 Lote 6/P9): a nota explica EM CIMA do status do motor,
    # com as mesmas palavras.
    rotulo = bruto.get("set_intensity_status_label")
    if rotulo and _sem_acento(rotulo) not in _sem_acento(bruto.get("note") or ""):
        problemas.append(f"nota nao usa o status do motor ({rotulo})")
    # set-eval-v6: com a regra da carga da proxima serie (`domain/next_set_load.py`)
    # a direcao e a carga sao do MOTOR; a IA tem de responder a mesma direcao
    # (categoria) e a mesma carga - senao a nota foi escrita para outra direcao.
    motor = saida.get("next_set")
    if motor is not None:
        if categoria != motor.direction:
            problemas.append(
                f"{DIRECAO_DIFERENTE_DO_MOTOR}: categoria da IA {categoria}, motor {motor.direction}"
            )
        carga_ia = sugestao["load_kg"]
        if motor.load_kg is not None and carga_ia is not None and abs(float(carga_ia) - motor.load_kg) > 1e-6:
            problemas.append(
                f"{DIRECAO_DIFERENTE_DO_MOTOR}: carga da IA {carga_ia} diferente da do motor {motor.load_kg} - o servidor corrigiu"
            )
        if carga_ia is None and not pode_encerrar:
            problemas.append(f"{DIRECAO_DIFERENTE_DO_MOTOR}: IA sem carga com o motor sugerindo {motor.load_kg}")
    # set-eval-v5: nota, categoria e carga concordam. Se o servidor trocou a
    # categoria pela direcao da carga final (`category_for_load`), a nota foi
    # escrita para outra direcao (ex.: "repita a carga" com a carga subindo).
    trocada = final.get("category_from_ai")
    if motor is None and trocada and trocada != final.get("category"):
        problemas.append(
            f"categoria da IA {trocada} trocada para {final.get('category')} pela carga final - nota contradiz a carga"
        )
    # set-eval-v5: a nota explica com palavras proprias - nao copia a linha de
    # status do prompt (fragmentos fixos dela, ou o separador da v4).
    copiados = [trecho for trecho in COPIA_DA_LINHA_DE_STATUS if trecho in _sem_acento(bruto.get("note") or "")]
    if copiados:
        problemas.append(f"nota copia a linha de status do motor ({', '.join(copiados)})")
    return problemas


def avaliar_exercise_summary(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    texto = saida["summary"]
    problemas: list[str] = []
    n = frases(texto)
    if not 2 <= n <= 4:
        problemas.append(f"{n} frases (pedido: 2 a 4)")
    if tem_markdown(texto):
        problemas.append("markdown/lista na resposta (pedido: texto corrido)")
    if re.search(r"\d+(?:[.,]\d+)?\s?kg", texto, re.IGNORECASE) or "%" in texto:
        problemas.append("sugere/repete carga ou percentual (pedido: nao sugerir carga, nao repetir numeros crus)")
    if (caso.get("expect") or {}).get("menciona_dor") and "dor" not in texto.lower():
        problemas.append("dor reportada nao mencionada")
    return problemas


def avaliar_workout_summary(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    texto = saida["summary"]
    problemas: list[str] = []
    n = frases(texto)
    if not 3 <= n <= 6:
        problemas.append(f"{n} frases (pedido: 3 a 6)")
    if tem_markdown(texto):
        problemas.append("markdown/lista na resposta (pedido: texto corrido)")
    if "%" in texto:
        problemas.append("percentual na resposta (pedido: nao sugerir aumento percentual)")
    fora = numeros_fora_da_entrada(texto, caso["input"])
    if fora:
        problemas.append(f"numeros que nao estao na entrada: {fora}")
    return problemas


def avaliar_insights(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    texto = saida["insight"]
    baixo = _sem_acento(texto)
    problemas: list[str] = []
    for secao in ("Diagnóstico", "Evidências", "Ajustes recomendados", "Próximo passo"):
        if _sem_acento(secao) not in baixo:
            problemas.append(f"secao ausente: {secao}")
    fora = numeros_fora_da_entrada(texto, caso["input"]["package"])
    if fora:
        problemas.append(f"numeros que nao estao no pacote: {fora}")
    for termo in CAUSALIDADE + FORA_DO_ESCOPO:
        if _sem_acento(termo) in baixo:
            problemas.append(f"termo proibido: {termo!r}")
    if (caso.get("expect") or {}).get("admite_dado_ausente") and not any(_sem_acento(m) in baixo for m in DADO_AUSENTE):
        problemas.append("dado ausente nao foi admitido")
    return problemas


def avaliar_workouts_analysis(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    texto = saida["insight"]
    problemas: list[str] = []
    esperadas = ["Evolução dos Treinos", "Volume e Frequência", "Destaques de Progressão", "Pontos de Atenção"]
    secoes = list(_secoes_markdown(texto))
    presentes = [s for s in (_sem_acento(e) for e in esperadas) if s in secoes]
    if presentes != [_sem_acento(e) for e in esperadas]:
        problemas.append(f"secoes ## esperadas {esperadas}, vieram {secoes}")
    fora = numeros_fora_da_entrada(texto, caso["input"]["training"])
    if fora:
        problemas.append(f"numeros que nao estao no JSON: {fora[:15]}")
    baixo = _sem_acento(texto)
    for termo in OFERTA_DE_ACAO:
        if _sem_acento(termo) in baixo:
            problemas.append(f"oferta/meta-comentario proibido: {termo!r}")
    return problemas


def avaliar_cardio_agent(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    """`cardio-agent-v3` (M98 C3a): o JSON estruturado depois da validacao do
    dono e do envelope. O eval mede o MODELO: sessao rejeitada pelo dono ou
    ajustada pelo envelope (HIIT vetado, zona/tempo acima do teto, tetos da
    semana) conta como problema, mesmo que o sistema tenha corrigido."""
    from app import cardio_recommendation_v3 as contract
    from app.domain import cardio_prescription

    rec = saida["recommendation_json"]
    problemas: list[str] = []
    if not (rec.get("summary") or "").strip():
        problemas.append("summary vazio")
    if rec.get("confidence") not in contract.CONFIDENCE_VALUES:
        problemas.append(f"confidence fora do contrato: {rec.get('confidence')!r}")
    for rejeitada in rec.get("rejected") or []:
        problemas.append(f"sessao rejeitada pelo motor: {rejeitada.get('code')} {rejeitada.get('detail') or ''}".strip())
    for ajuste in rec.get("adjustments") or []:
        problemas.append(f"envelope/limite ajustou a saida: {ajuste.get('code')} em {ajuste.get('date')}")
    horizonte = rec.get("horizon") or {}
    for sessao in rec.get("sessions") or []:
        erro = cardio_prescription.validation_error(sessao["prescription"])
        if erro:
            problemas.append(f"prescricao invalida em {sessao['date']}: {erro}")
        if not horizonte.get("date_from", "") <= sessao["date"] <= horizonte.get("date_to", "9999"):
            problemas.append(f"sessao fora do periodo: {sessao['date']}")
        if not (sessao.get("rationale") or "").strip():
            problemas.append(f"sessao sem justificativa em {sessao['date']}")
    entrada = caso["input"]
    if entrada.get("proposed_workout") and entrada.get("reference_date"):
        antes = [s["date"] for s in rec.get("sessions") or [] if s["date"] < entrada["reference_date"]]
        if antes:
            problemas.append(f"com plano planejado, sessao ANTES da ficha: {antes}")
    return problemas


def avaliar_periodization(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    """Regras DURAS do prompt conferidas na saida CRUA (antes dos auto-fixes
    do validador): o eval mede o modelo, nao o validador."""
    from app.periodization.limits import MAX_PRIORITIZED_PER_SLOT

    problemas: list[str] = []
    pedido = caso["input"]["request"]
    contexto = caso["input"]["context"]
    template = (saida.get("draft") or {}).get("template") or {}
    slots = template.get("slots") or []
    dias = pedido["days_per_cycle"]
    if template.get("volume_mode") != pedido["volume_mode"]:
        problemas.append(f"volume_mode {template.get('volume_mode')!r} != {pedido['volume_mode']!r}")
    if sorted(s.get("sequence") for s in slots) != list(range(1, dias + 1)):
        problemas.append(f"sequences {[s.get('sequence') for s in slots]} (esperado 1..{dias})")
    grupos = {g["id"]: g["name"] for g in contexto["muscle_groups"]}
    pares_por_grupo = {
        nome: {(row["mapping_id"], row["equipment_id"]) for row in rows}
        for nome, rows in (contexto.get("gym_catalog") or {}).items()
    }
    for slot in slots:
        itens = slot.get("items") or []
        rotulo = f"slot {slot.get('sequence')}"
        if slot.get("slot_type") == "descanso" and itens:
            problemas.append(f"{rotulo}: descanso com itens")
        priorizados = sum(1 for item in itens if item.get("prioritize"))
        if priorizados > MAX_PRIORITIZED_PER_SLOT:
            problemas.append(f"{rotulo}: {priorizados} priorizados (max {MAX_PRIORITIZED_PER_SLOT})")
        for item in itens:
            if item.get("muscle_group_id") not in grupos:
                problemas.append(f"{rotulo}: muscle_group_id inventado {item.get('muscle_group_id')}")
                continue
            if pedido["volume_mode"] == "equipment_series":
                par = (item.get("mapping_id"), item.get("equipment_id"))
                if item.get("item_kind") != "equipment" or par not in pares_por_grupo.get(grupos[item["muscle_group_id"]], set()):
                    problemas.append(f"{rotulo}: trio fora do catalogo {item.get('muscle_group_id')}/{par}")
                if not item.get("sets_count") or item["sets_count"] < 1:
                    problemas.append(f"{rotulo}: sets_count ausente")
                if item.get("rest_seconds") is not None:
                    problemas.append(f"{rotulo}: rest_seconds preenchido (regra: null)")
            else:
                minimo, maximo = item.get("priority_min_pct"), item.get("priority_max_pct")
                if item.get("item_kind") != "muscle_group" or minimo is None or maximo is None or not 0 <= minimo <= maximo <= 100:
                    problemas.append(f"{rotulo}: faixa percentual invalida {minimo}-{maximo}")
    problemas.extend(_avaliar_cardio_da_periodizacao(slots, caso))
    return problemas


def _avaliar_cardio_da_periodizacao(slots: list[Mapping[str, Any]], caso: Mapping[str, Any]) -> list[str]:
    """M98 C5: cardio da saida CRUA conferido pelos donos (`cardio_prescription`
    para a prescricao; `cardio_envelope` para HIIT x perna) e contra o que o
    caso espera (`expect.cardio`, `hiit_fora_da_perna_e_vespera` — desde
    27/10/2026 o envelope veta so a vespera do dia de perna —,
    `descanso_com_cardio`)."""
    from app.domain import cardio_envelope, cardio_prescription

    problemas: list[str] = []
    contexto = caso["input"]["context"]
    expect = caso.get("expect") or {}
    cardio = contexto.get("cardio")
    nomes = {g["id"]: g["name"] for g in contexto["muscle_groups"]}
    com_cardio = [s for s in slots if s.get("cardio_items")]
    if not cardio and com_cardio:
        problemas.append("cardio sem pedido de cardio")
    if expect.get("cardio") == "obrigatorio" and not com_cardio:
        problemas.append("pedido com cardio, saida sem nenhum cardio_items")
    if expect.get("cardio") == "proibido" and com_cardio:
        problemas.append(f"usuario recusou cardio, saida com cardio nos slots {[s.get('sequence') for s in com_cardio]}")
    for slot in com_cardio:
        itens = slot["cardio_items"]
        if len(itens) > cardio_envelope.SESSIONS_PER_DAY_MAX:
            problemas.append(f"slot {slot.get('sequence')}: {len(itens)} cardios (max {cardio_envelope.SESSIONS_PER_DAY_MAX})")
        for item in itens:
            erro = cardio_prescription.validation_error(item)
            if erro:
                problemas.append(f"slot {slot.get('sequence')}: cardio invalido pelo dono: {erro}")
    if expect.get("hiit_fora_da_perna_e_vespera") and cardio:
        info = [
            {
                "sequence": s.get("sequence"),
                "weekday": s.get("weekday"),
                "muscle_groups": [nomes.get(i.get("muscle_group_id")) for i in s.get("items") or []],
            }
            for s in slots
            if s.get("sequence") is not None
        ]
        envelope, dia = cardio_envelope.build_template_envelope(
            info, cardio_envelope.template_signals((cardio.get("signals") or {}).get("tsb"))
        )
        hiit = cardio_prescription.CardioMode.HIIT.value
        for slot in com_cardio:
            dia_env = envelope.day(dia.get(slot.get("sequence")))
            for item in slot["cardio_items"]:
                if item.get(cardio_prescription.MODE_FIELD) == hiit and dia_env is not None and not dia_env.allows(hiit):
                    problemas.append(f"slot {slot.get('sequence')}: HIIT vetado pelo envelope ({', '.join(dia_env.reasons)})")
    if expect.get("descanso_com_cardio"):
        descansos = [s for s in slots if s.get("slot_type") == "descanso"]
        if not any(s.get("cardio_items") and not s.get("items") for s in descansos):
            problemas.append("nenhum slot de descanso com cardio (e sem exercicio)")
    return problemas


def avaliar_exercise_description(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    from app.exercise_description_ai import MAX_EXECUTION_STEPS

    texto = saida["text"]
    problemas: list[str] = []
    if re.search(r"\d+\s*(s[ée]ries?|repeti[çc][õo]es|reps?\b|kg\b)|\bRPE\b", texto, re.IGNORECASE):
        problemas.append("prescreve series/reps/carga/RPE (proibido: vem da ficha)")
    execucao = texto.split("Execução:", 1)[-1].split("\n\n", 1)[0]
    passos = [l for l in execucao.splitlines() if l.startswith("- ")]
    if not 2 <= len(passos) <= MAX_EXECUTION_STEPS:
        problemas.append(f"{len(passos)} passos de execucao (pedido: ate {MAX_EXECUTION_STEPS}, em ordem)")
    return problemas


def _itens_da_leitura(saida: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list((saida.get("resultado") or {}).get("items") or [])


def _casa_palavras(nome: str, palavras: Any) -> bool:
    from app.domain.nutrition import food_text

    normalizado = food_text.normalize_name(nome)
    return any(food_text.normalize_name(p) in normalizado for p in palavras or ())


def _gramas_da_proposta(item: Mapping[str, Any]) -> Any:
    from app.domain.nutrition import meal_math, meal_parse

    return meal_math.resolve_amount(item.get("quantity"), item.get("unit"), meal_parse.ESTIMATE_BASIS.value).amount


def classificar_item_da_leitura(item: Mapping[str, Any], esperado: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Como a proposta de UM item atende o esperado: `catalogo` (pré-seleção ou
    escolha com o alimento certo da TACO), `estimativa` (estimativa aceita) ou
    `falha`; e os problemas. A pré-seleção errada é sempre problema (é o erro
    que a pessoa não vê)."""
    from app.domain.nutrition import meal_math

    problemas: list[str] = []
    rotulo = esperado["rotulo"]
    aceitos = set(esperado.get("taco") or [])
    status = item.get("status")
    candidatos = {c.get("food_id") for c in item.get("candidates") or []}
    estimativa = item.get("estimate") or {}
    faixa = esperado.get("estimativa")
    energia = estimativa.get(meal_math.ENERGY_FIELD)
    estimativa_ok = faixa is not None and energia is not None and faixa[0] <= energia <= faixa[1]
    if status == "selected":
        if item.get("food_id") in aceitos:
            desfecho = "catalogo"
        else:
            desfecho = "falha"
            problemas.append(f"{rotulo}: pré-seleção errada (TACO {item.get('food_id')}, aceitos {sorted(aceitos)})")
    elif status == "choice" and candidatos & aceitos:
        desfecho = "catalogo"
    elif status in ("choice", "estimated") and estimativa_ok:
        desfecho = "estimativa"
    else:
        desfecho = "falha"
        detalhe = f"estimativa {energia} kcal fora de {faixa}" if faixa and energia is not None else "sem proposta aceita"
        problemas.append(f"{rotulo}: {status}, {detalhe} (candidatos {sorted(c for c in candidatos if c is not None)})")
    gramas = _gramas_da_proposta(item)
    minimo, maximo = esperado["gramas"]
    if gramas is None:
        problemas.append(f"{rotulo}: sem quantidade proposta")
    elif not minimo <= gramas <= maximo:
        problemas.append(f"{rotulo}: {gramas:g} g fora de [{minimo}, {maximo}]")
    return desfecho, problemas


def avaliar_parse_refeicao(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    """Leitura da refeição por IA (nutrição L5b), medida no RESULTADO do worker
    (IA + motor + catálogo TACO): cada item esperado aparece (palavras no nome
    lido) com uma proposta aceita (`classificar_item_da_leitura`) e a quantidade
    na faixa; nenhum item a mais (inclusive no texto sem comida); unidade do
    vocabulário do motor. Limiar do lote (EVALS.md): todos os casos sem problema
    e nenhuma pré-seleção errada; a taxa de casamento com a TACO é medida à parte
    (`resumo_parse_refeicao`)."""
    from app.domain.nutrition import meal_parse

    problemas: list[str] = []
    itens = _itens_da_leitura(saida)
    esperados = (caso.get("expect") or {}).get("itens") or []
    unidades = {u.value for u in meal_parse.AI_UNITS}
    usados: set[int] = set()
    for esperado in esperados:
        achado = next(
            (
                (posicao, item)
                for posicao, item in enumerate(itens)
                if posicao not in usados and _casa_palavras((item.get("ai") or {}).get("name") or "", esperado["palavras"])
            ),
            None,
        )
        if achado is None:
            problemas.append(f"{esperado['rotulo']}: ausente na leitura")
            continue
        posicao, item = achado
        usados.add(posicao)
        problemas.extend(classificar_item_da_leitura(item, esperado)[1])
    for posicao, item in enumerate(itens):
        nome = (item.get("ai") or {}).get("name")
        if posicao not in usados:
            problemas.append(f"item a mais: {nome!r}")
        unidade = (item.get("ai") or {}).get("unit")
        if unidade is not None and unidade not in unidades:
            problemas.append(f"{nome}: unidade fora do vocabulário ({unidade})")
    return problemas


def resumo_parse_refeicao(rodadas: list[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
    """Agregado do eval da leitura: casos sem problema, itens esperados com
    proposta aceita, quantos casaram com a TACO e as pré-seleções erradas."""
    casos_ok = itens = aceitos = catalogo = selecao_errada = 0
    for saida, caso in rodadas:
        casos_ok += not avaliar_parse_refeicao(saida, caso)
        lidos = _itens_da_leitura(saida)
        usados: set[int] = set()
        for esperado in (caso.get("expect") or {}).get("itens") or []:
            itens += 1
            achado = next(
                (
                    (p, i)
                    for p, i in enumerate(lidos)
                    if p not in usados and _casa_palavras((i.get("ai") or {}).get("name") or "", esperado["palavras"])
                ),
                None,
            )
            if achado is None:
                continue
            usados.add(achado[0])
            desfecho, problemas = classificar_item_da_leitura(achado[1], esperado)
            aceitos += desfecho != "falha"
            catalogo += desfecho == "catalogo"
            selecao_errada += any("pré-seleção errada" in p for p in problemas)
    return {
        "casos": len(rodadas),
        "casos_sem_problema": casos_ok,
        "itens_esperados": itens,
        "itens_com_proposta_aceita": aceitos,
        "itens_casados_com_a_taco": catalogo,
        "pre_selecoes_erradas": selecao_errada,
    }


AVALIADORES: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]]] = {
    "set_evaluation": avaliar_set_evaluation,
    "exercise_summary": avaliar_exercise_summary,
    "workout_summary": avaliar_workout_summary,
    "insights": avaliar_insights,
    "workouts_analysis": avaliar_workouts_analysis,
    "cardio_agent": avaliar_cardio_agent,
    "periodization": avaliar_periodization,
    "exercise_description": avaliar_exercise_description,
    "meal_parse": avaliar_parse_refeicao,
}


def texto_da_saida(feature: str, saida: Mapping[str, Any]) -> str:
    """O texto gerado, para imprimir no relatorio do eval."""
    if feature == "set_evaluation":
        return saida["bruto"].get("raw_text") or ""
    if feature == "periodization":
        return json.dumps(saida.get("draft"), ensure_ascii=False, indent=1)
    if feature == "meal_parse":
        return json.dumps(
            [
                {**(i.get("ai") or {}), "status": i.get("status"), "food_id": i.get("food_id"),
                 "quantity": i.get("quantity"), "unit_proposta": i.get("unit"), "estimate": i.get("estimate")}
                for i in _itens_da_leitura(saida)
            ],
            ensure_ascii=False,
            indent=1,
        )
    for chave in ("summary", "insight", "recommendation_markdown", "text"):
        if chave in saida:
            return str(saida[chave])
    return json.dumps(saida, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Nutricao L6: leitura da foto do rotulo (eval proprio,
# `tests/eval/test_nutrition_label_eval.py`; fora de `AVALIADORES`, que e o
# conjunto do IA-11 com snapshot de texto)
# ---------------------------------------------------------------------------


def _numeros_da_tabela(tabela: Mapping[str, Any]) -> set[float]:
    """Todo numero impresso na tabela sintetica do caso (o que a IA poderia ter lido)."""
    return _valores([valores for _nome, valores, _recuo in tabela.get("linhas") or []] + [tabela.get("porcao")])


def _igual_na_escala(lido: Any, esperado: Any, casas: int) -> bool:
    if isinstance(esperado, bool) or not isinstance(esperado, (int, float)):
        return lido == esperado
    return isinstance(lido, (int, float)) and round(float(lido), casas) == round(float(esperado), casas)


def avaliar_rotulo(saida: Mapping[str, Any], caso: Mapping[str, Any]) -> list[str]:
    """Leitura da foto do rotulo, conferida no RESULTADO do worker (depois da
    revisao do motor `label_photo.review_reading`), nao no JSON cru: e o que a
    tela pre-preenche. Criterios objetivos: legibilidade aceita; cada valor
    esperado do formulario e da porcao na escala de `PREFILL_DECIMALS`; nenhum
    erro do catalogo nem aviso de porcao x por 100 numa tabela coerente; avisos
    obrigatorios (ex.: valor derivado da porcao); codigo de barras e nome; e, na
    foto borrada, nenhum numero que nao esteja impresso na tabela."""
    from app.domain.nutrition.label_photo import PREFILL_DECIMALS, WARN_LABEL_SERVING_INCONSISTENT

    resultado = saida["resultado"]
    expect = caso.get("expect") or {}
    problemas: list[str] = []
    if resultado["legibility"] not in expect.get("legibilidade_aceita", []):
        problemas.append(f"legibilidade {resultado['legibility']!r} fora de {expect.get('legibilidade_aceita')}")
    for bloco in ("food", "per_serving"):
        for campo, esperado in (expect.get(bloco) or {}).items():
            lido = (resultado.get(bloco) or {}).get(campo)
            if not _igual_na_escala(lido, esperado, PREFILL_DECIMALS):
                problemas.append(f"{bloco}.{campo}: lido {lido!r}, esperado {esperado!r}")
    codigos = [aviso["code"] for aviso in resultado.get("warnings") or []]
    if expect.get("food"):
        if resultado.get("errors"):
            problemas.append(f"erros do catalogo: {[erro['code'] for erro in resultado['errors']]}")
        inconsistentes = [c for c in codigos if c.startswith(WARN_LABEL_SERVING_INCONSISTENT)]
        if inconsistentes:
            problemas.append(f"porcao x por 100 inconsistente numa tabela coerente: {inconsistentes}")
    for obrigatorio in expect.get("avisos_obrigatorios") or []:
        if obrigatorio not in codigos:
            problemas.append(f"aviso esperado ausente: {obrigatorio}")
    if "barcode" in expect and resultado.get("barcode") != expect["barcode"]:
        problemas.append(f"codigo de barras {resultado.get('barcode')!r}, esperado {expect['barcode']!r}")
    if expect.get("nome_contem") and _sem_acento(expect["nome_contem"]) not in _sem_acento(
        str((resultado.get("food") or {}).get("name") or "")
    ):
        problemas.append(f"nome {(resultado.get('food') or {}).get('name')!r} sem {expect['nome_contem']!r}")
    if expect.get("sem_valor_inventado") and caso.get("desenho"):
        impressos = _numeros_da_tabela(caso["desenho"]["tabela"])
        lidos = [v for v in (saida.get("bruto") or {}).get("per_100", {}).values() if isinstance(v, (int, float))]
        lidos += [v for v in (saida.get("bruto") or {}).get("per_serving", {}).values() if isinstance(v, (int, float))]
        inventados = sorted({v for v in lidos if v not in impressos})
        if inventados:
            problemas.append(f"numeros que nao estao impressos na tabela: {inventados}")
    return problemas
