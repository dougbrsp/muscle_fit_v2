'use client';

import {
  Component,
  Suspense,
  lazy,
  useState,
  type ComponentType,
  type ErrorInfo,
  type ReactNode,
} from 'react';
import { LoadingState, OfflineState } from '../ui';

/**
 * Falha ao baixar o chunk de uma tela (tipicamente sem rede, rota nunca
 * visitada): mostra "Sem conexao" com tentar de novo em vez de quebrar o shell.
 */
class ChunkBoundary extends Component<
  { children: ReactNode; onRetry: () => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // o estado de erro ja basta; nada a registrar no cliente
  }

  render() {
    if (this.state.failed) return <OfflineState onRetry={this.props.onRetry} />;
    return this.props.children;
  }
}

/**
 * Tela mobile carregada sob demanda: o chunk so baixa quando a rota abre no
 * shell mobile (o desktop nunca passa pelo registry, entao o bundle dele nao
 * cresce). Inicio e hub Evolucao NAO usam isto (ficam estaticos no shell para a
 * PWA abrir offline); so as telas das frentes B e C. Uso:
 *
 *   lazyScreen(() => import('./telas/ExerciciosScreen').then((m) => ({ default: m.ExerciciosScreen })))
 *
 * Falha de chunk mostra OfflineState; "Tentar de novo" refaz o import.
 */
export function lazyScreen(load: () => Promise<{ default: ComponentType }>): ComponentType {
  let Inner = lazy(load);
  function LazyScreen() {
    const [attempt, setAttempt] = useState(0);
    return (
      <ChunkBoundary
        key={attempt}
        onRetry={() => {
          Inner = lazy(load);
          setAttempt((n) => n + 1);
        }}
      >
        <Suspense fallback={<LoadingState label="Carregando…" />}>
          <Inner />
        </Suspense>
      </ChunkBoundary>
    );
  }
  return LazyScreen;
}
