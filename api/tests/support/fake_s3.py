"""S3 falso para testes unitarios e de integracao dos cards ilustrados (e de
qualquer outro codigo que receba um client S3 injetado).

Implementa so o subconjunto que o app usa, com a MESMA assinatura do client
do boto3 (argumentos nomeados em PascalCase):

    put_object(Bucket, Key, Body, ContentType=None, CacheControl=None, **extra)
    get_object(Bucket, Key)          -> {"Body": <stream>, "ContentType", ...}
    head_object(Bucket, Key)
    delete_object(Bucket, Key)
    list_objects_v2(Bucket, Prefix="")
    generate_presigned_url(ClientMethod, Params, ExpiresIn=3600, HttpMethod=None)

Grava cada objeto (corpo + headers) e cada chamada, para o teste afirmar
chave, `Cache-Control` e `ContentType`. O presign e deterministico:
`{endpoint_url}/{Bucket}/{Key}?X-Fake-Expires={ExpiresIn}`.

Falhas sob demanda: `fail_put_keys` (conjunto de keys cujo put levanta
`FakeS3Error`) e `fail_get_keys` (erro generico de leitura). Ler uma key que
NAO existe levanta `FakeS3NoSuchKey` (subclasse de `FakeS3Error`), com o
mesmo `response["Error"]["Code"]` do `ClientError` do botocore (`NoSuchKey`
no get, `404` no head) - e o que o lifecycle do S3 produz quando apaga um
rascunho.

Nada aqui fala com rede nem com o LocalStack.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote


class FakeS3Error(Exception):
    """Falha simulada (o equivalente ao `ClientError` do botocore)."""


class FakeS3NoSuchKey(FakeS3Error):
    """Objeto inexistente, com o `response` no formato do `ClientError`."""

    def __init__(self, message: str, *, code: str = "NoSuchKey"):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


@dataclass
class FakeObject:
    body: bytes
    content_type: Optional[str]
    cache_control: Optional[str]
    extra: dict[str, Any] = field(default_factory=dict)


class _Body(io.BytesIO):
    """`StreamingBody` minimo: `read()` + `close()` + context manager."""


class FakeS3:
    def __init__(self, *, endpoint_url: str = "https://fake-s3.local"):
        self.endpoint_url = endpoint_url.rstrip("/")
        self.objects: dict[tuple[str, str], FakeObject] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_put_keys: set[str] = set()
        self.fail_get_keys: set[str] = set()

    # -- escrita ------------------------------------------------------------

    def put_object(self, *, Bucket: str, Key: str, Body: Any, ContentType: Optional[str] = None,
                   CacheControl: Optional[str] = None, **extra: Any) -> dict[str, Any]:
        self.calls.append(("put_object", {"Bucket": Bucket, "Key": Key, "ContentType": ContentType,
                                          "CacheControl": CacheControl, **extra}))
        if Key in self.fail_put_keys:
            raise FakeS3Error(f"put_object falhou (simulado) para {Key}")
        body = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[(Bucket, Key)] = FakeObject(body, ContentType, CacheControl, dict(extra))
        return {"ETag": f'"{len(body)}"'}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(("delete_object", {"Bucket": Bucket, "Key": Key}))
        self.objects.pop((Bucket, Key), None)
        return {}

    # -- leitura ------------------------------------------------------------

    def _get(self, op: str, Bucket: str, Key: str) -> FakeObject:
        self.calls.append((op, {"Bucket": Bucket, "Key": Key}))
        if Key in self.fail_get_keys:
            raise FakeS3Error(f"{op} falhou (simulado) para {Key}")
        if (Bucket, Key) not in self.objects:
            code = "404" if op == "head_object" else "NoSuchKey"
            raise FakeS3NoSuchKey(f"{op}: {Key} nao existe", code=code)
        return self.objects[(Bucket, Key)]

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        obj = self._get("get_object", Bucket, Key)
        return {"Body": _Body(obj.body), "ContentType": obj.content_type, "CacheControl": obj.cache_control,
                "ContentLength": len(obj.body)}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        obj = self._get("head_object", Bucket, Key)
        return {"ContentType": obj.content_type, "CacheControl": obj.cache_control, "ContentLength": len(obj.body)}

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", **_: Any) -> dict[str, Any]:
        self.calls.append(("list_objects_v2", {"Bucket": Bucket, "Prefix": Prefix}))
        keys = sorted(key for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix))
        return {"KeyCount": len(keys), "Contents": [{"Key": key, "Size": len(self.objects[(Bucket, key)].body)}
                                                    for key in keys]}

    # -- presign ------------------------------------------------------------

    def generate_presigned_url(self, ClientMethod: str, Params: Optional[dict[str, Any]] = None,
                               ExpiresIn: int = 3600, HttpMethod: Optional[str] = None) -> str:
        params = dict(Params or {})
        self.calls.append(("generate_presigned_url", {"ClientMethod": ClientMethod, "Params": params,
                                                      "ExpiresIn": ExpiresIn}))
        return f"{self.endpoint_url}/{params['Bucket']}/{quote(params['Key'])}?X-Fake-Expires={int(ExpiresIn)}"

    # -- conveniencias de teste ----------------------------------------------

    def keys(self, bucket: Optional[str] = None) -> list[str]:
        return sorted(key for b, key in self.objects if bucket is None or b == bucket)

    def body(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)].body

    def calls_of(self, op: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == op]
