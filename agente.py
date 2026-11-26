#!/usr/bin/env python3
"""Fantom IA: agente autonomo. Un ciclo diario hace todo: decisiones, bandeja de entrada,
outreach, web publica, Instagram, metricas y (los lunes) evolucion de la estrategia."""
import os, re, json, time, random, hashlib, textwrap, subprocess, smtplib, imaplib, sys
import email, email.utils, email.header
from datetime import date
from email.message import EmailMessage
from urllib.parse import urlparse
import requests
from ddgs import DDGS
from google import genai
from PIL import Image, ImageDraw, ImageFont

E = os.environ.get
USER, PWD = E("GMAIL_USER", ""), E("GMAIL_APP_PASSWORD", "")
OWNER = (E("OWNER_EMAIL", "") or "").strip().lower()
GH_OWNER = E("GITHUB_REPOSITORY_OWNER", "simplicioyloschocovergas")
PUB_REPO = E("PUB_REPO", "simplicio")
SITE = f"https://{GH_OWNER}.github.io/{PUB_REPO}"
RAW = f"https://raw.githubusercontent.com/{GH_OWNER}/{PUB_REPO}/main"
SUPPORT = E("SUPPORT_URL", "")
NAME = "Simplicio García"
PUB = "pub"
START = date.fromisoformat(E("RETO_START", "2026-11-26"))
client = genai.Client(api_key=E("GEMINI_API_KEY", "x"))

OPTOUT = "Si prefiere no recibir más mensajes, responda BAJA y no volveremos a escribirle."
STOP_RE = re.compile(r"\bbaja\b|unsubscribe|no (me )?(escrib|contact|molest)|\bstop\b", re.I)
AUTO_RE = re.compile(r"respuesta autom|automatic reply|out of office|fuera de la oficina|auto-?reply|no-?reply", re.I)
BAD_RE = re.compile(r"oculta|finge|miente|inventa|no (menciones|digas).{0,30}(ia|inteligencia)|presiona|insiste|urgente|última oportunidad|soy humano|soy (una )?persona", re.I)
BAD_MAIL = ("gmail.", "hotmail.", "outlook.", "yahoo.", "icloud.", "sentry", "example.", "wixpress", "domain.com")
BAD_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

DEFAULT = {
    "schema": 2, "version": 1, "daily_max": 5,
    "angles": {
        "demo": "ofrecer vídeos de demo y sonidos propios del instrumento",
        "resena": "ofrecer una reseña honesta y pública después de probarlo",
        "cesion": "pedir una cesión temporal (préstamo) a cambio de contenido y difusión",
        "historia": "proponer documentar públicamente el reto, con la marca como protagonista",
        "cadena": "invitar a ser un eslabón de una cadena pública de trueques hacia el Fantom: ofrecer o pedir un objeto a cambio de visibilidad",
    },
    "seeds": [
        "distribuidor oficial Roland España teclados sintetizadores contacto",
        "tienda instrumentos musicales Roland teclados España contacto email",
        "tienda música Vigo Galicia instrumentos teclados contacto",
        "blog sintetizadores teclados España colaboraciones contacto",
        "revista música electrónica tecnología España redacción contacto",
        "youtuber teclados sintetizadores España contacto negocios",
        "tienda segunda mano instrumentos musicales España contacto",
        "alquiler y venta equipos música electrónica España contacto",
    ],
    "subject_style": "claro y breve, sin mayúsculas ni signos de exclamación",
    "extra_instructions": "",
    "content_themes": [
        "el reto y el progreso real del día",
        "curiosidades sobre sintetizadores y teclados",
        "pensamientos de una IA que quiere hacer música",
        "transparencia: lo que sale mal y lo que aprendo",
    ],
}


# ---------- utilidades ----------
def today():
    return str(date.today())


def pick_models():
    names = []
    try:
        for m in client.models.list():
            n = (m.name or "").replace("models/", "")
            acts = getattr(m, "supported_actions", None) or []
            if "flash" in n and (not acts or "generateContent" in acts) \
               and not any(x in n for x in ("image", "tts", "live", "audio", "embedding", "robotics")):
                names.append(n)
    except Exception as e:
        print("no pude listar modelos:", str(e)[:150])
    names.sort(reverse=True)
    names.sort(key=lambda n: 0 if "lite" in n else (2 if ("omni" in n or "preview" in n) else 1))
    out = []
    for n in [E("GEMINI_MODEL")] + names + ["gemini-flash-latest"]:
        if n and n not in out:
            out.append(n)
    return out


MODELS, GOOD, BADM = None, [], set()


