# =====================================================================
#  CONFIGURACION  ->  esto es LO UNICO que tienes que editar tu.
# =====================================================================

import os

# Token y chat_id. Orden de busqueda:
#   1) Secrets de GitHub (variables de entorno) -> cuando corre en la nube.
#   2) Archivo secrets_local.py               -> cuando corre en tu Mac.
# Asi tu token real NUNCA queda escrito en un archivo que se sube a internet.
try:
    import secrets_local as _sl
    _LOCAL_TOKEN = getattr(_sl, "TELEGRAM_TOKEN", "")
    _LOCAL_CHAT = getattr(_sl, "CHAT_ID", 0)
    _LOCAL_AI = getattr(_sl, "ANTHROPIC_API_KEY", "")
except ImportError:
    _LOCAL_TOKEN, _LOCAL_CHAT, _LOCAL_AI = "", 0, ""

def _pick(env_name, local):
    """Usa la variable de entorno solo si NO esta vacia; si no, el valor local."""
    v = os.environ.get(env_name)
    return v if v else local

TELEGRAM_TOKEN = _pick("TELEGRAM_TOKEN", _LOCAL_TOKEN)
CHAT_ID = int(_pick("CHAT_ID", _LOCAL_CHAT or 0))
ANTHROPIC_API_KEY = _pick("ANTHROPIC_API_KEY", _LOCAL_AI)

# Modelo de IA para resumir (Haiku = el mas barato y rapido).
AI_MODEL = "claude-haiku-4-5-20251001"

# 3) Hora del informe diario (formato 24h) y tu zona horaria.
REPORT_HOUR = 8
REPORT_MINUTE = 0
TIMEZONE = "America/Santiago"   # hora de Chile (ajusta solo el horario de verano)

# 4) Cada cuantos minutos revisa noticias nuevas el vigilante (--watch).
WATCH_EVERY_MIN = 5

# 5) Paises del calendario economico que te importan.
#    "USD" mueve mas el cripto. Agrega "EUR","CNY","GBP" si quieres globales.
CALENDAR_COUNTRIES = ["USD"]

# ---------------------------------------------------------------------
#  De aqui para abajo NO necesitas tocar nada (pero puedes si quieres).
# ---------------------------------------------------------------------

# Fuentes de noticias (RSS gratis, sin cuenta).
#   - Medios directos: dan titulares crudos (se filtran por palabras clave).
#   - "Busquedas" de Google Noticias: ya vienen filtradas por tema, asi que
#     capturan TODO lo relevante de ese tema aunque el titular no tenga las
#     palabras exactas (ej: discusiones de Trump, geopolitica, Fed).
NEWS_FEEDS = {
    # --- cripto ---
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block":     "https://www.theblock.co/rss.xml",
    # --- mercados / macro ---
    "CNBC":          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "MarketWatch":   "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo BTC":     "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
    "Investing":     "https://www.investing.com/rss/news_25.rss",
}

# Busquedas temáticas de Google Noticias (barren TODOS los medios por tema).
# "when:1d" = solo de las ultimas 24h. Son las que capturan a Trump, la Fed, etc.
GOOGLE_TOPICS = {
    "Trump/mercados": "Trump economy OR tariffs OR trade when:1d",
    "Fed/tasas":      "Federal Reserve interest rates OR inflation when:1d",
    "Geopolitica":    "geopolitics OR war OR sanctions OR oil market when:1d",
    "Cripto/regulacion": "bitcoin OR crypto SEC OR ETF OR regulation when:1d",
}
# Las busquedas de Google ya vienen filtradas -> puntaje base pequeño.
# (Bajo, para que NO pasen solas: deben ademas tener palabras fuertes.)
GOOGLE_BASE_SCORE = 1

# Palabras de ALTO impacto (cada una suma 3 puntos).
KW_HIGH = [
    "fed", "fomc", "powell", "rate cut", "rate hike", "interest rate", "cpi",
    "inflation", "pce", "jobs report", "payroll", "sec", "etf", "hack",
    "exploit", "breach", "bankrupt", "insolven", "liquidat", "default",
    "recession", "tariff", "trade war", "sanction", "war", "invasion",
    "lawsuit", "crash", "collapse", "halt", "emergency", "downgrade",
    "shutdown", "stimulus", "bailout",
]

# Palabras de impacto MEDIO (cada una suma 1 punto).
KW_MED = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "regulation", "regulat",
    "china", "trump", "powell", "treasury", "jobs", "unemployment", "gdp",
    "yield", "dollar", "euro", "gold", "oil", "stocks", "nasdaq", "s&p",
    "dow", "whale", "billion", "trillion", "iran", "russia", "opec",
    "tariff", "geopolit", "election", "debt",
]

# Umbral: si el titular suma este puntaje o mas, dispara ALERTA instantanea.
# Subido a 6 -> solo lo REALMENTE fuerte (ej: palabra de alto impacto + tema seguido).
ALERT_THRESHOLD = 6

# Tope de alertas por revision (anti-inundacion). Si hay mas que superan el
# umbral, manda solo las N mas fuertes; el resto lo veras en el informe diario.
MAX_ALERTS_PER_RUN = 4
