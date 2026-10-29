'use client';

import { Avatar } from '@mantine/core';
import { IconUser } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { fetchUserPhotoUrl } from '@/lib/user-photo';

type Props = {
  userId: number;
  hasPhoto?: boolean;
  /** Incrementado pelo caller após upload/remoção para forçar novo fetch. */
  version?: number;
  size?: number | string;
  alt?: string;
};

/**
 * Miniatura de foto de perfil de QUALQUER usuário (`GET /users/{id}/photo`,
 * autenticada — ver `src/lib/user-photo.ts`). Sem foto, ou em caso de falha,
 * cai no ícone placeholder — nunca bloqueia a linha da tabela/modal.
 */
export function UserAvatar({ userId, hasPhoto, version = 0, size = 32, alt = '' }: Props) {
  const photoQuery = useQuery({
    queryKey: ['user-photo', userId, version],
    queryFn: () => fetchUserPhotoUrl(userId, version),
    enabled: Boolean(userId && hasPhoto),
    staleTime: Infinity,
  });

  return (
    <Avatar size={size} radius="xl" src={photoQuery.data ?? undefined} color="brand" alt={alt}>
      {!photoQuery.data && <IconUser size={typeof size === 'number' ? size * 0.55 : 18} />}
    </Avatar>
  );
}
