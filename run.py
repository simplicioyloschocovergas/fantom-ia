import os, time, random
import main

def pick_models():
    names = []
    try:
        for m in main.client.models.list():
            n = (m.name or "").replace("models/", "")
            acts = getattr(m, "supported_actions", None) or []
            if "flash" in n and (not acts or "generateContent" in acts) \
               and not any(x in n for x in ("image", "tts", "live", "audio", "embedding", "robotics")):
                names.append(n)
    except Exception as e:
        print("no pude listar modelos:", str(e)[:200])
    names.sort(reverse=True)
    out = []
    for n in [os.environ.get("GEMINI_MODEL")] + names + ["gemini-3.8-flash", "gemini-flash-latest"]:
        if n and n not in out:
            out.append(n)
    return out

MODELS = pick_models()
GOOD = []
print("modelos candidatos:", MODELS[:8])

def llm(prompt):
    order = GOOD + [m for m in MODELS[:6] if m not in GOOD]
    for m in order:
        for _ in range(2):
            try:
                t = main.client.models.generate_content(model=m, contents=prompt).text.strip()
                if m not in GOOD:
                    GOOD.insert(0, m)
                    print("modelo OK:", m)
                return t
            except Exception as e:
                msg = str(e)[:250]
                print("fallo modelo", m, "->", msg)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    time.sleep(20)
                else:
                    break
    raise RuntimeError("ningun modelo respondio")

def next_query(st):
    for q in main.SEEDS:
        if q not in st["queries"]:
            return q
    try:
        q = llm("Proyecto: conseguir un Roland Fantom EX 08 gratis. Genera UNA consulta de búsqueda web (máx. 8 palabras) "
                "para encontrar el email de contacto de tiendas, distribuidores, medios o creadores de teclados y "
                "sintetizadores en España. Ya usadas: " + "; ".join(st["queries"][-30:]) + ". Responde solo con la consulta.")
        return q.strip().strip('"')
    except Exception as e:
        print("sin consulta nueva del modelo:", e)
        return random.choice(main.SEEDS) + " " + random.choice(
            ["tienda", "blog", "colaboración", "Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao", "Galicia"])

main.llm = llm
main.next_query = next_query

if __name__ == "__main__":
    main.main()
