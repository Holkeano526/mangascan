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
    "IMPORTANTE: Como a menudo no hay contexto visual, si el género de quien habla es ambiguo, utiliza LENGUAJE NEUTRO o evita adjetivos con género marcado (ej. 'me tranquilizo' en lugar de 'estoy tranquilo').\n"
    "Debes traducir cada frase, adaptando las expresiones al contexto.\n"
    "Devuelve ÚNICAMENTE un JSON válido con este formato exacto: {\"0\": \"texto en español\", \"1\": \"texto en español\"}\n"
    "No añadas texto extra, ni explicaciones, ni comillas markdown."
)


def parsear_traduccion(contenido: str, ids_originales: set[str]) -> dict[str, str]:
    """Limpia y valida la respuesta del modelo. Función pura (testeable).

    - Quita cercas markdown (```json ... ```).
    - Rechaza traducciones con caracteres asiáticos (kanji/kana/hanzi).
    - Rellena con "" los ids que el modelo haya omitido.
    """
    contenido = contenido.strip().removeprefix("```json").removeprefix("```")
    contenido = contenido.removesuffix("```").strip()
    traducciones = json.loads(contenido)

    for k, v in traducciones.items():
        if PATRON_ASIATICO.search(str(v)):
            raise ValueError(f"Caracteres asiáticos en ID {k}: {str(v)[:80]}")

    for fid in ids_originales - set(traducciones.keys()):
        traducciones[fid] = ""
    return traducciones


class TraductorMangaOptimizado:
    """Pipeline completo en memoria. Los modelos ML se cargan UNA vez y se reutilizan."""

    def __init__(self, api_key: str, font_size_min: int = 8):
        self.api_key = api_key
        self.font_size_min = font_size_min
        self._translator = None  # lazy singleton
        self._config_cache = None
        self.contexts: dict = {}  # contexto por página (liberado tras renderizar)

    def _get_translator(self):
        """Retorna la instancia singleton de MangaTranslator."""
        if self._translator is None:
            base_dir = Path(__file__).parent.parent
            # Soportar entorno local (WSL/Windows) y entorno Docker NAS
            mt_rutas = [
                base_dir / "manga-image-translator-src",
                base_dir / "manga-image-translator",
                Path("/app/manga-image-translator")
            ]
            
            for ruta in mt_rutas:
                if ruta.exists():
                    if str(ruta.resolve()) not in sys.path:
                        sys.path.insert(0, str(ruta.resolve()))
                    break

            # Crear directorio de caché seguro si estamos en Docker
            if Path("/config").exists():
                Path("/config/models").mkdir(parents=True, exist_ok=True)

            from manga_translator import MangaTranslator
            logging.getLogger('manga_translator').setLevel(logging.ERROR)
            self._translator = MangaTranslator({"kernel_size": 3})
            logger.info("MangaTranslator instanciado (singleton)")
        return self._translator

    def _get_config(self, mode: str = "full", fast_mode: bool = False):
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
            cfg.inpainter.inpainter = "none" if fast_mode else "default"
            cfg.inpainter.inpainting_size = 1024  # LIMITE ESTRICTO DE MEMORIA RAM (NAS)
            cfg.render.renderer = "manga2eng_pillow"
            cfg.render.alignment = "center"
            cfg.render.direction = "h"
        return cfg

    async def preparar_pagina(self, img_path: Path, fast_mode: bool = False) -> dict:
        """Fase A: Detección, OCR y Traducción. Retorna los textos propuestos."""
        translator = self._get_translator()
        from manga_translator import Context
        from manga_translator.utils import load_image

        img = Image.open(img_path)
        cfg_detect = self._get_config("detect", fast_mode=fast_mode)
        ctx = Context()
        ctx.input = img
        ctx.upscaled = img
        ctx.img_colorized = img
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(cfg_detect, ctx)
        ctx.textlines = await translator._run_ocr(cfg_detect, ctx)
        ctx.text_regions = await translator._run_textline_merge(cfg_detect, ctx)

        bubbles = []
        if not ctx.text_regions:
            logger.warning(f"  ⚠ Sin texto detectado en {img_path.name}")
        else:
            for i, region in enumerate(ctx.text_regions):
                bubbles.append({"id": i, "src": region.text})

            traducciones = await self._traducir_pagina_async(bubbles)
            for i, region in enumerate(ctx.text_regions):
                region.translation = traducciones.get(str(i), region.text)
            for b in bubbles:
                b["translation"] = traducciones.get(str(b["id"]), b["src"])

        # Liberar memoria pesada (las imágenes) para no saturar RAM en pausas largas
        ctx.input = None
        ctx.upscaled = None
        ctx.img_colorized = None
        ctx.img_rgb = None
        ctx.img_alpha = None
        
        self.contexts[img_path.name] = ctx
        return {"pagina": img_path.name, "bubbles": bubbles}

    def liberar_contexto(self, nombre: str) -> None:
        """Descarta el contexto de una página para liberar RAM."""
        self.contexts.pop(nombre, None)

    async def renderizar_pagina(self, img_path: Path, fast_mode: bool = False) -> Path:
        """Fase B: Inpainting y Renderizado final (usa la traducción ya fijada en preparar_pagina)."""
        translator = self._get_translator()
        from manga_translator.utils import dump_image, load_image
        
        ctx = self.contexts.get(img_path.name)
        if not ctx:
            raise ValueError(f"Contexto no encontrado para {img_path.name}. ¿Se saltó la fase A?")
            
        # Rehidratar imágenes
        img = Image.open(img_path)
        ctx.input = img
        ctx.upscaled = img
        ctx.img_colorized = img
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        cfg_full = self._get_config("full", fast_mode=fast_mode)
        for region in ctx.text_regions or []:
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
        
        # Limpiar contexto de la memoria
        del self.contexts[img_path.name]
        
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
                traducciones = parsear_traduccion(contenido, set(entrada.keys()))
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