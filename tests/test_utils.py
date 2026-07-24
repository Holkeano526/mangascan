"""Tests de utilidades puras y del saneo de nombres de archivo."""
from pathlib import Path

from src.orquestador import formatear_tiempo


def test_formatear_segundos():
    assert formatear_tiempo(45) == "45s"


def test_formatear_minutos():
    assert formatear_tiempo(125) == "2m 5s"


def test_formatear_horas():
    assert formatear_tiempo(3725) == "1h 2m 5s"


def test_saneo_nombre_descarta_rutas():
    # Path(...).name descarta componentes de ruta (defensa contra path traversal).
    assert Path("../../etc/passwd").name == "passwd"
    assert Path("carpeta/sub/malo.pdf").name == "malo.pdf"
    assert Path("normal.pdf").name == "normal.pdf"
