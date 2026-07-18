"""
Fase 6 — Orquestación
Encadena las fases 1→5 con manejo de errores, idempotencia y logs.
"""

import os
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from datetime import datetime

from .fase1_pdf_a_imagenes import pdf_a_imagenes
from .fase2_detectar_ocr import detectar_y_ocr, guardar_json_pagina, cargar_json_pagina
from .fase3_traducir import traducir_pagina, aplicar_traducciones
from .fase4_inpainting_render import aplicar_traduccion
from .fase5_imagenes_a_pdf import imagenes_a_pdf

logger = logging.getLogger(__name__)


def limpiar_directorios_temporales(work_dir: Path, debug: bool = False) -> None:
    """
    Elimina las subcarpetas raw/, jsons/ y render/ del directorio de trabajo
    si el modo debug está desactivado.

    Args:
        work_dir: Directorio de trabajo raíz.
        debug: Si True, conserva los archivos temporales.
    """
    if debug:
        logger.info("Modo debug activado: se conservan archivos temporales")
        return

    logger.info("Limpiando archivos temporales...")
    for subdir in ["raw", "jsons", "render"]:
        target = work_dir / subdir
        if target.exists() and target.is_dir():
            try:
                shutil.rmtree(target)
                logger.info(f"  ✓ Eliminado: {target}")
            except OSError as e:
                logger.error(f"  ✗ Error al eliminar {target}: {e}")
    logger.info("Limpieza de temporales completada.")


def copiar_con_manejo_errores(origen: Path, destino: Path) -> None:
    """
    Copia un archivo con manejo de errores de permisos NAS.

    Args:
        origen: Ruta del archivo origen.
        destino: Ruta del archivo destino.

    Raises:
        OSError: Si hay un error de permisos o E/S.
    """
    try:
        shutil.copyfile(origen, destino)
    except PermissionError as e:
        logger.error(
            f"Error de permisos al copiar {origen.name}: {e}. "
            "Verifica que el volumen montado tenga permisos de lectura/escritura."
        )
        raise
    except OSError as e:
        logger.error(
            f"Error de E/S al copiar {origen.name} a {destino}: {e}. "
            "Posible restricción del NAS o espacio en disco insuficiente."
        )
        raise


