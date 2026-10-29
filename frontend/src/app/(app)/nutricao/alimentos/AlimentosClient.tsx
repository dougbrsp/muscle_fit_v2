'use client';

import { Alert, Button, Group, Paper, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { BarcodeLookup } from '@/components/nutrition/BarcodeLookup';
import { FoodCitationFooter } from '@/components/nutrition/FoodCitationFooter';
import { FoodDetailCard } from '@/components/nutrition/FoodDetailCard';
import { FoodForm } from '@/components/nutrition/FoodForm';
import { FoodSearch } from '@/components/nutrition/FoodSearch';
import { LabelPhotoUpload } from '@/components/nutrition/LabelPhotoUpload';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { UsdaSearch } from '@/components/nutrition/UsdaSearch';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { fetchFood, foodKeys, nutritionErrorInfo, promoteFood } from '@/lib/nutrition-api';
import { LABEL_LEGIBILITY_OPTIONS, LABEL_PHOTO, nutritionLabel } from '@/lib/nutrition-constants';
import { foodFormFromPrefill } from '@/lib/nutrition-food-form';
import type { ExternalFoodResponse, FoodSaveResponse, LabelPhotoReading } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

type Editing = { mode: 'new' } | { mode: 'edit' } | { mode: 'label'; reading: LabelPhotoReading } | null;

/**
 * `/nutricao/alimentos` — catálogo de alimentos (Lote L3a). Busca na TACO 4ª
 * ed. e nos alimentos cadastrados; o usuário cadastra os próprios (privados,
 * decisão D-G) e o admin promove a públicos. Desde o L3b, consulta por código
 * de barras (Open Food Facts) e importação do USDA FoodData Central, que entram
 * públicos e somente leitura, com a citação de cada fonte. Busca, ordem,
 * permissões, regras de entrada e o mapeamento das fontes externas são da API
 * (motor `domain/nutrition`: `food_text`, `catalog`, `external_sources`); a tela
 * só exibe. Desde o L6, a foto do rótulo: a IA lê a tabela, o motor revisa
 * (`label_photo`) e o formulário abre pré-preenchido para o usuário conferir e
 * salvar (`source=label_photo`). Sem leitor de código de barras pela câmera.
 */
export function AlimentosClient() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [saved, setSaved] = useState<FoodSaveResponse | null>(null);
  const [external, setExternal] = useState<ExternalFoodResponse | null>(null);
  const [unreadable, setUnreadable] = useState<LabelPhotoReading | null>(null);
  // Cada leitura nova remonta o formulário (o estado inicial vem da leitura).
  const [labelReads, setLabelReads] = useState(0);

  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  const isAdmin = currentUserQuery.data?.role === 'admin';

  const detailQuery = useQuery({
    queryKey: foodKeys.detail(selectedId ?? 0),
    queryFn: () => fetchFood(selectedId as number),
    enabled: selectedId !== null,
    retry: false,
  });

  const promoteMutation = useMutation({
    mutationFn: (foodId: number) => promoteFood(foodId),
    onSuccess: async (food) => {
      await qc.invalidateQueries({ queryKey: foodKeys.detail(food.id) });
      await qc.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
    },
  });

  /** Leitura da foto do rótulo: legível abre o formulário pré-preenchido; ilegível mostra o porquê. */
  function afterLabelRead(reading: LabelPhotoReading) {
    setSaved(null);
    setExternal(null);
    if (reading.readable) {
      setUnreadable(null);
      setLabelReads((n) => n + 1);
      setEditing({ mode: 'label', reading });
    } else {
      setEditing(null);
      setUnreadable(reading);
    }
  }

  async function afterSave(food: FoodSaveResponse) {
    setEditing(null);
    setUnreadable(null);
    setSaved(food);
    setExternal(null);
    setSelectedId(food.id);
    await qc.invalidateQueries({ queryKey: foodKeys.detail(food.id) });
    await qc.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
  }

  /** Código de barras ou importação do USDA: abre o item (já gravado pela API) no detalhe. */
  async function afterExternal(food: ExternalFoodResponse) {
    setEditing(null);
    setSaved(null);
    setExternal(food);
    setSelectedId(food.id);
    await qc.invalidateQueries({ queryKey: foodKeys.detail(food.id) });
    await qc.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
  }

  const detail = detailQuery.data ?? null;

  return (
    <Stack gap="xl">
      <Stack gap="xs">
        <NutritionSubnav active="/nutricao/alimentos" />
        <Title order={1} c={BRAND.textPrimary}>
          Alimentos
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Busque na Tabela Brasileira de Composição de Alimentos (TACO) e nos seus alimentos, consulte um produto pelo
          código de barras ou importe do USDA. Alimento novo cadastrado por você fica visível só para você até o
          administrador torná-lo público.
        </Text>
      </Stack>

      <FoodSearch
        isAdmin={isAdmin}
        selectedId={selectedId}
        onSelect={(item) => {
          setSelectedId(item.id);
          setEditing(null);
          setSaved(null);
          setExternal(null);
        }}
      />

      <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }} spacing="md">
        <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
          <Stack gap="xs">
            <Title order={4} c={BRAND.textPrimary}>
              Foto do rótulo
            </Title>
            <LabelPhotoUpload onRead={afterLabelRead} />
          </Stack>
        </Paper>
        <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
          <Stack gap="xs">
            <Title order={4} c={BRAND.textPrimary}>
              Código de barras
            </Title>
            <BarcodeLookup onFound={afterExternal} />
          </Stack>
        </Paper>
        <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
          <Stack gap="xs">
            <Title order={4} c={BRAND.textPrimary}>
              USDA FoodData Central
            </Title>
            <UsdaSearch onImported={afterExternal} />
          </Stack>
        </Paper>
      </SimpleGrid>

      {unreadable && (
        <NutritionLimitations
          items={unreadable.errors}
          title={nutritionLabel(LABEL_LEGIBILITY_OPTIONS, unreadable.legibility) ?? 'Foto do rótulo'}
          tone="error"
        />
      )}

      {external && external.warnings.length > 0 && (
        <NutritionLimitations items={external.warnings} title={`Avisos sobre ${external.name}`} tone="warning" />
      )}

      {saved && saved.warnings.length > 0 && (
        <NutritionLimitations items={saved.warnings} title={`Alimento salvo, com avisos: ${saved.name}`} tone="warning" />
      )}
      {saved && saved.warnings.length === 0 && (
        <Text size="sm" c="green">
          Alimento salvo: {saved.name}.
        </Text>
      )}

      {detailQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(detailQuery.error, 'Não foi possível carregar o alimento.').message}
        </Alert>
      )}
      {promoteMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(promoteMutation.error, 'Não foi possível tornar o alimento público.').message}
        </Alert>
      )}

      {editing ? (
        <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
          <Stack gap="sm">
            <Title order={3} c={BRAND.textPrimary}>
              {editing.mode === 'edit' ? 'Editar alimento' : editing.mode === 'label' ? 'Alimento da foto do rótulo' : 'Novo alimento'}
            </Title>
            {editing.mode === 'label' && (
              <>
                <Text size="sm" c={BRAND.textMuted}>
                  {nutritionLabel(LABEL_LEGIBILITY_OPTIONS, editing.reading.legibility)}. Confira cada valor com a
                  embalagem antes de salvar: a leitura pode errar.
                </Text>
                <NutritionLimitations items={editing.reading.errors} title="Corrija antes de salvar" tone="error" />
                <NutritionLimitations items={editing.reading.warnings} title="Confira" tone="warning" />
                {editing.reading.ai_notes.length > 0 && (
                  <NutritionLimitations
                    items={editing.reading.ai_notes.map((nota, i) => ({ code: `ai_note_${i}`, label: nota }))}
                    title="Observações da leitura"
                    tone="info"
                  />
                )}
              </>
            )}
            <FoodForm
              key={editing.mode === 'edit' ? (detail?.id ?? 'editar') : editing.mode === 'label' ? `rotulo-${labelReads}` : 'novo'}
              food={editing.mode === 'edit' ? detail : null}
              initial={editing.mode === 'label' ? foodFormFromPrefill(editing.reading.food) : undefined}
              createSource={editing.mode === 'label' ? LABEL_PHOTO.food_source : undefined}
              onSaved={afterSave}
              onCancel={() => setEditing(null)}
            />
          </Stack>
        </Paper>
      ) : (
        <Group>
          <Button
            color="brand"
            leftSection={<IconPlus size={16} />}
            onClick={() => {
              setEditing({ mode: 'new' });
              setSaved(null);
              setExternal(null);
            }}
          >
            Cadastrar alimento
          </Button>
        </Group>
      )}

      {detail && !editing && (
        <FoodDetailCard
          food={detail}
          onEdit={() => {
            setEditing({ mode: 'edit' });
            setSaved(null);
            setExternal(null);
          }}
          onPromote={() => promoteMutation.mutate(detail.id)}
          promoting={promoteMutation.isPending}
          onPortionAdded={() => qc.invalidateQueries({ queryKey: foodKeys.detail(detail.id) })}
        />
      )}

      <FoodCitationFooter />
    </Stack>
  );
}
