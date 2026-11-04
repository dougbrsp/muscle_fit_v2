'use client';

/**
 * Modais e folhas do Mantine renderizam num portal fora do `<main>` do shell,
 * então a regra de 16 px do shell não chega nos campos deles (o iOS dá zoom ao
 * focar abaixo disso). Enquanto uma tela mobile está montada, este estilo cobre
 * os campos dos portais; ao desmontar ele sai (o desktop nunca o recebe).
 */
const CSS = `
.mantine-Modal-content input:not([type='checkbox']):not([type='radio']),
.mantine-Modal-content textarea,
.mantine-Drawer-content input:not([type='checkbox']):not([type='radio']),
.mantine-Drawer-content textarea,
.mantine-Popover-dropdown input,
.mantine-Select-dropdown input { font-size: 16px !important; }
[data-mobile-shell] .mantine-Input-input::placeholder { color: rgba(245, 245, 245, 0.64); opacity: 1; }
`;

export function PortalFieldsStyle() {
  return <style data-mobile-portal-fields="true">{CSS}</style>;
}
