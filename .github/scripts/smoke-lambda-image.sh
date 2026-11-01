#!/usr/bin/env bash
# Smoke de uma imagem Lambda de producao JA BUILDADA (sem AWS, sem banco, sem rede).
#
# Uso (a partir da raiz do repo):
#   .github/scripts/smoke-lambda-image.sh <Dockerfile> <imagem:tag>
#   ex.: .github/scripts/smoke-lambda-image.sh Dockerfile.ai mfsmoke-ai:local
#
# O que prova (ver docs/local-dev.md, secao "Smoke das imagens Lambda"):
#   1. todos os handlers que o Terraform (infra/*.tf) configura para a imagem
#      sao importaveis dentro dela, com o runtime da propria imagem e
#      `--network none` (nenhuma dependencia de rede/AWS em tempo de import);
#   2. a imagem nao carrega artefatos de dev (tests/, scripts/,
#      requirements-dev.txt, .env*, dumps/backups);
#   2b. do submodulo privado so os templates de prompt (privado/prompts/**/*.md,
#      M103 P1/P2) entram, e os builders das features com prompt renderizam;
#   3. imagens com Lambda Web Adapter tem a extensao em /opt/extensions;
#      e toda imagem declara USER sem privilegio e roda com uid != 0 (SEG-16);
#   4. o Pillow da imagem codifica WebP (cards ilustrados de exercicios sao
#      publicados em WebP) e, nas imagens da musclefit-api-core (Dockerfile,
#      onde a aprovacao publica o card) e do worker musclefit-ai-generators
#      (Dockerfile.ai, onde o card e COMPOSTO logo depois da OpenAI): o logo
#      oficial app/assets/branding/musclefit-logo.png esta presente, RGBA e
#      >= 1024 px; as fontes de app/assets/fonts/ (Oswald + Inter) carregam e
#      renderizam acentos sem cair no glifo .notdef; o mapa corporal v1
#      (app/assets/bodymap/v1/: arte, regions.json e mascaras) esta inteiro
#      (sha256 de cada arquivo = manifest) e renderiza (app.body_map_render e
#      app.muscle_map); e o compositor (app.exercise_card_composer) monta um
#      card de verdade, com a silhueta do rodape, a partir de uma ilustracao
#      sintetica;
#   5. Dockerfile.ai: /var/task/db/migrations bate 1:1 com api/db/migrations,
#      o runner (app.migrate) resolve esse diretorio e parseia todas as
#      migracoes, e o handler app.migrate.handler, invocado pelo Lambda Runtime
#      Interface Emulator com DB_BACKEND=pymysql, RECUSA executar.
#
# Nao faz push, nao usa credenciais, nao altera a imagem testada (o binario do
# RIE e copiado para um container descartavel, nunca para a imagem).
set -euo pipefail

# Git Bash no Windows converte "/var/task" em caminho do Windows sem isto.
export MSYS_NO_PATHCONV=1

DOCKERFILE="${1:?uso: $0 <Dockerfile> <imagem:tag>}"
IMAGE="${2:?uso: $0 <Dockerfile> <imagem:tag>}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
PY="$(command -v python3 || command -v python)"

RIE_VERSION="v1.37"
RIE_SHA256="6b1e686e62ab2baf5759c412c4864276ef2a88b094fca53ec070637ccba9b9a5"

# Mesmo mapeamento Dockerfile -> repositorio ECR do job backend-build de deploy-producao.yml.
case "$DOCKERFILE" in
  Dockerfile)         ECR_RESOURCE=musclefit_api_core ;;
  Dockerfile.engines) ECR_RESOURCE=musclefit_engines ;;
  Dockerfile.ai)      ECR_RESOURCE=musclefit_ai_generators ;;
  *) echo "Dockerfile desconhecido: $DOCKERFILE" >&2; exit 2 ;;
esac

