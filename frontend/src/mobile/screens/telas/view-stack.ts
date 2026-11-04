/**
 * Pilha de visões de página inteira (formulário, editor de linha, geração de
 * plano). Cada visão aberta empurra UMA entrada no histórico; o voltar do
 * navegador/gesto fecha só a visão do TOPO, e o `history.back()` que a própria
 * tela dispara ao fechar por código é ignorado (não fecha a visão de baixo).
 * Módulo único com um só listener de `popstate`: por isso a ordem de aninhamento
 * não importa.
 */
type Entry = { close: () => void; popped: boolean };

const entries: Entry[] = [];
let ignoreNextPops = 0;
let listening = false;

function onPop() {
  if (ignoreNextPops > 0) {
    ignoreNextPops -= 1;
    return;
  }
  const top = entries[entries.length - 1];
  if (!top) return;
  top.popped = true;
  entries.pop();
  top.close();
}

/** Há alguma visão de página inteira aberta (o voltar do cabeçalho deve só fechá-la)? */
export function hasOpenView(): boolean {
  return entries.length > 0;
}

/**
 * Abre uma visão. `release(unmounting)` a retira da pilha: fechar por código
 * desfaz a entrada de histórico; sair da rota (`unmounting`) não mexe no histórico.
 */
export function registerView(close: () => void): { release: (unmounting: boolean) => void } {
  if (!listening) {
    window.addEventListener('popstate', onPop);
    listening = true;
  }
  window.history.pushState(window.history.state, '');
  const entry: Entry = { close, popped: false };
  entries.push(entry);
  return {
    release(unmounting) {
      const index = entries.indexOf(entry);
      if (index >= 0) entries.splice(index, 1);
      if (!entry.popped && !unmounting) {
        ignoreNextPops += 1;
        window.history.back();
      }
    },
  };
}
