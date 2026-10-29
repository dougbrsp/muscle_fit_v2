'use client';

import { Stack, Text } from '@mantine/core';
import { FOOD_CITATIONS, FOOD_LICENSES, FOOD_SOURCE_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import { BRAND } from '@/theme';

/**
 * Rodapé com a citação e a licença de cada tabela/base usada no catálogo (risco
 * R4 do plano): a TACO exige a fonte citada, o USDA pede a citação (CC0) e o
 * Open Food Facts exige o aviso de atribuição da ODbL. Textos do dono
 * (`catalog.SOURCE_CITATIONS`/`SOURCE_LICENSES`); cada item traz a sua citação.
 */
export function FoodCitationFooter() {
  const fontes = Object.entries(FOOD_CITATIONS);
  if (fontes.length === 0) return null;
  return (
    <Stack gap={2} component="footer" aria-label="Fontes das tabelas de alimentos">
      {fontes.map(([fonte, citacao]) => (
        <Text key={fonte} size="xs" c={BRAND.textMuted}>
          <Text span fw={600} inherit>
            Fonte ({nutritionLabel(FOOD_SOURCE_OPTIONS, fonte)}):
          </Text>{' '}
          {citacao}
          {FOOD_LICENSES[fonte] ? ` Licença: ${FOOD_LICENSES[fonte]}` : ''}
        </Text>
      ))}
    </Stack>
  );
}
