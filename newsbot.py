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

def fetch_calendar(force=False):
    """Eventos de esta semana (ForexFactory, gratis). Cachea en disco 3h y
    aguanta el rate-limit 429 devolviendo la ultima copia guardada.
    force=True ignora la cache (para ver resultados recien publicados)."""
    global _CAL_CACHE
    if _CAL_CACHE is not None and not force:
        return _CAL_CACHE
    ts, cached = _read_cal_cache()
    if cached and not force and (time.time() - ts) < CAL_TTL:
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
        _CAL_CACHE = cached if cached else (_CAL_CACHE or [])
    return _CAL_CACHE

def calendar_for_day(target_date, source=None):
    """Eventos de impacto Alto/Medio para una fecha dada (paises configurados)."""
    out = []
    for e in (source if source is not None else fetch_calendar()):
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


# --------------------------- Resultados en vivo ----------------------
RESULTS_FILE = os.path.join(HERE, "reported_events.json")

def _load_reported():
    if os.path.exists(RESULTS_FILE):
        try:
            return set(json.load(open(RESULTS_FILE)))
        except Exception:
            return set()
    return set()

def _save_reported(s):
    json.dump(list(s)[-300:], open(RESULTS_FILE, "w"))

def _event_key(e):
    return f"{e.get('date','')}|{e.get('title','')}"

# Como suele reaccionar el cripto a cada tipo de dato. Clave = palabra en el
# titulo del evento. Valor = signo cuando el dato sale MAYOR a lo esperado:
#   -1  mayor de lo esperado suele ser MALO para cripto (presion a la baja)
#   +1  mayor de lo esperado suele ser BUENO para cripto (presion al alza)
# (Se invierte si el dato sale MENOR.) Es una guia general, NO garantia.
CRYPTO_BIAS = [
    ("core cpi", -1), ("cpi", -1), ("core pce", -1), ("pce", -1),
    ("inflation", -1), ("ppi", -1),                    # inflacion alta = malo
    ("unemployment rate", +1),                          # mas paro = Fed baja tasas = bueno
    ("initial jobless", +1), ("jobless claims", +1),    # mas desempleo = bueno p/ riesgo
    ("non-farm", -1), ("nonfarm", -1), ("payroll", -1), # empleo fuerte = tasas altas = malo
    ("interest rate", -1), ("federal funds", -1),       # tasa mas alta = malo
    ("retail sales", +1), ("gdp", +1),                  # economia fuerte = apetito riesgo
    ("consumer confidence", +1), ("ism services", +1),
    ("ism manufacturing", +1), ("pmi", +1),
]

def crypto_read_from_surprise(title, surprise):
    """surprise: +1 dato salio MAYOR a lo esperado, -1 MENOR, 0 en linea.
    Devuelve texto de como PODRIA leerlo el cripto (al alza/baja) o ''."""
    if not surprise:
        return ""
    t = title.lower()
    bias = None
    for kw, sign in CRYPTO_BIAS:
        if kw in t:
            bias = sign
            break
    if bias is None:
        return ""   # evento sin regla -> no arriesgamos interpretacion
    net = bias * surprise
    return ("🟢 Lectura cripto: posible presion AL ALZA" if net > 0
            else "🔴 Lectura cripto: posible presion A LA BAJA")

def crypto_read(title, actual_num, forecast_num):
    """Version con numeros: calcula la sorpresa y delega."""
    if actual_num is None or forecast_num is None:
        return ""
    surprise = 0 if actual_num == forecast_num else (1 if actual_num > forecast_num else -1)
    return crypto_read_from_surprise(title, surprise)

# Palabras en titulares que indican la DIRECCION de la sorpresa (respaldo
# cuando el feed no trae el numero 'actual'). +1 = salio mas alto de lo esperado.
_SURPRISE_UP = ["more than expected", "higher than expected", "hotter", "above expectations",
                "above forecast", "beats", "stronger than expected", "hot ", "reaccelerat",
                "jumped", "surged", "accelerat", "rose more"]
