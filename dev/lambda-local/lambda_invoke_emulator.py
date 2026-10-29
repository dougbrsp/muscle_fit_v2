"""Emulador LOCAL da API `Invoke` do AWS Lambda - SOMENTE para o Docker
Compose de desenvolvimento (ver docs/local-dev.md, "Jobs assincronos").

Por que existe
--------------
Em producao, `musclefit-api-core` cria o job no DynamoDB e dispara a Lambda
de IA/relatorio com `boto3.client("lambda").invoke(InvocationType="Event")`
(`api/app/async_jobs.py::invoke_async`). No compose local nao existe Lambda,
entao o `invoke` falhava e nenhum job assincrono funcionava.

Este processo fala o MESMO protocolo HTTP que o boto3 usa para `Invoke`
(`POST /2015-03-31/functions/{FunctionName}/invocations`). O servico `api`
aponta o cliente Lambda para ca via `AWS_ENDPOINT_URL_LAMBDA` (suportado
nativamente pelo botocore), sem NENHUMA mudanca no codigo da aplicacao.

Para cada invocacao, roda o handler REAL de producao
(`app.lambda_plan_generator.handler` etc. - o mesmo mapeamento de
`image_config.command`/`CMD` do Terraform) num subprocesso Python novo:
- isolamento parecido com um ambiente de execucao Lambda (sem estado de
  modulo compartilhado entre jobs concorrentes);
- hot-reload de graca: cada invocacao importa o codigo montado em
  `./api/app`, sem reiniciar o container;
- `Event` responde 202 imediatamente (fire-and-forget, como a Lambda real);
  `RequestResponse` espera e devolve o retorno do handler.

O que este arquivo NAO e
------------------------
- Nao contem regra de negocio: so faz dispatch para `handler(event, context)`.
- Nao vai para nenhuma imagem de producao: fica fora do build context `api/`
  (so e montado por volume no compose) e nao e referenciado por `infra/`
  nem por workflows de deploy.
- Nao finge ser o runtime Lambda: NAO define `AWS_LAMBDA_FUNCTION_NAME`/
  `AWS_EXECUTION_ENV` no subprocesso. Por isso a regra anti-vazamento de
  `app/migrate.py::_guard_backend_in_lambda` (DB_BACKEND != data-api dentro
  de Lambda) continua valendo e intacta - e, no sentido inverso, este
  emulador se RECUSA a subir se detectar que esta dentro de uma Lambda real.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

logger = logging.getLogger("lambda-local")

_INVOKE_PATH = re.compile(r"^/2015-03-31/functions/(?P<name>[^/?]+)/invocations(?:\?.*)?$")

# Variaveis que o runtime Lambda real define. Presenca de qualquer uma =
# estamos dentro de uma Lambda de verdade, onde este emulador nunca deve rodar.
_LAMBDA_RUNTIME_MARKERS = ("AWS_LAMBDA_FUNCTION_NAME", "AWS_EXECUTION_ENV", "AWS_LAMBDA_RUNTIME_API")

_DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class FunctionConfig:
    name: str
    handler: str
    timeout: int


def parse_functions(spec: str) -> dict[str, FunctionConfig]:
    """`LAMBDA_LOCAL_FUNCTIONS`: lista separada por virgula/linha de
    `nome=modulo.handler[:timeout_segundos]`."""
    functions: dict[str, FunctionConfig] = {}
    for raw in re.split(r"[,\n]", spec or ""):
        item = raw.strip()
        if not item:
            continue
        name, sep, target = item.partition("=")
        if not sep or not name.strip() or not target.strip():
            raise ValueError(f"Entrada invalida em LAMBDA_LOCAL_FUNCTIONS: {item!r}")
        handler, _, timeout = target.strip().partition(":")
        functions[name.strip()] = FunctionConfig(
            name=name.strip(),
            handler=handler.strip(),
            timeout=int(timeout) if timeout.strip() else _DEFAULT_TIMEOUT_SECONDS,
        )
    return functions


def refuse_inside_real_lambda() -> None:
    present = [var for var in _LAMBDA_RUNTIME_MARKERS if os.environ.get(var)]
    if present:
        raise SystemExit(
            f"lambda-local: recusando subir - variaveis do runtime Lambda presentes ({', '.join(present)}). "
            "Este emulador e exclusivo do Docker Compose de desenvolvimento."
        )


# ---------------------------------------------------------------------------
# Execucao de uma invocacao (subprocesso)
# ---------------------------------------------------------------------------


def run_invocation(fn: FunctionConfig, payload: bytes, request_id: str) -> tuple[Optional[bytes], bool]:
    """Roda o handler num subprocesso. Retorna (payload de resposta, houve_erro)."""
    env = {k: v for k, v in os.environ.items() if k not in _LAMBDA_RUNTIME_MARKERS}
    with tempfile.TemporaryDirectory(prefix="lambda-local-") as tmp:
        event_path = os.path.join(tmp, "event.json")
        result_path = os.path.join(tmp, "result.json")
        with open(event_path, "wb") as fh:
            fh.write(payload or b"{}")
        cmd = [
            sys.executable,
            os.path.abspath(__file__),
            "--run",
            fn.handler,
            "--function-name",
            fn.name,
            "--request-id",
            request_id,
            "--timeout",
            str(fn.timeout),
            "--event-file",
            event_path,
            "--result-file",
            result_path,
        ]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, env=env, cwd=os.getcwd(), timeout=fn.timeout)
        except subprocess.TimeoutExpired:
            # Mesmo comportamento da Lambda real: timeout mata a execucao e o
            # handler nao chega a gravar nada (o job fica como estava).
            logger.error("%s request_id=%s TIMEOUT apos %ss", fn.name, request_id, fn.timeout)
            return json.dumps({"errorType": "Sandbox.Timedout", "errorMessage": "Task timed out"}).encode(), True
        elapsed = time.monotonic() - started
        body = None
        if os.path.exists(result_path):
            with open(result_path, "rb") as fh:
                body = fh.read()
        failed = proc.returncode != 0
        logger.info(
            "%s request_id=%s fim exit=%s duracao=%.2fs", fn.name, request_id, proc.returncode, elapsed
        )
        return body, failed


def child_main(args: argparse.Namespace) -> int:
    """Modo subprocesso: importa e chama o handler real."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    with open(args.event_file, "rb") as fh:
        raw = fh.read()
    event = json.loads(raw) if raw.strip() else {}

    deadline = time.monotonic() + args.timeout

    class _Context:
        function_name = args.function_name
        function_version = "$LATEST"
        aws_request_id = args.request_id
        memory_limit_in_mb = "0"
        invoked_function_arn = f"arn:aws:lambda:local:000000000000:function:{args.function_name}"

        @staticmethod
        def get_remaining_time_in_millis() -> int:
            return max(0, int((deadline - time.monotonic()) * 1000))

    module_name, _, attr = args.run.rpartition(".")
    exit_code = 0
    try:
        handler = getattr(importlib.import_module(module_name), attr)
        result: Any = handler(event, _Context())
        out = json.dumps(result, default=str).encode("utf-8")
    except Exception as exc:  # noqa: BLE001 - mesma semantica de "Unhandled" da Lambda
        logging.getLogger("lambda-local").exception("Handler %s levantou excecao", args.run)
        out = json.dumps({"errorType": type(exc).__name__, "errorMessage": str(exc)}).encode("utf-8")
        exit_code = 1
    with open(args.result_file, "wb") as fh:
        fh.write(out)
    return exit_code


