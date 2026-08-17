import pathlib
import sys
from PIL import Image

def build_pdf_low_mem():
    tid = 'f4b9ac0e'
    output_dir = pathlib.Path('data/output') / tid
    render_dir = output_dir / 'render'
    
    # Buscar el nombre original para que la web lo reconozca
    inp = next(pathlib.Path('data/input').glob(f'{tid}_*'), None)
    name = inp.name[9:] if inp else 'manga.pdf'
    
    salida = output_dir / f'translated_{name}'
    
    images = sorted(render_dir.glob("*_es.*"))
    if not images:
        print(f"No se encontraron imagenes en {render_dir}")
        return
        
    print(f"Ensamblando {len(images)} paginas en {salida} usando optimizacion extrema (PIL iterators)...")
    
    # Abrimos la primera imagen
    first_img = Image.open(images[0]).convert("RGB")
    
    # Usamos un generador para abrir, convertir y liberar una pagina a la vez
    def image_generator():
        for p in images[1:]:
            with Image.open(p) as img:
                yield img.convert("RGB")
                
    first_img.save(
        salida,
        "PDF",
        resolution=100.0,
        save_all=True,
        append_images=image_generator()
    )
    print("¡Terminado con exito!")

if __name__ == "__main__":
    build_pdf_low_mem()
