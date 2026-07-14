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

def send_photo(path, caption=""):
    """Envia una imagen (PNG) a tu chat con un pie de foto opcional."""
    if not C.CHAT_ID:
        print("ERROR: CHAT_ID = 0.")
        return False
    url = API.format(token=C.TELEGRAM_TOKEN, method="sendPhoto")
    try:
        with open(path, "rb") as f:
            r = requests.post(url,
                              data={"chat_id": C.CHAT_ID, "caption": caption[:1024],
                                    "parse_mode": "HTML"},
                              files={"photo": f}, timeout=60)
        res = r.json()
    except Exception as e:
        print("Error enviando foto:", e)
        return False
    if not res.get("ok"):
        print("Telegram respondio error (foto):", res)
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
    # PRIORIDAD: los eventos del pais prioritario (la Fed/USD) van primero;
    # dentro de cada grupo, por hora.
    prio = getattr(C, "CALENDAR_PRIORITY", "USD")
    out.sort(key=lambda x: (x[1].get("country") != prio, x[0]))
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
    # --- Asia (van PRIMERO: reglas mas especificas que las genericas de abajo) ---
    # Japon: yen fuerte / BOJ sube tasas -> se deshace el "carry trade"
    # (dinero barato en yenes que financia activos de riesgo) -> malo p/ cripto.
    ("boj policy rate", -1), ("boj", -1), ("policy rate", -1),
    # China: crecimiento fuerte -> apetito de riesgo global -> bueno p/ cripto.
    ("industrial production", +1), ("trade balance", +1),
    ("manufacturing pmi", +1), ("services pmi", +1), ("caixin", +1),
    # --- EE.UU. / Fed ---
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
    return ("🟢 posible presión AL ALZA" if net > 0
            else "🔴 posible presión A LA BAJA")

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

        pais = esc(e.get("country", ""))
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
            msg = (f"📅 <b>DATO ECONÓMICO</b>  ·  <i>{pais}</i>\n"
                   f"{DIV}\n"
                   f"{flag} <b>{esc(titulo)}</b>\n"
                   f"   Real: <b>{esc(actual)}</b>\n"
                   f"   Esperado: {esc(fc or '—')}   ·   Previo: {esc(prev or '—')}\n"
                   f"{comp}")
        else:
            # CAMINO B: feed sin numero -> leer la DIRECCION de los titulares
            surprise, evidencia = result_from_headlines(titulo)
            if surprise is None:
                continue   # ni feed ni titulares -> reintentar proximo ciclo
            comp = "⬆️ MAYOR a lo esperado" if surprise > 0 else "⬇️ MENOR a lo esperado"
            lectura = crypto_read_from_surprise(titulo, surprise)
            msg = (f"📅 <b>DATO ECONÓMICO</b>  ·  <i>{pais}</i>\n"
                   f"{DIV}\n"
                   f"{flag} <b>{esc(titulo)}</b>\n"
                   f"{comp}\n"
                   f"   Esperado: {esc(fc or '—')}   ·   Previo: {esc(prev or '—')}\n"
                   f"<i>según titulares: {esc((evidencia or '')[:90])}</i>")

        if lectura:
            msg += f"\n{DIV}\n📈 <b>Lectura cripto:</b> {lectura}\n<i>(orientativo, no garantía)</i>"
        pl = _price_line()
        if pl:
            msg += f"\n{pl}"
        if send_fn(msg):
            reported.add(_event_key(e))
            enviados += 1

    _save_reported(reported)
    return enviados


# --------------------------- Movimiento de precio --------------------
PRICE_STATE = os.path.join(HERE, "price_state.json")

