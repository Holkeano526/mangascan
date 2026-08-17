import pathlib
import sys

def build_pdf_img2pdf():
    try:
        import img2pdf
    except ImportError:
        print("Falta instalar img2pdf. Ejecuta: docker exec mangascan-ai pip install img2pdf")
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
        
    print(f"Ensamblando {len(images)} paginas en {salida} usando img2pdf (Streaming directo a disco sin RAM)...")
    
    # img2pdf es la unica libreria garantizada de no usar RAM extra,
    # ya que incrusta los bytes crudos directamente en el PDF por streaming.
    with open(salida, "wb") as f:
        img2pdf.convert([str(p) for p in images], outputstream=f)
        
    print("¡Terminado con exito!")

if __name__ == "__main__":
    build_pdf_img2pdf()
