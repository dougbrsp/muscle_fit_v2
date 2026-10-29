"""Armazenamento dos cards (`app/exercise_card_storage.py`) e URL publica
(`app/public_assets.py`).

S3 sempre falso (`tests/support/fake_s3.py`), exceto nos testes de presign
com o client REAL do boto3: assinar uma URL e calculo local (sem rede), e e
justamente o que precisa provar que o endpoint de DEV
(`S3_PRESIGN_ENDPOINT_URL`) muda o host e que, sem ele, a URL sai para o S3
da AWS.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_storage.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app import exercise_card_storage as st
from app import public_assets
from tests.support.fake_s3 import FakeS3

REPO = Path(__file__).resolve().parents[3]
BUCKET = "musclefit-uploads"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", BUCKET)
    for name in ("PUBLIC_ASSETS_BASE_URL", "S3_PRESIGN_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Chaves
# ---------------------------------------------------------------------------


def test_chave_do_rascunho():
    assert st.draft_key(7) == "exercise-card-drafts/7/preview.png"
    assert st.draft_key("7") == "exercise-card-drafts/7/preview.png"


def test_chave_da_ilustracao_deriva_do_gid():
    """Estrategia B: a ilustracao crua da OpenAI fica ao lado do preview
    (card composto), sem coluna nova - a chave sai so do generation_id."""
    assert st.illustration_key(7) == "exercise-card-drafts/7/illustration.png"
    assert st.draft_key(7, st.DRAFT_ILLUSTRATION) == st.illustration_key(7)
    assert st.illustration_key(7).startswith(st.DRAFT_PREFIX + "/")


@pytest.mark.parametrize("nome", ["card.png", "../preview.png", "preview.png/../x", ""])
def test_arquivo_de_rascunho_fora_da_lista_e_recusado(nome):
    with pytest.raises(ValueError):
        st.draft_key(7, nome)
    with pytest.raises(ValueError):
        st.put_draft(7, b"x", filename=nome, client=FakeS3())


def test_put_draft_grava_ilustracao_e_preview_lado_a_lado():
    s3 = FakeS3()
    ilustracao = st.put_draft(9, b"crua", filename=st.DRAFT_ILLUSTRATION, client=s3)
    preview = st.put_draft(9, b"composto", client=s3)
    assert (ilustracao, preview) == ("exercise-card-drafts/9/illustration.png", "exercise-card-drafts/9/preview.png")
    for key in (ilustracao, preview):
        obj = s3.objects[(BUCKET, key)]
        assert obj.content_type == "image/png" and obj.cache_control == st.DRAFT_CACHE_CONTROL
    assert st.read_draft(ilustracao, client=s3) == b"crua"
    assert st.read_draft(preview, client=s3) == b"composto"


def test_chaves_da_versao_publicada():
    keys = st.published_keys(42, 3)
    assert keys == st.PublishedKeys(
        card_webp="exercise-cards/42/v3/card.webp",
        card_png="exercise-cards/42/v3/card.png",
        thumb_webp="exercise-cards/42/v3/thumb.webp",
    )


@pytest.mark.parametrize("fn, args", [(st.draft_key, (0,)), (st.draft_key, (-1,)),
                                      (st.published_keys, (0, 1)), (st.published_keys, (1, 0))])
def test_ids_invalidos_nao_viram_chave(fn, args):
    with pytest.raises(ValueError):
        fn(*args)


def test_rascunho_fora_do_prefixo_publicado_pela_cdn():
    """A CDN/bucket policy libera so `exercise-cards/*`: o rascunho (pago e
    nao aprovado) nao pode morar debaixo desse prefixo."""
    assert not st.draft_key(1).startswith(st.PUBLISHED_PREFIX + "/")
    assert st.published_keys(1, 1).card_webp.startswith("exercise-cards/")


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def test_publicacao_grava_os_tres_arquivos_imutaveis_com_content_type():
    s3 = FakeS3()
    keys = st.put_published(42, 1, card_webp=b"webp", card_png=b"png", thumb_webp=b"thumb", client=s3)
    assert s3.keys(BUCKET) == sorted([keys.card_webp, keys.card_png, keys.thumb_webp])
    esperado = {
        keys.card_webp: ("image/webp", b"webp"),
        keys.card_png: ("image/png", b"png"),
        keys.thumb_webp: ("image/webp", b"thumb"),
    }
    for key, (content_type, body) in esperado.items():
        obj = s3.objects[(BUCKET, key)]
        assert obj.content_type == content_type
        assert obj.body == body
        assert obj.cache_control == "public, max-age=31536000, immutable"
    assert st.IMMUTABLE_CACHE_CONTROL == "public, max-age=31536000, immutable"


def test_rascunho_sem_cache_publico():
    s3 = FakeS3()
    key = st.put_draft(9, b"\x89PNG...", client=s3)
    obj = s3.objects[(BUCKET, key)]
    assert key == "exercise-card-drafts/9/preview.png"
    assert obj.content_type == "image/png"
    assert "public" not in obj.cache_control
    assert "no-store" in obj.cache_control


def test_falha_do_s3_vira_erro_tipado_s3_upload_failed():
    s3 = FakeS3()
    s3.fail_put_keys.add("exercise-cards/42/v1/card.png")
    with pytest.raises(st.ExerciseCardStorageError) as exc:
        st.put_published(42, 1, card_webp=b"a", card_png=b"b", thumb_webp=b"c", client=s3)
    assert exc.value.code == "S3_UPLOAD_FAILED"


def test_corpo_vazio_e_recusado_antes_do_s3():
    s3 = FakeS3()
    with pytest.raises(st.ExerciseCardStorageError):
        st.put_draft(1, b"", client=s3)
    assert s3.calls == []


def test_bucket_vem_do_ambiente(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", "outro-bucket")
    st.put_draft(1, b"x", client=s3)
    assert s3.keys("outro-bucket") == ["exercise-card-drafts/1/preview.png"]


def test_sem_bucket_configurado_e_storage_unavailable(monkeypatch):
    monkeypatch.delenv("EQUIPMENT_PHOTOS_BUCKET")
    with pytest.raises(st.ExerciseCardStorageError) as exc:
        st.put_draft(1, b"x", client=FakeS3())
    assert exc.value.code == "STORAGE_UNAVAILABLE"


def test_le_o_rascunho_de_volta():
    s3 = FakeS3()
    key = st.put_draft(5, b"bytes-do-png", client=s3)
    assert st.read_draft(key, client=s3) == b"bytes-do-png"


def test_leitura_so_na_area_de_rascunho_e_falha_tipada():
    s3 = FakeS3()
    with pytest.raises(ValueError):
        st.read_draft("exercise-cards/1/v1/card.png", client=s3)
    s3.fail_get_keys.add(st.draft_key(500))
    with pytest.raises(st.ExerciseCardStorageError) as exc:
        st.read_draft(st.draft_key(500), client=s3)
    assert exc.value.code == "S3_READ_FAILED"


def test_rascunho_inexistente_e_draft_expired():
    """M72: o lifecycle de 7 dias apagou o objeto -> codigo proprio."""
    with pytest.raises(st.ExerciseCardStorageError) as exc:
        st.read_draft(st.draft_key(404), client=FakeS3())
    assert exc.value.code == st.DRAFT_EXPIRED == "DRAFT_EXPIRED"


@pytest.mark.parametrize(
    ("code", "esperado"),
    [("NoSuchKey", "DRAFT_EXPIRED"), ("404", "DRAFT_EXPIRED"), ("NotFound", "DRAFT_EXPIRED"),
     ("AccessDenied", "S3_READ_FAILED"), ("SlowDown", "S3_READ_FAILED"), ("InternalError", "S3_READ_FAILED")],
)
def test_client_error_real_do_botocore_distingue_inexistente(code, esperado):
    from botocore.exceptions import ClientError

    class _Client:
        def get_object(self, **_):
            raise ClientError({"Error": {"Code": code, "Message": "x"}}, "GetObject")

    with pytest.raises(st.ExerciseCardStorageError) as exc:
        st.read_draft(st.draft_key(1), client=_Client(), bucket="b")
    assert exc.value.code == esperado


def test_erro_sem_response_e_s3_read_failed():
    class _Client:
        def get_object(self, **_):
            raise ConnectionError("rede")

    with pytest.raises(st.ExerciseCardStorageError) as exc:
        st.read_draft(st.draft_key(1), client=_Client(), bucket="b")
    assert exc.value.code == "S3_READ_FAILED"


# ---------------------------------------------------------------------------
# Presign
# ---------------------------------------------------------------------------


def test_presign_de_15_minutos_com_expiracao_informada():
    s3 = FakeS3(endpoint_url="http://localhost:4566")
    agora = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
    signed = st.presign_draft_url(st.draft_key(3), client=s3, now=agora)
    assert signed.url == "http://localhost:4566/musclefit-uploads/exercise-card-drafts/3/preview.png?X-Fake-Expires=900"
    assert signed.expires_at == datetime(2026, 10, 3, 12, 15, 0, tzinfo=timezone.utc)
    (chamada,) = s3.calls_of("generate_presigned_url")
    assert chamada["ClientMethod"] == "get_object" and chamada["ExpiresIn"] == 900
    assert st.PRESIGN_EXPIRES_SECONDS == 15 * 60


def test_presign_so_para_rascunho():
    with pytest.raises(ValueError):
        st.presign_draft_url("exercise-cards/1/v1/card.webp", client=FakeS3())


@pytest.fixture
def aws_offline(monkeypatch, tmp_path):
    """Credenciais falsas e nenhum arquivo/endpoint do ambiente: o client
    real do boto3 so assina, nao fala com ninguem."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXEMPLO")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "segredo-de-teste")
    monkeypatch.setenv("AWS_REGION", "sa-east-1")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "nao-existe-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "nao-existe-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)