def procesar_tomo(
    pdf_path: str | Path,
    work_dir: str | Path,
    api_key: str,
    salida_pdf: str | Path | None = None,
    dpi: int = 250,
    force: bool = False,
    font_size_min: int = 8,
    manga_translator_cmd: str = "manga-image-translator",
    debug: bool = False,
) -> dict[str, Any]:
    """
    Procesa un tomo completo: PDF → imágenes → OCR → traducción → render → PDF.

    Args:
        pdf_path: Ruta al PDF de entrada.
        work_dir: Directorio de trabajo (se crean subcarpetas raw/ jsons/ render/).
        api_key: API key de DeepSeek (o variable de entorno DEEPSEEK_API_KEY).
        salida_pdf: Ruta del PDF final. Por defecto: {work_dir}/salida.pdf.
        dpi: Resolución para la extracción de imágenes.
        force: Si True, reprocesa todo ignorando archivos existentes.
        font_size_min: Tamaño mínimo de fuente para el render.
        manga_translator_cmd: Comando para invocar manga-image-translator.
        debug: Si True, conserva los archivos temporales.

    Returns:
        Dict con resumen del proceso: páginas_ok, páginas_fallo, fallos, tiempo_total.
    """
    pdf_path = Path(pdf_path)
    work_dir = Path(work_dir)
    salida_pdf = Path(salida_pdf) if salida_pdf else work_dir / f"{pdf_path.stem}_traducido.pdf"

    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise ValueError(
            "Se requiere API key de DeepSeek. "
            "Pásala como argumento o define DEEPSEEK_API_KEY en entorno."
        )

    inicio_total = time.time()

    # Crear estructura de directorios
    raw_dir = work_dir / "raw"
    json_dir = work_dir / "jsons"
    render_dir = work_dir / "render"
    for d in [raw_dir, json_dir, render_dir]:
        d.mkdir(parents=True, exist_ok=True)

    logger.info(f"{'='*60}")
    logger.info(f"Iniciando procesamiento de: {pdf_path.name}")
    logger.info(f"Directorio de trabajo: {work_dir}")
    logger.info(f"{'='*60}")

    pdf_generado = False

    try:
        # ─── Fase 1: PDF → imágenes ───────────────────────────────────────────
        if pdf_path.is_dir():
            logger.info(f"\n--- FASE 1: Carpeta de imágenes detectada, saltando extracción ---")
            imgs = []
            for ext in ["*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG", "*.JPEG"]:
                for img_file in sorted(pdf_path.glob(ext)):
                    dest = raw_dir / img_file.name
                    copiar_con_manejo_errores(img_file, dest)
                    if dest not in imgs:
                        imgs.append(dest)
            imgs.sort()
        else:
            logger.info(f"\n--- FASE 1: PDF → imágenes ({dpi} DPI) ---")
            imgs = pdf_a_imagenes(pdf_path, raw_dir, dpi=dpi)

        # ─── Fase 2 + 3 + 4: por cada página ──────────────────────────────────
        fallos = []
        for img_path in imgs:
            page_name = img_path.name
            json_path = json_dir / f"{img_path.stem}.json"
            json_translated = json_dir / f"{img_path.stem}_translated.json"
            render_img = render_dir / f"{img_path.stem}_es{img_path.suffix}"

            # Verificar si ya está completo (idempotencia)
            if not force and render_img.exists() and json_translated.exists():
                logger.info(f"✓ Página {page_name} ya procesada completamente, saltando")
                continue

            logger.info(f"\n--- Procesando: {page_name} ---")
            inicio_pagina = time.time()

            try:
                # Fase 2: Detección + OCR
                logger.info(f"  [Fase 2] Detectando texto...")
                page_data = detectar_y_ocr(img_path, work_dir, manga_translator_cmd, force=force)

                if not page_data.get("bubbles"):
                    logger.warning(f"  ⚠ No se detectaron globos en {page_name}")
                    # Guardar JSON vacío para no reprocesar
                    guardar_json_pagina(page_data, json_path)
                    # Copiar la imagen raw a render_dir para no saltarla en el PDF
                    copiar_con_manejo_errores(img_path, render_img)
                    continue

                # Fase 3: Traducción
                logger.info(f"  [Fase 3] Traduciendo {len(page_data['bubbles'])} textos...")
                traducciones = traducir_pagina(page_data["bubbles"], api_key)
                page_data = aplicar_traducciones(page_data, traducciones)

                # Guardar JSON con traducciones
                guardar_json_pagina(page_data, json_translated)

                # Fase 4: Inpainting + Render
                logger.info(f"  [Fase 4] Aplicando inpainting + render...")
                img_render = aplicar_traduccion(
                    img_path, page_data, work_dir,
                    manga_translator_cmd=manga_translator_cmd,
                    font_size_min=font_size_min,
                    force=force,
                )

                tiempo_pagina = time.time() - inicio_pagina
                logger.info(f"  ✓ Página {page_name} completada en {tiempo_pagina:.1f}s")

            except Exception as e:
                logger.error(f"  ✗ Error en página {page_name}: {e}", exc_info=True)
                fallos.append({"pagina": page_name, "error": str(e)})
                continue

        # ─── Fase 5: Imágenes → PDF ──────────────────────────────────────────
        logger.info(f"\n--- FASE 5: Generando PDF final ---")
        try:
            pdf_final = imagenes_a_pdf(render_dir, salida_pdf)
            pdf_generado = True
        except FileNotFoundError as e:
            logger.error(f"No hay imágenes renderizadas para generar PDF: {e}")
            pdf_final = None

        tiempo_total = time.time() - inicio_total

        # ─── Resumen ──────────────────────────────────────────────────────────
        paginas_ok = len(imgs) - len(fallos)
        resumen = {
            "pdf_entrada": str(pdf_path),
            "pdf_salida": str(pdf_final) if pdf_final else None,
            "total_paginas": len(imgs),
            "paginas_ok": paginas_ok,
            "paginas_fallo": len(fallos),
            "debug": debug,
            "fallos": fallos,
            "tiempo_total_segundos": round(tiempo_total, 1),
            "tiempo_total_formateado": formatear_tiempo(tiempo_total),
        }

        logger.info(f"\n{'='*60}")
        logger.info(f"PROCESO COMPLETADO")
        logger.info(f"  Total páginas:   {len(imgs)}")
        logger.info(f"  OK:              {paginas_ok}")
        logger.info(f"  Fallos:          {len(fallos)}")
        logger.info(f"  Tiempo total:    {resumen['tiempo_total_formateado']}")
        if fallos:
            logger.warning(f"  Páginas con error:")
            for f in fallos:
                logger.warning(f"    - {f['pagina']}: {f['error'][:100]}")
        logger.info(f"{'='*60}")

        return resumen

    finally:
        # ─── Limpieza automática ─────────────────────────────────────────────
        if pdf_generado and salida_pdf.exists():
            limpiar_directorios_temporales(work_dir, debug=debug)
        elif not debug:
            logger.info(
                "No se generó el PDF final o falló el proceso. "
                "Los archivos temporales se conservan para depuración."
            )


