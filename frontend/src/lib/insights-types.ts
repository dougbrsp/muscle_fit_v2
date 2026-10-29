// Tipos para a tela "Insights" — pergunta livre respondida pela IA.
//
// F3.2 (Novo Dashboard): **o cliente não monta mais o contexto**. Até esta
// entrega, esta tela buscava quatro endpoints v1/v2 (`/analytics/summary`,
// `/analytics/training-trend`, a tabela de grupos mapeados e
// `/analytics/rpe-distribution`), serializava tudo num JSON e mandava como
// texto livre no campo `context`. O backend então concatenava esse texto com
// o **dump completo** de `rag/markdown` e chamava a OpenAI. Dois problemas
// numa coisa só: o modelo recebia números sem `nature`, sem versão de fórmula
// e sem qualidade declarada (e os recalculava), e recebia centenas de
// milhares de caracteres de metodologia sem relação com a pergunta.
//
// Agora:
//
//   - Requisição: `{ question, window?, date_from?, date_to? }`. `window` é
//     um `WindowKind` do hub (`7d | 28d | 90d | 180d | cycle | custom`),
//     default `28d`; `date_from`/`date_to` só para `custom`.
//   - O servidor monta o `intelligence_package` (contrato fechado, ≤ 40 KB,
//     `GET /analytics/dashboard/intelligence-package`) a partir da MESMA
//     leitura da visão geral, e injeta o RAG **filtrado** pelos grupos
//     musculares presentes nesse pacote.
//   - Resposta imediata do POST: 202 `{ job_id, status: "pending" }` — o
//     padrão assíncrono da Fase 3 (polling em `GET /jobs/{job_id}`, ver
//     `async-job-poll.ts`), inalterado.
//   - Resultado final do job (mesmo shape de sempre): `{ model, insight }`.
//   - Sem OPENAI_API_KEY no backend: job termina `failed` com `error` textual.
//
// `context` continua no tipo, opcional e **deprecado**: durante a janela de
// compatibilidade do deploy o backend aceita e IGNORA o campo (registrando
// `insights.legacy_context_ignored`), porque frontend e API não sobem no
// mesmo instante. Nenhum código novo deve preenchê-lo.
//
// Não existe endpoint de histórico de perguntas/respostas para /insights no
// backend atual (só há histórico para /cardio-recommendations) — por isso
// esta tela não persiste nem lista perguntas anteriores.

export type InsightsGenerateRequest = {
  question: string;
  window?: string;
  date_from?: string;
  date_to?: string;
  /** @deprecated F3.2 — o backend ignora. Ver o cabeçalho deste arquivo. */
  context?: string;
};

export type InsightsGenerateResponse = {
  model: string;
  insight: string;
};
