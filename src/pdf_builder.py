"""
Módulo Ensamblador — Salida a PDF
Recompone las imágenes traducidas a un PDF final.
"""

import fitz  # PyMuPDF
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def imagenes_a_pdf(
    img_dir: str | Path,
    salida_pdf: str | Path,
    patron: str = "*_es.png",
    calidad_jpg: int | None = None,
) -> Path:
    """
    Convierte imágenes traducidas en un PDF final, en orden.

    Args:
        img_dir: Directorio con las imágenes renderizadas.
        salida_pdf: Ruta del PDF de salida.
        patron: Glob pattern para encontrar las imágenes.
        calidad_jpg: Si se especifica, convierte las imágenes a JPEG
                     con esta calidad (1-100) para reducir tamaño.
                     Si None, usa las imágenes originales.

    Returns:
        Ruta al PDF generado.
    """
    img_dir = Path(img_dir)
    salida = Path(salida_pdf)
    salida.parent.mkdir(parents=True, exist_ok=True)

    imgs = sorted(img_dir.glob(patron))
    if not imgs:
        raise FileNotFoundError(
            f"No se encontraron imágenes con patrón '{patron}' en {img_dir}"
        )

    logger.info(f"Recomponiendo {len(imgs)} imágenes a PDF: {salida}")

    doc = fitz.open()
    for img_path in imgs:
        try:
            # OPTIMIZACIÓN OOM: Usamos PIL para leer solo el header y las dimensiones, 
            # evitando descomprimir toda la imagen en RAM como hacía fitz.Pixmap().
            try:
                from PIL import Image
            except ImportError:
                raise RuntimeError("Se requiere la librería 'Pillow' (PIL) para el ensamblaje de PDF optimizado.")

            with Image.open(img_path) as img:
                width, height = img.size

            img_stream = None
            # Si se pide JPEG para reducir tamaño, lo convertimos en memoria
            if calidad_jpg is not None:
                import io
                with Image.open(img_path) as img:
                    if img.mode in ("RGBA", "P", "LA"):
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=calidad_jpg)
                    img_stream = buf.getvalue()

            # Crear página con dimensiones exactas de la imagen
            rect = fitz.Rect(0, 0, width, height)
            page = doc.new_page(width=width, height=height)
            
            if img_stream:
                page.insert_image(rect, stream=img_stream)
            else:
                # Al pasar filename, PyMuPDF solo almacena la referencia y lee el archivo
                # directo al disco al momento del doc.save(), gastando casi 0 RAM.
                page.insert_image(rect, filename=str(img_path))

            logger.debug(f"Página añadida: {img_path.name}")

        except Exception as e:
            logger.error(f"Error al procesar {img_path.name}: {e}")
            raise

    doc.save(str(salida), deflate=True)  # compresión deflate para reducir tamaño
    doc.close()

    tamaño_mb = salida.stat().st_size / (1024 * 1024)
    logger.info(f"PDF generado: {salida} ({tamaño_mb:.1f} MB, {len(imgs)} páginas)")

    return salida