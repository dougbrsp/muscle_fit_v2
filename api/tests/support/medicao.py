"""Afirmacoes de MEDICAO que nao dependem de quanto a maquina esta ocupada.

Politica do projeto: **teto de desempenho e rede de ORDEM DE GRANDEZA, nunca
numero exato**. O historico deste repositorio mostra por que:

- `test_medicao_recovery_overview_antes_e_depois` reprovou DOIS PRs em
  2026-09-25 - o #213 (exclusivamente frontend, `1,0939 < 0,6520`) e o #217
  (exclusivamente Terraform/compose, `1,4341 < 0,5554`) - e os dois passaram
  no rerun do MESMO commit. A variacao entre execucoes (0,55s a 1,43s) era
  MAIOR que a diferenca que o teste tentava provar;
- `test_medicao_overview_com_meta_do_modelo_por_equipamento` mediu 3,07s
  contra teto de 3,0s numa maquina que varia de 1,1s a 3,3s com o MESMO
  codigo;
- um teste de aceite de 3,0s reprovou na maquina de um agente inclusive com a
  funcionalidade nova DESLIGADA (e, no caminho da Data API, ficou pior
  desligada: 4,73s vs 4,00s);
- e o caso mais grave, medido para este PR: das 20 medicoes do overview que
  duas execucoes completas produziram, com o codigo de producao INTACTO,
  **20 de 20 ficaram acima do teto de 3,0s** (min 3,28s, max 6,47s, media
  4,51s). Ou seja, `test_medicao_overview_por_bloco` e
  `test_medicao_overview_com_meta_do_modelo_por_equipamento` nao eram
  instaveis: nesta maquina eram vermelho GARANTIDO. Um teste que so passa em
  maquina rapida nao e portao, e loteria.

Nada disso e regressao de codigo: e ruido do ambiente. Um vermelho que e
provavelmente flake ensina a equipe a rerodar, e um vermelho VERDADEIRO passa
despercebido - o CI deixa de ser portao.

Este modulo oferece as duas ferramentas que substituem o cronometro apertado:

1. `assert_ordem_de_grandeza`: o relogio so reprova quando o endpoint se
   aproxima do UNICO limite que existe de verdade em producao - o timeout de
   29s do API Gateway (`infra/lambda_engines.tf`), que e o que derrubou o
   drill-down (`{"message": "Endpoint request timed out"}`). Abaixo disso,
   milissegundos sao informacao para o PR, nao criterio de aprovacao.

2. `assert_no_maximo_statements` / `assert_reduziu_statements`: a protecao de
   VERDADE, e a preferida sempre que couber. O que estourou o timeout em
   producao foi contagem de idas ao banco (N+1 por grupo/exercicio, e
   subconsulta correlacionada por serie), nao constante de CPU. Contar
   statements e DETERMINISTICO: nao depende da maquina, do cache do MySQL nem
   de quantos containers estao subindo ao lado. No M19 foi justamente a
   contagem que provou o ganho; o cronometro, sozinho, nao provou nada.

Medido para este PR, TRES execucoes completas da suite, mesmo commit, mesma
maquina, nada alterado entre elas - `dashboard-alerts` no PyMySQL:

    statements  23    23    23      variacao   0%
    tempo       7,67s 5,18s 4,73s   variacao  62%

E na MESMA execucao, entre backends: `get_recovery_overview` serie a serie
custou 5,19s no PyMySQL e 9,39s no shim da Data API (1,8x), emitindo
exatamente 18 statements nos dois.

A contagem mede o CODIGO. O relogio mede a MAQUINA.

Contagem de statements sai de `tests.support.sql_capture.recorded_sql`, que
ja captura igual nos dois backends (PyMySQL e o shim da RDS Data API).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Sequence

# Limite REAL de producao: timeout do API Gateway em `infra/lambda_engines.tf`.
TIMEOUT_API_GATEWAY_S = 29.0

# Rede de ordem de grandeza. Fica abaixo do timeout real (da tempo de o CI
# reprovar ANTES de a rota morrer em producao) e muito acima do ruido medido
# (3x a pior leitura ja registrada em CI para estes endpoints).
REDE_ORDEM_DE_GRANDEZA_S = 20.0

_LITERAL = re.compile(r"'[^']*'|\b\d+\b|%s")


def forma(statement: str) -> str:
    """Forma canonica de um statement: espacos colapsados e literais/
    parametros apagados. Duas leituras do mesmo SELECT com ids diferentes tem
    a MESMA forma - e e por forma que se enxerga um N+1."""
    return _LITERAL.sub("?", " ".join(str(statement).split()))


def repetidos(statements: Iterable[str], *, top: int = 3) -> list[tuple[int, str]]:
    """As formas mais repetidas, da mais repetida para a menos. E o diagnostico
    que acompanha a falha: numa regressao de N+1 a forma repetida e a
    consulta que voltou a rodar em laco."""
    contagem = Counter(forma(s) for s in statements)
    return [(n, f[:160]) for f, n in contagem.most_common(top)]


def _diagnostico(statements: Sequence[str]) -> str:
    linhas = [f"    {n}x {f}" for n, f in repetidos(statements)]
    return "\n".join(linhas) or "    (nenhum statement capturado)"


def assert_ordem_de_grandeza(
    medicoes: Iterable[tuple[str, float]],
    *,
    backend: str,
    teto: float = REDE_ORDEM_DE_GRANDEZA_S,
) -> None:
    """Reprova so quando o desempenho piorou de ORDEM DE GRANDEZA.

    `medicoes`: pares `(rotulo, segundos)`. `teto` default e a rede unica do
    projeto; passe um teto maior apenas para caminhos que ja nascem mais
    caros (e diga no proprio codigo por que).
    """
    estourou = [(rotulo, s) for rotulo, s in medicoes if s >= teto]
    assert not estourou, (
        "regressao de ORDEM DE GRANDEZA no backend "
        f"{backend}: {', '.join(f'{r} levou {s:.2f}s' for r, s in estourou)}. "
        f"A rede e {teto:.0f}s, derivada do timeout real de "
        f"{TIMEOUT_API_GATEWAY_S:.0f}s do API Gateway (infra/lambda_engines.tf). "
        "Este teto NAO e criterio de aceite de milissegundos - se estourou, o "
        "caminho passou a custar varias vezes mais do que custava."
    )


def assert_no_maximo_statements(
    statements: Sequence[str],
    *,
    teto: int,
    label: str,
    backend: str,
) -> None:
    """Teto DETERMINISTICO de idas ao banco.

    E esta a protecao contra a regressao que derrubou estes endpoints em
    producao: uma consulta que volta a rodar por grupo/exercicio multiplica a
    contagem e reprova aqui na hora, em qualquer maquina. O teto e folgado o
    bastante para uma consulta nova legitima passar, e apertado o bastante
    para um N+1 (que multiplica por dezenas) nao passar.
    """
    assert len(statements) <= teto, (
        f"{label} emitiu {len(statements)} statements no backend {backend} "
        f"(teto {teto}). No shim da RDS Data API cada statement e uma ida ao "
        "servico: contagem que cresce em laco e o que estourou o timeout de "
        f"{TIMEOUT_API_GATEWAY_S:.0f}s em producao. Formas mais repetidas:\n"
        f"{_diagnostico(statements)}"
    )


def assert_reduziu_statements(
    antes: Sequence[str],
    depois: Sequence[str],
    *,
    label: str,
    backend: str,
    fator: float = 1.0,
) -> None:
    """O caminho novo tem que emitir MENOS statements que o antigo.

    Substitui `assert depois_s < antes_s` (duas leituras de relogio numa
    maquina compartilhada, sem folga nenhuma): a mesma afirmacao, agora
    deterministica. `fator > 1.0` exige ganho de pelo menos esse fator.
    """
    assert len(depois) * fator < len(antes), (
        f"{label}: o caminho novo emitiu {len(depois)} statements contra "
        f"{len(antes)} do antigo no backend {backend}"
        + (f" (exigido ganho de {fator:.1f}x)" if fator != 1.0 else "")
        + ". O ganho que este teste existe para travar desapareceu. "
        f"Formas mais repetidas do caminho NOVO:\n{_diagnostico(depois)}"
    )