def formatear_tiempo(segundos: float) -> str:
    """Formatea segundos a HH:MM:SS."""
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    if horas > 0:
        return f"{horas}h {minutos}m {segs}s"
    elif minutos > 0:
        return f"{minutos}m {segs}s"
    else:
        return f"{segs}s"


def main():
    """Punto de entrada para ejecución por línea de comandos."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Traductor de Manga desde PDF (local)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python -m src.orquestador tomo01.pdf --work-dir ./work/tomo01\n"
            "  python -m src.orquestador tomo01.pdf --work-dir ./work/tomo01 --dpi 300 --force\n"
            "  python -m src.orquestador ./data/input/tomo01.pdf -o ./data/output/tomo01_es.pdf\n"
            "\nVariables de entorno:\n"
            "  DEEPSEEK_API_KEY  API key de DeepSeek (obligatoria)\n"
        ),
    )

    parser.add_argument("pdf", type=str, help="Ruta al PDF de entrada")
    parser.add_argument(
        "--work-dir", "-w",
        type=str,
        default="./work/tomo",
        help="Directorio de trabajo (default: ./work/tomo)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Ruta del PDF de salida (default: {work_dir}/salida.pdf)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=250,
        help="Resolución para imágenes (200-300, default: 250)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key de DeepSeek (alternativa a variable de entorno)",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Reprocesar todo ignorando archivos existentes",
    )
    parser.add_argument(
        "--font-size-min",
        type=int,
        default=8,
        help="Tamaño mínimo de fuente para render (default: 8)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Logging detallado (DEBUG)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Conservar archivos temporales tras la generación del PDF",
    )
    parser.add_argument(
        "--manga-translator-cmd",
        type=str,
        default="manga-image-translator",
        help="Comando para invocar manga-image-translator",
    )

    args = parser.parse_args()

    # Configurar logging
    nivel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolver API key
    from dotenv import load_dotenv
    load_dotenv()
    api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY", "")

    try:
        resumen = procesar_tomo(
            pdf_path=args.pdf,
            work_dir=args.work_dir,
            api_key=api_key,
            salida_pdf=args.output,
            dpi=args.dpi,
            force=args.force,
            font_size_min=args.font_size_min,
            manga_translator_cmd=args.manga_translator_cmd,
            debug=args.debug,
        )

        # Guardar resumen JSON
        resumen_path = Path(args.work_dir) / "resumen.json"
        with open(resumen_path, "w", encoding="utf-8") as f:
            json.dump(resumen, f, ensure_ascii=False, indent=2)
        logger.info(f"Resumen guardado en: {resumen_path}")

        # Salida con código de error si hubo fallos
        if resumen["paginas_fallo"] > 0:
            sys.exit(1)

    except Exception as e:
        logger.critical(f"Error fatal: {e}", exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()