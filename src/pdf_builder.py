"""
Módulo Ensamblador — Salida a PDF
Recompone las imágenes traducidas a un PDF final (Optimizado para OOM).
"""

from pathlib import Path
import logging
import os

logger = logging.getLogger(__name__)

def imagenes_a_pdf(
    img_dir: str | Path,
    salida_pdf: str | Path,
    patron: str = "*_es.png",
    calidad_jpg: int | None = 70,
    max_width: int = 1400
) -> Path:
    """
    Convierte imágenes traducidas en un PDF final, en orden.
    Utiliza img2pdf y pre-compresión JPG para evitar consumo 
    excesivo de RAM (OOM-safe) y reducir el peso final del archivo.
    """
    try:
        import img2pdf
        from PIL import Image
    except ImportError:
        raise RuntimeError("Faltan librerías: asegúrate de tener img2pdf y Pillow instalados.")

    img_dir = Path(img_dir)
    salida = Path(salida_pdf)
    salida.parent.mkdir(parents=True, exist_ok=True)

    imgs = sorted(img_dir.glob(patron))
    if not imgs:
        if patron == "*_es.png":
            imgs = sorted(img_dir.glob("*_es.jpg"))
        if not imgs:
            imgs = sorted(img_dir.glob("*_es.*"))
        if not imgs:
            raise FileNotFoundError(f"No se encontraron imágenes en {img_dir}")

    logger.info(f"Recomponiendo {len(imgs)} imágenes a PDF: {salida}")

    # Directorio temporal para los JPGs redimensionados
    jpg_dir = salida.parent / f"temp_jpgs_{salida.stem}"
    jpg_dir.mkdir(exist_ok=True)
    
    jpg_files = []
    try:
        # 1. Compresión y re-escalado (1 a 1 para no saturar memoria)
        for idx, img_path in enumerate(imgs):
            jpg_path = jpg_dir / f"page_{idx:04d}.jpg"
            jpg_files.append(str(jpg_path))
            
            with Image.open(img_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                
                # Re-escalar si es muy ancha
                w, h = img.size
                if max_width and w > max_width:
                    ratio = max_width / w
                    new_h = int(h * ratio)
                    img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
                
                calidad = calidad_jpg if calidad_jpg else 80
                img.save(jpg_path, "JPEG", quality=calidad, optimize=True)
                
        # 2. Ensamblado directo al disco (Streaming OOM-proof)
        with open(salida, "wb") as f:
            img2pdf.convert(jpg_files, outputstream=f)
            
    except Exception as e:
        logger.error(f"Error construyendo PDF: {e}")
        raise
    finally:
        # 3. Limpieza estricta de temporales
        for jf in jpg_files:
            try:
                os.remove(jf)
            except OSError:
                pass
        try:
            jpg_dir.rmdir()
        except OSError:
            pass

    if salida.exists():
        tamaño_mb = salida.stat().st_size / (1024 * 1024)
        logger.info(f"PDF generado exitosamente: {salida.name} ({tamaño_mb:.1f} MB)")

    return salida