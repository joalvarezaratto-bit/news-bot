#!/usr/bin/env python3
"""
Bot de noticias + calendario economico -> alertas a Telegram.

Comandos:
    python3 newsbot.py test      -> manda un mensaje de prueba a tu Telegram
    python3 newsbot.py chatid    -> descubre tu CHAT_ID (escribele "hola" al bot antes)
    python3 newsbot.py report    -> arma y envia el informe de la manana
    python3 newsbot.py watch     -> vigila noticias y alerta al instante (deja corriendo)
    python3 newsbot.py once      -> revisa noticias UNA vez (util para cron)

Honesto: detecta que SALIO la noticia, no predice si el precio sube o baja.
Los RSS gratis pueden tardar 1-5 min vs terminales de pago.
"""
import sys, os, json, time, html
import datetime as dt
import requests
import feedparser
import config as C

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "seen.json")
API = "https://api.telegram.org/bot{token}/{method}"


# --------------------------- Telegram --------------------------------
def tg(method, **params):
    url = API.format(token=C.TELEGRAM_TOKEN, method=method)
    r = requests.get(url, params=params, timeout=20)
    return r.json()

def send(text):
    """Envia un mensaje a tu chat. Usa HTML para negritas."""
    if not C.CHAT_ID:
        print("ERROR: CHAT_ID = 0. Corre primero: python3 newsbot.py chatid")
        return False
    res = tg("sendMessage", chat_id=C.CHAT_ID, text=text,
             parse_mode="HTML", disable_web_page_preview="true")
    if not res.get("ok"):
        print("Telegram respondio error:", res)
        return False
    return True


# --------------------------- Utilidades ------------------------------
def check_token():
    if C.TELEGRAM_TOKEN == "PEGA_TU_TOKEN_AQUI" or not C.TELEGRAM_TOKEN:
        print("ERROR: falta tu token en config.py (linea TELEGRAM_TOKEN).")
        sys.exit(1)

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            return set(json.load(open(SEEN_FILE)))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    # guardamos solo los ultimos 500 para no crecer infinito
    json.dump(list(seen)[-500:], open(SEEN_FILE, "w"))

import re as _re2
_KW_CACHE = {}
def _kw_matches(kw, text):
    """True si la palabra clave aparece como PALABRA COMPLETA (evita que
    'war' active en 'Warsh', o 'ban' en 'urban'). Frases con espacio: subcadena."""
    if " " in kw:
        return kw in text
    pat = _KW_CACHE.get(kw)
    if pat is None:
        pat = _re2.compile(r"\b" + _re2.escape(kw) + r"\b")
        _KW_CACHE[kw] = pat
    return bool(pat.search(text))

def score_headline(title):
    """Devuelve (puntaje, palabras_encontradas)."""
    t = title.lower()
    pts, hits = 0, []
    for kw in C.KW_HIGH:
        if _kw_matches(kw, t):
            pts += 3; hits.append(kw.strip())
    for kw in C.KW_MED:
        if _kw_matches(kw, t):
            pts += 1; hits.append(kw.strip())
    return pts, hits

def esc(s):
    return html.escape(s or "")


# --------------------------- Calendario ------------------------------
CAL_FILE = os.path.join(HERE, "calendar_cache.json")
CAL_TTL = 3 * 3600   # refrescar como mucho cada 3 horas (el feed limita peticiones)
_CAL_CACHE = None

def _read_cal_cache():
    if os.path.exists(CAL_FILE):
        try:
            blob = json.load(open(CAL_FILE))
            return blob.get("ts", 0), blob.get("data", [])
        except Exception:
            pass
    return 0, []

def fetch_calendar():
    """Eventos de esta semana (ForexFactory, gratis). Cachea en disco 3h y
    aguanta el rate-limit 429 devolviendo la ultima copia guardada."""
    global _CAL_CACHE
    if _CAL_CACHE is not None:
        return _CAL_CACHE
    ts, cached = _read_cal_cache()
    if cached and (time.time() - ts) < CAL_TTL:
        _CAL_CACHE = cached
        return _CAL_CACHE
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code == 429:
            raise RuntimeError("rate limit (429)")
        data = r.json()
        json.dump({"ts": time.time(), "data": data}, open(CAL_FILE, "w"))
        _CAL_CACHE = data
    except Exception as e:
        print(f"No pude refrescar el calendario ({e}); uso copia guardada.")
        _CAL_CACHE = cached
    return _CAL_CACHE

