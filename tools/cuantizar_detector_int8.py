"""
Regenera el modelo INT8 del detector para src/detector_cascada.py.

Solo hace falta si se pierde /config/models/openvino/ o si manga-image-translator
cambia el detector (detect-20241225.ckpt).

Requisitos: openvino (ya en la imagen) + nncf (solo para esto):
    pip install --target /tmp/nncf nncf

Uso (dentro de la imagen, en un contenedor APARTE con >= 5 GB de RAM: con el
límite de 4 GB del servicio el OOM killer mata la calibración):

    docker run --rm --memory 5g --cpus 4 --cpu-shares 512 --user 1000:100 \
      -e HOME=/tmp -v /datos/appdata/mangascan/config:/config \
      -v /ruta/con/pngs_de_calibracion:/calib:ro \
      -v /datos/src/mangascan/tools:/tools:ro \
      --entrypoint python mangascan-ai:latest /tools/cuantizar_detector_int8.py /calib

Calibración: ~25 páginas PNG a resolución nativa, de VARIOS mangas distintos y
que no se usen luego para evaluar. Tarda ~5 min por cada pasada en el NAS.
Resultado: /config/models/openvino/int8_c24.{xml,bin} y det_fp32.{xml,bin}.
Después, validar contra PyTorch con tools/prototipo_cascada_openvino.py.
"""
import glob
import sys
import time

sys.path.append("/tmp/nncf")
sys.path.insert(0, "/app/manga-image-translator")

import cv2
import einops
import numpy as np
import openvino as ov
import torch

SALIDA = "/config/models/openvino"


def preparar(ruta: str) -> np.ndarray:
    """Mismo preprocesado que el detector por defecto (filtro bilateral + redimensionado a 2048)."""
    from manga_translator.detection.default_utils import imgproc
    im = cv2.cvtColor(cv2.imread(ruta), cv2.COLOR_BGR2RGB)
    r, *_ = imgproc.resize_aspect_ratio(cv2.bilateralFilter(im, 17, 80, 80), 2048, cv2.INTER_LINEAR, mag_ratio=1)
    return einops.rearrange(r[None].astype(np.float32) / 127.5 - 1.0, "n h w c -> n c h w")


def main() -> None:
    import os
    import nncf
    from manga_translator.detection.default_utils.DBNet_resnet34 import TextDetection

    carpeta = sys.argv[1] if len(sys.argv) > 1 else "/calib"
    pngs = sorted(glob.glob(f"{carpeta}/*.png"))
    if len(pngs) < 10:
        sys.exit(f"Hacen falta al menos 10 PNG de calibración en {carpeta} (hay {len(pngs)})")
    os.makedirs(SALIDA, exist_ok=True)

    # 1) PyTorch -> OpenVINO FP32 (forma dinámica)
    modelo = TextDetection()
    sd = torch.load("/config/models/detection/detect-20241225.ckpt", map_location="cpu")
    modelo.load_state_dict(sd["model"] if "model" in sd else sd)
    modelo.eval()
    t = time.time()
    fp32 = ov.convert_model(modelo, example_input=torch.zeros(1, 3, 2048, 1536),
                            input=[ov.PartialShape([1, 3, -1, -1])])
    ov.save_model(fp32, f"{SALIDA}/det_fp32.xml", compress_to_fp16=False)
    print(f"FP32 convertido en {time.time() - t:.0f}s", flush=True)

    # 2) Cuantización INT8 (dataset perezoso: no carga todas las páginas en RAM)
    t = time.time()
    q = nncf.quantize(fp32, nncf.Dataset(pngs, preparar), subset_size=len(pngs),
                      preset=nncf.QuantizationPreset.MIXED)
    ov.save_model(q, f"{SALIDA}/int8_c24.xml")
    print(f"INT8 cuantizado con {len(pngs)} páginas en {time.time() - t:.0f}s -> {SALIDA}/int8_c24.xml", flush=True)


if __name__ == "__main__":
    main()