CONTAINERS=()
cleanup() { for c in "${CONTAINERS[@]:-}"; do [ -n "$c" ] && docker rm -f "$c" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT

fail() { echo "::error::[$DOCKERFILE] $*" >&2; exit 1; }
step() { echo; echo "==> [$DOCKERFILE] $*"; }

# python dentro da imagem, sem rede e com o backend de producao (DB_BACKEND
# ausente = data-api, default de app/db.py). AWS_REGION e so o default da
# regiao, sem credencial nenhuma.
in_image() {
  # -i obrigatorio: sem ele scripts passados por heredoc (python -) recebem
  # stdin vazio e "passam" sem executar nada.
  docker run --rm -i --network none --entrypoint python \
    -e AWS_REGION=sa-east-1 "$IMAGE" "$@"
}

dump_container_logs() {
  echo "---- docker logs $1 ----" >&2
  docker logs "$1" >&2 2>&1 || true
  echo "---- fim docker logs $1 ----" >&2
}

# Invoca pelo RIE que roda no container $1 (payload JSON em $2) e imprime o
# corpo da resposta. Todo passo que invoca via RIE deve usar esta funcao.
# .github/scripts/rie_invoke.py vai por stdin (nada e copiado para a imagem),
# espera o RIE abrir a porta e so repete falha transitoria de transporte
# (502/503/504 sem errorType no corpo, conexao recusada/resetada); resposta do
# handler/runtime (200, ou erro com errorType, ex. 502 de init) e definitiva.
# Qualquer falha: mostra `docker logs` e encerra o smoke.
rie_invoke() {
  local ct="$1" payload="$2" out rc=0
  out="$(docker exec -i "$ct" python - "$payload" < "$REPO_ROOT/.github/scripts/rie_invoke.py")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    dump_container_logs "$ct"
    fail "invocacao via RIE em $ct falhou (rie_invoke.py saiu $rc; ver [rie_invoke] e docker logs acima)"
  fi
  printf '%s\n' "$out"
}

WORKDIR="$(docker image inspect -f '{{.Config.WorkingDir}}' "$IMAGE")"
ENTRYPOINT="$(docker image inspect -f '{{json .Config.Entrypoint}}' "$IMAGE")"
CMD_JSON="$(docker image inspect -f '{{json .Config.Cmd}}' "$IMAGE")"
echo "imagem=$IMAGE workdir=$WORKDIR entrypoint=$ENTRYPOINT cmd=$CMD_JSON"
docker image inspect -f 'arch={{.Architecture}} os={{.Os}} size={{.Size}}' "$IMAGE"
[ "$(docker image inspect -f '{{.Architecture}}' "$IMAGE")" = "amd64" ] \
  || fail "arquitetura da imagem diferente de amd64 (Lambdas de producao sao x86_64)"

# SEG-16: a imagem declara usuario sem privilegio (o Lambda ja roda com um
# usuario proprio de privilegio minimo; isto vale para docker run/scanners) e o
# processo de fato NAO e root. Todos os passos abaixo rodam com esse usuario,
# o que prova que codigo, libs e extensao sao legiveis por nao-root.
IMAGE_USER="$(docker image inspect -f '{{.Config.User}}' "$IMAGE")"
case "$IMAGE_USER" in
  ""|root|0|0:*|root:*) fail "imagem roda como root (USER='$IMAGE_USER'); esperado usuario sem privilegio (SEG-16)" ;;
esac
RUNTIME_UID="$(docker run --rm --network none --entrypoint id "$IMAGE" -u | tr -d '\r')"
[ "$RUNTIME_UID" != "0" ] || fail "processo dentro da imagem roda com uid 0 (SEG-16)"
echo "ok: USER=$IMAGE_USER uid=$RUNTIME_UID (nao root)"

# ---------------------------------------------------------------------------
step "1/5 handlers configurados no Terraform para aws_ecr_repository.$ECR_RESOURCE"
LAMBDAS="$("$PY" "$REPO_ROOT/.github/scripts/lambda_image_handlers.py" "$ECR_RESOURCE" --infra-dir "$REPO_ROOT/infra" | tr -d '\r')"
echo "$LAMBDAS"

TARGETS=()
while IFS=$'\t' read -r fn handler; do
  [ -z "$fn" ] && continue
  if [ -n "$handler" ]; then
    # image_config.command sobrescreve o CMD: so faz sentido com awslambdaric.
    case "$ENTRYPOINT" in *awslambdaric*) ;; *) fail "$fn define command=$handler mas a imagem nao usa awslambdaric (entrypoint=$ENTRYPOINT)";; esac
    TARGETS+=("$fn|handler|$handler")
  else
    case "$ENTRYPOINT" in
      *awslambdaric*)
        TARGETS+=("$fn|handler|$(echo "$CMD_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)[0])')") ;;
      *)
        # Imagens com Lambda Web Adapter: CMD = uvicorn <modulo>:<app>.
        ASGI="$(echo "$CMD_JSON" | "$PY" -c 'import json,sys; c=json.load(sys.stdin); assert c[0]=="uvicorn", c; print(c[1])')" \
          || fail "$fn sem command e CMD da imagem nao e uvicorn: $CMD_JSON"
        TARGETS+=("$fn|asgi|$ASGI") ;;
    esac
  fi
done <<< "$LAMBDAS"