_SURPRISE_DOWN = ["less than expected", "lower than expected", "cooler", "cooled",
                  "below expectations", "below forecast", "softer", "weaker than expected",
                  "slowed more", "eased more", "missed", "fell more", "moderat"]

def result_from_headlines(event_title):
    """Busca en titulares recientes la direccion del dato (y el titular de prueba).
    Devuelve (surprise, evidencia) o (None, None) si no hay señal clara."""
    # consulta enfocada al evento (ej. 'CPI inflation report')
    base = event_title.lower()
    if "cpi" in base or "inflation" in base:
        q = "CPI inflation report when:1d"
    elif "payroll" in base or "non-farm" in base or "nonfarm" in base:
        q = "nonfarm payrolls jobs report when:1d"
    elif "unemployment" in base or "jobless" in base:
        q = "unemployment jobless claims when:1d"
    elif "pce" in base:
        q = "PCE inflation report when:1d"
    elif "gdp" in base:
        q = "GDP growth report when:1d"
    elif "retail" in base:
        q = "retail sales report when:1d"
    else:
        q = event_title + " when:1d"
    try:
        d = feedparser.parse(_google_url(q))
    except Exception:
        return None, None
    up = dn = 0
    evidencia = None
    for e in d.entries[:12]:
        tl = html.unescape(e.get("title", "")).lower()
        if any(w in tl for w in _SURPRISE_DOWN):
            dn += 1
            evidencia = evidencia or e.get("title", "")
        if any(w in tl for w in _SURPRISE_UP):
            up += 1
            evidencia = evidencia or e.get("title", "")
    if up == 0 and dn == 0:
        return None, None
    if up > dn:
        return 1, evidencia
    if dn > up:
        return -1, evidencia
    return None, None   # empate -> no arriesgamos

def _num(s):
    """Extrae numero de textos como '0.2%', '215K', '-1.3%', '3.5M'. None si no hay."""
    if not s:
        return None
    m = _re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    v = float(m.group())
    if "K" in s.upper():
        v *= 1_000
    elif "M" in s.upper():
        v *= 1_000_000
    return v

def check_calendar_results(send_fn):
    """Avisa el RESULTADO de eventos economicos apenas se publican (una vez).
    Devuelve cuantos avisos mando."""
    today = dt.date.today()
    reported = _load_reported()

    # eventos de hoy (con cache) cuya hora ya paso y aun no reportamos
    pendientes = []
    for when, e in calendar_for_day(today):
        if _event_key(e) in reported:
            continue
        # hora del evento ya paso? (comparo en la misma zona del evento)
        try:
            if dt.datetime.now(when.tzinfo) < when:
                continue   # aun no es la hora
        except Exception:
            continue
        pendientes.append((when, e))

    if not pendientes:
        return 0

    # hay eventos cuya hora paso -> refrescar calendario para ver si ya hay 'actual'
    fresh = fetch_calendar(force=True)
    enviados = 0
    for when, _old in pendientes:
        # buscar el evento fresco equivalente
        e = None
        for f in fresh:
            if f.get("title") == _old.get("title") and f.get("date") == _old.get("date"):
                e = f
                break
        e = e or _old
        titulo = e.get("title", "")
        fc = e.get("forecast") or ""
        prev = e.get("previous") or ""
        actual = e.get("actual")
        flag = "🔴" if e.get("impact") == "High" else "🟠"

        if actual:
            # CAMINO A: el feed trae el numero -> comparacion exacta
            a, f = _num(actual), _num(fc)
            if a is not None and f is not None:
                comp = ("⬆️ MAYOR a lo esperado" if a > f else
                        "⬇️ MENOR a lo esperado" if a < f else
                        "➡️ EN LINEA con lo esperado")
                surprise = 0 if a == f else (1 if a > f else -1)
            else:
                comp, surprise = "dato publicado", 0
            lectura = crypto_read_from_surprise(titulo, surprise)
            msg = (f"{flag} <b>RESULTADO: {esc(titulo)}</b> ({esc(e.get('country',''))})\n"
                   f"Real: <b>{esc(actual)}</b>  |  esperado: {esc(fc or '—')}  |  previo: {esc(prev or '—')}\n"
                   f"{comp}")
        else:
            # CAMINO B: feed sin numero -> leer la DIRECCION de los titulares
            surprise, evidencia = result_from_headlines(titulo)
            if surprise is None:
                continue   # ni feed ni titulares -> reintentar proximo ciclo
            comp = "⬆️ MAYOR a lo esperado" if surprise > 0 else "⬇️ MENOR a lo esperado"
            lectura = crypto_read_from_surprise(titulo, surprise)
            msg = (f"{flag} <b>RESULTADO: {esc(titulo)}</b> ({esc(e.get('country',''))})\n"
                   f"{comp} (esperado: {esc(fc or '—')}, previo: {esc(prev or '—')})\n"
                   f"<i>segun titulares: {esc((evidencia or '')[:90])}</i>")

        if lectura:
            msg += f"\n{lectura}\n<i>(orientativo, no garantia)</i>"
        if send_fn(msg):
            reported.add(_event_key(e))
            enviados += 1

    _save_reported(reported)
    return enviados


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

