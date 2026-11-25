"""Smoke tests: los módulos deben importar sin errores (detecta fallos de sintaxis/imports)."""


def test_import_translator_engine():
    import src.translator_engine  # noqa: F401


def test_import_orquestador():
    import src.orquestador  # noqa: F401


def test_import_main():
    # El servidor web vive en src.main desde el refactor 9faa520 (antes src.web_server)
    import src.main  # noqa: F401


def test_import_modulos_de_aceleracion():
    # Deben importar sin OpenVINO ni manga-image-translator (se cargan bajo demanda)
    import src.aceleracion  # noqa: F401
    import src.detector_cascada  # noqa: F401
    import src.optimizaciones_mit  # noqa: F401
