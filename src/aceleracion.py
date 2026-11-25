"""
Aceleración por GPU integrada (Intel Iris Xe) con OpenVINO.

El NAS (i5-1135G7) tiene una iGPU Iris Xe (96 EU). Con OpenVINO en FP16:

  Fase              PyTorch CPU   iGPU FP16   Calidad (validado 25/11/2026)
  Detector (DBNet)  4,75 s        1,48 s      0/220 cajas perdidas, 0 extra, esquinas ±3 px
  Inpainting (AOT)  4,53 s        1,72 s      dif. media 0,004/255, máx. 5/255 (invisible)

Además, mientras la GPU trabaja, la CPU queda libre.

Requisitos (los cumple la imagen de Dockerfile.nas y la receta de OMV):
  - Runtime Intel compute-runtime 26.x + IGC 2.x en la imagen. Debian 13 no lo
    trae, y la rama "legacy1" de Intel NO sirve para Tiger Lake: es para Gen9-11.
  - /dev/dri mapeado al contenedor y el grupo "render" del host (gid 992) en group_add.
  - Modelos en /config/models/openvino/: det_fp32.xml y aot_fp32.xml
    (regenerables con tools/convertir_modelos_openvino.py).

Variables de entorno:
  MANGASCAN_GPU=0        desactiva la GPU (todo vuelve a CPU)
  MANGASCAN_OV_DIR       carpeta de modelos (por defecto /config/models/openvino)

Todo es opcional: si no hay GPU, falta un modelo o la GPU falla en una página,
esa fase usa el camino anterior (cascada INT8 en CPU o PyTorch).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DIR_MODELOS = Path(os.environ.get("MANGASCAN_OV_DIR", "/config/models/openvino"))
DIR_CACHE = Path("/config/cache/openvino")  # kernels de GPU compilados (arranque ~10 s -> ~1 s)

_core = None
_estado = {"det_gpu": False, "inp_gpu": False, "t_det": 0.0, "n_det": 0, "t_inp": 0.0, "n_inp": 0, "fallos": 0}


def core():
    """ov.Core compartido, con caché de compilación en disco si se puede escribir."""
    global _core
    if _core is None:
        import openvino as ov
        _core = ov.Core()
        try:
            DIR_CACHE.mkdir(parents=True, exist_ok=True)
            _core.set_property({"CACHE_DIR": str(DIR_CACHE)})
        except Exception as e:
            logger.debug(f"Sin caché de OpenVINO ({e})")
    return _core


def gpu_disponible() -> bool:
    if os.environ.get("MANGASCAN_GPU", "1").strip() == "0":
        return False
    try:
        return "GPU" in core().available_devices
    except Exception as e:
        logger.debug(f"OpenVINO no disponible ({e})")
        return False


def _compilar_gpu(nombre: str):
    """Compila un modelo .xml en la GPU con FP16. None si no se puede."""
    xml = DIR_MODELOS / f"{nombre}.xml"
    if not xml.exists() or not xml.with_suffix(".bin").exists():
        logger.warning(f"GPU: falta el modelo {xml}")
        return None
    try:
        t = time.time()
        c = core().compile_model(str(xml), "GPU", {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_PRECISION_HINT": "f16"})
        logger.info(f"GPU: {nombre} compilado en {time.time() - t:.1f}s")
        return c
    except Exception as e:
        logger.warning(f"GPU: no se pudo compilar {nombre} ({e})")
        return None


def activar_detector_gpu() -> bool:
    """Sustituye el forward del detector DBNet por la versión en GPU (FP16)."""
    if _estado["det_gpu"]:
        return True
    if not gpu_disponible():
        return False
    try:
        import einops
        from manga_translator.detection import default as D
    except Exception as e:
        logger.warning(f"GPU: detector no parcheable ({e})")
        return False
    compilado = _compilar_gpu("det_fp32")
    if compilado is None:
        return False
    original = D.det_batch_forward_default

    def forward_gpu(batch, device):
        try:
            if isinstance(batch, list):
                batch = np.array(batch)
            b = einops.rearrange(batch.astype(np.float32) / 127.5 - 1.0, "n h w c -> n c h w")
            t = time.time()
            r = compilado([b])
            db = 1.0 / (1.0 + np.exp(-np.asarray(r[0], dtype=np.float32)))
            mask = np.asarray(r[1], dtype=np.float32)
            _estado["t_det"] += time.time() - t
            _estado["n_det"] += 1
            return db, mask
        except Exception as e:
            _estado["fallos"] += 1
            logger.warning(f"  Detector GPU falló ({e}); esta página va por PyTorch")
            return original(batch, device)

    D.det_batch_forward_default = forward_gpu
    _estado["det_gpu"] = True
    logger.info("Detector: iGPU Intel (OpenVINO FP16)")
    return True


class _AOTenGPU:
    """Sustituto de AOTGenerator con la misma llamada: model(img, mask) -> tensor.
    Si la GPU falla, usa el modelo de PyTorch original."""

    def __init__(self, compilado, modelo_pytorch):
        self.c = compilado
        self.pt = modelo_pytorch

    def __call__(self, img, mask):
        import torch
        try:
            t = time.time()
            r = self.c([img.numpy(), mask.numpy()])[0]
            _estado["t_inp"] += time.time() - t
            _estado["n_inp"] += 1
            return torch.from_numpy(np.asarray(r, dtype=np.float32))
        except Exception as e:
            _estado["fallos"] += 1
            logger.warning(f"  Inpainting GPU falló ({e}); esta página va por PyTorch")
            return self.pt(img, mask)

    def __getattr__(self, nombre):  # .eval(), .to(), etc. -> el modelo original
        return getattr(self.pt, nombre)


def activar_inpainting_gpu() -> bool:
    """Hace que el inpainter por defecto (AOT) use la GPU tras cargar sus pesos."""
    if _estado["inp_gpu"]:
        return True
    if not gpu_disponible():
        return False
    try:
        from manga_translator.inpainting.inpainting_aot import AotInpainter
    except Exception as e:
        logger.warning(f"GPU: inpainter no parcheable ({e})")
        return False
    compilado = _compilar_gpu("aot_fp32")
    if compilado is None:
        return False
    carga_original = AotInpainter._load

    async def _load(self, device):
        await carga_original(self, device)
        if not isinstance(self.model, _AOTenGPU):
            self.model = _AOTenGPU(compilado, self.model)

    AotInpainter._load = _load
    _estado["inp_gpu"] = True
    logger.info("Inpainting: iGPU Intel (OpenVINO FP16)")
    return True


def resumen() -> str | None:
    if not (_estado["det_gpu"] or _estado["inp_gpu"]):
        return None
    partes = []
    if _estado["n_det"]:
        partes.append(f"detector {_estado['t_det'] / _estado['n_det']:.2f}s/pág")
    if _estado["n_inp"]:
        partes.append(f"inpainting {_estado['t_inp'] / _estado['n_inp']:.2f}s/pág")
    return f"iGPU: {', '.join(partes) or 'sin uso'}, {_estado['fallos']} fallo(s) con vuelta a CPU"
