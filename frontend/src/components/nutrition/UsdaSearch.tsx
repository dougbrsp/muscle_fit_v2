'use client';

import { Alert, Badge, Button, Group, Paper, Stack, Table, Text, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconDownload, IconSearch } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { externalFoodKeys, importUsdaFood, nutritionErrorInfo, searchUsdaFoods } from '@/lib/nutrition-api';
import {
  FOOD_PER_100_FIELDS,
  FOOD_REQUIRED_FIELDS,
  USDA_DATA_TYPE_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatWithUnit } from '@/lib/nutrition-format';
import type { ExternalFoodResponse } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

// Colunas resumidas: os campos que o dono exige no cadastro (`catalog.REQUIRED_PER_100_FIELDS`),
// com rótulo e unidade da RDC 429 do JSON gerado — as mesmas da busca do catálogo.
const COLUNAS = FOOD_PER_100_FIELDS.filter((campo) => FOOD_REQUIRED_FIELDS.includes(campo.code));
// O resultado de uma busca no USDA não muda no meio do uso da tela.
const BUSCA_VALIDA_MS = 5 * 60_000;

type Props = {
  onImported: (food: ExternalFoodResponse) => void;
};

/**
 * Busca no USDA FoodData Central (Lote L3b) e importação de um item para o
 * catálogo. A API filtra os tipos de dado aceitos, mapeia os nutrientes (motor
 * `external_sources`) e grava o item público com a citação do USDA; a tela só
 * exibe. A busca dispara no envio (não a cada tecla): a chave do USDA tem cota
 * por hora.
 */
export function UsdaSearch({ onImported }: Props) {
  const [texto, setTexto] = useState('');
  const [busca, setBusca] = useState('');

  const query = useQuery({
    queryKey: externalFoodKeys.usdaSearch(busca),
    queryFn: () => searchUsdaFoods(busca),
    enabled: busca.length > 0,
    retry: false,
    staleTime: BUSCA_VALIDA_MS,
  });
  const importacao = useMutation({
    mutationFn: (usdaFoodId: number) => importUsdaFood(usdaFoodId),
    onSuccess: onImported,
  });

  function buscar(event: FormEvent) {
    event.preventDefault();
    setBusca(texto.trim());
  }

  const itens = query.data?.items ?? [];
  const erroDaImportacao = importacao.isError
    ? nutritionErrorInfo(importacao.error, 'Não foi possível importar o alimento do USDA.')
    : null;

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        A busca no USDA FoodData Central é em inglês (ex.: rice, chicken breast, banana). O item importado entra no
        catálogo para todos, com a citação do USDA.
      </Text>
      <form onSubmit={buscar}>
        <Group gap="sm" align="flex-end" wrap="wrap">
          <TextInput
            label="Buscar no USDA (em inglês)"
            placeholder="Ex.: rice, white, cooked"
            leftSection={<IconSearch size={16} />}
            value={texto}
            onChange={(event) => setTexto(event.currentTarget.value)}
            style={{ flex: '1 1 260px' }}
            maxLength={120}
          />
          <Button type="submit" variant="light" color="brand" disabled={texto.trim().length === 0} loading={query.isFetching}>
            Buscar no USDA
          </Button>
        </Group>
      </form>

      {query.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(query.error, 'Não foi possível buscar no USDA.').message}
        </Alert>
      )}
      {erroDaImportacao && erroDaImportacao.items.length > 0 ? (
        <NutritionLimitations items={erroDaImportacao.items} title={erroDaImportacao.message} tone="error" />
      ) : (
        erroDaImportacao && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {erroDaImportacao.message}
          </Alert>
        )
      )}
      {busca.length > 0 && query.isSuccess && itens.length === 0 && (
        <Text c={BRAND.textMuted} size="sm">
          Nenhum alimento encontrado no USDA para essa busca.
        </Text>
      )}

      {itens.length > 0 && (
        <Paper withBorder radius="md" bg={BRAND.bgElevated} style={{ overflowX: 'auto' }}>
          <Table verticalSpacing="xs" miw={720}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Alimento (USDA)</Table.Th>
                <Table.Th>Tipo</Table.Th>
                {COLUNAS.map((campo) => (
                  <Table.Th key={campo.code} ta="right">
                    {campo.label}
                  </Table.Th>
                ))}
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {itens.map((item) => (
                <Table.Tr key={item.fdc_id}>
                  <Table.Td>
                    <Text size="sm" fw={600} c={BRAND.textPrimary} lang="en">
                      {item.name}
                    </Text>
                    <Text size="xs" c={BRAND.textMuted}>
                      {[item.category, `FDC ID ${item.fdc_id}`].filter(Boolean).join(' · ')}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge size="sm" variant="light" color="gray">
                      {nutritionLabel(USDA_DATA_TYPE_OPTIONS, item.data_type)}
                    </Badge>
                  </Table.Td>
                  {COLUNAS.map((campo) => (
                    <Table.Td key={campo.code} ta="right">
                      <Text size="sm">{formatWithUnit(item.per_100[campo.code], campo.unit, 1)}</Text>
                    </Table.Td>
                  ))}
                  <Table.Td ta="right">
                    <Button
                      size="xs"
                      variant="light"
                      color="brand"
                      leftSection={<IconDownload size={14} />}
                      loading={importacao.isPending && importacao.variables === item.fdc_id}
                      disabled={importacao.isPending && importacao.variables !== item.fdc_id}
                      onClick={() => importacao.mutate(item.fdc_id)}
                    >
                      Importar
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
    </Stack>
  );
}
