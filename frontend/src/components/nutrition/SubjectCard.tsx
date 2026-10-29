'use client';

import { Card, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { formatNumberOrDash } from '@/lib/format';
import {
  ACTIVITY_LEVEL_OPTIONS,
  BODY_FAT_METHOD_OPTIONS,
  SEX_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatKcal } from '@/lib/nutrition-format';
import type { NutritionSubject } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

function withUnit(value: number | null, unit: string, digits = 1): string {
  const texto = formatNumberOrDash(value, digits);
  return texto === '-' ? texto : `${texto} ${unit}`;
}

function withDate(texto: string, isoDate: string | null): string {
  return isoDate && texto !== '-' ? `${texto} (${formatDateBR(isoDate)})` : texto;
}

/** Quem recebe o plano, como o motor o montou para o dia (perfil + leituras). */
export function SubjectCard({ subject, title = 'Aluno hoje' }: { subject: NutritionSubject; title?: string }) {
  const gordura = formatNumberOrDash(subject.body_fat_percent, 1);
  const metodo = nutritionLabel(BODY_FAT_METHOD_OPTIONS, subject.body_fat_method);
  const linhas: { label: string; value: string }[] = [
    { label: 'Sexo', value: nutritionLabel(SEX_OPTIONS, subject.sex) ?? '-' },
    { label: 'Idade', value: subject.age_years == null ? '-' : `${subject.age_years} anos` },
    { label: 'Altura', value: withUnit(subject.height_cm, 'cm') },
    { label: 'Peso', value: withDate(withUnit(subject.weight_kg, 'kg'), subject.weight_date) },
    {
      label: '% de gordura',
      value: withDate(gordura === '-' ? gordura : `${gordura}%${metodo ? ` · ${metodo}` : ''}`, subject.body_fat_date),
    },
    { label: 'Massa livre de gordura', value: withUnit(subject.ffm_kg, 'kg') },
    { label: 'Nível de atividade', value: nutritionLabel(ACTIVITY_LEVEL_OPTIONS, subject.activity_level) ?? '-' },
    { label: 'IMC', value: formatNumberOrDash(subject.bmi, 1) },
    { label: 'TMB da balança', value: formatKcal(subject.scale_bmr_kcal) },
  ];
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="xs">
        <Title order={4} c={BRAND.textPrimary}>
          {title}
        </Title>
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="xs" verticalSpacing={4}>
          {linhas.map((linha) => (
            <div key={linha.label}>
              <Text size="xs" c="dimmed">
                {linha.label}
              </Text>
              <Text size="sm" c={BRAND.textPrimary}>
                {linha.value}
              </Text>
            </div>
          ))}
        </SimpleGrid>
      </Stack>
    </Card>
  );
}