for t in "${TARGETS[@]}"; do
  IFS='|' read -r fn kind target <<< "$t"
  if [ "$kind" = handler ]; then
    mod="${target%.*}"; attr="${target##*.}"
  else
    mod="${target%%:*}"; attr="${target##*:}"
  fi
  echo "-- $fn: import $mod ; $attr ($kind)"
  in_image -c "import importlib; obj = getattr(importlib.import_module('$mod'), '$attr'); assert callable(obj), '$target nao e chamavel'; print('ok', '$target')" \
    || fail "$fn: nao foi possivel importar $target dentro da imagem"
done

# ---------------------------------------------------------------------------
step "2/5 ausencia de artefatos de dev em $WORKDIR"
in_image - "$WORKDIR" <<'PYEOF' || fail "imagem contem artefatos de dev (lista acima)"
import fnmatch, os, sys
root = sys.argv[1]
forbidden_names = {"tests", "scripts", "requirements-dev.txt", "backups", "onprem-sync", "tools"}
forbidden_globs = [".env", ".env.*", "*.dump", "*.sql.gz", "conftest.py"]
bad = []
for dirpath, dirnames, filenames in os.walk(root):
    for name in dirnames + filenames:
        if name in forbidden_names or any(fnmatch.fnmatch(name, g) for g in forbidden_globs):
            bad.append(os.path.join(dirpath, name))
print("conteudo de topo:", sorted(os.listdir(root)))
if bad:
    print("PROIBIDO:", *bad, sep="\n  ")
    sys.exit(1)
print("ok: nenhum artefato de dev")
PYEOF

# ---------------------------------------------------------------------------
# M103 P1: os templates dos prompts de sistema vem do submodulo privado e sao a
# UNICA parte dele que entra na imagem. Prova: em $WORKDIR/privado so ha
# prompts/**/*.md (sem README, sem RAG) e os builders das features renderizam
# (template presente, nenhum placeholder sobrando). Nenhum texto e impresso.
step "2b/5 templates de prompt do submodulo privado (so privado/prompts)"
in_image - "$WORKDIR" <<'PYEOF' || fail "templates de prompt ausentes/incompletos ou conteudo do submodulo privado alem de privado/prompts (detalhe acima)"
import os, sys
root = os.path.join(sys.argv[1], "privado")
achados = [os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for d, _, fs in os.walk(root) for f in fs]
fora = [a for a in achados if not a.startswith("prompts/") or not a.endswith(".md") or a.endswith("README.md")]
if fora or not achados:
    print("privado/ deveria ter so prompts/**/*.md:", fora or "(vazio)")
    sys.exit(1)
from types import SimpleNamespace
from app import cardio_agent_ai, exercise_description_ai, nutrition_label_ai, nutrition_meal_parse_ai
from app import exercise_summary_ai, goal_prompt_context, insights_prompt, prompt_user_data, report_ai, workout_summary_ai
from app.intelligence.template_ai_selector_v2 import build_v2_selection_prompt
from app.domain.training_goal import GOAL_VALUES
from app.domain.training_safety import goal_disallowed_techniques
from app.engine_decision_context import EngineDecision
renderizados = {
    "cardio_agent": cardio_agent_ai.system_prompt(),
    "nutrition_label": nutrition_label_ai.build_prompt(),
    "nutrition_meal_parse": nutrition_meal_parse_ai.build_prompt("smoke", None),
    "exercise_description": exercise_description_ai.build_prompt({"name": "smoke", "equipment_names": ["x"]}, [{"muscle_group_name": "y"}])
    + exercise_description_ai.build_prompt({"name": "smoke"}, []),
    # M103 P2: insights, analise por IA, resumos e os fragmentos compartilhados
    # (objetivo/deload em todos os ramos e regra do dado do usuario).
    "insights": insights_prompt.build_insight_prompt({"question": "smoke"}, "rag", training_goals=GOAL_VALUES, deload=True)
    + insights_prompt.build_insight_prompt({"question": "smoke"}, ""),
    "workouts_analysis": report_ai.build_prompt({}, 30, "rag") + report_ai.build_prompt({}, 30, ""),
    "exercise_summary": exercise_summary_ai.build_prompt({"exercise_name": "smoke", "sets": [], "reps_target": "8", "notes": "n", "muscle_pain": 1, "training_goal": GOAL_VALUES[-1], "deload": True})
    + exercise_summary_ai.build_prompt({"exercise_name": "smoke", "sets": []})
    + exercise_summary_ai.build_prompt({"exercise_name": "smoke", "sets": [], "engine_decision": EngineDecision(decision="HOLD")}),
    "workout_summary": workout_summary_ai.build_prompt({"id": 1, "exercises": [], "deload_source": "manual", "duration_seconds": 60}),
    "goal_context": "".join(
        goal_prompt_context.goal_section(GOAL_VALUES, nomes)
        for nomes in (None, {c: "x" for c in sorted({c for g in GOAL_VALUES for c in goal_disallowed_techniques(g)})[:1]})
    )
    + goal_prompt_context.deload_section(True, prescription=SimpleNamespace(load_kg=1, reps_target=1, sets_count=1, target_rpe=1)),
    # Regra do dado do usuario: tambem lida pela avaliacao de serie e pela selecao legada v2.
    "prompt_user_data": prompt_user_data.user_data_rule(),
    "template_ai_selector_v2": build_v2_selection_prompt({"id": 1, "name": "smoke", "sequence": 1}, [], {}, ""),
}
# M103 P3: periodizacao (modos A/B, os dois modos de contagem, objetivo por dia,
# cardio em cada faixa do TSB, preferencias do usuario) e a ficha legada do Modelo.
from app.periodization import cardio_plan
from app.periodization.context import build_prompt as periodizacao
from app.workout_plan_templates_engine import build_template_prompt
from app.domain.training_goal import goal_label
def _periodizacao(modo, contagem=None, objetivo=None, tsb=False, origem=None, perfil=False, sem_tabela=False):
    pedido = {"volume_mode": modo, "days_per_cycle": 2}
    if contagem:
        pedido["volume_counting_mode"] = contagem
    contexto = {
        "request": pedido, "muscle_groups": [], "targets": [], "techniques": [{"code": "rest_pause", "activation_set_equivalent": 0.4, "max_equivalent_per_anchor": 1}],
        "methodology_ranges": [{"macro_grupo": "G", "frequency_bucket": "1x", "sets_per_session_min": 16, "sets_per_session_max": 20, "exercises_per_session_min": 4, "exercises_per_session_max": 5}],
        "methodology_targets": [{"g": 1}], "gym_catalog": {}, "rag_text": "rag", "instructions": "smoke",
    }
    if perfil:
        contexto["targets"] = [{"g": 1}]
    if sem_tabela:
        contexto["methodology_ranges"] = []
    if objetivo:
        contexto["goal_plan"] = {"template_goal": objetivo, "template_goal_label": goal_label(objetivo),
                                 "days": [{"sequence": 1, "training_goal": objetivo, "label": goal_label(objetivo)}]}
    if tsb is not False:
        contexto["cardio"] = {"source": origem, "signals": {"tsb": tsb}, "rag_text": "rag"}
    return periodizacao(contexto)
