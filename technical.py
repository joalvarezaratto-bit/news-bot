"""
Analisis tecnico de BTC en temporalidad 4 horas.

Todo se calcula sobre las velas 4h de Binance (gratis, sin API key):
  - Soportes y resistencias: niveles donde el precio giro varias veces.
  - Fibonacci: retrocesos del ultimo swing (impulso) importante.

Honesto: son NIVELES calculados con reglas (donde el precio reacciono antes),
no una prediccion. El mercado puede romperlos. Sirven de mapa, no de garantia.
"""
import requests

FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]

def fetch_klines(symbol="BTCUSDT", interval="4h", limit=120):
    """Velas 4h de Binance. Devuelve lista de dicts {t, o, h, l, c}."""
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    out = []
    for k in r.json():
        out.append({"t": k[0] // 1000,
                    "o": float(k[1]), "h": float(k[2]),
                    "l": float(k[3]), "c": float(k[4])})
    return out

def _pivots(candles, k=3):
    """Detecta pivotes: maximos/minimos locales (giros del precio)."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        win = candles[i - k:i + k + 1]
        h, l = candles[i]["h"], candles[i]["l"]
        if h == max(c["h"] for c in win):
            highs.append((i, h))
        if l == min(c["l"] for c in win):
            lows.append((i, l))
    return highs, lows

def _cluster(levels, price, tol=0.006):
    """Agrupa niveles cercanos (dentro de tol%) y cuenta cuantas veces se toco."""
    grupos = []
    for _, p in sorted(levels, key=lambda x: x[1]):
        for g in grupos:
            if abs(p - g["price"]) / price <= tol:
                g["price"] = (g["price"] * g["touches"] + p) / (g["touches"] + 1)
                g["touches"] += 1
                break
        else:
            grupos.append({"price": p, "touches": 1})
    return grupos

def support_resistance(candles, max_niveles=3):
    """Devuelve (soportes, resistencias) relativos al precio actual."""
    price = candles[-1]["c"]
    highs, lows = _pivots(candles)
    niveles = _cluster(highs + lows, price)
    sop = sorted([g for g in niveles if g["price"] < price * 0.999],
                 key=lambda g: (-g["touches"], -g["price"]))
    res = sorted([g for g in niveles if g["price"] > price * 1.001],
                 key=lambda g: (-g["touches"], g["price"]))
    # los mas cercanos y mas tocados primero
    sop = sorted(sop[:max_niveles], key=lambda g: -g["price"])
    res = sorted(res[:max_niveles], key=lambda g: g["price"])
    return [g["price"] for g in sop], [g["price"] for g in res]

def fibonacci(candles, lookback=60):
    """Fibonacci sobre el swing (impulso) mas reciente e importante.
    Devuelve dict {dir, hi, lo, levels:{ratio:precio}} o None."""
    ventana = candles[-lookback:] if len(candles) > lookback else candles
    hi_i = max(range(len(ventana)), key=lambda i: ventana[i]["h"])
    lo_i = min(range(len(ventana)), key=lambda i: ventana[i]["l"])
    hi = ventana[hi_i]["h"]
    lo = ventana[lo_i]["l"]
    if hi == lo:
        return None
    # direccion del impulso: cual extremo es mas reciente
    subiendo = hi_i > lo_i          # el maximo llego despues -> impulso al alza
    levels = {}
    for r in FIB_RATIOS:
        # retroceso medido desde el fin del impulso hacia su inicio
        if subiendo:
            levels[r] = hi - (hi - lo) * r
        else:
            levels[r] = lo + (hi - lo) * r
    return {"dir": "alza" if subiendo else "baja",
            "hi": hi, "lo": lo, "levels": levels}

def analyze(symbol="BTCUSDT"):
    """Junta todo. Devuelve dict con velas, precio, niveles y fibonacci."""
    candles = fetch_klines(symbol=symbol)
    price = candles[-1]["c"]
    sop, res = support_resistance(candles)
    fib = fibonacci(candles)
    return {"symbol": symbol, "candles": candles, "price": price,
            "soportes": sop, "resistencias": res, "fib": fib}

def _fmt(p):
    return f"${p:,.0f}"

def text_summary(a):
    """Resumen en texto para el comando /tecnico."""
    lineas = [f"📐 <b>ANÁLISIS TÉCNICO BTC · 4H</b>",
              f"Precio actual: <b>{_fmt(a['price'])}</b>"]
    lineas.append("")
    lineas.append("🔴 <b>Resistencias</b> (techos):")
    if a["resistencias"]:
        for p in a["resistencias"]:
            d = (p / a["price"] - 1) * 100
            lineas.append(f"   • {_fmt(p)}  <i>(+{d:.1f}%)</i>")
    else:
        lineas.append("   <i>sin resistencias claras arriba</i>")
    lineas.append("🟢 <b>Soportes</b> (pisos):")
    if a["soportes"]:
        for p in a["soportes"]:
            d = (1 - p / a["price"]) * 100
            lineas.append(f"   • {_fmt(p)}  <i>(-{d:.1f}%)</i>")
    else:
        lineas.append("   <i>sin soportes claros abajo</i>")
    fib = a["fib"]
    if fib:
        lineas.append("")
        lineas.append(f"📏 <b>Fibonacci</b> (impulso {fib['dir']}, "
                      f"{_fmt(fib['lo'])}–{_fmt(fib['hi'])}):")
        for r in [0.236, 0.382, 0.5, 0.618, 0.786]:
            lineas.append(f"   {r:.3f} → {_fmt(fib['levels'][r])}")
    lineas.append("")
    lineas.append("<i>Niveles calculados, no predicción. El precio puede romperlos.</i>")
    return "\n".join(lineas)

def make_chart(a, path="btc_4h.png"):
    """Dibuja las velas 4h con soportes, resistencias y Fibonacci. Guarda PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    candles = a["candles"][-80:]   # ultimas ~13 dias
    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("#0e1117")
    ax.set_facecolor("#0e1117")

    for i, c in enumerate(candles):
        subio = c["c"] >= c["o"]
        color = "#26a69a" if subio else "#ef5350"
        # mecha (high-low)
        ax.plot([i, i], [c["l"], c["h"]], color=color, linewidth=0.8, zorder=2)
        # cuerpo (open-close)
        lo_body = min(c["o"], c["c"])
        alto = abs(c["c"] - c["o"]) or (c["h"] * 0.0005)
        ax.add_patch(Rectangle((i - 0.3, lo_body), 0.6, alto,
                               facecolor=color, edgecolor=color, zorder=3))

    n = len(candles)
    # resistencias (rojo) y soportes (verde)
    for p in a["resistencias"]:
        ax.axhline(p, color="#ef5350", linestyle="--", linewidth=1.1, alpha=0.9, zorder=1)
        ax.text(n - 0.5, p, f" R {p:,.0f}", color="#ef5350", va="center",
                fontsize=9, fontweight="bold")
    for p in a["soportes"]:
        ax.axhline(p, color="#26a69a", linestyle="--", linewidth=1.1, alpha=0.9, zorder=1)
        ax.text(n - 0.5, p, f" S {p:,.0f}", color="#26a69a", va="center",
                fontsize=9, fontweight="bold")
    # fibonacci (naranja punteado)
    fib = a["fib"]
    if fib:
        for r in [0.236, 0.382, 0.5, 0.618, 0.786]:
            p = fib["levels"][r]
            ax.axhline(p, color="#f0b90b", linestyle=":", linewidth=0.9, alpha=0.7, zorder=1)
            ax.text(0, p, f"{r:.3f} ", color="#f0b90b", va="center", ha="right",
                    fontsize=8)
    # precio actual
    ax.axhline(a["price"], color="white", linewidth=0.9, alpha=0.5, zorder=1)

    ax.set_title(f"BTC/USDT · 4H · {a['price']:,.0f} USD",
                 color="white", fontsize=14, fontweight="bold")
    ax.tick_params(colors="#888")
    for s in ax.spines.values():
        s.set_color("#333")
    ax.grid(True, color="#222", linewidth=0.5)
    ax.set_xlim(-2, n + 6)
    ax.margins(y=0.05)
    plt.tight_layout()
    fig.savefig(path, dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path
