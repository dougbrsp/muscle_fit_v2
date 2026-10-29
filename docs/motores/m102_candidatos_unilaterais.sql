-- M102 — candidatos a exercício unilateral (SÓ LEITURA).
--
-- O Modo Treino pede reps/RPE por lado (E/D) quando `exercise_catalog.is_unilateral = 1`.
-- Não há backfill automático: o usuário marca cada exercício no domínio de
-- exercícios (/dominio-exercicios, chave "Unilateral"). Esta consulta lista os
-- ativos ainda NÃO marcados cujo nome ou palavras-chave sugerem execução de um
-- lado por vez, com quantas vezes cada um foi treinado (para priorizar).
--
-- Rodar no DEV ou em produção (SELECT só leitura; nada é gravado).

SELECT ec.id,
       ec.name,
       ec.movement,
       ec.keywords,
       COUNT(e.id) AS vezes_treinado
FROM exercise_catalog ec
LEFT JOIN exercises e ON e.mapping_id = ec.id
WHERE ec.is_active = 1
  AND ec.is_unilateral = 0
  AND (
        LOWER(ec.name) REGEXP 'unilateral|alternad|um bra|uma perna|um lado|isolad|serrote|concentrad|bulgar|afundo|passada|avan[cç]o|lunge|single|one arm|one leg'
     OR LOWER(COALESCE(ec.keywords, '')) REGEXP 'unilateral|alternad|single|one arm|one leg'
      )
GROUP BY ec.id, ec.name, ec.movement, ec.keywords
ORDER BY vezes_treinado DESC, ec.name;

-- Prova de impacto após a migração 100 (deve ser 0 até alguém lançar uma série por lado):
-- SELECT COUNT(*) FROM exercise_sets
-- WHERE reps_left IS NOT NULL OR reps_right IS NOT NULL OR rpe_left IS NOT NULL OR rpe_right IS NOT NULL;
