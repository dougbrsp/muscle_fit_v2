'use client';

import { Alert, Button, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconPlus, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { DayHeader } from '@/components/nutrition/DayHeader';
import { MealEditor } from '@/components/nutrition/MealEditor';
import { MealList } from '@/components/nutrition/MealList';
import { MealParseModal } from '@/components/nutrition/MealParseModal';
import { TotalsBadges, TotalsLine } from '@/components/nutrition/MealCard';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { TargetVsIntakeCard } from '@/components/nutrition/TargetVsIntakeCard';
import { TrainingKcalRow } from '@/components/nutrition/TrainingKcalRow';
import { WaterHungerInputs } from '@/components/nutrition/WaterHungerInputs';
import { WeekStrip } from '@/components/nutrition/WeekStrip';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { deleteMeal, diaryKeys, fetchNutritionDay, fetchNutritionWeek, nutritionErrorInfo } from '@/lib/nutrition-api';
import type { MealEditorForm } from '@/lib/nutrition-meal-form';
import type { Meal } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

type Editing = { mode: 'new' } | { mode: 'edit'; meal: Meal } | { mode: 'parsed'; form: MealEditorForm } | null;

/**
 * `/nutricao` — diário de refeições (Lote L4). O aluno registra as próprias
 * refeições; admin e personal trainer escolhem o aluno (sem vínculo formal,
 * D-L). Meta do dia, totais, balanço, semana e kcal do treino vêm prontos da
 * API (motor `domain/nutrition`); o preview da refeição é uma chamada (E9). A
 * refeição também pode ser descrita em texto para a IA ler (L5b, `MealParseModal`):
 * o editor abre com os itens propostos para a pessoa confirmar. A tela só exibe.
 */
export function NutricaoClient() {
  const qc = useQueryClient();
  const [userId, setUserId] = useState<number | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [lendo, setLendo] = useState(false);

  const currentUserQuery = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });
  const role = currentUserQuery.data?.role;
  const canManage = role === 'admin' || role === 'personal_trainer';

  const weekQuery = useQuery({
    queryKey: diaryKeys.week(userId, anchor),
    queryFn: () => fetchNutritionWeek(anchor, userId),
    enabled: currentUserQuery.isSuccess,
    retry: false,
  });
  // Primeira carga: o dia aberto é o "hoje" do ALUNO, como a API o calcula (fuso do perfil).
  useEffect(() => {
    if (date === null && weekQuery.data) setDate(weekQuery.data.today);
  }, [date, weekQuery.data]);

  const dayQuery = useQuery({
    queryKey: diaryKeys.day(userId, date ?? ''),
    queryFn: () => fetchNutritionDay(date as string, userId),
    enabled: date !== null && currentUserQuery.isSuccess,
    retry: false,
  });

  const remover = useMutation({
    mutationFn: (meal: Meal) => deleteMeal(meal.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: diaryKeys.all }),
  });

  function trocarAluno(novo: number | null) {
    setUserId(novo);
    setEditing(null);
  }

  function trocarDia(novo: string) {
    setDate(novo);
    setAnchor(novo);
    setEditing(null);
  }

  async function atualizar() {
    await qc.invalidateQueries({ queryKey: diaryKeys.all });
  }

  const dia = dayQuery.data;
  const erroDia = dayQuery.isError ? nutritionErrorInfo(dayQuery.error, 'Não foi possível carregar o diário.') : null;
  const erroSemana = weekQuery.isError ? nutritionErrorInfo(weekQuery.error, 'Não foi possível carregar a semana.') : null;

  return (
    <Stack gap="xl">
      <Stack gap="xs">
        <NutritionSubnav active="/nutricao" />
        <Title order={1} c={BRAND.textPrimary}>
          Diário
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Registre as refeições com alimentos do catálogo ou descreva a refeição para a IA ler. A meta do dia, os
          totais e a semana são calculados pelo motor de nutrição.
        </Text>
      </Stack>

      {currentUserQuery.isLoading && <Text c={BRAND.textMuted}>Carregando...</Text>}
      {currentUserQuery.isSuccess && (
        <>
          <DayHeader
            canManage={canManage}
            userId={userId}
            onUserChange={trocarAluno}
            studentName={canManage && userId !== null ? (dia?.user.full_name || dia?.user.username || null) : null}
            date={date}
            today={weekQuery.data?.today ?? dia?.today ?? null}
            onDateChange={trocarDia}
          />
          {erroSemana && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {erroSemana.message}
            </Alert>
          )}
          <WeekStrip
            week={weekQuery.data}
            selected={date}
            onSelect={(novo) => {
              setDate(novo);
              setEditing(null);
            }}
            onWeekChange={(novo) => setAnchor(novo)}
          />
          {erroDia && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {erroDia.message}
            </Alert>
          )}
          {date !== null && dayQuery.isLoading && <Text c={BRAND.textMuted}>Carregando o dia...</Text>}
          {dia && date !== null && (
            <Stack gap="lg">
              <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
                <TargetVsIntakeCard day={dia} />
                <Stack gap="md">
                  <TrainingKcalRow training={dia.training} />
                  <WaterHungerInputs key={`${userId ?? 'eu'}-${date}`} date={date} userId={userId} dayLog={dia.day_log} onSaved={atualizar} />
                </Stack>
              </SimpleGrid>

              <Stack gap="sm">
                <Group justify="space-between" wrap="wrap">
                  <Group gap="xs">
                    <Title order={3} c={BRAND.textPrimary}>
                      Refeições
                    </Title>
                    {dia.totals && <TotalsBadges totals={dia.totals} />}
                  </Group>
                  {editing === null && (
                    <Group gap="xs">
                      <Button variant="light" color="brand" leftSection={<IconSparkles size={16} />} onClick={() => setLendo(true)}>
                        Descrever com IA
                      </Button>
                      <Button color="brand" leftSection={<IconPlus size={16} />} onClick={() => setEditing({ mode: 'new' })}>
                        Adicionar refeição
                      </Button>
                    </Group>
                  )}
                </Group>
                {dia.totals && <TotalsLine totals={dia.totals} />}
                <MealParseModal
                  opened={lendo}
                  onClose={() => setLendo(false)}
                  userId={userId}
                  date={date}
                  onParsed={(form) => {
                    setLendo(false);
                    setEditing({ mode: 'parsed', form });
                  }}
                />
                {editing && (
                  <MealEditor
                    key={
                      editing.mode === 'new'
                        ? `nova-${date}`
                        : editing.mode === 'parsed'
                          ? `ia-${editing.form.ai?.job_id ?? date}`
                          : `editar-${editing.meal.id}`
                    }
                    userId={userId}
                    date={date}
                    meal={editing.mode === 'edit' ? editing.meal : null}
                    initialForm={editing.mode === 'parsed' ? editing.form : null}
                    onSaved={async () => {
                      setEditing(null);
                      await atualizar();
                    }}
                    onCancel={() => setEditing(null)}
                  />
                )}
                {remover.isError && (
                  <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                    {nutritionErrorInfo(remover.error, 'Não foi possível excluir a refeição.').message}
                  </Alert>
                )}
                <MealList
                  meals={dia.meals}
                  onEdit={(meal) => setEditing({ mode: 'edit', meal })}
                  onDelete={(meal) => remover.mutate(meal)}
                  deletingId={remover.isPending ? (remover.variables?.id ?? null) : null}
                />
              </Stack>
            </Stack>
          )}
        </>
      )}
    </Stack>
  );
}
