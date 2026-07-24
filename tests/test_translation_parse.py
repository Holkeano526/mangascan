"""Tests de la validación/parseo de la respuesta del modelo (función pura)."""
import json

import pytest

from src.translator_engine import parsear_traduccion


def test_json_simple():
    out = parsear_traduccion('{"0": "Hola", "1": "Adiós"}', {"0", "1"})
    assert out == {"0": "Hola", "1": "Adiós"}


def test_quita_cercas_markdown():
    contenido = '```json\n{"0": "Hola"}\n```'
    assert parsear_traduccion(contenido, {"0"}) == {"0": "Hola"}


def test_rellena_ids_faltantes():
    out = parsear_traduccion('{"0": "Hola"}', {"0", "1", "2"})
    assert out["0"] == "Hola"
    assert out["1"] == ""
    assert out["2"] == ""


def test_rechaza_caracteres_asiaticos():
    # Hiragana en la traducción → debe rechazarse para forzar reintento.
    with pytest.raises(ValueError):
        parsear_traduccion('{"0": "こんにちは"}', {"0"})


def test_rechaza_kanji():
    with pytest.raises(ValueError):
        parsear_traduccion('{"0": "今日"}', {"0"})


def test_rechaza_json_invalido():
    with pytest.raises(json.JSONDecodeError):
        parsear_traduccion("esto no es json", {"0"})
