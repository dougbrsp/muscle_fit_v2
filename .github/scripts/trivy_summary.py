"""Resumo da varredura Trivy de uma imagem (AWS-12), para o log e o job summary.

Uso: python3 .github/scripts/trivy_summary.py <relatorio.json> <rotulo> [<arquivo_summary>]

Le o JSON do `trivy image --format json` e escreve:
  - contagem por severidade (CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN), separando as
    que ja tem versao corrigida publicada;
  - tabela das CRITICAL/HIGH (limitada a MAX_LINHAS para nao estourar o
    summary do GitHub, que aceita no maximo 1 MiB por job).

Informativo: sai sempre com 0. A decisao de bloquear o PR por severidade e
outro passo (ver o comentario do step no ci-docker-images.yml).
"""
import json
import sys

SEVERIDADES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
DETALHAR = ("CRITICAL", "HIGH")
MAX_LINHAS = 50


def main() -> int:
    caminho, rotulo = sys.argv[1], sys.argv[2]
    summary = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        with open(caminho, encoding="utf-8") as f:
            rel = json.load(f)
    except (OSError, ValueError) as exc:
        texto = f"### Trivy `{rotulo}`\n\nSem relatorio ({exc}); a varredura nao concluiu (informativa, nao bloqueia).\n"
        _emitir(texto, summary)
        return 0

    total = {s: 0 for s in SEVERIDADES}
    com_fix = {s: 0 for s in SEVERIDADES}
    linhas = []
    vistos = set()
    for res in rel.get("Results") or []:
        alvo = res.get("Target", "")
        for v in res.get("Vulnerabilities") or []:
            sev = v.get("Severity", "UNKNOWN")
            if sev not in total:
                sev = "UNKNOWN"
            chave = (v.get("VulnerabilityID"), v.get("PkgName"), v.get("InstalledVersion"))
            if chave in vistos:
                continue
            vistos.add(chave)
            total[sev] += 1
            if v.get("FixedVersion"):
                com_fix[sev] += 1
            if sev in DETALHAR:
                linhas.append((
                    SEVERIDADES.index(sev), sev, v.get("VulnerabilityID", ""),
                    v.get("PkgName", ""), v.get("InstalledVersion", ""),
                    v.get("FixedVersion", "") or "-", alvo,
                ))

    linhas.sort()
    out = [f"### Trivy `{rotulo}` (informativo, nao bloqueia)", ""]
    out.append("| Severidade | Total | Com correcao publicada |")
    out.append("|---|---:|---:|")
    for s in SEVERIDADES:
        out.append(f"| {s} | {total[s]} | {com_fix[s]} |")
    out.append("")
    if linhas:
        out.append("| Sev. | ID | Pacote | Instalada | Corrigida em | Alvo |")
        out.append("|---|---|---|---|---|---|")
        for _, sev, vid, pkg, inst, fix, alvo in linhas[:MAX_LINHAS]:
            out.append(f"| {sev} | {vid} | {pkg} | {inst} | {fix} | {alvo} |")
        if len(linhas) > MAX_LINHAS:
            out.append(f"\n... e mais {len(linhas) - MAX_LINHAS} CRITICAL/HIGH (ver o log do step).")
    else:
        out.append("Nenhuma CRITICAL/HIGH.")
    out.append("")
    _emitir("\n".join(out) + "\n", summary)
    return 0


def _emitir(texto: str, summary: str | None) -> None:
    print(texto)
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(texto)


if __name__ == "__main__":
    sys.exit(main())
