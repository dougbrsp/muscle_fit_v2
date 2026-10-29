'use client';

import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Skeleton,
  Stack,
  Text,
  Textarea,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconCheck,
  IconClockOff,
  IconEye,
  IconPhotoOff,
  IconRefresh,
  IconSparkles,
  IconTrash,
} from '@tabler/icons-react';
import { useState } from 'react';
import { PhotoLightbox, usePhotoLightbox } from '@/app/(fullscreen)/modo-treino/PhotoLightbox';
import {
  deriveExerciseCardView,
  elapsedSeconds,
  EXERCISE_CARD_REASON_MAX_CHARS,
  EXERCISE_CARD_SLOW_AFTER_SECONDS,
  EXERCISE_CARD_STATUS_BADGES,
  EXERCISE_CARD_TYPICAL_SECONDS,
  exerciseCardErrorText,
  formatCardDateTime,
  formatCostUsd,
  formatElapsed,
  DRAFT_EXPIRED_CODE,
  exerciseCardErrorCode,
  generationFailureMessage,
  isDraftExpired,
  parentForNewGeneration,
  useExerciseCardManager,
  useNow,
  type ExerciseCardViewKind,
} from '@/lib/exercise-cards';
import type { ExerciseCardGeneration, PublishedExerciseCard } from '@/lib/exercise-cards-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';
import { ExerciseCardHistoryList } from './ExerciseCardHistoryList';
import { RegenerateCardModal } from './RegenerateCardModal';

const PREVIEW_WIDTH = 240;
const THUMB_WIDTH = 132;

/** Tooltip que também aparece com o botão desabilitado (o `<button disabled>` não emite eventos de mouse). */
function ActionTooltip({ label, children }: { label: string | null; children: React.ReactNode }) {
  return (
    <Tooltip label={label} disabled={!label} withArrow>
      <Box component="span" style={{ display: 'inline-block' }}>
        {children}
      </Box>
    </Tooltip>
  );
}

