"""
Detector en cascada: INT8 (OpenVINO) propone, PyTorch confirma las zonas dudosas.

El detector por defecto de manga-image-translator (DBNet-ResNet34) es la fase más
lenta del pipeline. Cuantizado a INT8 con OpenVINO corre ~4x más rápido en el
i5-1135G7 del NAS (instrucciones AVX-512 VNNI), pero pierde ~1 % de textos
cortos con confianza límite. La cascada recupera esa precisión:

  1. INT8 calcula el mapa de probabilidad de toda la página (~1,2 s).
  2. Se localizan las regiones de texto con confianza media < CONF (dudosas).
  3. PyTorch recalcula SOLO esas zonas (recorte con MARGEN px de contexto,
     alineado a 256 px como exige la red) y sobrescribe el mapa en su interior.
  4. El posprocesado de manga-image-translator no cambia.

Validado el 25/11/2026 en 12 páginas no usadas para calibrar (220 cajas):
0 perdidas, 1 extra; detección 6,4 s -> 4,2 s. Con MARGEN 64 los bloques largos
de texto vertical se cortan en el borde de la ventana: no bajar de 128.

Variables de entorno:
  MANGASCAN_DETECTOR   "auto" (por defecto: iGPU si existe, si no cascada) |
                       "gpu" | "cascada" | "pytorch"
  MANGASCAN_OV_MODEL   ruta del modelo INT8 (.xml); por defecto
                       /config/models/openvino/int8_c24.xml
Si OpenVINO o el modelo faltan, o la cascada falla en una página, se usa
PyTorch automáticamente (nunca se pierde una página por esto).
Modelo regenerable con tools/cuantizar_detector_int8.py.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

CONF = 0.6          # media de probabilidad por encima de la cual se confía en INT8
UMBRAL_CAND = 0.2   # binarización para buscar regiones candidatas
AREA_MIN = 12       # px del mapa; por debajo es ruido
MARGEN = 128        # contexto alrededor de la zona dudosa (px del mapa)
AL = 256            # alineación/relleno exigido por la red (MULT=256 en imgproc)

MODELO_POR_DEFECTO = "/config/models/openvino/int8_c24.xml"

_estado = {"activa": False, "paginas": 0, "zonas": 0, "t_int8": 0.0, "t_torch": 0.0, "fallos": 0}


def _zonas_dudosas(db: np.ndarray) -> list[tuple[int, int, int, int]]:
    import cv2
    cand = (db > UMBRAL_CAND).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    rects = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area < AREA_MIN:
            continue
        if db[lab == i].mean() < CONF:
            rects.append((y, y + h, x, x + w))
    return rects


def _fusionar(rects, H: int, W: int) -> list[tuple[int, int, int, int]]:
    """Une las ventanas (con margen) que se solapan para no recalcular dos veces."""
    import cv2
    if not rects:
        return []
    m = np.zeros((H, W), np.uint8)
    for y0, y1, x0, x1 in rects:
        m[max(0, y0 - MARGEN):min(H, y1 + MARGEN), max(0, x0 - MARGEN):min(W, x1 + MARGEN)] = 1
    n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
    return [(st[i][1], st[i][1] + st[i][3], st[i][0], st[i][0] + st[i][2]) for i in range(1, n)]


def _refinar_con_pytorch(modelo, b: np.ndarray, db: np.ndarray, mask: np.ndarray) -> int:
    """Recalcula con PyTorch las zonas dudosas de db/mask (in situ). Devuelve nº de zonas."""
    import torch
    N, _, H, W = db.shape
    fy, fx = mask.shape[2] / H, mask.shape[3] / W  # la máscara sale a otra escala
    total = 0
    for n in range(N):
        ventanas = _fusionar(_zonas_dudosas(db[n, 0]), H, W)
        total += len(ventanas)
        for y0, y1, x0, x1 in ventanas:
            y0 = (y0 // AL) * AL
            x0 = (x0 // AL) * AL
            y1 = min(H, -(-y1 // AL) * AL)
            x1 = min(W, -(-x1 // AL) * AL)
            crop = b[n:n + 1, :, y0:y1, x0:x1]
            ph, pw = (-crop.shape[2]) % AL, (-crop.shape[3]) % AL
            if ph or pw:
                crop = np.pad(crop, ((0, 0), (0, 0), (0, ph), (0, pw)), mode="edge")
            with torch.no_grad():
                dbt, mt = modelo(torch.from_numpy(np.ascontiguousarray(crop)))
            dbt = dbt.sigmoid().numpy()[:, :, :y1 - y0, :x1 - x0]
            mt = mt.numpy()
            # escribir solo el interior (sin el borde de contexto, salvo en los bordes de la imagen)
            h, w = y1 - y0, x1 - x0
            iy0 = 0 if y0 == 0 else min(MARGEN // 2, h // 4)
            ix0 = 0 if x0 == 0 else min(MARGEN // 2, w // 4)
            iy1 = h if y1 == H else h - min(MARGEN // 2, h // 4)
            ix1 = w if x1 == W else w - min(MARGEN // 2, w // 4)
            db[n, 0, y0 + iy0:y0 + iy1, x0 + ix0:x0 + ix1] = dbt[0, 0, iy0:iy1, ix0:ix1]
            my0, my1 = int((y0 + iy0) * fy), int((y0 + iy1) * fy)
            mx0, mx1 = int((x0 + ix0) * fx), int((x0 + ix1) * fx)
            mty0, mtx0 = int(iy0 * fy), int(ix0 * fx)
            hh = min(my1 - my0, mt.shape[2] - mty0)
            ww = min(mx1 - mx0, mt.shape[3] - mtx0)
            if hh > 0 and ww > 0:
                mask[n, 0, my0:my0 + hh, mx0:mx0 + ww] = mt[0, 0, mty0:mty0 + hh, mtx0:mtx0 + ww]
    return total


def activar() -> bool:
    """Sustituye el forward del detector por la cascada. Devuelve True si quedó activa.

    Llamar después de instanciar MangaTranslator. Es idempotente.
    """
    if _estado["activa"]:
        return True
    modo = os.environ.get("MANGASCAN_DETECTOR", "auto").strip().lower()
    # auto: iGPU si existe (igual de precisa que PyTorch y más rápida); si no, cascada en CPU
    if modo in ("auto", "gpu"):
        from .aceleracion import activar_detector_gpu
        if activar_detector_gpu():
            return True
        if modo == "gpu":
            logger.warning("Detector: GPU pedida pero no disponible; se usa la cascada en CPU")
        modo = "cascada"
    if modo != "cascada":
        logger.info(f"Detector: PyTorch (MANGASCAN_DETECTOR={modo})")
        return False

    ruta = Path(os.environ.get("MANGASCAN_OV_MODEL", MODELO_POR_DEFECTO))
    try:
        import einops
        import openvino as ov
        from manga_translator.detection import default as D
    except Exception as e:  # OpenVINO no instalado o manga_translator distinto
        logger.warning(f"Detector: cascada no disponible ({e}); se usa PyTorch")
        return False
    if not ruta.exists() or not ruta.with_suffix(".bin").exists():
        logger.warning(f"Detector: modelo INT8 no encontrado en {ruta}; se usa PyTorch")
        return False

    try:
        core = ov.Core()
        hilos = int(os.environ.get("OMP_NUM_THREADS", "4"))
        core.set_property("CPU", {"INFERENCE_NUM_THREADS": hilos})
        compilado = core.compile_model(core.read_model(str(ruta)), "CPU", {"PERFORMANCE_HINT": "LATENCY"})
    except Exception as e:
        logger.warning(f"Detector: no se pudo cargar el modelo INT8 ({e}); se usa PyTorch")
        return False

    original = D.det_batch_forward_default

    def forward_cascada(batch, device):
        if D.MODEL is None:  # el detector aún no cargó sus pesos de PyTorch
            return original(batch, device)
        try:
            if isinstance(batch, list):
                batch = np.array(batch)
            b = einops.rearrange(batch.astype(np.float32) / 127.5 - 1.0, "n h w c -> n c h w")
            t = time.time()
            r = compilado([b])
            db = 1.0 / (1.0 + np.exp(-np.asarray(r[0])))
            mask = np.array(r[1])
            t_int8 = time.time() - t
            t = time.time()
            zonas = _refinar_con_pytorch(D.MODEL, b, db, mask)
            t_torch = time.time() - t
            _estado["paginas"] += 1
            _estado["zonas"] += zonas
            _estado["t_int8"] += t_int8
            _estado["t_torch"] += t_torch
            logger.debug(f"  Detector cascada: INT8 {t_int8:.2f}s + PyTorch {zonas} zona(s) {t_torch:.2f}s")
            return db, mask
        except Exception as e:
            _estado["fallos"] += 1
            logger.warning(f"  Detector cascada falló ({e}); esta página se detecta con PyTorch")
            return original(batch, device)

    D.det_batch_forward_default = forward_cascada
    _estado["activa"] = True
    logger.info(f"Detector: cascada INT8 (OpenVINO {ov.__version__}) + PyTorch para zonas dudosas")
    return True


def resumen() -> str | None:
    """Línea de estadísticas para el log final (None si la cascada no se usó)."""
    p = _estado["paginas"]
    if not _estado["activa"] or not p:
        return None
    return (f"Detector cascada: {p} página(s), {_estado['zonas'] / p:.1f} zonas dudosas/pág., "
            f"INT8 {_estado['t_int8'] / p:.2f}s + PyTorch {_estado['t_torch'] / p:.2f}s por pág., "
            f"{_estado['fallos']} fallo(s) con vuelta a PyTorch")
