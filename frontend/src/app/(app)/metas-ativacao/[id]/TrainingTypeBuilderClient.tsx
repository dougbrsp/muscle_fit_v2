'use client';

import {
  ActionIcon,
  Alert,
  Button,
  Group,
  Loader,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconArrowLeft, IconInfoCircle, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import type {
  ApiListResponse,
  MuscleGroupOption,
  TrainingTypeDetail,
  TrainingTypePayload,
} from '@/lib/training-types-types';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

// Estado local por linha de meta — mantém apenas os grupos musculares já
// adicionados; min/target/max preenchidos sob demanda. Regra do backend:
// linha com min=target=max=0 não é persistida (ver `_validate_training_type_payload`).
type TargetRowState = {
  muscle_group_id: number;
  muscle_group_name: string;
  min_weekly_sets: number;
  target_weekly_sets: number;
  max_weekly_sets: number;
};

export function TrainingTypeBuilderClient() {
  const params = useParams<{ id: string }>();
  // Em `output: 'export'` o `useParams()` devolve o slug do build
  // ("placeholder"), não o id da barra de endereços — ver
  // `useDynamicRouteParam`. `routeId` é `null` só no primeiro render.
  const routeId = useDynamicRouteParam(params.id);
  const router = useRouter();
  const qc = useQueryClient();
  const isNew = routeId === 'novo';
  const numericId = routeId && !isNew ? Number(routeId) : null;

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);
  // `rag_folder` saiu da interface no PR-15 (D16), mas a coluna (migração 015)
  // e o uso no backend continuam: `workout_plan_templates_engine` ainda soma o
  // conteúdo dessa pasta ao RAG da geração. O valor salvo é só carregado e
  // devolvido intacto no PUT — sem isso, salvar pela tela apagaria a
  // configuração existente no banco.
  const [ragFolder, setRagFolder] = useState<string | null>(null);
  const [targets, setTargets] = useState<TargetRowState[]>([]);
  // Começa sempre `false`: no primeiro render `routeId` ainda é `null`, então
  // `isNew` não pode ser usado como valor inicial — quem liga o flag no caso
  // "novo" é o efeito de hidratação abaixo.
  const [initialized, setInitialized] = useState(false);
  const [addGroupValue, setAddGroupValue] = useState<string | null>(null);

  const trainingTypeQuery = useQuery({
    queryKey: ['training-type', numericId],
    queryFn: () => apiFetch<TrainingTypeDetail>(`/training-types/${numericId}`),
    enabled: !isNew && !!numericId,
    retry: false,
  });

  const muscleGroupsQuery = useQuery({
    queryKey: ['muscle-groups-catalog'],
    queryFn: () => apiFetch<ApiListResponse<MuscleGroupOption>>('/muscle-groups'),
  });

  // Hidrata o estado do builder a partir do detalhe salvo (só uma vez).
  useEffect(() => {
    if (initialized) return;
    if (isNew) {
      setInitialized(true);
      return;
    }
    if (!trainingTypeQuery.data) return;

    const detail = trainingTypeQuery.data;
    setName(detail.name);
    setDescription(detail.description || '');
    setIsActive(detail.is_active);
    setRagFolder(detail.rag_folder || null);
    setTargets(
      detail.targets.map((target) => ({
        muscle_group_id: target.muscle_group_id,
        muscle_group_name: target.muscle_group_name,
        min_weekly_sets: target.min_weekly_sets,
        target_weekly_sets: target.target_weekly_sets,
        max_weekly_sets: target.max_weekly_sets,
      })),
    );
    setInitialized(true);
  }, [isNew, initialized, trainingTypeQuery.data]);

  const muscleGroupOptions = muscleGroupsQuery.data?.items || [];
  const addGroupOptions = muscleGroupOptions
    .filter((group) => !targets.some((t) => t.muscle_group_id === group.id))
    .map((group) => ({ value: String(group.id), label: group.name }));

  function addTargetRow(groupId: number) {
    const group = muscleGroupOptions.find((g) => g.id === groupId);
    if (!group) return;
    setTargets((prev) => [
      ...prev,
      {
        muscle_group_id: group.id,
        muscle_group_name: group.name,
        min_weekly_sets: 0,
        target_weekly_sets: 0,
        max_weekly_sets: 0,
      },
    ]);
  }

  function updateTargetRow(groupId: number, patch: Partial<TargetRowState>) {
    setTargets((prev) => prev.map((row) => (row.muscle_group_id === groupId ? { ...row, ...patch } : row)));
  }

  function removeTargetRow(groupId: number) {
    setTargets((prev) => prev.filter((row) => row.muscle_group_id !== groupId));
  }

  function validateTargets(): string | null {
    for (const row of targets) {
      if (row.min_weekly_sets === 0 && row.target_weekly_sets === 0 && row.max_weekly_sets === 0) {
        continue; // linha vazia — backend descarta, não precisa validar
      }
      if (
        !(
          0 <= row.min_weekly_sets &&
          row.min_weekly_sets <= row.target_weekly_sets &&
          row.target_weekly_sets <= row.max_weekly_sets
        )
      ) {
        return `${row.muscle_group_name}: exige 0 <= mínimo <= alvo <= máximo (mínimo=${row.min_weekly_sets}, alvo=${row.target_weekly_sets}, máximo=${row.max_weekly_sets}).`;
      }
    }
    return null;
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload: TrainingTypePayload = {
        name,
        description: description || null,
        is_active: isActive,
        rag_folder: ragFolder || null,
        targets: targets.map((row) => ({
          muscle_group_id: row.muscle_group_id,
          min_weekly_sets: row.min_weekly_sets,
          target_weekly_sets: row.target_weekly_sets,
          max_weekly_sets: row.max_weekly_sets,
        })),
      };
      const path = isNew ? '/training-types' : `/training-types/${numericId}`;
      const method = isNew ? 'POST' : 'PUT';
      return apiFetch<TrainingTypeDetail>(path, { method, body: JSON.stringify(payload) });
    },
    onSuccess: async (data) => {
      await qc.invalidateQueries({ queryKey: ['training-types'] });
      notifications.show({ color: 'green', message: 'Meta de ativação salva com sucesso.' });
      if (isNew) {
        router.replace(`/metas-ativacao/${data.id}`);
      } else {
        await qc.invalidateQueries({ queryKey: ['training-type', numericId] });
      }
    },
    onError: (error: unknown) => {
      notifications.show({
        color: 'red',
        title: 'Erro ao salvar',
        message: errorMessage(error, 'Erro ao salvar meta de ativação.'),
      });
    },
  });

  function handleSave() {
    if (!name.trim()) {
      notifications.show({ color: 'red', message: 'Informe um nome para a meta de ativação.' });
      return;
    }
    const validationError = validateTargets();
    if (validationError) {
      notifications.show({ color: 'red', title: 'Metas inválidas', message: validationError });
      return;
    }
    saveMutation.mutate();
  }

  const forbidden = trainingTypeQuery.error instanceof ApiError && trainingTypeQuery.error.status === 403;

  if (!isNew && trainingTypeQuery.isError) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Meta de Ativação
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {forbidden
            ? 'Acesso restrito a administradores e personal trainers.'
            : errorMessage(
                trainingTypeQuery.error,
                'Não foi possível carregar esta meta de ativação (talvez não exista mais).',
              )}
        </Alert>
        <Button
          variant="light"
          color="brand"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => router.push('/metas-ativacao')}
        >
          Voltar para metas de ativação
        </Button>
      </Stack>
    );
  }

  if (!initialized) {
    return (
      <Group justify="center" py="xl">
        <Loader color="brand" />
      </Group>
    );
  }

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Group gap="sm">
          <ActionIcon
            variant="light"
            color="brand"
            onClick={() => router.push('/metas-ativacao')}
            aria-label="Voltar"
          >
            <IconArrowLeft size={18} />
          </ActionIcon>
          <div>
            <Title order={1} c={BRAND.textPrimary}>
              {isNew ? 'Nova meta de ativação' : `Editando: ${name}`}
            </Title>
            <Text c={BRAND.textMuted} size="md">
              Defina metas de ativação semanais (mínimo/alvo/máximo de séries) por grupo muscular. Só grupos com pelo menos um
              valor maior que zero são salvos.
            </Text>
          </div>
        </Group>
        <Button color="brand" onClick={handleSave} loading={saveMutation.isPending}>
          Salvar meta de ativação
        </Button>
      </Group>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group grow align="flex-start">
          <TextInput label="Nome" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <Switch
            color="brand"
            label={isActive ? 'Ativo' : 'Inativo'}
            checked={isActive}
            onChange={(e) => setIsActive(e.currentTarget.checked)}
            mt={28}
          />
        </Group>
        <Textarea
          label="Descrição"
          mt="sm"
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          autosize
          minRows={1}
        />
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="sm" align="flex-end" wrap="wrap">
          <Title order={3} c={BRAND.textPrimary}>
            Metas de ativação semanais por grupo muscular
          </Title>
          <Select
            placeholder="Adicionar grupo muscular..."
            searchable
            data={addGroupOptions}
            value={addGroupValue}
            onChange={(value) => {
              if (value) addTargetRow(Number(value));
              setAddGroupValue(null);
            }}
            w={260}
          />
        </Group>

        {muscleGroupsQuery.isLoading && <Loader size="sm" color="brand" />}
        {muscleGroupsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="sm">
            {errorMessage(muscleGroupsQuery.error, 'Não foi possível carregar os grupos musculares.')}
          </Alert>
        )}

        <Table.ScrollContainer minWidth={560}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Grupo muscular</Table.Th>
                <Table.Th>Mín. semanal</Table.Th>
                <Table.Th>Alvo semanal</Table.Th>
                <Table.Th>Máx. semanal</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {targets.map((row) => (
                <Table.Tr key={row.muscle_group_id}>
                  <Table.Td c={BRAND.textPrimary}>{row.muscle_group_name}</Table.Td>
                  <Table.Td>
                    <NumberInput
                      size="xs"
                      value={row.min_weekly_sets}
                      onChange={(value) => updateTargetRow(row.muscle_group_id, { min_weekly_sets: Number(value) || 0 })}
                      min={0}
                      w={90}
                    />
                  </Table.Td>
                  <Table.Td>
                    <NumberInput
                      size="xs"
                      value={row.target_weekly_sets}
                      onChange={(value) =>
                        updateTargetRow(row.muscle_group_id, { target_weekly_sets: Number(value) || 0 })
                      }
                      min={0}
                      w={90}
                    />
                  </Table.Td>
                  <Table.Td>
                    <NumberInput
                      size="xs"
                      value={row.max_weekly_sets}
                      onChange={(value) => updateTargetRow(row.muscle_group_id, { max_weekly_sets: Number(value) || 0 })}
                      min={0}
                      w={90}
                    />
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon
                      size="xs"
                      color="red"
                      variant="subtle"
                      onClick={() => removeTargetRow(row.muscle_group_id)}
                      aria-label="Remover meta"
                    >
                      <IconTrash size={12} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
              {targets.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={5}>
                    <Text c={BRAND.textMuted} ta="center" py="md">
                      Nenhuma meta configurada ainda. Use o seletor acima para adicionar um grupo muscular.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <Tooltip label="Grupos com mínimo, alvo e máximo iguais a 0 não são salvos" position="top-start">
          <Text size="xs" c={BRAND.textMuted} mt="xs">
            <IconInfoCircle size={12} style={{ verticalAlign: 'middle' }} /> Dica: adicione só os grupos que esta
            meta de ativação realmente prioriza.
          </Text>
        </Tooltip>
      </Paper>
    </Stack>
  );
}
