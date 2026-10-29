"""Invoca uma funcao pelo Lambda Runtime Interface Emulator (RIE), de dentro
do proprio container, com retry SO para falha transitoria de transporte.

Uso (o smoke passa este arquivo via stdin, sem copia-lo para a imagem):
    docker exec -i <ct> python - '<payload-json>' < .github/scripts/rie_invoke.py

Contrato:
  1. Prontidao: espera a porta do RIE aceitar conexao TCP (poll de 100 ms,
     ate READY_TIMEOUT_S). Isso nao conta como tentativa de invocacao.
  2. Invocacao: POST no endpoint de invocations, ate MAX_ATTEMPTS tentativas
     com backoff exponencial (0.5, 1, 2, 4 s) dentro de TOTAL_DEADLINE_S.
     So repete quando a falha e de transporte e o handler nao respondeu:
       - HTTP 502/503/504 SEM `errorType` no corpo (gateway/corpo vazio);
       - conexao recusada/resetada/fechada sem resposta.
  3. Qualquer resposta HTTP 200 e o resultado DEFINITIVO (inclusive um
     errorType inesperado): imprime o corpo em stdout e sai 0. Quem chama
     valida o conteudo. Nunca ha segunda invocacao depois de uma resposta.
  4. Resposta do runtime com status de erro (ex.: o RIE devolve 502 com
     {"errorType": "Runtime.HandlerNotFound"} quando o init falha), timeout de
     leitura (o handler pode estar executando) e qualquer outro status HTTP
     sao falha definitiva, sem retry.

Codigos de saida: 0 resposta do handler | 3 status HTTP nao transitorio |
4 timeout de leitura | 5 transitorio esgotado | 6 RIE nao ficou pronto.
Logs de diagnostico vao para stderr; stdout carrega so o corpo da resposta.
"""

import http.client
import json
import socket
import sys
import time
import urllib.error
import urllib.request

HOST, PORT = "127.0.0.1", 8080
URL = f"http://{HOST}:{PORT}/2015-03-31/functions/function/invocations"
READY_TIMEOUT_S = 15.0
MAX_ATTEMPTS = 5
TOTAL_DEADLINE_S = 30.0
REQUEST_TIMEOUT_S = 20.0
TRANSIENT_HTTP = {502, 503, 504}
TRANSIENT_EXC = (
    ConnectionRefusedError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    http.client.RemoteDisconnected,
)


def log(msg):
    print(f"[rie_invoke] {msg}", file=sys.stderr, flush=True)


def wait_ready():
    start = time.monotonic()
    while True:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                log(f"RIE aceitando conexao em {PORT} apos {time.monotonic() - start:.2f}s")
                return
        except OSError as exc:
            if time.monotonic() - start >= READY_TIMEOUT_S:
                log(f"RIE nao abriu {PORT} em {READY_TIMEOUT_S:.0f}s: {exc!r}")
                sys.exit(6)
            time.sleep(0.1)


def has_error_type(body):
    """True se o corpo e a resposta do runtime/handler (JSON com errorType)."""
    try:
        return isinstance(json.loads(body), dict) and "errorType" in json.loads(body)
    except ValueError:
        return False


def transient_reason(exc, body):
    """Motivo se `exc` e falha transitoria de transporte; None se definitiva."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in TRANSIENT_HTTP and not has_error_type(body):
            return f"HTTP {exc.code} sem errorType (corpo={body[:200]!r})"
        return None
    if isinstance(exc, urllib.error.URLError):
        exc = exc.reason
    if isinstance(exc, TRANSIENT_EXC):
        return repr(exc)
    return None


def main():
    payload = (sys.argv[1] if len(sys.argv) > 1 else "{}").encode()
    wait_ready()
    start = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(URL, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = resp.read().decode()
        except Exception as exc:  # noqa: BLE001 - classificado abaixo
            if isinstance(exc, (socket.timeout, TimeoutError)) or (
                isinstance(exc, urllib.error.URLError)
                and isinstance(exc.reason, (socket.timeout, TimeoutError))
            ):
                log(f"tentativa {attempt}: timeout de leitura ({REQUEST_TIMEOUT_S:.0f}s) - definitivo, sem retry")
                sys.exit(4)
            body = ""
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    body = exc.read().decode(errors="replace")
                except OSError:
                    body = ""
            reason = transient_reason(exc, body)
            if reason is None:
                detail = f" corpo={body[:2000]}" if body else ""
                log(f"tentativa {attempt}: falha NAO transitoria {exc!r}{detail} - definitivo, sem retry")
                sys.exit(3)
            delay = 0.5 * 2 ** (attempt - 1)
            elapsed = time.monotonic() - start
            if attempt == MAX_ATTEMPTS or elapsed + delay >= TOTAL_DEADLINE_S:
                log(f"tentativa {attempt}: {reason} - transitorio esgotado ({attempt} tentativas, {elapsed:.1f}s)")
                sys.exit(5)
            log(f"tentativa {attempt}: {reason} - transitorio, nova tentativa em {delay:.1f}s")
            time.sleep(delay)
            continue
        log(f"tentativa {attempt}: handler respondeu (HTTP 200) - resultado definitivo")
        print(body)
        return


if __name__ == "__main__":
    main()
