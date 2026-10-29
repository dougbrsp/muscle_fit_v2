'use client';

import { Alert, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconBarcode } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { lookupBarcode, nutritionErrorInfo } from '@/lib/nutrition-api';
import type { ExternalFoodResponse } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  onFound: (food: ExternalFoodResponse) => void;
};

/**
 * Consulta por código de barras digitado (Lote L3b; leitor pela câmera fica
 * para depois). A API valida o EAN (dígito verificador do motor), procura no
 * catálogo e, se não achar, no Open Food Facts — com limite diário por usuário
 * de consultas novas. A tela só manda o texto e exibe o resultado ou o erro.
 */
export function BarcodeLookup({ onFound }: Props) {
  const [ean, setEan] = useState('');
  const consulta = useMutation({
    mutationFn: (codigo: string) => lookupBarcode(codigo),
    onSuccess: onFound,
  });

  function consultar(event: FormEvent) {
    event.preventDefault();
    const codigo = ean.trim();
    if (codigo) consulta.mutate(codigo);
  }

  const erro = consulta.isError ? nutritionErrorInfo(consulta.error, 'Não foi possível consultar o código de barras.') : null;
  const achado = consulta.isSuccess ? consulta.data : null;

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        Digite os números do código de barras da embalagem. Produto que ainda não está no catálogo vem do Open Food
        Facts (base aberta e colaborativa); essas consultas novas têm um limite diário por usuário.
      </Text>
      <form onSubmit={consultar}>
        <Group gap="sm" align="flex-end" wrap="wrap">
          <TextInput
            label="Código de barras (EAN)"
            placeholder="Ex.: 7891000100103"
            leftSection={<IconBarcode size={16} />}
            inputMode="numeric"
            autoComplete="off"
            value={ean}
            onChange={(event) => setEan(event.currentTarget.value)}
            style={{ flex: '1 1 220px' }}
            maxLength={32}
          />
          <Button type="submit" variant="light" color="brand" disabled={ean.trim().length === 0} loading={consulta.isPending}>
            Consultar
          </Button>
        </Group>
      </form>

      {erro && erro.items.length > 0 && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
      {erro && erro.items.length === 0 && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {erro.message}
        </Alert>
      )}
      {achado && (
        <Text size="sm" c="green">
          {achado.from_cache ? 'Produto já estava no catálogo' : 'Produto trazido do Open Food Facts'}: {achado.name}.
        </Text>
      )}
    </Stack>
  );
}
