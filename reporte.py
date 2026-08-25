"""Genera el reporte HTML de seguimiento: tabla de rentabilidades por serie,
gráfico del índice ajustado por dividendos (base 100) y dividendos pagados.

El gráfico es SVG generado acá mismo (sin librerías): líneas de 2px, grilla
hairline, etiquetas al final de cada línea y tooltip con crosshair vía un JS
mínimo embebido. Colores según paleta validada (claro/oscuro).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

import rentabilidad

RUTA_REPORTE = Path(__file__).parent / "reportes" / "reporte.html"
RUTA_LOGO = Path(__file__).parent / "assets" / "ameris-logo.png"
RUTA_FAVICON = Path(__file__).parent / "assets" / "ameris-favicon.png"


def _logo_uri() -> str:
    """Logo Ameris embebido como data URI (reporte autocontenido)."""
    if not RUTA_LOGO.exists():
        return ""
    b64 = base64.b64encode(RUTA_LOGO.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def _favicon_uri() -> str:
    """Ícono de pestaña: isotipo triangular Ameris (cuadrado). Cae al logo
    completo si el favicon recortado no existe."""
    ruta = RUTA_FAVICON if RUTA_FAVICON.exists() else RUTA_LOGO
    if not ruta.exists():
        return ""
    b64 = base64.b64encode(ruta.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"

# paleta categórica validada (validate_palette.js: PASS claro y oscuro)
_PALETA = [
    ("#2a78d6", "#3987e5"),  # azul
    ("#eb6834", "#d95926"),  # naranjo
    ("#1baf7a", "#199e70"),  # aqua
    ("#eda100", "#c98500"),  # amarillo
    ("#e87ba4", "#d55181"),  # magenta
    ("#008300", "#008300"),  # verde
    ("#4a3aa7", "#9085e9"),  # violeta
    ("#e34948", "#e66767"),  # rojo
]

_MESES = ["ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]

# geometría del gráfico
_ANCHO, _ALTO = 980, 380
_MI, _MD, _MS, _MB = 52, 118, 18, 30  # márgenes izq/der/sup/inf


# ---------------------------------------------------------------- formato CL
def _num(x: float | None, dec: int = 2) -> str:
    if x is None or pd.isna(x):
        return "–"
    entero, _, decimal = f"{x:,.{dec}f}".partition(".")
    entero = entero.replace(",", ".")
    return f"{entero},{decimal}" if decimal else entero


def _pct(x: float | None, dec: int = 2) -> str:
    if x is None or pd.isna(x):
        return "–"
    return _num(x * 100, dec) + "%"


def _celda_pct(x: float | None) -> str:
    if x is None or pd.isna(x):
        return '<td class="num">–</td>'
    clase = "pos" if x >= 0 else "neg"
    signo = "+" if x > 0 else ""
    return f'<td class="num {clase}">{signo}{_pct(x)}</td>'


def _fecha_cl(iso: str) -> str:
    f = dt.date.fromisoformat(iso)
    return f"{f.day:02d}-{_MESES[f.month - 1]}-{f.year}"


# ---------------------------------------------------------------- gráfico SVG
def _ticks(lo: float, hi: float, objetivo: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    bruto = (hi - lo) / objetivo
    magnitud = 10 ** len(str(int(bruto))) / 10 if bruto >= 1 else 1
    for paso in (1, 2, 2.5, 5, 10):
        if bruto <= paso * magnitud:
            paso *= magnitud
            break
    else:
        paso = 10 * magnitud
    inicio = int(lo / paso) * paso
    ticks = []
    t = inicio
    while t <= hi + paso * 0.01:
        if t >= lo - paso * 0.01:
            ticks.append(round(t, 6))
        t += paso
    return ticks


def _svg_ventana(series: dict[str, pd.Series], colores: dict[str, int],
                 id_grafico: str, modo: str = "base100") -> str:
    """SVG de líneas para una ventana de tiempo. `modo` controla el tooltip:
    "base100" muestra nivel y variación %; "abs" solo el nivel (ej. AUM)."""
    fechas_union = sorted({f for s in series.values() for f in s.index})
    if not fechas_union or all(len(s) < 2 for s in series.values()):
        return "<p class='muted'>Sin datos suficientes en esta ventana.</p>"

    x0, x1 = fechas_union[0].toordinal(), fechas_union[-1].toordinal()
    x1 = max(x1, x0 + 1)
    vmin = min(s.min() for s in series.values())
    vmax = max(s.max() for s in series.values())
    margen = (vmax - vmin) * 0.06 or 1
    vmin, vmax = vmin - margen, vmax + margen

    def px(fecha) -> float:
        return _MI + (fecha.toordinal() - x0) / (x1 - x0) * (_ANCHO - _MI - _MD)

    def py(v: float) -> float:
        return _MS + (vmax - v) / (vmax - vmin) * (_ALTO - _MS - _MB)

    partes = [f'<svg viewBox="0 0 {_ANCHO} {_ALTO}" role="img" '
              f'aria-label="Índice de rentabilidad ajustado, base 100">']

    # grilla horizontal + etiquetas Y
    for t in _ticks(vmin, vmax):
        y = py(t)
        partes.append(f'<line x1="{_MI}" y1="{y:.1f}" x2="{_ANCHO - _MD}" y2="{y:.1f}" '
                      f'class="grid"/>')
        partes.append(f'<text x="{_MI - 8}" y="{y + 4:.1f}" class="tick" '
                      f'text-anchor="end">{_num(t, 0)}</text>')

    # etiquetas X (~6 fechas)
    paso = max(1, len(fechas_union) // 6)
    for f in fechas_union[::paso]:
        x = px(f)
        etiqueta = f"{_MESES[f.month - 1]} {f.year % 100:02d}"
        partes.append(f'<text x="{x:.1f}" y="{_ALTO - 8}" class="tick" '
                      f'text-anchor="middle">{etiqueta}</text>')

    # líneas por serie + punto final
    finales = []  # (y_final, nombre, color_idx, x_final)
    for nombre, s in series.items():
        idx = colores[nombre] % len(_PALETA)
        puntos = " ".join(f"{px(f):.1f},{py(v):.1f}" for f, v in s.items())
        partes.append(f'<polyline points="{puntos}" class="linea s{idx}"/>')
        xf, yf = px(s.index[-1]), py(s.iloc[-1])
        partes.append(f'<circle cx="{xf:.1f}" cy="{yf:.1f}" r="4.5" class="dot s{idx}"/>')
        finales.append([yf, nombre, idx, xf])

    # etiquetas directas al final, con separación mínima y línea guía si se corren
    finales.sort()
    y_previa = -1e9
    for yf, nombre, idx, xf in finales:
        y_texto = max(yf, y_previa + 15)
        y_previa = y_texto
        if abs(y_texto - yf) > 2:  # se corrió: línea guía
            partes.append(f'<line x1="{xf + 6:.1f}" y1="{yf:.1f}" '
                          f'x2="{_ANCHO - _MD + 26}" y2="{y_texto - 4:.1f}" class="guia"/>')
        partes.append(f'<circle cx="{_ANCHO - _MD + 32}" cy="{y_texto - 4:.1f}" r="4" '
                      f'class="dot s{idx}"/>')
        partes.append(f'<text x="{_ANCHO - _MD + 40}" y="{y_texto:.1f}" '
                      f'class="etiqueta">{nombre}</text>')

    # capa interactiva: crosshair + captura de mouse
    partes.append(f'<line id="{id_grafico}-ch" class="crosshair" y1="{_MS}" '
                  f'y2="{_ALTO - _MB}" x1="-10" x2="-10"/>')
    partes.append(f'<rect class="captura" x="{_MI}" y="{_MS}" '
                  f'width="{_ANCHO - _MI - _MD}" height="{_ALTO - _MS - _MB}"/>')
    partes.append("</svg>")

    # datos para el tooltip
    datos = {
        "x0": x0, "x1": x1, "mi": _MI, "md": _MD, "ancho": _ANCHO, "modo": modo,
        "fechas": [f.strftime("%Y-%m-%d") for f in fechas_union],
        "series": {n: {f.strftime("%Y-%m-%d"): round(v, 3) for f, v in s.items()}
                   for n, s in series.items()},
        "colores": {n: colores[n] % len(_PALETA) for n in series},
    }
    return ("".join(partes)
            + f'<script type="application/json" id="{id_grafico}-datos">'
            + json.dumps(datos, ensure_ascii=False) + "</script>")


def _ventanas(ultimo: dt.date) -> dict[str, dt.date | None]:
    return {
        "1M": ultimo - dt.timedelta(days=30),
        "3M": ultimo - dt.timedelta(days=91),
        "YTD": dt.date(ultimo.year - 1, 12, 31),
        "12M": ultimo - dt.timedelta(days=365),
        "Todo": None,
    }


def _bloques_ventanas(id_prefijo: str, indices: dict[str, pd.Series],
                      colores: dict[str, int], ultimo: dt.date) -> tuple[str, str]:
    """Botones preset + gráficos base 100 por ventana para un set de índices."""
    botones, bloques = [], []
    for nombre_v, desde in _ventanas(ultimo).items():
        series_ventana = {}
        for nombre, s0 in indices.items():
            s = s0 if desde is None else s0[s0.index >= pd.Timestamp(desde)]
            if len(s) >= 2:
                series_ventana[nombre] = s / s.iloc[0] * 100
        id_g = f"{id_prefijo}-{nombre_v.lower()}"
        activo = " activo" if nombre_v == "12M" else ""
        botones.append(f'<button class="preset{activo}" data-destino="{id_g}">{nombre_v}</button>')
        bloques.append(f'<div class="grafico{activo}" id="{id_g}">'
                       + _svg_ventana(series_ventana, colores, id_g) + "</div>")
    return "".join(botones), "".join(bloques)


# ---------------------------------------------------------------- reporte
def _slug(texto: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") or "cat"


def generar(con: sqlite3.Connection, fondos: dict) -> Path:
    # navegación agrupada por categoría de estrategia; cada categoría tiene su
    # propio comparativo (compara fondos similares entre sí) + detalle por fondo
    categorias: dict[str, list] = {}
    for fondo_id, ficha in fondos.items():
        cat = ficha.get("categoria", "Otros fondos")
        categorias.setdefault(cat, []).append((fondo_id, ficha))

    nav_grupos, cuerpos = [], []
    cats = list(categorias)
    for cat, items in categorias.items():
        cid = _slug(cat)
        es_primera = cat == cats[0]  # categoría con la vista activa por defecto
        act = " activo" if es_primera else ""
        botones = [f'<button class="tab tab-comp{act}" data-destino="vista-comp-{cid}">'
                   f'<span class="ico-comp"></span>Comparativo</button>',
                   '<div class="nav-sep"></div>']
        cuerpos.append(f'<div class="vista{" activa" if es_primera else ""}" '
                       f'id="vista-comp-{cid}">'
                       f'{_seccion_comparativo(con, dict(items), cat)}</div>')
        for i, (fondo_id, ficha) in enumerate(items):
            corto = ficha.get("nombre_corto", ficha["nombre"])
            idx = i % len(_PALETA)
            botones.append(f'<button class="tab" data-destino="vista-{fondo_id}">'
                           f'<span class="swatch s{idx}"></span>{corto}</button>')
            cuerpos.append(f'<div class="vista" id="vista-{fondo_id}">'
                           f'{_seccion_fondo(con, fondo_id, ficha)}</div>')
        nav_grupos.append(
            f'<details class="nav-menu{" activa" if es_primera else ""}">'
            f'<summary class="nav-cat">{cat}</summary>'
            f'<div class="nav-menu-items">{"".join(botones)}</div>'
            f'</details>')

    logo = _logo_uri()
    logo_html = f'<img class="logo" src="{logo}" alt="Ameris">' if logo else ""
    # favicon (ícono de la pestaña del navegador): isotipo triangular Ameris
    favicon = _favicon_uri()
    favicon_html = f'<link rel="icon" type="image/png" href="{favicon}">' if favicon else ""
    html = _PLANTILLA.replace("__NAV__", "".join(nav_grupos)) \
                     .replace("__CUERPO__", "\n".join(cuerpos)) \
                     .replace("__LOGO__", logo_html) \
                     .replace("__FAVICON__", favicon_html) \
                     .replace("__GENERADO__", dt.datetime.now().strftime("%d-%m-%Y %H:%M"))
    RUTA_REPORTE.parent.mkdir(exist_ok=True)
    RUTA_REPORTE.write_text(html, encoding="utf-8")
    return RUTA_REPORTE


# ---------------------------------------------------------------- comparativo
def _check(v) -> str:
    if v is None or v == "":
        return '<span class="muted">–</span>'
    if v is True:
        return '<span class="si">✔</span>'
    if v is False:
        return '<span class="no">✘</span>'
    return str(v)


def _fin_mes_valor(s: pd.Series, anio: int, mes: int):
    sub = s[(s.index.year == anio) & (s.index.month == mes)]
    return sub.iloc[-1] if len(sub) else None


def _precio_fresco(con: sqlite3.Connection, nemos: list[str], fecha_cmf: str):
    """Último precio transado en Bolsa posterior al último cierre CMF, o None.

    Devuelve (precio, fecha). Es precio de mercado (lleva prima/descuento sobre
    el NAV); se muestra solo como referencia del último precio reportado — NO
    entra en el índice de rentabilidad, para no contaminar las ventanas."""
    if not nemos:
        return None
    q = ",".join("?" * len(nemos))
    row = con.execute(
        f"""SELECT fecha, precio FROM precio_bolsa
            WHERE nemo IN ({q}) AND fecha > ?
            ORDER BY fecha DESC, monto DESC LIMIT 1""",
        (*nemos, fecha_cmf)).fetchone()
    return (row[1], row[0]) if row else None


def _presencia_fondo(con: sqlite3.Connection, ficha: dict) -> str | None:
    """Presencia bursátil en vivo (presen_aju por nemo, tabla presencia_bursatil).
    Devuelve las series con presencia ≥25% y su %, 'No' si ninguna, o None si no
    hay dato guardado (para caer al valor estático de la ficha)."""
    filas = []
    for serie, cfg in ficha["series"].items():
        nemos = cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else [])
        vals = [r[0] for n in nemos
                for r in [con.execute(
                    "SELECT presen_aju FROM presencia_bursatil WHERE nemo=?", (n,)).fetchone()]
                if r and r[0] is not None]
        if vals:
            filas.append((serie, max(vals)))
    if not filas:
        return None
    con_pres = sorted(((s, p) for s, p in filas if p >= 25), key=lambda x: -x[1])
    return " · ".join(f"{s} {p:.0f}%" for s, p in con_pres) if con_pres else "No"


def _liquidez_celda(con: sqlite3.Connection, ficha: dict, aum: float) -> str:
    """Nivel de liquidez del fondo (Alta/Media/Baja) con su detalle.

    Alta  = rescatable (el inversionista rescata directo al fondo).
    Media = no rescatable con mercado secundario (presencia ≥25% o turnover ≥15%).
    Baja  = no rescatable con baja liquidez secundaria (única salida es la bolsa
            y casi no transa).
    Turnover = monto transado 12m / AUM. Presencia y volumen son datos en vivo.
    """
    tipo = str(ficha.get("ficha", {}).get("Tipo", ""))
    rescatable = "no rescat" not in tipo.lower()
    nemos = [n for cfg in ficha["series"].values()
             for n in (cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else []))]
    vol = pres = 0.0
    for nemo in nemos:
        rv = con.execute("SELECT monto12m FROM volumen_bolsa WHERE nemo=?", (nemo,)).fetchone()
        if rv and rv[0]:
            vol += rv[0]
        rp = con.execute("SELECT presen_aju FROM presencia_bursatil WHERE nemo=?", (nemo,)).fetchone()
        if rp and rp[0]:
            pres = max(pres, rp[0])
    turn = (vol / aum * 100) if aum else 0.0
    if rescatable:
        nivel, color, desc = "Alta", "#1a9850", "rescate directo al fondo"
    elif pres >= 25 or turn >= 15:
        nivel, color, desc = "Media", "#d9a300", "salida por bolsa"
    else:
        nivel, color, desc = "Baja", "#d73027", "salida por bolsa"
    detalle = f"{desc} · vol $ {_num(vol / 1e6, 0)} MM/año"
    if not rescatable:
        detalle += f" · pres {pres:.0f}%"
    return (f'<span style="color:{color};font-weight:700">● {nivel}</span>'
            f'<div class="muted mini">{detalle}</div>')


def _seccion_comparativo(con: sqlite3.Connection, fondos: dict,
                         titulo: str = "Comparativo de fondos") -> str:
    colores = {ficha.get("nombre_corto", fid): i
               for i, (fid, ficha) in enumerate(fondos.items())}

    # serie comparativa de cada fondo + AUM total del fondo (todas las series CMF)
    indices: dict[str, pd.Series] = {}
    dfs: dict[str, pd.DataFrame] = {}
    aums: dict[str, pd.Series] = {}
    etiquetas_serie: dict[str, str] = {}
    mensual: dict[str, bool] = {}  # fondos con valorización mensual (vol √12, nota)
    for fid, ficha in fondos.items():
        corto = ficha.get("nombre_corto", fid)
        serie = ficha.get("serie_comparativa") or next(iter(ficha["series"]))
        cfg = ficha["series"].get(serie)
        if not cfg:
            continue
        nemos = cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else [])
        df = rentabilidad.serie_ajustada(con, fid, nemos, cfg["series_cmf"],
                                         ficha.get("anomalias"))
        if df.empty:
            continue
        dfs[corto] = df
        indices[corto] = df["indice"]
        etiquetas_serie[corto] = serie
        mensual[corto] = ficha.get("frecuencia") == "mensual"
        # AUM = Activo Total del fondo (CMF). Ese valor es fondo-level: viene
        # idéntico en cada fila de serie, así que se toma UNA vez (MAX), no se suma.
        aum = pd.read_sql_query(
            """SELECT fecha, MAX(activo_total) AS pat FROM valores_cuota
               WHERE fondo_id = ? AND activo_total IS NOT NULL
               GROUP BY fecha ORDER BY fecha""",
            con, params=[fid], parse_dates=["fecha"]).set_index("fecha")["pat"]
        if not aum.empty:
            aums[corto] = aum

    if not dfs:
        return "<section><p>Sin datos.</p></section>"

    ultimo = max(s.index[-1] for s in indices.values()).date()

    # --- ficha cualitativa (datos manuales de fondos.json + AUM vivo) ---
    campos = []
    for ficha in fondos.values():
        for k in ficha.get("ficha", {}):
            if k not in campos:
                campos.append(k)
    # presencia bursátil siempre visible (dato en vivo, aunque la ficha no lo liste)
    if "Presencia bursátil" not in campos:
        pos = campos.index("Tipo inversionista") + 1 if "Tipo inversionista" in campos else len(campos)
        campos.insert(pos, "Presencia bursátil")
    # nivel de liquidez (calculado en vivo: rescatabilidad + presencia + volumen)
    if "Liquidez" not in campos:
        pos = campos.index("Tipo") + 1 if "Tipo" in campos else 0
        campos.insert(pos, "Liquidez")
    encabezado = "".join(
        f'<th>{f.get("nombre_corto", fid)}</th>' for fid, f in fondos.items())
    filas_ficha = [f'<tr><td class="etq">AUM fondo <span class="muted mini">(activo total)</span></td>' + "".join(
        f'<td>$ {_num(aums[f.get("nombre_corto", fid)].iloc[-1] / 1e6, 0)} MM</td>'
        if f.get("nombre_corto", fid) in aums else "<td>–</td>"
        for fid, f in fondos.items()) + "</tr>"]
    filas_ficha.append('<tr><td class="etq">Series seguidas</td>' + "".join(
        f'<td>{", ".join(f["series"])}</td>' for f in fondos.values()) + "</tr>")
    for campo in campos:
        celdas = []
        for f in fondos.values():
            val = f.get("ficha", {}).get(campo)
            if campo == "Liquidez":
                corto = f.get("nombre_corto", "")
                aum = aums[corto].iloc[-1] if corto in aums else 0.0
                celdas.append(f'<td>{_liquidez_celda(con, f, aum)}</td>')
                continue
            if campo == "Presencia bursátil":
                din = _presencia_fondo(con, f)
                if din and din != "No":
                    val = din                      # hay presencia en vivo ≥25%
                elif not (val and "market maker" in str(val).lower()):
                    val = din if din is not None else val  # sin MM: usar el vivo
            celdas.append(f'<td>{_check(val)}</td>')
        filas_ficha.append(f'<tr><td class="etq">{campo}</td>' + "".join(celdas) + "</tr>")

    # fecha(s) de última verificación de la ficha cualitativa (dato manual)
    verifs = sorted({f.get("ficha_verificada", "") for f in fondos.values()
                     if f.get("ficha_verificada")})
    rango_verif = (verifs[0] if len(verifs) == 1
                   else f"{verifs[0]} … {verifs[-1]}") if verifs else "s/f"

    # --- tabla cuantitativa (serie comparativa, ventanas rolling + riesgo 12M) ---
    filas_cuant = []
    for corto, df in dfs.items():
        r = rentabilidad.resumen_rentabilidades(df)
        if not r:
            continue
        idx = colores[corto] % len(_PALETA)
        desde_12m = df.index[-1] - pd.Timedelta(days=365)
        r12 = df.loc[df.index >= desde_12m, "retorno_diario"].dropna()
        es_mensual = mensual.get(corto, False)
        # anualización según frecuencia de los retornos: √12 para valor cuota
        # mensual (deuda inmobiliaria), √365 para diario
        if es_mensual:
            vol = r12.std() * (12 ** 0.5) if len(r12) >= 6 else None
        else:
            vol = r12.std() * (365 ** 0.5) if len(r12) > 30 else None
        ind12 = df.loc[df.index >= desde_12m, "indice"]
        mdd = (ind12 / ind12.cummax() - 1).min() if len(ind12) >= (6 if es_mensual else 31) else None
        marca = '<sup>*</sup>' if es_mensual else ""
        aum_fondo = aums.get(corto)  # patrimonio total del fondo (todas las series)
        filas_cuant.append(
            "<tr>"
            f'<td><span class="swatch s{idx}"></span>{corto}</td>'
            f'<td>{etiquetas_serie[corto]}</td>'
            f'<td class="num">{"$ " + _num(aum_fondo.iloc[-1] / 1e6, 0) + " MM" if aum_fondo is not None and len(aum_fondo) else "–"}</td>'
            + _celda_pct(r.get("mes_anterior")) + _celda_pct(r["mtd"])
            + _celda_pct(r["ytd"]) + _celda_pct(r["12m"])
            + f'<td class="num">{_pct(vol)}{marca}</td>'
            + f'<td class="num">{_pct(mdd)}{marca}</td>'
            "</tr>")
    hay_mensual = any(mensual.values())

    fin_mes_prev = dt.date(ultimo.year, ultimo.month, 1) - dt.timedelta(days=1)
    etiqueta_mes = f"{_MESES[fin_mes_prev.month - 1]}-{fin_mes_prev.year % 100:02d}"

    # --- matriz de rentabilidad mensual (mes vigente a la izquierda → hacia atrás) ---
    meses: list[tuple[int, int]] = []
    a, m = ultimo.year, ultimo.month
    for _ in range(15):
        meses.append((a, m))
        a, m = (a, m - 1) if m > 1 else (a - 1, 12)
    enc_meses = "".join(
        f'<th>{_MESES[m - 1]}<div class="mini">{a}</div></th>' for a, m in meses)
    filas_matriz = []
    for corto, indice in indices.items():
        idx = colores[corto] % len(_PALETA)
        celdas = []
        for a, m in meses:
            a0, m0 = (a, m - 1) if m > 1 else (a - 1, 12)
            i0, i1 = _fin_mes_valor(indice, a0, m0), _fin_mes_valor(indice, a, m)
            celdas.append(_celda_pct(i1 / i0 - 1 if i0 is not None and i1 is not None else None))
        filas_matriz.append(
            f'<tr><td><span class="swatch s{idx}"></span>{corto} · '
            f'{etiquetas_serie[corto]}</td>' + "".join(celdas) + "</tr>")

    # --- gráficos: índice base 100 por ventana + evolución AUM ---
    # prefijo único por categoría: hay un comparativo por cada una y los ids no
    # pueden repetirse (si no, getElementById activa el gráfico de la otra vista)
    pref = f"g-comp-{_slug(titulo)}"
    botones, bloques = _bloques_ventanas(pref, indices, colores, ultimo)

    aum_mensual = {}
    for corto, aum in aums.items():
        s = aum.groupby(aum.index.to_period("M")).apply(lambda g: g.iloc[-1]) / 1e6
        s.index = s.index.to_timestamp(how="end").normalize()
        if len(s) >= 2:
            aum_mensual[corto] = s
    id_aum = f"{pref}-aum"
    grafico_aum = (f'<div class="grafico activo" id="{id_aum}">'
                   + _svg_ventana(aum_mensual, colores, id_aum, modo="abs")
                   + "</div>") if aum_mensual else ""

    # --- mayor aportante de cada fondo (concentración de propiedad) ---
    filas_mayor, trim_mayor = [], None
    for fondo_id, ficha in fondos.items():
        corto = ficha.get("nombre_corto", fondo_id)
        if corto not in colores:
            continue
        trim, top1 = _mayores_aportantes(con, fondo_id, top=1)
        if top1 is None or top1.empty:
            continue
        trim_mayor = trim_mayor or trim
        r0 = top1.iloc[0]
        idx = colores[corto] % len(_PALETA)
        filas_mayor.append(
            "<tr>"
            f'<td><span class="swatch s{idx}"></span>{corto}</td>'
            f'<td>{_nombre_aportante(r0.nombre)}</td>'
            f'<td class="muted">{_TIPO_AP.get(r0.tipo, "Jurídica")}</td>'
            f'<td class="num">{_num(r0.pct, 2)}%</td>'
            "</tr>")
    seccion_mayor = (f"""
  <h3>Mayor aportante por fondo <span class="muted mini">(al cierre de {trim_mayor} ·
     fuente CMF · concentración de propiedad)</span></h3>
  <div class="tabla-scroll"><table>
    <thead><tr><th>Fondo</th><th>Mayor aportante</th><th>Tipo</th><th>% propiedad</th></tr></thead>
    <tbody>{''.join(filas_mayor)}</tbody>
  </table></div>""" if filas_mayor else "")

    # notas para categorías con fondos de valorización mensual (deuda inmobiliaria):
    # su vol/MDD salen artificialmente bajos porque el valor cuota es a tasación
    # mensual, no a mercado diario → no comparables con los fondos diarios.
    nota_riesgo = ('<p class="muted mini"><strong>*</strong> Valorización mensual a '
                   'tasación (no mark-to-market): la Vol y el Max Drawdown salen '
                   'artificialmente bajos y <strong>no son comparables</strong> con los '
                   'fondos de valor cuota diario. Vol anualizada con √12.</p>'
                   if hay_mensual else "")
    nota_frescura = (' En fondos de valorización <strong>mensual</strong> (marcados *) las '
                     'rentabilidades y el AUM son al último cierre de mes de CMF (con rezago); '
                     'el último precio transado en Bolsa se muestra aparte en el detalle de '
                     'cada fondo (es precio de mercado, no entra en la rentabilidad).'
                     if hay_mensual else "")

    return f"""
