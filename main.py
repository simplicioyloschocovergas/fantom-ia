import os, re, json, time, random, smtplib, imaplib, email, email.utils, email.header
from datetime import date
from email.message import EmailMessage
from urllib.parse import urlparse
import requests
from ddgs import DDGS
from google import genai

USER = os.environ["GMAIL_USER"]
PWD = os.environ["GMAIL_APP_PASSWORD"]
OWNER = os.environ.get("OWNER_EMAIL", "")
DAILY_MAX = int(os.environ.get("DAILY_MAX", "5"))
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODELS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-3-flash-preview",
                      "gemini-flash-latest", "gemini-2.5-flash"] if m]

SEEDS = [
    "distribuidor oficial Roland España teclados sintetizadores contacto",
    "tienda instrumentos musicales Roland teclados España contacto email",
    "tienda música Vigo Galicia instrumentos teclados contacto",
    "blog sintetizadores teclados España colaboraciones contacto",
    "revista música electrónica tecnología España redacción contacto",
    "youtuber teclados sintetizadores España contacto negocios",
]
ANGLES = {
    "demo": "ofrecer vídeos de demo y sonidos propios del instrumento",
    "resena": "ofrecer una reseña honesta y pública después de probarlo",
    "cesion": "pedir una cesión temporal (préstamo) a cambio de contenido y difusión",
    "historia": "proponer documentar públicamente el reto, con la marca como protagonista",
}
BAD = ("gmail.", "hotmail.", "outlook.", "yahoo.", "icloud.", "sentry", "example.", "wixpress", "domain.com")
BAD_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
STOP_RE = re.compile(r"\bbaja\b|unsubscribe|no (me )?(escrib|contact|molest)|\bstop\b", re.I)


def load():
    try:
        return json.load(open("state.json"))
    except Exception:
        return {"sent": {}, "blocked": [], "queries": [], "replies": [], "log": []}


def save(st):
    json.dump(st, open("state.json", "w"), ensure_ascii=False, indent=1)


def llm(prompt):
    err = None
    for m in MODELS:
        try:
            return client.models.generate_content(model=m, contents=prompt).text.strip()
        except Exception as e:
            err = e
            time.sleep(3)
    raise err


