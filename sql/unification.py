import glob

# Lista de archivos en el orden exacto que prefieras (o usa glob.glob("*.sql") para todos)
archivos = sorted(glob.glob("*.sql"))

with open("archivo_unificado.sql", "w", encoding="utf-8") as outfile:
    for fname in archivos:
        if fname == "archivo_unificado.sql":
            continue
        outfile.write(f"\n-- ==========================================\n")
        outfile.write(f"-- INICIO DEL ARCHIVO: {fname}\n")
        outfile.write(f"-- ==========================================\n\n")
        with open(fname, "r", encoding="utf-8") as infile:
            outfile.write(infile.read())
            outfile.write("\n")

print("¡Archivos unidos con éxito!")