<section>
  <h2>Comparativo · {titulo}</h2>
  <p class="muted">Al {_fecha_cl(ultimo.isoformat())} · CLP nominal · rentabilidades ajustadas por dividendos</p>

  <h3>Ficha comparativa</h3>
  <div class="tabla-scroll"><table class="ficha">
    <thead><tr><th></th>{encabezado}</tr></thead>
    <tbody>{''.join(filas_ficha)}</tbody>
  </table></div>
  <p class="muted mini"><strong>En vivo, se actualiza a diario</strong>: AUM,
     presencia bursátil, rentabilidades, riesgo y aportantes.{nota_frescura}
     <strong>Verificación periódica</strong> (reglamentos internos CMF / factsheets):
     el resto de la ficha — última verificación {rango_verif}; «–» = sin dato público.
     Editable en fondos.json → "ficha".</p>

  <h3>Rentabilidad y riesgo <span class="muted mini">(serie comparativa; 3M/YTD/12M a fin
     de mes cerrado — convención factsheet; MTD al último dato)</span></h3>
  <div class="tabla-scroll"><table>
    <thead><tr><th>Fondo</th><th>Serie</th><th>AUM fondo</th><th>{etiqueta_mes}</th>
    <th>MTD</th><th>YTD</th><th>12M</th><th>Vol 12M (a)</th><th>MDD 12M</th></tr></thead>
    <tbody>{''.join(filas_cuant)}</tbody>
  </table></div>
  {nota_riesgo}
{seccion_mayor}
  <h3>Rentabilidad mensual comparativa <span class="muted mini">(meses calendario;
     el mes en curso es parcial)</span></h3>
  <div class="tabla-scroll"><table class="matriz">
    <thead><tr><th>Fondo · Serie</th>{enc_meses}</tr></thead>
    <tbody>{''.join(filas_matriz)}</tbody>
  </table></div>

  <h3>Evolución índice ajustado <span class="muted mini">(base 100 al inicio de la ventana)</span></h3>
  <div class="presets">{botones}</div>
  {bloques}

  <h3>Evolución AUM <span class="muted mini">(activo total del fondo, MM CLP,
     cierre de mes)</span></h3>
  {grafico_aum}