def calendar_for_day(target_date):
    """Eventos de impacto Alto/Medio para una fecha dada (paises configurados)."""
    out = []
    for e in fetch_calendar():
        if e.get("country") not in C.CALENDAR_COUNTRIES:
            continue
        if e.get("impact") not in ("High", "Medium"):
            continue
        try:
            when = dt.datetime.fromisoformat(e["date"])
        except Exception:
            continue
        if when.date() == target_date:
            out.append((when, e))
    out.sort(key=lambda x: x[0])
    return out


# --------------------------- Noticias --------------------------------
import re as _re
import urllib.parse as _urlparse

def _google_url(query):
    q = _urlparse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

def _norm_title(title):
    """Titulo normalizado para detectar la MISMA noticia en varios medios.
    Google News agrega ' - Publicacion' al final; se lo quitamos."""
    t = _re.sub(r"\s+-\s+[^-]+$", "", title)      # quita ' - Publisher'
    t = _re.sub(r"[^a-z0-9 ]", "", t.lower())      # solo letras/numeros
    t = _re.sub(r"\s+", " ", t).strip()
    return t[:80]

def collect_news():
    """Devuelve lista de dicts: {src, title, link, eid, base}.
    Junta medios directos + busquedas de Google, y DE-DUPLICA la misma
    noticia contada por varios medios (se queda con la primera)."""
    raw = []
    # medios directos
    for src, url in C.NEWS_FEEDS.items():
        try:
            d = feedparser.parse(url)
        except Exception:
            continue
        for e in d.entries[:30]:
            title = html.unescape(e.get("title", "").strip())
            raw.append({"src": src, "title": title, "link": e.get("link", ""),
                        "eid": e.get("id") or e.get("link") or title, "base": 0})
    # busquedas tematicas de Google (ya vienen filtradas -> base score)
    for tema, query in getattr(C, "GOOGLE_TOPICS", {}).items():
        try:
            d = feedparser.parse(_google_url(query))
        except Exception:
            continue
        for e in d.entries[:20]:
            title = html.unescape(e.get("title", "").strip())
            raw.append({"src": tema, "title": title, "link": e.get("link", ""),
                        "eid": e.get("id") or e.get("link") or title,
                        "base": getattr(C, "GOOGLE_BASE_SCORE", 0)})
    # de-duplicar por titulo normalizado
    seen_titles, items = set(), []
    for it in raw:
        key = _norm_title(it["title"])
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        items.append(it)
    return items


# --------------------------- Comandos --------------------------------
def cmd_test():
    check_token()
    if send("<b>Bot de noticias conectado.</b>\nSi ves esto, todo funciona."):
        print("Enviado. Revisa tu Telegram.")

def cmd_chatid():
    check_token()
    res = tg("getUpdates")
    if not res.get("ok"):
        print("Error hablando con Telegram:", res); return
    updates = res.get("result", [])
    if not updates:
        print("No hay mensajes. Abre tu bot en Telegram, mandale 'hola', y vuelve a correr esto.")
        return
    ids = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            ids[chat["id"]] = chat.get("first_name") or chat.get("title") or "?"
    print("Chat IDs encontrados:")
    for cid, name in ids.items():
        print(f"   CHAT_ID = {cid}   (de: {name})")
    print("\nCopia ese numero en config.py -> CHAT_ID")

def build_report():
    """Arma el texto del informe matutino."""
    today = dt.date.today()
    yest = today - dt.timedelta(days=1)
    lines = [f"<b>Informe economico — {today.strftime('%d/%m/%Y')}</b>\n"]

    # --- lo agendado para HOY ---
    hoy = calendar_for_day(today)
    lines.append("<b>Agenda de hoy:</b>")
    if hoy:
        for when, e in hoy:
            flag = "🔴" if e["impact"] == "High" else "🟠"
            fc = f" (esp: {e['forecast']}, prev: {e['previous']})" if e.get("forecast") else ""
            lines.append(f"{flag} {when.strftime('%H:%M')} {esc(e['country'])} — {esc(e['title'])}{esc(fc)}")
    else:
        lines.append("Sin eventos de alto/medio impacto hoy.")

    # --- lo que paso AYER (eventos con dato ya publicado) ---
    ayer = calendar_for_day(yest)
    lines.append("\n<b>Lo de ayer:</b>")
    if ayer:
        for when, e in ayer:
            act = e.get("actual") or "—"
            lines.append(f"• {esc(e['title'])}: dato {esc(act)} (esp: {esc(e.get('forecast') or '—')})")
    else:
        lines.append("Sin eventos relevantes ayer.")

    # --- titulares mas fuertes de las ultimas horas ---
    lines.append("\n<b>Titulares destacados:</b>")
    ranked = []
    for it in collect_news():
        pts, hits = score_headline(it["title"])
        pts += it.get("base", 0)
        if pts > 0:
            ranked.append((pts, it["src"], it["title"], it["link"]))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if ranked:
        for pts, src, title, link in ranked[:10]:
            lines.append(f'• <a href="{esc(link)}">{esc(title)}</a> <i>({esc(src)})</i>')
    else:
        lines.append("Nada relevante en los feeds ahora mismo.")

    return "\n".join(lines)

