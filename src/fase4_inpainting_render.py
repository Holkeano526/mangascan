"""
Fase 4 — Reinserción (inpainting + render)
Usa manga-image-translator para borrar el japonés y dibujar el español.
"""

import json
from pathlib import Path
import logging
import sys
import asyncio
from typing import Any
from .fase2_detectar_ocr import guardar_json_pagina

logger = logging.getLogger(__name__)

def aplicar_traduccion(
    imagen_path: str | Path,
    page_data: dict[str, Any],
    work_dir: str | Path,
    manga_translator_cmd: str = "manga-image-translator",
    font_size_min: int = 8,
    force: bool = False,
) -> Path:
    """
    Ejecuta inpainting + render con las traducciones ya asignadas de forma programática.
    """
    img_path = Path(imagen_path)
    render_dir = Path(work_dir) / "render"
    render_dir.mkdir(parents=True, exist_ok=True)

    img_es = render_dir / f"{img_path.stem}_es{img_path.suffix}"

    if not force and img_es.exists():
        logger.info(f"Render ya existe para {img_path.name}, reutilizando")
        return img_es

    json_path = Path(work_dir) / "jsons" / f"{img_path.stem}_translated.json"
    guardar_json_pagina(page_data, json_path)

    mt_src = Path(__file__).parent.parent / "manga-image-translator-src"
    if str(mt_src.resolve()) not in sys.path:
        sys.path.insert(0, str(mt_src.resolve()))

    from manga_translator import MangaTranslator, Config, Context
    from manga_translator.utils import load_image, dump_image
    from PIL import Image

    logger.info(f"Ejecutando inpainting+render para: {img_path.name}...")
    
    # Desactivamos el logger de manga_translator
    logging.getLogger('manga_translator').setLevel(logging.ERROR)

    img = Image.open(img_path)
    
    config = Config()
    config.translator.translator = "none" # Saltar traducción interna
    config.inpainter.inpainter = "default"
    config.render.renderer = "manga2eng_pillow"
    config.render.alignment = "center"
    config.render.direction = "h"
    
    translator = MangaTranslator({"kernel_size": 3})
    
    async def process():
        ctx = Context()
        ctx.input = img
        ctx.result = None
        
        ctx.upscaled = img
        ctx.img_colorized = img
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
        
        # Debemos volver a detectar para tener las regiones exactas (Quadrilateral)
        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
        ctx.textlines = await translator._run_ocr(config, ctx)
        ctx.text_regions = await translator._run_textline_merge(config, ctx)
        
        # Inyectar traducciones desde el JSON
        bubbles = page_data.get('bubbles', [])
        for region in ctx.text_regions:
            # Por defecto usamos el texto original si no hay traducción
            region.translation = region.text
            region.target_lang = "ESP"  # Asumimos español
            region._alignment = config.render.alignment
            region._direction = config.render.direction

            # Buscar en las burbujas guardadas
            for bubble in bubbles:
                # Se asocia por el texto (o se podría usar el id o el box)
                if bubble['src'] == region.text:
                    if bubble['dst']:
                        region.translation = bubble['dst']
                    break
        
        # Continuar con el pipeline
        if ctx.mask is None:
            ctx.mask = await translator._run_mask_refinement(config, ctx)
        ctx.img_inpainted = await translator._run_inpainting(config, ctx)
        ctx.img_rendered = await translator._run_text_rendering(config, ctx)
        
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
        return ctx.result

    try:
        result_img = asyncio.run(process())
    except Exception as e:
        logger.error(f"Inpainting+render falló: {e}")
        raise RuntimeError(f"Error en inpainting/render: {e}")

    # Guardamos la imagen final
    if img_es.suffix.lower() == '.jpg' or img_es.suffix.lower() == '.jpeg':
        result_img = result_img.convert('RGB')
    result_img.save(img_es)

    logger.info(f"Render completado para {img_path.name} → {img_es.name}")

    if not img_es.exists():
        raise RuntimeError(f"No se generó imagen de salida para {img_path.name} en {render_dir}")

    return img_es