'use client';

import { Alert, Box, Button, FileButton, Group, Image, Loader, Skeleton, Stack, Text, Textarea } from '@mantine/core';
import { IconAlertTriangle, IconPhotoOff, IconTrash, IconUpload } from '@tabler/icons-react';
import { useRef, useState, type RefObject } from 'react';
import { blobToBase64 } from '@/lib/base64';
import {
  exerciseCardErrorText,
  referenceAcceptAttribute,
  referenceResizeOptions,
  useExerciseCardReferences,
} from '@/lib/exercise-cards';
import type { ExerciseCardReference, ExerciseCardReferenceLimits } from '@/lib/exercise-cards-types';
import { resizeImageForUpload } from '@/lib/image-resize';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

const THUMB_SIZE = 96;

/** Foto reduzida maior que o teto do backend (`limits.max_upload_bytes`): recusa antes de enviar. */
export function isReferenceBlobTooLarge(blobBytes: number, limits: Pick<ExerciseCardReferenceLimits, 'max_upload_bytes'>): boolean {
  return blobBytes > limits.max_upload_bytes;
}

/** Só enquanto houver vaga: `limits.max_active` vem do backend. */
export function canUploadReference(activeCount: number, limits: Pick<ExerciseCardReferenceLimits, 'max_active'>): boolean {
  return activeCount < limits.max_active;
}

function Thumb({ reference }: { reference: ExerciseCardReference }) {
  const [broken, setBroken] = useState(false);
  const label = `Foto de referência ${reference.id}`;
  if (!reference.url || broken) {
    return (
      <Box
        role="img"
        aria-label={`${label} (miniatura indisponível)`}
        style={{
          width: THUMB_SIZE,
          height: THUMB_SIZE,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 6,
          border: `1px dashed ${BRAND.textMuted}`,
        }}
      >
        <IconPhotoOff size={24} />
      </Box>
    );
  }
  return (
    <Image
      src={reference.url}
      alt={label}
      w={THUMB_SIZE}
      h={THUMB_SIZE}
      fit="cover"
      radius="sm"
      onError={() => setBroken(true)}
    />
  );
}

export type ExerciseCardReferencesViewProps = {
  items: ExerciseCardReference[];
  limits: ExerciseCardReferenceLimits | null;
  loading: boolean;
  loadError: unknown;
  note: string;
  uploading: boolean;
  /** Erro local (redução/tamanho) ou da API (409/400...) do último envio ou exclusão. */
  actionError: string | null;
  onNoteChange: (value: string) => void;
  onPick: (file: File | null) => void;
  /** Ref do `FileButton`: `resetRef.current?.()` limpa o input para o mesmo arquivo disparar `onChange` de novo. */
  resetRef?: RefObject<() => void>;
  onAskDelete: (reference: ExerciseCardReference) => void;
  onRetry: () => void;
};

