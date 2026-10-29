'use client';

import { Alert, Badge, Group, Paper, Select, Stack, Switch, Table, Text, TextInput, Tooltip, UnstyledButton } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconAlertTriangle, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { foodKeys, nutritionErrorInfo, searchFoods } from '@/lib/nutrition-api';
import { FOOD_PER_100_FIELDS, FOOD_REQUIRED_FIELDS, FOOD_SOURCE_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import { formatWithUnit } from '@/lib/nutrition-format';
import type { FoodSearchItem, FoodSearchScope } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

// Colunas resumidas da tabela de resultados: os campos que o dono exige no
// cadastro (energia e os três macros, `catalog.REQUIRED_PER_100_FIELDS`), com o
// rótulo e a unidade da RDC 429 do JSON gerado. Os demais ficam no detalhe.
const COLUNAS = FOOD_PER_100_FIELDS.filter((campo) => FOOD_REQUIRED_FIELDS.includes(campo.code));
const ATRASO_DA_BUSCA_MS = 300;

type Props = {
  isAdmin: boolean;
  selectedId: number | null;
  onSelect: (item: FoodSearchItem) => void;
};

/**
 * Busca no catálogo. A API filtra (FULLTEXT), ordena pela nota do motor
 * (`food_text`) e diz de que fonte é cada item, com a citação; a tela só exibe.
 */
export function FoodSearch({ isAdmin, selectedId, onSelect }: Props) {
  const [texto, setTexto] = useState('');
  const [fonte, setFonte] = useState<string | null>(null);
  const [privados, setPrivados] = useState(false);
  const [busca] = useDebouncedValue(texto.trim(), ATRASO_DA_BUSCA_MS);
  const scope: FoodSearchScope = isAdmin && privados ? 'private_all' : 'visible';

  const query = useQuery({
    queryKey: foodKeys.search(busca, scope, fonte),
    queryFn: () => searchFoods(busca, { scope, source: fonte }),
    enabled: busca.length > 0,
    retry: false,
    staleTime: 30_000,
  });
  const itens = query.data?.items ?? [];

  return (
    <Stack gap="sm">
      <Group gap="sm" align="flex-end" wrap="wrap">
        <TextInput
          label="Buscar alimento"
          placeholder="Ex.: arroz branco cozido, banana prata, pão francês"
          leftSection={<IconSearch size={16} />}
          value={texto}
          onChange={(event) => setTexto(event.currentTarget.value)}
          style={{ flex: '1 1 260px' }}
          maxLength={120}
        />
        <Select
          label="Fonte"
          placeholder="Todas"
          data={[...FOOD_SOURCE_OPTIONS]}
          value={fonte}
          onChange={setFonte}
          clearable
          w={200}
        />
        {isAdmin && (
          <Switch
            label="Privados de usuários (promover)"
            checked={privados}
            onChange={(event) => setPrivados(event.currentTarget.checked)}
            mb={6}
          />
        )}
      </Group>

      {query.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(query.error, 'Não foi possível buscar alimentos.').message}
        </Alert>
      )}
      {busca.length > 0 && query.isSuccess && itens.length === 0 && (
        <Text c={BRAND.textMuted} size="sm">
          Nenhum alimento encontrado. Cadastre um alimento novo abaixo.
        </Text>
      )}

      {itens.length > 0 && (
        <Paper withBorder radius="md" bg={BRAND.bgElevated} style={{ overflowX: 'auto' }}>
          <Table highlightOnHover verticalSpacing="xs" miw={640}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Alimento</Table.Th>
                <Table.Th>Fonte</Table.Th>
                {COLUNAS.map((campo) => (
                  <Table.Th key={campo.code} ta="right">
                    {campo.label}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {itens.map((item) => (
                <Table.Tr
                  key={item.id}
                  bg={item.id === selectedId ? 'var(--mantine-color-brand-light)' : undefined}
                  style={{ cursor: 'pointer' }}
                  onClick={() => onSelect(item)}
                >
                  <Table.Td>
                    {/* O clique sobe para a linha; o botão dá foco e Enter pelo teclado. */}
                    <UnstyledButton aria-label={`Ver ${item.name}`}>
                      <Text size="sm" fw={600} c={BRAND.textPrimary}>
                        {item.name}
                      </Text>
                      <Text size="xs" c={BRAND.textMuted}>
                        {[item.brand, item.category].filter(Boolean).join(' · ') || ' '}
                      </Text>
                    </UnstyledButton>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip
                      label={[item.citation ?? 'Cadastro sem citação de tabela', ...(item.field_sources ?? []).map((o) => o.note)].join(' ')}
                      multiline
                      w={320}
                      withArrow
                    >
                      <Group gap={4} wrap="nowrap">
                        <Badge size="sm" variant="light" color={item.is_public ? 'brand' : 'gray'}>
                          {nutritionLabel(FOOD_SOURCE_OPTIONS, item.source)}
                        </Badge>
                        {!item.is_public && (
                          <Badge size="sm" variant="outline" color="gray">
                            {item.is_own ? 'Meu' : 'Privado'}
                          </Badge>
                        )}
                      </Group>
                    </Tooltip>
                  </Table.Td>
                  {COLUNAS.map((campo) => (
                    <Table.Td key={campo.code} ta="right">
                      <Text size="sm">{formatWithUnit(item.per_100[campo.code], campo.unit, 1)}</Text>
                    </Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
    </Stack>
  );
}