/** Card em retrato 2:3, clicável para ampliar no `PhotoLightbox`. */
function CardImage({
  src,
  alt,
  width,
  onOpen,
  onError,
}: {
  src: string;
  alt: string;
  width: number;
  onOpen: () => void;
  onError?: () => void;
}) {
  return (
    <UnstyledButton
      onClick={onOpen}
      aria-label={`Ampliar ${alt}`}
      style={{ width, maxWidth: '100%', flexShrink: 0, cursor: 'zoom-in' }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        onError={onError}
        style={{
          display: 'block',
          width: '100%',
          aspectRatio: '2 / 3',
          objectFit: 'cover',
          borderRadius: 8,
          border: `1px solid ${BRAND.border}`,
          background: BRAND.bg,
        }}
      />
    </UnstyledButton>
  );
}

function GeneratingBlock({ generation, now }: { generation: ExerciseCardGeneration; now: number }) {
  const seconds = elapsedSeconds(generation, now);
  return (
    <Group align="flex-start" gap="md" data-card-generating={generation.id}>
      <Skeleton w={PREVIEW_WIDTH} maw="100%" radius="md" style={{ aspectRatio: '2 / 3' }} />
      <Stack gap={6} style={{ flex: '1 1 180px' }}>
        <Group gap="xs">
          <Loader size="xs" color="brand" />
          <Text fw={600} c={BRAND.textPrimary}>
            {generation.status === 'PENDING' ? 'Na fila para gerar…' : 'Gerando o card…'}
          </Text>
        </Group>
        <Text size="sm" c={BRAND.textPrimary} data-elapsed-seconds={seconds}>
          {formatElapsed(seconds)} decorridos
        </Text>
        <Text size="xs" c={BRAND.textMuted}>
          Costuma levar ~{EXERCISE_CARD_TYPICAL_SECONDS} s (até 2 min).
        </Text>
        {seconds >= EXERCISE_CARD_SLOW_AFTER_SECONDS && (
          <Text size="xs" c="yellow.4">
            Está demorando mais que o normal. Se passar de 10 minutos sem resposta, a geração é encerrada e você pode
            tentar de novo.
          </Text>
        )}
        {generation.request_instruction && (
          <Text size="xs" c={BRAND.textMuted}>
            Instrução: “{generation.request_instruction}”
          </Text>
        )}
        <Text size="xs" c={BRAND.textMuted}>
          Pode fechar esta tela: a geração continua e o resultado aparece aqui quando você voltar.
        </Text>
      </Stack>
    </Group>
  );
}

function ReviewBlock({
  generation,
  alt,
  publishing,
  publishErrorShown,
  draftExpired,
  previewBroken,
  reloadingPreview,
  onOpen,
  onPreviewError,
  onReloadPreview,
}: {
  generation: ExerciseCardGeneration;
  alt: string;
  publishing: boolean;
  /** A aprovação acabou de falhar nesta aba e o erro já está na tela — não repete. */
  publishErrorShown: boolean;
  /** M72: rascunho passou dos 7 dias (lifecycle do S3) — não dá para aprovar. */
  draftExpired: boolean;
  previewBroken: boolean;
  reloadingPreview: boolean;
  onOpen: (src: string) => void;
  onPreviewError: () => void;
  onReloadPreview: () => void;
}) {
  const cost = formatCostUsd(generation.cost_usd);
  const generatedAt = formatCardDateTime(generation.generated_at);
  const previewUrl = generation.preview_url;

  if (draftExpired) {
    return (
      <Stack gap="sm" data-draft-expired>
        <Group align="flex-start" gap="md">
          <Stack
            gap="xs"
            align="center"
            justify="center"
            w={PREVIEW_WIDTH}
            maw="100%"
            p="md"
            style={{ aspectRatio: '2 / 3', border: `1px dashed ${BRAND.border}`, borderRadius: 8 }}
          >
            <IconClockOff size={28} color={BRAND.textMuted} />
            <Text size="sm" fw={600} ta="center" c={BRAND.textPrimary}>
              Rascunho expirado — gere de novo
            </Text>
            <Text size="xs" ta="center" c={BRAND.textMuted}>
              Rascunhos não aprovados são apagados depois de 7 dias e não podem mais ser publicados.
            </Text>
          </Stack>
          <Stack gap={4} style={{ flex: '1 1 180px' }}>
            {generatedAt && (
              <Text size="xs" c={BRAND.textMuted}>
                Gerado em {generatedAt}
              </Text>
            )}
            {generation.request_instruction && (
              <Text size="xs" c={BRAND.textMuted}>
                Instrução: “{generation.request_instruction}”
              </Text>
            )}
          </Stack>
        </Group>
      </Stack>
    );
  }

  return (
    <Stack gap="sm">
      <Alert color="yellow" variant="light" icon={<IconEye size={16} />} title="Revise a execução ilustrada antes de publicar.">
        Confira posição inicial, movimento, equipamento e músculos. A imagem já é o card final — com logo, nome e
        músculos — e é exatamente o que será publicado.
      </Alert>
      {generation.error_code && !publishing && !publishErrorShown && (
        <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />}>
          A última tentativa de publicar falhou: {generationFailureMessage(generation.error_code)} Aprove de novo ou gere
          outra versão.
        </Alert>
      )}
      <Group align="flex-start" gap="md">
        {previewUrl && !previewBroken ? (
          <CardImage src={previewUrl} alt={alt} width={PREVIEW_WIDTH} onOpen={() => onOpen(previewUrl)} onError={onPreviewError} />
        ) : (
          <Stack
            gap="xs"
            align="center"
            justify="center"
            w={PREVIEW_WIDTH}
            maw="100%"
            p="md"
            style={{ aspectRatio: '2 / 3', border: `1px dashed ${BRAND.border}`, borderRadius: 8 }}
          >
            <IconPhotoOff size={28} color={BRAND.textMuted} />
            <Text size="xs" ta="center" c={BRAND.textMuted}>
              Prévia indisponível (o link expira em 15 min).
            </Text>
            <Button size="xs" variant="light" onClick={onReloadPreview} loading={reloadingPreview}>
              Recarregar prévia
            </Button>
          </Stack>
        )}
        <Stack gap={4} style={{ flex: '1 1 180px' }}>
          {publishing && (
            <Group gap="xs">
              <Loader size="xs" color="brand" />
              <Text size="sm" fw={600} c={BRAND.textPrimary}>
                Publicando o card…
              </Text>
            </Group>
          )}
          {generatedAt && (
            <Text size="xs" c={BRAND.textMuted}>
              Gerado em {generatedAt}
            </Text>
          )}
          {generation.width != null && generation.height != null && (
            <Text size="xs" c={BRAND.textMuted}>
              {generation.width}×{generation.height} px
            </Text>
          )}
          {cost && (
            <Text size="xs" c={BRAND.textMuted}>
              Custo da geração: {cost}
            </Text>
          )}
          {generation.request_instruction && (
            <Text size="xs" c={BRAND.textMuted}>
              Instrução: “{generation.request_instruction}”
            </Text>
          )}
        </Stack>
      </Group>
    </Stack>
  );
}