/** Parte visual (sem hooks de dados): testável por `renderToStaticMarkup`. */
export function ExerciseCardReferencesView({
  items,
  limits,
  loading,
  loadError,
  note,
  uploading,
  actionError,
  onNoteChange,
  onPick,
  resetRef,
  onAskDelete,
  onRetry,
}: ExerciseCardReferencesViewProps) {
  const canUpload = limits != null && canUploadReference(items.length, limits);
  return (
    <Stack gap="xs" data-card-references>
      <Text fw={600} size="sm">
        Fotos de referência
      </Text>
      <Text size="xs" c={BRAND.textMuted}>
        As fotos orientam a geometria do aparelho e a posição do corpo no card. A pessoa e o rosto da foto não são
        copiados.
      </Text>

      {loading && <Skeleton height={THUMB_SIZE} data-card-references-loading />}

      {loadError != null && (
        <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />} data-card-references-error>
          <Group gap="xs" justify="space-between" wrap="wrap">
            <Text size="sm">
              {exerciseCardErrorText(loadError, 'Não foi possível carregar as fotos de referência.')}
            </Text>
            <Button size="xs" variant="light" color="orange" onClick={onRetry}>
              Tentar de novo
            </Button>
          </Group>
        </Alert>
      )}

      {!loading && loadError == null && items.length === 0 && (
        <Text size="sm" c={BRAND.textMuted} data-card-references-empty>
          Nenhuma foto de referência. Sem elas, a IA gera o aparelho pela descrição do exercício.
        </Text>
      )}

      {items.length > 0 && (
        <Group gap="sm" align="flex-start" wrap="wrap" component="ul" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {items.map((reference) => (
            <Box component="li" key={reference.id} data-reference-id={reference.id} style={{ width: THUMB_SIZE }}>
              <Stack gap={4}>
                <Thumb reference={reference} />
                {reference.note && (
                  <Text size="xs" c={BRAND.textMuted} lineClamp={2} title={reference.note}>
                    {reference.note}
                  </Text>
                )}
                <Button
                  size="compact-xs"
                  variant="subtle"
                  color="red"
                  leftSection={<IconTrash size={12} />}
                  aria-label={`Excluir foto de referência ${reference.id}`}
                  onClick={() => onAskDelete(reference)}
                >
                  Excluir
                </Button>
              </Stack>
            </Box>
          ))}
        </Group>
      )}

      {limits != null && !canUpload && (
        <Text size="xs" c={BRAND.textMuted} data-card-references-full>
          Limite de {limits.max_active} fotos atingido. Exclua uma para enviar outra.
        </Text>
      )}

      {limits != null && canUpload && (
        <Stack gap="xs" data-card-references-upload>
          <Textarea
            label="Nota (opcional) — vai para a IA junto com a foto"
            description="Ex.: rolo atrás dos tornozelos, polia abaixo dos ombros."
            value={note}
            maxLength={limits.note_max_chars}
            autosize
            minRows={1}
            maxRows={3}
            disabled={uploading}
            onChange={(event) => onNoteChange(event.currentTarget.value)}
          />
          <Group gap="xs">
            <FileButton resetRef={resetRef} onChange={onPick} accept={referenceAcceptAttribute(limits.accepted_types)} disabled={uploading}>
              {(props) => (
                <Button
                  {...props}
                  variant="light"
                  leftSection={uploading ? <Loader size={14} /> : <IconUpload size={16} />}
                  disabled={uploading}
                >
                  {uploading ? 'Enviando...' : 'Enviar foto de referência'}
                </Button>
              )}
            </FileButton>
          </Group>
        </Stack>
      )}

      {actionError != null && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} data-card-references-action-error>
          {actionError}
        </Alert>
      )}
    </Stack>
  );
}

/** Fotos de referência do exercício (admin): lista, envio reduzido no navegador e exclusão com confirmação. */
export function ExerciseCardReferences({ exerciseId }: { exerciseId: number }) {
  const { query, createMutation, deleteMutation } = useExerciseCardReferences({ exerciseId, enabled: true });
  const resetRef = useRef<() => void>(null);
  const [note, setNote] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ExerciseCardReference | null>(null);

  const items = query.data?.items ?? [];
  const limits = query.data?.limits ?? null;

  async function handlePick(file: File | null) {
    if (!file || !limits) return;
    // Limpa o input já: após erro (400/409) o mesmo arquivo precisa poder ser escolhido de novo.
    resetRef.current?.();
    setLocalError(null);
    createMutation.reset();
    let imageBase64: string;
    try {
      const blob = await resizeImageForUpload(file, referenceResizeOptions(limits));
      if (isReferenceBlobTooLarge(blob.size, limits)) {
        setLocalError('A foto continua grande demais depois de reduzida. Escolha outra imagem.');
        return;
      }
      imageBase64 = await blobToBase64(blob, { padding: false });
    } catch (error) {
      setLocalError(error instanceof Error && error.message ? error.message : 'Não foi possível preparar a foto.');
      return;
    }
    createMutation.mutate(
      { image_base64: imageBase64, note: note.trim() || null },
      { onSuccess: () => setNote('') },
    );
  }

  const apiError = createMutation.error ?? null;
  const actionError =
    localError ?? (apiError != null ? exerciseCardErrorText(apiError, 'Não foi possível enviar a foto.') : null);

  return (
    <>
      <ExerciseCardReferencesView
        items={items}
        limits={limits}
        loading={query.isLoading}
        loadError={query.isError ? query.error : null}
        note={note}
        uploading={createMutation.isPending}
        actionError={actionError}
        onNoteChange={setNote}
        onPick={(file) => void handlePick(file)}
        resetRef={resetRef}
        onAskDelete={(reference) => {
          deleteMutation.reset();
          setDeleteTarget(reference);
        }}
        onRetry={() => void query.refetch()}
      />
      <ConfirmDeleteModal
        opened={deleteTarget != null}
        title="Excluir esta foto de referência?"
        message="A foto deixa de orientar as próximas gerações. Cards já publicados não mudam."
        loading={deleteMutation.isPending}
        error={deleteMutation.error}
        fallbackErrorMessage="Não foi possível excluir a foto."
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return;
          deleteMutation.mutate(deleteTarget.id, { onSuccess: () => setDeleteTarget(null) });
        }}
      />
    </>
  );
}