def local_now():
    """Hora actual en la zona horaria configurada (ej. Chile)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(C.TIMEZONE))
    except Exception:
        return dt.datetime.now()

def cmd_report():
    check_token()
    # --gate: solo envia si en tu zona horaria es la hora del informe.
    # Se usa en la nube, donde el workflow corre cada hora.
    if "--gate" in sys.argv:
        h = local_now().hour
        if h != C.REPORT_HOUR:
            print(f"Aun no es la hora ({h}h en {C.TIMEZONE}, informe a las {C.REPORT_HOUR}h). No envio.")
            return
    text = build_report()
    if send(text):
        print("Informe enviado a Telegram.")

def _short_line(title, source):
    """Resumen de 2 lineas max. Usa IA si hay saldo; si no, recorta el titular."""
    try:
        import ai_summary
        out = ai_summary.summarize_alert(title, source)
    except Exception:
        out = None
    if out and out.strip().upper() != "IRRELEVANTE":
        # nos quedamos con las primeras 2 lineas no vacias
        lns = [l.strip() for l in out.splitlines() if l.strip()]
        return "\n".join(lns[:2]) if lns else None
    if out and out.strip().upper() == "IRRELEVANTE":
        return None
    # fallback sin IA: el titular recortado
    return (title[:160] + "…") if len(title) > 160 else title

def cmd_once(verbose=True):
    """Revisa feeds una vez y alerta SOLO las mas fuertes (anti-inundacion)."""
    check_token()
    seen = load_seen()

    # 1) juntar lo nuevo que supera el umbral
    candidatos = []
    for it in collect_news():
        if it["eid"] in seen:
            continue
        seen.add(it["eid"])          # marcar visto aunque no se alerte
        pts, hits = score_headline(it["title"])
        pts += it.get("base", 0)
        if pts >= C.ALERT_THRESHOLD:
            candidatos.append((pts, hits, it))

    # 2) quedarse solo con las N mas fuertes, evitando repetir el MISMO tema
    #    (si dos noticias comparten las mismas palabras clave, es el mismo evento)
    candidatos.sort(key=lambda x: x[0], reverse=True)
    tope = getattr(C, "MAX_ALERTS_PER_RUN", 4)
    elegidos, temas_vistos = [], set()
    for pts, hits, it in candidatos:
        firma = tuple(sorted(set(hits))[:3])   # firma del tema
        if firma and firma in temas_vistos:
            continue
        temas_vistos.add(firma)
        elegidos.append((pts, hits, it))
        if len(elegidos) >= tope:
            break

    # 3) enviar en formato corto (2 lineas)
    nuevos = 0
    for pts, hits, it in elegidos:
        urg = "🔴" if pts >= 9 else "🟠"
        resumen = _short_line(it["title"], it["src"])
        if not resumen:              # IA dijo que era irrelevante
            continue
        msg = (f"{urg} <b>{esc(it['src'])}</b>\n"
               f"{esc(resumen)}\n"
               f'<a href="{esc(it["link"])}">ver noticia</a>')
        if send(msg):
            nuevos += 1

    save_seen(seen)
    if verbose:
        print(f"Revision: {len(candidatos)} superaron umbral, envie {nuevos} (tope {tope}).")
    return nuevos

def cmd_watch():
    check_token()
    print(f"Vigilando noticias cada {C.WATCH_EVERY_MIN} min. Ctrl+C para parar.")
    # primera pasada: marcar lo actual como visto sin alertar (evita spam inicial)
    seen = load_seen()
    for it in collect_news():
        seen.add(it["eid"])
    save_seen(seen)
    print("Estado inicial guardado. A partir de ahora solo avisa lo NUEVO.")
    while True:
        try:
            cmd_once(verbose=False)
        except Exception as e:
            print("Error en ciclo (sigo):", e)
        time.sleep(C.WATCH_EVERY_MIN * 60)


CMDS = {"test": cmd_test, "chatid": cmd_chatid, "report": cmd_report,
        "once": cmd_once, "watch": cmd_watch}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in CMDS:
        print(__doc__)
        sys.exit(0)
    CMDS[cmd]()