</section>"""


_TIPO_AP = {"A": "Natural", "B": "Natural (extr.)", "G": "Inst./Fondo"}

# CMF abrevia los nombres de aportantes distinto según el fondo; se normalizan
# a un nombre canónico (clave sin acentos y en mayúsculas) para consistencia.
_ALIAS_APORTANTE = {
    "LARRAIN VIAL S.A. C. DE B.": "LARRAIN VIAL S.A CORREDORA DE BOLSA",
    # CMF entrega el nombre con un byte corrupto en la "Ó" de INVERSIÓN
    # (y en otra fila sin tilde); ambas variantes → nombre limpio.
    "BTG PACTUAL DEUDA ESTRATEGICA FONDO DE INVERSI": "BTG Pactual Deuda Estratégica FI",
    "BTG PACTUAL DEUDA ESTRATEGICA FONDO DE INVERSION": "BTG Pactual Deuda Estratégica FI",
}


def _nombre_aportante(nombre: str) -> str:
    import unicodedata
    clave = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore") \
                       .decode().upper().strip()
    return _ALIAS_APORTANTE.get(clave, nombre)


def _mayores_aportantes(con: sqlite3.Connection, fondo_id: str, top: int = 10):
    """Top `top` aportantes del último trimestre declarado en CMF (pestaña 27).
    Devuelve (etiqueta_trimestre, DataFrame) o (None, None)."""
    fila = con.execute("SELECT MAX(trimestre) FROM aportantes_mayores WHERE fondo_id=?",
                       (fondo_id,)).fetchone()
    trimestre = fila[0] if fila else None
    if not trimestre:
        return None, None
    df = pd.read_sql_query(
        """SELECT posicion, nombre, tipo, pct FROM aportantes_mayores
           WHERE fondo_id = ? AND trimestre = ? ORDER BY posicion LIMIT ?""",
        con, params=[fondo_id, trimestre, top])
    aa, mm = trimestre.split("-")
    etiqueta = f"{_MESES[int(mm) - 1]}-{aa}"
    return etiqueta, df


def _seccion_fondo(con: sqlite3.Connection, fondo_id: str, ficha: dict) -> str:
    colores = {serie: i for i, serie in enumerate(ficha["series"])}
    datos_series: dict[str, pd.DataFrame] = {}
    resumenes: dict[str, dict] = {}

    for serie, cfg in ficha["series"].items():
        nemos = cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else [])
        df = rentabilidad.serie_ajustada(con, fondo_id, nemos, cfg["series_cmf"],
                                         ficha.get("anomalias"))
        if df.empty:
            continue
        datos_series[serie] = df
        r = rentabilidad.resumen_rentabilidades(df)
        if r:
            resumenes[serie] = r

    if not datos_series:
        return f"<section><h2>{ficha['nombre']}</h2><p>Sin datos.</p></section>"

    ultimo = max(df.index[-1] for df in datos_series.values()).date()

    # mes calendario anterior: columna de control contra el factsheet mensual
    fin_mes_prev = dt.date(ultimo.year, ultimo.month, 1) - dt.timedelta(days=1)
    etiqueta_mes = f"{_MESES[fin_mes_prev.month - 1]}-{fin_mes_prev.year % 100:02d}"

    # tabla de certificación al cierre de mes (convención factsheet)
    filas_cierre = []
    corte_fondo = None
    for serie, df in datos_series.items():
        rc = rentabilidad.resumen_cierre_mensual(df)
        if not rc:
            continue
        corte_fondo = corte_fondo or rc["corte"]
        idx = colores[serie] % len(_PALETA)
        filas_cierre.append(
            "<tr>"
            f'<td><span class="swatch s{idx}"></span>{serie}</td>'
            + _celda_pct(rc["mes"]) + _celda_pct(rc["3m"]) + _celda_pct(rc["ytd"])
            + _celda_pct(rc["12m"]) + _celda_pct(rc["24m"]) + _celda_pct(rc["36m"])
            + "</tr>")

    filas_tabla = []
    for serie, r in resumenes.items():
        cfg = ficha["series"][serie]
        nemos = cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else [])
        idx = colores[serie] % len(_PALETA)
        anual = f' <span class="muted">({_pct(r["inicio_anual"])} anual)</span>' \
            if "inicio_anual" in r else ""
        # valor cuota oficial (NAV, CMF) + último precio Bolsa como referencia
        vc_txt = _num(r["valor_cuota"], 4)
        if ficha.get("precio_bolsa"):
            fresco = _precio_fresco(con, nemos, r["fecha"])
            if fresco:
                vc_txt += (f'<div class="muted mini">Bolsa {_fecha_cl(fresco[1])}: '
                           f'{_num(fresco[0], 4)}</div>')
        filas_tabla.append(
            "<tr>"
            f'<td><span class="swatch s{idx}"></span>{serie}</td>'
            f'<td class="muted">{nemos[-1] if nemos else "–"}</td>'
            f'<td class="num">{vc_txt}</td>'
            + _celda_pct(r["diaria"]) + _celda_pct(r["mes_anterior"])
            + _celda_pct(r["mtd"])
            + _celda_pct(r["3m"]) + _celda_pct(r["ytd"]) + _celda_pct(r["12m"])
            + f'<td class="num">{_pct(r["inicio"])}{anual}'
              f'<div class="muted mini">desde {_fecha_cl(r["fecha_inicio"])}</div></td>'
            "</tr>")

    # gráficos por ventana
    botones, bloques = _bloques_ventanas(
        f"g-{fondo_id}", {s: df["indice"] for s, df in datos_series.items()},
        colores, ultimo)

    # dividendos (un mismo reparto puede venir bajo nemo viejo y nuevo: se
    # agrupa por serie/fechas/monto para mostrarlo una sola vez)
    divs = pd.read_sql_query(
        """SELECT serie, MAX(nemo) AS nemo, fecha_limite, fecha_pago, monto,
                  MAX(descripcion) AS descripcion
           FROM dividendos WHERE fondo_id = ?
           GROUP BY serie, fecha_limite, fecha_pago, monto
           ORDER BY fecha_pago DESC, serie""",
        con, params=[fondo_id])
    filas_divs = "".join(
        "<tr>"
        f'<td><span class="swatch s{colores.get(d.serie, 0) % len(_PALETA)}"></span>{d.serie}</td>'
        f'<td class="muted">{d.nemo}</td>'
        f'<td>{_fecha_cl(d.fecha_limite)}</td><td>{_fecha_cl(d.fecha_pago)}</td>'
        f'<td class="num">${_num(d.monto, 4)}</td>'
        f'<td class="muted">{"Provisorio" if "PROV" in d.descripcion else "Definitivo"}</td>'
        "</tr>"
        for d in divs.itertuples())

    # mayores aportantes (quiénes están en el fondo) — sin RUT en la vista
    ap_trim, ap_top = _mayores_aportantes(con, fondo_id, top=10)
    if ap_top is not None and not ap_top.empty:
        filas_may = "".join(
            f'<tr><td class="num">{int(r.posicion)}</td>'
            f'<td>{_nombre_aportante(r.nombre)}</td>'
            f'<td class="muted">{_TIPO_AP.get(r.tipo, "Jurídica")}</td>'
            f'<td class="num">{_num(r.pct, 2)}%</td></tr>'
            for r in ap_top.itertuples())
        seccion_mayores = f"""
  <h3>Mayores aportantes <span class="muted mini">(top 10 al cierre de {ap_trim} ·
     fuente CMF · % de propiedad del fondo)</span></h3>
  <div class="tabla-scroll"><table>
    <thead><tr><th>#</th><th>Aportante</th><th>Tipo</th><th>% propiedad</th></tr></thead>
    <tbody>{filas_may}</tbody>
  </table></div>"""
    else:
        seccion_mayores = ""

    # en fondos de valor cuota mensual el último "retorno" es el del mes cerrado,
    # no un dato diario: la columna se rotula con el nombre de ese mes (ej. jun-26)
    es_mensual = ficha.get("frecuencia") == "mensual"
    etiqueta_ult_mes = f"{_MESES[ultimo.month - 1]}-{ultimo.year % 100:02d}"
    col_ult = (f'<th title="Rentabilidad del último mes cerrado (valorización mensual: '
               f'no hay dato diario)">{etiqueta_ult_mes}</th>' if es_mensual
               else "<th>Diaria</th>")

    return f"""
