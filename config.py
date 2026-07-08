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
except ImportError:
    _LOCAL_TOKEN, _LOCAL_CHAT = "", 0

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", _LOCAL_TOKEN)
CHAT_ID = int(os.environ.get("CHAT_ID", _LOCAL_CHAT or 0))

# 3) Hora del informe diario (formato 24h) y tu zona horaria.
REPORT_HOUR = 9
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
NEWS_FEEDS = {
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Yahoo BTC":     "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
    "Investing":     "https://www.investing.com/rss/news_25.rss",
}

# Palabras de ALTO impacto (cada una suma 3 puntos).
KW_HIGH = [
    "fed", "fomc", "powell", "rate", "interest rate", "cpi", "inflation",
    "sec", "etf", "hack", "exploit", "breach", "bankrupt", "insolven",
    "liquidat", "default", "recession", "tariff", "sanction", "war",
    "lawsuit", "ban ", "crash", "collapse", "halt", "emergency",
]

# Palabras de impacto MEDIO (cada una suma 1 punto).
KW_MED = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "regulation", "regulat",
    "china", "trump", "treasury", "jobs", "unemployment", "gdp", "yield",
    "dollar", "gold", "oil", "stocks", "nasdaq", "s&p", "whale", "billion",
]

# Umbral: si el titular suma este puntaje o mas, dispara ALERTA instantanea.
ALERT_THRESHOLD = 3
