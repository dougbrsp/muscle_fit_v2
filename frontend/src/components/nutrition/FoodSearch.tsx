'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconAlertTriangle, IconSearch, IconStar, IconStarFilled } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import {
  addFavorite,
  favoriteKeys,
  fetchFavorites,
  foodKeys,
  nutritionErrorInfo,
  removeFavorite,
  searchFoods,
} from '@/lib/nutrition-api';
import { favoriteIds, favoritesFirst } from '@/lib/nutrition-favorites';
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

  // Preferidos (N3): primeiros nos resultados e com a estrela marcada. Sem a migração 102 (503) ou sem
  // sessão a consulta falha em silêncio: a busca segue sem estrelas.
  const queryClient = useQueryClient();
  const favoritos = useQuery({ queryKey: favoriteKeys.all, queryFn: fetchFavorites, retry: false, staleTime: 30_000 });
  const marcados = useMemo(() => favoriteIds(favoritos.data?.items ?? []), [favoritos.data]);
  const alternar = useMutation({
    mutationFn: async (item: FoodSearchItem) => {
      if (marcados.has(item.id)) await removeFavorite(item.id);
      else await addFavorite({ food_id: item.id });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: favoriteKeys.all }),
  });
  const itens = useMemo(() => favoritesFirst(query.data?.items ?? [], marcados), [query.data, marcados]);
  const comEstrela = favoritos.isSuccess;

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
      {alternar.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(alternar.error, 'Não foi possível atualizar os preferidos.').message}
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
                {comEstrela && <Table.Th w={52} aria-label="Preferido" />}
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
                  {comEstrela && (
                    <Table.Td>
                      <ActionIcon
                        variant="subtle"
                        color={marcados.has(item.id) ? 'yellow' : 'gray'}
                        aria-label={marcados.has(item.id) ? `Remover ${item.name} dos preferidos` : `Favoritar ${item.name}`}
                        aria-pressed={marcados.has(item.id)}
                        loading={alternar.isPending && alternar.variables?.id === item.id}
                        style={{ minWidth: 44, minHeight: 44 }}
                        onClick={(event) => {
                          event.stopPropagation(); // a linha seleciona o alimento; a estrela só alterna o preferido
                          alternar.mutate(item);
                        }}
                      >
                        {marcados.has(item.id) ? <IconStarFilled size={20} /> : <IconStar size={20} />}
                      </ActionIcon>
                    </Table.Td>
                  )}
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
