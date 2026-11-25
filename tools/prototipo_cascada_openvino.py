"""Cascada de detección: INT8 (OpenVINO) propone, PyTorch confirma las zonas dudosas.
Uso: python prototipo_cascada_openvino.py [conf] [margen]  (ver CASCADA_PDF_A/B abajo)
Compara contra PyTorch puro en 12 páginas de prueba (las mismas de la evaluación INT8).
"""
import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, "/app"); sys.path.append("/tmp/ovrt")
import numpy as np, fitz, torch, cv2, einops
import openvino as ov
from src.translator_engine import TraductorMangaOptimizado
from src.pdf_extractor import zoom_nativo

CONF = float(sys.argv[1]) if len(sys.argv) > 1 else 0.6     # media de prob. por encima = confiar en INT8
MARGEN = int(sys.argv[2]) if len(sys.argv) > 2 else 128     # contexto alrededor de la zona dudosa (px)
UMBRAL_CAND = 0.2                                           # binarización para buscar candidatas
AL = 256  # alineación/relleno exigido por la red (MULT=256 en imgproc)
AREA_MIN = 12                                               # px de mapa; menos = ruido

# Dos PDF de prueba distintos (rutas dentro del contenedor), por variable de entorno:
#   CASCADA_PDF_A=/app/data/input/a.pdf CASCADA_PDF_B=/app/data/input/b.pdf python prototipo_cascada_openvino.py
import os
PDFS = {"bed": os.environ["CASCADA_PDF_A"], "f4b": os.environ["CASCADA_PDF_B"]}
work = Path("/tmp/casc"); work.mkdir(exist_ok=True)
def extraer(key, idxs):
    doc = fitz.open(PDFS[key]); out = []
    for i in idxs:
        p = work / f"{key}{i:04d}.png"
        if not p.exists():
            z = zoom_nativo(doc[i], 250 / 72); doc[i].get_pixmap(matrix=fitz.Matrix(z, z)).save(str(p))
        out.append(p)
    return out
test = extraer("bed", range(4, 10)) + extraer("f4b", range(10, 16))

eng = TraductorMangaOptimizado("x"); tr = eng._get_translator()
from manga_translator.detection import default as D
from manga_translator.utils import load_image
from manga_translator import Context
from PIL import Image
cfg = eng._get_config("detect")
orig = D.det_batch_forward_default

core = ov.Core(); core.set_property("CPU", {"INFERENCE_NUM_THREADS": 4})
INT8 = core.compile_model(core.read_model("/config/models/openvino/int8_c24.xml"), "CPU", {"PERFORMANCE_HINT": "LATENCY"})
STATS = {"zonas": 0, "px_torch": 0, "px_total": 0, "t_int8": 0.0, "t_torch": 0.0}

def zonas_dudosas(db):
    """Rectángulos (y0,y1,x0,x1) en coords del mapa con texto de confianza media < CONF."""
    cand = (db > UMBRAL_CAND).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    rects = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area < AREA_MIN:
            continue
        media = db[lab == i].mean()
        if media < CONF:
            rects.append((y, y + h, x, x + w))
    return rects

def fusionar(rects, H, W):
    """Une rectángulos (con margen) que se solapan para no repetir trabajo."""
    if not rects:
        return []
    m = np.zeros((H, W), np.uint8)
    for y0, y1, x0, x1 in rects:
        m[max(0, y0 - MARGEN):min(H, y1 + MARGEN), max(0, x0 - MARGEN):min(W, x1 + MARGEN)] = 1
    n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
    return [(st[i][1], st[i][1] + st[i][3], st[i][0], st[i][0] + st[i][2]) for i in range(1, n)]

