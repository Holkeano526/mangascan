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
            # Cargar imagen como Pixmap
            pix = fitz.Pixmap(str(img_path))

            # Si se pide JPEG para reducir tamaño
            if calidad_jpg is not None:
                # Convertir a RGB si es necesario (PNG puede ser RGBA)
                if pix.n > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                # Guardar temporal como JPEG
                import io
                buf = io.BytesIO()
                # PyMuPDF no tiene save a BytesIO directo para JPEG,
                # así que usamos el método directo si existe
                # Alternativa: usar PIL para convertir
                try:
                    from PIL import Image
                    img_temp = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    buf = io.BytesIO()
                    img_temp.save(buf, format="JPEG", quality=calidad_jpg)
                    buf.seek(0)
                    pix = fitz.Pixmap(buf.read())
                except ImportError:
                    logger.warning("PIL no disponible, usando imagen original sin compresión")

            # Crear página con dimensiones de la imagen
            rect = fitz.Rect(0, 0, pix.width, pix.height)
            page = doc.new_page(width=pix.width, height=pix.height)
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