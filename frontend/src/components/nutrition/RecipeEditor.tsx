'use client';

import { ActionIcon, Alert, Button, Group, Paper, Select, Stack, Table, Text, TextInput, Textarea, Title } from '@mantine/core';
import { IconAlertTriangle, IconArrowDown, IconArrowUp, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { FoodSearch } from './FoodSearch';
import { FoodRegistrationFlow } from './FoodRegistrationFlow';
import { NutritionLimitations } from './NutritionLimitations';
import { RDC429_FIELDS, UNIT_OPTIONS } from '@/lib/nutrition-constants';
import { createRecipe, fetchFood, fetchRecipeVersions, foodKeys, nutritionErrorInfo, previewRecipe, recipeKeys, updateRecipe } from '@/lib/nutrition-api';
import { unitUsesPortion } from '@/lib/nutrition-meal-form';
import type {
  RecipeCatalogStatus,
  RecipeDetail,
  RecipeIngredientPayload,
  RecipePreview,
  RecipeSavePayload,
} from '@/lib/nutrition-types';
import { ApiError } from '@/lib/api';
import { BRAND } from '@/theme';

type Row = { key: number; food_id: number; label: string; quantity: string; unit: string; portion_id: number | null; status?: RecipeCatalogStatus };

const STATUS_AVISO: Record<'outdated' | 'unavailable', string> = {
  outdated: 'Valores do alimento mudaram no catálogo.',
  unavailable: 'Alimento não está mais disponível; mantido com os valores gravados.',
};

type Props = {
  /** Receita em edição ou `null` (nova). */
  recipe: RecipeDetail | null;
  onSaved: (recipe: RecipeDetail) => void;
  onCancel: () => void;
};

const text = (n: number | null | undefined) => (n == null ? '' : String(n));
// Texto digitado -> número da API (vírgula decimal); vazio ou inválido vira `null`/NaN e a API acusa.
const toNumber = (s: string) => (s.trim() === '' ? NaN : finiteOrNaN(Number(s.trim().replace(',', '.'))));
const finiteOrNaN = (n: number) => (Number.isFinite(n) ? n : NaN);
const fmt = (n: number | null | undefined) => (n == null ? '—' : String(n));

/** `index` do erro de ingrediente: campo `index` da API ou sufixo `:<índice>` do `code`. */
function ingredientIndex(error: unknown): number | null {
  const detail = error instanceof ApiError && typeof error.detail === 'object' && error.detail ? (error.detail as Record<string, unknown>) : null;
  if (!detail) return null;
  if (typeof detail.index === 'number') return detail.index;
  const m = /:(\d+)$/.exec(String(detail.code ?? ''));
  return m ? Number(m[1]) : null;
}

function PortionSelect({ foodId, value, onChange }: { foodId: number; value: number | null; onChange: (id: number | null) => void }) {
  const detalhe = useQuery({ queryKey: foodKeys.detail(foodId), queryFn: () => fetchFood(foodId), retry: false, staleTime: 60_000 });
  const data = (detalhe.data?.portions ?? []).map((p) => ({ value: String(p.id), label: `${p.label} (${p.amount} ${detalhe.data?.basis ?? ''})` }));
  return (
    <Select
      label="Porção"
      placeholder="Escolha uma porção"
      data={data}
      value={value != null ? String(value) : null}
      onChange={(v) => onChange(v ? Number(v) : null)}
      clearable
      w={220}
      nothingFoundMessage="Alimento sem porção cadastrada"
    />
  );
}

function initialState(recipe: RecipeDetail | null) {
  const v = recipe?.recipe;
  const eff = v?.effective as { overridden_fields?: string[]; effective?: Record<string, number | null> } | null | undefined;
  const overrides: Record<string, string> = {};
  for (const campo of eff?.overridden_fields ?? []) overrides[campo] = text(eff?.effective?.[campo]);
  return {
    name: recipe?.name ?? '',
    instructions: v?.instructions ?? '',
    finalWeight: text(v?.final_weight_g),
    overrides,
    rows: (v?.ingredients ?? []).map((i, n): Row => ({
      key: n,
      food_id: i.food_id,
      label: i.label ?? `Alimento ${i.food_id}`,
      quantity: text(i.quantity),
      unit: i.unit ?? 'g',
      portion_id: i.portion_id,
      status: i.catalog_status,
    })),
  };
}

export function RecipeEditor({ recipe, onSaved, onCancel }: Props) {
  const initial = useMemo(() => initialState(recipe), [recipe]);
  const [name, setName] = useState(initial.name);
  const [instructions, setInstructions] = useState(initial.instructions);
  const [finalWeight, setFinalWeight] = useState(initial.finalWeight);
  const [overrides, setOverrides] = useState(initial.overrides);
  const [rows, setRows] = useState<Row[]>(initial.rows);
  const [preview, setPreview] = useState<RecipePreview | null>(null);
  const [stale, setStale] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [confirmRefresh, setConfirmRefresh] = useState(false);
  const [cadastro, setCadastro] = useState<{ nome: string } | null>(null);
  const [linkPendente, setLinkPendente] = useState<HTMLAnchorElement | null>(null);
  const liberaLink = useRef(false);
  const queryClient = useQueryClient();
  const versions = useQuery({
    queryKey: recipeKeys.versions(recipe?.id ?? 0),
    queryFn: () => fetchRecipeVersions(recipe!.id),
    enabled: recipe != null,
    retry: false,
  });
  const nextKey = useRef(initial.rows.length);

  const snapshot = (s: { name: string; instructions: string; finalWeight: string; overrides: unknown; rows: unknown }) => JSON.stringify(s);
  const dirty = snapshot({ name, instructions, finalWeight, overrides, rows }) !== snapshot(initial);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // Navegação interna (Link) não dispara beforeunload: intercepta o clique em link interno enquanto há alterações.
  useEffect(() => {
    if (!dirty) return;
    const onClick = (e: MouseEvent) => {
      if (liberaLink.current) {
        liberaLink.current = false;
        return;
      }
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
      const href = a.getAttribute('href') ?? '';
      if (!href.startsWith('/') || href.startsWith('//')) return;
      e.preventDefault();
      e.stopPropagation();
      setLinkPendente(a);
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [dirty]);

  function seguirLink() {
    const a = linkPendente;
    setLinkPendente(null);
    if (!a) return;
    liberaLink.current = true;
    a.click();
  }

  function edit<T>(setter: (v: T) => void) {
    return (v: T) => {
      setter(v);
      setStale(true);
    };
  }

  function payload(): RecipeSavePayload {
    const ov: Record<string, number | null> = {};
    for (const [campo, valor] of Object.entries(overrides)) if (valor.trim() !== '') ov[campo] = toNumber(valor);
    return {
      name: name.trim(),
      instructions: instructions.trim() === '' ? null : instructions.trim(),
      ingredients: rows.map((r): RecipeIngredientPayload => ({
        food_id: r.food_id,
        quantity: toNumber(r.quantity),
        unit: r.unit,
        portion_id: unitUsesPortion(r.unit) ? r.portion_id : null,
      })),
      final_weight_g: toNumber(finalWeight),
      overrides: Object.keys(ov).length > 0 ? ov : null,
    };
  }

  const previewMutation = useMutation({
    mutationFn: () => previewRecipe(payload()),
    onSuccess: (data) => {
      setPreview(data);
      setStale(false);
      saveMutation.reset();
    },
  });
  const saveMutation = useMutation({
    mutationFn: (refresh?: boolean) =>
      recipe ? updateRecipe(recipe.id, refresh ? { ...payload(), refresh_ingredients: true } : payload()) : createRecipe(payload()),
    onSuccess: onSaved,
  });

  const error = saveMutation.error ?? previewMutation.error;
  const errorIndex = error ? ingredientIndex(error) : null;
  const errorInfo = error ? nutritionErrorInfo(error, 'Não foi possível processar a receita.') : null;

  function updateRow(i: number, patch: Partial<Row>) {
    // Mudou quantidade/unidade/porção: o salvar resolve do catálogo, o aviso gravado deixa de valer.
    edit(setRows)(rows.map((r, n) => (n === i ? { ...r, ...patch, status: undefined } : r)));
  }
  // Ajuste digitado que não é número: não envia (viraria null e seria descartado em silêncio).
  const invalidOverrides = Object.entries(overrides).filter(([, v]) => v.trim() !== '' && Number.isNaN(toNumber(v))).map(([c]) => c);
  function run(mutation: { mutate: () => void }, other: { reset: () => void }) {
    if (invalidOverrides.length > 0) return;
    other.reset();
    mutation.mutate();
  }
  // O índice do erro aponta para a linha antiga: limpa ao reordenar/remover.
  function clearErrors() {
    saveMutation.reset();
    previewMutation.reset();
  }
  function move(i: number, d: -1 | 1) {
    clearErrors();
    const novo = [...rows];
    [novo[i], novo[i + d]] = [novo[i + d], novo[i]];
    edit(setRows)(novo);
  }
  const temDesatualizado = rows.some((r) => r.status === 'outdated');
  function atualizarIngredientes() {
    setConfirmRefresh(false);
    if (invalidOverrides.length > 0) return;
    previewMutation.reset();
    saveMutation.mutate(true);
  }
  function adicionarCadastrado(food: { id: number; name: string; basis: string }) {
    const key = nextKey.current++;
    setRows((atual) => [...atual, { key, food_id: food.id, label: food.name, quantity: '', unit: food.basis, portion_id: null }]);
    setStale(true);
    setCadastro(null);
    void queryClient.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
  }
  const [erroExistente, setErroExistente] = useState<string | null>(null);
  async function usarExistente(foodId: number) {
    setErroExistente(null);
    try {
      adicionarCadastrado(await queryClient.fetchQuery({ queryKey: foodKeys.detail(foodId), queryFn: () => fetchFood(foodId) }));
    } catch (e) {
      setErroExistente(nutritionErrorInfo(e, 'Não foi possível carregar o alimento existente.').message);
    }
  }
  function leave() {
    if (dirty) setConfirmLeave(true);
    else onCancel();
  }

  const fields = preview ? RDC429_FIELDS.filter((f) => f.code in preview.per_100_effective) : [];

  return (
    <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
      <Stack gap="md">
        <Title order={3} c={BRAND.textPrimary}>
          {recipe ? 'Editar receita' : 'Nova receita'}
        </Title>

        <TextInput label="Nome da receita" value={name} onChange={(e) => edit(setName)(e.currentTarget.value)} required />

        <Textarea
          label="Modo de preparo"
          description="Texto simples; não aceita formatação."
          value={instructions}
          onChange={(e) => setInstructions(e.currentTarget.value)}
          autosize
          minRows={3}
        />

        <Stack gap="xs">
          <Text fw={600} c={BRAND.textPrimary}>
            Ingredientes
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Informe a quantidade do ingrediente cru, como vem da embalagem. Uma receita não pode ser ingrediente de outra receita.
          </Text>
          <FoodSearch
            isAdmin={false}
            selectedId={null}
            excludeRecipes
            onRegister={(termo) => setCadastro({ nome: termo })}
            onSelect={(item) => {
              edit(setRows)([
                ...rows,
                { key: nextKey.current++, food_id: item.id, label: item.name, quantity: '', unit: item.basis, portion_id: null },
              ]);
            }}
          />
          {cadastro && (
            <Paper withBorder radius="md" p="md" data-testid="cadastro-alimento">
              <FoodRegistrationFlow
                initialName={cadastro.nome}
                onSaved={adicionarCadastrado}
                onFound={adicionarCadastrado}
                onUseExisting={usarExistente}
                onCancel={() => setCadastro(null)}
              />
              {erroExistente && (
                <Text size="xs" c="red" role="alert" data-testid="erro-existente">
                  {erroExistente}
                </Text>
              )}
            </Paper>
          )}
          {temDesatualizado && recipe && (
            <Group>
              <Button variant="light" color="yellow" onClick={() => setConfirmRefresh(invalidOverrides.length === 0)}>
                Atualizar ingredientes e recalcular
              </Button>
            </Group>
          )}
          {confirmRefresh && (
            <Alert color="yellow" title="Atualizar ingredientes?" icon={<IconAlertTriangle size={16} />}>
              Isso substitui os valores gravados de todos os ingredientes pelos do catálogo atual e salva uma nova versão da receita.
              <Group mt="xs">
                <Button size="xs" onClick={atualizarIngredientes}>
                  Confirmar atualização
                </Button>
                <Button variant="default" size="xs" onClick={() => setConfirmRefresh(false)}>
                  Manter valores gravados
                </Button>
              </Group>
            </Alert>
          )}
          {rows.length === 0 && (
            <Text size="sm" c={BRAND.textMuted}>
              Nenhum ingrediente. Busque um alimento acima para adicionar.
            </Text>
          )}
          {rows.map((r, i) => (
            <Stack key={r.key} gap={2} data-testid="ingrediente">
              <Group gap="xs" align="flex-end" wrap="wrap">
                <Text size="sm" fw={600} c={BRAND.textPrimary} style={{ flex: '1 1 200px' }}>
                  {r.label}
                </Text>
                <TextInput
                  label="Quantidade"
                  inputMode="decimal"
                  value={r.quantity}
                  onChange={(e) => updateRow(i, { quantity: e.currentTarget.value })}
                  w={120}
                  error={errorIndex === i ? true : undefined}
                />
                <Select
                  label="Unidade"
                  data={[...UNIT_OPTIONS]}
                  value={r.unit}
                  onChange={(u) => u && updateRow(i, { unit: u, portion_id: unitUsesPortion(u) ? r.portion_id : null })}
                  allowDeselect={false}
                  w={190}
                />
                {unitUsesPortion(r.unit) && (
                  <PortionSelect foodId={r.food_id} value={r.portion_id} onChange={(id) => updateRow(i, { portion_id: id })} />
                )}
                <ActionIcon variant="subtle" aria-label={`Subir ${r.label}`} disabled={i === 0} onClick={() => move(i, -1)}>
                  <IconArrowUp size={16} />
                </ActionIcon>
                <ActionIcon variant="subtle" aria-label={`Descer ${r.label}`} disabled={i === rows.length - 1} onClick={() => move(i, 1)}>
                  <IconArrowDown size={16} />
                </ActionIcon>
                <ActionIcon
                  variant="subtle"
                  color="red"
                  aria-label={`Remover ${r.label}`}
                  onClick={() => {
                    clearErrors();
                    edit(setRows)(rows.filter((_, n) => n !== i));
                  }}
                >
                  <IconTrash size={16} />
                </ActionIcon>
              </Group>
              {(r.status === 'outdated' || r.status === 'unavailable') && (
                <Text size="xs" c="yellow" data-testid="status-ingrediente">
                  {STATUS_AVISO[r.status]}
                </Text>
              )}
              {errorIndex === i && errorInfo && (
                <Text size="xs" c="red" role="alert">
                  {errorInfo.message}
                </Text>
              )}
            </Stack>
          ))}
        </Stack>

        <TextInput
          label="Peso final pronto (g)"
          description="Quanto a receita pesa depois de pronta."
          inputMode="decimal"
          value={finalWeight}
          onChange={(e) => edit(setFinalWeight)(e.currentTarget.value)}
          w={220}
        />

        <Group>
          <Button variant="light" onClick={() => run(previewMutation, saveMutation)} loading={previewMutation.isPending}>
            Calcular
          </Button>
        </Group>

        {invalidOverrides.length > 0 && (
          <Alert color="yellow" icon={<IconAlertTriangle size={16} />} data-testid="ajuste-invalido">
            Há ajuste manual com número inválido. Corrija ou use &quot;Voltar ao calculado&quot; para calcular e salvar.
          </Alert>
        )}

        {errorInfo && errorIndex === null && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Não foi possível concluir">
            {errorInfo.message}
            {errorInfo.items.length > 0 && (
              <ul>
                {errorInfo.items.map((it) => (
                  <li key={it.code}>{it.label}</li>
                ))}
              </ul>
            )}
          </Alert>
        )}

        {preview && (
          <Stack gap="sm" data-testid="previa">
            {stale && (
              <Text size="sm" c="yellow">
                Você mudou a receita depois do último cálculo. Calcule de novo para atualizar.
              </Text>
            )}
            <Group gap="lg">
              <Text size="sm">Peso cru: {fmt(preview.raw_weight_g)} g</Text>
              <Text size="sm">Peso final: {fmt(preview.final_weight_g)} g</Text>
              {preview.yield_percent != null && <Text size="sm">Rendimento: {preview.yield_percent}%</Text>}
            </Group>
            {preview.incomplete_fields.length > 0 && (
              <Text size="sm" c="yellow">
                Campos incompletos (faltam dados em algum ingrediente):{' '}
                {preview.incomplete_fields.map((c) => RDC429_FIELDS.find((f) => f.code === c)?.label ?? c).join(', ')}.
              </Text>
            )}
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Nutriente</Table.Th>
                  <Table.Th>Total</Table.Th>
                  <Table.Th>Por 100 g</Table.Th>
                  <Table.Th>Ajustar por 100 g</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {fields.map((f) => {
                  const ajustado = preview.overridden_fields.includes(f.code);
                  return (
                    <Table.Tr key={f.code}>
                      <Table.Td>{f.label}</Table.Td>
                      <Table.Td>
                        {fmt(preview.totals[f.code])} {f.unit}
                      </Table.Td>
                      <Table.Td>
                        {fmt(preview.per_100_effective[f.code])} {f.unit}
                        {ajustado && (
                          <Text span size="xs" c="yellow">
                            {' '}
                            ajustado (calculado: {fmt(preview.per_100_calculated[f.code])})
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="nowrap">
                          <TextInput
                            aria-label={`Ajustar ${f.label} por 100 g`}
                            inputMode="decimal"
                            value={overrides[f.code] ?? ''}
                            onChange={(e) => {
                              const v = e.currentTarget.value;
                              edit(setOverrides)(v === '' ? Object.fromEntries(Object.entries(overrides).filter(([c]) => c !== f.code)) : { ...overrides, [f.code]: v });
                            }}
                            w={90}
                            error={invalidOverrides.includes(f.code) ? 'Número inválido' : undefined}
                          />
                          {f.code in overrides && (
                            <Button
                              size="compact-xs"
                              variant="subtle"
                              onClick={() => edit(setOverrides)(Object.fromEntries(Object.entries(overrides).filter(([c]) => c !== f.code)))}
                            >
                              Voltar ao calculado
                            </Button>
                          )}
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
            <NutritionLimitations items={preview.warnings} title="Avisos" tone="warning" />
            <NutritionLimitations items={preview.limitations} title="Limitações do cálculo" tone="info" />
          </Stack>
        )}

        {recipe && versions.data && versions.data.items.length > 0 && (
          <Stack gap={4} data-testid="historico-versoes">
            <Text fw={600} c={BRAND.textPrimary}>
              Histórico de versões
            </Text>
            {versions.data.items.map((v) => (
              <Text key={v.version} size="sm" c={BRAND.textMuted}>
                Versão {v.version}
                {v.created_at ? ` · ${new Date(v.created_at).toLocaleDateString('pt-BR')}` : ''} · peso final {fmt(v.final_weight_g)} g ·{' '}
                {fmt(v.energy_kcal_per_100g)} kcal por 100 g
              </Text>
            ))}
          </Stack>
        )}
        {recipe && versions.isError && (
          <Text size="sm" c="red">
            Não foi possível carregar o histórico de versões.
          </Text>
        )}

        {linkPendente && (
          <Alert color="yellow" title="Sair sem salvar?" icon={<IconAlertTriangle size={16} />}>
            Você tem alterações não salvas nesta receita.
            <Group mt="xs">
              <Button color="red" size="xs" onClick={seguirLink}>
                Sair e descartar
              </Button>
              <Button variant="default" size="xs" onClick={() => setLinkPendente(null)}>
                Continuar editando
              </Button>
            </Group>
          </Alert>
        )}

        {confirmLeave && (
          <Alert color="yellow" title="Descartar alterações?" icon={<IconAlertTriangle size={16} />}>
            <Group mt="xs">
              <Button color="red" size="xs" onClick={onCancel}>
                Descartar alterações
              </Button>
              <Button variant="default" size="xs" onClick={() => setConfirmLeave(false)}>
                Continuar editando
              </Button>
            </Group>
          </Alert>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={leave}>
            Cancelar
          </Button>
          <Button onClick={() => run(saveMutation, previewMutation)} loading={saveMutation.isPending}>
            Salvar receita
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