def check_price_move(send_fn, hubo_noticia=False):
    """Avisa si BTC se movio mas de PRICE_ALERT_PCT desde la ultima referencia.
    Lee el FLUJO: distingue si el movimiento va con o sin noticia (sin noticia =
    flujo puro que suele adelantarse), su intensidad y si es sostenido.
    Guarda la referencia en disco. Devuelve 1 si aviso, 0 si no."""
    try:
        import price
        d = price.get_btc(force=True)
    except Exception:
        return 0
    if not d or not d.get("price"):
        return 0
    now, cur = time.time(), d["price"]

    base = None
    if os.path.exists(PRICE_STATE):
        try:
            base = json.load(open(PRICE_STATE))
        except Exception:
            base = None

    max_age = getattr(C, "PRICE_BASELINE_MAX_MIN", 90) * 60
    # sin referencia o muy vieja -> fijar una nueva y no avisar todavia
    if not base or (now - base.get("ts", 0)) > max_age:
        json.dump({"price": cur, "ts": now}, open(PRICE_STATE, "w"))
        return 0

    ref = base["price"]
    pct = (cur - ref) / ref * 100 if ref else 0
    umbral = getattr(C, "PRICE_ALERT_PCT", 2.5)
    if abs(pct) < umbral:
        return 0

    subiendo = pct > 0
    direccion = "AL ALZA" if subiendo else "A LA BAJA"
    flecha = "🟢📈" if subiendo else "🔴📉"
    verbo = "SUBIÓ" if subiendo else "CAYÓ"
    fuerza = "🔥 MUY FUERTE" if abs(pct) >= umbral * 2 else "fuerte"

    # flujo sostenido: mismo sentido que el aviso anterior y hace poco (<3h)
    prev_dir = base.get("last_dir")
    prev_ts = base.get("last_alert_ts", 0)
    sostenido = (prev_dir == (1 if subiendo else -1)) and (now - prev_ts) < 3 * 3600

    lineas = [f"{flecha} <b>MOVIMIENTO BTC — {fuerza}</b>", DIV,
              f"Bitcoin {verbo} <b>{pct:+.1f}%</b> en poco tiempo.",
              f"   ${ref:,.0f} → <b>${cur:,.0f}</b>"]
    # LECTURA DEL FLUJO: lo mas valioso segun con/sin noticia
    if hubo_noticia:
        lineas.append(f"📊 El precio <b>confirma</b> el flujo {direccion} (hay noticia detrás).")
    else:
        lineas.append(f"📊 Movimiento <b>SIN noticia clara</b>: el flujo apunta "
                      f"{direccion}. El dinero suele moverse antes que la noticia — atento.")
    if sostenido:
        lineas.append("⏫ <b>Flujo sostenido</b> (segundo tramo en la misma dirección).")

    ok = send_fn("\n".join(lineas))
    json.dump({"price": cur, "ts": now,
               "last_dir": (1 if subiendo else -1), "last_alert_ts": now},
              open(PRICE_STATE, "w"))
    return 1 if ok else 0


# --------------------------- Comandos entrantes + silencio -----------
OFFSET_FILE = os.path.join(HERE, "bot_offset.json")
MUTE_FILE = os.path.join(HERE, "mute.json")

def _load_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            return json.load(open(OFFSET_FILE)).get("offset", 0)
        except Exception:
            pass
    return 0

def _save_offset(off):
    try:
        json.dump({"offset": off}, open(OFFSET_FILE, "w"))
    except Exception:
        pass

def is_muted():
    """True si estas en modo silencio (por /silencio). Expira solo."""
    if os.path.exists(MUTE_FILE):
        try:
            until = json.load(open(MUTE_FILE)).get("until", 0)
            return time.time() < until
        except Exception:
            return False
    return False

def _set_mute(hours):
    until = time.time() + hours * 3600
    json.dump({"until": until}, open(MUTE_FILE, "w"))
    return until

def _clear_mute():
    try:
        if os.path.exists(MUTE_FILE):
            os.remove(MUTE_FILE)
    except Exception:
        pass

AYUDA = ("🤖 <b>Radar Financiero — comandos</b>\n"
         "/precio — precio de BTC ahora\n"
         "/tecnico — gráfico 4h con soportes/resistencias/fibonacci\n"
         "/semana — agenda de la semana\n"
         "/estado — actividad de hoy y salud\n"
         "/silencio Nh — silenciar N horas (ej: /silencio 2h)\n"
         "/activar — quitar el silencio\n"
         "/ayuda — esta lista")