renderizados["periodization"] = "".join(
    _periodizacao(modo, contagem, objetivo)
    for modo in ("percent_budget", "equipment_series")
    for contagem in (None, "primary_secondary")
    for objetivo in (None, *GOAL_VALUES)
) + "".join(_periodizacao(modo, perfil=True, sem_tabela=True) for modo in ("percent_budget", "equipment_series"))
renderizados["periodization_cardio"] = "".join(
    _periodizacao(modo, tsb=tsb, origem=origem)
    for modo in ("percent_budget", "equipment_series")
    for tsb in (None, 10.0, -30.0, -40.0)
    for origem in (cardio_plan.SOURCE_FLAG, cardio_plan.SOURCE_INSTRUCTIONS)
)
renderizados["plan_templates"] = "".join(
    build_template_prompt({}, rag, "full_body", None, None, [{"muscle_group": "x", "dose_level": dose}])
    for rag in ("", "rag")
    for dose in ("FULL", "REDUCED_DOSE")
)
# M103 P4: avaliacao de serie (sem motor, sem decisao, decisao com carga, regra
# da proxima serie com carga/sem passo/rampa, dor alta, teto do par/recuperacao,
# status com evento) e a ilustracao do card (minimo, completo, com revisao).
from decimal import Decimal
from app import exercise_card_prompt, exercise_set_evaluation_ai as avaliacao
from app.domain.effective_intensity import SetIntensityReading
from app.domain.next_set_load import NextSetLoad
from app.domain.training_safety import IntensityComposition
from app.engine_decision_context import PairEngineContext
_serie = {"exercise_name": "smoke", "set_number": 3, "sets_count": 3, "reps": 10, "weight_kg": 60, "rpe": 9, "notes": "n"}
_motores = (None, PairEngineContext(step_kg=Decimal("2.5")),
            PairEngineContext(step_kg=Decimal("2.5"), decision=EngineDecision(decision="HOLD", load_kg=60)))
_composicoes = (None, IntensityComposition(ceiling=9, failure_allowed=False, target=8, failure_denied_by="PAIR"),
                IntensityComposition(ceiling=9, failure_allowed=False, target=8, failure_denied_by="RECOVERY"),
                IntensityComposition(ceiling=10, failure_allowed=True, target=9))
