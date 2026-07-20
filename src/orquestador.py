"""
Orquestador — Despachador elegante de tareas asyncio.
Usa el Pipeline Core (translator_engine.py) que mantiene los modelos ML vivos.
"""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .pdf_extractor import pdf_a_imagenes
from .pdf_builder import imagenes_a_pdf
from .translator_engine import TraductorMangaOptimizado

logger = logging.getLogger(__name__)


def copiar_con_manejo_errores(origen: Path, destino: Path) -> None:
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


async def procesar_tomo_async(
    pdf_path: Path,
    work_dir: Path,
    api_key: str,
    salida_pdf: Path,
    dpi: int = 250,
    force: bool = False,
    font_size_min: int = 8,
    debug: bool = False,
    fast_mode: bool = False,
) -> dict[str, Any]:
    """Procesa un tomo completo con pipeline en memoria (asíncrono)."""
    raw_dir = work_dir / "raw"
    render_dir = work_dir / "render"
    raw_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"{'='*60}")
    logger.info(f"Iniciando: {pdf_path.name}")
    logger.info(f"Work dir: {work_dir}")
    logger.info(f"{'='*60}")

    # ── Extracción: PDF → imágenes ─────────────────────────────────────────
    if pdf_path.is_dir():
        logger.info("Carpeta de imágenes, saltando extracción PDF")
        imgs = []
        for ext in ["*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG", "*.JPEG"]:
            for img_file in sorted(pdf_path.glob(ext)):
                dest = raw_dir / img_file.name
                copiar_con_manejo_errores(img_file, dest)
                if dest not in imgs:
                    imgs.append(dest)
        imgs.sort()
    else:
        logger.info(f"PDF → imágenes ({dpi} DPI)")
        imgs = pdf_a_imagenes(pdf_path, raw_dir, dpi=dpi)

    if not imgs:
        raise RuntimeError("No se extrajeron imágenes del PDF o carpeta.")

    # ── Fase A: Preparación (Detección, OCR, Traducción) ───────────────
    pipeline = TraductorMangaOptimizado(api_key, font_size_min)
    fallos = []
    
    # Lista global para guardar textos de todo el tomo
    todas_las_paginas = []
    
    logger.info("\n--- FASE A: Analizando textos de todo el tomo ---")
    for img_path in imgs:
        render_img = render_dir / f"{img_path.stem}_es{img_path.suffix}"
        if not force and render_img.exists():
            logger.info(f"✓ {img_path.name} ya procesada, saltando análisis")
            continue

        logger.info(f"Analizando: {img_path.name}")
        try:
            res_pagina = await pipeline.preparar_pagina(img_path, fast_mode=fast_mode)
            todas_las_paginas.append(res_pagina)
        except Exception as e:
            logger.error(f"  ✗ Error al analizar {img_path.name}: {e}", exc_info=True)
            fallos.append({"pagina": img_path.name, "error": str(e)})

    # Guardar traducciones para que el Web Server las lea
    json_path = work_dir / "traducciones.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(todas_las_paginas, f, ensure_ascii=False, indent=2)
        
    # Convertir a un diccionario { "pagina.png": { "0": "texto", "1": "texto" } }
    traducciones_dict = {}
    for pag in todas_las_paginas:
        traducciones_dict[pag["pagina"]] = {str(b["id"]): b["translation"] for b in pag["bubbles"]}

    # ── Fase B: Renderizado e Inpainting ───────────────────────────────
    logger.info("\n--- FASE B: Redibujando páginas ---")
    for img_path in imgs:
        render_img = render_dir / f"{img_path.stem}_es{img_path.suffix}"
        if not force and render_img.exists():
            continue

        if img_path.name not in pipeline.contexts:
            # Saltado o fallo previo
            shutil.copy2(img_path, render_img)
            continue
            
        inicio = time.time()
        try:
            textos_editados = traducciones_dict.get(img_path.name, {})
            await pipeline.renderizar_pagina(img_path, textos_editados, fast_mode=fast_mode)
            logger.info(f"  ✓ {img_path.name} redibujada en {time.time() - inicio:.1f}s")
        except Exception as e:
            logger.error(f"  ✗ {img_path.name}: {e}", exc_info=True)
            fallos.append({"pagina": img_path.name, "error": str(e)})
            shutil.copy2(img_path, render_img)

    # ── Ensamblaje: PDF final ──────────────────────────────────────────────
    logger.info("\n--- Generando PDF ---")
    pdf_final = imagenes_a_pdf(render_dir, salida_pdf)

    resumen = {
        "pdf_entrada": str(pdf_path),
        "pdf_salida": str(pdf_final) if pdf_final else None,
        "total_paginas": len(imgs),
        "paginas_ok": len(imgs) - len(fallos),
        "paginas_fallo": len(fallos),
        "fallos": fallos,
    }

    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETADO: {resumen['paginas_ok']}/{resumen['total_paginas']}")
    if fallos:
        for f in fallos:
            logger.warning(f"  - {f['pagina']}: {f['error'][:100]}")
    logger.info(f"{'='*60}")

    if not debug and pdf_final and pdf_final.exists():
        shutil.rmtree(raw_dir, ignore_errors=True)
        shutil.rmtree(render_dir, ignore_errors=True)
        logger.info("Temporales eliminados.")

    return resumen


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
    fast_mode: bool = False,
) -> dict[str, Any]:
    """Wrapper síncrono para compatibilidad."""
    pdf_path = Path(pdf_path)
    work_dir = Path(work_dir)
    salida_pdf = Path(salida_pdf) if salida_pdf else work_dir / f"{pdf_path.stem}_traducido.pdf"

    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise ValueError("Se requiere DEEPSEEK_API_KEY en argumento o variable de entorno.")

    return asyncio.run(procesar_tomo_async(
        pdf_path, work_dir, api_key, salida_pdf, dpi, force, font_size_min, debug, fast_mode
    ))