def _handle_command(text):
    """Devuelve el texto de respuesta a un comando, o None si no aplica."""
    t = (text or "").strip().lower()
    if not t.startswith("/"):
        return None
    cmd = t.split()[0].lstrip("/")
    if cmd in ("precio", "btc"):
        return _price_line() or "No pude obtener el precio ahora."
    if cmd in ("semana", "agenda"):
        wk = week_ahead_lines()
        cuerpo = "\n".join(wk) if wk else "<i>Sin eventos de alto impacto en el feed.</i>"
        return f"📆 <b>Agenda de la semana</b>\n{DIV}\n{cuerpo}"
    if cmd in ("estado", "status"):
        st = read_stats()
        salud = "✅ operativo" if not is_muted() else "🔕 en silencio"
        return (f"📊 <b>Estado</b>\n{DIV}\n"
                f"Hoy: {st['noticias']} noticias · {st['datos']} datos · "
                f"{st['precio']} avisos de precio\n"
                f"{_price_line()}\n{salud}")
    if cmd in ("silencio", "mute"):
        m = _re2.search(r"(\d+)\s*h?", t)
        horas = int(m.group(1)) if m else 2
        _set_mute(horas)
        return f"🔕 Silenciado por <b>{horas}h</b>. Usa /activar para volver antes."
    if cmd in ("activar", "unmute"):
        _clear_mute()
        return "🔔 Silencio quitado. Vuelvo a avisarte."
    if cmd in ("tecnico", "grafico", "chart", "ta"):
        return "__FOTO_TECNICO__"   # se maneja aparte (envia imagen, no texto)
    if cmd in ("ayuda", "help", "start"):
        return AYUDA
    return f"No conozco ese comando.\n\n{AYUDA}"

def process_commands():
    """Lee mensajes entrantes de Telegram y responde a los comandos.
    Corre en cada revision (cada ~5 min); no es tiempo real."""
    off = _load_offset()
    res = tg("getUpdates", offset=off + 1, timeout=0)
    if not res.get("ok"):
        return 0
    respondidos = 0
    max_id = off
    for u in res.get("result", []):
        max_id = max(max_id, u.get("update_id", off))
        msg = u.get("message") or u.get("channel_post") or {}
        texto = msg.get("text", "")
        resp = _handle_command(texto)
        if resp == "__FOTO_TECNICO__":
            send_tecnico()
            respondidos += 1
        elif resp:
            send(resp)
            respondidos += 1
    if max_id != off:
        _save_offset(max_id)
    return respondidos


# --------------------------- Salud + estadisticas --------------------
HEARTBEAT_FILE = os.path.join(HERE, "heartbeat.json")
STATS_FILE = os.path.join(HERE, "stats.json")

def touch_heartbeat():
    """Marca que el vigilante corrio bien recien (para detectar caidas)."""
    try:
        json.dump({"ts": time.time()}, open(HEARTBEAT_FILE, "w"))
    except Exception:
        pass

def cmd_health():
    """Avisa si el vigilante lleva mucho sin correr. Lo llama el workflow horario."""
    check_token()
    ts = 0
    if os.path.exists(HEARTBEAT_FILE):
        try:
            ts = json.load(open(HEARTBEAT_FILE)).get("ts", 0)
        except Exception:
            ts = 0
    if ts == 0:
        print("Sin heartbeat aun; no aviso (primera vez).")
        return
    mins = (time.time() - ts) / 60
    limite = getattr(C, "HEALTH_MAX_MIN", 30)
    if mins > limite:
        msg = (f"🛠️ <b>AVISO: el vigilante no corre</b>\n{DIV}\n"
               f"Hace <b>{mins:.0f} min</b> que no hay una revisión exitosa "
               f"(lo normal es cada ~5 min).\n"
               f"<i>Puede ser un feed caído o un fallo en GitHub Actions. "
               f"Revisa la pestaña Actions del repo.</i>")
        if send(msg):
            print(f"Aviso de caida enviado ({mins:.0f} min sin correr).")
    else:
        print(f"Vigilante sano ({mins:.0f} min desde la ultima corrida).")