_leituras = (None, SetIntensityReading(status="REACHED", safety_event="RPE_ABOVE_SAFE_CEILING", rpe=9.0, target=9, ceiling=10,
                                       failure_allowed=True, reps=10, reps_min=8, reps_max=12),
             SetIntensityReading(status="REACHED", safety_event="RPE_ABOVE_RECOVERY_TARGET", rpe=9.0, target=9, ceiling=9,
                                 failure_allowed=False))
_proximas = (None, NextSetLoad(direction="INCREASE_LOAD", load_kg=62.5, reason="NEXT_SET_WITHIN_RULE", executed_kg=60, step_kg=2.5, planned_rpe=8.0),
             NextSetLoad(direction="DECREASE_LOAD", load_kg=None, reason="NEXT_SET_PAIN", executed_kg=5, step_kg=2.5))
renderizados["set_evaluation"] = "".join(
    avaliacao.build_prompt(dict(_serie, muscle_pain=dor), rag, engine=motor, composition=comp, set_reading=leitura, next_set=proxima)
    for dor in (None, 10)
    for rag in ("", "rag")
    for motor in _motores
    for comp in _composicoes
    for leitura in _leituras
    for proxima in _proximas
)
_card = exercise_card_prompt.ExerciseCardContext
renderizados["exercise_card"] = "".join(
    exercise_card_prompt.build_prompt(ctx, "minimal", instrucao, motivo)
    for ctx in (_card(exercise_id=1, name="smoke"),
                _card(exercise_id=2, name="smoke", name_en="smoke en", aliases=("um apelido",), equipment=("a", "b"),
                      load_conventions=("bodyweight", "bodyweight_plus", "assisted"), body_position="x", is_unilateral=True,
                      setup_steps=("s",), execution_steps=("e",), attention_points=("p",)),
                _card(exercise_id=3, name="smoke", equipment=("a",)))
    for instrucao, motivo in ((None, None), ("pedido", "motivo"))
)
# Secao do Intervals.icu da ficha legada (achada na varredura final do P4): sem dado e com dado.
from app.workout_plan_ai import format_intervals_wellness_section
renderizados["intervals_wellness"] = format_intervals_wellness_section(None) + format_intervals_wellness_section(
    {"wellness_date": "2026-10-01", "form_tsb": -5, "stale": False}
)
# Presenca e leitura de TODO fragmento das pastas do P3 e do P4, sem depender de ramo.
from app import prompt_store
for pasta in ("periodization", "periodization_cardio", "plan_templates", "template_selector", "set_evaluation", "exercise_card",
              "intervals_wellness"):
    arquivos = sorted((prompt_store.prompts_dir() / pasta).glob("*.md"))
    if not arquivos or not all(prompt_store.load(pasta, a.stem) for a in arquivos):
        print("templates ausentes ou vazios em", pasta)
        sys.exit(1)
ruins = sorted(nome for nome, texto in renderizados.items() if not texto or "{{" in texto)
if ruins:
    print("prompt vazio ou com placeholder:", ruins)
    sys.exit(1)
print(f"ok: {len(achados)} templates em privado/prompts; {len(renderizados)} features renderizam")
PYEOF

# ---------------------------------------------------------------------------
step "3/5 Lambda Web Adapter"
if [[ "$ENTRYPOINT" == *awslambdaric* ]]; then
  # Sem a extensao: com ela o LWA disputaria o loop da Runtime API com o awslambdaric.
  in_image -c "import os,sys; sys.exit(1 if os.path.exists('/opt/extensions/lambda-adapter') else 0)" \
    || fail "imagem awslambdaric contem /opt/extensions/lambda-adapter"
  echo "ok: imagem awslambdaric sem extensao LWA"
else
  in_image -c "import os,sys; p='/opt/extensions/lambda-adapter'; sys.exit(0 if os.path.isfile(p) and os.access(p, os.X_OK) else 1)" \
    || fail "extensao /opt/extensions/lambda-adapter ausente ou nao executavel"
  echo "ok: extensao LWA presente e executavel"
fi

# ---------------------------------------------------------------------------
step "4/5 Pillow com WebP, logo oficial, fontes e mapa corporal dos cards de exercicios"
# WebP: as 3 imagens instalam locks com a MESMA versao do Pillow (requirements*.txt; libwebp
# embutida no wheel); um encode real em memoria, alem do features.check,
# prova que o codec funciona dentro da imagem, sem rede.
# Logo + fontes + mapa corporal + compositor: obrigatorios na imagem da
# api-core (Dockerfile: a aprovacao publica o card e GET
# /workout-plans/{id}/muscle-map.png renderiza o mapa) e na do worker
# (Dockerfile.ai, o job exercise-card compoe o card - com a silhueta - logo
# depois da OpenAI). A engines copia o mesmo app/ mas nao usa nada disso.
REQUIRE_CARD_ASSETS=0
case "$DOCKERFILE" in Dockerfile|Dockerfile.ai) REQUIRE_CARD_ASSETS=1 ;; esac
in_image - "$WORKDIR" "$REQUIRE_CARD_ASSETS" <<'PYEOF' || fail "Pillow sem WebP, ou logo/fontes/mapa corporal/compositor dos cards ausentes ou quebrados na imagem (detalhe acima)"
import hashlib, io, json, os, sys
from pathlib import Path
import PIL
from PIL import Image, ImageDraw, ImageFont, features

