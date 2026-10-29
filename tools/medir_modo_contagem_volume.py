"""Lote 0 do "modo de contagem do volume por Modelo": medição SÓ LEITURA.

Para os Modelos do usuário (o vigente + os ativos do histórico de atribuição,
ou os `--template-id` dados), roda o motor REAL da metodologia
(`methodology_volume_service` + `methodology_volume.evaluate_methodology_volume`)
nos dois modos de contagem e imprime, por grupo (bloco):

- séries semanais (janela de 7 dias), bucket de frequência e status de cada
  eixo (exercícios/sessão, séries/sessão, semanal) e o combinado;
- as TRANSIÇÕES de `primary_only` -> `primary_secondary` (status por eixo e
  bucket);
- a distribuição do `stimulus_weight` das contribuições SECONDARY ativas dos
  exercícios desses Modelos.

PIOR CASO: as faixas ponderadas ainda não existem (Lote 2). Os dois modos são
comparados com as MESMAS faixas de hoje (`primary_only`), então todo ABOVE que
aparece em `primary_secondary` é o que as faixas próprias do modo terão de
absorver na calibração.

Só SELECT, pelas funções de leitura do próprio app. Nada é gravado.

Uso (DEV local, variáveis só no processo; nunca em arquivo de config):

    python tools/medir_modo_contagem_volume.py --user-id 1 --dev-env-file .env

Sem `--dev-env-file` usa o backend do ambiente (Data API = produção, leitura
autorizada; continua só SELECT).
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))



def _carregar_env_dev(caminho: str) -> None:
    """`--dev-env-file`: le SO as chaves `MYSQL_*` do `.env` do compose para o
    PROCESSO (nada e gravado em config), aponta para o MySQL local publicado
    em 127.0.0.1 e liga o backend pymysql. Roda antes de importar o app
    (`app.db` escolhe o backend no import)."""
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        chave, sep, valor = linha.strip().partition("=")
        if sep and chave.startswith("MYSQL_"):
            os.environ[chave] = valor.strip().strip('"').strip("'")
    os.environ["DB_BACKEND"] = "pymysql"
    os.environ["MYSQL_HOST"] = "127.0.0.1"
    os.environ.setdefault("MYSQL_PORT", "3306")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Medição só leitura do modo de contagem do volume.")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--template-id", type=int, action="append", default=None)
    parser.add_argument("--dev-env-file", default=None, help="`.env` do compose local (só DEV)")
    return parser.parse_args()


ARGS = _args() if __name__ == "__main__" else None
if ARGS is not None and ARGS.dev_env_file:
    _carregar_env_dev(ARGS.dev_env_file)

from app.db import transaction  # noqa: E402
from app.intelligence import methodology_volume as mv  # noqa: E402
from app.intelligence import methodology_volume_service as svc  # noqa: E402
from app.template_assignments import resolve_active_template_id  # noqa: E402
from app.workout_plan_templates import _get_template  # noqa: E402

MODOS = (mv.VolumeCountingMode.PRIMARY_ONLY, mv.VolumeCountingMode.PRIMARY_SECONDARY)
EIXOS = (("ex", "direct_exercises_axis"), ("ss", "direct_sets_axis"), ("sem", "weekly_sets_axis"))
CURTO = {
    "WITHIN_RANGE": "WITHIN",
    "BELOW_RANGE": "BELOW",
    "ABOVE_RANGE": "ABOVE",
    "NOT_DIRECTLY_TRAINED": "-",
    "NOT_EVALUABLE": "n/a",
}


def _modelos_do_usuario(user_id: int) -> list[int]:
    ids: list[int] = []
    vigente = resolve_active_template_id(user_id)
    if vigente is not None:
        ids.append(int(vigente))
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT a.template_id FROM workout_plan_template_assignments a "
            "JOIN workout_plan_templates t ON t.id = a.template_id "
            "WHERE a.user_id = %s AND t.is_active = 1 ORDER BY a.template_id",
            (user_id,),
        )
        for row in cur.fetchall() or []:
            if int(row["template_id"]) not in ids:
                ids.append(int(row["template_id"]))
    return ids


def _avaliar(template: dict, modo: mv.VolumeCountingMode) -> mv.MethodologyVolumeEvaluation:
    return svc.evaluate_slots(
        template.get("slots") or [],
        str(template.get("volume_mode") or "percent_budget"),
        mv.MethodologyIntent(),
        modo,
    )


def _status(axis: mv.AxisEvaluation) -> str:
    return CURTO.get(axis.status.value, axis.status.value)


def _linha(g: mv.GroupEvaluation) -> dict:
    # Sem valor semanal do motor (grupo sem sessao que conte): "-", nunca uma
    # soma refeita aqui.
    semanal = g.weekly_sets_axis.value_weekly
    return {
        "series_sem": "-" if semanal is None else f"{semanal:g}",
        "bucket": g.frequency_bucket or "-",
        **{nome: _status(getattr(g, attr)) for nome, attr in EIXOS},
        "status": CURTO.get(g.status.value, g.status.value),
    }


def _pesos_secundarios(mapping_ids: set[int]) -> collections.Counter:
    if not mapping_ids:
        return collections.Counter()
    with transaction() as conn, conn.cursor() as cur:
        canonical = svc.load_canonical_exercise_ids(cur, mapping_ids)
        lookup = set(mapping_ids) | {c for c in canonical.values() if c is not None}
        contribs = svc.load_volume_contributions(cur, lookup, mv.VolumeCountingMode.PRIMARY_SECONDARY)
    contagem: collections.Counter = collections.Counter()
    for mapping_id in mapping_ids:
        # Estatistica do CADASTRO (linhas cruas), nao regra de contagem.
        own = contribs.get(mapping_id) or contribs.get(canonical.get(mapping_id) or -1) or ()
        for row in own:
            if row.role.upper() == "SECONDARY" and row.stimulus_weight is not None:
                contagem[round(row.stimulus_weight, 2)] += 1
    return contagem


def medir(template_id: int) -> tuple[int, int]:
    template = _get_template(template_id)
    if not template:
        print(f"\n## Modelo {template_id}: não encontrado")
        return 0, 0
    print(f"\n## Modelo {template_id} - {template.get('name')} (volume_mode={template.get('volume_mode')})")
    if str(template.get("volume_mode")) != "equipment_series":
        print("Modo A (percent_budget): o item já é o grupo; o modo de contagem não se aplica.")
        return 0, 0
    por_modo = {modo: {g.muscle_group_id: g for g in _avaliar(template, modo).groups} for modo in MODOS}
    a, b = (por_modo[m] for m in MODOS)
    print()
    print("| Grupo | séries/sem PO → PS | bucket PO → PS | ex PO → PS | séries/sessão PO → PS | semanal PO → PS | combinado PO → PS |")
    print("|---|---|---|---|---|---|---|")
    transicoes = grupos_tocados = 0
    for gid in sorted(a, key=lambda i: a[i].muscle_group_name):
        la, lb = _linha(a[gid]), _linha(b[gid])
        if la == lb and la["status"] == "-" and not a[gid].sessions and not b[gid].sessions:
            continue
        grupos_tocados += 1
        mudou = any(la[k] != lb[k] for k in ("bucket", "ex", "ss", "sem", "status"))
        transicoes += int(mudou)

        def par(chave: str) -> str:
            return f"{la[chave]} → {lb[chave]}" if la[chave] != lb[chave] else f"{la[chave]}"

        nome = a[gid].muscle_group_name + (" **(muda)**" if mudou else "")
        print(
            f"| {nome} | {la['series_sem']} → {lb['series_sem']} | {par('bucket')} | {par('ex')} | "
            f"{par('ss')} | {par('sem')} | {par('status')} |"
        )
    print(f"\nGrupos com volume: {grupos_tocados}; grupos com transição de status/bucket: {transicoes}")
    pesos = _pesos_secundarios(svc.mapping_ids_of(template.get("slots") or []))
    if pesos:
        dist = ", ".join(f"{peso:g}: {n}" for peso, n in sorted(pesos.items()))
        print(f"stimulus_weight SECONDARY dos itens (peso: nº de contribuições): {dist}")
    return grupos_tocados, transicoes


def main() -> int:
    args = ARGS or _args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console do Windows (cp1252)
    backend = os.environ.get("DB_BACKEND", "data-api")
    print(f"# Medição do modo de contagem do volume (só leitura; backend: {backend})")
    print("PO = primary_only (hoje); PS = primary_secondary. PIOR CASO: PS com as faixas de PO.")
    ids = args.template_id or _modelos_do_usuario(args.user_id)
    if not ids:
        print(f"Usuário {args.user_id} sem Modelo.")
        return 0
    total_grupos = total_trans = 0
    for template_id in ids:
        g, t = medir(template_id)
        total_grupos += g
        total_trans += t
    print(f"\n**Total**: {len(ids)} Modelo(s), {total_grupos} grupo(s) com volume, {total_trans} com transição.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