# ---------------------------------------------------------------------------
# Servidor HTTP (protocolo Invoke do boto3)
# ---------------------------------------------------------------------------


def make_handler(functions: dict[str, FunctionConfig]):
    class InvokeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *fargs: Any) -> None:  # silencia o log padrao do http.server
            logger.debug(fmt, *fargs)

        def _send(self, status: int, body: bytes = b"", headers: Optional[dict[str, str]] = None) -> None:
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _error(self, status: int, error_type: str, message: str) -> None:
            body = json.dumps({"Type": "User", "Message": message, "message": message}).encode()
            self._send(status, body, {"x-amzn-ErrorType": error_type})

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, json.dumps({"status": "ok", "functions": sorted(functions)}).encode())
                return
            self._error(404, "ResourceNotFoundException", f"Rota nao suportada: {self.path}")

        def do_POST(self) -> None:  # noqa: N802
            match = _INVOKE_PATH.match(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else b""
            if not match:
                self._error(404, "ResourceNotFoundException", f"Rota nao suportada: {self.path}")
                return

            # FunctionName pode vir como nome, ARN parcial ou ARN completo.
            requested = match.group("name").split(":function:")[-1].split(":")[0]
            fn = functions.get(requested)
            if fn is None:
                self._error(
                    404,
                    "ResourceNotFoundException",
                    f"Function not found: {requested} (lambda-local conhece: {', '.join(sorted(functions))})",
                )
                return

            invocation_type = self.headers.get("X-Amz-Invocation-Type", "RequestResponse")
            request_id = str(uuid.uuid4())
            common = {"x-amzn-RequestId": request_id}

            if invocation_type == "DryRun":
                self._send(204, b"", common)
                return

            if invocation_type == "Event":
                logger.info("%s request_id=%s Event recebido (%d bytes)", fn.name, request_id, len(payload))
                threading.Thread(
                    target=run_invocation, args=(fn, payload, request_id), name=f"invoke-{request_id}", daemon=True
                ).start()
                self._send(202, b"", common)
                return

            if invocation_type != "RequestResponse":
                self._error(400, "InvalidParameterValueException", f"InvocationType invalido: {invocation_type}")
                return

            logger.info("%s request_id=%s RequestResponse recebido", fn.name, request_id)
            body, failed = run_invocation(fn, payload, request_id)
            headers = dict(common, **{"X-Amz-Executed-Version": "$LATEST"})
            if failed:
                headers["X-Amz-Function-Error"] = "Unhandled"
            self._send(200, body or b"null", headers)

    return InvokeHandler


def server_main(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    refuse_inside_real_lambda()
    functions = parse_functions(os.environ.get("LAMBDA_LOCAL_FUNCTIONS", ""))
    if not functions:
        raise SystemExit("lambda-local: LAMBDA_LOCAL_FUNCTIONS vazio - nenhuma funcao para servir.")
    for fn in functions.values():
        logger.info("funcao %s -> %s (timeout %ss)", fn.name, fn.handler, fn.timeout)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(functions))
    server.daemon_threads = True
    logger.info("lambda-local escutando em :%s", args.port)
    server.serve_forever()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Emulador local da API Invoke do AWS Lambda (somente dev).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAMBDA_LOCAL_PORT", "9001")))
    parser.add_argument("--run", help=argparse.SUPPRESS)
    parser.add_argument("--function-name", help=argparse.SUPPRESS)
    parser.add_argument("--request-id", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT_SECONDS, help=argparse.SUPPRESS)
    parser.add_argument("--event-file", help=argparse.SUPPRESS)
    parser.add_argument("--result-file", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.run:
        return child_main(args)
    return server_main(args)


if __name__ == "__main__":
    sys.exit(main())