def formatear_tiempo(segundos: float) -> str:
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    if horas > 0:
        return f"{horas}h {minutos}m {segs}s"
    elif minutos > 0:
        return f"{minutos}m {segs}s"
    return f"{segs}s"


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Traductor de Manga desde PDF (pipeline optimizado)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python -m src.orquestador tomo01.pdf --work-dir ./work/tomo01\n"
            "  python -m src.orquestador tomo01.pdf --work-dir ./work/tomo01 --dpi 300 --force\n"
            "\nVariables de entorno:\n"
            "  DEEPSEEK_API_KEY  API key de DeepSeek (obligatoria)\n"
        ),
    )

    parser.add_argument("pdf", type=str, help="Ruta al PDF de entrada")
    parser.add_argument("--work-dir", "-w", type=str, default="./work/tomo", help="Directorio de trabajo")
    parser.add_argument("--output", "-o", type=str, default=None, help="Ruta del PDF de salida")
    parser.add_argument("--dpi", type=int, default=250, help="Resolución (200-300, default: 250)")
    parser.add_argument("--api-key", type=str, default=None, help="API key de DeepSeek")
    parser.add_argument("--force", "-f", action="store_true", help="Reprocesar todo")
    parser.add_argument("--font-size-min", type=int, default=8, help="Tamaño mínimo de fuente")
    parser.add_argument("--verbose", "-v", action="store_true", help="Logging DEBUG")
    parser.add_argument("--debug", action="store_true", help="Conservar temporales")
    parser.add_argument("--fast", action="store_true", help="Modo Rápido (sin inpainting)")

    args = parser.parse_args()

    nivel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=nivel, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

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
            debug=args.debug,
            fast_mode=args.fast,
        )
        resumen_path = Path(args.work_dir) / "resumen.json"
        resumen_path.parent.mkdir(parents=True, exist_ok=True)
        with open(resumen_path, "w", encoding="utf-8") as f:
            json.dump(resumen, f, ensure_ascii=False, indent=2)
        logger.info(f"Resumen guardado en: {resumen_path}")
        if resumen["paginas_fallo"] > 0:
            sys.exit(1)
    except Exception as e:
        logger.critical(f"Error fatal: {e}", exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()