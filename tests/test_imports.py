"""Smoke tests: los módulos deben importar sin errores (detecta fallos de sintaxis/imports)."""


def test_import_translator_engine():
    import src.translator_engine  # noqa: F401


def test_import_orquestador():
    import src.orquestador  # noqa: F401


def test_import_web_server():
    import src.web_server  # noqa: F401