def test_presign_real_em_dev_assina_para_o_host_que_o_navegador_alcanca(aws_offline, monkeypatch):
    # Como no compose: o client padrao fala com `localstack:4566` (rede do
    # Docker), mas a URL vai para o navegador.
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localstack:4566")
    monkeypatch.setenv("S3_PRESIGN_ENDPOINT_URL", "http://localhost:4566")
    signed = st.presign_draft_url(st.draft_key(3))
    url = urlparse(signed.url)
    assert (url.scheme, url.netloc) == ("http", "localhost:4566")
    assert url.path == "/musclefit-uploads/exercise-card-drafts/3/preview.png"  # path-style
    query = parse_qs(url.query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["900"]


def test_presign_real_em_producao_vai_para_o_s3_da_aws(aws_offline):
    signed = st.presign_draft_url(st.draft_key(3))
    url = urlparse(signed.url)
    assert url.scheme == "https"
    assert url.netloc.endswith("amazonaws.com")
    assert "localhost" not in signed.url and "localstack" not in signed.url
    assert "exercise-card-drafts/3/preview.png" in url.path
    query = parse_qs(url.query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["900"]
    assert "sa-east-1" in query["X-Amz-Credential"][0]


def test_endpoint_de_presign_de_dev_nao_existe_na_infra():
    """`S3_PRESIGN_ENDPOINT_URL` e SO de DEV: nenhum `.tf` pode defini-lo
    (regra anti-vazamento dev->prod)."""
    tfs = list((REPO / "infra").rglob("*.tf"))
    assert tfs, "diretorio infra/ sem .tf - caminho do repo mudou?"
    for tf in tfs:
        assert "S3_PRESIGN_ENDPOINT_URL" not in tf.read_text(encoding="utf-8", errors="ignore"), tf.name


# ---------------------------------------------------------------------------
# URL publica
# ---------------------------------------------------------------------------


def test_base_url_padrao_e_a_cdn_de_producao():
    assert public_assets.base_url() == "https://app.musclefit.com.br"
    assert public_assets.DEFAULT_BASE_URL == "https://app.musclefit.com.br"


@pytest.mark.parametrize(
    "valor, esperado",
    [
        ("http://localhost:4566/musclefit-uploads", "http://localhost:4566/musclefit-uploads"),
        ("http://localhost:4566/musclefit-uploads/", "http://localhost:4566/musclefit-uploads"),
        ("  https://cdn.exemplo.com//  ", "https://cdn.exemplo.com"),
        ("   ", "https://app.musclefit.com.br"),
    ],
)
def test_base_url_do_ambiente_sem_barra_final(monkeypatch, valor, esperado):
    monkeypatch.setenv("PUBLIC_ASSETS_BASE_URL", valor)
    assert public_assets.base_url() == esperado


def test_url_for_monta_sem_barra_dupla(monkeypatch):
    assert public_assets.url_for("exercise-cards/1/v1/card.webp") == (
        "https://app.musclefit.com.br/exercise-cards/1/v1/card.webp"
    )
    monkeypatch.setenv("PUBLIC_ASSETS_BASE_URL", "http://localhost:4566/musclefit-uploads/")
    assert public_assets.url_for("/exercise-cards/1/v1/card.webp") == (
        "http://localhost:4566/musclefit-uploads/exercise-cards/1/v1/card.webp"
    )


@pytest.mark.parametrize("key", ["", "   ", "/", None])
def test_url_for_recusa_key_vazia(key):
    with pytest.raises(ValueError):
        public_assets.url_for(key)


def test_urls_publicas_da_versao(monkeypatch):
    keys = st.published_keys(42, 2)
    assert st.public_urls(keys) == {
        "url": "https://app.musclefit.com.br/exercise-cards/42/v2/card.webp",
        "png_url": "https://app.musclefit.com.br/exercise-cards/42/v2/card.png",
        "thumbnail_url": "https://app.musclefit.com.br/exercise-cards/42/v2/thumb.webp",
    }
    monkeypatch.setenv("PUBLIC_ASSETS_BASE_URL", "http://localhost:4566/musclefit-uploads")
    assert st.public_urls(keys)["url"] == "http://localhost:4566/musclefit-uploads/exercise-cards/42/v2/card.webp"


# ---------------------------------------------------------------------------
# Resumo do card publicado para o Modo Treino (PR5)
# ---------------------------------------------------------------------------


def test_resumo_do_card_para_o_modo_treino(monkeypatch):
    monkeypatch.delenv("PUBLIC_ASSETS_BASE_URL", raising=False)
    assert st.training_card(42, 3) == {
        "url": "https://app.musclefit.com.br/exercise-cards/42/v3/card.webp",
        "thumbnail_url": "https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp",
        "version": 3,
    }
    # Mesmo helper de URL do resto do modulo: a base vem do ambiente.
    monkeypatch.setenv("PUBLIC_ASSETS_BASE_URL", "http://localhost:4566/musclefit-uploads")
    assert st.training_card(42, 3)["thumbnail_url"] == (
        "http://localhost:4566/musclefit-uploads/exercise-cards/42/v3/thumb.webp"
    )


@pytest.mark.parametrize(
    "exercise_id, version",
    [(42, None), (None, 1), (None, None), (42, 0), (0, 1), (42, "x"), (42, -2)],
)
def test_sem_ponteiro_valido_nao_ha_card_e_nunca_levanta(exercise_id, version):
    """`None` = a UI nao mostra nada. Valor torto tambem vira `None`: a
    leitura da ficha no meio do treino nao pode quebrar por causa do card."""
    assert st.training_card(exercise_id, version) is None
