"""Lógica pura de las optimizaciones (sin modelos ML)."""
import random

import fitz
import numpy as np

from src.optimizaciones_mit import _find_collision_free_position_np
from src.pdf_extractor import zoom_nativo


# Copia literal del algoritmo original de manga-image-translator
# (rendering/text_render_pillow_eng.py) para comprobar la equivalencia exacta.
def _colision(b1, b2):
    return not (b1[2] <= b2[0] or b1[0] >= b2[2] or b1[3] <= b2[1] or b1[1] >= b2[3])


def _espiral(ax, ay, limit):
    yield ax, ay
    for r in range(1, int(limit ** 0.5)):
        for dx in range(-r, r + 1):
            yield ax + dx, ay - r
            yield ax + dx, ay + r
        for dy in range(-r + 1, r):
            yield ax - r, ay + dy
            yield ax + r, ay + dy


def _original(i, bboxes, anchors, bounds, limit):
    mx, my = bounds
    w = bboxes[i][2] - bboxes[i][0]
    h = bboxes[i][3] - bboxes[i][1]
    for x, y in _espiral(anchors[i][0], anchors[i][1], limit):
        c = [x, y, x + w, y + h]
        if not (0 <= x and 0 <= y and x + w <= mx and y + h <= my):
            continue
        if not any(k != i and _colision(c, o) for k, o in enumerate(bboxes)):
            return c
    return None


def test_colocacion_vectorizada_igual_al_original():
    random.seed(7)
    for _ in range(400):
        W, H = random.choice([(300, 400), (800, 1100)])
        n = random.randint(2, 15)
        bb = []
        for _ in range(n):
            x, y = random.randint(-20, W), random.randint(-20, H)
            bb.append([x, y, x + random.randint(5, 200), y + random.randint(5, 200)])
        anchors = [(b[0], b[1]) for b in bb]
        i = random.randrange(n)
        lim = random.choice([1e2, 1e3, 1e4])
        assert _find_collision_free_position_np(i, bb, anchors, (W, H), lim) == _original(i, bb, anchors, (W, H), lim)


def _pdf_con_imagen(ancho_px, alto_px, ancho_pt, alto_pt):
    doc = fitz.open()
    page = doc.new_page(width=ancho_pt, height=alto_pt)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, ancho_px, alto_px), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    page.insert_image(page.rect, pixmap=pix)
    return doc, page


def test_zoom_nativo_no_amplia_escaneos():
    # Página de 72 pt/px con imagen de 2070x2880: debe salir a ~2070 px, no a 250 DPI (5390 px)
    _, page = _pdf_con_imagen(2070, 2880, 1552.5, 2160)
    z = zoom_nativo(page, 250 / 72)
    assert abs(z * 1552.5 - 2070) < 2


def test_zoom_nativo_respeta_el_techo_de_dpi():
    # Imagen enorme en página pequeña: no pasar de 250 DPI
    _, page = _pdf_con_imagen(6000, 8000, 595, 842)
    assert zoom_nativo(page, 250 / 72) == 250 / 72


def test_zoom_nativo_pagina_sin_imagen_usa_dpi():
    doc = fitz.open()
    page = doc.new_page()
    assert zoom_nativo(page, 250 / 72) == 250 / 72