def record_stats(noticias, datos, precio):
    """Suma la actividad del dia (para el resumen del informe)."""
    hoy = dt.date.today().isoformat()
    s = {"day": hoy, "noticias": 0, "datos": 0, "precio": 0}
    if os.path.exists(STATS_FILE):
        try:
            prev = json.load(open(STATS_FILE))
            if prev.get("day") == hoy:
                s = prev
        except Exception:
            pass
    s["noticias"] += noticias
    s["datos"] += datos
    s["precio"] += precio
    try:
        json.dump(s, open(STATS_FILE, "w"))
    except Exception:
        pass

def read_stats():
    """Devuelve las estadisticas de HOY (o ceros)."""
    hoy = dt.date.today().isoformat()
    if os.path.exists(STATS_FILE):
        try:
            s = json.load(open(STATS_FILE))
            if s.get("day") == hoy:
                return s
        except Exception:
            pass
    return {"day": hoy, "noticias": 0, "datos": 0, "precio": 0}


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
    # de-duplicar por titulo normalizado, contando cuantas fuentes lo traen
    by_key, items = {}, []
    for it in raw:
        key = _norm_title(it["title"])
        if not key:
            continue
        if key in by_key:
            by_key[key]["fuentes"] += 1   # misma noticia en otro medio
            continue
        it["fuentes"] = 1
        by_key[key] = it
        items.append(it)
    return items


# --------------------------- Visuales --------------------------------
DIV = "➖➖➖➖➖➖➖➖➖➖"

def _price_line():
    """Linea de contexto con el precio de BTC. '' si no se puede obtener."""
    try:
        import price
        return esc(price.btc_line())
    except Exception:
        return ""

def topic_icon(text):
    """Emoji segun el tema detectado en el texto (fuente o titulo)."""
    t = (text or "").lower()
    reglas = [
        (("fed", "tasa", "rate", "fomc", "powell", "warsh", "inflation", "cpi", "pce"), "🏦"),
        (("bitcoin", "btc", "ethereum", "eth", "cripto", "crypto", "etf"), "₿"),
        (("trump", "politic", "election", "white house", "congress"), "🏛️"),
        (("china", "japan", "korea", "asia", "yen", "yuan", "nikkei",
          "hang seng", "shanghai", "kospi", "boj"), "🌏"),
        (("war", "guerra", "iran", "russia", "sanction", "oil", "geopolit"), "🌍"),
        (("hack", "exploit", "breach", "lawsuit", "sec", "fraud"), "⚠️"),
        (("stock", "nasdaq", "s&p", "dow", "gdp", "jobs", "payroll", "retail"), "📊"),
    ]
    for claves, icono in reglas:
        if any(k in t for k in claves):
            return icono
    return "📰"


# --------------------------- Comandos --------------------------------
def cmd_test():
    check_token()
    if send("✅ <b>Radar Financiero conectado</b>\n<i>Si ves esto, todo funciona.</i>"):
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
    lines = [f"🗞️ <b>INFORME ECONÓMICO</b>",
             f"<i>{today.strftime('%d/%m/%Y')}</i>",
             DIV]

    # --- pulso del mercado + actividad del bot ---
    pl = _price_line()
    if pl:
        lines.append(pl)
    st = read_stats()
    total = st["noticias"] + st["datos"] + st["precio"]
    if total:
        lines.append(f"<i>📨 hoy: {st['noticias']} noticias · "
                     f"{st['datos']} datos · {st['precio']} avisos de precio</i>")
    lines.append(DIV)

    # --- lo agendado para HOY ---
    hoy = calendar_for_day(today)
    lines.append("📅 <b>Agenda de hoy</b>")
    if hoy:
        for when, e in hoy:
            flag = "🔴" if e["impact"] == "High" else "🟠"
            fc = f"  <i>(esp: {esc(str(e['forecast']))} · prev: {esc(str(e['previous']))})</i>" if e.get("forecast") else ""
            lines.append(f"{flag} <b>{when.strftime('%H:%M')}</b> {esc(e['country'])} — {esc(e['title'])}{fc}")
    else:
        lines.append("<i>Sin eventos de alto/medio impacto hoy.</i>")

    # --- lo que paso AYER (eventos con dato ya publicado) ---
    ayer = calendar_for_day(yest)
    lines.append(f"\n{DIV}\n📊 <b>Lo de ayer</b>")
    if ayer:
        for when, e in ayer:
            act = e.get("actual") or "—"
            lines.append(f"• {esc(e['title'])}: <b>{esc(act)}</b> <i>(esp: {esc(e.get('forecast') or '—')})</i>")
    else:
        lines.append("<i>Sin eventos relevantes ayer.</i>")

    # --- titulares mas fuertes de las ultimas horas ---
    lines.append(f"\n{DIV}\n📰 <b>Titulares destacados</b>")
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
            ic = topic_icon(src + " " + title)
            lines.append(f'{ic} <a href="{esc(link)}">{esc(title_es)}</a> <i>({esc(src)})</i>')
    else:
        lines.append("<i>Nada relevante en los feeds ahora mismo.</i>")

    # --- los domingos: adelanto de la semana ---
    if today.weekday() == 6:   # 6 = domingo
        wk = week_ahead_lines()
        if wk:
            lines.append(f"\n{DIV}\n📆 <b>Lo que viene esta semana</b>")
            lines.extend(wk)

    return "\n".join(lines)

