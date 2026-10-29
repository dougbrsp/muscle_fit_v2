'use client';

import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  CopyButton,
  Group,
  List,
  Loader,
  Modal,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconCopy, IconDeviceWatch } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FormEvent, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { formatDateTimeOrDash } from '@/lib/format';
import { BRAND } from '@/theme';

/** Linha de `GET /devices?include_revoked=true` (M40). */
export interface DeviceItem {
  id: number;
  device_name: string;
  platform: string;
  paired_at: string | null;
  last_seen_at: string | null;
  revoked_at: string | null;
  /** Calculado pela API: 'active' | 'revoked'. */
  status: string;
}

/** Resposta de `POST /devices/pair`: o token cru só vem aqui, uma vez. */
export interface PairedDevice {
  device_id: number;
  device_name: string;
  platform: string;
  device_token: string;
}

const DEVICES_QUERY_KEY = ['devices', 'mine'] as const;

const PLATFORM_LABELS: Record<string, string> = { zepp: 'Zepp OS (Amazfit)' };

export function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? platform;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export interface DevicesListProps {
  items: DeviceItem[];
  onRevoke: (device: DeviceItem) => void;
}

/** Lista de dispositivos (apresentação pura). */
export function DevicesList({ items, onRevoke }: DevicesListProps) {
  if (items.length === 0) {
    return (
      <Text size="sm" c={BRAND.textMuted}>
        Nenhum dispositivo pareado.
      </Text>
    );
  }

  return (
    <Stack gap="sm">
      {items.map((device) => {
        const active = device.status === 'active';
        return (
          <Card key={device.id} withBorder radius="md" p="sm" style={{ borderColor: BRAND.border }}>
            <Group justify="space-between" align="flex-start" wrap="nowrap">
              <Stack gap={2} style={{ minWidth: 0 }}>
                <Group gap="xs">
                  <Text fw={600} c={BRAND.textPrimary} truncate>
                    {device.device_name}
                  </Text>
                  <Badge size="sm" variant="light" color={active ? 'green' : 'gray'}>
                    {active ? 'Ativo' : 'Revogado'}
                  </Badge>
                </Group>
                <Text size="xs" c={BRAND.textMuted}>
                  {platformLabel(device.platform)}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  Pareado em {formatDateTimeOrDash(device.paired_at)} · Último uso{' '}
                  {formatDateTimeOrDash(device.last_seen_at)}
                </Text>
                {!active && device.revoked_at && (
                  <Text size="xs" c={BRAND.textMuted}>
                    Revogado em {formatDateTimeOrDash(device.revoked_at)}
                  </Text>
                )}
              </Stack>
              {active && (
                <Button size="xs" variant="light" color="red" onClick={() => onRevoke(device)}>
                  Revogar
                </Button>
              )}
            </Group>
          </Card>
        );
      })}
    </Stack>
  );
}

export interface DeviceTokenRevealProps {
  device: PairedDevice;
  onDone: () => void;
}

