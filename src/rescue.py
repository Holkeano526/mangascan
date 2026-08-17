import pathlib
import sys
import tempfile
import os

def build_pdf_compressed():
    try:
        import img2pdf
        from PIL import Image
    except ImportError:
        print("Faltan librerias. Ejecuta: docker exec mangascan-ai pip install img2pdf pillow")
        return

    tid = 'f4b9ac0e'
    output_dir = pathlib.Path('data/output') / tid
    render_dir = output_dir / 'render'
    
    inp = next(pathlib.Path('data/input').glob(f'{tid}_*'), None)
    name = inp.name[9:] if inp else 'manga.pdf'
    salida = output_dir / f'translated_{name}'
    
    images = sorted(render_dir.glob("*_es.*"))
    if not images:
        print(f"No se encontraron imagenes en {render_dir}")
        return
        
    print(f"Encontradas {len(images)} paginas. Redimensionando y comprimiendo a JPEG...")
    
    jpg_dir = output_dir / 'temp_jpgs'
    jpg_dir.mkdir(exist_ok=True)
    
    jpg_files = []
    
    # Ancho maximo HD estandar para lectura
    MAX_WIDTH = 1400 
    
    try:
        for idx, img_path in enumerate(images):
            jpg_path = jpg_dir / f"page_{idx:04d}.jpg"
            jpg_files.append(str(jpg_path))
            
            with Image.open(img_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                    
                # Calcular nuevo tamaño si excede el MAX_WIDTH
                w, h = img.size
                if w > MAX_WIDTH:
                    ratio = MAX_WIDTH / w
                    new_h = int(h * ratio)
                    img = img.resize((MAX_WIDTH, new_h), Image.Resampling.LANCZOS)
                
                # Guardar con buena compresion y modo optimizado
                img.save(jpg_path, "JPEG", quality=70, optimize=True)
            
            if (idx + 1) % 50 == 0:
                print(f"  -> Procesadas {idx + 1}/{len(images)} paginas...")
                
        print("Ensamblando el PDF final ultra ligero...")
        with open(salida, "wb") as f:
            img2pdf.convert(jpg_files, outputstream=f)
            
        print("Limpiando temporales...")
        for j_file in jpg_files:
            try:
                os.remove(j_file)
            except:
                pass
        jpg_dir.rmdir()
            
        print(f"¡Terminado con exito! Tu manga ahora tiene peso pluma.")
        
    except Exception as e:
        print(f"Ocurrio un error: {e}")

if __name__ == "__main__":
    build_pdf_compressed()
