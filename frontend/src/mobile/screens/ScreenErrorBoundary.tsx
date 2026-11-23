'use client';

import { Component, type ErrorInfo, type ReactNode } from 'react';
import { ErrorState, OfflineState } from '../ui';
import { isChunkLoadError } from './chunk-error';

function isNextNavigationError(error: unknown): boolean {
  const digest = (error as { digest?: unknown } | null)?.digest;
  return typeof digest === 'string' && (digest.startsWith('NEXT_REDIRECT') || digest.startsWith('NEXT_NOT_FOUND'));
}

type Props = { children: ReactNode; resetKey: string };
type State = { failed: boolean; error: unknown };

/**
 * Contém o erro de render da tela mobile DENTRO da área de conteúdo: cabeçalho e
 * navegação do shell seguem de pé (o shell fica dentro do layout `(app)`, que o
 * `error.tsx` do segmento não cobre; sem isto o erro subia ao `global-error`).
 * Fica POR FORA do `ChunkBoundary` de `lazy.tsx`: falha de chunk é tratada lá, como
 * antes; o que ele relança cai aqui. Chunk que chegue até aqui (tela estática) vira
 * o mesmo "Sem conexão". `resetKey` (rota) reinicia o limite ao navegar.
 */
export class ScreenErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { failed: true, error };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    // Só o erro técnico; nada do que o usuário digitou entra no log.
    console.error(error);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.retry();
  }

  retry = () => this.setState({ failed: false, error: null });

  // Chunk que não baixou só volta com a página recarregada; os demais erros só zeram o estado.
  retryChunk = () => window.location.reload();

  render() {
    if (!this.state.failed) return this.props.children;
    // redirect()/notFound() do Next chegam como erro com digest; o Next os trata acima daqui.
    if (isNextNavigationError(this.state.error)) throw this.state.error;
    if (isChunkLoadError(this.state.error)) return <OfflineState onRetry={this.retryChunk} />;
    return (
      <ErrorState
        title="Algo deu errado nesta tela."
        description="Você continua conectado. Tente de novo ou use a navegação para ir a outra tela."
        onRetry={this.retry}
      />
    );
  }
}
