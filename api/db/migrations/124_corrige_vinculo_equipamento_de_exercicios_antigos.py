"""124 - corrige o vinculo de equipamento de 23 exercicios antigos do usuario 1 (issue #244).

`exercises.equipment_id` guarda um `gym_equipment.id` (vinculo academia x
exercicio x aparelho fisico). Em producao, 67 exercicios antigos do usuario 1
(de 20/07 a 19/09/2026, nenhum criado pelo Modo Treino) apontam para um vinculo
cujo exercicio nao e o do registro (par incoerente, `equipment_pair.is_pair_coherent`).
Medicao de 22/11/2026 (so leitura): 67 exercicios, 49 pares distintos; em 23
exercicios existe, na MESMA academia, exatamente UM vinculo do MESMO aparelho
fisico com o exercicio do registro (17 com vinculo novo ativo, 6 inativo;
academias 1 e 10; atividades de 23/07 a 05/09/2026; usuario 1).

Decisao do usuario (22/11/2026): corrigir SO esses 23, apontando
`exercises.equipment_id` para esse vinculo coerente. Os outros 44 ficam como
estao. Vinculo novo inativo e aceito: so indica que a academia nao oferece mais
a combinacao.

Por que nao mexe em historico nem apaga trilha (diferente da 117): o aparelho
fisico (`gym_equipment.equipment_id`) e o exercicio do registro
(`exercises.mapping_id`) NAO mudam. Historico e progressao usam esses dois
(`training_history`, `comparability`), entao nenhuma chave de comparacao muda e
nenhuma trilha derivada fica invalida. So a tabela `exercises` e escrita.

Estado ANTERIOR (medido em producao; usar para desfazer a mao): a coluna ANTIGO
de cada tupla abaixo.

Tupla: (exercises.id, activity_id, exercises.mapping_id, gym_equipment.id ANTIGO,
gym_equipment.id NOVO, gym_id, equipment.id fisico, mapping_id do vinculo novo,
vinculo novo ativo, data da atividade). Em algumas linhas `exercises.mapping_id`
difere do `mapping_id` do vinculo novo: e id legado x canonico (mesmo exercicio
pelo `exercise_legacy_id_map`); por isso a guarda do vinculo novo usa o
`mapping_id` do VINCULO registrado na tupla.

Guardas (cada statement so age se TUDO valer; senao 0 linhas, sem erro):
- exercises: id, activity_id, mapping_id e equipment_id atuais exatos (o antigo);
- vinculo novo existe com id, gym_id, equipment_id e mapping_id exatos
  (`is_active` nao e exigido);
- vinculo ANTIGO existe com id, gym_id e equipment_id (aparelho fisico) da tupla.
`plan(db)` repete a guarda lendo o banco (linha fora do estado medido nao entra
no plano); o SQL repete com EXISTS. Idempotente: depois de aplicada, o valor
antigo deixa de bater (plano vazio). Banco de teste/CI sem as linhas: plano vazio.
`down`: devolve o antigo so onde o exercicio ainda tem o NOVO e o vinculo antigo
ainda e da mesma academia e aparelho.

Data API: um statement por chamada, parametros `%s`, nenhuma literal com ':';
modulo autocontido.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("fitness.migrate")

# (exercise_id, activity_id, ex_mapping_id, antigo, novo, gym_id, equipment_fisico,
#  mapping_do_vinculo_novo, vinculo_novo_ativo, data)
FIXES = (
    (101, 35, 2, 87, 86, 1, 33, 2, 1, '2026-07-23'),
    (102, 35, 1, 119, 268, 1, 37, 3, 0, '2026-07-23'),
    (104, 35, 13, 81, 197, 1, 32, 13, 1, '2026-07-23'),
    (119, 51, 46, 120, 205, 1, 34, 46, 0, '2026-07-24'),
    (130, 58, 32, 127, 183, 10, 32, 32, 1, '2026-07-25'),
    (131, 58, 16, 126, 132, 10, 26, 16, 1, '2026-07-25'),
    (138, 66, 44, 126, 178, 10, 26, 44, 1, '2026-07-26'),
    (145, 73, 16, 194, 72, 1, 26, 16, 1, '2026-07-27'),
    (246, 135, 31, 202, 203, 1, 34, 31, 1, '2026-08-10'),
    (265, 146, 52, 87, 298, 1, 33, 52, 0, '2026-08-12'),
    (268, 146, 14, 197, 198, 1, 32, 14, 0, '2026-08-12'),
    (278, 149, 31, 202, 203, 1, 34, 31, 1, '2026-08-14'),
    (296, 155, 95, 518, 527, 1, 26, 95, 1, '2026-08-16'),
    (311, 168, 31, 202, 203, 1, 34, 31, 1, '2026-08-18'),
    (326, 170, 93, 197, 509, 1, 32, 93, 1, '2026-08-20'),
    (330, 170, 95, 518, 527, 1, 26, 95, 1, '2026-08-20'),
    (336, 171, 31, 202, 203, 1, 34, 31, 1, '2026-08-22'),
    (352, 174, 44, 72, 73, 1, 26, 44, 1, '2026-08-24'),
    (357, 174, 95, 518, 527, 1, 26, 95, 1, '2026-08-24'),
    (361, 176, 96, 199, 536, 1, 32, 96, 1, '2026-08-26'),
    (380, 179, 109, 72, 613, 1, 26, 109, 1, '2026-08-28'),
    (412, 191, 52, 337, 119, 1, 37, 52, 0, '2026-09-01'),
    (438, 195, 15, 558, 55, 1, 14, 15, 0, '2026-09-05'),
)

_EXISTS_NEW = (
    "EXISTS (SELECT 1 FROM gym_equipment g WHERE g.id = %s AND g.gym_id = %s "
    "AND g.equipment_id = %s AND g.mapping_id = %s)"
)
# Premissa: vinculo antigo e novo sao do mesmo aparelho fisico e da mesma academia.
_EXISTS_OLD = (
    "EXISTS (SELECT 1 FROM gym_equipment o WHERE o.id = %s AND o.gym_id = %s "
    "AND o.equipment_id = %s)"
)
_WHERE_EX = "WHERE id = %s AND activity_id = %s AND mapping_id = %s AND equipment_id = %s AND "
RELINK_SQL = "UPDATE exercises SET equipment_id = %s " + _WHERE_EX + _EXISTS_NEW + " AND " + _EXISTS_OLD
UNRELINK_SQL = "UPDATE exercises SET equipment_id = %s " + _WHERE_EX + _EXISTS_OLD
CHECK_UP_SQL = "SELECT 1 AS ok FROM exercises " + _WHERE_EX + _EXISTS_NEW + " AND " + _EXISTS_OLD
CHECK_DOWN_SQL = "SELECT 1 AS ok FROM exercises " + _WHERE_EX + _EXISTS_OLD


def _up_params(f) -> tuple:
    ex_id, act, ex_map, old, new, gym, eq, link_map = f[:8]
    return (new, ex_id, act, ex_map, old, new, gym, eq, link_map, old, gym, eq)


def _down_params(f) -> tuple:
    ex_id, act, ex_map, old, new, gym, eq = f[:7]
    return (old, ex_id, act, ex_map, new, old, gym, eq)


def plan(db=None) -> list[tuple[str, tuple]]:
    """Um UPDATE por tupla cujo estado atual bate com o medido (leitura)."""
    out = []
    for f in FIXES:
        p = _up_params(f)
        if db is not None and not db.query(CHECK_UP_SQL, p[1:]):
            continue
        out.append((RELINK_SQL, p))
    return out


def plan_down(db=None) -> list[tuple[str, tuple]]:
    """Devolve o vinculo antigo so onde o exercicio ainda tem o novo."""
    out = []
    for f in FIXES:
        p = _down_params(f)
        if db is not None and not db.query(CHECK_DOWN_SQL, p[1:]):
            continue
        out.append((UNRELINK_SQL, p))
    return out


def _run(db, rotulo: str, itens) -> None:
    aplicadas = 0
    for sql, params in itens:
        aplicadas += int(db.execute(sql, params) or 0)  # None/dry-run conta 0
    logger.info(
        "migracao 124%s: plano=%d aplicadas=%d puladas=%d de %d",
        rotulo, len(itens), aplicadas, len(FIXES) - aplicadas, len(FIXES),
    )


def up(db):
    _run(db, "", plan(db))


def down(db):
    _run(db, " down", plan_down(db))
