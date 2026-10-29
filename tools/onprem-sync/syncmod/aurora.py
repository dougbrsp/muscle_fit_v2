"""Cliente do Aurora MySQL via RDS Data API (boto3 `rds-data`).

Por que Data API e nao conexao TCP: o cluster `musclefit-db` vive em subnets
privadas sem NAT/IGW, nao e publico e nao ha bastion/VPN/Lambda na VPC.
Conexao direta da maquina do desenvolvedor e impossivel. Ver
`spikes/data-api-shim/rds_data_shim.py` e `docs/spike-data-api.md`.

Pontos delicados tratados aqui:

1. **Estado de sessao so existe dentro de transacao.** Cada
   `execute_statement` solto pode cair em uma conexao diferente do pool do
   Data API, entao `SET time_zone` / `SET FOREIGN_KEY_CHECKS` evaporam. Por
   isso TODA operacao (inclusive leitura) roda dentro de uma transacao, com
   um prelude de sessao, e o prelude e VERIFICADO lendo as variaveis de volta.
2. **Limite de ~1 MB por chamada**, de ida e de volta. Leitura e paginada com
   tamanho de pagina derivado do tamanho real medido das linhas; escrita usa
   lotes dimensionados por bytes, nao por contagem fixa.
3. **Aurora Serverless v2 com min 0.5 ACU** pode responder devagar na
   primeira chamada. Ha retry com backoff para erros transitorios.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    NoRegionError,
)

from .config import AuroraConfig, BatchConfig
from .schema import Table


class AuroraError(RuntimeError):
    pass


class AuroraCredentialsError(AuroraError):
    """Sessao AWS ausente ou expirada.

    Separado de `AuroraError` porque nao e falha da ferramenta nem do banco:
    e o operador que precisa autenticar. A CLI usa isso para dar uma
    orientacao util em vez de um traceback de botocore.
    """


_CREDENTIAL_MARKERS = (
    "expiredtoken",
    "invalidclienttokenid",
    "unrecognizedclient",
    "requestexpired",
    "accessdenied",
    "sso session",
    "token has expired",
)


def _raise_if_credentials(exc: Exception) -> None:
    if isinstance(exc, (NoCredentialsError, NoRegionError)):
        raise AuroraCredentialsError(str(exc)) from exc
    if type(exc).__name__ == "MissingDependencyException" and "crt" in str(exc).lower():
        # Perfil que guarda a sessao via `login_session` exige botocore[crt].
        # Nao instalamos nada: o perfil `musclefit-tf` le a MESMA sessao por
        # `credential_process` (delegando ao AWS CLI, que ja traz o crt).
        raise AuroraCredentialsError(
            "O perfil escolhido usa o provider `login_session`, que exige a dependencia "
            'extra botocore[crt], ausente neste Python.\n'
            "  Solucao sem instalar nada: use o perfil que le a mesma sessao via "
            "credential_process,\n"
            "  por exemplo `--aws-profile musclefit-tf`.\n"
            f"  (erro original: {exc})"
        ) from exc
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        message = str(exc).lower()
        if any(m in code or m in message for m in _CREDENTIAL_MARKERS):
            raise AuroraCredentialsError(str(exc)) from exc
    # Erros de resolucao de perfil/SSO chegam como subclasses variadas de
    # BotoCoreError cujo nome sempre menciona credencial ou token.
    if isinstance(exc, BotoCoreError) and any(
        word in type(exc).__name__.lower() for word in ("credential", "token", "profile")
    ):
        raise AuroraCredentialsError(str(exc)) from exc


_TRANSIENT_MARKERS = (
    "communications link failure",
    "connection is not available",
    "is resuming after being auto-paused",
    "is currently unavailable",
    "throttl",
    "too many connections",
)

_SIZE_MARKERS = (
    "exceeded size limit",
    "result set too large",
    "response size",
)


def _message(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Message", "")) + " " + str(exc)


def build_session(cfg: AuroraConfig) -> boto3.Session:
    """Sessao boto3 honrando o perfil nomeado.

    Ordem de resolucao: `aurora.profile` do config > AWS_PROFILE do ambiente >
    sessao padrao. Como este ambiente NAO tem perfil `default`, cair na sessao
    padrao quase sempre significa "Unable to locate credentials" mesmo com o
    login perfeitamente valido em `musclefit`.
    """
    if cfg.profile:
        return boto3.Session(profile_name=cfg.profile, region_name=cfg.region)
    return boto3.Session(region_name=cfg.region)


def available_profiles() -> list[str]:
    """Perfis configurados, para diagnostico na mensagem de erro."""
    try:
        return sorted(boto3.Session().available_profiles)
    except Exception:  # noqa: BLE001 - diagnostico best-effort, nunca deve falhar
        return []


def resolve_secret_arn(cfg: AuroraConfig, session: boto3.Session | None = None) -> str:
    """Descobre o secret gerenciado pelo RDS, se nao vier fixado na config."""
    if cfg.secret_arn:
        return cfg.secret_arn
    session = session or build_session(cfg)
    try:
        rds = session.client("rds", region_name=cfg.region)
        resp = rds.describe_db_clusters(DBClusterIdentifier=cfg.cluster_identifier)
    except Exception as exc:
        _raise_if_credentials(exc)
        raise AuroraError(f"Falha ao descrever o cluster {cfg.cluster_identifier}: {exc}") from exc
    clusters = resp.get("DBClusters") or []
    if not clusters:
        raise AuroraError(f"Cluster {cfg.cluster_identifier} nao encontrado em {cfg.region}.")
    secret = (clusters[0].get("MasterUserSecret") or {}).get("SecretArn")
    if not secret:
        raise AuroraError(
            "Cluster nao expoe MasterUserSecret. Preencha `aurora.secret_arn` em sync_config.json."
        )
    return secret


class AuroraClient:
    def __init__(
        self,
        cfg: AuroraConfig,
        batch_cfg: BatchConfig,
        time_zone: str = "+00:00",
        max_retries: int = 5,
    ) -> None:
        self.cfg = cfg
        self.batch_cfg = batch_cfg
        self.time_zone = time_zone
        self.max_retries = max_retries
        try:
            self.session = build_session(cfg)
        except Exception as exc:  # perfil inexistente / config quebrada
            _raise_if_credentials(exc)
            raise AuroraCredentialsError(
                f"Nao foi possivel abrir a sessao AWS com o perfil {cfg.profile!r}: {exc}"
            ) from exc
        self.client = self.session.client("rds-data", region_name=cfg.region)
        self.secret_arn = resolve_secret_arn(cfg, self.session)
        self.transaction_id: str | None = None
        self._fk_checks_disabled = False

    def caller_identity(self) -> str:
        """Quem a ferramenta esta usando para escrever. Vale imprimir sempre.

        Num banco de producao, saber com qual identidade se esta conectado
        antes de confirmar o apply e tao importante quanto saber em qual
        cluster.
        """
        try:
            sts = self.session.client("sts", region_name=self.cfg.region)
            ident = sts.get_caller_identity()
            return f"{ident.get('Arn')} (conta {ident.get('Account')})"
        except Exception as exc:  # noqa: BLE001
            _raise_if_credentials(exc)
            return "(nao foi possivel obter)"

    # -- baixo nivel -------------------------------------------------------

    def _call(self, fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        delay = 1.0
        last: ClientError | None = None
        for attempt in range(self.max_retries):
            try:
                return fn(**kwargs)
            except ClientError as exc:
                _raise_if_credentials(exc)
                msg = _message(exc).lower()
                if any(m in msg for m in _SIZE_MARKERS):
                    raise  # tratado pelo chamador (reduz pagina/lote)
                if not any(m in msg for m in _TRANSIENT_MARKERS) or attempt == self.max_retries - 1:
                    raise
                last = exc
                time.sleep(delay)
                delay = min(delay * 2, 20.0)
        raise AuroraError(f"Falha apos {self.max_retries} tentativas: {last}")

    def execute(
        self,
        sql: str,
        parameters: Sequence[dict[str, Any]] | None = None,
        include_metadata: bool = True,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "resourceArn": self.cfg.cluster_arn,
            "secretArn": self.secret_arn,
            "database": self.cfg.database,
            "sql": sql,
            "includeResultMetadata": include_metadata,
        }
        if parameters:
            kwargs["parameters"] = list(parameters)
        if self.transaction_id:
            kwargs["transactionId"] = self.transaction_id
        try:
            return self._call(self.client.execute_statement, **kwargs)
        except ClientError as exc:
            raise AuroraError(f"execute_statement falhou.\nSQL: {sql[:400]}\nErro: {_message(exc)}") from exc

    def batch(self, sql: str, parameter_sets: Sequence[Sequence[dict[str, Any]]]) -> dict[str, Any]:
        if not parameter_sets:
            return {}
        kwargs: dict[str, Any] = {
            "resourceArn": self.cfg.cluster_arn,
            "secretArn": self.secret_arn,
            "database": self.cfg.database,
            "sql": sql,
            "parameterSets": [list(ps) for ps in parameter_sets],
        }
        if self.transaction_id:
            kwargs["transactionId"] = self.transaction_id
        try:
            return self._call(self.client.batch_execute_statement, **kwargs)
        except ClientError as exc:
            raise AuroraError(
                f"batch_execute_statement falhou ({len(parameter_sets)} linhas).\n"
                f"SQL: {sql[:400]}\nErro: {_message(exc)}"
            ) from exc

    # -- transacao ---------------------------------------------------------

    def begin(self, disable_fk_checks: bool = False) -> None:
        if self.transaction_id:
            raise AuroraError("Ja existe uma transacao aberta.")
        resp = self._call(
            self.client.begin_transaction,
            resourceArn=self.cfg.cluster_arn,
            secretArn=self.secret_arn,
            database=self.cfg.database,
        )
        self.transaction_id = resp["transactionId"]
        self._apply_session_prelude(disable_fk_checks)

    def _apply_session_prelude(self, disable_fk_checks: bool) -> None:
        self.execute(f"SET SESSION time_zone = '{self.time_zone}'", include_metadata=False)
        if disable_fk_checks:
            self.execute("SET SESSION FOREIGN_KEY_CHECKS = 0", include_metadata=False)
            self._fk_checks_disabled = True

        # Verificacao explicita: se o Data API tivesse trocado de conexao por
        # baixo dos panos, o SET acima teria sido perdido em silencio e o sync
        # gravaria TIMESTAMPs no fuso errado. Melhor abortar aqui.
        rows = self.fetch_all(
            "SELECT @@SESSION.time_zone AS tz, CAST(@@SESSION.foreign_key_checks AS CHAR) AS fk"
        )
        if not rows:
            raise AuroraError("Nao foi possivel validar o estado de sessao do Data API.")
        got_tz = rows[0]["tz"]
        got_fk = str(rows[0]["fk"])
        if got_tz != self.time_zone:
            raise AuroraError(
                f"time_zone da sessao Aurora ficou '{got_tz}', esperado '{self.time_zone}'. Abortando."
            )
        if disable_fk_checks and got_fk != "0":
            raise AuroraError(
                f"FOREIGN_KEY_CHECKS ficou '{got_fk}', esperado '0'. Abortando."
            )

    def restore_fk_checks(self) -> None:
        if self._fk_checks_disabled:
            self.execute("SET SESSION FOREIGN_KEY_CHECKS = 1", include_metadata=False)
            rows = self.fetch_all("SELECT CAST(@@SESSION.foreign_key_checks AS CHAR) AS fk")
            if not rows or str(rows[0]["fk"]) != "1":
                raise AuroraError("Nao foi possivel reativar FOREIGN_KEY_CHECKS. Abortando.")
            self._fk_checks_disabled = False

    def commit(self) -> None:
        if not self.transaction_id:
            return
        tx = self.transaction_id
        self.transaction_id = None
        self._call(
            self.client.commit_transaction,
            resourceArn=self.cfg.cluster_arn,
            secretArn=self.secret_arn,
            transactionId=tx,
        )

    def rollback(self) -> None:
        if not self.transaction_id:
            return
        tx = self.transaction_id
        self.transaction_id = None
        try:
            self._call(
                self.client.rollback_transaction,
                resourceArn=self.cfg.cluster_arn,
                secretArn=self.secret_arn,
                transactionId=tx,
            )
        except ClientError:
            pass  # transacao ja encerrada pelo servidor; nada a fazer

    def __enter__(self) -> "AuroraClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()

    # -- leitura -----------------------------------------------------------

    @staticmethod
    def _decode_field(field: dict[str, Any]) -> str | None:
        if field.get("isNull"):
            return None
        for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
            if key in field:
                value = field[key]
                return value if isinstance(value, str) else str(value)
        if "blobValue" in field:
            return field["blobValue"].decode("utf-8", errors="replace")
        return None

    def fetch_all(self, sql: str, parameters: Sequence[dict[str, Any]] | None = None) -> list[dict[str, str | None]]:
        resp = self.execute(sql, parameters)
        columns = [c.get("label") or c.get("name") for c in (resp.get("columnMetadata") or [])]
        return [
            {col: self._decode_field(val) for col, val in zip(columns, record)}
            for record in (resp.get("records") or [])
        ]

    def _fetch_pk_list(self, table: Table) -> list[tuple[str | None, ...]]:
        pk_select = ", ".join(
            f"{table.column(c).select_expr()} AS `{c}`" for c in table.pk_columns
        )
        rows = self.fetch_all(
            f"SELECT {pk_select} FROM `{table.name}` ORDER BY {table.order_by()}"
        )
        return [tuple(r[c] for c in table.pk_columns) for r in rows]

    def fetch_table(self, table: Table, page_hint: int | None = None) -> list[dict[str, str | None]]:
        """Le a tabela inteira na forma canonica, em duas fases.

        Fase 1: le somente as PKs, ordenadas. Fase 2: busca as linhas completas
        em lotes, filtrando por essas PKs, SEM `ORDER BY`.

        Por que nao o obvio `ORDER BY pk LIMIT n OFFSET m` na tabela inteira:
        a lista de selecao converte cada coluna com `CAST(... AS CHAR)`, o que
        impede a leitura index-only e forca um filesort carregando as colunas
        largas no sort buffer. Em `reports` (`context_snapshot` de ~316 KB por
        linha) isso estoura com "Out of sort memory" - erro observado de fato
        no auto-teste. Ordenar apenas as PKs (colunas numericas estreitas) e
        depois filtrar por `IN` elimina o problema e ainda evita o custo
        quadratico de OFFSET.

        `page_hint` vem do tamanho REAL medido no onprem (ver
        `planner.suggest_page_size`), nao de uma contagem fixa.
        """
        if not table.has_pk:
            raise AuroraError(f"Tabela {table.name} sem PK: sync nao suportado.")

        pks = self._fetch_pk_list(table)
        if not pks:
            return []

        page = max(1, min(page_hint or self.batch_cfg.max_rows_per_read_page,
                          self.batch_cfg.max_rows_per_read_page))
        select_list = table.select_list()
        pk_cols = table.pk_columns
        rows: list[dict[str, str | None]] = []
        index = 0

        while index < len(pks):
            chunk_pks = pks[index : index + page]
            params: list[dict[str, Any]] = []
            tuples: list[str] = []
            for i, pk in enumerate(chunk_pks):
                names = []
                for col, value in zip(pk_cols, pk):
                    name = f"k{i}_{col}"
                    params.append(table.column(col).data_api_param(name, value))
                    names.append(f":{name}")
                tuples.append(f"({', '.join(names)})")
            row_ctor = "(" + ", ".join(f"`{c}`" for c in pk_cols) + ")"
            sql = f"SELECT {select_list} FROM `{table.name}` WHERE {row_ctor} IN ({', '.join(tuples)})"

            try:
                rows.extend(self.fetch_all(sql, params))
            except AuroraError as exc:
                if any(m in str(exc).lower() for m in _SIZE_MARKERS) and page > 1:
                    page = max(1, page // 2)
                    continue
                raise
            index += len(chunk_pks)

        return rows

    def count_rows(self, tables: Iterable[str]) -> dict[str, int]:
        tables = list(tables)
        if not tables:
            return {}
        out: dict[str, int] = {}
        # UNION ALL em blocos: uma unica chamada para 45 tabelas continua
        # pequena, mas fatiar evita SQL absurdamente longo se o schema crescer.
        for i in range(0, len(tables), 25):
            chunk = tables[i : i + 25]
            union = " UNION ALL ".join(
                f"SELECT '{t}' AS t, CAST(COUNT(*) AS CHAR) AS n FROM `{t}`" for t in chunk
            )
            for row in self.fetch_all(union):
                out[str(row["t"])] = int(row["n"] or 0)
        return out

    def text_byte_stats(self, table: Table) -> dict[str, dict[str, int]]:
        text_cols = [c for c in table.columns if c.is_string or c.is_json]
        if not text_cols:
            return {}
        select_list = ", ".join(
            f"CAST(IFNULL(SUM(OCTET_LENGTH(`{c.name}`)), 0) AS CHAR) AS `{c.name}__bytes`, "
            f"CAST(IFNULL(SUM(CHAR_LENGTH(`{c.name}`)), 0) AS CHAR) AS `{c.name}__chars`"
            for c in text_cols
        )
        rows = self.fetch_all(f"SELECT {select_list} FROM `{table.name}`")
        flat = rows[0] if rows else {}
        return {
            c.name: {
                "bytes": int(flat.get(f"{c.name}__bytes") or 0),
                "chars": int(flat.get(f"{c.name}__chars") or 0),
            }
            for c in text_cols
        }

    def probe_hex(self, table: Table, pk: dict[str, str], column: str) -> dict[str, Any] | None:
        conditions = []
        params = []
        for i, (k, v) in enumerate(pk.items()):
            conditions.append(f"`{k}` = :pk{i}")
            params.append(table.column(k).data_api_param(f"pk{i}", str(v)))
        rows = self.fetch_all(
            f"SELECT HEX(`{column}`) AS hex, "
            f"CAST(OCTET_LENGTH(`{column}`) AS CHAR) AS bytes, "
            f"CAST(CHAR_LENGTH(`{column}`) AS CHAR) AS chars "
            f"FROM `{table.name}` WHERE {' AND '.join(conditions)} LIMIT 1",
            params,
        )
        return rows[0] if rows else None

    def list_tables(self) -> list[str]:
        rows = self.fetch_all(
            "SELECT TABLE_NAME AS t FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{self.cfg.database}' AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
        )
        return [str(r["t"]) for r in rows]

    def list_columns(self) -> dict[str, list[str]]:
        rows = self.fetch_all(
            # Coluna GERADA fica de fora: ela nao aceita INSERT (erro 3105) e
            # e derivada das outras, entao nao ha nada para copiar. A primeira
            # e `workout_plan_template_assignments.active_user_id` (migracao
            # 049/M23), que existe so para o UNIQUE de "um Modelo vigente por
            # usuario".
            "SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{self.cfg.database}' AND EXTRA NOT LIKE '%GENERATED%' "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(str(r["t"]), []).append(str(r["c"]))
        return out

    def table_collations(self) -> dict[str, str]:
        rows = self.fetch_all(
            "SELECT TABLE_NAME AS t, TABLE_COLLATION AS col FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{self.cfg.database}' AND TABLE_TYPE = 'BASE TABLE'"
        )
        return {str(r["t"]): str(r["col"] or "") for r in rows}
