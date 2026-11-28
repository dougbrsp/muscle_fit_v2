'use client';

import { IconChevronDown, IconLogout } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { ListGroup, ListRow } from '../ui';
import { BottomSheet } from '../ui/Sheets';
import styles from '../ui/ui.module.css';
import { isNavGroupItemActive } from './nav-active';
import type { GroupNavItem, NavGroup } from './primary-nav';

/**
 * Folha "Mais": lista agrupada com as telas que não são aba nem Evolução (as
 * seções só do computador não aparecem) e "Sair". Os grupos já chegam
 * filtrados por papel. Item com `children` expande as seções logo abaixo.
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
  // Escolha do usuário por item; sem escolha, abre expandido se a rota atual é uma das seções.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  useEffect(() => setToggled({}), [pathname]);

  const renderExpandable = (item: GroupNavItem & { children: NonNullable<GroupNavItem['children']> }) => {
    // A seção mais específica que casa vence (/nutricao/plano/x marca Plano, não Diário).
    const current = item.children
      .filter((c) => isNavGroupItemActive(c.href, pathname))
      .sort((a, b) => b.href.length - a.href.length)[0];
    const expanded = toggled[item.href] ?? current !== undefined;
    const panelId = `mais-sub-${item.href.replace(/\W+/g, '-')}`;
    const Icon = item.icon;
    return (
      <div key={item.href}>
        <button
          type="button"
          className={`${styles.row} ${styles.rowWithIcon}`}
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => setToggled((t) => ({ ...t, [item.href]: !expanded }))}
        >
          <span className={styles.rowIcon} aria-hidden>
            <Icon size={18} />
          </span>
          <span className={styles.rowBody}>
            <span className={styles.rowTitle}>{item.label}</span>
          </span>
          <IconChevronDown
            size={18}
            className={styles.chevron}
            aria-hidden
            style={{ transform: expanded ? 'rotate(180deg)' : undefined, transition: 'transform 150ms' }}
          />
        </button>
        <div id={panelId} hidden={!expanded} style={{ paddingLeft: 42 }}>
          {item.children.map((child) => (
            <ListRow
              key={child.href}
              title={child.label}
              href={child.href}
              onNavigate={onClose}
              current={child === current}
            />
          ))}
        </div>
      </div>
    );
  };

  return (
    <BottomSheet opened={opened} onClose={onClose} title="Mais">
      {groups.map((group) => (
        <ListGroup key={group.title} title={group.title}>
          {group.items.map((item) =>
            item.children?.length ? (
              renderExpandable({ ...item, children: item.children })
            ) : (
              <ListRow
                key={item.href}
                icon={item.icon}
                title={item.label}
                subtitle={item.href === '/perfil' && displayName ? displayName : undefined}
                href={item.href}
                onNavigate={onClose}
                current={isNavGroupItemActive(item.href, pathname)}
              />
            ),
          )}
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
