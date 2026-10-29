"""Guarda do versionamento de Modelo (065): todo SQL em `api/app` que le ou
escreve `workout_plan_template_slots` precisa citar `version_id`.

Por que: depois da 065 a tabela guarda TODAS as versoes de cada Modelo. Um
SQL que filtra so por `template_id` passa a somar os slots da v1, da v2, da
v3... - o Modelo "dobra" a cada edicao, e nada quebra de forma visivel: os
numeros so ficam errados. O erro e silencioso no dia em que alguem escreve
uma consulta nova sem lembrar das versoes; este teste o torna barulhento.

Como: le o codigo-fonte com `ast` (sem importar nada), junta os literais de
string (literais adjacentes ja chegam unidos; f-strings entram pelas partes
constantes) e olha os que usam a tabela como RELACAO (`FROM`/`JOIN`/`INTO`/
`UPDATE workout_plan_template_slots`, palavra-chave em caixa alta, como o SQL
do projeto - prosa de docstring nao casa). Cada um precisa citar `version_id`
no mesmo literal: o filtro da vigente (ou a versao explicita) e parte da
consulta, nunca um statement a mais.

Excecao so com motivo, em `EXCECOES` (arquivo -> motivo). Hoje: nenhuma.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

_RELACAO_RE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE)\s+workout_plan_template_slots\b")

# arquivo relativo a `api/app` -> motivo (uma frase de verdade).
EXCECOES: dict[str, str] = {}


def _literais(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            partes = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            yield node.lineno, "{}".join(partes)


def _sql_de_slots(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for lineno, texto in _literais(tree):
        if _RELACAO_RE.search(texto):
            yield lineno, texto


def test_todo_sql_de_slots_em_app_cita_version_id():
    violacoes = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in EXCECOES:
            continue
        violacoes += [f"{rel}:{lineno}" for lineno, texto in _sql_de_slots(path) if "version_id" not in texto]
    assert not violacoes, (
        "SQL lendo/escrevendo workout_plan_template_slots sem `version_id` (a 065 guarda todas as "
        "versoes na mesma tabela; filtre a vigente com JOIN em "
        "workout_plan_template_versions.current_template_id no MESMO statement, ou registre a "
        f"excecao com motivo): {violacoes}"
    )


def test_a_guarda_enxerga_os_leitores_conhecidos():
    """Sanidade da propria guarda: se a regex deixar de casar, o teste acima
    passaria por nao ver nada. Estes arquivos TEM SQL de slots hoje."""
    vistos = {
        path.relative_to(APP_DIR).as_posix() for path in APP_DIR.rglob("*.py") if any(_sql_de_slots(path))
    }
    esperados = {
        "workout_plan_templates.py",
        "template_versions.py",
        "generation/freeze_mode_b.py",
        "intelligence/dashboard/adherence_service.py",
        "intelligence/dashboard/engine_service.py",
        "intelligence/dashboard/muscle_summary_service.py",
        "intelligence/report/daily_breakdown.py",
        "advanced_techniques_report.py",
        "training_safety_backfill_report.py",
    }
    assert esperados <= vistos, sorted(esperados - vistos)


def test_a_guarda_pega_um_sql_sem_versao_e_ignora_prosa():
    assert _RELACAO_RE.search("SELECT name FROM workout_plan_template_slots WHERE template_id = %s")
    assert not _RELACAO_RE.search("le os nomes de `workout_plan_template_slots` do Modelo")
    assert not _RELACAO_RE.search("FROM workout_plan_template_slot_items i")


def test_toda_excecao_tem_motivo_e_arquivo():
    for rel, motivo in EXCECOES.items():
        assert (APP_DIR / rel).exists(), rel
        assert len(motivo.strip()) >= 20, rel