/** Token recém-gerado: exibido uma única vez, com copiar e o passo a passo. */
export function DeviceTokenReveal({ device, onDone }: DeviceTokenRevealProps) {
  return (
    <Stack gap="sm">
      <Alert color="yellow" icon={<IconAlertTriangle />} title="Copie o token agora">
        Este token aparece só desta vez. Se perder, revogue o dispositivo e gere outro.
      </Alert>
      <Text size="sm" c={BRAND.textPrimary}>
        Token de <b>{device.device_name}</b>:
      </Text>
      <Code block style={{ wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>
        {device.device_token}
      </Code>
      <CopyButton value={device.device_token}>
        {({ copied, copy }) => (
          <Button
            variant="light"
            color={copied ? 'green' : 'brand'}
            leftSection={copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
            onClick={copy}
          >
            {copied ? 'Copiado' : 'Copiar token'}
          </Button>
        )}
      </CopyButton>
      <Text size="sm" fw={600} c={BRAND.textPrimary}>
        No celular:
      </Text>
      <List type="ordered" size="sm" c={BRAND.textMuted}>
        <List.Item>Abra o app Zepp.</List.Item>
        <List.Item>Entre em MuscleFit Treino → Configurações.</List.Item>
        <List.Item>Cole o token.</List.Item>
        <List.Item>Toque em Testar conexão.</List.Item>
      </List>
      <Group justify="flex-end">
        <Button color="brand" onClick={onDone}>
          Já copiei
        </Button>
      </Group>
    </Stack>
  );
}

/** Seção "Dispositivos" do perfil (M40): parear, listar e revogar relógios. */
export function DevicesCard() {
  const queryClient = useQueryClient();
  const [pairOpen, setPairOpen] = useState(false);
  const [deviceName, setDeviceName] = useState('');
  const [paired, setPaired] = useState<PairedDevice | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<DeviceItem | null>(null);

  const devicesQuery = useQuery({
    queryKey: DEVICES_QUERY_KEY,
    queryFn: () => apiFetch<{ items: DeviceItem[] }>('/devices?include_revoked=true'),
  });

  const pairMutation = useMutation({
    mutationFn: (name: string) =>
      apiFetch<PairedDevice>('/devices/pair', { method: 'POST', body: JSON.stringify({ device_name: name }) }),
    onSuccess: (data) => {
      setPaired(data);
      setDeviceName('');
      void queryClient.invalidateQueries({ queryKey: DEVICES_QUERY_KEY });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (deviceId: number) => apiFetch(`/devices/${deviceId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setRevokeTarget(null);
      void queryClient.invalidateQueries({ queryKey: DEVICES_QUERY_KEY });
    },
  });

  function openPair() {
    pairMutation.reset();
    setPaired(null);
    setDeviceName('');
    setPairOpen(true);
  }

  function closePair() {
    setPairOpen(false);
    // O token sai da memória da tela ao fechar: não volta a ser exibido.
    setPaired(null);
  }

  function handlePair(event: FormEvent) {
    event.preventDefault();
    const name = deviceName.trim();
    if (!name) return;
    pairMutation.mutate(name);
  }

  function openRevoke(device: DeviceItem) {
    revokeMutation.reset();
    setRevokeTarget(device);
  }

  return (
    <Card withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="md">
        <Group justify="space-between">
          <Group gap="xs">
            <IconDeviceWatch size={20} color={BRAND.textPrimary} />
            <Title order={4} c={BRAND.textPrimary}>
              Dispositivos
            </Title>
          </Group>
          <Button size="xs" color="brand" onClick={openPair}>
            Novo dispositivo
          </Button>
        </Group>
        <Text size="sm" c={BRAND.textMuted}>
          Relógios pareados com a sua conta. Cada um tem o próprio token e registra os treinos em seu nome.
        </Text>

        {devicesQuery.isLoading && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando dispositivos...</Text>
          </Group>
        )}
        {devicesQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar dispositivos">
            {errorMessage(devicesQuery.error, 'Não foi possível consultar os dispositivos.')}
          </Alert>
        )}
        {devicesQuery.data && <DevicesList items={devicesQuery.data.items} onRevoke={openRevoke} />}
      </Stack>

      <Modal opened={pairOpen} onClose={closePair} title="Novo dispositivo" size="md">
        {paired ? (
          <DeviceTokenReveal device={paired} onDone={closePair} />
        ) : (
          <form onSubmit={handlePair}>
            <Stack gap="md">
              <TextInput
                label="Nome do dispositivo"
                placeholder="ex.: Amazfit Balance"
                value={deviceName}
                onChange={(event) => setDeviceName(event.currentTarget.value)}
                required
                data-autofocus
              />
              {pairMutation.isError && (
                <Alert color="red" icon={<IconAlertTriangle />}>
                  {errorMessage(pairMutation.error, 'Não foi possível parear o dispositivo.')}
                </Alert>
              )}
              <Group justify="flex-end">
                <Button variant="default" onClick={closePair}>
                  Cancelar
                </Button>
                <Button type="submit" color="brand" loading={pairMutation.isPending} disabled={!deviceName.trim()}>
                  Gerar token
                </Button>
              </Group>
            </Stack>
          </form>
        )}
      </Modal>

      <Modal opened={revokeTarget !== null} onClose={() => setRevokeTarget(null)} title="Revogar dispositivo" size="sm">
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Revogar &quot;{revokeTarget?.device_name}&quot;? O relógio deixa de acessar sua conta na hora e precisará de
            um token novo.
          </Text>
          {revokeMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(revokeMutation.error, 'Não foi possível revogar o dispositivo.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRevokeTarget(null)}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={revokeMutation.isPending}
              onClick={() => revokeTarget && revokeMutation.mutate(revokeTarget.id)}
            >
              Revogar
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}
