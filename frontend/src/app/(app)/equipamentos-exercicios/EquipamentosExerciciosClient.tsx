'use client';

import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  CloseButton,
  Group,
  Loader,
  Modal,
  Paper,
  Popover,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDebouncedValue, useDisclosure, useMediaQuery } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconCheck,
  IconDeviceFloppy,
  IconLink,
  IconPencil,
  IconPlus,
  IconSearch,
  IconTags,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import { PhotoLightbox, usePhotoLightbox, type LightboxPhoto } from '@/app/(fullscreen)/modo-treino/PhotoLightbox';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { apiFetch, ApiError } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import type {
  ApiListResponse as CatalogAdminApiListResponse,
  EquipmentType,
  ExerciseAlias,
  ExerciseAliasCreatePayload,
  ExerciseAliasCreateResponse,
  ExerciseAliasDeleteResponse,
  MuscleGroupOption,
} from '@/lib/exercise-catalog-admin-types';
import type {
  ApiListResponse,
  EquipmentCreateOrUpdateResponse,
  EquipmentDeleteResponse,
  EquipmentPhotoDeleteResponse,
  MuscleGroupEquipmentResponse,
} from '@/lib/equipment-exercise-mappings-types';
import {
  LOAD_CONVENTION_EMPTY_LABEL,
  LOAD_CONVENTION_OPTIONS,
  toLoadConventionPayload,
} from '@/lib/equipment-exercises-types';
import type {
  EquipmentExerciseCreatePayload,
  EquipmentExerciseCreateResponse,
  EquipmentExerciseDeleteResponse,
  EquipmentExerciseLink,
  EquipmentExerciseUpdatePayload,
  EquipmentExerciseUpdateResponse,
} from '@/lib/equipment-exercises-types';
import type { Equipment } from '@/lib/gyms-types';
import type { MappingCanonicalOptionsResponse } from '@/lib/mappings-types';
import type { ExerciseMappingCatalogListResponse } from '@/lib/muscle-groups-types';
import { BRAND } from '@/theme';
import { EquipmentSafetyTab } from './EquipmentSafetyTab';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * Miniatura de equipamento que abre a foto ampliada no `PhotoLightbox` já
 * usado pelo modo treino. `photoUrl` é o arquivo estático servido via
 * CloudFront + S3 (campo `photo_url` da API, ver `api/app/gyms.py`) — sem
 * foto resolvida, cai no placeholder de `EquipmentPhoto`.
 */
function EquipmentPhotoThumb({
  photoUrl,
  alt,
  size = 40,
  onOpen,
}: {
  photoUrl?: string | null;
  alt: string;
  size?: number;
  onOpen: (photo: LightboxPhoto) => void;
}) {
  if (!photoUrl) {
    return <EquipmentPhoto photoUrl={photoUrl} alt={alt} size={size} />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={photoUrl}
      alt={alt}
      width={size}
      height={size}
      style={{ objectFit: 'cover', borderRadius: 8, cursor: 'zoom-in' }}
      onClick={(event) => {
        event.stopPropagation();
        onOpen({ src: photoUrl, alt });
      }}
    />
  );
}

/**
 * Apelidos (`exercise_aliases`) do exercicio canonico de uma associacao,
 * editaveis sem sair da tela - o mesmo dado da secao "Apelidos" de
 * /dominio-exercicios, que segue sendo o lugar de gestao completa do
 * catalogo (o popover traz um atalho para la).
 *
 * Divergencia de contrato confirmada em `api/app/exercise_catalog_admin.py`:
 * leitura e criacao sao `GET/POST /exercises/{exercise_id}/aliases`, mas a
 * remocao e `DELETE /exercises/aliases/{alias_id}` - e NAO
 * `/exercises/{id}/aliases/{alias_id}`. Leitura livre a qualquer usuario
 * autenticado; escrita exige admin.
 *
 * `mapping_id` da associacao e `exercise_id` do catalogo canonico sao o mesmo
 * id (os catalogos foram fundidos na migracao - ver o cabecalho de
 * `exercise_catalog_admin.py`).
 */