<section>
  <h2>{ficha['nombre']}</h2>
  <p class="muted">Último valor cuota: {_fecha_cl(ultimo.isoformat())} ·
     Fuentes: CMF (valores cuota) y Bolsa de Santiago / nuam (dividendos) · CLP nominal</p>

  <h3>Rentabilidades por serie <span class="muted mini">(ajustadas por dividendos ·
     3M/YTD/12M a fin de mes cerrado, convención factsheet; MTD y diaria al último dato)</span></h3>
  <div class="tabla-scroll"><table>
    <thead><tr><th>Serie</th><th>Nemo</th><th>Valor cuota</th>{col_ult}
    <th title="Mes calendario anterior — comparable con el factsheet mensual">{etiqueta_mes}</th>
    <th>MTD</th><th>3M</th><th>YTD</th><th>12M</th><th>Desde inicio</th></tr></thead>
    <tbody>{''.join(filas_tabla)}</tbody>
  </table></div>
{seccion_mayores}
  <h3>Corte fin de mes <span class="muted mini">(ventanas de meses calendario al
     {_fecha_cl(corte_fondo) if corte_fondo else "–"} — para certificar contra el factsheet)</span></h3>
  <div class="tabla-scroll"><table>
    <thead><tr><th>Serie</th><th>Mes ({etiqueta_mes})</th><th>3M</th><th>YTD</th>
    <th>12M</th><th>24M</th><th>36M</th></tr></thead>
    <tbody>{''.join(filas_cierre) or '<tr><td colspan="7">Sin mes cerrado aún</td></tr>'}</tbody>
  </table></div>

  <h3>Evolución índice ajustado <span class="muted mini">(base 100 al inicio de la ventana)</span></h3>
  <div class="presets">{botones}</div>
  {bloques}
  <p class="muted mini">Cada serie parte en 100 en su primer dato dentro de la ventana.
     El índice reinvierte los dividendos en el día en que el valor cuota los descuenta
     (detectado empíricamente; típicamente la fecha límite).</p>

  <h3>Dividendos pagados</h3>
  <div class="tabla-scroll"><table>
    <thead><tr><th>Serie</th><th>Nemo</th><th>Fecha límite</th><th>Fecha pago</th>
    <th>Monto por cuota</th><th>Tipo</th></tr></thead>
    <tbody>{filas_divs or '<tr><td colspan="6">Sin dividendos registrados</td></tr>'}</tbody>
  </table></div>
