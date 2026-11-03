'use client';

import { IconLogout } from '@tabler/icons-react';
import { ListGroup, ListRow } from '../ui';
import { BottomSheet } from '../ui/Sheets';
import { isNavGroupItemActive } from './nav-active';
import type { NavGroup } from './primary-nav';

/**
 * Folha "Mais": lista agrupada com as telas que não são aba nem Evolução (as
 * seções só do computador não aparecem) e "Sair". Os grupos já chegam
 * filtrados por papel.
 */
export function MoreSheet({
  opened,
  onClose,
  groups,
  pathname,
  displayName,
  onLogout,
}: {
  opened: boolean;
  onClose: () => void;
  groups: readonly NavGroup[];
  pathname: string | null;
  displayName: string;
  onLogout: () => void;
}) {
  return (
    <BottomSheet opened={opened} onClose={onClose} title="Mais">
      {groups.map((group) => (
        <ListGroup key={group.title} title={group.title}>
          {group.items.map((item) => (
            <ListRow
              key={item.href}
              icon={item.icon}
              title={item.label}
              subtitle={item.href === '/perfil' && displayName ? displayName : undefined}
              href={item.href}
              onNavigate={onClose}
              current={isNavGroupItemActive(item.href, pathname)}
            />
          ))}
        </ListGroup>
      ))}
      <ListGroup>
        <ListRow
          icon={IconLogout}
          title="Sair"
          destructive
          onClick={() => {
            onClose();
            onLogout();
          }}
        />
      </ListGroup>
    </BottomSheet>
  );
}
