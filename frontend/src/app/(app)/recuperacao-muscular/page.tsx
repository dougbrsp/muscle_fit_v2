import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada (F2.4, 2ª passada): "Recuperação muscular" foi absorvida pelo
// hub v3 em `/dashboard/recovery`.
//
// A 1ª passada da F2.4 (PR #228) decidiu MANTER esta tela: o hub não tinha
// mapa corporal, recomendação de séries nem os fatores do cálculo — a própria
// tela v3 dizia isso num alerta e linkava de volta para cá. A F2.5-A (M33)
// fechou os três e o alerta saiu. Paridade reverificada coluna a coluna antes
// de aposentar:
//
// - gráfico "Estado de recuperação" (recuperação % por grupo) e as colunas
//   Grupo / Recuperação / Horas restantes / Última data -> tabela "Prontidão
//   por grupo" (Prontidão, Falta, Recuperação estimada, Desde o estímulo,
//   Última sessão), do MESMO `MuscleRecoveryService`;
// - "Bom para" (data/hora em que o grupo fica disponível) -> é `agora +
//   horas restantes`, apresentado lá como a coluna "Falta"; nenhum dado a
//   mais entrava nessa célula;
// - Status (verde/amarelo/vermelho) e "Leitura" (`guidance`) -> o hub mostra
//   o `readiness_status`, que é o MESMO estado mapeado 1:1
//   (`MuscleRecoveryService._STATUS_META`, ver `lib/muscle-readiness.ts`);
//   esta tela exibia as três leituras do mesmo dado lado a lado;
// - "RPE médio" da janela -> `factors.avg_rpe_window`, que já vinha no payload
//   de `/analytics/dashboard/recovery-signals` desde o M33 e passou a ser
//   exibido nos "Fatores" (era a última coluna sem equivalente);
// - "Recomendação" e "Explicação" (fatores) -> colunas "Recomendação" e
//   "Fatores" (M33), repassadas do mesmo modelo;
// - mapa muscular -> bloco "Mapa corporal" (M33), mesmo componente
//   `MuscleBodyMap` e mesma escala de cor;
// - ajuste global de fadiga por TSB -> alerta "Ajuste global de fadiga" (M33);
// - janela de 14 dias (não oferecida como atalho no hub) -> "Período
//   personalizado".
//
// O hub ainda ACRESCENTA o painel de sinais objetivos contra a baseline
// pessoal e o recorte dia com treino × dia sem treino (M36).
//
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar
// aqui; este componente é o fallback para ambientes sem ela (`next dev`).
export default function RecuperacaoMuscularLegacyRedirectPage() {
  return <LegacyPathRedirect from="/recuperacao-muscular" to="/dashboard/recovery" />;
}
