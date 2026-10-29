/**
 * Reserva o espaço de um gráfico enquanto o chunk do recharts (carregado sob
 * demanda via `next/dynamic`, COD-14) ainda não chegou. Mesma largura e altura
 * do `ResponsiveContainer` que ele substitui, para não haver salto de layout.
 * Vazio de propósito: o gráfico só aparece depois que os dados chegaram, e o
 * chunk costuma chegar antes disso.
 */
export function ChartPlaceholder({ height }: { height: number | string }) {
  return <div aria-hidden style={{ width: '100%', height }} />;
}