function ExerciseAliasesPopover({
  exerciseId,
  exerciseName,
  isAdmin,
}: {
  exerciseId: number;
  exerciseName: string;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [newAlias, setNewAlias] = useState('');

  const aliasesQuery = useQuery({
    queryKey: ['exercise-aliases', exerciseId],
    queryFn: () => apiFetch<CatalogAdminApiListResponse<ExerciseAlias>>(`/exercises/${exerciseId}/aliases`),
    enabled: opened,
  });

  const addMutation = useMutation({
    mutationFn: (alias: string) =>
      apiFetch<ExerciseAliasCreateResponse>(`/exercises/${exerciseId}/aliases`, {
        method: 'POST',
        body: JSON.stringify({ alias, source: 'manual' } satisfies ExerciseAliasCreatePayload),
      }),
    onSuccess: async () => {
      setNewAlias('');
      await qc.invalidateQueries({ queryKey: ['exercise-aliases', exerciseId] });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (aliasId: number) =>
      apiFetch<ExerciseAliasDeleteResponse>(`/exercises/aliases/${aliasId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['exercise-aliases', exerciseId] });
    },
  });

  const aliases = aliasesQuery.data?.items || [];

  function submitAlias() {
    const trimmed = newAlias.trim();
    if (!trimmed || addMutation.isPending) return;
    addMutation.mutate(trimmed);
  }

  return (
    <Popover opened={opened} onChange={setOpened} width={300} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Tooltip label="Apelidos deste exercício">
          <ActionIcon variant="subtle" color="brand" onClick={() => setOpened((current) => !current)}>
            <IconTags size={16} />
          </ActionIcon>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text size="sm" fw={600} c={BRAND.textPrimary}>
            Apelidos — {exerciseName}
          </Text>
          {aliasesQuery.isLoading && <Loader size="xs" color="brand" />}
          {aliasesQuery.isError && (
            <Text size="xs" c="red">
              {errorMessage(aliasesQuery.error, 'Não foi possível carregar os apelidos.')}
            </Text>
          )}
          <Group gap={4} wrap="wrap">
            {aliases.map((alias) => (
              <Badge
                key={alias.id}
                variant="light"
                color="gray"
                rightSection={
                  isAdmin ? (
                    <CloseButton
                      size="xs"
                      aria-label={`Remover apelido ${alias.alias}`}
                      onClick={() => removeMutation.mutate(alias.id)}
                    />
                  ) : undefined
                }
              >
                {alias.alias}
              </Badge>
            ))}
            {!aliasesQuery.isLoading && aliases.length === 0 && (
              <Text size="xs" c={BRAND.textMuted}>
                Nenhum apelido cadastrado.
              </Text>
            )}
          </Group>
          {isAdmin && (
            <Group gap={4} wrap="nowrap">
              <TextInput
                size="xs"
                placeholder="Novo apelido..."
                value={newAlias}
                onChange={(event) => setNewAlias(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    submitAlias();
                  }
                }}
                style={{ flex: 1 }}
              />
              <ActionIcon
                size="sm"
                variant="light"
                color="brand"
                aria-label="Adicionar apelido"
                loading={addMutation.isPending}
                disabled={!newAlias.trim()}
                onClick={submitAlias}
              >
                <IconPlus size={14} />
              </ActionIcon>
            </Group>
          )}
          {(addMutation.isError || removeMutation.isError) && (
            <Text size="xs" c="red">
              {errorMessage(
                addMutation.error || removeMutation.error,
                'Não foi possível salvar o apelido (talvez já exista).',
              )}
            </Text>
          )}
          <Anchor href="/dominio-exercicios" size="xs" c={BRAND.orange}>
            Abrir domínio de exercícios
          </Anchor>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

export function EquipamentosExerciciosClient() {
  const qc = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');
  // Lightbox compartilhado por todas as miniaturas desta tela.
  const lightbox = usePhotoLightbox();

  // Mesma query key usada pelo AppShellLayout/outras telas — o React Query
  // dedupa, não gera uma chamada extra a /auth/me.
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  // `_require_admin_identity` no backend exige role EXATAMENTE "admin" —
  // mesmo padrão estrito de /mapeamentos (Etapa 16), diferente do
  // admin_or_pt usado em /academias.
  const isAdmin = currentUserQuery.data?.role === 'admin';

  // ---- Filtros (query params reais de GET /equipment-exercises) ------------
  const [filterEquipmentId, setFilterEquipmentId] = useState<string | null>(null);
  const [filterPrimaryGroup, setFilterPrimaryGroup] = useState<string | null>(null);

  const equipmentQuery = useQuery({
    queryKey: ['equipment-catalog'],
    queryFn: () => apiFetch<ApiListResponse<Equipment>>('/equipment?active_only=true'),
  });
  const equipmentItems = equipmentQuery.data?.items || [];

  // ---- Busca por nome no catálogo (GET /equipment?query=) ----------------
  // Query separada, só para as listagens navegáveis (tabela do catálogo e
  // passo 2 da associação em massa). Os Selects de filtro/associação
  // continuam lendo `equipmentItems` completo — filtrar o catálogo inteiro a
  // partir de uma caixa de busca de outra aba seria confuso. A chave começa
  // com 'equipment-catalog', então as invalidações existentes já a alcançam.
  const [equipmentSearch, setEquipmentSearch] = useState('');
  const [debouncedEquipmentSearch] = useDebouncedValue(equipmentSearch, 300);
  const equipmentSearchTerm = debouncedEquipmentSearch.trim();

  const equipmentSearchQuery = useQuery({
    queryKey: ['equipment-catalog', equipmentSearchTerm],
    queryFn: () =>
      apiFetch<ApiListResponse<Equipment>>(
        `/equipment?active_only=true&query=${encodeURIComponent(equipmentSearchTerm)}`,
      ),
    enabled: Boolean(equipmentSearchTerm),
  });

  const browsableEquipmentItems = equipmentSearchTerm ? equipmentSearchQuery.data?.items || [] : equipmentItems;
  const browsableEquipmentLoading = equipmentSearchTerm ? equipmentSearchQuery.isLoading : equipmentQuery.isLoading;
  const browsableEquipmentError = equipmentSearchTerm ? equipmentSearchQuery.isError : equipmentQuery.isError;

  const mappingsQuery = useQuery({
    queryKey: ['equipment-exercises', filterEquipmentId, filterPrimaryGroup],
    queryFn: () => {
      const params = new URLSearchParams({ active_only: 'true' });
      if (filterEquipmentId) params.set('equipment_id', filterEquipmentId);
      if (filterPrimaryGroup) params.set('primary_group', filterPrimaryGroup);
      return apiFetch<ApiListResponse<EquipmentExerciseLink>>(`/equipment-exercises?${params.toString()}`);
    },
  });

  const links = mappingsQuery.data?.items || [];

  // Opções de grupo muscular primário para o filtro: derivadas das próprias
  // associações já carregadas (sem filtro de grupo) — evita depender de um
  // endpoint extra só para listar grupos.
  const allLinksQuery = useQuery({
    queryKey: ['equipment-exercises', null, null],
    queryFn: () => apiFetch<ApiListResponse<EquipmentExerciseLink>>('/equipment-exercises?active_only=true'),
  });

  const primaryGroupOptions = useMemo(() => {
    const groups = new Set<string>();
    for (const item of allLinksQuery.data?.items || []) {
      if (item.primary_group) groups.add(item.primary_group);
    }
    return Array.from(groups).sort((a, b) => a.localeCompare(b));
  }, [allLinksQuery.data]);

  // ---- Modal: nova associação (POST /equipment-exercises, admin) -----------
  const [createOpen, { open: openCreate, close: closeCreateRaw }] = useDisclosure(false);
  const [createEquipmentId, setCreateEquipmentId] = useState<string | null>(null);
  const [exerciseSearch, setExerciseSearch] = useState('');
  const [createMappingId, setCreateMappingId] = useState<string | null>(null);

  function closeCreate() {
    closeCreateRaw();
    setCreateEquipmentId(null);
    setExerciseSearch('');
    setCreateMappingId(null);
    createMutation.reset();
  }

  // Reaproveita GET /mapping-canonical/options (Etapa 16), já com o formato
  // adequado (id + nome + grupo) e já consumido nesta base — evita inventar
  // um novo endpoint só para popular este select.
  const exerciseOptionsQuery = useQuery({
    queryKey: ['mapping-canonical-options', exerciseSearch],
    queryFn: () => {
      const params = exerciseSearch.trim() ? `?q=${encodeURIComponent(exerciseSearch.trim())}` : '';
      return apiFetch<MappingCanonicalOptionsResponse>(`/mapping-canonical/options${params}`);
    },
    enabled: createOpen,
  });

  const exerciseSelectData = useMemo(
    () =>
      (exerciseOptionsQuery.data?.items || []).map((item) => ({
        value: String(item.id),
        label: [
          item.exercise_name,
          item.amazfit_name ? `(${item.amazfit_name})` : null,
          item.primary_group ? `— ${item.primary_group}` : null,
        ]
          .filter(Boolean)
          .join(' '),
      })),
    [exerciseOptionsQuery.data],
  );

  const createMutation = useMutation({
    mutationFn: () => {
      const payload: EquipmentExerciseCreatePayload = {
        equipment_id: Number(createEquipmentId),
        exercise_id: Number(createMappingId),
      };
      return apiFetch<EquipmentExerciseCreateResponse>('/equipment-exercises', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    },
    onSuccess: async () => {
      closeCreate();
      await qc.invalidateQueries({ queryKey: ['equipment-exercises'] });
    },
  });

  const canSubmitCreate = Boolean(createEquipmentId) && Boolean(createMappingId);

  // ---- Remoção (DELETE /equipment-exercises/{id}, admin) — soft ------------
  // delete no backend (is_active = 0), mas ainda assim exige confirmação
  // clara: reativar depois exige refazer a associação manualmente.
  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<EquipmentExerciseLink | null>(null);

  function openRemoveConfirm(link: EquipmentExerciseLink) {
    setRemoveTarget(link);
    openRemove();
  }

  const removeMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<EquipmentExerciseDeleteResponse>(`/equipment-exercises/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: async () => {
      closeRemove();
      setRemoveTarget(null);
      await qc.invalidateQueries({ queryKey: ['equipment-exercises'] });
    },
  });

  // ---- Convenção de carga por par (PUT /equipment-exercises/{id}) ---------
  // Edição inline na própria linha da tabela: é um atributo só, e abrir um
  // modal para um Select seria atrito sem ganho. O valor exibido vem sempre da
  // query (nenhum estado local espelhando o servidor); enquanto o PUT está no
  // ar, só AQUELA linha fica desabilitada.
  const [savingConventionId, setSavingConventionId] = useState<number | null>(null);

  const loadConventionMutation = useMutation({
    mutationFn: ({ linkId, value }: { linkId: number; value: string | null }) => {
      const payload: EquipmentExerciseUpdatePayload = { load_convention: toLoadConventionPayload(value) };
      return apiFetch<EquipmentExerciseUpdateResponse>(`/equipment-exercises/${linkId}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
    },
    onMutate: ({ linkId }) => setSavingConventionId(linkId),
    onSettled: async () => {
      setSavingConventionId(null);
      await qc.invalidateQueries({ queryKey: ['equipment-exercises'] });
    },
  });

  // ---- Associação em massa por grupo muscular (aba dedicada) -------------
  // Fluxo em 3 passos — grupo muscular -> equipamento -> checklist dos
  // exercícios daquele grupo — com um rascunho local que só vai para a API
  // ao confirmar, um POST/DELETE por item (o backend não tem endpoint de
  // lote), com progresso "N/M salvos". O modal 1-a-1 acima continua
  // disponível para ajustes pontuais.
  const [massGroupId, setMassGroupId] = useState<number | null>(null);
  const [massEquipmentId, setMassEquipmentId] = useState<number | null>(null);
  const [massDraft, setMassDraft] = useState<Set<number>>(new Set());
  const [massProgress, setMassProgress] = useState<{ done: number; total: number } | null>(null);

  const muscleGroupsQuery = useQuery({
    queryKey: ['muscle-groups-catalog'],
    queryFn: () => apiFetch<CatalogAdminApiListResponse<MuscleGroupOption>>('/muscle-groups'),
  });

  const muscleGroups = useMemo(
    () => (muscleGroupsQuery.data?.items || []).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [muscleGroupsQuery.data],
  );

  const massGroup = muscleGroups.find((group) => group.id === massGroupId) || null;

  // Quais equipamentos já atendem o grupo selecionado — usado só para o badge
  // "já associado" no passo 2. O catálogo continua sendo listado INTEIRO: um
  // equipamento sem vínculo nenhum precisa poder ser escolhido, senão não há
  // como começar a mapear um grupo do zero.
  const massGroupEquipmentQuery = useQuery({
    queryKey: ['muscle-group-equipment', massGroupId],
    queryFn: () => apiFetch<MuscleGroupEquipmentResponse>(`/muscle-groups/${massGroupId}/equipment`),
    enabled: Boolean(massGroupId),
  });

  const massGroupEquipmentIds = useMemo(() => {
    const set = new Set<number>();
    for (const item of massGroupEquipmentQuery.data?.items || []) set.add(item.equipment_id);
    return set;
  }, [massGroupEquipmentQuery.data]);

  // Exercícios canônicos do grupo selecionado. Divergência de contrato: o
  // parâmetro é `primary_group` (nome do grupo), não `group` — confirmado em
  // `api/app/exercise_mapping_catalog.py::list_mappings`.
  const massExercisesQuery = useQuery({
    queryKey: ['exercise-mapping-catalog-by-group', massGroup?.name],
    queryFn: () =>
      apiFetch<ExerciseMappingCatalogListResponse>(
        `/exercise-mapping-catalog?active_only=true&limit=500&primary_group=${encodeURIComponent(massGroup?.name || '')}`,
      ),
    enabled: Boolean(massGroup),
  });

  const massExercises = useMemo(
    () =>
      (massExercisesQuery.data?.items || [])
        .slice()
        .sort((a, b) => a.exercise_name.localeCompare(b.exercise_name)),
    [massExercisesQuery.data],
  );

  // Associações ativas do equipamento escolhido, para saber o que já está
  // marcado e qual `link_id` usar no DELETE.
  const massEquipmentLinksQuery = useQuery({
    queryKey: ['equipment-exercises-by-equipment', massEquipmentId],
    queryFn: () =>
      apiFetch<ApiListResponse<EquipmentExerciseLink>>(
        `/equipment-exercises?active_only=true&equipment_id=${massEquipmentId}`,
      ),
    enabled: Boolean(massEquipmentId),
  });

  // exercise_id -> link_id (id de `equipment_exercises`), restrito aos
  // exercícios do grupo em foco (o equipamento pode atender exercícios de
  // outros grupos, que não devem ser tocados por este rascunho).
  const massExistingLinks = useMemo(() => {
    const map = new Map<number, number>();
    const groupExerciseIds = new Set(massExercises.map((item) => item.id));
    for (const link of massEquipmentLinksQuery.data?.items || []) {
      if (groupExerciseIds.has(link.exercise_id)) map.set(link.exercise_id, link.id);
    }
    return map;
  }, [massEquipmentLinksQuery.data, massExercises]);

  // Sempre que trocar equipamento/grupo (ou a lista de vínculos for
  // recarregada), o rascunho volta a refletir o estado real do servidor.
  const massLinksSignature = useMemo(
    () => Array.from(massExistingLinks.keys()).sort((a, b) => a - b).join(','),
    [massExistingLinks],
  );

  useEffect(() => {
    setMassDraft(new Set(massExistingLinks.keys()));
    setMassProgress(null);
  }, [massLinksSignature, massEquipmentId, massGroupId]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleMassDraft(mappingId: number, checked: boolean) {
    setMassDraft((current) => {
      const next = new Set(current);
      if (checked) next.add(mappingId);
      else next.delete(mappingId);
      return next;
    });
  }

  const massToAdd = useMemo(
    () => massExercises.filter((item) => massDraft.has(item.id) && !massExistingLinks.has(item.id)),
    [massExercises, massDraft, massExistingLinks],
  );
  const massToRemove = useMemo(
    () => massExercises.filter((item) => !massDraft.has(item.id) && massExistingLinks.has(item.id)),
    [massExercises, massDraft, massExistingLinks],
  );
  const massPendingCount = massToAdd.length + massToRemove.length;

  const massSaveMutation = useMutation({
    mutationFn: async () => {
      const total = massPendingCount;
      let done = 0;
      setMassProgress({ done, total });

      for (const item of massToAdd) {
        if (massEquipmentId == null) break;
        const payload: EquipmentExerciseCreatePayload = { equipment_id: massEquipmentId, exercise_id: item.id };
        await apiFetch<EquipmentExerciseCreateResponse>('/equipment-exercises', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        done += 1;
        setMassProgress({ done, total });
      }
      for (const item of massToRemove) {
        const linkId = massExistingLinks.get(item.id);
        if (linkId) {
          await apiFetch<EquipmentExerciseDeleteResponse>(`/equipment-exercises/${linkId}`, {
            method: 'DELETE',
          });
        }
        done += 1;
        setMassProgress({ done, total });
      }
    },
    onSettled: async () => {
      // Mesmo em erro parcial, recarrega para o rascunho refletir o que de
      // fato foi gravado — nunca deixar a UI otimista divergir do servidor.
      await qc.invalidateQueries({ queryKey: ['equipment-exercises'] });
      await qc.invalidateQueries({ queryKey: ['equipment-exercises-by-equipment'] });
      await qc.invalidateQueries({ queryKey: ['muscle-group-equipment'] });
    },
  });

  const massEquipment = equipmentItems.find((eq) => eq.id === massEquipmentId) || null;

  // ---- Catálogo de equipamentos (POST/PUT/DELETE /equipment, admin) -------
  // Reaproveita a mesma query key `['equipment-catalog']` já usada pelos
  // selects de filtro/associação acima — invalidar aqui atualiza tudo junto.
  const [equipmentFormOpen, { open: openEquipmentFormRaw, close: closeEquipmentFormRaw }] = useDisclosure(false);
  const [equipmentFormTarget, setEquipmentFormTarget] = useState<Equipment | null>(null);
  const [equipmentFormName, setEquipmentFormName] = useState('');
  const [equipmentFormTypeId, setEquipmentFormTypeId] = useState<string | null>(null);
  const [equipmentFormManufacturer, setEquipmentFormManufacturer] = useState('');
  const [equipmentFormModel, setEquipmentFormModel] = useState('');
  const [equipmentFormLocation, setEquipmentFormLocation] = useState('');
  const equipmentPhotoInputRef = useRef<HTMLInputElement | null>(null);

  // Tipos de equipamento (`equipment_types`) são mantidos na tela
  // /dominio-exercicios (`EquipmentTypesPanel`); aqui só consumimos a lista
  // para o Select do formulário — mesma rota `GET /equipment-types`.
  const equipmentTypesQuery = useQuery({
    queryKey: ['exercise-catalog-admin-equipment-types'],
    queryFn: () => apiFetch<CatalogAdminApiListResponse<EquipmentType>>('/equipment-types'),
  });

  const equipmentTypeNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const type of equipmentTypesQuery.data?.items || []) map.set(type.id, type.name);
    return map;
  }, [equipmentTypesQuery.data]);

  const equipmentTypeSelectData = useMemo(
    () =>
      (equipmentTypesQuery.data?.items || []).map((type) => ({
        value: String(type.id),
        label: type.name,
      })),
    [equipmentTypesQuery.data],
  );

  function resetEquipmentFormFields(equipment: Equipment | null) {
    setEquipmentFormName(equipment?.name || '');
    setEquipmentFormTypeId(equipment?.equipment_type_id ? String(equipment.equipment_type_id) : null);
    setEquipmentFormManufacturer(equipment?.manufacturer || '');
    setEquipmentFormModel(equipment?.model || '');
    setEquipmentFormLocation(equipment?.location || '');
  }

  function openCreateEquipment() {
    setEquipmentFormTarget(null);
    resetEquipmentFormFields(null);
    upsertEquipmentMutation.reset();
    openEquipmentFormRaw();
  }

  function openEditEquipment(equipment: Equipment) {
    setEquipmentFormTarget(equipment);
    resetEquipmentFormFields(equipment);
    upsertEquipmentMutation.reset();
    openEquipmentFormRaw();
  }

  function closeEquipmentForm() {
    closeEquipmentFormRaw();
    setEquipmentFormTarget(null);
    resetEquipmentFormFields(null);
  }

  const upsertEquipmentMutation = useMutation({
    mutationFn: () => {
      // `notes` vai explicitamente no payload porque o `PUT /equipment/{id}`
      // grava `notes = %s` sem COALESCE: omitir o campo APAGA a observação
      // existente. Os demais campos novos (tipo/fabricante/modelo/local) usam
      // `... if "x" in item else current["x"]` no backend, mas reenviamos
      // todos mesmo assim para o formulário ser a fonte de verdade.
      const payload = {
        name: equipmentFormName.trim(),
        notes: equipmentFormTarget?.notes ?? null,
        equipment_type_id: equipmentFormTypeId ? Number(equipmentFormTypeId) : null,
        manufacturer: equipmentFormManufacturer.trim() || null,
        model: equipmentFormModel.trim() || null,
        location: equipmentFormLocation.trim() || null,
      };
      return equipmentFormTarget
        ? apiFetch<EquipmentCreateOrUpdateResponse>(`/equipment/${equipmentFormTarget.id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          })
        : apiFetch<EquipmentCreateOrUpdateResponse>('/equipment', {
            method: 'POST',
            body: JSON.stringify(payload),
          });
    },
    onSuccess: async () => {
      closeEquipmentForm();
      await qc.invalidateQueries({ queryKey: ['equipment-catalog'] });
    },
  });

  // Vinculado ao equipamento em edição no modal (nunca ao alvo de exclusão),
  // para permitir enviar/remover a foto sem fechar o formulário.
  const liveEquipmentFormTarget = equipmentFormTarget
    ? equipmentItems.find((eq) => eq.id === equipmentFormTarget.id) ?? equipmentFormTarget
    : null;

  const uploadEquipmentPhotoMutation = useMutation({
    mutationFn: async ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return apiFetch<EquipmentCreateOrUpdateResponse>(`/equipment/${id}/photo`, { method: 'POST', body: form });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['equipment-catalog'] });
      await qc.invalidateQueries({ queryKey: ['equipment-photo'] });
    },
  });

  function triggerEquipmentPhotoUpload() {
    equipmentPhotoInputRef.current?.click();
  }

  function handleEquipmentPhotoSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (file && equipmentFormTarget) {
      uploadEquipmentPhotoMutation.mutate({ id: equipmentFormTarget.id, file });
    }
  }

  const [removeEquipmentPhotoOpen, { open: openRemoveEquipmentPhoto, close: closeRemoveEquipmentPhoto }] =
    useDisclosure(false);

  const removeEquipmentPhotoMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<EquipmentPhotoDeleteResponse>(`/equipment/${id}/photo`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemoveEquipmentPhoto();
      await qc.invalidateQueries({ queryKey: ['equipment-catalog'] });
      await qc.invalidateQueries({ queryKey: ['equipment-photo'] });
    },
  });

  // ---- Exclusão de equipamento (DELETE /equipment/{id}, admin) — soft ------
  // delete (`is_active = 0`). Não há proteção explícita no backend contra
  // excluir equipamento em uso por associações/academias — a mensagem do
  // modal deixa isso claro para o admin decidir com cuidado.
  const [deleteEquipmentOpen, { open: openDeleteEquipment, close: closeDeleteEquipment }] = useDisclosure(false);
  const [deleteEquipmentTarget, setDeleteEquipmentTarget] = useState<Equipment | null>(null);

  function openDeleteEquipmentConfirm(equipment: Equipment) {
    setDeleteEquipmentTarget(equipment);
    openDeleteEquipment();
  }

  const deleteEquipmentMutation = useMutation({
    mutationFn: (id: number) => apiFetch<EquipmentDeleteResponse>(`/equipment/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDeleteEquipment();
      setDeleteEquipmentTarget(null);
      await qc.invalidateQueries({ queryKey: ['equipment-catalog'] });
    },
  });

  return (
    <Stack gap="xl">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Equipamentos x Exercícios
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Gerencie o catálogo global de equipamentos (nome + foto) e a associação GLOBAL (sem academia) entre
          equipamento e exercício canônico: &quot;este equipamento serve este exercício?&quot;. Para dizer quais equipamentos
          uma academia possui, use a tela Academias.
        </Text>
      </div>

      <Tabs defaultValue="associacoes" color="brand" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="associacoes">Associações</Tabs.Tab>
          <Tabs.Tab value="massa">Associação em massa</Tabs.Tab>
          <Tabs.Tab value="catalogo">Catálogo de equipamentos</Tabs.Tab>
          <Tabs.Tab value="seguranca">Segurança</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="associacoes" pt="lg">
          <Stack gap="md">
            <Group justify="flex-end">
              {isAdmin ? (
                <Button color="brand" leftSection={<IconLink size={16} />} onClick={openCreate}>
                  Nova associação
                </Button>
              ) : (
                <Tooltip label="Ação restrita a administradores">
                  <Button color="brand" leftSection={<IconLink size={16} />} disabled>
                    Nova associação
                  </Button>
                </Tooltip>
              )}
            </Group>

            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Group mb="md" wrap="wrap">
          <Select
            label="Filtrar por equipamento"
            placeholder="Todos os equipamentos"
            clearable
            searchable
            data={equipmentItems.map((eq) => ({ value: String(eq.id), label: eq.name }))}
            value={filterEquipmentId}
            onChange={setFilterEquipmentId}
            w={260}
          />
          <Select
            label="Filtrar por grupo muscular primário"
            placeholder="Todos os grupos"
            clearable
            searchable
            data={primaryGroupOptions.map((g) => ({ value: g, label: g }))}
            value={filterPrimaryGroup}
            onChange={setFilterPrimaryGroup}
            w={260}
          />
        </Group>

        {mappingsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
            {errorMessage(mappingsQuery.error, 'Não foi possível carregar as associações.')}
          </Alert>
        )}
        {removeMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
            {isForbidden(removeMutation.error)
              ? 'Ação restrita a administradores.'
              : errorMessage(removeMutation.error, 'Não foi possível remover a associação.')}
          </Alert>
        )}

        {mappingsQuery.isLoading && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando associações...</Text>
          </Group>
        )}

        {!mappingsQuery.isLoading && !mappingsQuery.isError && (
          <Table.ScrollContainer minWidth={700}>
            <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Foto</Table.Th>
                  <Table.Th>Equipamento</Table.Th>
                  <Table.Th>Exercício</Table.Th>
                  <Table.Th>Amazfit</Table.Th>
                  <Table.Th>Grupo primário</Table.Th>
                  <Table.Th>Grupo secundário</Table.Th>
                  <Table.Th>Convenção de carga</Table.Th>
                  <Table.Th>Apelidos</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {links.map((link) => (
                  <Table.Tr key={link.id}>
                    <Table.Td>
                      <EquipmentPhotoThumb
                        photoUrl={link.photo_url}
                        alt={link.equipment_name}
                        size={40}
                        onOpen={lightbox.open}
                      />
                    </Table.Td>
                    <Table.Td c={BRAND.textPrimary}>{link.equipment_name}</Table.Td>
                    <Table.Td c={BRAND.textPrimary}>{link.exercise_name || '-'}</Table.Td>
                    <Table.Td c={BRAND.textMuted}>{link.exercise_amazfit_name || '-'}</Table.Td>
                    <Table.Td>
                      {link.primary_group ? (
                        <Badge variant="light" color="brand">
                          {link.primary_group}
                        </Badge>
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                    <Table.Td>
                      {link.secondary_group ? (
                        <Badge variant="outline" color="gray">
                          {link.secondary_group}
                        </Badge>
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Select
                        data={LOAD_CONVENTION_OPTIONS}
                        value={link.load_convention}
                        onChange={(value) =>
                          loadConventionMutation.mutate({ linkId: link.id, value })
                        }
                        placeholder={LOAD_CONVENTION_EMPTY_LABEL}
                        clearable
                        disabled={!isAdmin || savingConventionId === link.id}
                        aria-label={`Convenção de carga de ${link.equipment_name}`}
                        w={210}
                        size="xs"
                        comboboxProps={{ withinPortal: true }}
                      />
                    </Table.Td>
                    <Table.Td>
                      {link.exercise_id ? (
                        <ExerciseAliasesPopover
                          exerciseId={link.exercise_id}
                          exerciseName={link.exercise_name || '-'}
                          isAdmin={isAdmin}
                        />
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                    <Table.Td>
                      {isAdmin ? (
                        <Tooltip label="Remover associação">
                          <ActionIcon color="red" variant="subtle" onClick={() => openRemoveConfirm(link)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      ) : (
                        <Tooltip label="Ação restrita a administradores">
                          <ActionIcon color="red" variant="subtle" disabled>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
                {links.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={9}>
                      <Text c={BRAND.textMuted} ta="center">
                        Nenhuma associação encontrada para os filtros selecionados.
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="massa" pt="lg">
          <Stack gap="md">
            <Text c={BRAND.textMuted} size="sm">
              Associe vários exercícios de uma vez a um mesmo equipamento: escolha o grupo muscular, depois o
              equipamento, marque/desmarque os exercícios e confirme. Nada é gravado até você clicar em salvar.
            </Text>

            <Group align="flex-start" gap="md" wrap={isMobile ? 'wrap' : 'nowrap'} style={{ alignItems: 'stretch' }}>
              <Paper
                withBorder
                radius="lg"
                p="md"
                style={{
                  background: BRAND.bgElevated,
                  borderColor: BRAND.border,
                  width: isMobile ? '100%' : 260,
                  flexShrink: isMobile ? undefined : 0,
                }}
              >
                <Text fw={600} c={BRAND.textPrimary} mb="sm">
                  1. Grupo muscular
                </Text>
                {muscleGroupsQuery.isLoading && <Loader size="sm" color="brand" />}
                {muscleGroupsQuery.isError && (
                  <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                    {errorMessage(muscleGroupsQuery.error, 'Não foi possível carregar os grupos musculares.')}
                  </Alert>
                )}
                <Stack gap={4}>
                  {muscleGroups.map((group) => (
                    <Paper
                      key={group.id}
                      withBorder
                      radius="md"
                      p="xs"
                      style={{
                        cursor: 'pointer',
                        borderColor: massGroupId === group.id ? BRAND.orange : BRAND.border,
                        background: massGroupId === group.id ? BRAND.bgSurface : 'transparent',
                      }}
                      onClick={() => {
                        setMassGroupId(group.id);
                        setMassEquipmentId(null);
                      }}
                    >
                      <Text size="sm" c={BRAND.textPrimary} fw={massGroupId === group.id ? 700 : 500}>
                        {group.name}
                      </Text>
                    </Paper>
                  ))}
                  {!muscleGroupsQuery.isLoading && muscleGroups.length === 0 && (
                    <Text size="sm" c={BRAND.textMuted}>
                      Nenhum grupo muscular cadastrado.
                    </Text>
                  )}
                </Stack>
              </Paper>

              <Paper
                withBorder
                radius="lg"
                p="md"
                style={{
                  background: BRAND.bgElevated,
                  borderColor: BRAND.border,
                  width: isMobile ? '100%' : 300,
                  flexShrink: isMobile ? undefined : 0,
                }}
              >
                <Text fw={600} c={BRAND.textPrimary} mb="sm">
                  2. Equipamento
                </Text>
                <TextInput
                  placeholder="Buscar equipamento..."
                  leftSection={<IconSearch size={16} />}
                  value={equipmentSearch}
                  onChange={(event) => setEquipmentSearch(event.currentTarget.value)}
                  mb="sm"
                  size="xs"
                />
                {!massGroupId && (
                  <Text size="sm" c={BRAND.textMuted} mb="sm">
                    Escolha um grupo para ver quais equipamentos já o atendem.
                  </Text>
                )}
                {massGroupEquipmentQuery.isError && (
                  <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="sm">
                    {errorMessage(
                      massGroupEquipmentQuery.error,
                      'Não foi possível verificar quais equipamentos já atendem este grupo.',
                    )}
                  </Alert>
                )}
                {browsableEquipmentLoading && <Loader size="sm" color="brand" />}
                <Stack gap={4}>
                  {browsableEquipmentItems.map((eq) => (
                    <Paper
                      key={eq.id}
                      withBorder
                      radius="md"
                      p="xs"
                      style={{
                        cursor: 'pointer',
                        borderColor: massEquipmentId === eq.id ? BRAND.orange : BRAND.border,
                        background: massEquipmentId === eq.id ? BRAND.bgSurface : 'transparent',
                      }}
                      onClick={() => setMassEquipmentId(eq.id)}
                    >
                      <Group gap="xs" wrap="nowrap" justify="space-between">
                        <Group gap="xs" wrap="nowrap">
                          <EquipmentPhotoThumb photoUrl={eq.photo_url} alt={eq.name} size={32} onOpen={lightbox.open} />
                          <Text size="sm" c={BRAND.textPrimary} fw={massEquipmentId === eq.id ? 700 : 500}>
                            {eq.name}
                          </Text>
                        </Group>
                        {massGroupId && massGroupEquipmentIds.has(eq.id) && (
                          <Badge size="sm" variant="light" color="teal" leftSection={<IconCheck size={10} />}>
                            já associado
                          </Badge>
                        )}
                      </Group>
                    </Paper>
                  ))}
                  {!browsableEquipmentLoading && browsableEquipmentItems.length === 0 && (
                    <Text size="sm" c={BRAND.textMuted}>
                      Nenhum equipamento no catálogo.
                    </Text>
                  )}
                </Stack>
              </Paper>

              <Paper
                withBorder
                radius="lg"
                p="md"
                style={{ background: BRAND.bgElevated, borderColor: BRAND.border, flex: 1, minWidth: 0 }}
              >
                <Group justify="space-between" align="flex-start" mb="sm" wrap="wrap">
                  <div>
                    <Text fw={600} c={BRAND.textPrimary}>
                      3. Exercícios{massGroup ? ` — ${massGroup.name}` : ''}
                      {massEquipment ? ` x ${massEquipment.name}` : ''}
                    </Text>
                    <Text size="sm" c={BRAND.textMuted}>
                      Marque os exercícios que este equipamento atende.
                    </Text>
                  </div>
                  {massEquipment && massGroup && (
                    <Badge variant="light" color="brand">
                      {massDraft.size} marcado(s)
                    </Badge>
                  )}
                </Group>

                {!massGroup && <Text c={BRAND.textMuted}>Selecione um grupo muscular para começar.</Text>}
                {massGroup && !massEquipment && (
                  <Text c={BRAND.textMuted}>Selecione um equipamento para editar as associações.</Text>
                )}

                {massGroup && massEquipment && (massExercisesQuery.isLoading || massEquipmentLinksQuery.isLoading) && (
                  <Group>
                    <Loader size="sm" color="brand" />
                    <Text c={BRAND.textMuted}>Carregando exercícios...</Text>
                  </Group>
                )}

                {massGroup && massEquipment && (massExercisesQuery.isError || massEquipmentLinksQuery.isError) && (
                  <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                    {errorMessage(
                      massExercisesQuery.error || massEquipmentLinksQuery.error,
                      'Não foi possível carregar os exercícios deste grupo.',
                    )}
                  </Alert>
                )}

                {massGroup &&
                  massEquipment &&
                  !massExercisesQuery.isLoading &&
                  !massEquipmentLinksQuery.isLoading &&
                  !massExercisesQuery.isError &&
                  !massEquipmentLinksQuery.isError && (
                    <Stack gap="sm">
                      <Table.ScrollContainer minWidth={420}>
                        <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
                          <Table.Thead>
                            <Table.Tr>
                              <Table.Th w={70}>Atende</Table.Th>
                              <Table.Th>Exercício</Table.Th>
                              <Table.Th>Amazfit</Table.Th>
                            </Table.Tr>
                          </Table.Thead>
                          <Table.Tbody>
                            {massExercises.map((exercise) => (
                              <Table.Tr key={exercise.id}>
                                <Table.Td>
                                  <Checkbox
                                    color="brand"
                                    checked={massDraft.has(exercise.id)}
                                    disabled={!isAdmin || massSaveMutation.isPending}
                                    onChange={(event) =>
                                      toggleMassDraft(exercise.id, event.currentTarget.checked)
                                    }
                                    aria-label={`Associar ${exercise.exercise_name}`}
                                  />
                                </Table.Td>
                                <Table.Td c={BRAND.textPrimary}>{exercise.exercise_name}</Table.Td>
                                <Table.Td c={BRAND.textMuted}>{exercise.amazfit_name || '-'}</Table.Td>
                              </Table.Tr>
                            ))}
                            {massExercises.length === 0 && (
                              <Table.Tr>
                                <Table.Td colSpan={3}>
                                  <Text c={BRAND.textMuted} ta="center">
                                    Nenhum exercício canônico cadastrado para este grupo.
                                  </Text>
                                </Table.Td>
                              </Table.Tr>
                            )}
                          </Table.Tbody>
                        </Table>
                      </Table.ScrollContainer>

                      {massSaveMutation.isError && (
                        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                          {isForbidden(massSaveMutation.error)
                            ? 'Ação restrita a administradores.'
                            : errorMessage(massSaveMutation.error, 'Não foi possível salvar todas as associações.')}
                        </Alert>
                      )}

                      <Group justify="space-between" wrap="wrap">
                        <Text size="sm" c={BRAND.textMuted}>
                          {massProgress && massSaveMutation.isPending
                            ? `${massProgress.done}/${massProgress.total} salvos...`
                            : massProgress && massSaveMutation.isSuccess
                              ? `${massProgress.done}/${massProgress.total} salvos.`
                              : massPendingCount === 0
                                ? 'Nenhuma alteração pendente.'
                                : `${massToAdd.length} a associar, ${massToRemove.length} a remover.`}
                        </Text>
                        <Group gap="xs">
                          <Button
                            variant="default"
                            disabled={massPendingCount === 0 || massSaveMutation.isPending}
                            onClick={() => setMassDraft(new Set(massExistingLinks.keys()))}
                          >
                            Descartar
                          </Button>
                          <Tooltip label="Ação restrita a administradores" disabled={isAdmin}>
                            <Button
                              color="brand"
                              leftSection={<IconDeviceFloppy size={16} />}
                              loading={massSaveMutation.isPending}
                              disabled={!isAdmin || massPendingCount === 0}
                              onClick={() => massSaveMutation.mutate()}
                            >
                              Salvar {massPendingCount > 0 ? `(${massPendingCount})` : ''}
                            </Button>
                          </Tooltip>
                        </Group>
                      </Group>
                    </Stack>
                  )}
              </Paper>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="catalogo" pt="lg">
          <Stack gap="md">
            <Group justify="flex-end">
              {isAdmin ? (
                <Button color="brand" leftSection={<IconPlus size={16} />} onClick={openCreateEquipment}>
                  Novo equipamento
                </Button>
              ) : (
                <Tooltip label="Ação restrita a administradores">
                  <Button color="brand" leftSection={<IconPlus size={16} />} disabled>
                    Novo equipamento
                  </Button>
                </Tooltip>
              )}
            </Group>

            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <TextInput
                label="Buscar equipamento"
                placeholder="Filtrar por nome..."
                leftSection={<IconSearch size={16} />}
                value={equipmentSearch}
                onChange={(event) => setEquipmentSearch(event.currentTarget.value)}
                mb="md"
                w={isMobile ? '100%' : 320}
              />
              {browsableEquipmentError && (
                <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
                  {errorMessage(
                    equipmentSearchTerm ? equipmentSearchQuery.error : equipmentQuery.error,
                    'Não foi possível carregar o catálogo de equipamentos.',
                  )}
                </Alert>
              )}
              {deleteEquipmentMutation.isError && (
                <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
                  {isForbidden(deleteEquipmentMutation.error)
                    ? 'Ação restrita a administradores.'
                    : errorMessage(deleteEquipmentMutation.error, 'Não foi possível excluir o equipamento.')}
                </Alert>
              )}

              {browsableEquipmentLoading && (
                <Group>
                  <Loader size="sm" color="brand" />
                  <Text c={BRAND.textMuted}>Carregando catálogo...</Text>
                </Group>
              )}

              {!browsableEquipmentLoading && !browsableEquipmentError && (
                <Table.ScrollContainer minWidth={500}>
                  <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Foto</Table.Th>
                        <Table.Th>Equipamento</Table.Th>
                        <Table.Th>Tipo</Table.Th>
                        <Table.Th>Fabricante / modelo</Table.Th>
                        <Table.Th>Localização</Table.Th>
                        <Table.Th />
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {browsableEquipmentItems.map((eq) => (
                        <Table.Tr key={eq.id}>
                          <Table.Td>
                            <EquipmentPhotoThumb photoUrl={eq.photo_url} alt={eq.name} size={40} onOpen={lightbox.open} />
                          </Table.Td>
                          <Table.Td c={BRAND.textPrimary}>{eq.name}</Table.Td>
                          <Table.Td c={BRAND.textMuted}>
                            {eq.equipment_type_id ? equipmentTypeNameById.get(eq.equipment_type_id) || '-' : '-'}
                          </Table.Td>
                          <Table.Td c={BRAND.textMuted}>
                            {[eq.manufacturer, eq.model].filter(Boolean).join(' / ') || '-'}
                          </Table.Td>
                          <Table.Td c={BRAND.textMuted}>{eq.location || '-'}</Table.Td>
                          <Table.Td>
                            <Group gap="xs" wrap="nowrap" justify="flex-end">
                              {isAdmin ? (
                                <>
                                  <Tooltip label="Editar equipamento">
                                    <ActionIcon color="brand" variant="subtle" onClick={() => openEditEquipment(eq)}>
                                      <IconPencil size={16} />
                                    </ActionIcon>
                                  </Tooltip>
                                  <Tooltip label="Excluir equipamento">
                                    <ActionIcon
                                      color="red"
                                      variant="subtle"
                                      onClick={() => openDeleteEquipmentConfirm(eq)}
                                    >
                                      <IconTrash size={16} />
                                    </ActionIcon>
                                  </Tooltip>
                                </>
                              ) : (
                                <Tooltip label="Ação restrita a administradores">
                                  <ActionIcon color="brand" variant="subtle" disabled>
                                    <IconPencil size={16} />
                                  </ActionIcon>
                                </Tooltip>
                              )}
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                      {browsableEquipmentItems.length === 0 && (
                        <Table.Tr>
                          <Table.Td colSpan={6}>
                            <Text c={BRAND.textMuted} ta="center">
                              Nenhum equipamento cadastrado no catálogo ainda.
                            </Text>
                          </Table.Td>
                        </Table.Tr>
                      )}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              )}
            </Paper>
          </Stack>
        </Tabs.Panel>
        <Tabs.Panel value="seguranca" pt="lg">
          <EquipmentSafetyTab
            equipment={equipmentItems}
            equipmentTypes={equipmentTypesQuery.data?.items || []}
            isAdmin={isAdmin}
            loading={equipmentQuery.isLoading}
          />
        </Tabs.Panel>
      </Tabs>

      {/* ---- Modal: nova associação (POST /equipment-exercises) ----------- */}
      <Modal
        opened={createOpen}
        onClose={closeCreate}
        title="Nova associação equipamento x exercício"
        size="lg"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="sm">
          <Text c={BRAND.textMuted} size="sm">
            Diz que o equipamento selecionado atende o exercício canônico escolhido. Se a associação já existir
            (mesmo desativada), ela é reativada em vez de duplicada.
          </Text>

          <Select
            label="Equipamento"
            placeholder="Selecione um equipamento do catálogo"
            data={equipmentItems.map((eq) => ({ value: String(eq.id), label: eq.name }))}
            value={createEquipmentId}
            onChange={setCreateEquipmentId}
            searchable
            required
          />

          <Select
            label="Exercício canônico"
            placeholder="Busque por nome, Amazfit, grupo ou músculo"
            leftSection={<IconSearch size={16} />}
            data={exerciseSelectData}
            value={createMappingId}
            onChange={setCreateMappingId}
            searchable
            searchValue={exerciseSearch}
            onSearchChange={setExerciseSearch}
            nothingFoundMessage={exerciseOptionsQuery.isFetching ? 'Buscando...' : 'Nenhum exercício encontrado'}
            required
          />

          {createMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(createMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(createMutation.error, 'Não foi possível criar a associação.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeCreate}>
              Cancelar
            </Button>
            <Button
              color="brand"
              loading={createMutation.isPending}
              disabled={!canSubmitCreate}
              onClick={() => createMutation.mutate()}
            >
              Associar
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ---- Modal: remoção (DELETE /equipment-exercises/{id}) ------------ */}
      <Modal
        opened={removeOpen}
        onClose={closeRemove}
        title="Confirmar remoção"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Remover a associação entre &quot;{removeTarget?.equipment_name}&quot; e &quot;
            {removeTarget?.exercise_name}&quot;? Você pode recriá-la a qualquer momento.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeRemove}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={removeMutation.isPending}
              onClick={() => removeTarget && removeMutation.mutate(removeTarget.id)}
            >
              Remover
            </Button>
          </Group>
        </Stack>
      </Modal>

      <input
        ref={equipmentPhotoInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        style={{ display: 'none' }}
        onChange={handleEquipmentPhotoSelected}
      />

      {/* ---- Modal: criar/editar equipamento (POST/PUT /equipment) -------- */}
      <Modal
        opened={equipmentFormOpen}
        onClose={closeEquipmentForm}
        title={equipmentFormTarget ? 'Editar equipamento' : 'Novo equipamento'}
        size="md"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <TextInput
            label="Nome"
            placeholder="Ex.: Leg press 45°"
            required
            value={equipmentFormName}
            onChange={(event) => setEquipmentFormName(event.currentTarget.value)}
          />

          <Select
            label="Tipo de equipamento"
            placeholder="Selecione (opcional)"
            clearable
            searchable
            data={equipmentTypeSelectData}
            value={equipmentFormTypeId}
            onChange={setEquipmentFormTypeId}
            nothingFoundMessage={
              equipmentTypesQuery.isLoading ? 'Carregando...' : 'Nenhum tipo cadastrado em Domínio de exercícios'
            }
          />
          <TextInput
            label="Fabricante"
            placeholder="Ex.: Life Fitness"
            value={equipmentFormManufacturer}
            onChange={(event) => setEquipmentFormManufacturer(event.currentTarget.value)}
          />
          <TextInput
            label="Modelo"
            placeholder="Ex.: Hammer Strength MTS"
            value={equipmentFormModel}
            onChange={(event) => setEquipmentFormModel(event.currentTarget.value)}
          />
          <TextInput
            label="Localização"
            placeholder="Ex.: Mezanino, fundo à direita"
            value={equipmentFormLocation}
            onChange={(event) => setEquipmentFormLocation(event.currentTarget.value)}
          />

          {equipmentFormTarget && (
            <div>
              <Text size="sm" fw={600} mb={4} c={BRAND.textPrimary}>
                Foto
              </Text>
              <Group gap="sm" align="center">
                <EquipmentPhotoThumb
                  photoUrl={liveEquipmentFormTarget?.photo_url}
                  alt={liveEquipmentFormTarget?.name || 'Equipamento'}
                  size={56}
                  onOpen={lightbox.open}
                />
                <Button
                  variant="light"
                  color="brand"
                  size="xs"
                  leftSection={<IconUpload size={14} />}
                  loading={uploadEquipmentPhotoMutation.isPending}
                  onClick={triggerEquipmentPhotoUpload}
                >
                  Enviar foto
                </Button>
                {liveEquipmentFormTarget?.has_photo && (
                  <Button variant="light" color="orange" size="xs" onClick={openRemoveEquipmentPhoto}>
                    Remover foto
                  </Button>
                )}
              </Group>
              {uploadEquipmentPhotoMutation.isError && (
                <Text size="xs" c="red" mt={4}>
                  {isForbidden(uploadEquipmentPhotoMutation.error)
                    ? 'Ação restrita a administradores.'
                    : errorMessage(uploadEquipmentPhotoMutation.error, 'Não foi possível enviar a foto.')}
                </Text>
              )}
            </div>
          )}
          {!equipmentFormTarget && (
            <Text size="xs" c={BRAND.textMuted}>
              Salve o equipamento primeiro para poder enviar uma foto.
            </Text>
          )}

          {upsertEquipmentMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(upsertEquipmentMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(upsertEquipmentMutation.error, 'Não foi possível salvar o equipamento.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeEquipmentForm}>
              Cancelar
            </Button>
            <Button
              color="brand"
              disabled={!equipmentFormName.trim()}
              loading={upsertEquipmentMutation.isPending}
              onClick={() => upsertEquipmentMutation.mutate()}
            >
              Salvar
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ---- Modal: remover foto do equipamento (DELETE /equipment/{id}/photo) --- */}
      <Modal
        opened={removeEquipmentPhotoOpen}
        onClose={closeRemoveEquipmentPhoto}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir a foto de &quot;{equipmentFormTarget?.name}&quot;? Essa ação não pode ser desfeita.
          </Text>
          {removeEquipmentPhotoMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(removeEquipmentPhotoMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(removeEquipmentPhotoMutation.error, 'Não foi possível excluir a foto.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeRemoveEquipmentPhoto}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={removeEquipmentPhotoMutation.isPending}
              onClick={() => equipmentFormTarget && removeEquipmentPhotoMutation.mutate(equipmentFormTarget.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ---- Modal: excluir equipamento (DELETE /equipment/{id}) ---------- */}
      <Modal
        opened={deleteEquipmentOpen}
        onClose={closeDeleteEquipment}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir o equipamento &quot;{deleteEquipmentTarget?.name}&quot; do catálogo? Ele deixará de aparecer
            nas listas de associação e nas academias, mas pode ser reativado depois recadastrando o mesmo nome.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDeleteEquipment}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteEquipmentMutation.isPending}
              onClick={() => deleteEquipmentTarget && deleteEquipmentMutation.mutate(deleteEquipmentTarget.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>

      <PhotoLightbox photo={lightbox.photo} onClose={lightbox.close} />
    </Stack>
  );
}