workdir, require = sys.argv[1], sys.argv[2] == "1"
assert features.check("webp"), "PIL.features.check('webp') = False"
assert features.check("freetype2"), "PIL.features.check('freetype2') = False"
buf = io.BytesIO()
Image.new("RGBA", (8, 12), (255, 149, 0, 255)).save(buf, format="WEBP", quality=85)
assert buf.getvalue()[:4] == b"RIFF" and buf.getvalue()[8:12] == b"WEBP", "encode WebP invalido"
print("ok: Pillow", PIL.__version__, "codifica WebP e tem FreeType")

assets_dir = Path(workdir) / "app" / "assets"
logo = assets_dir / "branding" / "musclefit-logo.png"
fonts = sorted((assets_dir / "fonts").glob("*/*.ttf"))
bodymap_dir = assets_dir / "bodymap" / "v1"
if not require:
    print("logo/fontes/mapa corporal nao exigidos nesta imagem; logo presente:", logo.is_file(), "| fontes:", len(fonts),
          "| mapa corporal:", (bodymap_dir / "manifest.json").is_file())
    sys.exit(0)

assert logo.is_file(), f"logo ausente na imagem: {logo}"
with Image.open(logo) as im:
    assert im.format == "PNG" and im.mode == "RGBA", (im.format, im.mode)
    assert im.width >= 1024, im.size
    print("ok: logo", logo, im.mode, im.size)

expected = {"Oswald-Medium.ttf", "Oswald-SemiBold.ttf", "Oswald-Bold.ttf", "Inter-Regular.ttf", "Inter-SemiBold.ttf"}
names = {f.name for f in fonts}
assert expected <= names, f"fontes ausentes na imagem: {sorted(expected - names)} (achadas: {sorted(names)})"
for family in ("oswald", "inter"):
    assert (assets_dir / "fonts" / family / "OFL.txt").is_file(), f"OFL.txt de {family} ausente"
ACCENTS = "ÁÂÃÀÇÉÊÍÓÔÕÚáâãàçéêíóôõú"


def glyph(face, ch):
    """Pixels do caractere renderizado sozinho (API publica do Pillow)."""
    left, top, right, bottom = face.getbbox(ch)
    im = Image.new("L", (max(1, right - left) + 4, max(1, bottom - top) + 4), 0)
    ImageDraw.Draw(im).text((2 - left, 2 - top), ch, font=face, fill=255)
    return im.size, im.tobytes()


for path in fonts:
    face = ImageFont.truetype(str(path), 48, layout_engine=ImageFont.Layout.BASIC)
    notdef = glyph(face, "\ue000")  # code point que a fonte nao tem -> .notdef
    for ch in ACCENTS:
        rendered = glyph(face, ch)
        assert any(rendered[1]), f"{path.name}: '{ch}' sem tinta"
        assert rendered != notdef, f"{path.name}: '{ch}' caiu no glifo .notdef"
    print("ok: fonte", path.relative_to(assets_dir), face.getname(), "renderiza", ACCENTS)

# Mapa corporal v1: cada arquivo do pacote com o sha256 do manifest (arte
# WebP, regions.json - o mesmo da web - e as mascaras por slug).
manifest = json.loads((bodymap_dir / "manifest.json").read_text(encoding="utf-8"))
assert manifest.get("files"), "manifest do mapa corporal sem arquivos"
for name, meta in sorted(manifest["files"].items()):
    path = bodymap_dir / name
    assert path.is_file(), f"mapa corporal: {name} ausente na imagem"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == meta["sha256"], f"mapa corporal: {name} diverge do manifest"
masks = sorted(p.stem for p in (bodymap_dir / "masks").glob("*.png"))
regions = json.loads((bodymap_dir / "regions.json").read_text(encoding="utf-8"))["regions"]
assert masks and masks == sorted(regions), (masks, sorted(regions))
print("ok: mapa corporal", manifest["version"], "-", len(manifest["files"]), "arquivos conferem;", len(masks), "regioes")

os.chdir(workdir)
sys.path.insert(0, workdir)

