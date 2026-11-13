'use client';

import { Alert, Paper, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { FoodCitationFooter } from '@/components/nutrition/FoodCitationFooter';
import { FoodDetailCard } from '@/components/nutrition/FoodDetailCard';
import { FoodForm } from '@/components/nutrition/FoodForm';
import { FoodRegistrationFlow } from '@/components/nutrition/FoodRegistrationFlow';
import { FoodSearch } from '@/components/nutrition/FoodSearch';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { alimentosControls } from '@/lib/screen-permissions';
import { existingFoodSelection } from '@/lib/nutrition-barcode-flow';
import { fetchFood, foodKeys, nutritionErrorInfo, promoteFood } from '@/lib/nutrition-api';
import type { ExternalFoodResponse, FoodSaveResponse } from '@/lib/nutrition-types';
import { usePresentationSlot } from '@/lib/presentation-slots';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

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
 * salvar (`source=label_photo`). Desde o N3, o código de barras também se lê pela câmera, e o
 * código sem produto no Open Food Facts abre o cadastro manual com o EAN preenchido.
 */
export function AlimentosClient() {
  const qc = useQueryClient();
  const Header = usePresentationSlot('nutritionHeader');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // Edição de um alimento já aberto no detalhe; o cadastro novo é o fluxo único (`FoodRegistrationFlow`).
  const [editing, setEditing] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  // O detalhe (onde fica o botão Editar) some ao editar: leva a vista até o formulário de edição.
  useEffect(() => {
    if (editing) editorRef.current?.scrollIntoView?.({ block: 'start' });
  }, [editing]);
  const detailRef = useRef<HTMLDivElement>(null);
  // Sobe a cada alimento aberto (busca, cadastro, produto achado, existente), mesmo o que já estava aberto.
  const [scrollTick, setScrollTick] = useState(0);
  const [saved, setSaved] = useState<FoodSaveResponse | null>(null);
  const [external, setExternal] = useState<ExternalFoodResponse | null>(null);
  // Muda depois de salvar/abrir um produto: o fluxo de cadastro volta limpo.
  const [resets, setResets] = useState(0);

  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  // admin.catalogos/ver (RBAC): privados de todos e promoção.
  const canListPrivate = alimentosControls(currentUserQuery.data?.permissions).canListPrivate;

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

  /** `doCadastro`: veio do fluxo de cadastro (que volta limpo); a edição não mexe no cadastro em andamento. */
  async function afterSave(food: FoodSaveResponse, doCadastro: boolean) {
    if (doCadastro) setResets((n) => n + 1);
    setEditing(false);
    setSaved(food);
    setExternal(null);
    setSelectedId(food.id);
    setScrollTick((n) => n + 1);
    await qc.invalidateQueries({ queryKey: foodKeys.detail(food.id) });
    await qc.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
  }

  /** Código de barras ou importação do USDA: abre o item (já gravado pela API) no detalhe. */
  async function afterExternal(food: ExternalFoodResponse) {
    setEditing(false);
    setSaved(null);
    setExternal(food);
    setSelectedId(food.id);
    setScrollTick((n) => n + 1);
    await qc.invalidateQueries({ queryKey: foodKeys.detail(food.id) });
    await qc.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
  }

  /** EAN já cadastrado (409 do cadastro): abre o alimento existente no detalhe. */
  async function openExisting(foodId: number, doCadastro: boolean) {
    const selecao = existingFoodSelection(foodId);
    if (doCadastro) setResets((n) => n + 1);
    setEditing(selecao.editing !== null);
    setSaved(null);
    setExternal(null);
    setSelectedId(selecao.selectedId);
    setScrollTick((n) => n + 1);
    await qc.invalidateQueries({ queryKey: foodKeys.detail(foodId) });
  }

  const detail = detailQuery.data ?? null;
  // O detalhe fica abaixo do cadastro: ao escolher um alimento na busca, leva a vista até ele.
  const detailId = detail?.id ?? null;
  useEffect(() => {
    if (!editing) detailRef.current?.scrollIntoView?.({ block: 'start' });
  }, [detailId, editing, scrollTick]);

  return (
    <Stack gap="xl">
      {Header ? (
        <Header
          active="/nutricao/alimentos"
          title="Alimentos"
          description="Busque na TACO e nos seus alimentos, consulte um produto pelo código de barras ou importe do USDA. Alimento novo cadastrado por você fica visível só para você até o administrador torná-lo público."
        />
      ) : (
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
      )}

      <FoodSearch
        isAdmin={canListPrivate}
        selectedId={selectedId}
        onSelect={(item) => {
          setSelectedId(item.id);
          setScrollTick((n) => n + 1);
          setEditing(false);
          setSaved(null);
          setExternal(null);
        }}
      />

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

      {editing && (
        <Paper ref={editorRef} withBorder radius="md" p="md" bg={BRAND.bgElevated}>
          <Stack gap="sm">
            <Title order={3} c={BRAND.textPrimary}>
              Editar alimento
            </Title>
            <FoodForm
              key={detail?.id ?? 'editar'}
              food={detail}
              onUseExisting={(id) => openExisting(id, false)}
              onSaved={(food) => afterSave(food, false)}
              onCancel={() => setEditing(false)}
            />
          </Stack>
        </Paper>
      )}
      {/* Fica montado (só oculto) durante a edição: o cadastro em andamento não se perde ao abrir "Editar". */}
      <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated} display={editing ? 'none' : undefined}>
        <Stack gap="sm">
          <Title order={3} c={BRAND.textPrimary}>
            Cadastrar alimento
          </Title>
          <FoodRegistrationFlow
            withUsda
            onSaved={(food) => afterSave(food, true)}
            onFound={afterExternal}
            onUseExisting={(id) => openExisting(id, true)}
            resetSignal={resets}
          />
        </Stack>
      </Paper>

      {!editing && (detail || saved || external) && (
        <Stack ref={detailRef} gap="md">
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
          {detail && (
            <FoodDetailCard
              food={detail}
              onEdit={() => {
                setEditing(true);
                setSaved(null);
                setExternal(null);
              }}
              onPromote={() => promoteMutation.mutate(detail.id)}
              promoting={promoteMutation.isPending}
              onPortionAdded={() => qc.invalidateQueries({ queryKey: foodKeys.detail(detail.id) })}
            />
          )}
        </Stack>
      )}

      <FoodCitationFooter />
    </Stack>
  );
}