def week_ahead_lines():
    """Lineas con los eventos de ALTO impacto de los proximos 7 dias."""
    today = dt.date.today()
    cal = fetch_calendar()
    out = []
    for i in range(7):
        day = today + dt.timedelta(days=i)
        eventos = [(w, e) for w, e in calendar_for_day(day, source=cal)
                   if e.get("impact") == "High"]
        for when, e in eventos:
            nombre = day.strftime('%a %d/%m')
            out.append(f"🔴 <b>{nombre}</b> {when.strftime('%H:%M')} "
                       f"{esc(e['country'])} — {esc(e['title'])}")
    return out

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

def cmd_week():
    check_token()
    wk = week_ahead_lines()
    if not wk:
        wk = ["<i>Sin eventos de alto impacto detectados en el feed.</i>"]
    texto = (f"📆 <b>AGENDA DE LA SEMANA</b>\n"
             f"<i>eventos de alto impacto</i>\n{DIV}\n" + "\n".join(wk))
    if send(texto):
        print("Agenda semanal enviada a Telegram.")

def cmd_tecnico():
    """Genera y envia el grafico 4h con soportes/resistencias/fibonacci."""
    check_token()
    if send_tecnico():
        print("Analisis tecnico enviado a Telegram.")
    else:
        print("No pude generar/enviar el analisis tecnico.")

def send_tecnico():
    """Arma el grafico + resumen y lo manda. Devuelve True si envio."""
    try:
        import technical
        a = technical.analyze()
        caption = technical.text_summary(a)
        path = technical.make_chart(a, os.path.join(HERE, "btc_4h.png"))
    except Exception as e:
        print("Error en analisis tecnico:", e)
        return False
    return send_photo(path, caption)

CHART_STATE = os.path.join(HERE, "chart_state.json")

def check_chart_schedule(send_now=False):
    """Envia el grafico 4h cuando el reloj de Chile entra en un horario de
    CHART_TIMES (una vez por horario/dia). Robusto al horario de verano.
    Devuelve 1 si envio, 0 si no. Lo llama el vigilante cada ~5 min."""
    ahora = local_now()
    hoy = ahora.date().isoformat()
    horarios = getattr(C, "CHART_TIMES", [])

    # cual horario "toca" ahora (dentro de una ventana de WATCH_EVERY_MIN)
    ventana = getattr(C, "WATCH_EVERY_MIN", 5)
    slot = None
    for hm in horarios:
        try:
            hh, mm = map(int, hm.split(":"))
        except Exception:
            continue
        objetivo = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        diff = (ahora - objetivo).total_seconds() / 60
        if 0 <= diff < ventana:      # el reloj acaba de pasar ese horario
            slot = hm
            break
    if slot is None and not send_now:
        return 0

    # no repetir el mismo slot el mismo dia
    if slot is not None:
        marca = f"{hoy} {slot}"
        prev = ""
        if os.path.exists(CHART_STATE):
            try:
                prev = json.load(open(CHART_STATE)).get("last", "")
            except Exception:
                prev = ""
        if prev == marca:
            return 0

    ok = send_tecnico()
    if ok and slot is not None:
        json.dump({"last": f"{hoy} {slot}"}, open(CHART_STATE, "w"))
    return 1 if ok else 0