# Render de teste do mapa (so Pillow, sem numpy na imagem): principal x
# secundario e o PNG do endpoint muscle-map.png.
from app import body_map_render as body_map
from app import muscle_map
figure = body_map.render_body_map(body_map.styles_for_roles(["chest"], ["triceps"]), width=420)
assert figure.mode == "RGBA" and figure.width == 420, (figure.mode, figure.size)
assert figure.getchannel("A").getbbox() is not None, "mapa corporal renderizou vazio"
plain = body_map.render_body_map({}, width=420)
assert figure.tobytes() != plain.tobytes(), "destaque nao mudou o mapa corporal"
mm = muscle_map.render_muscle_map_png({"chest": 2, "triceps": 1})
with Image.open(io.BytesIO(mm)) as im:
    assert (im.format, im.mode, im.width) == ("PNG", "RGB", 420), (im.format, im.mode, im.size)
print("ok: mapa corporal renderiza (", figure.size, ") e muscle-map.png", len(mm), "bytes")

# O compositor de verdade, com os assets da imagem e uma ilustracao sintetica
# (duas figuras em fundo transparente) - com slugs, para desenhar a silhueta
# do rodape (CARD_LAYOUT_V2).
from app import exercise_card_composer as composer
illustration = Image.new("RGBA", (1024, 1536), (0, 0, 0, 0))
draw = ImageDraw.Draw(illustration)
draw.rectangle((200, 200, 820, 560), fill=(120, 120, 128, 255))
draw.ellipse((380, 950, 640, 1380), fill=(205, 150, 120, 255))
png = io.BytesIO()
illustration.save(png, format="PNG")
assets = composer.load_card_assets()
content = composer.CardContent(
    "Extensão de tríceps na máquina (variação)", ("Tríceps",), ("Ombros",), ("triceps",), ("front-deltoids",)
)
card = composer.compose_card(png.getvalue(), content, assets)
with Image.open(io.BytesIO(card)) as im:
    assert (im.format, im.mode, im.size) == ("PNG", "RGB", (1024, 1536)), (im.format, im.mode, im.size)
    assert im.info == {}, im.info
    box = composer.card_layout(True).silhouette
    silhouette = im.crop((box.left, box.top, box.right, box.bottom))
    assert len(silhouette.getcolors(1 << 16) or ()) > 50, "silhueta do rodape nao foi desenhada"
print("ok: compositor", composer.LAYOUT_VERSION, "monta o card com a silhueta na imagem (", len(card), "bytes )")
PYEOF

# ---------------------------------------------------------------------------
if [ "$DOCKERFILE" != "Dockerfile.ai" ]; then
  step "5/5 (so Dockerfile.ai) pulado"
  echo; echo "SMOKE OK: $DOCKERFILE ($IMAGE)"
  exit 0
fi

step "5a migracoes: /var/task/db/migrations x api/db/migrations"
EXPECTED="$(ls -1 "$REPO_ROOT/api/db/migrations" | LC_ALL=C sort)"
ACTUAL="$(in_image -c "import os; print('\n'.join(sorted(os.listdir('/var/task/db/migrations'))))" | tr -d '\r' | LC_ALL=C sort)"
if ! diff <(echo "$EXPECTED") <(echo "$ACTUAL"); then
  fail "lista de migracoes na imagem diverge de api/db/migrations (diff acima: < repo, > imagem)"
fi
for n in $(seq -w 1 21); do
  echo "$ACTUAL" | grep -q "^0${n}_.*\.sql$" || fail "migracao 0${n}_*.sql ausente na imagem"
done
echo "$ACTUAL" | grep -qx "022_equipment_types_parity.py" || fail "022_equipment_types_parity.py ausente na imagem"
echo "ok: $(echo "$ACTUAL" | wc -l | tr -d ' ') migracoes, identicas ao repo"

step "5b runner resolve o diretorio e parseia todas as migracoes"
in_image -c "
from app import migrate as m
d = m._migrations_dir_from_env()
assert str(d) == '/var/task/db/migrations', d
ms = m.discover_migrations()
assert ms and all(len(x.checksum) == 64 for x in ms)
# Mesma regra de _run_one: baseline (<= BASELINE_MAX_VERSION) so e registrada,
# nunca parseada/executada; as demais precisam passar na validacao do runner.
for x in ms:
    if x.is_baseline:
        continue
    if x.kind == 'sql':
        m.validate_sql_statements(x.filename, m.split_sql_statements(x.path.read_text(encoding='utf-8')))
    else:
        m.load_python_migration(x.path)
print('ok:', len(ms), 'migracoes descobertas em', d, '- ultima:', ms[-1].filename)
" || fail "app.migrate nao consegue descobrir/parsear as migracoes da imagem"

