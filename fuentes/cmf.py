"""Valores cuota diarios de fondos de inversión desde la ficha pública de CMF.

La ficha de cada fondo (pestaña "Valores Cuota") es un formulario POST con rango
de fechas que devuelve una tabla HTML con una fila por (fecha, serie).
"""
from __future__ import annotations

import datetime as dt

import requests
from bs4 import BeautifulSoup

_URL = ("https://www.cmfchile.cl/institucional/mercados/entidad.php"
        "?mercado=V&rut={rut}&tipoentidad={tipoentidad}&vig=VI&control=svs&pestania=7")

# ficha de aportantes (12 mayores + total) — se consulta por POST (mm/aa en el body)
_URL_APORTANTES = ("https://www.cmfchile.cl/institucional/mercados/entidad.php"
                   "?rut={rut}&mercado=V&tipoentidad={tipoentidad}&vig=VI&control=svs&pestania=27")

# códigos "Tipo persona" de CMF
_TIPO_PERSONA = {"A": "Natural", "B": "Natural (extr.)", "C": "Jurídica",
                 "D": "Estado", "E": "Jurídica", "F": "Jurídica (extr.)",
                 "G": "Inst./Fondo"}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# columnas esperadas en la tabla CMF, en orden
_COLUMNAS = ["fecha", "serie", "moneda", "valor_libro", "valor_economico",
             "patrimonio_neto", "activo_total", "aportantes", "aportantes_inst", "agencia"]


def _numero(texto: str) -> float | None:
    """'1375,0190' -> 1375.019 ; '30.775.492.090' -> 30775492090.0"""
    texto = texto.strip()
    if not texto or texto in {"-", "S/I"}:
        return None
    return float(texto.replace(".", "").replace(",", "."))


def obtener_valores_cuota(rut: str, tipoentidad: str,
                          desde: dt.date, hasta: dt.date) -> list[dict]:
    """Consulta un rango de fechas y devuelve una fila por (fecha, serie)."""
    url = _URL.format(rut=rut, tipoentidad=tipoentidad)
    sesion = requests.Session()
    sesion.headers["User-Agent"] = _UA
    sesion.get(url, timeout=30)  # cookies de sesión

    filas: list[dict] = []
    # la consulta se hace por tramos anuales para no exigir demasiado al formulario
    tramo_ini = desde
    while tramo_ini <= hasta:
        tramo_fin = min(dt.date(tramo_ini.year, 12, 31), hasta)
        datos = {
            "dia1": f"{tramo_ini.day:02d}", "mes1": f"{tramo_ini.month:02d}",
            "anio1": str(tramo_ini.year),
            "dia2": f"{tramo_fin.day:02d}", "mes2": f"{tramo_fin.month:02d}",
            "anio2": str(tramo_fin.year),
            "sub_consulta_fi": "Consultar", "enviado": "1",
        }
        respuesta = sesion.post(url, data=datos, timeout=120)
        respuesta.raise_for_status()
        filas.extend(_parsear_tabla(respuesta.text))
        tramo_ini = dt.date(tramo_ini.year + 1, 1, 1)
    return filas


def _pct_aportante(texto: str) -> float | None:
    """'29,9664' -> 29.9664 ; '.59' -> 0.59 (los <1% vienen sin cero inicial)."""
    texto = texto.strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _trimestres_recientes(hoy: dt.date, n: int = 8) -> list[tuple[str, str]]:
    """Últimos n cierres de trimestre (mm, aaaa) del más nuevo al más viejo."""
    q = (hoy.month - 1) // 3          # 0..3
    y = hoy.year
    salida = []
    for _ in range(n):
        salida.append((f"{(q + 1) * 3:02d}", str(y)))
        q -= 1
        if q < 0:
            q, y = 3, y - 1
    return salida


def obtener_mayores_aportantes(rut: str, tipoentidad: str,
                               hoy: dt.date | None = None) -> dict | None:
    """Los mayores aportantes del último trimestre declarado en CMF (pestaña 27).

    La página ignora el período por querystring; hay que enviarlo por POST
    (mm/aa en el cuerpo). Devuelve dict con `trimestre` ('YYYY-MM'),
    `total_aportantes` y `filas` [{posicion, nombre, tipo, rut, pct}], o None
    si no hay ninguna declaración reciente.
    """
    hoy = hoy or dt.date.today()
    url = _URL_APORTANTES.format(rut=rut, tipoentidad=tipoentidad)
    sesion = requests.Session()
    sesion.headers["User-Agent"] = _UA
    for mm, aa in _trimestres_recientes(hoy):
        r = sesion.post(url, data={"rut": rut, "mm": mm, "aa": aa}, timeout=90)
        r.raise_for_status()
        total, filas = _parsear_aportantes(r.text)
        if total and filas:
            return {"trimestre": f"{aa}-{mm}", "total_aportantes": total, "filas": filas}
    return None


def _parsear_aportantes(html: str) -> tuple[int | None, list[dict]]:
    import re
    soup = BeautifulSoup(html, "html.parser")
    total = None
    filas: list[dict] = []
    for tabla in soup.find_all("table"):
        texto = tabla.get_text(" ", strip=True)
        m = re.search(r"TOTAL APORTANTES\s+([\d\.]+)", texto)
        if m:
            total = int(m.group(1).replace(".", ""))
        if "mayores aportantes" in texto.lower():
            for tr in tabla.find_all("tr")[1:]:
                celdas = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(celdas) < 4 or "No existen datos" in " ".join(celdas):
                    continue
                nombre, tipo, rut_ap, pct = celdas[-4:]
                filas.append({
                    "posicion": len(filas) + 1,
                    "nombre": nombre,
                    "tipo": tipo.strip().upper()[:1],
                    "rut": rut_ap.strip(),
                    "pct": _pct_aportante(pct),
                })
    return total, filas


def _parsear_tabla(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    filas: list[dict] = []
    for tabla in soup.find_all("table"):
        encabezado = [c.get_text(strip=True) for c in tabla.find("tr").find_all(["th", "td"])]
        if not encabezado or encabezado[0] != "Fecha":
            continue
        for tr in tabla.find_all("tr")[1:]:
            celdas = [c.get_text(strip=True) for c in tr.find_all("td")]
            if len(celdas) < len(_COLUMNAS) or celdas[0] == "Sin Información":
                continue
            fila = dict(zip(_COLUMNAS, celdas))
            filas.append({
                "fecha": dt.datetime.strptime(fila["fecha"], "%d/%m/%Y").date().isoformat(),
                "serie": fila["serie"],
                "moneda": fila["moneda"],
                "valor_cuota": _numero(fila["valor_libro"]),
                "valor_economico": _numero(fila["valor_economico"]),
                "patrimonio_neto": _numero(fila["patrimonio_neto"]),
                "activo_total": _numero(fila["activo_total"]),
                "aportantes": _numero(fila["aportantes"]),
            })
    return filas
