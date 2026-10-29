'use client';

import { Alert, Button, Table, Text, Tooltip } from '@mantine/core';
import { IconCheck, IconLink } from '@tabler/icons-react';
import { useState } from 'react';
import type { UnmappedExerciseRow } from '@/lib/unmapped-exercises';
import { formatIsoDate } from '@/lib/unmapped-exercises';
import { AssociateCanonicalExerciseModal } from './AssociateCanonicalExerciseModal';

type Props = {
  rows: UnmappedExerciseRow[];
  // false: botão "Associar" desabilitado com tooltip (o 403 do backend
  // continua sendo a garantia real).
  canAssociate: boolean;
  restrictedLabel?: string;
  // Colunas de ocorrências e primeira/última vez (lista sem janela de dias).
  showHistory?: boolean;
};

/**
 * Tabela de exercícios sem classificação canônica + modal de associação +
 * mensagem de sucesso. A origem das linhas fica com a tela (ver mappers em
 * `lib/unmapped-exercises.ts`).
 */
export function UnmappedExercisesAssociation({
  rows,
  canAssociate,
  restrictedLabel = 'Ação restrita a administradores',
  showHistory = false,
}: Props) {
  const [target, setTarget] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function associateButton(name: string) {
    const button = (
      <Button
        size="xs"
        variant="light"
        leftSection={<IconLink size={14} />}
        disabled={!name || !canAssociate}
        onClick={() => {
          setSuccess(null);
          setTarget(name);
        }}
      >
        Associar
      </Button>
    );
    return canAssociate ? button : <Tooltip label={restrictedLabel}>{button}</Tooltip>;
  }

  return (
    <>
      {success && (
        <Alert color="green" icon={<IconCheck />} withCloseButton onClose={() => setSuccess(null)} mb="sm">
          {success}
        </Alert>
      )}

      <Table.ScrollContainer minWidth={showHistory ? 760 : 620}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Exercício</Table.Th>
              {showHistory && <Table.Th>Ocorrências</Table.Th>}
              <Table.Th>Séries</Table.Th>
              {showHistory && <Table.Th>Primeira vez</Table.Th>}
              {showHistory && <Table.Th>Última vez</Table.Th>}
              <Table.Th>Ação</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row, index) => (
              <Table.Tr key={`${row.name}-${index}`}>
                <Table.Td>{row.name || 'Sem nome'}</Table.Td>
                {showHistory && <Table.Td>{(row.exercisesCount ?? 0).toLocaleString('pt-BR')}</Table.Td>}
                <Table.Td>{row.sets.toLocaleString('pt-BR')}</Table.Td>
                {showHistory && <Table.Td>{formatIsoDate(row.firstSeen)}</Table.Td>}
                {showHistory && <Table.Td>{formatIsoDate(row.lastSeen)}</Table.Td>}
                <Table.Td>{associateButton(row.name)}</Table.Td>
              </Table.Tr>
            ))}
            {rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={showHistory ? 6 : 3}>
                  <Text size="sm" ta="center">
                    Nenhum exercício sem classificação canônica.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      <AssociateCanonicalExerciseModal
        aliasName={target}
        onClose={() => setTarget(null)}
        onAssociated={(response) =>
          setSuccess(`${response.alias_name} foi associado a ${response.target_exercise_name || 'um exercício canônico'}.`)
        }
      />
    </>
  );
}
