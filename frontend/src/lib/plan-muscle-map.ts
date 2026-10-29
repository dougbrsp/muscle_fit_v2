import { getApiBaseUrl } from './config';

/**
 * `GET /workout-plans/{plan_id}/muscle-map.png` exige sessão autenticada
 * (cookie httpOnly — diferente de foto de equipamento/logo de academia, que
 * agora são estáticas via CloudFront, este endpoint gera a imagem sob
 * demanda e continua autenticado): busca a imagem como blob autenticado
 * (`credentials: 'include'`) e devolve
 * uma Object URL para uso em `<img>`. Cacheada em
 * memória por plan_id — a imagem só muda quando o plano é salvo de novo, e a
 * tela sempre refaz o fetch com uma key nova (nenhuma invalidação necessária
 * aqui, cada save cria/atualiza o mesmo id mas a página é remontada ao
 * navegar de volta para o plano).
 *
 * Retorna `null` em qualquer falha (plano sem exercícios, rede, 404) — quem
 * chama trata como "mapa indisponível", nunca como erro bloqueante.
 */
const mapUrlCache = new Map<number, string>();
const inFlight = new Map<number, Promise<string | null>>();

export function fetchPlanMuscleMapUrl(planId: number): Promise<string | null> {
  const cached = mapUrlCache.get(planId);
  if (cached) return Promise.resolve(cached);

  const pending = inFlight.get(planId);
  if (pending) return pending;

  const promise = (async () => {
    try {
      const response = await fetch(`${getApiBaseUrl()}/workout-plans/${planId}/muscle-map.png`, {
        credentials: 'include',
      });
      if (!response.ok) return null;
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      mapUrlCache.set(planId, objectUrl);
      return objectUrl;
    } catch {
      return null;
    } finally {
      inFlight.delete(planId);
    }
  })();

  inFlight.set(planId, promise);
  return promise;
}
