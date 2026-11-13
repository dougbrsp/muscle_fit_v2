'use client';

import { Alert, Badge, Button, Group, List, Modal, NativeSelect, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { accessErrorMessage, accessKeys, replaceAccessGrants } from '@/lib/access-admin-api';
import type { AccessCatalog, AccessGroup } from '@/lib/access-admin-types';
import {
  cellKey,
  cellWarning,
  GrantMatrix,
  grantsToMatrix,
  groupResourcesByDomain,
  matrixToGrants,
  presentationLabel,
  warningsForSave,
} from '@/lib/access-grants';
import { BRAND } from '@/theme';

type Props = { group: AccessGroup; catalog: AccessCatalog; canEdit: boolean; onDirtyChange?: (dirty: boolean) => void };

/**
 * Matriz recurso x ação. Cada célula escolhe o escopo (ou nenhum). O backend
 * decide o que é válido; aqui só se reflete o catálogo e se envia o conjunto
 * inteiro em PUT /access/groups/{id}/grants.
 */
export function MatrizConcessoes({ group, catalog, canEdit, onDirtyChange }: Props) {
  const qc = useQueryClient();
  // Salvar exige admin.grupos/editar (PUT /access/groups/{id}/grants), além de o grupo ser editável.
  const readOnly = !group.editable || !canEdit;
  const [draft, setDraft] = useState<GrantMatrix>(() => grantsToMatrix(group.grants));
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    setDraft(grantsToMatrix(group.grants));
  }, [group.id, group.grants]);

  const sections = useMemo(
    () => groupResourcesByDomain(catalog.resources, { onlyGrantable: !readOnly }),
    [catalog.resources, readOnly],
  );
  const draftGrants = useMemo(() => matrixToGrants(draft, catalog), [draft, catalog]);
  const dirty = JSON.stringify(draftGrants) !== JSON.stringify(matrixToGrants(grantsToMatrix(group.grants), catalog));
  const pendingWarnings = useMemo(
    () => warningsForSave(group.grants, draftGrants, catalog),
    [group.grants, draftGrants, catalog],
  );

  // Avisa o pai se há rascunho não salvo (ele pede confirmação ao trocar de grupo).
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const saveMutation = useMutation({
    mutationFn: () => replaceAccessGrants(group.id, draftGrants),
    onSuccess: async () => {
      setConfirmOpen(false);
      await qc.invalidateQueries({ queryKey: accessKeys.groups });
      await qc.invalidateQueries({ queryKey: accessKeys.audit(group.id) });
    },
    onError: () => setConfirmOpen(false),
  });

  function requestSave() {
    saveMutation.reset();
    if (pendingWarnings.length > 0) setConfirmOpen(true);
    else saveMutation.mutate();
  }

  const scopeOptions = [
    { value: '', label: 'Nenhum' },
    ...catalog.scopes.map((scope) => ({ value: scope, label: presentationLabel(scope) })),
  ];

  return (
    <Stack gap="sm">
      {!group.editable && (
        <Alert color="gray" variant="light">
          Grupo de sistema: as concessões são fixas e aparecem somente para consulta.
        </Alert>
      )}
      {group.editable && !canEdit && (
        <Alert color="gray" variant="light">
          Seu perfil não pode editar concessões. As concessões aparecem somente para consulta.
        </Alert>
      )}

      <Table.ScrollContainer minWidth={560}>
        <Table withTableBorder verticalSpacing="xs" aria-label={`Concessões do grupo ${group.name}`}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Recurso</Table.Th>
              {catalog.actions.map((action) => (
                <Table.Th key={action}>{presentationLabel(action)}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sections.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={catalog.actions.length + 1}>
                  <Text c={BRAND.textMuted} ta="center">
                    Nenhum recurso disponível para concessão.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
            {sections.map((section) => (
              <SectionRows key={section.domain}>
                <Table.Tr>
                  <Table.Td colSpan={catalog.actions.length + 1} bg="var(--mantine-color-gray-light)">
                    <Text fw={700} size="sm" tt="capitalize">
                      {section.domain}
                    </Text>
                  </Table.Td>
                </Table.Tr>
                {section.resources.map((resource) => (
                  <Table.Tr key={resource.key}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm">{resource.label}</Text>
                        {resource.warning && (
                          <Tooltip label={resource.warning} multiline maw={320}>
                            <Badge color="orange" variant="light" leftSection={<IconAlertTriangle size={12} />}>
                              Atenção
                            </Badge>
                          </Tooltip>
                        )}
                        {!resource.grantable && (
                          <Badge color="gray" variant="outline">
                            Não concedível
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    {catalog.actions.map((action) => {
                      const key = cellKey(resource.key, action);
                      const scope = draft[key] ?? '';
                      const warning = cellWarning(resource, scope || undefined, catalog);
                      return (
                        <Table.Td key={action}>
                          <Group gap={4} wrap="nowrap">
                            <NativeSelect
                              size="xs"
                              aria-label={`${resource.label}: ${presentationLabel(action)}`}
                              data={scopeOptions}
                              value={scope}
                              disabled={readOnly || !resource.grantable}
                              onChange={(event) => {
                                const next = event.currentTarget.value;
                                setDraft((current) => {
                                  const copy = { ...current };
                                  if (next) copy[key] = next;
                                  else delete copy[key];
                                  return copy;
                                });
                              }}
                            />
                            {warning && scope && (
                              <Tooltip label={warning} multiline maw={320}>
                                <IconAlertTriangle
                                  size={16}
                                  color="var(--mantine-color-orange-6)"
                                  aria-label={`Aviso: ${warning}`}
                                  role="img"
                                />
                              </Tooltip>
                            )}
                          </Group>
                        </Table.Td>
                      );
                    })}
                  </Table.Tr>
                ))}
              </SectionRows>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      {saveMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {accessErrorMessage(saveMutation.error, 'Não foi possível salvar as concessões.')}
        </Alert>
      )}
      {saveMutation.isSuccess && !dirty && <Text size="sm" c="teal">Concessões salvas.</Text>}

      {!readOnly && (
        <Group justify="flex-end">
          <Button variant="default" disabled={!dirty} onClick={() => setDraft(grantsToMatrix(group.grants))}>
            Descartar alterações
          </Button>
          <Button color="brand" disabled={!dirty} loading={saveMutation.isPending} onClick={requestSave}>
            Salvar concessões
          </Button>
        </Group>
      )}

      <Modal opened={confirmOpen} onClose={() => setConfirmOpen(false)} title="Confirmar concessões com aviso" size="lg">
        <Stack gap="md">
          <Text size="sm">Estas concessões novas ou alteradas têm avisos. Confirme para salvar.</Text>
          <List spacing="xs" size="sm">
            {pendingWarnings.map((w) => (
              <List.Item key={`${w.resource}|${w.action}`}>
                <Text fw={600} size="sm">
                  {w.label} · {presentationLabel(w.action)} · {presentationLabel(w.scope)}
                </Text>
                {w.texts.map((text) => (
                  <Text key={text} size="sm" c={BRAND.textMuted}>
                    {text}
                  </Text>
                ))}
              </List.Item>
            ))}
          </List>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmOpen(false)}>
              Cancelar
            </Button>
            <Button color="orange" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
              Confirmar e salvar
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

/** Fragmento agrupador (linhas de uma seção de domínio). */
function SectionRows({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