def cascada_fwd(batch, device):
    if isinstance(batch, list):
        batch = np.array(batch)
    b = einops.rearrange(batch.astype(np.float32) / 127.5 - 1.0, 'n h w c -> n c h w')
    s = time.time()
    r = INT8([b]); db = 1 / (1 + np.exp(-r[0])); mask = np.array(r[1])
    STATS["t_int8"] += time.time() - s
    s = time.time()
    N, _, H, W = db.shape
    mh, mw = mask.shape[2], mask.shape[3]
    fy, fx = mh / H, mw / W  # la máscara sale a otra escala
    for n in range(N):
        ventanas = fusionar(zonas_dudosas(db[n, 0]), H, W)
        STATS["zonas"] += len(ventanas); STATS["px_total"] += H * W
        for y0, y1, x0, x1 in ventanas:
            # alinear a múltiplos de 32 (exigencia de la red), recortando dentro de la imagen
            y0 = (y0 // AL) * AL; x0 = (x0 // AL) * AL
            y1 = min(H, -(-y1 // AL) * AL); x1 = min(W, -(-x1 // AL) * AL)
            crop = b[n:n + 1, :, y0:y1, x0:x1]
            ph, pw = (-crop.shape[2]) % AL, (-crop.shape[3]) % AL
            if ph or pw:
                crop = np.pad(crop, ((0, 0), (0, 0), (0, ph), (0, pw)), mode="edge")
            with torch.no_grad():
                dbt, mt = D.MODEL(torch.from_numpy(np.ascontiguousarray(crop)))
            dbt = dbt.sigmoid().numpy()[:, :, :y1 - y0, :x1 - x0]
            mt = mt.numpy()
            STATS["px_torch"] += (y1 - y0) * (x1 - x0)
            # escribir solo el interior (sin el borde de contexto, salvo en los bordes de la imagen)
            iy0 = 0 if y0 == 0 else min(MARGEN // 2, (y1 - y0) // 4)
            ix0 = 0 if x0 == 0 else min(MARGEN // 2, (x1 - x0) // 4)
            iy1 = (y1 - y0) if y1 == H else (y1 - y0) - min(MARGEN // 2, (y1 - y0) // 4)
            ix1 = (x1 - x0) if x1 == W else (x1 - x0) - min(MARGEN // 2, (x1 - x0) // 4)
            db[n, 0, y0 + iy0:y0 + iy1, x0 + ix0:x0 + ix1] = dbt[0, 0, iy0:iy1, ix0:ix1]
            my0, my1 = int((y0 + iy0) * fy), int((y0 + iy1) * fy)
            mx0, mx1 = int((x0 + ix0) * fx), int((x0 + ix1) * fx)
            mty0, mtx0 = int(iy0 * fy), int(ix0 * fx)
            hh = min(my1 - my0, mt.shape[2] - mty0); ww = min(mx1 - mx0, mt.shape[3] - mtx0)
            if hh > 0 and ww > 0:
                mask[n, 0, my0:my0 + hh, mx0:mx0 + ww] = mt[0, 0, mty0:mty0 + hh, mtx0:mtx0 + ww]
    STATS["t_torch"] += time.time() - s
    return db, mask

T = []
async def correr(nombre, fwd):
    D.det_batch_forward_default = fwd
    res = []; T.clear()
    for p in test:
        ctx = Context(); im = Image.open(p)
        ctx.input = ctx.upscaled = ctx.img_colorized = im
        ctx.img_rgb, ctx.img_alpha = load_image(im)
        s = time.time(); tl, raw, _ = await tr._run_detection(cfg, ctx); T.append(time.time() - s)
        res.append((tl, raw))
    print(f"[{nombre}] deteccion completa media={np.mean(T[1:]):.2f}s/pag", flush=True)
    return res

def comparar(ref, res, nombre):
    perd, extra, tot, desv = [], [], 0, []
    for p, ((tr_, rr), (to, ro)) in zip(test, zip(ref, res)):
        tot += len(tr_)
        for a in tr_:
            m = [b for b in to if np.abs(b.pts.mean(0) - a.pts.mean(0)).max() <= 8]
            if not m:
                perd.append((p.stem, round(float(a.prob), 2)))
            else:
                desv.append(np.abs(m[0].pts - a.pts).max())
        for b in to:
            if not any(np.abs(a.pts.mean(0) - b.pts.mean(0)).max() <= 8 for a in tr_):
                extra.append((p.stem, round(float(b.prob), 2)))
    dmask = np.mean([np.abs(rr.astype(int) - ro.astype(int)).mean() for (_, rr), (_, ro) in zip(ref, res)])
    print(f"   {nombre}: perdidas {len(perd)}/{tot} {perd} | extra {len(extra)} {extra} | "
          f"desv. esquinas max={max(desv) if desv else 0}px p95={np.percentile(desv, 95):.0f}px | dif mascara={dmask:.2f}", flush=True)

async def main():
    await correr("warm", orig)
    ref = await correr("PyTorch", orig)
    def int8_solo(batch, device):
        if isinstance(batch, list): batch = np.array(batch)
        b = einops.rearrange(batch.astype(np.float32) / 127.5 - 1.0, 'n h w c -> n c h w')
        r = INT8([b]); return 1 / (1 + np.exp(-r[0])), r[1]
    await correr("int8-warm", int8_solo)
    for k in STATS: STATS[k] = 0 if isinstance(STATS[k], int) else 0.0
    res = await correr(f"CASCADA conf={CONF} margen={MARGEN}", cascada_fwd)
    comparar(ref, res, "CASCADA")
    n = len(test)
    print(f"   zonas a PyTorch: {STATS['zonas'] / n:.1f}/pag | area recalculada {100 * STATS['px_torch'] / STATS['px_total']:.0f}% | "
          f"t INT8 {STATS['t_int8'] / n:.2f}s + t PyTorch zonas {STATS['t_torch'] / n:.2f}s por pag", flush=True)

asyncio.run(main())
