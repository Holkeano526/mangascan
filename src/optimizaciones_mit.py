"""
Optimizaciones de manga-image-translator que NO cambian el resultado.

Se aplican como parches en caliente (monkeypatch) sobre la librería clonada en
la imagen, para no tener que mantener un fork. Cada parche produce exactamente
la misma salida que el original; solo cambia cuánto tarda.

Perfilado en el NAS (i5-1135G7), por página:
- solve_collisions_spiral_xyxy (colocar los textos sin solaparse): ~1,5 s en
  Python puro, con ~900 000 comprobaciones de colisión una a una. Vectorizado
  con numpy: misma búsqueda en espiral, mismo orden, mismo punto elegido.

(La detección de viñetas y la compresión PNG se ajustan en translator_engine.py
y pdf_extractor.py; ver allí.)
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_aplicado = False

# Anillos de la espiral evaluados por bloque: más anillos = menos llamadas a numpy
# pero más trabajo desperdiciado si el hueco libre está cerca del ancla.
_ANILLOS_POR_BLOQUE = 8


def _puntos_anillos(r0: int, r1: int) -> tuple[np.ndarray, np.ndarray]:
    """Desplazamientos (dx, dy) de los anillos r0..r1-1 en el MISMO orden que
    _spiral_points_generator del original:
      para cada radio r:
        para dx en -r..r:      (dx, -r), (dx, +r)        (bordes superior e inferior)
        para dy en -r+1..r-1:  (-r, dy), (+r, dy)        (bordes izquierdo y derecho)
    """
    dxs, dys = [], []
    for r in range(r0, r1):
        d = np.arange(-r, r + 1)
        tb_dx = np.repeat(d, 2)
        tb_dy = np.tile(np.array([-r, r]), d.size)
        e = np.arange(-r + 1, r)
        lr_dx = np.tile(np.array([-r, r]), e.size)
        lr_dy = np.repeat(e, 2)
        dxs += [tb_dx, lr_dx]
        dys += [tb_dy, lr_dy]
    return np.concatenate(dxs), np.concatenate(dys)


def _find_collision_free_position_np(bbox_idx, bboxes, anchors, image_bounds, spiral_limit):
    """Equivalente exacto de text_render_pillow_eng._find_collision_free_position."""
    max_x, max_y = image_bounds
    w = bboxes[bbox_idx][2] - bboxes[bbox_idx][0]
    h = bboxes[bbox_idx][3] - bboxes[bbox_idx][1]
    ax, ay = anchors[bbox_idx][0], anchors[bbox_idx][1]

    otros = np.array([b for k, b in enumerate(bboxes) if k != bbox_idx], dtype=np.int64).reshape(-1, 4)

    def valido(x, y):
        """Máscara de candidatos dentro de la imagen y sin colisión con ninguna otra caja."""
        dentro = (x >= 0) & (y >= 0) & (x + w <= max_x) & (y + h <= max_y)
        if otros.size == 0:
            return dentro
        cx0, cy0, cx1, cy1 = x[:, None], y[:, None], (x + w)[:, None], (y + h)[:, None]
        choca = ~((cx1 <= otros[:, 0]) | (cx0 >= otros[:, 2]) | (cy1 <= otros[:, 1]) | (cy0 >= otros[:, 3]))
        return dentro & ~choca.any(axis=1)

    # Radio 0: el propio ancla (primer punto del generador original)
    if valido(np.array([ax], dtype=np.int64), np.array([ay], dtype=np.int64))[0]:
        return [ax, ay, ax + w, ay + h]

    radio_max = int(spiral_limit ** 0.5)  # el original usa range(1, int(limit**0.5))
    r = 1
    while r < radio_max:
        r_fin = min(radio_max, r + _ANILLOS_POR_BLOQUE)
        dx, dy = _puntos_anillos(r, r_fin)
        x = ax + dx
        y = ay + dy
        ok = valido(x.astype(np.int64), y.astype(np.int64))
        idx = np.flatnonzero(ok)
        if idx.size:
            k = int(idx[0])  # el primero en el orden de la espiral, como el original
            nx = ax + int(dx[k])
            ny = ay + int(dy[k])
            return [nx, ny, nx + w, ny + h]
        r = r_fin
    return None


def aplicar() -> None:
    """Instala los parches. Idempotente; si la librería cambió, avisa y no toca nada."""
    global _aplicado
    if _aplicado:
        return
    try:
        from manga_translator.rendering import text_render_pillow_eng as tr
        if not hasattr(tr, "_find_collision_free_position"):
            raise AttributeError("_find_collision_free_position no existe")
        tr._find_collision_free_position_original = tr._find_collision_free_position
        tr._find_collision_free_position = _find_collision_free_position_np
        _aplicado = True
        logger.info("Optimizaciones MIT: colocación de textos vectorizada")
    except Exception as e:
        logger.warning(f"Optimizaciones MIT no aplicadas ({e}); se usa el código original")
