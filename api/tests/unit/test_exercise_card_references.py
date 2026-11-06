"""Fotos de referência dos cards (`app/exercise_card_references.py`): validação e
re-encode da imagem, limites, SQL no SQLite falso (que PROJETA só as colunas
pedidas pelo SELECT, como o banco real) e S3 falso.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_references.py
"""

from __future__ import annotations

import base64
import io
import struct
import zlib
from datetime import datetime, timezone

import pytest
from PIL import Image

from app import exercise_card_references as refs
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from tests.support.cards_sqlite import CardsSqlite
from tests.support.fake_s3 import FakeS3

BUCKET = "musclefit-uploads"
ADMIN = 7
T0 = datetime(2026, 11, 6, 12, 0, 0)


# ---------------------------------------------------------------------------
# Imagens sintéticas
# ---------------------------------------------------------------------------


def _save(image: Image.Image, fmt: str, **kwargs) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _png(w: int = 640, h: int = 480, color=(90, 120, 150), mode: str = "RGB") -> bytes:
    return _save(Image.new(mode, (w, h), color), "PNG")


def _jpeg_com_exif(w: int = 600, h: int = 400, orientation: int = 6) -> bytes:
    image = Image.new("RGB", (w, h), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    exif[0x010E] = "segredo-do-exif"  # ImageDescription
    exif[0x013B] = "autor-secreto"  # Artist
    return _save(image, "JPEG", exif=exif)


def _b64(raw: bytes, *, padding: bool = False) -> str:
    text = base64.b64encode(raw).decode("ascii")
    return text if padding else text.rstrip("=")


def _png_com_cabecalho(w: int, h: int) -> bytes:
    """PNG que SO tem assinatura + IHDR (sem pixels): serve para provar que a
    checagem de pixels vem do cabecalho, antes de decodificar."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    # Um IDAT minimo faz o Pillow abrir (ele le os chunks ate o primeiro IDAT).
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b""))


def _erro(fn, *args, **kwargs) -> svc.ExerciseCardError:
    with pytest.raises(svc.ExerciseCardError) as exc:
        fn(*args, **kwargs)
    return exc.value


def _aberta(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    image.load()
    return image


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


def test_limites_do_contrato():
    assert refs.MAX_ACTIVE_REFERENCES == 3
    assert refs.MAX_NOTE_CHARS == 160
    assert refs.MAX_UPLOAD_BYTES == 2 * 1024 * 1024
    assert (refs.MAX_EDGE_PX, refs.MIN_EDGE_PX, refs.MAX_SOURCE_PIXELS) == (1536, 256, 40_000_000)
    assert refs.CLIENT_RESIZE_JPEG_QUALITY_PERCENT == 90
    assert refs.ACCEPTED_TYPES == ("image/png", "image/jpeg", "image/webp")
    assert refs.limits() == {
        "max_active": 3,
        "max_upload_bytes": 2097152,
        "resize_max_edge_px": 1536,
        "resize_jpeg_quality_percent": 90,
        "note_max_chars": 160,
        "accepted_types": ["image/png", "image/jpeg", "image/webp"],
    }


def test_base64_do_maximo_cabe_no_invoke_sincrono():
    """O API Gateway (binary_media_types */*) re-codifica o corpo em base64 para
    a Lambda: o evento tem ~ MAX_BASE64_CHARS * 4/3 bytes, abaixo dos 6 MB."""
    assert refs.MAX_BASE64_CHARS * 4 / 3 < 4_000_000
    assert refs.MAX_BASE64_CHARS * 4 / 3 < 6_000_000


# ---------------------------------------------------------------------------
# base64
# ---------------------------------------------------------------------------


def test_decodifica_com_e_sem_padding():
    for raw in (b"a", b"ab", b"abc", b"abcd", bytes(range(200))):
        assert refs.decode_upload(_b64(raw)) == raw
        assert refs.decode_upload(_b64(raw, padding=True)) == raw


@pytest.mark.parametrize("texto", [None, "", 123, ["x"], b"abc"], ids=["none", "vazio", "int", "lista", "bytes"])
def test_base64_ausente_ou_de_tipo_errado(texto):
    erro = _erro(refs.decode_upload, texto)
    assert (erro.code, erro.http_status) == ("reference_invalid_encoding", 400)


@pytest.mark.parametrize(
    "texto",
    ["http://exemplo.com/foto.png", "https://x.y/z", "abc$def", "aGVsbG8-", "a b c d", "abc\n", "a=bc", "QUJDRA===", "QUJDR"],
    ids=["url-http", "url-https", "cifrao", "urlsafe", "espaco", "quebra", "igual-no-meio", "tres-iguais", "resto-1"],
)
def test_base64_invalido_nunca_vira_busca_de_url(texto):
    erro = _erro(refs.decode_upload, texto)
    assert (erro.code, erro.http_status) == ("reference_invalid_encoding", 400)


def test_texto_maior_que_o_limite_e_recusado_antes_de_decodificar(monkeypatch):
    def nao_decodifica(*args, **kwargs):
        raise AssertionError("decodificou um texto acima do limite")

    monkeypatch.setattr(refs.base64, "b64decode", nao_decodifica)
    erro = _erro(refs.decode_upload, "A" * (refs.MAX_BASE64_CHARS + 1))
    assert (erro.code, erro.http_status) == ("reference_too_large", 400)


# ---------------------------------------------------------------------------
# Re-encode
# ---------------------------------------------------------------------------


def test_jpeg_com_exif_sai_jpeg_sem_exif_e_com_orientacao_aplicada():
    png, w, h = refs.normalize_reference_image(_jpeg_com_exif(600, 400, orientation=6))
    image = _aberta(png)
    assert (image.format, image.mode) == ("JPEG", "RGB")
    assert (w, h) == image.size == (400, 600)  # orientacao 6 = girar 90 graus
    assert not {"exif", "comment", "icc_profile", "photoshop"} & set(image.info) and len(image.getexif()) == 0
    for segredo in (b"segredo-do-exif", b"autor-secreto", b"Exif"):
        assert segredo not in png


def test_png_com_texto_e_dpi_sai_sem_metadados():
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "segredo-do-png")
    png, _, _ = refs.normalize_reference_image(_save(Image.new("RGB", (400, 400), "red"), "PNG", pnginfo=info, dpi=(300, 300)))
    assert b"segredo-do-png" not in png
    assert not {"exif", "comment", "icc_profile", "dpi"} & set(_aberta(png).info)


def test_webp_e_aceito_e_vira_jpeg():
    raw = _save(Image.new("RGB", (500, 300), (10, 200, 10)), "WEBP")
    png, w, h = refs.normalize_reference_image(raw)
    assert (_aberta(png).format, (w, h)) == ("JPEG", (500, 300))


def test_png_com_transparencia_e_achatado_sobre_branco():
    raw = _png(300, 300, (255, 0, 0, 0), mode="RGBA")  # totalmente transparente
    png, _, _ = refs.normalize_reference_image(raw)
    image = _aberta(png)
    assert image.mode == "RGB" and image.getpixel((10, 10)) == (255, 255, 255)


def test_imagem_maior_que_o_limite_e_reduzida_mantendo_a_proporcao():
    png, w, h = refs.normalize_reference_image(_png(3000, 2000))
    assert max(w, h) == refs.MAX_EDGE_PX and (w, h) == (1536, 1024)
    assert _aberta(png).size == (w, h)
    retrato, rw, rh = refs.normalize_reference_image(_png(1200, 4000))
    assert max(rw, rh) == refs.MAX_EDGE_PX and rw < rh


def test_imagem_dentro_do_limite_nao_e_ampliada():
    _, w, h = refs.normalize_reference_image(_png(700, 500))
    assert (w, h) == (700, 500)


def test_menor_lado_abaixo_do_minimo_e_recusado():
    erro = _erro(refs.normalize_reference_image, _png(255, 900))
    assert (erro.code, erro.http_status) == ("reference_too_small", 400)
    assert refs.normalize_reference_image(_png(256, 900))[1:] == (256, 900)  # o minimo passa


def test_pixels_acima_do_limite_sao_recusados_antes_de_decodificar(monkeypatch):
    def nao_decodifica(self, *args, **kwargs):
        raise AssertionError("decodificou os pixels antes de checar o limite")

    monkeypatch.setattr(Image.Image, "load", nao_decodifica)
    erro = _erro(refs.normalize_reference_image, _png_com_cabecalho(8000, 6000))  # 48 milhoes
    assert (erro.code, erro.http_status) == ("reference_too_many_pixels", 400)


def test_pixels_no_limite_exato_nao_sao_recusados_pelo_limite():
    assert refs.MAX_SOURCE_PIXELS == 40_000_000
    erro = _erro(refs.normalize_reference_image, _png_com_cabecalho(8000, 5001))  # 40.008.000
    assert erro.code == "reference_too_many_pixels"
    # 8000 x 5000 = limite exato: passa o limite e so falha depois (sem pixels no arquivo).
    erro = _erro(refs.normalize_reference_image, _png_com_cabecalho(8000, 5000))
    assert erro.code == "reference_unsupported_type"


@pytest.mark.parametrize(
    "raw",
    [
        b"GIF89a" + b"\x00" * 64,
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"isto nao e uma imagem" * 20,
        b"%PDF-1.7\n" + b"x" * 64,
        b"BM" + b"\x00" * 64,
        b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32,
    ],
    ids=["gif", "svg", "texto", "pdf", "bmp", "riff-wave"],
)
def test_formatos_nao_aceitos_sao_recusados_pela_assinatura(raw):
    erro = _erro(refs.normalize_reference_image, raw)
    assert (erro.code, erro.http_status) == ("reference_unsupported_type", 400)


def test_o_tipo_vem_da_assinatura_e_nao_do_nome_ou_do_declarado():
    gif = _save(Image.new("RGB", (400, 400), "blue"), "GIF")
    assert _erro(refs.normalize_reference_image, gif).code == "reference_unsupported_type"
    assert refs.sniff_content_type(_png()) == "image/png"
    assert refs.sniff_content_type(_jpeg_com_exif()) == "image/jpeg"
    assert refs.sniff_content_type(_save(Image.new("RGB", (4, 4)), "WEBP")) == "image/webp"
    assert refs.sniff_content_type(gif) is None


def test_arquivo_truncado_ou_corrompido_e_erro_tipado():
    erro = _erro(refs.normalize_reference_image, _png(400, 400)[:60])
    assert erro.http_status == 400 and erro.code.startswith("reference_")
    assert _erro(refs.normalize_reference_image, b"").code == "reference_invalid_encoding"


def test_bytes_acima_do_limite_sao_recusados():
    grande = _png(10, 10) + b"\x00" * refs.MAX_UPLOAD_BYTES
    erro = _erro(refs.normalize_reference_image, grande)
    assert (erro.code, erro.http_status) == ("reference_too_large", 400)


def test_saida_e_deterministica():
    assert refs.normalize_reference_image(_png(500, 400)) == refs.normalize_reference_image(_png(500, 400))


# ---------------------------------------------------------------------------
# Nota
# ---------------------------------------------------------------------------


def test_nota_normaliza_espacos_e_vazio_vira_none():
    assert refs.normalize_note(None) is None
    assert refs.normalize_note("   \n ") is None
    assert refs.normalize_note("  banco   inclinado \n 30 graus ") == "banco inclinado 30 graus"
    assert refs.normalize_note("x" * refs.MAX_NOTE_CHARS) == "x" * refs.MAX_NOTE_CHARS


def test_nota_longa_e_422():
    erro = _erro(refs.normalize_note, "x" * (refs.MAX_NOTE_CHARS + 1))
    assert (erro.code, erro.http_status) == ("reference_note_too_long", 422)


# ---------------------------------------------------------------------------
# Serviço (SQLite falso + S3 falso)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(svc.ENV_ENABLED, "true")
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", BUCKET)
    for name in ("PUBLIC_ASSETS_BASE_URL", "S3_PRESIGN_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db(monkeypatch):
    fake = CardsSqlite()
    monkeypatch.setattr(svc, "transaction", fake.transaction)
    monkeypatch.setattr(svc, "utcnow", lambda: T0)
    fake.add_exercise(42, "Supino reto")
    fake.add_exercise(43, "Agachamento livre")
    yield fake
    fake.close()


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3(endpoint_url="https://fake-s3.local")
    monkeypatch.setattr(storage, "s3_client", lambda: fake)
    monkeypatch.setattr(storage, "presign_client", lambda: fake)
    return fake


def _criar(exercise_id=42, raw=None, **kwargs):
    return refs.create_reference(
        exercise_id=exercise_id, user_id=ADMIN, image_base64=_b64(raw or _png()), note=kwargs.pop("note", None), **kwargs
    )


def _ativas(db, exercise_id=42):
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            return refs.active_reference_keys(cursor, exercise_id)


def test_cria_grava_jpeg_privado_no_s3_e_devolve_a_referencia(db, s3):
    ref = _criar(note="  banco 30 graus  ")
    assert set(ref) == {"id", "url", "url_expires_at", "width", "height", "bytes", "note", "created_at"}
    assert (ref["width"], ref["height"], ref["note"]) == (640, 480, "banco 30 graus")
    key = f"exercise-card-references/42/{ref['id']}.jpg"
    obj = s3.objects[(BUCKET, key)]
    assert obj.content_type == "image/jpeg" and "no-store" in obj.cache_control and "public" not in obj.cache_control
    assert _aberta(obj.body).size == (640, 480) and ref["bytes"] == len(obj.body)
    assert ref["url"].startswith("https://fake-s3.local/musclefit-uploads/exercise-card-references/42/")
    expira = datetime.strptime(ref["url_expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    assert 14 * 60 <= (expira - agora).total_seconds() <= 15 * 60 + 5  # 15 min
    assert ref["created_at"] == "2026-11-06T12:00:00Z"
    linha = db.reference(ref["id"])
    assert (linha["exercise_id"], linha["s3_key"], linha["content_type"], linha["created_by"]) == (42, key, "image/jpeg", ADMIN)
    assert linha["deleted_at"] is None and linha["bytes"] == len(obj.body)


def test_resposta_nunca_expoe_chave_s3_nem_autor(db, s3):
    ref = _criar()
    texto = repr(ref)
    assert "s3_key" not in ref and "created_by" not in ref
    assert "exercise-card-references/" in ref["url"]  # so dentro da URL assinada
    assert "created_by" not in texto


def test_so_presigna_o_prefixo_de_referencias(db, s3):
    _criar()
    (chamada,) = s3.calls_of("generate_presigned_url")
    assert chamada["Params"]["Key"].startswith(f"{storage.REFERENCE_PREFIX}/") and chamada["ExpiresIn"] == 900


def test_limite_de_tres_ativas_409_e_nada_e_gravado(db, s3):
    for _ in range(3):
        _criar()
    antes = sorted(s3.keys(BUCKET))
    erro = _erro(_criar)
    assert (erro.code, erro.http_status) == ("exercise_card_reference_limit", 409)
    assert sorted(s3.keys(BUCKET)) == antes and len(antes) == 3
    assert db.raw.execute("SELECT COUNT(*) FROM exercise_card_references").fetchone()[0] == 3


def test_limite_e_por_exercicio(db, s3):
    for _ in range(3):
        _criar(42)
    assert _criar(43)["id"]  # outro exercicio nao e afetado
    assert len(_ativas(db, 43)) == 1


def test_excluida_nao_conta_para_o_limite(db, s3):
    primeira = _criar()
    _criar()
    _criar()
    refs.delete_reference(exercise_id=42, ref_id=primeira["id"], user_id=ADMIN)
    assert _criar()["id"] > primeira["id"]
    assert len(_ativas(db)) == 3


def test_nota_longa_e_imagem_invalida_nao_gravam_nada(db, s3):
    assert _erro(_criar, note="x" * 161).code == "reference_note_too_long"
    assert _erro(_criar, raw=b"GIF89a" + b"\x00" * 64).code == "reference_unsupported_type"
    assert _erro(refs.create_reference, exercise_id=42, user_id=ADMIN, image_base64="http://x.y/a.png").code == (
        "reference_invalid_encoding"
    )
    assert s3.keys(BUCKET) == []
    assert db.raw.execute("SELECT COUNT(*) FROM exercise_card_references").fetchone()[0] == 0
    assert db.locks == []  # nem chegou a travar o exercicio


def test_exercicio_inexistente_e_404_e_nada_e_gravado(db, s3):
    erro = _erro(_criar, exercise_id=999)
    assert (erro.code, erro.http_status) == ("exercise_not_found", 404)
    assert s3.keys(BUCKET) == []


def test_falha_do_s3_vira_502_e_desfaz_a_linha(db, s3):
    # A chave depende do id gerado: o 1o INSERT do fake recebe id 1.
    s3.fail_put_keys.add("exercise-card-references/42/1.jpg")
    erro = _erro(_criar)
    assert (erro.code, erro.http_status) == ("exercise_card_reference_storage_failed", 502)
    assert db.raw.execute("SELECT COUNT(*) FROM exercise_card_references").fetchone()[0] == 0
    assert "fake" not in erro.message.lower() and "falhou" not in erro.message.lower()  # sem detalhe interno


def test_flag_desligada_e_503_em_todas_as_operacoes(db, s3, monkeypatch):
    ref = _criar()
    monkeypatch.setenv(svc.ENV_ENABLED, "false")
    for fn, kwargs in (
        (refs.create_reference, {"exercise_id": 42, "user_id": ADMIN, "image_base64": _b64(_png())}),
        (refs.list_references, {"exercise_id": 42}),
        (refs.delete_reference, {"exercise_id": 42, "ref_id": ref["id"], "user_id": ADMIN}),
    ):
        erro = _erro(fn, **kwargs)
        assert (erro.code, erro.http_status) == ("exercise_card_generation_disabled", 503)
    assert db.reference(ref["id"])["deleted_at"] is None


def test_lista_so_as_ativas_do_exercicio_com_limites(db, s3):
    a, b = _criar(), _criar()
    outro = _criar(43)
    refs.delete_reference(exercise_id=42, ref_id=a["id"], user_id=ADMIN)
    resposta = refs.list_references(exercise_id=42)
    assert [item["id"] for item in resposta["items"]] == [b["id"]]
    assert outro["id"] not in [item["id"] for item in resposta["items"]]
    assert resposta["limits"] == refs.limits()
    assert refs.list_references(exercise_id=43)["items"][0]["id"] == outro["id"]


def test_lista_de_exercicio_inexistente_e_404(db, s3):
    assert _erro(refs.list_references, exercise_id=999).code == "exercise_not_found"


def test_lista_vazia_tem_limites(db, s3):
    assert refs.list_references(exercise_id=42) == {"items": [], "limits": refs.limits()}


def test_falha_ao_assinar_nao_derruba_a_lista(db, s3, monkeypatch):
    _criar()

    def quebra(*args, **kwargs):
        raise RuntimeError("sem credencial")

    monkeypatch.setattr(storage, "presign_reference_url", quebra)
    (item,) = refs.list_references(exercise_id=42)["items"]
    assert item["url"] is None and item["url_expires_at"] is None and item["width"] == 640


def test_soft_delete_some_da_lista_do_worker_e_do_s3_mas_a_linha_fica(db, s3):
    a, b = _criar(), _criar()
    refs.delete_reference(exercise_id=42, ref_id=a["id"], user_id=ADMIN)
    assert _ativas(db) == [f"exercise-card-references/42/{b['id']}.jpg"]
    assert (BUCKET, f"exercise-card-references/42/{a['id']}.jpg") not in s3.objects
    linha = db.reference(a["id"])  # soft-delete: a linha continua, marcada
    assert linha["deleted_at"] == T0
    assert [item["id"] for item in refs.list_references(exercise_id=42)["items"]] == [b["id"]]


def test_excluir_duas_vezes_e_404(db, s3):
    ref = _criar()
    refs.delete_reference(exercise_id=42, ref_id=ref["id"], user_id=ADMIN)
    erro = _erro(refs.delete_reference, exercise_id=42, ref_id=ref["id"], user_id=ADMIN)
    assert (erro.code, erro.http_status) == ("exercise_card_reference_not_found", 404)


def test_anti_idor_referencia_de_outro_exercicio_e_404_e_nada_muda(db, s3):
    ref = _criar(42)
    erro = _erro(refs.delete_reference, exercise_id=43, ref_id=ref["id"], user_id=ADMIN)
    assert (erro.code, erro.http_status) == ("exercise_card_reference_not_found", 404)
    assert db.reference(ref["id"])["deleted_at"] is None
    assert (BUCKET, f"exercise-card-references/42/{ref['id']}.jpg") in s3.objects
    assert _erro(refs.delete_reference, exercise_id=42, ref_id=9999, user_id=ADMIN).code == "exercise_card_reference_not_found"
    assert _erro(refs.delete_reference, exercise_id=999, ref_id=ref["id"], user_id=ADMIN).code == "exercise_not_found"


def test_falha_do_s3_ao_excluir_nao_desfaz_o_soft_delete(db, s3, monkeypatch):
    ref = _criar()

    def quebra(*args, **kwargs):
        raise storage.ExerciseCardStorageError("S3 fora")

    monkeypatch.setattr(storage, "delete_reference", quebra)
    refs.delete_reference(exercise_id=42, ref_id=ref["id"], user_id=ADMIN)  # nao levanta
    assert db.reference(ref["id"])["deleted_at"] == T0
    assert _ativas(db) == []


def test_worker_so_ve_as_tres_mais_antigas_ativas_em_ordem(db, s3):
    ids = [db.add_reference(42) for _ in range(4)]  # 4 ativas direto no banco (estado anormal)
    db.add_reference(42, deleted=True)
    assert _ativas(db) == [f"exercise-card-references/42/{i}.jpg" for i in ids[:3]]
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            rows = refs.active_reference_rows(cursor, 42)
    assert [r["id"] for r in rows] == ids[:3]


def test_selects_projetam_so_as_colunas_pedidas(db, s3):
    """O fake devolve exatamente as colunas do SELECT (como o banco): se o SQL
    esquecesse uma coluna, o codigo quebraria aqui em vez de passar."""
    db.add_reference(42, note="n")
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            (row,) = refs.active_reference_rows(cursor, 42)
            cursor.execute("SELECT s3_key FROM exercise_card_references WHERE exercise_id = %s", (42,))
            so_chave = cursor.fetchone()
    assert set(row) == {"id", "s3_key", "width", "height", "bytes", "note", "created_at"}
    assert set(so_chave) == {"s3_key"}
    assert "deleted_at" not in row and "created_by" not in row
    assert refs.serialize_reference(row, None)["created_at"] == "2026-11-06T12:00:00Z"


def test_worker_le_os_bytes_pelas_chaves(db, s3):
    primeira = _criar(raw=_png(300, 300, (1, 2, 3)))
    segunda = _criar(raw=_png(400, 300, (4, 5, 6)))
    dados = storage.get_reference_bytes(_ativas(db))
    assert [_aberta(d).size for d in dados] == [(300, 300), (400, 300)]
    assert primeira["id"] < segunda["id"]


def test_serialize_nao_vaza_a_chave():
    row = {"id": 3, "s3_key": "exercise-card-references/42/3.jpg", "width": 10, "height": 20, "bytes": 30, "note": None,
           "created_at": T0}
    assert "exercise-card-references" not in repr(refs.serialize_reference(row, None))
