"""
Convierte a OpenVINO (FP32; la GPU lo ejecuta en FP16) los modelos que acelera
src/aceleracion.py:
  - det_fp32.xml  detector DBNet-ResNet34 (detect-20241225.ckpt)
  - aot_fp32.xml  inpainting AOT (inpainting.ckpt)

Solo hace falta si se pierde /config/models/openvino/ o si cambian los pesos.
La conversión del AOT necesita más de 4 GB de RAM: con el límite del servicio el
OOM killer la mata. Ejecutar en un contenedor APARTE:

    docker run --rm --memory 6g --cpus 4 --cpu-shares 512 --user 1000:100 \
      -e HOME=/tmp -v /datos/appdata/mangascan/config:/config \
      -v /datos/src/mangascan/tools:/tools:ro \
      --entrypoint python mangascan-ai:latest /tools/convertir_modelos_openvino.py
"""
import sys
import time

sys.path.insert(0, "/app/manga-image-translator")

import openvino as ov
import torch

SALIDA = "/config/models/openvino"


def cargar(modelo, ruta):
    sd = torch.load(ruta, map_location="cpu")
    modelo.load_state_dict(sd["model"] if "model" in sd else sd)
    return modelo.eval()


def main() -> None:
    import os
    os.makedirs(SALIDA, exist_ok=True)

    from manga_translator.detection.default_utils.DBNet_resnet34 import TextDetection
    t = time.time()
    det = cargar(TextDetection(), "/config/models/detection/detect-20241225.ckpt")
    m = ov.convert_model(det, example_input=torch.zeros(1, 3, 2048, 1536),
                         input=[ov.PartialShape([1, 3, -1, -1])])
    ov.save_model(m, f"{SALIDA}/det_fp32.xml", compress_to_fp16=False)
    print(f"det_fp32 en {time.time() - t:.0f}s", flush=True)

    from manga_translator.inpainting.inpainting_aot import AOTGenerator
    t = time.time()
    aot = cargar(AOTGenerator(), "/config/models/inpainting/inpainting.ckpt")
    m = ov.convert_model(aot, example_input=(torch.zeros(1, 3, 1024, 728), torch.zeros(1, 1, 1024, 728)),
                         input=[ov.PartialShape([1, 3, -1, -1]), ov.PartialShape([1, 1, -1, -1])])
    ov.save_model(m, f"{SALIDA}/aot_fp32.xml", compress_to_fp16=False)
    print(f"aot_fp32 en {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
