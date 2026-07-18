"""
Fase 2 — Detección + OCR
Pasa cada imagen por manga-image-translator para obtener
cajas de texto y texto japonés.
"""

import json
from pathlib import Path
import logging
import sys
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

# Estructura del JSON por página (contrato entre fases):
# {
#   "page": "page_0001.png",
#   "width": 1240,
#   "height": 1754,
#   "bubbles": [
#     { "id": 0, "box": [x1, y1, x2, y2], "src": "...", "dst": None },
#     ...
#   ]
# }


def detectar_y_ocr(
    imagen_path: str | Path,
    work_dir: str | Path,
    manga_translator_cmd: str = "manga-image-translator",
    force: bool = False,
) -> dict[str, Any]:
    """
    Ejecuta manga-image-translator programáticamente sobre una imagen para detectar globos
    y extraer texto japonés.
    """
    img_path = Path(imagen_path)
    json_path = Path(work_dir) / "jsons" / f"{img_path.stem}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)

    if not force and json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            logger.info(f"JSON ya existe para {img_path.name}, reutilizando")
            return data

    # Añadir manga_translator al path
    mt_src = Path(__file__).parent.parent / "manga-image-translator-src"
    if str(mt_src.resolve()) not in sys.path:
        sys.path.insert(0, str(mt_src.resolve()))

    from manga_translator import MangaTranslator, Config, Context
    from manga_translator.utils import load_image
    from PIL import Image

    logger.info(f"Detectando y haciendo OCR en {img_path.name}...")
    
    # Desactivamos el logger de manga_translator para no ensuciar
    logging.getLogger('manga_translator').setLevel(logging.ERROR)

    img = Image.open(img_path)
    
    config = Config()
    config.translator.translator = "none" # Saltar traducción
    config.inpainter.inpainter = "none"   # Saltar inpainting
    config.upscale.upscaler = "none"      # Saltar upscaling
    
    translator = MangaTranslator({"kernel_size": 3})
    
    async def process():
        ctx = Context()
        ctx.input = img
        ctx.result = None
        
        ctx.upscaled = img
        ctx.img_colorized = img
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
        
        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
        ctx.textlines = await translator._run_ocr(config, ctx)
        ctx.text_regions = await translator._run_textline_merge(config, ctx)
        return ctx

    try:
        ctx = asyncio.run(process())
    except Exception as e:
        logger.error(f"Error procesando OCR: {e}")
        raise

    bubbles = []
    if ctx.text_regions:
        for i, region in enumerate(ctx.text_regions):
            x, y, w, h = region.xywh
            bubbles.append({
                "id": i,
                "box": [int(x), int(y), int(x + w), int(y + h)],
                "src": region.text,
                "dst": ""
            })

    data = {
        "page": img_path.name,
        "width": img.width,
        "height": img.height,
        "bubbles": bubbles,
    }

    guardar_json_pagina(data, json_path)
    logger.info(f"OCR completado para {img_path.name}. Encontrados {len(bubbles)} globos.")
    return data

def cargar_json_pagina(json_path: str | Path) -> dict[str, Any]:
    """Carga un JSON de página ya procesado."""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def guardar_json_pagina(data: dict[str, Any], json_path: str | Path) -> None:
    """Guarda el JSON de una página."""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)