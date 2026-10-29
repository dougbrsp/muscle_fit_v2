"""O schema de teste compartilhado recebe TODAS as migracoes `.py` (M25).

Por que este arquivo existe (e por que ele e UNITARIO, sem MySQL):

Antes do M25, `recreate_schema_with_migrations` aplicava so os `.sql`
(001..021) e as `.py` entravam no schema compartilhado por uma LISTA escrita a
mao na fixture `mysql_schema`. A lista era silenciosa: uma migracao nova
simplesmente nao aparecia nela, o schema nascia sem a tabela, e o erro so
surgia quando algum teste tentava usa-la. Aconteceu tres vezes - a 040 e a 043
ficaram de fora (notado e deixado passar) e a 048 gastou uma hora de CI no PR
#203 para dizer `Table '..._test.context_events' doesn't exist`.

O M25 tirou a lista: `recreate_schema_with_all_migrations` DESCOBRE as `.py` no
diretorio e aplica todas, em ordem de versao, pelo mesmo `up(db)` de producao.
Este teste guarda essa propriedade em SEGUNDOS, sem MySQL:

- nenhuma migracao `.py` fica fora do schema por omissao (so por excecao
  registrada com motivo);
- a ordem de aplicacao e a ordem de VERSAO, nao a alfabetica ingenua;
- toda excecao cita um arquivo que existe e tem motivo de verdade.
"""

from __future__ import annotations

from tests.support.migrations import (
    MIGRATIONS_DIR,
    PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE,
    py_migration_files,
)


def _nomes_no_diretorio() -> set[str]:
    return {path.name for path in MIGRATIONS_DIR.glob("*.py")}


def test_toda_migracao_py_entra_no_schema_compartilhado():
    """Regressao das 040/043/048: o conjunto aplicado e o diretorio INTEIRO
    menos as excecoes registradas - nunca uma lista que alguem manteve a mao."""
    aplicadas = {path.name for path in py_migration_files()}
    esperadas = _nomes_no_diretorio() - set(PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE)
    assert aplicadas == esperadas, (
        "Migracoes .py que nao entram no schema compartilhado e nao estao "
        f"registradas como excecao: {sorted(esperadas ^ aplicadas)}"
    )


def test_hoje_nenhuma_migracao_fica_de_fora():
    """As 022..NNN aplicam em sequencia num schema recem-criado (verificado
    contra o MySQL local no M25). Se alguem precisar excluir uma, este teste
    obriga a escrever o motivo aqui - e o motivo aparece no diff."""
    assert PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE == {}, (
        "Alguma migracao passou a ficar fora do schema compartilhado: "
        f"{PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE}. Isso afasta o schema de teste de "
        "producao - confirme que e inevitavel antes de aceitar."
    )


def test_a_ordem_e_de_versao_e_nao_a_alfabetica():
    versoes = [int(path.name[:3]) for path in py_migration_files()]
    assert versoes == sorted(versoes)
    assert len(set(versoes)) == len(versoes), f"versao .py duplicada: {versoes}"


def test_a_048_do_contexto_entra_no_schema_de_teste():
    """Regressao direta do PR #203: o CRUD de `/context-events` e a leitura do
    `context_service` exigem a tabela no schema compartilhado."""
    assert "048_context_events.py" in {path.name for path in py_migration_files()}


def test_as_excecoes_so_citam_arquivos_que_existem():
    fantasmas = sorted(set(PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE) - _nomes_no_diretorio())
    assert not fantasmas, f"Excecoes citam migracoes que nao existem mais: {fantasmas}"


def test_todo_motivo_de_exclusao_e_uma_frase_de_verdade():
    """Motivo vazio ou de duas palavras nao explica nada para quem vier
    depois - e o proximo a tropecar vai precisar exatamente disso."""
    curtos = sorted(
        nome for nome, motivo in PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE.items() if len(motivo.strip()) < 20
    )
    assert not curtos, f"Motivo insuficiente para: {curtos}"