function PublishedBlock({
  card,
  alt,
  heading,
  onOpen,
}: {
  card: PublishedExerciseCard;
  alt: string;
  heading: string | null;
  onOpen: (src: string) => void;
}) {
  const publishedAt = formatCardDateTime(card.published_at);
  return (
    <Stack gap="xs" data-card-published={card.version}>
      {heading && (
        <Text size="xs" fw={600} c={BRAND.textMuted} tt="uppercase">
          {heading}
        </Text>
      )}
      <Group align="flex-start" gap="md">
        <CardImage src={card.thumbnail_url} alt={alt} width={THUMB_WIDTH} onOpen={() => onOpen(card.url)} />
        <Stack gap={4} style={{ flex: '1 1 160px' }}>
          <Text size="sm" c={BRAND.textPrimary}>
            Versão {card.version} em uso{publishedAt ? ` desde ${publishedAt}` : ''}.
          </Text>
          <Text size="xs" c={BRAND.textMuted}>
            Clique na imagem para ampliar.
          </Text>
          <Anchor href={card.png_url} target="_blank" rel="noopener noreferrer" size="xs" c={BRAND.orange}>
            Abrir PNG original
          </Anchor>
        </Stack>
      </Group>
    </Stack>
  );
}

type RegenerateTarget = { parentId: number | null; title: string; description: string };

/**
 * Card ilustrado do exercício (PR4 dos cards; Estratégia B #349): gerar por IA
 * → revisar o card final → aprovar e publicar, com regeneração por instrução,
 * descarte com motivo e histórico. Espelha `ExecutionDescriptionSection`
 * (badges de estado, Tooltip admin-only, seção própria abaixo de `Divider`).
 *
 * Só admin aciona; sem `features.exercise_cards` nada é chamado e a seção diz
 * "Recurso desativado neste ambiente". O andamento é acompanhado pelo recurso
 * da geração (polling de 2,5 s enquanto PENDING/GENERATING/APPROVING).
 */
