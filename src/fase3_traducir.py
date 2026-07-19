"""
Fase 3 — Traducción con DeepSeek API
Traduce japonés → español por lotes con contexto de página.
"""

import json
import re
import requests
import time
import os
from pathlib import Path
from typing import Any
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Actúa como un traductor profesional de manga. Tu objetivo es traducir los siguientes textos al ESPAÑOL LATINO.\n"
    "REGLA DE ORO: La respuesta DEBE estar completamente en idioma ESPAÑOL.\n"
    "Está ESTRICTAMENTE PROHIBIDO incluir caracteres kanji, hiragana, katakana o caracteres chinos (hanzi) en tus traducciones.\n"
    "Debes traducir cada frase, adaptando las expresiones al contexto.\n"
    "Devuelve ÚNICAMENTE un JSON válido con este formato exacto: {\"0\": \"texto en español\", \"1\": \"texto en español\"}\n"
    "No añadas texto extra, ni explicaciones, ni comillas markdown."
)

# Endpoint y Modelo de DeepSeek
API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODELO = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Patrón para detectar caracteres asiáticos (kanji, hiragana, katakana, hanzi)
PATRON_ASIATICO = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')


def validar_sin_asiaticos(traducciones: dict[str, str]) -> None:
    """
    Valida que ningún texto traducido contenga caracteres asiáticos.

    Args:
        traducciones: Diccionario {id_str: texto_traducido}.

    Raises:
        ValueError: Si algún texto contiene caracteres asiáticos.
    """
    for k, v in traducciones.items():
        if PATRON_ASIATICO.search(str(v)):
            raise ValueError(
                f"El texto devuelto contiene caracteres asiáticos prohibidos: '{v[:80]}' "
                f"(ID: {k})"
            )


def traducir_pagina(
    bubbles: list[dict[str, Any]],
    api_key: str,
    max_retries: int = 2,
    temperature: float = 0.3,
) -> dict[str, str]:
    """
    Traduce todos los textos de una página usando DeepSeek API.

    Args:
        bubbles: Lista de globos con campos 'id' y 'src'.
        api_key: API key de DeepSeek.
        max_retries: Número máximo de reintentos ante fallos de parseo.
        temperature: Temperatura del modelo (0.0-1.0).

    Returns:
        Diccionario {id_str: traducción} para cada globo.

    Raises:
        ValueError: Si no se puede parsear la respuesta tras reintentos.
        requests.RequestException: Si falla la comunicación HTTP.
    """
    if not bubbles:
        logger.warning("No hay globos que traducir en esta página")
        return {}

    # Preparar entrada: {id: src}
    entrada = {str(b["id"]): b["src"] for b in bubbles}

    payload = {
        "model": MODELO,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(entrada, ensure_ascii=False)},
        ],
        "temperature": temperature,
        "max_tokens": 4096,
    }

    ultimo_error = None
    for intento in range(max_retries + 1):
        try:
            logger.debug(
                f"Enviando {len(bubbles)} textos a DeepSeek "
                f"(intento {intento + 1}/{max_retries + 1})"
            )

            r = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )

            # --- Exponential Backoff para HTTP 429 Too Many Requests ---
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 60))
                logger.warning(
                    f"HTTP 429 Too Many Requests (intento {intento + 1}). "
                    f"Esperando {retry_after}s antes de reintentar..."
                )
                time.sleep(retry_after)
                continue

            r.raise_for_status()
            respuesta = r.json()

            contenido = respuesta["choices"][0]["message"]["content"]
            # Limpiar posibles delimitadores markdown
            contenido = contenido.strip()
            contenido = contenido.removeprefix("```json").removeprefix("```")
            contenido = contenido.removesuffix("```").strip()

            traducciones = json.loads(contenido)

            # --- Validación externalizada: Regla de Oro (anti-asiáticos) ---
            validar_sin_asiaticos(traducciones)

            # Validar que todas las claves esperadas están presentes
            ids_originales = set(entrada.keys())
            ids_traducidos = set(traducciones.keys())
            faltantes = ids_originales - ids_traducidos
            if faltantes:
                logger.warning(
                    f"Faltan traducciones para IDs: {faltantes}. "
                    "Completando con texto vacío."
                )
                for fid in faltantes:
                    traducciones[fid] = ""

            logger.info(
                f"Traducción exitosa: {len(traducciones)} textos "
                f"({len(faltantes)} faltantes)" if faltantes else
                f"Traducción exitosa: {len(traducciones)} textos"
            )
            return traducciones

        except (json.JSONDecodeError, ValueError) as e:
            ultimo_error = e
            logger.warning(
                f"Error parseando respuesta JSON (intento {intento + 1}): {e}\n"
                f"Respuesta raw: {contenido[:200] if 'contenido' in locals() else 'N/A'}"
            )
            if intento < max_retries:
                # Espera exponencial progresiva: 1s, 2s, 4s...
                wait = 2 ** intento
                logger.info(f"Reintentando en {wait}s...")
                time.sleep(wait)
                continue

        except (requests.RequestException, KeyError) as e:
            ultimo_error = e
            status = getattr(getattr(e, 'response', None), 'status_code', None)

            # Manejo de errores HTTP 4xx no recuperables (excepto 408 y 429)
            if status and 400 <= status < 500 and status not in (408, 429):
                logger.error(f"Error HTTP {status} en DeepSeek: {e}")
                raise

            logger.error(f"Error de comunicación con DeepSeek: {e}")

            if intento < max_retries:
                # Espera exponencial progresiva para errores de red/HTTP
                wait = 2 ** intento
                logger.info(f"Reintentando en {wait}s...")
                time.sleep(wait)
                continue

    # Si llegamos aquí, todos los reintentos fallaron
    error_msg = (
        f"No se pudo traducir la página tras {max_retries + 1} intentos. "
        f"Último error: {ultimo_error}"
    )
    logger.error(error_msg)
    raise ValueError(error_msg)


def aplicar_traducciones(
    page_data: dict[str, Any],
    traducciones: dict[str, str],
) -> dict[str, Any]:
    """
    Escribe las traducciones en el JSON de la página (campo dst).

    Args:
        page_data: Dict con la estructura de página (bubbles).
        traducciones: Dict {id_str: traducción}.

    Returns:
        El mismo dict con los campos dst actualizados.
    """
    for bubble in page_data["bubbles"]:
        id_str = str(bubble["id"])
        bubble["dst"] = traducciones.get(id_str, "")
    return page_data