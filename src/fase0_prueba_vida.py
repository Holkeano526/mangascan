"""
Fase 0 — Prueba de vida
Valida que el hardware funciona y la calidad es aceptable.
NO SALTAR esta fase.
"""

import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def prueba_rapida(
    imagenes_prueba: list[str | Path],
    api_key: str | None = None,
    manga_translator_cmd: str = "manga-image-translator",
) -> dict:
    """
    Prueba rápida del pipeline con 2-3 imágenes de manga real.

    1. Verifica que manga-image-translator está instalado.
    2. Ejecuta detección+OCR sobre las imágenes.
    3. Si hay API key, prueba traducción con DeepSeek.
    4. Mide tiempo por página y reporta resultados.

    Args:
        imagenes_prueba: Lista de rutas a imágenes de prueba.
        api_key: API key opcional para probar traducción.
        manga_translator_cmd: Comando para invocar el traductor.

    Returns:
        Dict con resultados de la prueba.
    """
    from .fase1_pdf_a_imagenes import pdf_a_imagenes
    from .fase2_detectar_ocr import detectar_y_ocr
    from .fase3_traducir import traducir_pagina

    resultados = {
        "manga_translator_instalado": False,
        "ocr_funciona": False,
        "traduccion_funciona": False,
        "tiempo_por_pagina": [],
        "errores": [],
    }

    logger.info("Verificando instalación de manga-image-translator...")
    import subprocess
    cmd_list = [sys.executable, "-m", "manga_translator"]
    try:
        result = subprocess.run(
            cmd_list + ["local", "--help"],
            capture_output=True, text=True, encoding='utf-8', timeout=30,
            cwd=str(Path(__file__).parent.parent / "manga-image-translator-src")
        )
        if result.returncode == 0:
            resultados["manga_translator_instalado"] = True
            logger.info("✓ manga-image-translator instalado")
        else:
            logger.warning(f"⚠ manga-image-translator devolvió código {result.returncode}")
            logger.info(f"Salida: {result.stdout[:200]}")
    except FileNotFoundError:
        logger.error(
            f"✗ manga-image-translator NO encontrado como '{manga_translator_cmd}'.\n"
            "  Instálalo con: pip install manga-image-translator\n"
            "  O revisa su documentación en github.com/zyddnys/manga-image-translator"
        )
        resultados["errores"].append("manga-image-translator no instalado")
        return resultados

    # 2. Probar OCR con las imágenes
    logger.info(f"\nProbando OCR con {len(imagenes_prueba)} imágenes...")
    for img_path in imagenes_prueba:
        img = Path(img_path)
        if not img.exists():
            logger.warning(f"  Imagen no encontrada: {img}, saltando")
            continue

        logger.info(f"  Procesando: {img.name}")
        inicio = time.time()

        try:
            work_dir = img.parent / "_fase0_prueba"
            page_data = detectar_y_ocr(img, work_dir, manga_translator_cmd)

            tiempo = time.time() - inicio
            
            # Since manga-image-translator no longer exports JSON natively in this version,
            # we verify success by checking if the output image was generated.
            render_dir = work_dir / "render"
            output_img = render_dir / img.name
            if output_img.exists():
                resultados["ocr_funciona"] = True
                logger.info(f"    ✓ Traducción end-to-end exitosa en {tiempo:.1f}s")
                logger.info(f"      Imagen generada en: {output_img}")
            else:
                logger.warning(f"    ⚠ No se generó la imagen de salida para {img.name}")

            # Agregar tiempo a resultados
            resultados["tiempo_por_pagina"].append({
                "imagen": img.name,
                "tiempo_segundos": round(tiempo, 1),
                "globos_detectados": 0, # Cannot know without JSON
            })

        except Exception as e:
            logger.error(f"    ✗ Error en OCR para {img.name}: {e}")
            resultados["errores"].append(f"OCR falló en {img.name}: {e}")

    # 3. Probar traducción (si hay API key)
    if api_key and resultados["ocr_funciona"]:
        logger.info("\nProbando traducción con DeepSeek...")
        try:
            # Usar el primer JSON generado
            work_dir = Path(imagenes_prueba[0]).parent / "_fase0_prueba"
            jsons = list((work_dir / "jsons").glob("*.json"))
            if jsons:
                import json
                with open(jsons[0], "r", encoding="utf-8") as f:
                    page_data = json.load(f)
                if page_data.get("bubbles"):
                    traducciones = traducir_pagina(page_data["bubbles"], api_key)
                    resultados["traduccion_funciona"] = True
                    logger.info(f"  ✓ Traducción exitosa: {len(traducciones)} textos")
                    for id_str, txt in list(traducciones.items())[:3]:
                        original = next(
                            (b["src"] for b in page_data["bubbles"] if str(b["id"]) == id_str),
                            "?"
                        )
                        logger.info(f"    {original} → {txt}")
        except Exception as e:
            logger.error(f"  ✗ Error en traducción: {e}")
            resultados["errores"].append(f"Traducción falló: {e}")
    elif not api_key:
        logger.info("\n⚠ Sin API key: saltando prueba de traducción")

    # 4. Reporte final
    logger.info(f"\n{'='*50}")
    logger.info("RESULTADOS DE LA PRUEBA DE VIDA (FASE 0)")
    logger.info(f"{'='*50}")
    logger.info(f"  manga-image-translator: {'✓' if resultados['manga_translator_instalado'] else '✗'}")
    logger.info(f"  OCR funciona:           {'✓' if resultados['ocr_funciona'] else '✗'}")
    logger.info(f"  Traducción funciona:    {'✓' if resultados['traduccion_funciona'] else '?'}")
    logger.info(f"  Tiempo promedio/página: {_tiempo_promedio(resultados['tiempo_por_pagina'])}")

    if resultados["tiempo_por_pagina"]:
        t_total = sum(t["tiempo_segundos"] for t in resultados["tiempo_por_pagina"])
        t_prom = t_total / len(resultados["tiempo_por_pagina"])
        logger.info(f"\n  Estimación para tomo de 180 páginas:")
        logger.info(f"    Tiempo estimado: {t_prom * 180:.0f}s = {t_prom * 180 / 60:.1f} min")
        logger.info(f"    (asumiendo {t_prom:.1f}s por página)")

    if resultados["errores"]:
        logger.warning(f"\n  Errores encontrados:")
        for e in resultados["errores"]:
            logger.warning(f"    • {e}")
    else:
        logger.info(f"\n  ✓ Sin errores. Puedes continuar con el pipeline completo.")

    logger.info(f"{'='*50}")
    return resultados


def _tiempo_promedio(tiempos: list) -> str:
    if not tiempos:
        return "N/A"
    prom = sum(t["tiempo_segundos"] for t in tiempos) / len(tiempos)
    return f"{prom:.1f}s"


def main():
    """Punto de entrada para la prueba de vida."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fase 0 — Prueba de vida del Traductor de Manga",
    )
    parser.add_argument(
        "imagenes",
        nargs="+",
        help="Rutas a 2-3 imágenes de manga japonés para probar",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key de DeepSeek (opcional para probar traducción)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Logging detallado",
    )

    args = parser.parse_args()

    nivel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    prueba_rapida(args.imagenes, api_key=args.api_key)


if __name__ == "__main__":
    main()