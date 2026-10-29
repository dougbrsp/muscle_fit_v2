// Campos numéricos/curtos lado a lado: 4 por linha quando o slot é largo
// (1 coluna no celular), 2x2 na grade de 4 colunas (~280 px por slot).
export const FIELD_GRID_STYLE = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
  gap: 'var(--mantine-spacing-xs)',
  alignItems: 'end',
} as const;
