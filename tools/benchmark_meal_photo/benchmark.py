"""Benchmark de modelos da foto do prato (issue #326) com fotos públicas do Nutrition5k.

Chama a MESMA leitura de produção (`nutrition_meal_photo_ai.read_meal_photo` + a revisão do
motor `meal_photo.review_reading`) trocando só provedor/modelo pelas envs que o adaptador já
lê (`llm_anthropic`: `LLM_PROVIDER_MEAL_PHOTO`, `ANTHROPIC_MODEL_MEAL_PHOTO`,
`ANTHROPIC_EFFORT_MEAL_PHOTO`; OpenAI: `OPENAI_NUTRITION_VISION_MODEL`), no processo deste
script. O fallback é desligado aqui para que cada linha meça o modelo pedido.

Nutrientes: com `--dev-env-file` (MySQL do compose local, só DEV) o casamento é o do worker
(`nutrition_meal_parse_worker.match_items` + `meal_parse.build_result`) e o item escolhido usa
o catálogo; sem ele (ou item sem alimento escolhido), a estimativa por 100 g da própria IA. Em
ambos o cálculo é do motor `meal_math`. Nada é gravado em banco.

Dataset: Nutrition5k (Google, CC BY 4.0), https://github.com/google-research-datasets/Nutrition5k,
arquivos em `URL_BASE`. Os arquivos baixados são dados não confiáveis: só bytes PNG (convertidos
para JPEG em memória pelo Pillow) e CSV/texto; nada é executado.

Uso: veja `tools/benchmark_meal_photo/README.md`.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

AQUI = Path(__file__).resolve().parent
API_DIR = AQUI.parents[1] / "api"
for caminho in (API_DIR, AQUI):
    if str(caminho) not in sys.path:
        sys.path.insert(0, str(caminho))

import metricas  # noqa: E402

URL_BASE = "https://storage.googleapis.com/nutrition5k_dataset/nutrition5k_dataset"
ARQUIVOS_METADADOS = ("metadata/dish_metadata_cafe1.csv", "metadata/dish_metadata_cafe2.csv")
# Pratos com a foto de cima (overhead RGB) do split de teste.
ARQUIVO_IDS = "dish_ids/splits/rgb_test_ids.txt"
URL_IMAGEM = "imagery/realsense_overhead/{dish}/rgb.png"
MAX_BYTES_TEXTO = 50 * 1024 * 1024
MAX_BYTES_IMAGEM = 10 * 1024 * 1024
MAX_PIXELS_IMAGEM = 4_000_000  # o dataset tem 640x480
# Banco do compose local (.env.example); qualquer outro é recusado.
DEV_BANCO = "fitness"
DEV_PORTA = "3306"
DISH_ID = re.compile(r"dish_\d{6,12}")  # usar com fullmatch
MAX_AMOSTRA = 100
QUALIDADE_JPEG = 90
FEATURE = "meal_photo"

# shortcut: estimativa grosseira de tokens só para o --dry-run (chars/4 para o texto; imagem pela
# fórmula da Anthropic largura*altura/750 com a foto de 640x480 do dataset); a medição real usa os
# tokens devolvidos pelo provedor.
CARACTERES_POR_TOKEN = 4
TOKENS_IMAGEM_ESTIMADOS = 640 * 480 // 750


def _pasta_cache_padrao() -> Path:
    if os.environ.get("MUSCLEFIT_BENCHMARK_CACHE"):
        return Path(os.environ["MUSCLEFIT_BENCHMARK_CACHE"])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "musclefit-benchmark"
    return Path.home() / ".cache" / "musclefit-benchmark"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark da foto do prato (Nutrition5k).")
    p.add_argument("--amostra", type=int, default=30, help="pratos sorteados (padrão 30)")
    p.add_argument("--semente", type=int, default=326)
    p.add_argument("--configs", default="haiku,sonnet,openai", help="subconjunto de haiku,sonnet,openai")
    p.add_argument("--cache-dir", type=Path, default=None, help="fora do repositório (padrão: LOCALAPPDATA ou ~/.cache)")
    p.add_argument("--saida", type=Path, default=None, help="pasta do JSON e do markdown (padrão: <cache>/resultados/<data>)")
    p.add_argument("--massa-min-visivel", type=float, default=metricas.MASSA_MIN_VISIVEL_G)
    p.add_argument("--dev-env-file", default=None, help="`.env` do compose local: liga o casamento com o catálogo do DEV")
    p.add_argument("--user-id", type=int, default=1, help="usuário do DEV para a visibilidade do catálogo")
    p.add_argument("--dry-run", action="store_true", help="valida e estima custo; sem rede e sem LLM")
    return p.parse_args()


def _carregar_env_dev(caminho: str) -> None:
    """Igual a `tools/medir_modo_contagem_volume.py`: só `MYSQL_*` do `.env` do compose, MySQL
    local em 127.0.0.1 e backend pymysql. Roda antes de importar o app."""
    valores: dict[str, str] = {}
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        chave, sep, valor = linha.strip().partition("=")
        if sep and chave.startswith("MYSQL_"):
            valores[chave] = valor.strip().strip('"').strip("'")
    porta = valores.get("MYSQL_PORT", DEV_PORTA)
    banco = valores.get("MYSQL_DATABASE")
    if any("prod" in v.lower() for v in valores.values()) or banco != DEV_BANCO or porta != DEV_PORTA:
        sys.exit(f"ERRO: {caminho} não é o MySQL do compose local (esperado 127.0.0.1:{DEV_PORTA}/{DEV_BANCO}; "
                 f"veio porta {porta}, banco {banco}, ou há 'prod' em algum valor MYSQL_*). Nada foi conectado.")
    os.environ.update(valores)
    os.environ["DB_BACKEND"] = "pymysql"
    os.environ["MYSQL_HOST"] = "127.0.0.1"
    os.environ["MYSQL_PORT"] = porta
    print(f"DEV: conectará a 127.0.0.1:{porta}/{banco}")


ARGS = _args() if __name__ == "__main__" else None
if ARGS is not None and ARGS.dev_env_file:
    _carregar_env_dev(ARGS.dev_env_file)

from app import llm_anthropic, nutrition_meal_parse_worker, nutrition_meal_photo_ai, openai_client  # noqa: E402
from app.domain.nutrition import ai_usage, meal_math, meal_parse, meal_photo  # noqa: E402


# ---------------------------------------------------------------------------
# Configurações (só envs que o adaptador já lê)
# ---------------------------------------------------------------------------


def configuracoes() -> dict[str, dict[str, Any]]:
    env = lambda prefixo: llm_anthropic.feature_env(prefixo, FEATURE)  # noqa: E731
    anthropic = lambda modelo: {  # noqa: E731
        env(llm_anthropic.ENV_PROVIDER_PREFIX): llm_anthropic.PROVIDER_ANTHROPIC,
        env(llm_anthropic.ENV_MODEL_PREFIX): modelo,
        env(llm_anthropic.ENV_EFFORT_PREFIX): llm_anthropic.EFFORT_MEDIUM,
    }
    return {
        "haiku": {"rotulo": f"{llm_anthropic.ANTHROPIC_MODEL_HAIKU} esforço {llm_anthropic.EFFORT_MEDIUM} (atual)",
                  "modelo": llm_anthropic.ANTHROPIC_MODEL_HAIKU, "chave": llm_anthropic.ENV_API_KEY,
                  "env": anthropic(llm_anthropic.ANTHROPIC_MODEL_HAIKU)},
        "sonnet": {"rotulo": f"{llm_anthropic.ANTHROPIC_MODEL_SONNET} esforço {llm_anthropic.EFFORT_MEDIUM}",
                   "modelo": llm_anthropic.ANTHROPIC_MODEL_SONNET, "chave": llm_anthropic.ENV_API_KEY,
                   "env": anthropic(llm_anthropic.ANTHROPIC_MODEL_SONNET)},
        "openai": {"rotulo": f"{nutrition_meal_photo_ai.model_name()} (fallback configurado)",
                   "modelo": nutrition_meal_photo_ai.model_name(), "chave": "OPENAI_API_KEY",
                   "env": {env(llm_anthropic.ENV_PROVIDER_PREFIX): llm_anthropic.PROVIDER_OPENAI}},
    }


def aplicar_config(config: dict[str, Any]) -> None:
    """Só no processo: provedor/modelo/esforço da configuração e fallback desligado."""
    for chave, valor in config["env"].items():
        os.environ[chave] = valor
    os.environ[llm_anthropic.feature_env(llm_anthropic.ENV_FALLBACK_PREFIX, FEATURE)] = ""
    os.environ.pop(llm_anthropic.ENV_FALLBACK_GLOBAL, None)


# ---------------------------------------------------------------------------
# Dataset (dados não confiáveis)
# ---------------------------------------------------------------------------


def _baixar(relativo: str, destino: Path, limite: int) -> Path:
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{URL_BASE}/{relativo}", timeout=60) as resposta:  # noqa: S310 (URL fixa https)
        if not resposta.geturl().startswith("https://storage.googleapis.com/"):  # redirecionamento inesperado
            raise ValueError(f"{relativo}: resposta fora de https://storage.googleapis.com")
        dados = resposta.read(limite + 1)
    if len(dados) > limite:
        raise ValueError(f"{relativo}: maior que {limite} bytes")
    temporario = destino.with_suffix(destino.suffix + ".part")
    temporario.write_bytes(dados)
    temporario.replace(destino)
    return destino


def _numero(texto: str) -> float:
    valor = float(texto)
    if valor != valor or valor < 0 or valor == float("inf"):
        raise ValueError(texto)
    return valor


def ler_metadados(caminhos: list[Path]) -> dict[str, dict[str, Any]]:
    """Linha do CSV: dish_id, kcal, massa, gordura, carbo, proteína e, por ingrediente,
    (id, nome, gramas, kcal, gordura, carbo, proteína). Linha malformada é ignorada."""
    pratos: dict[str, dict[str, Any]] = {}
    for caminho in caminhos:
        with caminho.open(encoding="utf-8", newline="") as arquivo:
            for linha in csv.reader(arquivo):
                try:
                    if not DISH_ID.fullmatch(linha[0]) or (len(linha) - 6) % 7:
                        continue
                    kcal, massa, gordura, carbo, proteina = (_numero(v) for v in linha[1:6])
                    ingredientes = [
                        {"nome": linha[i + 1][:80], "gramas": _numero(linha[i + 2])} for i in range(6, len(linha), 7)
                    ]
                except (ValueError, IndexError):
                    continue
                pratos[linha[0]] = {
                    "massa_total": massa, "ingredientes": ingredientes,
                    "totais": {"kcal": kcal, "proteina_g": proteina, "carbo_g": carbo, "gordura_g": gordura},
                }
    return pratos


def sortear(ids: list[str], pratos: dict[str, Any], quantidade: int, semente: int) -> list[str]:
    elegiveis = sorted(i for i in ids if i in pratos and pratos[i]["ingredientes"])
    return random.Random(semente).sample(elegiveis, min(quantidade, len(elegiveis)))


def foto_jpeg(caminho_png: Path) -> bytes:
    from PIL import Image  # já é dependência do projeto (requirements-ai/dev)

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS_IMAGEM
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)  # aviso vira erro já no open
        imagem = Image.open(io.BytesIO(caminho_png.read_bytes()))
    with imagem:
        if imagem.width * imagem.height > MAX_PIXELS_IMAGEM:
            raise ValueError("imagem grande demais")
        if imagem.format != "PNG":
            raise ValueError("não é PNG")
        saida = io.BytesIO()
        imagem.convert("RGB").save(saida, format="JPEG", quality=QUALIDADE_JPEG)
    dados = saida.getvalue()
    erro = meal_photo.upload_error(len(dados), meal_photo.MEAL_PHOTO_CONTENT_TYPE)
    if erro:
        raise ValueError(erro)
    return dados


# ---------------------------------------------------------------------------
# Leitura e nutrientes
# ---------------------------------------------------------------------------


def _nutrientes_item(per_100: meal_math.Nutrients, gramas: float, base: Any) -> Optional[meal_math.Nutrients]:
    resolvido = meal_math.resolve_amount(gramas, meal_math.Unit.GRAM, base)
    item = meal_math.item_nutrition(per_100, resolvido)
    return item.nutrients if item else None


def itens_com_nutrientes(revisao: meal_photo.MealPhotoReview, user_id: Optional[int]) -> list[dict[str, Any]]:
    """Por item: nome, gramas, fonte e nutrientes (catálogo do DEV se escolhido; senão a
    estimativa da IA; senão desconhecido)."""
    escolhidos: dict[int, dict[str, Any]] = {}
    if user_id is not None and revisao.items:
        rankings, linhas = nutrition_meal_parse_worker.match_items(
            revisao.items, registrant_user_id=user_id, diary_user_id=user_id
        )
        decididos = meal_parse.build_result(revisao.items, rankings, revisao.limitations)
        for indice, item in enumerate(decididos.items):
            linha = linhas.get(int(item.food_id)) if item.food_id is not None else None
            if linha:
                escolhidos[indice] = linha
    saida = []
    for indice, item in enumerate(revisao.items):
        gramas = item.grams_estimate or 0.0  # o schema da IA exige grams_estimate (nutrition_meal_photo_ai), então 0 só ocorre se a IA devolver 0
        nutrientes, fonte = None, "desconhecido"
        if indice in escolhidos:
            linha = escolhidos[indice]
            nutrientes = _nutrientes_item(meal_math.nutrients_from_values(linha), gramas, linha.get("basis"))
            fonte = f"catalogo:{linha.get('name')}"
        if nutrientes is None and item.estimate is not None:
            nutrientes = _nutrientes_item(item.estimate, gramas, meal_math.Unit.GRAM)
            fonte = "estimativa_ia"
        saida.append({"nome": item.name, "nome_en": metricas.traduzir(item.name), "gramas": gramas,
                      "fonte": fonte, "nutrientes": nutrientes.to_dict() if nutrientes else None})
    return saida


def totais(itens: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    """Soma por campo; item sem o campo deixa o total desconhecido (nunca 0)."""
    campos = {"kcal": "energy_kcal", "proteina_g": "protein_g", "carbo_g": "carbohydrates_g", "gordura_g": "total_fat_g"}
    saida: dict[str, Optional[float]] = {}
    for chave, campo in campos.items():
        valores = [(i["nutrientes"] or {}).get(campo) for i in itens]
        saida[chave] = None if any(v is None for v in valores) else sum(valores)
    return saida


def ler_prato(foto: bytes, prato: dict[str, Any], config: dict[str, Any], user_id: Optional[int],
              massa_min: float) -> dict[str, Any]:
    registro: dict[str, Any] = {"ok": False, "schema_valido": None, "latencia_s": None, "custo_usd": None}
    inicio = time.perf_counter()
    try:
        leitura = nutrition_meal_photo_ai.read_meal_photo(foto)
    except nutrition_meal_photo_ai.MealPhotoJobError as exc:
        registro.update(latencia_s=time.perf_counter() - inicio, schema_valido=False, erro=exc.code, modelo=exc.model)
        registro["custo_usd"] = _custo(exc.model, exc.tokens_in, exc.tokens_out)
        return registro
    except openai_client.OpenAIGenerationError as exc:
        # Corte/JSON inválido e recusa = resposta ruim (schema False); rede, chave ausente, timeout,
        # autenticação, cobrança e 4xx/5xx ficam None (transporte, fora da taxa de schema).
        erro, schema = metricas.classificar_falha_ia(
            exc.code, str(exc), exc.diagnostic, llm_anthropic.ANTHROPIC_REFUSAL, openai_client.USER_MESSAGE_INVALID_RESPONSE)
        registro.update(latencia_s=time.perf_counter() - inicio, erro=erro, schema_valido=schema)
        return registro
    except Exception as exc:  # noqa: BLE001 (nenhuma falha pode perder o que a chamada paga já mediu)
        registro.update(latencia_s=time.perf_counter() - inicio, erro=type(exc).__name__)
        return registro
    registro.update(
        latencia_s=time.perf_counter() - inicio, schema_valido=True, modelo=leitura.model,
        tokens_in=leitura.tokens_in, tokens_out=leitura.tokens_out,
        custo_usd=_custo(leitura.model, leitura.tokens_in, leitura.tokens_out),
    )
    if not str(leitura.model or "").startswith(config["modelo"]):
        registro["aviso"] = f"respondeu {leitura.model}, esperado {config['modelo']}"
    try:
        revisao = meal_photo.review_reading(leitura.data)
        itens = itens_com_nutrientes(revisao, user_id)
        registro.update(
            ok=True, plate_status=revisao.status.value, itens=itens,
            metricas=metricas.avaliar_leitura(itens, prato["ingredientes"], totais(itens), prato["totais"],
                                              prato["massa_total"], massa_min),
        )
    except Exception as exc:  # noqa: BLE001
        registro.update(ok=False, erro=type(exc).__name__)
    return registro


def _custo(modelo: Any, tokens_in: Any, tokens_out: Any) -> Optional[float]:
    valor = ai_usage.estimate_cost_usd(modelo, tokens_in, tokens_out)
    return None if valor is None else float(valor)


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------


def _fmt(valor: Any, pct: bool = False) -> str:
    if valor is None:
        return "-"
    return f"{valor * 100:.0f}%" if pct else f"{valor:.1f}" if isinstance(valor, float) else str(valor)


def _dist(d: dict[str, Any], pct: bool = False) -> str:
    return " / ".join(_fmt(d[k], pct) for k in ("p50", "p90", "max"))


def markdown(resultado: dict[str, Any]) -> str:
    linhas = [
        f"# Benchmark foto do prato ({resultado['gerado_em']})", "",
        f"Prompt `{resultado['prompt_version']}`, {resultado['amostra']} pratos do Nutrition5k (semente "
        f"{resultado['semente']}), nutrientes: {resultado['fonte_nutrientes']}.", "",
        "Distribuições em p50 / p90 / máx.", "",
        "| Config | ok/total | schema válido | precisão | recall visível | erro massa total | erro kcal | "
        "erro P g | erro C g | erro G g | desconhecido kcal/P/C/G (de ok) | falhas por código | "
        "latência s p50/p95 | custo médio US$ | custo total US$ |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for nome, bloco in resultado["configs"].items():
        r = bloco.get("resumo")
        if not r:
            linhas.append(f"| {nome} | pulada: {bloco.get('pulada')} |" + " |" * 13)
            continue
        linhas.append(
            f"| {bloco['rotulo']} | {r['ok']}/{r['leituras']} | {_fmt(r['taxa_schema_valido'], True)} | "
            f"{_fmt(r['precisao_micro'], True)} | {_fmt(r['recall_visivel_micro'], True)} | "
            f"{_dist(r['erro_massa_total_rel'], True)} | {_dist(r['erro_kcal_rel'], True)} | "
            f"{_dist(r['erro_proteina_g_abs'])} | {_dist(r['erro_carbo_g_abs'])} | {_dist(r['erro_gordura_g_abs'])} | "
            f"{'/'.join(str(r['desconhecidos'][c]) for c in metricas.NUTRIENTES)} de {r['ok']} | "
            f"{', '.join(f'{k}: {v}' for k, v in r['falhas_por_codigo'].items()) or '-'} | "
            f"{_fmt(r['latencia_s']['p50'])} / {_fmt(r['latencia_s']['p95'])} | "
            f"{f"{r['custo_usd']['media']:.4f}" if r['custo_usd']['media'] is not None else '-'} | {r['custo_total_usd']:.4f} |"
        )
    linhas += ["", "Latência: geral (inclui falhas) na tabela; só leituras ok abaixo (p50 / p95).", ""]
    for nome, bloco in resultado["configs"].items():
        if bloco.get("resumo"):
            lat = bloco["resumo"]["latencia_ok_s"]
            linhas.append(f"- {nome}: {_fmt(lat['p50'])} / {_fmt(lat['p95'])} s")
    avisos = [f"- {n} `{l['dish_id']}`: {l['aviso']}" for n, b in resultado["configs"].items()
              for l in b.get("leituras", []) if l.get("aviso")]
    if avisos:
        linhas += ["", "## Avisos (modelo diferente do pedido)", "", *avisos]
    linhas += ["", "## Casos difíceis (pior erro de kcal)", ""]
    for nome, bloco in resultado["configs"].items():
        for caso in bloco.get("casos_dificeis", []):
            m = caso.get("metricas") or {}
            linhas.append(
                f"- {nome} `{caso['dish_id']}`: "
                + (f"falha `{caso.get('erro')}`" if not caso.get("ok") else
                   f"kcal {_fmt(m['nutrientes']['kcal']['erro_rel'], True)} (- = desconhecido, conta como pior), omitidos {m['omitidos']}, "
                   f"inventados {m['inventados']}")
            )
    return "\n".join(linhas) + "\n"


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def estimar_custo_teto(config: dict[str, Any], tokens_in: int) -> Optional[float]:
    """Teto por leitura: saída no teto da feature (`LLM_MAX_OUTPUT_TOKENS`, conta o raciocínio)."""
    return _custo(config["modelo"], tokens_in, openai_client.LLM_MAX_OUTPUT_TOKENS[FEATURE])


def main(args: argparse.Namespace) -> int:
    cache = (args.cache_dir or _pasta_cache_padrao()).resolve()
    if AQUI.parents[1] in (cache, *cache.parents):
        print(f"ERRO: o cache {cache} fica dentro do repositório; use --cache-dir fora dele.")
        return 2
    todas = configuracoes()
    nomes = [n.strip() for n in args.configs.split(",") if n.strip()]
    desconhecidas = [n for n in nomes if n not in todas]
    if desconhecidas or not 1 <= args.amostra <= MAX_AMOSTRA:
        print(f"ERRO: configs inválidas {desconhecidas} ou --amostra fora de 1 a {MAX_AMOSTRA} (pedido {args.amostra}).")
        return 2
    prompt = nutrition_meal_photo_ai.build_prompt()  # valida o template do submódulo privado
    tokens_in = len(prompt) // CARACTERES_POR_TOKEN + TOKENS_IMAGEM_ESTIMADOS
    ativas = {}
    for nome in nomes:
        config = todas[nome]
        teto = estimar_custo_teto(config, tokens_in)
        # Típico: a saída no maior observado herdado (`LLM_OUTPUT_OBSERVED_MAX_TOKENS`, sem raciocínio).
        tipico = _custo(config["modelo"], tokens_in, openai_client.LLM_OUTPUT_OBSERVED_MAX_TOKENS[FEATURE])
        chave_ok = os.environ.get(config["chave"], "").strip() not in ("", "replace_me")  # como a produção
        print(f"[{nome}] {config['rotulo']} | env {config['env']} | chave {config['chave']}: "
              f"{'presente' if chave_ok else 'AUSENTE, será pulada'} | típico/leitura US$ {tipico} | "
              f"teto/leitura US$ {teto} | "
              f"teto total US$ {None if teto is None else round(teto * args.amostra, 4)}")
        if chave_ok:
            ativas[nome] = config
    print(f"Prompt {nutrition_meal_photo_ai.PROMPT_VERSION}: ~{tokens_in} tokens de entrada estimados por leitura.")
    print(f"Cache: {cache} | nutrientes: {'catálogo do DEV' if args.dev_env_file else 'estimativa da IA (sem banco)'}")

    metadados = [cache / "nutrition5k" / Path(a).name for a in ARQUIVOS_METADADOS]
    ids_arquivo = cache / "nutrition5k" / Path(ARQUIVO_IDS).name
    if args.dry_run:
        faltam = [a for a, c in zip((*ARQUIVOS_METADADOS, ARQUIVO_IDS), (*metadados, ids_arquivo)) if not c.exists()]
        for relativo in faltam:
            print(f"baixaria {URL_BASE}/{relativo}")
        if not faltam:
            pratos = ler_metadados(metadados)
            ids = ids_arquivo.read_text(encoding="utf-8").split()
            for dish in sortear(ids, pratos, args.amostra, args.semente):
                foto = cache / "nutrition5k" / dish / "rgb.png"
                print(f"{'em cache' if foto.exists() else 'baixaria'} {URL_BASE}/{URL_IMAGEM.format(dish=dish)}")
        else:
            print(f"e depois {args.amostra} fotos {URL_BASE}/{URL_IMAGEM.format(dish='<dish_id>')}")
        print(f"chamadas de LLM: {args.amostra} x {list(ativas) or 'nenhuma config com chave'} (dry-run: nenhuma feita)")
        return 0

    saida = (args.saida or cache / "resultados" / datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    if AQUI.parents[1] in (saida, *saida.parents):
        print(f"ERRO: a saída {saida} fica dentro do repositório; use --saida fora dele.")
        return 2
    for relativo, destino in zip((*ARQUIVOS_METADADOS, ARQUIVO_IDS), (*metadados, ids_arquivo)):
        _baixar(relativo, destino, MAX_BYTES_TEXTO)
    pratos = ler_metadados(metadados)
    ids = ids_arquivo.read_text(encoding="utf-8").split()
    sorteados = sortear(ids, pratos, args.amostra, args.semente)
    fotos: dict[str, bytes] = {}
    for dish in sorteados:
        try:
            fotos[dish] = foto_jpeg(_baixar(URL_IMAGEM.format(dish=dish), cache / "nutrition5k" / dish / "rgb.png",
                                            MAX_BYTES_IMAGEM))
        except Exception as exc:  # foto ausente/ruim: o prato sai da amostra, com aviso
            print(f"aviso: {dish} sem foto utilizável ({type(exc).__name__})")
    user_id = args.user_id if args.dev_env_file else None
    resultado: dict[str, Any] = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"), "prompt_version": nutrition_meal_photo_ai.PROMPT_VERSION,
        "amostra": len(fotos), "semente": args.semente, "massa_min_visivel_g": args.massa_min_visivel,
        "fonte_nutrientes": "catálogo do DEV (estimativa da IA no item sem alimento)" if user_id else "estimativa da IA",
        "configs": {},
    }
    try:
        _executar(nomes, todas, ativas, fotos, pratos, user_id, args, resultado, saida)
    finally:  # JSON parcial mesmo com falha no meio (as chamadas já foram pagas)
        _gravar_json(resultado, saida)
    (saida / "resultado.md").write_text(markdown(resultado), encoding="utf-8")
    print(f"Saída: {saida}")
    return 0


def _gravar_json(resultado: dict[str, Any], saida: Path) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "resultado.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _executar(nomes: list[str], todas: dict[str, Any], ativas: dict[str, Any], fotos: dict[str, bytes],
              pratos: dict[str, Any], user_id: Optional[int], args: argparse.Namespace,
              resultado: dict[str, Any], saida: Path) -> int:
    for nome in nomes:
        config = todas[nome]
        if nome not in ativas:
            print(f"aviso: {nome} pulada, falta {config['chave']}")
            resultado["configs"][nome] = {"rotulo": config["rotulo"], "pulada": f"falta {config['chave']}"}
            continue
        aplicar_config(config)
        leituras: list[dict[str, Any]] = []
        # Lista viva: o JSON parcial já contém as leituras pagas se algo derrubar o processo.
        resultado["configs"][nome] = {"rotulo": config["rotulo"], "env": config["env"], "leituras": leituras}
        for dish, foto in fotos.items():
            leitura = {"dish_id": dish, **ler_prato(foto, pratos[dish], config, user_id, args.massa_min_visivel)}
            leituras.append(leitura)
            print(f"[{nome}] {dish} ok={leitura['ok']} {leitura.get('erro', '')} {_fmt(leitura['latencia_s'])}s"
                  + (f" AVISO: {leitura['aviso']}" if leitura.get("aviso") else ""))
        resultado["configs"][nome].update(
            resumo=metricas.resumir_config(leituras),
            casos_dificeis=[{k: c.get(k) for k in ("dish_id", "ok", "erro", "metricas")}
                            for c in metricas.casos_dificeis(leituras)],
        )
        _gravar_json(resultado, saida)
    return 0


if __name__ == "__main__":
    sys.exit(main(ARGS))