def cmd_chart_gate():
    """Para el vigilante/cron: envia el grafico solo si toca por horario."""
    check_token()
    n = check_chart_schedule()
    print(f"Grafico tecnico: {'enviado' if n else 'no tocaba ahora'}.")

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

    # 0) responder comandos que te hayan escrito al bot (/precio, /silencio, etc.)
    try:
        cmds = process_commands()
    except Exception as e:
        print("Error procesando comandos:", e)
        cmds = 0

    # si estas en modo silencio, no mandes alertas automaticas (pero igual
    # respondiste comandos y dejas el heartbeat al final).
    if is_muted():
        touch_heartbeat()
        if verbose:
            print(f"En silencio: respondi {cmds} comandos, sin alertas.")
        return 0

    seen = load_seen()

    # 1) juntar lo nuevo que supera el umbral (protegido: si un feed rompe,
    #    seguimos con lo demas en vez de tumbar todo el ciclo)
    candidatos = []
    try:
        for it in collect_news():
            if it["eid"] in seen:
                continue
            seen.add(it["eid"])          # marcar visto aunque no se alerte
            pts, hits = score_headline(it["title"])
            pts += it.get("base", 0)
            if pts >= C.ALERT_THRESHOLD:
                candidatos.append((pts, hits, it))
    except Exception as e:
        print("Error recolectando noticias:", e)

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

    # 3) enviar en formato ~4 lineas (cada una protegida: si una falla, sigue
    #    con las demas)
    nuevos = 0
    for pts, hits, it in elegidos:
        try:
            urg = "🔴 URGENTE" if pts >= 9 else "🟠 Relevante"
            icono = topic_icon(it["src"] + " " + it["title"])
            cuerpo = _build_body(it)     # ya viene con HTML escapado
            if not cuerpo:               # IA dijo que era irrelevante
                continue
            msg = (f"{icono} <b>{urg}</b>  ·  <i>{esc(it['src'])}</i>\n"
                   f"{DIV}\n"
                   f"{cuerpo}\n")
            if it.get("fuentes", 1) > 1:
                msg += f"<i>📡 cubierto por {it['fuentes']} fuentes</i>\n"
            pl = _price_line()
            if pl:
                msg += f"{pl}\n"
            msg += f'🔗 <a href="{esc(it["link"])}">Leer noticia completa</a>'
            if send(msg):
                nuevos += 1
        except Exception as e:
            print("Error enviando una alerta (sigo con las demas):", e)

    save_seen(seen)

    # 4) resultados de eventos economicos recien publicados (CPI, NFP, etc.)
    try:
        res = check_calendar_results(send)
    except Exception as e:
        print("Error revisando resultados de calendario:", e)
        res = 0

    # 5) movimiento brusco del precio de BTC (si hubo noticia este ciclo, el
    #    movimiento queda "explicado"; si no, es flujo puro = mas relevante)
    try:
        mov = check_price_move(send, hubo_noticia=(nuevos + res) > 0)
    except Exception as e:
        print("Error revisando movimiento de precio:", e)
        mov = 0

    # 6) grafico tecnico 4h si el reloj entra en un horario programado
    try:
        graf = check_chart_schedule()
    except Exception as e:
        print("Error enviando grafico tecnico:", e)
        graf = 0

    # 7) registrar salud + estadisticas del dia
    touch_heartbeat()
    record_stats(nuevos, res, mov)

    if verbose:
        print(f"Revision: {len(candidatos)} superaron umbral, envie {nuevos} noticias, "
              f"{res} resultados de calendario, {mov} avisos de precio y "
              f"{graf} graficos.")
    return nuevos + res + mov + graf

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
        "once": cmd_once, "watch": cmd_watch, "week": cmd_week,
        "health": cmd_health, "tecnico": cmd_tecnico, "chart": cmd_chart_gate}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in CMDS:
        print(__doc__)
        sys.exit(0)
    CMDS[cmd]()