step "5c handler via Lambda Runtime Interface Emulator com DB_BACKEND=pymysql (deve RECUSAR)"
# A base e python:3.12-slim + awslambdaric (nao public.ecr.aws/lambda/python),
# entao o RIE NAO vem na imagem: baixa o binario oficial pinado por versao +
# sha256 e o injeta so num container descartavel.
RIE_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/mfsmoke-rie-$RIE_VERSION"
RIE_BIN="$RIE_DIR/aws-lambda-rie"
if [ ! -f "$RIE_BIN" ] || ! echo "$RIE_SHA256  $RIE_BIN" | sha256sum -c --status; then
  mkdir -p "$RIE_DIR"
  # --retry: o download do GitHub Releases ja devolveu 504 transitorio no CI
  # (PRs #78/#84: "curl: (22) The requested URL returned error: 504" logo apos
  # o cabecalho do 4c - hoje 5c -, antes de o container do RIE existir). curl so repete
  # timeout, conexao recusada e HTTP 408/429/500/502/503/504 (nunca 404/403),
  # com backoff exponencial 1,2,4,8,16s.
  # O status final e conferido por --write-out e NAO pelo exit code: com
  # `-f --retry` ha versoes de curl (ex.: 8.14.1 do Git Bash) que imprimem
  # "(22) ... 404" e saem 0. O sha256 abaixo segue como prova de integridade.
  RIE_PART="$RIE_BIN.part"
  RIE_PART_CURL="$RIE_PART"
  # curl nativo do Git Bash nao entende "/tmp/..." com MSYS_NO_PATHCONV.
  command -v cygpath >/dev/null 2>&1 && RIE_PART_CURL="$(cygpath -w "$RIE_PART")"
  rm -f "$RIE_PART"
  HTTP_CODE="$(curl -sSL --retry 5 --retry-connrefused --retry-max-time 60 \
    --connect-timeout 10 --max-time 120 -o "$RIE_PART_CURL" -w '%{http_code}' \
    "https://github.com/aws/aws-lambda-runtime-interface-emulator/releases/download/$RIE_VERSION/aws-lambda-rie")" || true
  if [ "$HTTP_CODE" != "200" ]; then
    rm -f "$RIE_PART"
    fail "download do aws-lambda-rie $RIE_VERSION falhou apos retries (HTTP final: ${HTTP_CODE:-sem resposta})"
  fi
  mv -f "$RIE_PART" "$RIE_BIN"
fi
echo "$RIE_SHA256  $RIE_BIN" | sha256sum -c - || fail "sha256 do aws-lambda-rie nao confere"
# docker.exe (Windows) precisa de caminho Windows; no Linux/CI e o proprio caminho.
RIE_BIN_HOST="$RIE_BIN"
command -v cygpath >/dev/null 2>&1 && RIE_BIN_HOST="$(cygpath -w "$RIE_BIN")"

RIE_CT="mfsmoke-rie-$$"
docker create --name "$RIE_CT" --network none \
  -e DB_BACKEND=pymysql \
  -e AWS_LAMBDA_FUNCTION_NAME=musclefit-migrations \
  -e AWS_REGION=sa-east-1 \
  --entrypoint /bin/sh "$IMAGE" \
  -c 'install -m 0755 /tmp/aws-lambda-rie /tmp/rie && exec /tmp/rie python -m awslambdaric app.migrate.handler' >/dev/null
# `install` (e nao chmod): o docker cp grava o binario como root e a imagem roda
# como usuario sem privilegio (SEG-16), que nao pode dar chmod em arquivo de
# outro dono; a copia em /tmp (gravavel por todos, como no Lambda) e dele.
CONTAINERS+=("$RIE_CT")
docker cp "$RIE_BIN_HOST" "$RIE_CT:/tmp/aws-lambda-rie"
docker start "$RIE_CT" >/dev/null

RESPONSE="$(rie_invoke "$RIE_CT" '{"action": "status"}')"
echo "resposta do RIE: $RESPONSE"
docker logs "$RIE_CT" 2>&1 | tail -n 20

# A resposta do handler e definitiva: errorType/mensagem diferentes do esperado
# falham aqui, na primeira invocacao, sem retry.
echo "$RESPONSE" | "$PY" -c '
import json, sys
body = json.loads(sys.stdin.read())
assert body.get("errorType") == "MigrationError", body
assert "recusando executar" in body.get("errorMessage", ""), body
print("ok: handler resolvido pelo awslambdaric e guarda anti-vazamento recusou DB_BACKEND=pymysql")
' || { dump_container_logs "$RIE_CT"; fail "handler nao recusou DB_BACKEND=pymysql como esperado"; }

echo; echo "SMOKE OK: $DOCKERFILE ($IMAGE)"