export function ExerciseCardSection({
  exerciseId,
  exerciseName,
  isAdmin,
  enabled,
}: {
  exerciseId: number;
  exerciseName: string;
  isAdmin: boolean;
  enabled: boolean;
}) {
  const { canManage, current, published, history, cardQuery, listQuery, generationQuery, startMutation, approveMutation, rejectMutation } =
    useExerciseCardManager({ exerciseId, enabled, isAdmin });
  const view: ExerciseCardViewKind = deriveExerciseCardView({ enabled, current, published });
  const live = view === 'generating' || view === 'approving';
  const now = useNow(view === 'generating');
  const lightbox = usePhotoLightbox();
  // Alvo separado do "aberto": o título continua certo durante a animação de fechar.
  const [regenerateTarget, setRegenerateTarget] = useState<RegenerateTarget | null>(null);
  const [regenerateOpen, setRegenerateOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [discardReason, setDiscardReason] = useState('');
  const [brokenPreviewUrl, setBrokenPreviewUrl] = useState<string | null>(null);

  const alt = `Card ilustrado — ${exerciseName}`;

  if (view === 'disabled') {
    return (
      <Alert
        color="gray"
        variant="light"
        icon={<IconPhotoOff size={16} />}
        title="Recurso desativado neste ambiente"
        data-card-state="disabled"
      >
        A geração de cards ilustrados não está ligada aqui. Quando estiver, esta seção permite gerar, revisar e publicar
        o card do exercício.
      </Alert>
    );
  }

  const initialError = (!cardQuery.data && cardQuery.error) || (canManage && !listQuery.data && listQuery.error);
  if (initialError) {
    return (
      <Alert color="red" icon={<IconAlertTriangle size={16} />} data-card-state="error">
        <Stack gap="xs" align="flex-start">
          <Text size="sm">{exerciseCardErrorText(initialError, 'Não foi possível carregar o card ilustrado.')}</Text>
          <Button
            size="xs"
            variant="light"
            color="red"
            onClick={() => {
              void cardQuery.refetch();
              if (canManage) void listQuery.refetch();
            }}
          >
            Tentar de novo
          </Button>
        </Stack>
      </Alert>
    );
  }

  if (cardQuery.isLoading || (canManage && listQuery.isLoading)) {
    return (
      <Group data-card-state="loading">
        <Loader size="sm" color="brand" />
        <Text c={BRAND.textMuted}>Carregando card ilustrado...</Text>
      </Group>
    );
  }

  const busy = startMutation.isPending || approveMutation.isPending || rejectMutation.isPending;
  const locked = !canManage || busy || live;
  const lockReason = !isAdmin
    ? 'Ação restrita a administradores'
    : live
      ? 'Aguarde a geração em andamento terminar'
      : null;
  const nextParent = parentForNewGeneration(current);
  const costHint = formatCostUsd(current?.cost_usd);

  function clearErrors() {
    startMutation.reset();
    approveMutation.reset();
    rejectMutation.reset();
  }

  function openRegenerate(target: RegenerateTarget) {
    clearErrors();
    setRegenerateTarget(target);
    setRegenerateOpen(true);
  }

  function startNow(parentId: number | null, instruction?: string) {
    clearErrors();
    startMutation.mutate({ parentId, instruction });
  }

  const draftExpired = isDraftExpired(current, {
    error: approveMutation.error,
    generationId: approveMutation.variables,
  });
  // O 410 DRAFT_EXPIRED já aparece no bloco da prévia — não repete no alerta de erro.
  const approveError = exerciseCardErrorCode(approveMutation.error) === DRAFT_EXPIRED_CODE ? null : approveMutation.error;
  const sectionError: unknown = (regenerateOpen ? null : startMutation.error) || approveError;
  const showPublishedBelow = published != null && (view === 'published' || view === 'generating' || view === 'failed');

  return (
    <Stack gap="sm" data-card-state={view}>
      <Group gap="xs">
        {published ? (
          <Badge color="teal" variant="light">
            Card publicado · v{published.version}
          </Badge>
        ) : (
          <Badge color="gray" variant="light">
            sem card publicado
          </Badge>
        )}
        {current && view !== 'published' && view !== 'empty' && (
          <Badge color={EXERCISE_CARD_STATUS_BADGES[current.status].color} variant="light">
            {EXERCISE_CARD_STATUS_BADGES[current.status].label}
          </Badge>
        )}
        {current && (view === 'review' || view === 'approving') && current.model && (
          <Badge color="blue" variant="light">
            {current.model}
            {current.prompt_version ? ` · ${current.prompt_version}` : ''}
          </Badge>
        )}
      </Group>

      {view === 'generating' && current && <GeneratingBlock generation={current} now={now} />}

      {(view === 'review' || view === 'approving') && current && (
        <ReviewBlock
          generation={current}
          alt={alt}
          publishing={view === 'approving' || approveMutation.isPending}
          publishErrorShown={approveMutation.isError}
          draftExpired={draftExpired}
          previewBroken={current.preview_url != null && brokenPreviewUrl === current.preview_url}
          reloadingPreview={generationQuery.isFetching}
          onOpen={(src) => lightbox.open({ src, alt })}
          onPreviewError={() => setBrokenPreviewUrl(current.preview_url)}
          onReloadPreview={() => void generationQuery.refetch()}
        />
      )}

      {view === 'failed' && current && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} title="A geração falhou">
          <Stack gap={4}>
            <Text size="sm">{generationFailureMessage(current.error_code)}</Text>
            {current.request_instruction && (
              <Text size="xs">Instrução usada: “{current.request_instruction}”</Text>
            )}
            {current.error_message && (
              <Text size="xs" c={BRAND.textMuted}>
                Detalhe técnico: {current.error_message}
              </Text>
            )}
          </Stack>
        </Alert>
      )}

      {view === 'empty' && (
        <Text size="sm" c={BRAND.textMuted}>
          {canManage
            ? 'Este exercício ainda não tem card ilustrado. A IA desenha a execução e o app monta o card com logo, nome e músculos; nada é publicado sem a sua revisão.'
            : 'Este exercício ainda não tem card ilustrado.'}
        </Text>
      )}

      {showPublishedBelow && published && (
        <PublishedBlock
          card={published}
          alt={alt}
          heading={view === 'published' ? null : 'Card em uso agora'}
          onOpen={(src) => lightbox.open({ src, alt })}
        />
      )}

      {canManage && generationQuery.isError && (
        <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />}>
          <Group gap="xs" justify="space-between" wrap="wrap">
            <Text size="sm">
              {exerciseCardErrorText(generationQuery.error, 'Não foi possível atualizar o andamento da geração.')}
            </Text>
            <Button size="xs" variant="light" color="orange" onClick={() => void generationQuery.refetch()}>
              Atualizar
            </Button>
          </Group>
        </Alert>
      )}

      {sectionError != null && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} data-card-error>
          {exerciseCardErrorText(sectionError, 'Não foi possível concluir a ação no card.')}
        </Alert>
      )}

      {canManage && nextParent != null && current?.reject_reason && (view === 'empty' || view === 'published') && (
        <Text size="xs" c={BRAND.textMuted}>
          A próxima geração leva em conta o motivo do último descarte: “{current.reject_reason}”.
        </Text>
      )}

      <Group gap="sm" wrap="wrap">
        {(view === 'review' || view === 'approving') && current ? (
          <>
            {!draftExpired && (
              <ActionTooltip label={lockReason}>
                <Button
                  color="brand"
                  leftSection={<IconCheck size={16} />}
                  loading={approveMutation.isPending}
                  disabled={locked && !approveMutation.isPending}
                  onClick={() => {
                    clearErrors();
                    approveMutation.mutate(current.id);
                  }}
                >
                  Aprovar e publicar
                </Button>
              </ActionTooltip>
            )}
            <ActionTooltip label={lockReason}>
              <Button
                color={draftExpired ? 'brand' : undefined}
                variant={draftExpired ? 'filled' : 'light'}
                leftSection={<IconRefresh size={16} />}
                disabled={locked}
                onClick={() =>
                  openRegenerate({
                    parentId: current.id,
                    title: 'Gerar novamente',
                    description: `Esta prévia vai para o histórico como substituída e uma nova ilustração é gerada.${
                      costHint ? ` Cada geração custa cerca de ${costHint}.` : ''
                    }`,
                  })
                }
              >
                Gerar novamente
              </Button>
            </ActionTooltip>
            <ActionTooltip label={lockReason}>
              <Button
                variant="subtle"
                color="red"
                leftSection={<IconTrash size={16} />}
                disabled={locked}
                onClick={() => {
                  clearErrors();
                  setDiscardReason('');
                  setDiscardOpen(true);
                }}
              >
                Descartar
              </Button>
            </ActionTooltip>
          </>
        ) : view === 'failed' && current ? (
          <>
            <ActionTooltip label={lockReason}>
              <Button
                color="brand"
                variant="light"
                leftSection={<IconRefresh size={16} />}
                loading={startMutation.isPending}
                disabled={locked && !startMutation.isPending}
                onClick={() => startNow(current.id, current.request_instruction ?? undefined)}
              >
                Tentar novamente
              </Button>
            </ActionTooltip>
            <ActionTooltip label={lockReason}>
              <Button
                variant="subtle"
                leftSection={<IconSparkles size={16} />}
                disabled={locked}
                onClick={() =>
                  openRegenerate({
                    parentId: current.id,
                    title: 'Gerar com outra instrução',
                    description: 'Uma nova geração parte desta que falhou, com a instrução que você escrever aqui.',
                  })
                }
              >
                Gerar com outra instrução
              </Button>
            </ActionTooltip>
          </>
        ) : published ? (
          <ActionTooltip label={lockReason}>
            <Button
              color="brand"
              variant="light"
              leftSection={<IconSparkles size={16} />}
              disabled={locked}
              onClick={() =>
                openRegenerate({
                  parentId: nextParent,
                  title: 'Gerar nova versão',
                  description: `O card publicado (v${published.version}) continua em uso até você aprovar a nova versão.`,
                })
              }
            >
              Gerar nova versão
            </Button>
          </ActionTooltip>
        ) : (
          <ActionTooltip label={lockReason}>
            <Button
              color="brand"
              leftSection={<IconSparkles size={16} />}
              loading={startMutation.isPending}
              disabled={locked && !startMutation.isPending}
              onClick={() => startNow(nextParent)}
            >
              Gerar card do exercício
            </Button>
          </ActionTooltip>
        )}
      </Group>

      {canManage && <ExerciseCardHistoryList items={history} />}

      <RegenerateCardModal
        opened={regenerateOpen}
        title={regenerateTarget?.title ?? ''}
        description={regenerateTarget?.description}
        confirmLabel={regenerateTarget?.title ?? 'Gerar'}
        loading={startMutation.isPending}
        error={regenerateOpen ? startMutation.error : null}
        onClose={() => setRegenerateOpen(false)}
        onConfirm={(instruction) => {
          if (!regenerateTarget) return;
          startMutation.mutate(
            { parentId: regenerateTarget.parentId, instruction },
            { onSuccess: () => setRegenerateOpen(false) },
          );
        }}
      />

      <ConfirmDeleteModal
        opened={discardOpen}
        title="Descartar esta geração?"
        message="A prévia não será publicada e fica no histórico como descartada."
        confirmLabel="Descartar"
        confirmDisabled={discardReason.trim().length > EXERCISE_CARD_REASON_MAX_CHARS}
        loading={rejectMutation.isPending}
        error={rejectMutation.error}
        fallbackErrorMessage="Não foi possível descartar a geração."
        onClose={() => setDiscardOpen(false)}
        onConfirm={() => {
          if (!current) return;
          rejectMutation.mutate(
            { generationId: current.id, reason: discardReason },
            { onSuccess: () => setDiscardOpen(false) },
          );
        }}
      >
        <Stack gap={4}>
          <Textarea
            label="Motivo (opcional)"
            description="Fica no histórico e, se você gerar de novo, vai junto para a IA."
            placeholder="Ex.: equipamento errado; posição inicial em pé no chão."
            autosize
            minRows={2}
            maxLength={EXERCISE_CARD_REASON_MAX_CHARS}
            value={discardReason}
            onChange={(event) => setDiscardReason(event.currentTarget.value)}
          />
          <Text size="xs" ta="right" c={BRAND.textMuted}>
            {discardReason.trim().length}/{EXERCISE_CARD_REASON_MAX_CHARS}
          </Text>
        </Stack>
      </ConfirmDeleteModal>

      <PhotoLightbox photo={lightbox.photo} onClose={lightbox.close} />
    </Stack>
  );
}