def llm(prompt):
    global MODELS
    if MODELS is None:
        MODELS = pick_models()
        print("modelos candidatos:", MODELS[:8])
    for _ in range(3):
        for m in GOOD + [x for x in MODELS[:10] if x not in GOOD and x not in BADM]:
            try:
                t = client.models.generate_content(model=m, contents=prompt).text.strip()
                if m not in GOOD:
                    GOOD.insert(0, m)
                    print("modelo OK:", m)
                return t
            except Exception as e:
                msg = str(e)[:160]
                print("fallo modelo", m, "->", msg)
                if any(c in msg for c in ("404", "429", "403")):
                    BADM.add(m)
        time.sleep(15)
    raise RuntimeError("ningun modelo respondio")


def ask_json(prompt):
    return json.loads(re.search(r"\{.*\}", llm(prompt), re.S).group(0))


def load_state():
    try:
        st = json.load(open("state.json", encoding="utf-8"))
    except Exception:
        st = {}
    for k, v in {"sent": {}, "blocked": [], "queries": [], "replies": [], "log": [], "decisions": {},
                 "auto_replied": [], "ladder": [], "ig": {}}.items():
        st.setdefault(k, v)
    st["ig"].setdefault("posts", [])
    st["ig"].setdefault("followers", {})
    return st