</section>"""


_PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seguimiento de fondos · Ameris</title>
__FAVICON__
</head>
<body>
<!-- Seguimiento de fondos — generado por fondos-tracker -->
<style>
:root {
  color-scheme: light;
  --plano: #eef3f9; --superficie: #ffffff; --tinta: #0e1b2e; --tinta-2: #3f5168;
  --muted: #7a8aa0; --grilla: #e4ebf4; --eje: #c4d1e2; --borde: rgba(20,42,84,.12);
  --pos: #0a7d33; --neg: #d0402f;
  --ameris: #1667c8; --ameris-navy: #143a75; --ameris-barra: #245ba8;
  --ameris-cian: #29b8e6; --ameris-suave: #e7f0fb;
  --s0:#2a78d6; --s1:#eb6834; --s2:#1baf7a; --s3:#eda100; --s4:#e87ba4;
  --s5:#008300; --s6:#4a3aa7; --s7:#e34948;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --plano: #0c1320; --superficie: #141d2e; --tinta: #eef3fb; --tinta-2: #b0c0d6;
    --muted: #7e8da0; --grilla: #24314a; --eje: #35455f; --borde: rgba(255,255,255,.10);
    --pos: #22b455; --neg: #e66767;
    --ameris: #5a9bf0; --ameris-navy: #17325f; --ameris-barra: #245ea6;
    --ameris-cian: #35c1ec; --ameris-suave: rgba(90,155,240,.14);
    --s0:#3987e5; --s1:#d95926; --s2:#199e70; --s3:#c98500; --s4:#d55181;
    --s5:#008300; --s6:#9085e9; --s7:#e66767;
  }
}
body { background: var(--plano); color: var(--tinta); margin: 0; padding: 24px;
       font: 400 15px/1.55 "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
       -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
main { max-width: 1060px; margin: 0 auto; }
.cabecera { display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
            background: linear-gradient(100deg, var(--ameris-navy), var(--ameris-barra));
            padding: 18px 24px; border-radius: 12px;
            border-bottom: 4px solid var(--ameris-cian); }
.cabecera h1 { color: #fff; }
.cabecera .sub { margin: 3px 0 0; color: rgba(255,255,255,.82); font-size: 0.95rem; }
.logo-chip { display: inline-flex; line-height: 0; }
.logo { height: 42px; width: auto; display: block;
        filter: brightness(0) invert(1); }
h1 { font-size: 1.5rem; font-weight: 600; margin: 0; color: var(--ameris);
     letter-spacing: -0.022em; }
h2 { font-size: 1.18rem; font-weight: 600; margin: 0 0 4px; color: var(--ameris);
     letter-spacing: -0.017em; }
h3 { font-size: 0.95rem; font-weight: 600; margin: 22px 0 10px; color: var(--ameris);
     letter-spacing: -0.01em; border-left: 3px solid var(--ameris-cian); padding-left: 9px; }
h3 .muted, h3 .mini { color: var(--muted); font-weight: 400; letter-spacing: 0; }
section { background: var(--superficie); border: 1px solid var(--borde);
          border-radius: 10px; padding: 20px 24px; margin-top: 20px; }
.muted { color: var(--muted); } .mini { font-size: 0.8rem; font-weight: 400; }
.tabla-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
th { text-align: left; color: #fff; font-weight: 600; background: var(--ameris-barra);
     padding: 7px 10px; white-space: nowrap; }
th .mini, th .muted { color: rgba(255,255,255,.72); }
td { padding: 6px 10px; border-bottom: 1px solid var(--grilla); white-space: nowrap; }
td.num { font-variant-numeric: tabular-nums; text-align: right; }
th:nth-child(n+3), td:nth-child(n+3) { text-align: right; }
.pos { color: var(--pos); } .neg { color: var(--neg); }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 3px;
          margin-right: 7px; vertical-align: baseline; }
.s0{background:var(--s0)} .s1{background:var(--s1)} .s2{background:var(--s2)}
.s3{background:var(--s3)} .s4{background:var(--s4)} .s5{background:var(--s5)}
.s6{background:var(--s6)} .s7{background:var(--s7)}
.nav-vistas { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.nav-menu { position: relative; }
.nav-cat { cursor: pointer; padding: 8px 16px; font-size: 0.9rem; font-weight: 500;
           color: var(--tinta-2); background: var(--superficie);
           border: 1px solid var(--borde); border-radius: 9px; user-select: none;
           list-style: none; display: flex; align-items: center; gap: 9px; white-space: nowrap;
           letter-spacing: -0.005em;
           transition: border-color .14s ease, background .14s ease, color .14s ease; }
.nav-cat:hover { border-color: var(--ameris); color: var(--tinta); }
.nav-cat::-webkit-details-marker { display: none; }
.nav-cat::after { content: "\\25BE"; color: var(--ameris); font-size: 0.72rem; opacity: .8;
                  transition: transform .18s ease; }
details[open] > .nav-cat::after { transform: rotate(180deg); }
.nav-menu.activa > .nav-cat { border-color: transparent; color: #fff; font-weight: 600;
           background: linear-gradient(100deg, var(--ameris-navy), var(--ameris-barra)); }
.nav-menu.activa > .nav-cat::after { color: rgba(255,255,255,.85); }
.nav-menu-items { position: absolute; top: calc(100% + 6px); left: 0; z-index: 20;
                  display: flex; flex-direction: column; gap: 1px; min-width: 210px;
                  padding: 7px; background: var(--superficie);
                  border: 1px solid var(--borde); border-radius: 12px;
                  box-shadow: 0 12px 30px rgba(20,42,84,.20); }
details[open] > .nav-menu-items { animation: menuIn .16s cubic-bezier(.2,.7,.3,1); }
@keyframes menuIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }
.nav-menu-items .tab { border: none; text-align: left; width: 100%; border-radius: 7px;
                       padding: 8px 12px; display: flex; align-items: center; gap: 9px;
                       color: var(--tinta-2); transition: background .12s ease, color .12s ease; }
.nav-menu-items .tab:hover { background: var(--ameris-suave); color: var(--tinta); }
.nav-menu-items .tab.activo { background: var(--ameris-barra); color: #fff; font-weight: 600; }
.nav-menu-items .tab.activo .swatch { box-shadow: 0 0 0 2px rgba(255,255,255,.55); }
.nav-menu-items .tab-comp { font-weight: 600; color: var(--tinta); }
.nav-menu-items .ico-comp { width: 11px; height: 11px; border-radius: 3px; flex: none;
           background: linear-gradient(135deg, var(--ameris), var(--ameris-cian)); }
.nav-sep { height: 1px; background: var(--grilla); margin: 5px 6px; }
.tab { background: none; border: 1px solid var(--borde); border-radius: 8px;
       color: var(--tinta-2); padding: 5px 14px; cursor: pointer; font: inherit;
       font-size: 0.9rem; }
.tab.activo { background: var(--ameris-barra); border-color: var(--ameris-barra);
              color: #fff; font-weight: 600; }
.vista { display: none; }
.vista.activa { display: block; }
.si { color: var(--pos); } .no { color: var(--neg); }
.presets { display: flex; gap: 6px; margin-bottom: 8px; }
.preset { background: none; border: 1px solid var(--borde); border-radius: 6px;
          color: var(--tinta-2); padding: 3px 12px; cursor: pointer; font: inherit;
          font-size: 0.85rem; }
.preset.activo { border-color: var(--ameris); color: var(--ameris); font-weight: 600; }
.grafico { display: none; position: relative; }
.grafico.activo { display: block; }
svg { width: 100%; height: auto; display: block; }
svg .grid { stroke: var(--grilla); stroke-width: 1; }
svg .tick, svg .etiqueta { fill: var(--muted); font-size: 12px;
                            font-family: system-ui, sans-serif; }
svg .etiqueta { fill: var(--tinta-2); font-weight: 600; }
svg .linea { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
polyline.s0{stroke:var(--s0)} polyline.s1{stroke:var(--s1)} polyline.s2{stroke:var(--s2)}
polyline.s3{stroke:var(--s3)} polyline.s4{stroke:var(--s4)} polyline.s5{stroke:var(--s5)}
polyline.s6{stroke:var(--s6)} polyline.s7{stroke:var(--s7)}
svg .dot { stroke: var(--superficie); stroke-width: 2; }
circle.s0{fill:var(--s0)} circle.s1{fill:var(--s1)} circle.s2{fill:var(--s2)}
circle.s3{fill:var(--s3)} circle.s4{fill:var(--s4)} circle.s5{fill:var(--s5)}
circle.s6{fill:var(--s6)} circle.s7{fill:var(--s7)}
svg .guia { stroke: var(--eje); stroke-width: 1; }
svg .crosshair { stroke: var(--eje); stroke-width: 1; }
svg .captura { fill: transparent; }
table.ficha td, table.ficha th { text-align: center; }
table.ficha td.etq { text-align: left; background: var(--ameris-suave);
                     color: var(--ameris); font-weight: 600; }
table.matriz td, table.matriz th { padding: 6px 6px; }
.tooltip { position: absolute; pointer-events: none; background: var(--superficie);
           border: 1px solid var(--borde); border-radius: 8px; padding: 8px 12px;
           font-size: 0.8rem; box-shadow: 0 2px 10px rgba(0,0,0,.12); display: none;
           white-space: nowrap; z-index: 2; }
.tooltip .fila { display: flex; align-items: center; gap: 6px; }
.tooltip .val { margin-left: auto; font-variant-numeric: tabular-nums; padding-left: 12px; }
footer { color: var(--muted); font-size: 0.8rem; margin: 18px 4px; }
</style>
<main>
<header class="cabecera">
  <span class="logo-chip">__LOGO__</span>
  <div>
    <h1>Seguimiento de fondos</h1>
    <p class="sub">Rentabilidades diarias ajustadas por dividendos</p>
  </div>
</header>
<nav class="nav-vistas">__NAV__</nav>
__CUERPO__
<footer>Generado el __GENERADO__ · fondos-tracker (CMF + Bolsa de Santiago/nuam)</footer>
</main>
<script>
var navMenus = document.querySelectorAll(".nav-menu");
document.querySelectorAll(".nav-vistas .tab").forEach(function (b) {
  b.addEventListener("click", function () {
    document.querySelectorAll(".nav-vistas .tab").forEach(function (x) { x.classList.remove("activo"); });
    document.querySelectorAll(".vista").forEach(function (x) { x.classList.remove("activa"); });
    b.classList.add("activo");
    document.getElementById(b.dataset.destino).classList.add("activa");
    // cerrar todos los menús y marcar como activa la categoría elegida
    var menu = b.closest(".nav-menu");
    navMenus.forEach(function (m) {
      m.removeAttribute("open");
      m.classList.toggle("activa", m === menu);
    });
    window.scrollTo(0, 0);
  });
});
// abrir un menú cierra los demás
navMenus.forEach(function (m) {
  m.querySelector("summary").addEventListener("click", function () {
    navMenus.forEach(function (o) { if (o !== m) o.removeAttribute("open"); });
  });
});
// clic fuera de la barra cierra los menús abiertos
document.addEventListener("click", function (e) {
  if (!e.target.closest(".nav-menu")) {
    navMenus.forEach(function (m) { m.removeAttribute("open"); });
  }
});

document.querySelectorAll(".preset").forEach(function (b) {
  b.addEventListener("click", function () {
    var seccion = b.closest("section");
    var presets = seccion.querySelectorAll(".preset");
    presets.forEach(function (x) { x.classList.remove("activo"); });
    // ocultar SOLO los gráficos que son destino de un preset (las ventanas);
    // el gráfico de AUM no es preset y debe quedar siempre visible
    presets.forEach(function (p) {
      var g = document.getElementById(p.dataset.destino);
      if (g) { g.classList.remove("activo"); }
    });
    b.classList.add("activo");
    document.getElementById(b.dataset.destino).classList.add("activo");
  });
});

var mesesCL = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
function fmt(x) { return x.toLocaleString("es-CL", {minimumFractionDigits: 1, maximumFractionDigits: 1}); }

document.querySelectorAll(".grafico").forEach(function (cont) {
  var svg = cont.querySelector("svg"), datosEl = cont.querySelector("script");
  if (!svg || !datosEl) return;
  var d = JSON.parse(datosEl.textContent);
  var tip = document.createElement("div");
  tip.className = "tooltip"; cont.appendChild(tip);
  var ch = cont.querySelector(".crosshair");
  var captura = cont.querySelector(".captura");

  captura.addEventListener("mousemove", function (ev) {
    var caja = svg.getBoundingClientRect();
    var escala = caja.width / d.ancho;
    var xSvg = (ev.clientX - caja.left) / escala;
    var frac = (xSvg - d.mi) / (d.ancho - d.mi - d.md);
    var ordinal = d.x0 + frac * (d.x1 - d.x0);
    var mejor = d.fechas[0], mejorDist = Infinity;
    d.fechas.forEach(function (f) {
      var o = Math.round(new Date(f + "T00:00:00").getTime() / 86400000) + 719163;
      var dist = Math.abs(o - ordinal);
      if (dist < mejorDist) { mejorDist = dist; mejor = f; }
    });
    var o = Math.round(new Date(mejor + "T00:00:00").getTime() / 86400000) + 719163;
    var xPos = d.mi + (o - d.x0) / (d.x1 - d.x0) * (d.ancho - d.mi - d.md);
    ch.setAttribute("x1", xPos); ch.setAttribute("x2", xPos);

    var fecha = new Date(mejor + "T00:00:00");
    var html = "<div class='fila'><strong>" + fecha.getDate() + "-" +
               mesesCL[fecha.getMonth()] + "-" + fecha.getFullYear() + "</strong></div>";
    Object.keys(d.series).forEach(function (nombre) {
      var v = d.series[nombre][mejor];
      if (v === undefined) return;
      var val = d.modo === "abs" ? fmt(v)
              : fmt(v) + " (" + (v >= 100 ? "+" : "") + fmt(v - 100) + "%)";
      html += "<div class='fila'><span class='swatch s" + d.colores[nombre] + "'></span>" +
              nombre + "<span class='val'>" + val + "</span></div>";
    });
    tip.innerHTML = html;
    tip.style.display = "block";
    var xPant = xPos * escala;
    tip.style.left = (xPant + (xPant > caja.width / 2 ? -tip.offsetWidth - 14 : 14)) + "px";
    tip.style.top = Math.max(0, (ev.clientY - caja.top) - 30) + "px";
  });
  captura.addEventListener("mouseleave", function () {
    tip.style.display = "none";
    ch.setAttribute("x1", -10); ch.setAttribute("x2", -10);
  });
});
</script>
</body>
</html>
"""
