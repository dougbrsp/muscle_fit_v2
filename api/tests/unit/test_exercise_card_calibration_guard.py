"""Guarda do script de calibracao dos cards (`api/scripts/exercise_card_calibration.py`):
ele chama a OpenAI de verdade e grava no banco, entao so pode rodar no DEV
local (MySQL do compose + LocalStack). Nada aqui roda o fluxo.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_calibration_guard.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "exercise_card_calibration.py"


@pytest.fixture(scope="module")
def calibracao():
    spec = importlib.util.spec_from_file_location("exercise_card_calibration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEV = {
    "DB_BACKEND": "pymysql",
    "AWS_ENDPOINT_URL": "http://localstack:4566",
    "OPENAI_API_KEY": "sk-dev",
}


def test_aceita_o_compose_de_dev(calibracao):
    calibracao.check_dev_environment(DEV)
    calibracao.check_dev_environment({**DEV, "AWS_ENDPOINT_URL": "http://localhost:4566"})


@pytest.mark.parametrize(
    "ajuste, trecho",
    [
        ({"DB_BACKEND": "data-api"}, "DB_BACKEND"),
        ({"DB_BACKEND": None}, "DB_BACKEND"),
        ({"AWS_ENDPOINT_URL": None}, "LocalStack"),
        ({"AWS_ENDPOINT_URL": "https://s3.sa-east-1.amazonaws.com"}, "LocalStack"),
        ({"AWS_ENDPOINT_URL": "http://localstack.evil.com:4566"}, "LocalStack"),
        ({"AWS_LAMBDA_FUNCTION_NAME": "musclefit-api-core"}, "Lambda"),
        ({"AWS_EXECUTION_ENV": "AWS_Lambda_python3.12"}, "Lambda"),
        ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY"),
        ({"OPENAI_API_KEY": "replace_me"}, "OPENAI_API_KEY"),
    ],
)
def test_recusa_qualquer_sinal_de_ambiente_real(calibracao, ajuste, trecho):
    env = {**DEV, **ajuste}
    env = {chave: valor for chave, valor in env.items() if valor is not None}
    with pytest.raises(calibracao.NotDevEnvironment) as exc:
        calibracao.check_dev_environment(env)
    assert trecho in str(exc.value)


def test_main_recusa_antes_de_tocar_no_banco(calibracao, monkeypatch, capsys):
    monkeypatch.setenv("DB_BACKEND", "data-api")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setattr(calibracao, "_first_admin_id", lambda: pytest.fail("nao podia consultar o banco"))
    assert calibracao.main(["--exercises", "1"]) == 2
    assert "RECUSADO" in capsys.readouterr().err


def test_nome_do_arquivo_sem_acento_nem_simbolo(calibracao):
    assert calibracao._slug("Extensão de tríceps (máquina) 45°") == "extensao-de-triceps-maquina-45"
    assert calibracao._slug("") == "exercicio"


# ---------------------------------------------------------------------------
# --compose-only (sem OpenAI) e prancha
# ---------------------------------------------------------------------------


def test_compose_only_nao_exige_chave_da_openai_mas_continua_so_no_dev(calibracao):
    sem_chave = {k: v for k, v in DEV.items() if k != "OPENAI_API_KEY"}
    calibracao.check_dev_environment(sem_chave, require_openai=False)
    with pytest.raises(calibracao.NotDevEnvironment):
        calibracao.check_dev_environment(sem_chave)  # gerar exige a chave
    for ajuste in (
        {"DB_BACKEND": "data-api"},
        {"AWS_ENDPOINT_URL": "https://s3.sa-east-1.amazonaws.com"},
        {"AWS_LAMBDA_FUNCTION_NAME": "musclefit-plan-generator"},
    ):
        with pytest.raises(calibracao.NotDevEnvironment):
            calibracao.check_dev_environment({**sem_chave, **ajuste}, require_openai=False)


def test_compose_only_recusa_fora_do_dev_antes_de_tocar_em_qualquer_coisa(calibracao, monkeypatch, capsys):
    monkeypatch.setenv("DB_BACKEND", "data-api")
    monkeypatch.setattr(calibracao, "compose_one", lambda *a, **k: pytest.fail("nao podia recompor"))
    assert calibracao.main(["--compose-only", "41"]) == 2
    assert "RECUSADO" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--exercises", "1", "--compose-only", "2"],
        ["--exercises", "1", "--background", "opaque"],
        ["--exercises", "1", "--text-level", "standard"],
    ],
    ids=["sem-modo", "dois-modos", "fundo-saiu-no-v3", "nivel-da-v1"],
)
def test_argumentos_invalidos(calibracao, argv):
    with pytest.raises(SystemExit) as exc:
        calibracao.main(argv)
    assert exc.value.code == 2


def test_prancha_poe_os_cards_lado_a_lado(calibracao):
    from PIL import Image

    def card(cor):
        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1536), cor).save(buffer, format="PNG")
        return buffer.getvalue()

    prancha = Image.open(io.BytesIO(calibracao.build_board([card((255, 0, 0)), card((0, 0, 255))])))
    largura, gap = calibracao.BOARD_CARD_WIDTH, calibracao.BOARD_GAP
    assert prancha.size == (gap + 2 * (largura + gap), 2 * gap + 600)
    assert prancha.getpixel((gap + 10, gap + 10)) == (255, 0, 0)
    assert prancha.getpixel((2 * gap + largura + 10, gap + 10)) == (0, 0, 255)
    with pytest.raises(ValueError):
        calibracao.build_board([])


class _FakeConn:
    def cursor(self):
        return contextlib.nullcontext(object())


def _compose_only_fakes(monkeypatch, contexto, objetos: dict[str, bytes], lidos: list[str]):
    from app import db as app_db
    from app import exercise_card_prompt as card_prompt
    from app import exercise_card_service as svc
    from app import exercise_card_storage as storage

    def read_draft(key):
        lidos.append(key)
        if key not in objetos:
            raise storage.ExerciseCardStorageError(f"{key} ausente", code=storage.S3_READ_FAILED)
        return objetos[key]

    monkeypatch.setattr(svc, "load_generation", lambda gid, include_prompt=False: {"exercise_id": 53})
    monkeypatch.setattr(app_db, "transaction", lambda: contextlib.nullcontext(_FakeConn()))
    monkeypatch.setattr(card_prompt, "load_context", lambda cursor, exercise_id: contexto)
    monkeypatch.setattr(storage, "read_draft", read_draft)


def test_compose_only_recompoe_no_layout_v3_com_os_slugs_do_catalogo(calibracao, monkeypatch, tmp_path):
    """`compose_one` (banco e S3 falsos): le as duas fotos guardadas e o
    contexto do catalogo e recompoe o card no layout atual (V3), com a
    silhueta acendendo os slugs que `load_context` trouxe - o caminho para
    iterar o layout sem pagar a OpenAI de novo."""
    from app import exercise_card_composer as composer
    from app import exercise_card_prompt as card_prompt
    from app import exercise_card_storage as storage
    from tests.support import card_illustrations as ill

    cena1, cena2 = ill.scene_photo_png(color=ill.START_COLOR), ill.scene_photo_png(color=ill.END_COLOR)
    base = {"exercise_id": 53, "name": "Supino reto com barra", "primary_muscles": ("Peitoral",),
            "secondary_muscles": ("Tríceps",)}
    contexto = card_prompt.ExerciseCardContext(**base, primary_slugs=("chest",), secondary_slugs=("triceps",))
    lidos: list[str] = []
    _compose_only_fakes(monkeypatch, contexto,
                        {storage.scene_key(41, 1): cena1, storage.scene_key(41, 2): cena2}, lidos)

    resumo = calibracao.compose_one(41, out_dir=tmp_path)
    assert resumo["status"] == "COMPOSED", resumo
    assert lidos == [storage.scene_key(41, 1), storage.scene_key(41, 2)]
    assets = composer.load_card_assets()
    esperado = composer.compose_card_v3(cena1, cena2, composer.content_from_context(contexto), assets)
    assert resumo["_card"] == esperado
    assert (tmp_path / resumo["card_file"]).read_bytes() == esperado
    assert (tmp_path / resumo["scene1_file"]).read_bytes() == cena1
    assert composer.LAYOUT_VERSION == "CARD_LAYOUT_V3_PHOTO"
    # A silhueta acendeu os slugs do catalogo: sem eles o card e outro.
    sem_slugs = card_prompt.ExerciseCardContext(**base)
    assert composer.compose_card_v3(cena1, cena2, composer.content_from_context(sem_slugs), assets) != esperado


def test_compose_only_de_geracao_v2_responde_sem_cenas(calibracao, monkeypatch, tmp_path):
    from app import exercise_card_prompt as card_prompt
    from app import exercise_card_storage as storage

    contexto = card_prompt.ExerciseCardContext(exercise_id=53, name="Supino")
    lidos: list[str] = []
    _compose_only_fakes(monkeypatch, contexto, {}, lidos)
    resumo = calibracao.compose_one(41, out_dir=tmp_path)
    assert resumo["status"] == "NO_V3_SCENES"
    assert lidos == [storage.scene_key(41, 1)]
    assert "_card" not in resumo


# ---------------------------------------------------------------------------
# V3: fotos de referencia locais, so em memoria
# ---------------------------------------------------------------------------


def test_referencias_locais_normalizadas_como_a_rota(calibracao, tmp_path):
    from PIL import Image

    from tests.support import card_illustrations as ill

    foto = tmp_path / "aparelho.png"
    foto.write_bytes(ill.scene_photo_png(800, 1200))
    (imagem,) = calibracao.load_local_references([foto])
    assert imagem.startswith(b"\xff\xd8\xff")  # JPEG, como as gravadas pela rota
    with Image.open(io.BytesIO(imagem)) as aberta:
        assert aberta.size == (800, 1200)


def test_referencias_locais_invalidas_sao_recusadas(calibracao, tmp_path):
    texto = tmp_path / "nao-e-foto.png"
    texto.write_bytes(b"nao e imagem")
    with pytest.raises(ValueError):
        calibracao.load_local_references([texto])
    with pytest.raises(ValueError):
        calibracao.load_local_references([texto] * 4)  # acima do limite de fotos ativas


def test_referencias_locais_entram_no_worker_so_durante_o_bloco(calibracao):
    from app import exercise_card_jobs as jobs

    original_rows, original_bytes = jobs.refs.active_reference_rows, jobs.storage.get_reference_bytes_present
    with calibracao.local_references([b"A", b"B"]):
        linhas = jobs.refs.active_reference_rows(None, 21)
        assert [linha["id"] for linha in linhas] == [-1, -2]
        assert [b for _k, b in jobs.storage.get_reference_bytes_present([linha["s3_key"] for linha in linhas])] == [b"A", b"B"]
    assert jobs.refs.active_reference_rows is original_rows
    assert jobs.storage.get_reference_bytes_present is original_bytes
    with calibracao.local_references([]):
        assert jobs.refs.active_reference_rows is original_rows


def test_main_recusa_referencia_invalida_antes_de_gerar(calibracao, monkeypatch, tmp_path, capsys):
    for chave, valor in {**DEV, "AWS_LAMBDA_FUNCTION_NAME": ""}.items():
        monkeypatch.setenv(chave, valor)
    monkeypatch.delenv("AWS_EXECUTION_ENV", raising=False)
    monkeypatch.setattr(calibracao, "_first_admin_id", lambda: 1)
    monkeypatch.setattr(calibracao, "run_one", lambda *a, **k: pytest.fail("nao podia gerar"))
    ruim = tmp_path / "ruim.jpg"
    ruim.write_bytes(b"x")
    assert calibracao.main(["--exercises", "21", "--references", str(ruim), "--out", str(tmp_path)]) == 2
    assert "referencia invalida" in capsys.readouterr().err
