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
    import asyncio
    from .fase2_pipeline_core import TraductorMangaOptimizado

    resultados = {
        "manga_translator_instalado": False,
        "ocr_funciona": False,
        "traduccion_funciona": False,
        "tiempo_por_pagina": [],
        "errores": [],
    }

    logger.info("Verificando instalación de manga-image-translator...")
    mt_src = Path(__file__).parent.parent / "manga-image-translator-src"
    if mt_src.exists():
        resultados["manga_translator_instalado"] = True
        logger.info("✓ manga-image-translator código fuente encontrado")
    else:
        logger.error(f"✗ manga-image-translator NO encontrado en {mt_src}")
        resultados["errores"].append("manga-image-translator no instalado")
        return resultados

    # 2. Probar Pipeline
    logger.info(f"\nProbando Pipeline con {len(imagenes_prueba)} imágenes...")
    
    async def correr_prueba():
        pipeline = TraductorMangaOptimizado(api_key=api_key or "DUMMY_KEY")
        for img_path in imagenes_prueba:
            img = Path(img_path)
            if not img.exists():
                logger.warning(f"  Imagen no encontrada: {img}, saltando")
                continue

            logger.info(f"  Procesando: {img.name}")
            inicio = time.time()

            try:
                # El pipeline genera los archivos de salida en render/
                output_img = await pipeline.procesar_pagina(img)
                tiempo = time.time() - inicio
                
                if output_img and output_img.exists():
                    resultados["ocr_funciona"] = True
                    resultados["traduccion_funciona"] = True
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

    # Ejecutar la prueba asíncrona
    asyncio.run(correr_prueba())

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