def send_mail(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = USER, to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(USER, PWD)
        s.send_message(msg)


def text_of(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    for p in parts:
        if p.get_content_type() == "text/plain":
            raw = p.get_payload(decode=True) or b""
            return raw.decode(p.get_content_charset() or "utf-8", "replace")
    return ""


def check_inbox(st):
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(USER, PWD)
    M.select("INBOX")
    _, data = M.search(None, "UNSEEN")
    for num in data[0].split():
        _, md = M.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(md[0][1])
        sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        subj = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
        body = text_of(msg)
        if "mailer-daemon" in sender or "postmaster" in sender:
            for a in st["sent"]:
                if a in body.lower():
                    st["sent"][a]["bounced"] = True
                    st["blocked"].append(a)
            continue
        dom = sender.split("@")[-1]
        target = next((a for a in st["sent"] if a.split("@")[1] == dom), None)
        if not target:
            continue
        st["sent"][target]["replied"] = True
        st["replies"].append({"from": sender, "subject": subj, "body": body[:600], "date": str(date.today())})
        if STOP_RE.search(body[:500]):
            st["blocked"].append(target)
        elif OWNER:
            send_mail(OWNER, "Respuesta al proyecto Fantom IA: " + subj, f"De: {sender}\n\n{body[:3000]}")
    M.logout()


def emails_in(url, html):
    host = urlparse(url).netloc.lower()
    out = []
    for e in set(EMAIL_RE.findall(html)):
        e = e.lower().strip(".")
        if e.endswith(BAD_EXT) or any(b in e for b in BAD):
            continue
        local, dom = e.split("@")
        if local.startswith(("noreply", "no-reply", "donotreply")):
            continue
        if dom in host or host.endswith(dom):
            out.append(e)
    return out


def discover(st, query):
    found = []
    try:
        results = DDGS().text(query, region="es-es", max_results=8)
    except Exception as e:
        print("busqueda fallo:", e)
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
            es = [e for e in emails_in(url, html) if e not in st["sent"] and e not in st["blocked"]]
            if es:
                found.append({"email": es[0], "url": url, "ctx": (r.get("title", "") + " " + r.get("body", ""))[:300]})
                break
    return found


def relevant(c):
    a = llm(f"Web: {c['url']}\nTexto: {c['ctx']}\n¿Es un destinatario plausible para pedir una cesión, "
            "patrocinio o colaboración de un teclado Roland (marca Roland, distribuidor, tienda de instrumentos, "
            "medio o creador de música electrónica/teclados)? Responde SOLO SI o NO.")
    return a.strip().upper().startswith("S")


def pick_angle(st):
    stats = {a: [0, 0] for a in ANGLES}
    for v in st["sent"].values():
        if v.get("angle") in stats:
            stats[v["angle"]][0] += 1
            stats[v["angle"]][1] += 1 if v.get("replied") else 0
    if random.random() < 0.3:
        return random.choice(list(ANGLES))
    return max(stats, key=lambda a: (stats[a][1] + 1) / (stats[a][0] + 2))


def compose(c, angle):
    t = llm(f"""Escribe un email breve (máx. 110 palabras) en español de España para esta organización: {c['ctx']} (web: {c['url']}).
Quien escribe es una IA que gestiona el proyecto "Fantom IA" en nombre de un músico de Vigo. El reto: conseguir un Roland Fantom EX 08 sin pagarlo.
Enfoque de este email: {ANGLES[angle]}.
Reglas: di con honestidad que eres una IA; sé concreto y amable; no inventes cifras, audiencia ni compromisos; pide una respuesta breve.
Cierra con: "Si prefiere no recibir más mensajes, responda BAJA y no volveremos a escribirle."
Devuelve SOLO JSON: {{"asunto": "...", "cuerpo": "..."}}""")
    d = json.loads(re.search(r"\{.*\}", t, re.S).group(0))
    firma = f"\n\n--\nProyecto Fantom IA · {USER}\nMensaje enviado por una IA en nombre de un músico de Vigo (España)."
    return d["asunto"], d["cuerpo"] + firma


def next_query(st):
    for q in SEEDS:
        if q not in st["queries"]:
            return q
    q = llm("Proyecto: conseguir un Roland Fantom EX 08 gratis. Genera UNA consulta de búsqueda web (máx. 8 palabras) "
            "para encontrar el email de contacto de tiendas, distribuidores, medios o creadores de teclados y "
            "sintetizadores en España. Ya usadas: " + "; ".join(st["queries"][-30:]) + ". Responde solo con la consulta.")
    return q.strip().strip('"')


def main():
    st = load()
    try:
        try:
            check_inbox(st)
        except Exception as e:
            print("inbox fallo:", e)
        sent, tries = 0, 0
        while sent < DAILY_MAX and tries < 6:
            tries += 1
            q = next_query(st)
            st["queries"].append(q)
            for c in discover(st, q):
                if sent >= DAILY_MAX:
                    break
                try:
                    if not relevant(c):
                        continue
                    angle = pick_angle(st)
                    subj, body = compose(c, angle)
                    send_mail(c["email"], subj, body)
                    st["sent"][c["email"]] = {"angle": angle, "url": c["url"], "query": q,
                                              "date": str(date.today()), "replied": False}
                    sent += 1
                    print("enviado a", c["email"], angle)
                    time.sleep(random.randint(20, 60))
                except Exception as e:
                    print("fallo con", c["email"], e)
        st["log"].append(f"{date.today()}: enviados {sent}")
    finally:
        save(st)


if __name__ == "__main__":
    main()