def _extract_summary(entry, title):
    """Extracto/descripcion de la noticia desde el feed (texto limpio, sin HTML)."""
    raw = entry.get("summary", "") or entry.get("description", "")
    txt = html.unescape(_re.sub(r"<[^>]+>", "", raw)).strip()
    # Google News mete el titulo + fuente como 'resumen' -> no aporta, lo descartamos
    if not txt or _norm_title(txt)[:40] == _norm_title(title)[:40]:
        return ""
    return _re.sub(r"\s+", " ", txt)

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
                        "eid": e.get("id") or e.get("link") or title, "base": 0,
                        "summary": _extract_summary(e, title)})
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
                        "base": getattr(C, "GOOGLE_BASE_SCORE", 0),
                        "summary": _extract_summary(e, title)})
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
        import translate
        for pts, src, title, link in ranked[:10]:
            title_es = translate.to_es(title)
            lines.append(f'• <a href="{esc(link)}">{esc(title_es)}</a> <i>({esc(src)})</i>')
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

def _build_body(item):
    """Cuerpo de la alerta (~4 lineas). Con IA: que paso/por que importa/reaccion.
    Sin IA: titulo + extracto de la noticia, ambos traducidos al español."""
    title, source = item["title"], item["src"]
    # 1) intento con IA (si hay saldo)
    try:
        import ai_summary
        out = ai_summary.summarize_alert(title, source)
    except Exception:
        out = None
    if out and out.strip().upper() != "IRRELEVANTE":
        lns = [esc(l.strip()) for l in out.splitlines() if l.strip()]
        return "\n".join(lns[:4]) if lns else None
    if out and out.strip().upper() == "IRRELEVANTE":
        return None
    # 2) fallback sin IA: titulo + extracto, traducidos
    import translate
    partes = [f"<b>{esc(translate.to_es(title))}</b>"]
    extracto = item.get("summary", "")
    if extracto:
        extracto = translate.to_es(extracto[:280])
        if len(extracto) > 240:
            extracto = extracto[:240].rsplit(" ", 1)[0] + "…"
        partes.append(esc(extracto))
    return "\n".join(partes)

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

    # 3) enviar en formato ~4 lineas
    nuevos = 0
    for pts, hits, it in elegidos:
        urg = "🔴" if pts >= 9 else "🟠"
        cuerpo = _build_body(it)     # ya viene con HTML escapado
        if not cuerpo:               # IA dijo que era irrelevante
            continue
        msg = (f"{urg} <i>{esc(it['src'])}</i>\n"
               f"{cuerpo}\n"
               f'<a href="{esc(it["link"])}">ver noticia</a>')
        if send(msg):
            nuevos += 1

    save_seen(seen)

    # 4) resultados de eventos economicos recien publicados (CPI, NFP, etc.)
    try:
        res = check_calendar_results(send)
    except Exception as e:
        print("Error revisando resultados de calendario:", e)
        res = 0

    if verbose:
        print(f"Revision: {len(candidatos)} superaron umbral, envie {nuevos} noticias "
              f"y {res} resultados de calendario.")
    return nuevos + res

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
