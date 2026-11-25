"""
Módulo Extractor — Ingesta del PDF
Convierte un PDF completo en imágenes por página (200-300 DPI) preparadas para el OCR.
"""

import fitz  # PyMuPDF
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


# Lado largo mínimo al que se renderiza una página escaneada de baja resolución.
# Por debajo de esto el OCR pierde texto pequeño.
LADO_MIN_PX = 1600


def zoom_nativo(page: "fitz.Page", zoom_max: float) -> float:
    """Zoom que reproduce la resolución NATIVA de la imagen escaneada de la página.

    Los PDF de manga suelen ser una imagen por página. Renderizarlos a DPI fijo
    los amplía sin añadir detalle: un PDF de páginas a 72 pt/px renderizado a
    250 DPI genera 5390x7500 en lugar de los 2070x2880 originales (6.8x más
    píxeles, y todas las fases del pipeline se vuelven más lentas).
    Medido en el NAS (i5-1135G7): 48 s/página -> 33 s/página con el mismo resultado.

    - Página con imagen dominante: zoom = ancho_px / ancho_pt de esa imagen,
      nunca por encima de zoom_max (el DPI pedido es el techo).
    - Página sin imágenes (texto vectorial): zoom_max.
    """
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    mayor = None
    for info in infos:
        x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area > 0 and (mayor is None or area > mayor[0]):
            mayor = (area, info, x1 - x0, y1 - y0)
    if mayor is None:
        return zoom_max
    _, info, ancho_pt, alto_pt = mayor
    # Solo si la imagen ocupa la mayor parte de la página (escaneo de página completa)
    if mayor[0] < 0.5 * page.rect.width * page.rect.height:
        return zoom_max
    zoom = max(info["width"] / ancho_pt, info["height"] / alto_pt)
    lado_largo_pt = max(page.rect.width, page.rect.height)
    zoom = max(zoom, LADO_MIN_PX / lado_largo_pt)
    return min(zoom, zoom_max)


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
        dpi: Resolución MÁXIMA. Las páginas escaneadas se extraen a su resolución
             nativa si es menor (ver zoom_nativo).
        prefijo: Prefijo para los nombres de archivo.
        formato: Formato de imagen ('png' o 'jpg').

    Returns:
        Lista de rutas a las imágenes generadas, en orden de página.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    zoom_max = dpi / 72  # 72 = DPI base del PDF (techo de resolución)

    doc = fitz.open(pdf_path)
    total_paginas = len(doc)
    logger.info(f"PDF abierto: {pdf_path} ({total_paginas} páginas)")

    rutas = []
    for i, page in enumerate(doc, start=1):
        try:
            zoom = zoom_nativo(page, zoom_max)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            ruta = out / f"{prefijo}{i:04d}.{formato}"
            pix.save(str(ruta))
            rutas.append(ruta)
            logger.debug(f"Página {i}/{total_paginas} → {ruta.name} ({pix.width}x{pix.height})")
        except Exception as e:
            logger.error(f"Error al procesar página {i}: {e}")
            raise

    doc.close()
    logger.info(f"Extraídas {len(rutas)} imágenes en {out}")
    return rutas