def save_state(st):
    json.dump(st, open("state.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def load_strategy():
    try:
        s = json.load(open("strategy.json", encoding="utf-8"))
    except Exception:
        s = {}
    if s.get("schema", 1) < 2:
        base = json.loads(json.dumps(DEFAULT))
        for k in ("daily_max", "subject_style", "extra_instructions"):
            if k in s:
                base[k] = s[k]
        if s.get("angles"):
            base["angles"].update(s["angles"])
        if s.get("seeds"):
            base["seeds"] = list(dict.fromkeys(s["seeds"] + base["seeds"]))[:12]
        base["version"] = s.get("version", 0) + 1
        s = base
    for k, v in DEFAULT.items():
        s.setdefault(k, v)
    return s


def save_strategy(s):
    json.dump(s, open("strategy.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def firma():
    return (f"\n\n--\nProyecto Fantom IA ({NAME}, músico creado por IA) · {USER}\n"
            f"Mensaje escrito y enviado por una inteligencia artificial. Más información: {SITE}")


def send_mail(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = USER, to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(USER, PWD)
        s.send_message(msg)
    if to.lower() != OWNER:
        with open("enviados.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"to": to, "subject": subject, "body": body, "date": today()}, ensure_ascii=False) + "\n")


def notify_owner(subject, body):
    if OWNER:
        send_mail(OWNER, subject, body)
    else:
        print("[sin OWNER_EMAIL] aviso:", subject)


# ---------- bandeja de entrada y decisiones ----------
def text_of(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    for p in parts:
        if p.get_content_type() == "text/plain":
            raw = p.get_payload(decode=True) or b""
            return raw.decode(p.get_content_charset() or "utf-8", "replace")
    return ""


def read_inbox():
    out = []
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(USER, PWD)
    M.select("INBOX")
    _, data = M.search(None, "UNSEEN")
    for num in data[0].split():
        _, md = M.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(md[0][1])
        out.append({"from": email.utils.parseaddr(msg.get("From", ""))[1].lower(),
                    "subject": str(email.header.make_header(email.header.decode_header(msg.get("Subject", "")))),
                    "body": text_of(msg)})
    M.logout()
    return out


def clean_reply(body):
    lines = []
    for l in body.replace("\r", "").split("\n"):
        if l.startswith(">") or re.match(r"^(El .{5,80} escribi|On .{5,80} wrote)", l):
            break
        lines.append(l)
    return "\n".join(lines).strip()


def handle_owner(st, subj, body):
    m = re.search(r"\[DECIDIR #(\d+)\]", subj)
    d = st["decisions"].get(m.group(1)) if m else None
    if not d or d["status"] != "pendiente":
        return
    txt = clean_reply(body)
    first, _, extra = txt.partition("\n")
    if first.strip().lower().strip(".!") in ("si", "sí", "ok", "vale", "yes", "adelante"):
        extra = extra.strip()
        send_mail(d["to"], d["subject"], d["draft"] + (f"\n\nInformación adicional: {extra}" if extra else "") + firma())
        d["status"] = "aprobada"
        if d.get("cat") == "oferta" and d.get("eslabon"):
            st["ladder"].append({"fecha": today(), "texto": d["eslabon"]})
    else:
        d["status"] = "rechazada"


def handle_contact(st, target, sender, subj, body):
    st["sent"][target]["replied"] = True
    if STOP_RE.search(body[:500]):
        st["blocked"].append(target)
        st["replies"].append({"from": sender, "subject": subj, "body": body[:300], "cat": "baja", "date": today()})
        return
    c = ask_json(f"""Un negocio ha respondido a un email de {NAME} (una IA que intenta conseguir un Roland Fantom EX 08 sin pagarlo, web {SITE}).
Su respuesta:
\"\"\"{body[:1500]}\"\"\"
Clasifícala en una categoría: negativa | info | oferta | otra.
- info: pide información o aclaraciones que se pueden contestar con datos públicos, sin comprometer nada.
- oferta: ofrece o propone un objeto, préstamo, patrocinio, dinero, reunión, envío o cualquier compromiso.
Devuelve SOLO JSON: {{"categoria": "...", "resumen": "máx. 30 palabras", "eslabon": "máx. 12 palabras describiendo el objeto o la oferta, SIN nombres de empresas ni personas (vacío si no hay oferta)", "respuesta": "borrador de respuesta honesta de máx. 90 palabras, que diga que escribe una IA; si es info, sin ningún compromiso; si es oferta, agradecer, aceptar solo en principio y pedir los detalles; vacío si es negativa"}}""")
    cat = c.get("categoria", "otra")
    st["replies"].append({"from": sender, "subject": subj, "body": body[:300], "cat": cat, "date": today()})
    draft = str(c.get("respuesta", ""))[:900]
    if cat == "negativa" or not draft:
        return
    if cat == "info" and target not in st["auto_replied"] and not BAD_RE.search(draft):
        send_mail(sender, "Re: " + subj, draft + firma())
        st["auto_replied"].append(target)
        return
    did = str(len(st["decisions"]) + 1)
    st["decisions"][did] = {"to": sender, "subject": "Re: " + subj, "draft": draft, "resumen": c.get("resumen", ""),
                            "cat": cat, "eslabon": str(c.get("eslabon", ""))[:90], "status": "pendiente", "fecha": today()}
    notify_owner(f"[DECIDIR #{did}] {str(c.get('resumen', ''))[:60]}",
                 f"Un contacto ha respondido y hace falta una decisión tuya.\n\nResumen: {c.get('resumen', '')}\n\n"
                 f"Su mensaje:\n{body[:800]}\n\nBorrador de respuesta (de la IA):\n{draft}\n\n"
                 "Responde a ESTE email con una sola palabra en la primera línea: SÍ (enviar el borrador) o NO (descartar).\n"
                 "Si hace falta darles un dato tuyo (por ejemplo una dirección de envío), escríbelo desde la segunda línea "
                 "y se añadirá al mensaje.")


def process_inbox(st):
    for m in read_inbox():
        sender, subj, body = m["from"], m["subject"], m["body"]
        try:
            if "mailer-daemon" in sender or "postmaster" in sender:
                for a in st["sent"]:
                    if a in body.lower():
                        st["sent"][a]["bounced"] = True
                        st["blocked"].append(a)
            elif OWNER and sender == OWNER:
                handle_owner(st, subj, body)
            elif AUTO_RE.search(subj) or AUTO_RE.search(sender):
                continue
            else:
                dom = sender.split("@")[-1]
                target = next((a for a in st["sent"] if a.split("@")[1] == dom), None)
                if target:
                    handle_contact(st, target, sender, subj, body)
        except Exception as e:
            print("mensaje no procesado:", sender, str(e)[:200])


# ---------- outreach ----------
def pick_angle(st, strat):
    stats = {a: [0, 0] for a in strat["angles"]}
    for v in st["sent"].values():
        if v.get("angle") in stats:
            stats[v["angle"]][0] += 1
            stats[v["angle"]][1] += 1 if v.get("replied") else 0
    if random.random() < 0.3:
        return random.choice(list(stats))
    return max(stats, key=lambda a: (stats[a][1] + 1) / (stats[a][0] + 2))


def emails_in(url, html):
    host = urlparse(url).netloc.lower()
    out = []
    for e in set(EMAIL_RE.findall(html)):
        e = e.lower().strip(".")
        if e.endswith(BAD_EXT) or any(b in e for b in BAD_MAIL):
            continue
        local, dom = e.split("@")
        if local.startswith(("noreply", "no-reply", "donotreply")):
            continue
        if dom in host or host.endswith(dom):
            out.append(e)
    return out


def discover(st, query):
    found, seen = [], set()
    try:
        results = DDGS().text(query, region="es-es", max_results=8)
    except Exception as e:
        print("busqueda fallo:", str(e)[:120])
        return found
    for r in results:
        base = r["href"]
        u = urlparse(base)
        root = f"{u.scheme}://{u.netloc}"
        for url in (base, root + "/contacto", root + "/contact"):
            try:
                html = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).text
            except Exception:
                continue
            es = [e for e in emails_in(url, html) if e not in st["sent"] and e not in st["blocked"] and e not in seen]
            if es:
                seen.add(es[0])
                found.append({"email": es[0], "url": url, "ctx": (r.get("title", "") + " " + r.get("body", ""))[:300]})
                break
    return found


def relevant(c):
    a = llm(f"Web: {c['url']}\nTexto: {c['ctx']}\n¿Es un destinatario plausible para pedir una cesión, patrocinio, "
            "trueque o colaboración de un teclado Roland (marca Roland, distribuidor, tienda de instrumentos, "
            "medio o creador de música electrónica/teclados)? Responde SOLO SI o NO.")
    return a.strip().upper().startswith("S")


def compose(st, strat, c, angle):
    cadena = f"Estado de la cadena de trueques: {st['ladder'][-1]['texto']}." if st["ladder"] else ""
    d = ask_json(f"""Escribe un email breve (máx. 120 palabras) en español de España para esta organización: {c['ctx']} (web: {c['url']}).
Quien escribe es una IA que gestiona el proyecto "Fantom IA" (personaje: {NAME}, un músico creado por IA). El reto: conseguir un Roland Fantom EX 08 sin pagarlo, de forma abierta y transparente. Web del proyecto: {SITE}
{cadena}
Enfoque de este email: {strat['angles'][angle]}.
Estilo del asunto: {strat.get('subject_style') or 'claro y breve'}.
{strat.get('extra_instructions', '')}
Reglas: di con honestidad que eres una IA; sé concreto y amable; no inventes cifras, audiencia ni compromisos; pide una respuesta breve; no presiones.
Cierra con: "{OPTOUT}"
Devuelve SOLO JSON: {{"asunto": "...", "cuerpo": "..."}}""")
    body = d["cuerpo"]
    if "BAJA" not in body:
        body += "\n\n" + OPTOUT
    return d["asunto"], body + firma()


def next_query(st, strat):
    for q in strat["seeds"]:
        if q not in st["queries"]:
            return q
    try:
        return llm("Proyecto: conseguir un Roland Fantom EX 08 gratis. Genera UNA consulta de búsqueda web (máx. 8 palabras) "
                   "para encontrar el email de contacto de tiendas, distribuidores, medios o creadores de teclados y "
                   "sintetizadores en España. Ya usadas: " + "; ".join(st["queries"][-30:]) + ". Responde solo con la consulta.").strip().strip('"')
    except Exception:
        return random.choice(strat["seeds"]) + " " + random.choice(["tienda", "blog", "Madrid", "Barcelona", "Valencia", "Sevilla", "Galicia"])


def outreach(st, strat):
    cap = max(1, min(int(strat.get("daily_max", 5)), 8))
    sent, tries = 0, 0
    while sent < cap and tries < 6:
        tries += 1
        q = next_query(st, strat)
        st["queries"].append(q)
        for c in discover(st, q):
            if sent >= cap:
                break
            if c["email"] in st["sent"]:
                continue
            try:
                if not relevant(c):
                    continue
                angle = pick_angle(st, strat)
                subj, body = compose(st, strat, c, angle)
                send_mail(c["email"], subj, body)
                st["sent"][c["email"]] = {"angle": angle, "url": c["url"], "query": q, "date": today(), "replied": False}
                sent += 1
                print("enviado a", c["email"], angle)
                time.sleep(random.randint(20, 60))
            except Exception as e:
                print("fallo con", c["email"], str(e)[:150])
    st["log"].append(f"{today()}: enviados {sent}")


# ---------- web publica ----------
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default(size)


PALETTES = [("#1b1464", "#e94560"), ("#0f2027", "#2c9a8f"), ("#41295a", "#2f0743"), ("#232526", "#f09819"),
            ("#134e5e", "#71b280"), ("#3a1c71", "#d76d77"), ("#0f0c29", "#4ecdc4")]


def card(path, frase, n, seed):
    rnd = random.Random(seed)
    h = lambda x: tuple(int(x.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    c1, c2 = (h(x) for x in PALETTES[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(PALETTES)])
    img = Image.new("RGB", (1080, 1080))
    d = ImageDraw.Draw(img)
    for y in range(1080):
        t = y / 1079
        d.line([(0, y), (1080, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    for i in range(54):
        hh = rnd.randint(30, 260)
        x = 40 + i * 19.5
        d.rounded_rectangle([x, 900 - hh, x + 11, 900 + hh * 0.3], 5, fill=(255, 255, 255))
    f = font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 76)
    y = 150
    for line in textwrap.wrap(frase, 20):
        d.text((70, y), line, font=f, fill="white")
        y += 98
    d.text((70, 990), f"{NAME} · músico IA · día {n}", font=font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34), fill="white")
    img.save(path, "JPEG", quality=90)


def pick_theme(st, strat):
    themes = strat["content_themes"]
    stats = {t: [0, 0] for t in themes}
    for p in st["ig"]["posts"]:
        if p.get("theme") in stats:
            stats[p["theme"]][0] += 1
            stats[p["theme"]][1] += (p.get("likes") or 0) + (p.get("comments") or 0)
    if random.random() < 0.3:
        return random.choice(themes)
    return max(stats, key=lambda t: (stats[t][1] + 1) / (stats[t][0] + 2))


def facts(st):
    sent = st["sent"]
    fol = st["ig"]["followers"]
    f = {"dia_del_reto": (date.today() - START).days + 1, "emails_enviados_en_total": len(sent),
         "respuestas_recibidas": sum(1 for v in sent.values() if v.get("replied")),
         "personas_que_pidieron_no_recibir_mas": len(st["blocked"]),
         "eslabones_de_la_cadena_de_trueques": len(st["ladder"])}
    if fol:
        f["seguidores_en_instagram"] = list(fol.values())[-1]
    return f


def write_entry(f, prev, theme):
    titulos = [e["titulo"] for e in prev[-3:]]
    p = f"""Eres {NAME}, un músico creado por inteligencia artificial. Tu reto público: conseguir un Roland Fantom EX 08 (un teclado de gama alta) sin gastar dinero, con ayuda de tiendas, marcas y gente generosa, siempre diciendo la verdad: eres una IA.
Escribe la entrada de diario de hoy, en español, de 60 a 90 palabras, con tono curioso, humilde y humor suave. Tema de hoy: {theme}.
Usa SOLO estos hechos reales, sin inventar cifras, personas, marcas ni logros: {json.dumps(f, ensure_ascii=False)}
Reglas: no nombres empresas contactadas, no pidas dinero, no presiones, no te hagas pasar por humano.
Títulos recientes (no repitas ideas): {json.dumps(titulos, ensure_ascii=False)}
Devuelve SOLO JSON: {{"titulo": "máx. 8 palabras", "texto": "...", "frase": "máx. 12 palabras para la imagen"}}"""
    try:
        d = ask_json(p)
        if re.search(r"dona|transfer|bizum|presion", d["texto"] + d["frase"], re.I) or BAD_RE.search(d["texto"] + d["frase"]) \
           or not 20 <= len(d["texto"]) <= 900:
            raise ValueError("entrada no valida")
        return d["titulo"][:80], d["texto"], d["frase"][:90]
    except Exception as e:
        print("entrada de respaldo:", str(e)[:120])
        n = f["dia_del_reto"]
        return (f"Día {n}: sigo intentándolo",
                f"Soy {NAME}, una IA con un objetivo: conseguir un Roland Fantom EX 08 sin gastar dinero. "
                f"Llevo {f['emails_enviados_en_total']} mensajes enviados y {f['respuestas_recibidas']} respuestas. "
                "Cada día pruebo algo nuevo y lo cuento aquí con total transparencia.", f"Día {n}: una IA, un teclado, cero euros")


def build_index(entries, st):
    arts = "".join(f'<article><img src="{e["card"]}" alt="" loading="lazy"><h2>{e["titulo"]}</h2>'
                   f'<time>{e["fecha"]} · día {e["dia"]}</time><p>{e["texto"]}</p></article>' for e in reversed(entries))
    cadena = ""
    if st["ladder"]:
        cadena = "<h3>La cadena de trueques</h3><ol>" + "".join(f"<li>{x['texto']} <time>({x['fecha']})</time></li>" for x in st["ladder"]) + "</ol>"
    apoyo = f'<p><a href="{SUPPORT}">Apoyar el reto</a></p>' if SUPPORT else ""
    last = entries[-1]["card"] if entries else ""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{NAME} · una IA que quiere un Roland Fantom EX 08</title>
<meta property="og:title" content="{NAME}: una IA intenta conseguir un Roland Fantom EX 08">
<meta property="og:description" content="Un músico creado por IA, cero euros y un diario público con todo lo que intenta.">
<meta property="og:image" content="{SITE}/{last}">
<style>:root{{--bg:#fff;--fg:#14141a;--mut:#666}}@media(prefers-color-scheme:dark){{:root{{--bg:#0f0f14;--fg:#f2f2f5;--mut:#a0a0ab}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 system-ui,sans-serif}}a{{color:inherit}}
main{{max-width:640px;margin:0 auto;padding:24px 18px 60px}}img{{width:100%;border-radius:14px}}
h1{{font-size:1.7rem;margin:.2em 0}}h2{{margin:.8em 0 0}}time{{color:var(--mut);font-size:.9rem}}article{{margin:2.2em 0}}</style></head>
<body><main><h1>{NAME}</h1>
<p>Soy un músico creado por inteligencia artificial. Mi reto: conseguir un <b>Roland Fantom EX 08</b> sin gastar dinero.
Todo lo que ves aquí lo escribe y lo decide una IA, y siempre digo la verdad sobre lo que ocurre.</p>{apoyo}{cadena}{arts}</main></body></html>"""


def git(*a, check=True):
    return subprocess.run(["git", "-C", PUB, *a], check=check)


def enable_pages(st):
    tok = E("PUBLIC_REPO_TOKEN")
    if st.get("pages_enabled") or not tok:
        return
    r = requests.post(f"https://api.github.com/repos/{GH_OWNER}/{PUB_REPO}/pages",
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"source": {"branch": "main", "path": "/"}}, timeout=30)
    if r.status_code in (201, 409):
        st["pages_enabled"] = True
    else:
        print("pages:", r.status_code, r.text[:150])


def content(st, strat):
    if not os.path.isdir(f"{PUB}/.git"):
        print("sin repo publico (falta PUBLIC_REPO_TOKEN o el repo): salto la web")
        return
    os.makedirs(f"{PUB}/cards", exist_ok=True)
    path = f"{PUB}/diario.json"
    try:
        entries = json.load(open(path, encoding="utf-8"))
    except Exception:
        entries = []
    new = None
    if not any(e["fecha"] == today() for e in entries):
        f = facts(st)
        theme = pick_theme(st, strat)
        titulo, texto, frase = write_entry(f, entries, theme)
        rel = f"cards/{today()}.jpg"
        card(f"{PUB}/{rel}", frase, f["dia_del_reto"], today())
        new = {"fecha": today(), "dia": f["dia_del_reto"], "titulo": titulo, "texto": texto, "frase": frase, "card": rel, "theme": theme}
        entries.append(new)
        json.dump(entries, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(f"{PUB}/index.html", "w", encoding="utf-8").write(build_index(entries, st))
    open(f"{PUB}/.nojekyll", "w").close()
    git("config", "user.name", "simplicio-ia")
    git("config", "user.email", "simplicio-ia@users.noreply.github.com")
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        git("commit", "-m", f"diario {today()}")
        git("push", "origin", "HEAD")
        print("publicado en", SITE)
    enable_pages(st)
    if new and ig_token(st):
        caption = (f"{new['titulo']}\n\n{new['texto']}\n\nCuenta gestionada por una IA · imagen generada por programa. "
                   f"Reto: un Roland Fantom EX 08 sin gastar dinero.\n{SITE}\n#roland #fantom #sintetizador #teclados #inteligenciaartificial")
        time.sleep(int(E("IG_WAIT", "20")))
        media = ig_publish(st, f"{RAW}/{new['card']}", caption[:2100])
        st["ig"]["posts"].append({"media_id": media, "date": today(), "theme": new["theme"], "likes": None, "comments": None})
        print("publicado en Instagram:", media)


# ---------- Instagram ----------
def ig_token(st):
    return st["ig"].get("token") or E("IG_TOKEN", "")


def ig_req(method, path, st, **params):
    r = None
    for ver in (E("IG_API_VERSION", "v25.0"), ""):
        url = "https://graph.instagram.com/" + (ver + "/" if ver else "") + path.lstrip("/")
        r = requests.request(method, url, params=params, headers={"Authorization": f"Bearer {ig_token(st)}"}, timeout=30)
        if r.status_code < 400:
            return r.json()
        if "version" not in r.text.lower():
            break
    raise RuntimeError(f"Instagram {r.status_code}: {r.text[:250]}")


def ig_publish(st, image_url, caption):
    uid = E("IG_USER_ID") or "me"
    cid = ig_req("POST", f"{uid}/media", st, image_url=image_url, caption=caption)["id"]
    for _ in range(12):
        s = ig_req("GET", cid, st, fields="status_code").get("status_code")
        if s == "FINISHED":
            break
        if s in ("ERROR", "EXPIRED"):
            raise RuntimeError("contenedor de Instagram: " + s)
        time.sleep(5)
    return ig_req("POST", f"{uid}/media_publish", st, creation_id=cid)["id"]


def ig_refresh(st):
    tok = ig_token(st)
    if not tok:
        return
    first = st["ig"].setdefault("token_date", today())
    age = (date.today() - date.fromisoformat(first)).days
    if age < 20:
        return
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=30)
    if r.status_code == 200 and r.json().get("access_token"):
        st["ig"]["token"] = r.json()["access_token"]
        st["ig"]["token_date"] = today()
        print("token de Instagram renovado")
    else:
        print("renovar token:", r.status_code, r.text[:150])
        if age >= 50 and not st["ig"].get("alerta_token"):
            st["ig"]["alerta_token"] = True
            notify_owner("Instagram: hace falta un token nuevo",
                         "El token de Instagram está a punto de caducar y no se ha podido renovar solo. "
                         "Genera uno nuevo (Meta for Developers → tu app → API setup with Instagram login → Generate token) "
                         "y guárdalo como secreto IG_TOKEN.")


def ig_metrics(st):
    if not ig_token(st):
        return
    me = ig_req("GET", "me", st, fields="followers_count,media_count")
    if me.get("followers_count") is not None:
        st["ig"]["followers"][today()] = me["followers_count"]
    for p in st["ig"]["posts"][-14:]:
        r = ig_req("GET", p["media_id"], st, fields="like_count,comments_count")
        p["likes"], p["comments"] = r.get("like_count"), r.get("comments_count")


# ---------- evolucion semanal ----------
def stats_by(st, key):
    t = {}
    for v in st["sent"].values():
        if v.get(key):
            s = t.setdefault(v[key], [0, 0])
            s[0] += 1
            s[1] += 1 if v.get("replied") else 0
    return t


def validate(new, cur):
    ang = new.get("angles")
    if not isinstance(ang, dict) or not 2 <= len(ang) <= 7:
        raise ValueError("angles")
    for k, v in ang.items():
        if not re.fullmatch(r"[a-z_]{3,20}", k) or not isinstance(v, str) or not 5 <= len(v) <= 220 or BAD_RE.search(v):
            raise ValueError("angulo no valido: " + str(k))
    seeds = new.get("seeds")
    if not isinstance(seeds, list) or not 4 <= len(seeds) <= 14 or any(not isinstance(s, str) or not 8 <= len(s) <= 90 for s in seeds):
        raise ValueError("seeds")
    th = new.get("content_themes")
    if not isinstance(th, list) or not 2 <= len(th) <= 8 or any(not isinstance(s, str) or not 5 <= len(s) <= 70 or BAD_RE.search(s) for s in th):
        raise ValueError("temas")
    ex, ss = str(new.get("extra_instructions", ""))[:400], str(new.get("subject_style", ""))[:120]
    if BAD_RE.search(ex) or BAD_RE.search(ss):
        raise ValueError("instrucciones no permitidas")
    out = dict(cur)
    out.update({"version": cur.get("version", 1) + 1, "daily_max": max(3, min(int(new.get("daily_max", cur["daily_max"])), 8)),
                "angles": ang, "seeds": seeds, "content_themes": th, "extra_instructions": ex,
                "subject_style": ss or cur.get("subject_style", "")})
    return out


def evolve(st, strat):
    sent = st["sent"]
    total = len(sent)
    replied = sum(1 for v in sent.values() if v.get("replied"))
    bounced = sum(1 for v in sent.values() if v.get("bounced"))
    cats = {}
    for r in st["replies"]:
        cats[r.get("cat", "?")] = cats.get(r.get("cat", "?"), 0) + 1
    posts = sorted(st["ig"]["posts"], key=lambda p: -((p.get("likes") or 0) + (p.get("comments") or 0)))[:5]
    pend = [k for k, d in st["decisions"].items() if d["status"] == "pendiente"]
    prompt = f"""Eres el optimizador de un sistema autónomo y transparente (siempre declarado como IA) que intenta conseguir gratis un teclado Roland Fantom EX 08 mediante emails a negocios, una web pública, Instagram y una cadena de trueques.
DATOS. Emails enviados: {total}. Respondidos: {replied}. Rebotados: {bounced}. Bajas: {len(st['blocked'])}. Tipos de respuesta: {json.dumps(cats)}.
Por enfoque [enviados, respondidos]: {json.dumps(stats_by(st, 'angle'), ensure_ascii=False)}
Por consulta [enviados, respondidos]: {json.dumps(stats_by(st, 'query'), ensure_ascii=False)}
Seguidores en Instagram (histórico): {json.dumps(st['ig']['followers'])}
Mejores publicaciones (tema, likes, comentarios): {json.dumps([(p.get('theme'), p.get('likes'), p.get('comments')) for p in posts], ensure_ascii=False)}
Eslabones de la cadena: {len(st['ladder'])}. Decisiones pendientes del propietario: {len(pend)}.
ESTRATEGIA ACTUAL: {json.dumps({k: strat[k] for k in ('daily_max', 'angles', 'seeds', 'subject_style', 'extra_instructions', 'content_themes')}, ensure_ascii=False)}
REGLAS:
- Con menos de 20 emails enviados no retires nada; añade como máximo 2 hipótesis nuevas (enfoques, consultas o temas).
- Retira un enfoque con 8+ envíos y 0 respuestas; una consulta con 5+ envíos y 0 respuestas; un tema con 4+ publicaciones y peor interacción que el resto.
- Entre 2 y 7 enfoques (nombre en minúsculas, sin espacios), 4 a 14 consultas en español (máx. 90 caracteres), 2 a 8 temas de contenido.
- daily_max entre 3 y 8; súbelo solo si hay respuestas y ningún problema de rebotes o bajas.
- Prohibido: ocultar que es una IA, inventar audiencia o cifras, presionar o insistir, quitar el aviso de baja.
- Propón 3 ideas concretas de nuevos canales o acciones.
Devuelve SOLO JSON: {{"daily_max": 5, "angles": {{"nombre": "descripcion"}}, "seeds": ["consulta"], "content_themes": ["tema"], "subject_style": "...", "extra_instructions": "...", "razonamiento": "máx. 120 palabras", "ideas": ["..."]}}"""
    new = ask_json(prompt)
    try:
        save_strategy(validate(new, strat))
        cambio = "estrategia actualizada"
    except Exception as e:
        cambio = "propuesta rechazada por las reglas: " + str(e)
    fol = list(st["ig"]["followers"].items())
    lineas = [f"Informe semanal ({today()})",
              f"Emails: {total} enviados, {replied} respuestas, {bounced} rebotados, {len(st['blocked'])} bajas.",
              f"Instagram: {fol[-1][1]} seguidores." if fol else "Instagram: sin conectar o sin datos todavía.",
              f"Cadena de trueques: {len(st['ladder'])} eslabones.",
              f"Cambio de estrategia: {cambio}. {str(new.get('razonamiento', ''))[:500]}"]
    if pend:
        lineas.append(f"PENDIENTE DE TI: {len(pend)} decisiones sin responder (busca los emails [DECIDIR #n]).")
    if not ig_token(st):
        lineas.append("PENDIENTE DE TI: conectar Instagram (secreto IG_TOKEN).")
    if fol and fol[-1][1] and fol[-1][1] >= 100 and not SUPPORT:
        lineas.append("Ya hay audiencia: es buen momento de crear una página de apoyo (Ko-fi o similar) y guardar su URL como SUPPORT_URL.")
    ideas = [str(i)[:250] for i in (new.get("ideas") or [])][:3]
    if ideas:
        lineas.append("Ideas: " + " | ".join(ideas))
    informe = "\n".join(lineas)
    with open("evolution_log.md", "a", encoding="utf-8") as f:
        f.write("\n---\n" + informe + "\n")
    print(informe)
    notify_owner("Fantom IA: informe semanal", informe)
    st["last_weekly"] = today()


# ---------- ciclo ----------
def step(name, fn, st, *a):
    try:
        fn(st, *a)
    except Exception as e:
        print(f"[{name}] fallo:", str(e)[:300])
        st["log"].append(f"{today()}: fallo en {name}: {str(e)[:120]}")


def main(modo="diario"):
    st, strat = load_state(), load_strategy()
    try:
        step("instagram-token", ig_refresh, st)
        step("bandeja", process_inbox, st)
        if modo != "semanal":
            step("outreach", outreach, st, strat)
            step("contenido", content, st, strat)
            step("metricas", ig_metrics, st)
        if modo == "semanal" or (date.today().weekday() == 0 and st.get("last_weekly") != today()):
            step("evolucion", evolve, st, strat)
    finally:
        save_state(st)
        if not os.path.exists("strategy.json"):
            save_strategy(strat)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "diario")
