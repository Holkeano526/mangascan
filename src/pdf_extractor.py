"""
Módulo Extractor — Ingesta del PDF
Convierte un PDF completo en imágenes por página (200-300 DPI) preparadas para el OCR.
"""

import fitz  # PyMuPDF
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def pdf_a_imagenes(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: int = 250,
    prefijo: str = "page_",
    formato: str = "png",
) -> list[Path]:
    """
    Convierte cada página de un PDF en una imagen.

    Args:
        pdf_path: Ruta al archivo PDF de entrada.
        out_dir: Directorio donde guardar las imágenes.
        dpi: Resolución de salida (200-300 recomendado).
        prefijo: Prefijo para los nombres de archivo.
        formato: Formato de imagen ('png' o 'jpg').

    Returns:
        Lista de rutas a las imágenes generadas, en orden de página.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    zoom = dpi / 72  # 72 = DPI base del PDF
    mat = fitz.Matrix(zoom, zoom)

    doc = fitz.open(pdf_path)
    total_paginas = len(doc)
    logger.info(f"PDF abierto: {pdf_path} ({total_paginas} páginas)")

    rutas = []
    for i, page in enumerate(doc, start=1):
        try:
            pix = page.get_pixmap(matrix=mat)
            ruta = out / f"{prefijo}{i:04d}.{formato}"
            pix.save(str(ruta))
            rutas.append(ruta)
            logger.debug(f"Página {i}/{total_paginas} → {ruta.name}")
        except Exception as e:
            logger.error(f"Error al procesar página {i}: {e}")
            raise

    doc.close()
    logger.info(f"Extraídas {len(rutas)} imágenes en {out}")
    return rutas