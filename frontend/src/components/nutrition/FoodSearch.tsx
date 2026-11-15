'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
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
import { IconAlertTriangle, IconBolt, IconPlus, IconSearch, IconStar, IconStarFilled } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  addFavorite,
  favoriteKeys,
  fetchFavorites,
  foodKeys,
  nutritionErrorInfo,
  searchFoods,
} from '@/lib/nutrition-api';
import { favoriteErrorLines, removeFavoriteIfPresent } from '@/lib/nutrition-favorites';
import { FOOD_PER_100_FIELDS, FOOD_REQUIRED_FIELDS, FOOD_SOURCE_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import { formatWithUnit } from '@/lib/nutrition-format';
import { FoodOriginBadge } from './FoodOriginBadge';
import type { FoodSearchItem, FoodSearchScope } from '@/lib/nutrition-types';
import { usePresentationSlot } from '@/lib/presentation-slots';
import { BRAND } from '@/theme';

// Colunas resumidas da tabela de resultados: os campos que o dono exige no
// cadastro (energia e os três macros, `catalog.REQUIRED_PER_100_FIELDS`), com o
// rótulo e a unidade da RDC 429 do JSON gerado. Os demais ficam no detalhe.
const COLUNAS = FOOD_PER_100_FIELDS.filter((campo) => FOOD_REQUIRED_FIELDS.includes(campo.code));
const ATRASO_DA_BUSCA_MS = 300;
/** Chave da mutação da estrela: a tela de preferidos a observa para travar a reordenação enquanto grava. */
export const FAVORITE_TOGGLE_KEY = ['nutrition-favorite-toggle'] as const;

type Props = {
  /** Nome legado: significa admin.catalogos/ver (permissão efetiva, não role); enxerga alimentos privados de todos (`scope=private_all`). */
  isAdmin: boolean;
  selectedId: number | null;
  onSelect: (item: FoodSearchItem) => void;
  /** Quando dado, mostra "Cadastrar novo alimento" sempre, com o termo buscado (pode ser vazio). */
  onRegister?: (term: string) => void;
  registerDisabled?: boolean;
  /** Quando dado, mostra "Lançamento rápido (só macros)" ao lado do cadastro: item sem alimento no catálogo. */
  onQuickEntry?: () => void;
  /** Chamado só depois de o favorito ser gravado com sucesso (marcar a estrela, nunca ao desmarcar). */
  onFavorited?: (item: FoodSearchItem) => void;
};

/**
 * Busca no catálogo. A API filtra (FULLTEXT), ordena pela nota do motor
 * (`food_text`) e diz de que fonte é cada item, com a citação; a tela só exibe.
 */
export function FoodSearch({ isAdmin, selectedId, onSelect, onRegister, registerDisabled = false, onQuickEntry, onFavorited }: Props) {
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

  // Preferidos (N3): a API já devolve os preferidos primeiro (antes do corte) e `is_favorite`; a tela só
  // exibe a estrela. Sem a migração 102 (503) a consulta falha em silêncio e a busca segue sem estrelas.
  // No escopo `private_all` (admin) os itens são de outros usuários: sem estrela.
  const queryClient = useQueryClient();
  const favoritos = useQuery({ queryKey: favoriteKeys.all, queryFn: fetchFavorites, retry: false, staleTime: 30_000 });
  const alternar = useMutation({
    mutationKey: FAVORITE_TOGGLE_KEY,
    mutationFn: async (item: FoodSearchItem) => {
      if (item.is_favorite) await removeFavoriteIfPresent(item.id);
      else await addFavorite({ food_id: item.id });
    },
    onSuccess: (_, item) => {
      if (!item.is_favorite) onFavorited?.(item);
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: favoriteKeys.all });
      await queryClient.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
    },
  });
  const Alternative = usePresentationSlot('foodResults');
  const itens = query.data?.items ?? [];
  const comEstrela = favoritos.isSuccess && scope === 'visible';

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

      {(onQuickEntry || onRegister) && (
        <Stack gap={4}>
          <Group gap="xs">
            {onQuickEntry && (
              <Button
                variant="filled"
                color="brand"
                leftSection={<IconBolt size={16} />}
                onClick={onQuickEntry}
                disabled={registerDisabled}
                style={{ minHeight: 44 }}
              >
                Lançamento rápido (só macros)
              </Button>
            )}
            {onRegister && (
              <Button
                variant="light"
                color="brand"
                leftSection={<IconPlus size={16} />}
                onClick={() => onRegister(texto.trim())}
                disabled={registerDisabled}
                style={{ minHeight: 44 }}
              >
                Cadastrar novo alimento
              </Button>
            )}
          </Group>
          {onQuickEntry && (
            <Text size="xs" c={BRAND.textMuted}>
              Já sabe os macros? Use o lançamento rápido: não salva alimento nem pede peso. Cadastrar cria um alimento
              reutilizável no catálogo.
            </Text>
          )}
        </Stack>
      )}

      {query.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(query.error, 'Não foi possível buscar alimentos.').message}
        </Alert>
      )}
      {alternar.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} withCloseButton onClose={() => alternar.reset()}>
          {favoriteErrorLines(alternar.error, 'Não foi possível atualizar os preferidos.').map((linha) => (
            <Text key={linha} size="sm">
              {linha}
            </Text>
          ))}
        </Alert>
      )}
      {busca.length > 0 && query.isSuccess && itens.length === 0 && (
        <Text c={BRAND.textMuted} size="sm">
          Nenhum alimento encontrado. Você pode cadastrar um alimento novo.
        </Text>
      )}

      {itens.length > 0 && Alternative && (
        <Alternative
          items={itens}
          selectedId={selectedId}
          withStar={comEstrela}
          onSelect={onSelect}
          onToggleFavorite={(item) => alternar.mutate(item)}
          togglingId={alternar.isPending ? (alternar.variables?.id ?? null) : null}
        />
      )}

      {itens.length > 0 && !Alternative && (
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
                        color={item.is_favorite ? 'yellow' : 'gray'}
                        aria-label={item.is_favorite ? `Remover ${item.name} dos preferidos` : `Favoritar ${item.name}`}
                        aria-pressed={item.is_favorite}
                        loading={alternar.isPending && alternar.variables?.id === item.id}
                        style={{ minWidth: 44, minHeight: 44 }}
                        onClick={(event) => {
                          event.stopPropagation(); // a linha seleciona o alimento; a estrela só alterna o preferido
                          alternar.mutate(item);
                        }}
                      >
                        {item.is_favorite ? <IconStarFilled size={20} /> : <IconStar size={20} />}
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
                        <FoodOriginBadge
                          origin={item.origin}
                          fallback={nutritionLabel(FOOD_SOURCE_OPTIONS, item.source)}
                          isPublic={item.is_public}
                        />
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
