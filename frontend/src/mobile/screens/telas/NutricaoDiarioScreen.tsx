'use client';

import { Alert, Button, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconBolt, IconCamera, IconPlus, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { CheckinCard } from '@/components/nutrition/CheckinCard';
import { DayHeader } from '@/components/nutrition/DayHeader';
import { DayNotesInput } from '@/components/nutrition/DayNotesInput';
import { MealEditor } from '@/components/nutrition/MealEditor';
import { MealParseModal } from '@/components/nutrition/MealParseModal';
import { QuickEntryForm } from '@/components/nutrition/QuickEntryForm';
import { quantidade, TotalsBadges, TotalsLine } from '@/components/nutrition/MealCard';
import { TargetVsIntakeCard } from '@/components/nutrition/TargetVsIntakeCard';
import { TrainingKcalRow } from '@/components/nutrition/TrainingKcalRow';
import { WeekStrip } from '@/components/nutrition/WeekStrip';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { formatWithUnit } from '@/lib/nutrition-format';
import { MEAL_SLOT_OPTIONS, ITEM_NUTRIENT_FIELDS, nutritionLabel } from '@/lib/nutrition-constants';
import { mealHeadline, mealMacrosLine } from '@/lib/nutrition-meal-list';
import { dashboardKeys } from '@/lib/nutrition-dashboard';
import { deleteMeal, diaryKeys, fetchNutritionDay, fetchNutritionWeek, nutritionErrorInfo } from '@/lib/nutrition-api';
import type { MealEditorForm } from '@/lib/nutrition-meal-form';
import type { Meal } from '@/lib/nutrition-types';
import { BottomSheet, ListBlock, ListGroup, ListRow, LoadingState, ScreenHeader, StickyAction } from '../../ui';
import { SheetButton, useBackToClose } from './common';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';
import { NutricaoNav } from './NutricaoNav';

type Editing = { mode: 'new' } | { mode: 'edit'; meal: Meal } | { mode: 'parsed'; form: MealEditorForm } | null;

/**
 * Diário de nutrição (celular). SEMPRE do usuário logado, mesmo para admin e
 * personal trainer (escolher aluno só existe na montagem do plano). Mesmas
 * queries/chaves do desktop; meta, totais, balanço e semana vêm prontos da API.
 */
function NutricaoDiarioContent() {
  const qc = useQueryClient();
  const [date, setDate] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [lendo, setLendo] = useState(false);
  const [lancando, setLancando] = useState(false);
  const [modoLeitura, setModoLeitura] = useState<'text' | 'photo'>('text');
  const [selectedMeal, setSelectedMeal] = useState<Meal | null>(null);

  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: () => apiFetch<SessionUser>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });
  const weekQuery = useQuery({
    queryKey: diaryKeys.week(anchor),
    queryFn: () => fetchNutritionWeek(anchor),
    enabled: currentUserQuery.isSuccess,
    retry: false,
  });
  useEffect(() => {
    if (date === null && weekQuery.data) setDate(weekQuery.data.today);
  }, [date, weekQuery.data]);

  const dayQuery = useQuery({
    queryKey: diaryKeys.day(date ?? ''),
    queryFn: () => fetchNutritionDay(date as string),
    enabled: date !== null && currentUserQuery.isSuccess,
    retry: false,
  });

  const invalidarDiario = () =>
    Promise.all([qc.invalidateQueries({ queryKey: diaryKeys.all }), qc.invalidateQueries({ queryKey: dashboardKeys.all })]);

  const remover = useMutation({
    mutationFn: (meal: Meal) => deleteMeal(meal.id),
    onSuccess: async () => {
      setSelectedMeal(null);
      await invalidarDiario();
    },
  });

  useBackToClose(editing !== null, () => setEditing(null));
  // Folha do lançamento rápido: o voltar fecha só a folha, não sai do diário.
  useBackToClose(lancando, () => setLancando(false));

  function abrirLeitura(modo: 'text' | 'photo') {
    setModoLeitura(modo);
    setLendo(true);
  }
  function trocarDia(novo: string) {
    setDate(novo);
    setAnchor(novo);
    setEditing(null);
  }

  const dia = dayQuery.data;
  const erroDia = dayQuery.isError ? nutritionErrorInfo(dayQuery.error, 'Não foi possível carregar o diário.') : null;
  const erroSemana = weekQuery.isError ? nutritionErrorInfo(weekQuery.error, 'Não foi possível carregar a semana.') : null;

  // Editor de refeição ocupa a tela (rascunho no estado do componente: teclado/rotação não perdem).
  if (editing && date !== null) {
    return (
      <>
        <ScreenHeader title={editing.mode === 'edit' ? 'Editar refeição' : 'Nova refeição'} subtitle={date} />
        <MealEditor
          key={
            editing.mode === 'new'
              ? `nova-${date}`
              : editing.mode === 'parsed'
                ? `ia-${editing.form.ai?.job_id ?? date}`
                : `editar-${editing.meal.id}`
          }
          date={date}
          today={weekQuery.data?.today ?? dia?.today ?? null}
          meal={editing.mode === 'edit' ? editing.meal : null}
          initialForm={editing.mode === 'parsed' ? editing.form : null}
          onSaved={async () => {
            setEditing(null);
            await invalidarDiario();
          }}
          onCancel={() => setEditing(null)}
        />
      </>
    );
  }

  return (
    <>
      <ScreenHeader title="Diário" subtitle="Refeições do dia; meta e totais vêm do motor de nutrição" />

      {currentUserQuery.isLoading ? <LoadingState label="Carregando..." /> : null}
      {currentUserQuery.isError ? (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
          {nutritionErrorInfo(currentUserQuery.error, 'Não foi possível carregar o usuário.').message}
        </Alert>
      ) : null}
      {currentUserQuery.isSuccess ? (
        <Stack gap="md">
          <CheckinCard />
          <DayHeader date={date} today={weekQuery.data?.today ?? dia?.today ?? null} onDateChange={trocarDia} />
          {erroSemana ? (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
              {erroSemana.message}
            </Alert>
          ) : null}
          <WeekStrip
            week={weekQuery.data}
            selected={date}
            onSelect={(novo) => {
              setDate(novo);
              setEditing(null);
            }}
            onWeekChange={(novo) => setAnchor(novo)}
          />
          {erroDia ? (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
              {erroDia.message}
              <Button variant="subtle" size="xs" ml="xs" mih={44} onClick={() => void dayQuery.refetch()}>
                Tentar de novo
              </Button>
            </Alert>
          ) : null}
          {date !== null && dayQuery.isLoading ? <LoadingState label="Carregando o dia..." /> : null}
          {dia && date !== null ? (
            <>
              <TargetVsIntakeCard day={dia} />
              <TrainingKcalRow training={dia.training} />
              <DayNotesInput key={date} date={date} dayLog={dia.day_log} onSaved={invalidarDiario} />

              <ListGroup title="Refeições" footer={dia.meals.length === 0 ? 'Nenhuma refeição registrada neste dia.' : undefined}>
                {dia.totals ? (
                  <ListBlock>
                    <TotalsBadges totals={dia.totals} items={dia.meals.flatMap((meal) => meal.items)} />
                    <TotalsLine totals={dia.totals} />
                  </ListBlock>
                ) : null}
                {dia.meals.map((meal) => (
                  <ListRow
                    key={meal.id}
                    title={mealHeadline(meal)}
                    subtitle={mealMacrosLine(meal.totals)}
                    onClick={() => setSelectedMeal(meal)}
                  />
                ))}
              </ListGroup>
              {remover.isError ? (
                <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
                  {nutritionErrorInfo(remover.error, 'Não foi possível excluir a refeição.').message}
                </Alert>
              ) : null}

              <ListGroup title="Registrar de outro jeito">
                <ListRow icon={IconBolt} title="Lançamento rápido" subtitle="Nome, peso e macros" onClick={() => setLancando(true)} />
                <ListRow icon={IconCamera} title="Foto do prato" onClick={() => abrirLeitura('photo')} />
                <ListRow icon={IconSparkles} title="Descrever com IA" onClick={() => abrirLeitura('text')} />
              </ListGroup>
            </>
          ) : null}
          <NutricaoNav active="/nutricao" />
        </Stack>
      ) : null}

      {date !== null ? (
        <MealParseModal
          opened={lendo}
          mode={modoLeitura}
          onClose={() => setLendo(false)}
          date={date}
          onParsed={(form) => {
            setLendo(false);
            setEditing((atual) => atual ?? { mode: 'parsed', form });
          }}
        />
      ) : null}

      <BottomSheet opened={lancando && date !== null} onClose={() => setLancando(false)} title="Lançamento rápido">
        {date !== null ? (
          <QuickEntryForm
            date={date}
            today={weekQuery.data?.today ?? dia?.today ?? null}
            touch
            onSaved={async () => {
              setLancando(false);
              await invalidarDiario();
            }}
            onCancel={() => setLancando(false)}
          />
        ) : null}
      </BottomSheet>

      {dia && date !== null ? (
        <StickyAction>
          <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={20} />} onClick={() => setEditing({ mode: 'new' })}>
            Adicionar refeição
          </Button>
        </StickyAction>
      ) : null}

      <BottomSheet
        opened={selectedMeal !== null}
        onClose={() => setSelectedMeal(null)}
        title={selectedMeal ? (nutritionLabel(MEAL_SLOT_OPTIONS, selectedMeal.meal_slot) ?? 'Refeição') : 'Refeição'}
        footer={
          selectedMeal ? (
            <>
              <SheetButton variant="default" color="red" loading={remover.isPending} onClick={() => remover.mutate(selectedMeal)}>
                Excluir
              </SheetButton>
              <SheetButton
                color="brand"
                onClick={() => {
                  setEditing({ mode: 'edit', meal: selectedMeal });
                  setSelectedMeal(null);
                }}
              >
                Editar
              </SheetButton>
            </>
          ) : undefined
        }
      >
        {selectedMeal ? (
          <Stack gap="sm">
            {remover.isError ? (
              <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
                {nutritionErrorInfo(remover.error, 'Não foi possível excluir a refeição.').message}
              </Alert>
            ) : null}
            <ListGroup title="Itens">
              {selectedMeal.items.map((item) => (
                <ListRow
                  key={item.id ?? `novo-${item.position}`}
                  title={item.label}
                  subtitle={[
                    quantidade(item),
                    ITEM_NUTRIENT_FIELDS.slice(1)
                      .map((campo) => `${campo.label} ${formatWithUnit(item.nutrients[campo.code], campo.unit, 1)}`)
                      .join(' · '),
                    item.limitations.map((l) => l.label).join(' '),
                  ]
                    .filter(Boolean)
                    .join(' | ')}
                  value={formatWithUnit(item.nutrients[ITEM_NUTRIENT_FIELDS[0].code], ITEM_NUTRIENT_FIELDS[0].unit, 0)}
                />
              ))}
            </ListGroup>
            <TotalsBadges totals={selectedMeal.totals} items={selectedMeal.items} />
            {!selectedMeal.totals.complete ? (
              <Text size="sm" c="dimmed">
                {selectedMeal.totals.limitations.map((item) => item.label).join(' ')}
              </Text>
            ) : null}
            <TotalsLine totals={selectedMeal.totals} />
            {selectedMeal.notes ? (
              <Text size="sm" c="dimmed">
                {selectedMeal.notes}
              </Text>
            ) : null}
          </Stack>
        ) : null}
      </BottomSheet>
    </>
  );
}

export function NutricaoDiarioScreen() {
  return (
    <MobileNutritionPresentation>
      <NutricaoDiarioContent />
    </MobileNutritionPresentation>
  );
}
