"""
Pipeline asíncrono continuo — cero serialización a disco entre fases.
Mantiene una única instancia de MangaTranslator viva en memoria (singleton).
"""
import asyncio
import logging
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# ─── DeepSeek constants ───────────────────────────────────────────────
API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODELO = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
PATRON_ASIATICO = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')

SYSTEM_PROMPT = (
    "Actúa como un traductor profesional de manga. Tu objetivo es traducir los siguientes textos al ESPAÑOL LATINO.\n"
    "REGLA DE ORO: La respuesta DEBE estar completamente en idioma ESPAÑOL.\n"
    "Está ESTRICTAMENTE PROHIBIDO incluir caracteres kanji, hiragana, katakana o caracteres chinos (hanzi) en tus traducciones.\n"
    "Debes traducir cada frase, adaptando las expresiones al contexto.\n"
    "Devuelve ÚNICAMENTE un JSON válido con este formato exacto: {\"0\": \"texto en español\", \"1\": \"texto en español\"}\n"
    "No añadas texto extra, ni explicaciones, ni comillas markdown."
)


class TraductorMangaOptimizado:
    """Pipeline completo en memoria. Los modelos ML se cargan UNA vez y se reutilizan."""

    def __init__(self, api_key: str, font_size_min: int = 8):
        self.api_key = api_key
        self.font_size_min = font_size_min
        self._translator = None  # lazy singleton
        self._config_cache = None

    def _get_translator(self):
        """Retorna la instancia singleton de MangaTranslator."""
        if self._translator is None:
            mt_src = Path(__file__).parent.parent / "manga-image-translator-src"
            if str(mt_src.resolve()) not in sys.path:
                sys.path.insert(0, str(mt_src.resolve()))

            from manga_translator import MangaTranslator
            logging.getLogger('manga_translator').setLevel(logging.ERROR)
            self._translator = MangaTranslator({"kernel_size": 3})
            logger.info("MangaTranslator instanciado (singleton)")
        return self._translator

    def _get_config(self, mode: str = "full"):
        """Config con thresholds sincronizados. mode='detect' salta traducción/inpainting."""
        from manga_translator import Config

        if self._config_cache is None:
            cfg = Config()
            cfg.detector.text_threshold = 0.3
            cfg.detector.box_threshold = 0.5
            cfg.render.renderer = "manga2eng_pillow"
            cfg.render.alignment = "center"
            cfg.render.direction = "h"
            self._config_cache = cfg

        cfg = self._config_cache
        if mode == "detect":
            cfg.translator.translator = "none"
            cfg.inpainter.inpainter = "none"
            cfg.upscale.upscaler = "none"
        else:
            cfg.translator.translator = "none"
            cfg.inpainter.inpainter = "default"
            cfg.render.renderer = "manga2eng_pillow"
            cfg.render.alignment = "center"
            cfg.render.direction = "h"
        return cfg

    async def procesar_pagina(self, img_path: Path) -> Path:
        """
        Pipeline completo para una página: detección → OCR → traducción → inpainting → render.
        Retorna la ruta de la imagen renderizada.
        """
        from manga_translator import Context
        from manga_translator.utils import load_image, dump_image

        translator = self._get_translator()
        img = Image.open(img_path)

        # ── 1 y 2: Detección + OCR ────────────────────────────────────
        cfg_detect = self._get_config("detect")
        ctx = Context()
        ctx.input = img
        ctx.result = None
        ctx.upscaled = img
        ctx.img_colorized = img
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(cfg_detect, ctx)
        ctx.textlines = await translator._run_ocr(cfg_detect, ctx)
        ctx.text_regions = await translator._run_textline_merge(cfg_detect, ctx)

        if not ctx.text_regions:
            logger.warning(f"  ⚠ Sin texto detectado en {img_path.name}")
            # Devolver imagen original como render
            render_dir = img_path.parent.parent / "render"
            render_dir.mkdir(parents=True, exist_ok=True)
            dest = render_dir / f"{img_path.stem}_es{img_path.suffix}"
            img.save(dest)
            return dest

        # ── 3: Traducción DeepSeek (síncrona, en asyncio executor) ────
        bubbles = []
        for i, region in enumerate(ctx.text_regions):
            x, y, w, h = region.xywh
            bubbles.append({"id": i, "src": region.text})

        traducciones = await self._traducir_pagina_async(bubbles)

        # ── 4: Inpainting + Render ────────────────────────────────────
        cfg_full = self._get_config("full")
        for i, region in enumerate(ctx.text_regions):
            region.translation = traducciones.get(str(i), region.text)
            region.target_lang = "ESP"
            region._alignment = cfg_full.render.alignment
            region._direction = cfg_full.render.direction

        if ctx.mask is None:
            ctx.mask = await translator._run_mask_refinement(cfg_full, ctx)
        ctx.img_inpainted = await translator._run_inpainting(cfg_full, ctx)
        ctx.img_rendered = await translator._run_text_rendering(cfg_full, ctx)

        result_img = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)

        render_dir = img_path.parent.parent / "render"
        render_dir.mkdir(parents=True, exist_ok=True)
        dest = render_dir / f"{img_path.stem}_es{img_path.suffix}"
        if dest.suffix.lower() in ('.jpg', '.jpeg'):
            result_img = result_img.convert('RGB')
        result_img.save(dest)
        logger.info(f"  ✓ Render: {img_path.name} → {dest.name}")
        return dest

    async def _traducir_pagina_async(self, bubbles: list[dict]) -> dict[str, str]:
        """Traducción DeepSeek envuelta en async (usa executor para requests síncronas)."""
        if not bubbles:
            return {}

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._traducir_pagina_sync, bubbles)

    def _traducir_pagina_sync(self, bubbles: list[dict]) -> dict[str, str]:
        """Wrapper síncrono para llamar a DeepSeek."""
        entrada = {str(b["id"]): b["src"] for b in bubbles}
        payload = {
            "model": MODELO,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(entrada, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        max_retries = 2
        ultimo_error = None
        for intento in range(max_retries + 1):
            try:
                r = requests.post(
                    API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120,
                )
                if r.status_code == 429:
                    retry_after = int(r.headers.get("Retry-After", 60))
                    logger.warning(f"HTTP 429, esperando {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                r.raise_for_status()
                contenido = r.json()["choices"][0]["message"]["content"]
                contenido = contenido.strip().removeprefix("```json").removeprefix("```")
                contenido = contenido.removesuffix("```").strip()
                traducciones = json.loads(contenido)

                for k, v in traducciones.items():
                    if PATRON_ASIATICO.search(str(v)):
                        raise ValueError(f"Caracteres asiáticos en ID {k}: {v[:80]}")

                ids_originales = set(entrada.keys())
                ids_traducidos = set(traducciones.keys())
                for fid in ids_originales - ids_traducidos:
                    traducciones[fid] = ""
                logger.info(f"Traducción: {len(traducciones)} textos")
                return traducciones

            except (json.JSONDecodeError, ValueError) as e:
                ultimo_error = e
                if intento < max_retries:
                    time.sleep(2 ** intento)
                    continue
            except requests.RequestException as e:
                status = getattr(getattr(e, 'response', None), 'status_code', None)
                if status and 400 <= status < 500 and status not in (408, 429):
                    raise
                if intento < max_retries:
                    time.sleep(2 ** intento)
                    continue
                ultimo_error = e

        raise ValueError(f"Traducción falló tras {max_retries + 1} intentos: {ultimo_error}")