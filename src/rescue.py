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
        
    print(f"Encontradas {len(images)} paginas. Comprimiendo a JPEG para reducir tamaño...")
    
    # Directorio temporal para guardar los JPGs optimizados
    jpg_dir = output_dir / 'temp_jpgs'
    jpg_dir.mkdir(exist_ok=True)
    
    jpg_files = []
    
    try:
        # Convertir una por una a JPG para no usar mucha RAM
        for idx, img_path in enumerate(images):
            jpg_path = jpg_dir / f"page_{idx:04d}.jpg"
            jpg_files.append(str(jpg_path))
            
            # Solo saltar si ya se comprimio en un intento anterior
            if not jpg_path.exists():
                with Image.open(img_path) as img:
                    # Convertir a RGB (elimina transparencia, obligatorio para JPG)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    # quality=65 ofrece un buen balance de lectura vs tamaño
                    img.save(jpg_path, "JPEG", quality=65)
            
            if (idx + 1) % 50 == 0:
                print(f"  -> Comprimidas {idx + 1}/{len(images)} paginas...")
                
        print("Ensamblando el PDF final (esto sera rapido y ligero)...")
        with open(salida, "wb") as f:
            img2pdf.convert(jpg_files, outputstream=f)
            
        print("Limpiando temporales...")
        for j_file in jpg_files:
            try:
                os.remove(j_file)
            except:
                pass
        jpg_dir.rmdir()
            
        print(f"¡Terminado con exito! El PDF ha sido optimizado.")
        
    except Exception as e:
        print(f"Ocurrio un error: {e}")

if __name__ == "__main__":
    build_pdf_compressed()
