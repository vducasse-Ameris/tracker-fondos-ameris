"""Cálculo de rentabilidades ajustadas por dividendos.

El valor cuota descuenta el dividendo típicamente en la *fecha límite*
informada por la Bolsa (verificado en Ameris FCP: 16/16 dividendos), pero el
día efectivo puede correrse unos días (verificado en Toesca Facturas: a veces
el día anterior, el siguiente o incluso la fecha de pago). Por eso el día del
descuento se detecta empíricamente: dentro de una ventana alrededor del
dividendo se busca el día cuya caída del VC calza con el monto, con la fecha
límite como respaldo. La rentabilidad de ese día se calcula como
(VC_t + dividendo) / VC_{t-1} - 1.

Cada serie comercial puede encadenar varios nombres históricos de serie en CMF
(ej: serie A = SLPA hasta oct-2024, luego A); el empalme se verificó continuo.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd


def _dia_descuento(vc: pd.Series, fecha_limite, fecha_pago,
                   monto: float) -> pd.Timestamp | None:
    """Día en que el valor cuota descuenta el dividendo.

    Busca en la ventana [fecha límite − 10, fecha pago + 10] el día cuya caída
    del VC mejor calza con el monto. Si ninguna caída se parece (tolerancia:
    ±50% del monto), usa la fecha límite — o el primer día con dato después de
    ella — como respaldo.
    """
    ref = fecha_limite if pd.notna(fecha_limite) else fecha_pago
    if pd.isna(ref):
        return None
    fin = fecha_pago if pd.notna(fecha_pago) else ref
    tramo = vc[(vc.index >= ref - pd.Timedelta(days=10)) &
               (vc.index <= fin + pd.Timedelta(days=10))]
    caidas = (tramo.shift(1) - tramo).dropna()
    if not caidas.empty:
        dia = (caidas - monto).abs().idxmin()
        if abs(caidas[dia] - monto) <= monto * 0.5:
            return dia
    posteriores = vc.index[vc.index >= ref]
    return posteriores[0] if len(posteriores) else None


def serie_ajustada(con: sqlite3.Connection, fondo_id: str, nemos: str | list[str],
                   series_cmf: list[str],
                   anomalias: list[str] | None = None) -> pd.DataFrame:
    """DataFrame indexado por fecha con valor_cuota, dividendo del día e
    índice de rentabilidad ajustado (base 1 al inicio de la serie).

    `nemos` admite varios nemos por serie (la Bolsa puede haber renombrado el
    instrumento); un mismo dividendo informado bajo más de un nemo se cuenta
    una sola vez (DISTINCT sobre fechas y monto).

    `anomalias` es una lista de fechas (YYYY-MM-DD) con eventos de capital
    verificados (rescates/aportes que re-golpean el valor cuota, no rendimiento
    orgánico); el retorno de esos días se reemplaza por el devengo típico de los
    días vecinos para que no contamine el índice acumulado.

    El índice se construye SOLO con valor cuota oficial (CMF): es NAV, no precio
    de mercado. El último precio transado en Bolsa se muestra aparte (ver
    `reporte._precio_fresco`) para no meter prima/descuento ni puntos fuera de
    ciclo en las ventanas de rentabilidad de los fondos de valorización mensual.
    """
    if isinstance(nemos, str):
        nemos = [nemos]
    marcadores = ",".join("?" * len(series_cmf))
    vc = pd.read_sql_query(
        f"""SELECT fecha, valor_cuota, patrimonio_neto, aportantes
            FROM valores_cuota
            WHERE fondo_id = ? AND serie IN ({marcadores}) AND valor_cuota > 0
            ORDER BY fecha""",
        con, params=[fondo_id, *series_cmf], parse_dates=["fecha"])
    if vc.empty:
        return vc
    # si un día aparece bajo el nombre viejo y el nuevo, prima el más reciente
    vc = vc.drop_duplicates(subset="fecha", keep="last").set_index("fecha")

    if nemos:
        marcadores_n = ",".join("?" * len(nemos))
        divs = pd.read_sql_query(
            f"""SELECT DISTINCT fecha_limite, fecha_pago, monto
                FROM dividendos WHERE nemo IN ({marcadores_n})""",
            con, params=nemos, parse_dates=["fecha_limite", "fecha_pago"])
    else:  # serie sin instrumento listado en Bolsa: sin datos de dividendos
        divs = pd.DataFrame(columns=["fecha_limite", "fecha_pago", "monto"])
    vc["dividendo"] = 0.0
    for _, d in divs.iterrows():
        dia = _dia_descuento(vc["valor_cuota"], d["fecha_limite"],
                             d["fecha_pago"], d["monto"])
        if dia is not None:
            vc.loc[dia, "dividendo"] += d["monto"]

    retorno_diario = (vc["valor_cuota"] + vc["dividendo"]) / vc["valor_cuota"].shift(1) - 1

    # neutralizar días con evento de capital verificado: el salto del VC es un
    # artefacto (no rendimiento), se reemplaza por la mediana de los ±5 vecinos
    for f_str in (anomalias or []):
        f = pd.Timestamp(f_str)
        if f in retorno_diario.index:
            loc = retorno_diario.index.get_loc(f)
            vecinos = retorno_diario.iloc[max(0, loc - 5):loc + 6].drop(f)
            if vecinos.notna().any():
                retorno_diario.loc[f] = vecinos.median()

    vc["retorno_diario"] = retorno_diario
    vc["indice"] = (1 + retorno_diario.fillna(0)).cumprod()
    return vc


def _rent(indice: pd.Series, desde: dt.date | None) -> float | None:
    """Rentabilidad acumulada desde la última fecha <= `desde` hasta el final."""
    if desde is None:
        base = indice.iloc[0]
    else:
        previos = indice[indice.index <= pd.Timestamp(desde)]
        if previos.empty:
            return None
        base = previos.iloc[-1]
    return indice.iloc[-1] / base - 1


def _rent_entre(indice: pd.Series, desde: dt.date, hasta: dt.date) -> float | None:
    """Rentabilidad entre el último dato <= `desde` y el último dato <= `hasta`."""
    base = indice[indice.index <= pd.Timestamp(desde)]
    fin = indice[indice.index <= pd.Timestamp(hasta)]
    if base.empty or fin.empty or base.index[-1] == fin.index[-1]:
        return None
    return fin.iloc[-1] / base.iloc[-1] - 1


def _fin_de_mes(anio: int, mes: int) -> dt.date:
    if mes == 12:
        return dt.date(anio, 12, 31)
    return dt.date(anio, mes + 1, 1) - dt.timedelta(days=1)


def resumen_cierre_mensual(df: pd.DataFrame) -> dict:
    """Rentabilidades al último cierre de mes, con ventanas de meses
    calendario — la convención de los factsheets mensuales. Sirve para
    certificar que el índice del tracker replica las cifras publicadas.
    """
    if df.empty or len(df) < 2:
        return {}
    indice = df["indice"]
    ultimo = indice.index[-1].date()
    siguiente = ultimo + dt.timedelta(days=1)
    corte = dt.date(siguiente.year, siguiente.month, 1) - dt.timedelta(days=1)

    def base_meses(n: int) -> dt.date:
        anio, mes0 = divmod(corte.year * 12 + corte.month - 1 - n, 12)
        return _fin_de_mes(anio, mes0 + 1)

    return {
        "corte": corte.isoformat(),
        "mes": _rent_entre(indice, base_meses(1), corte),
        "3m": _rent_entre(indice, base_meses(3), corte),
        "ytd": _rent_entre(indice, dt.date(corte.year - 1, 12, 31), corte),
        "12m": _rent_entre(indice, base_meses(12), corte),
        "24m": _rent_entre(indice, base_meses(24), corte),
        "36m": _rent_entre(indice, base_meses(36), corte),
    }


def resumen_rentabilidades(df: pd.DataFrame) -> dict:
    """Rentabilidades estándar (nominal CLP).

    Las ventanas 1M/3M/YTD/12M usan **cortes de mes calendario** al último mes
    cerrado (convención factsheet): coinciden 1:1 con «Corte fin de mes». En
    cambio `diaria` y `mtd` siguen al último dato disponible (mes en curso).
    """
    if df.empty or len(df) < 2:
        return {}
    indice = df["indice"]
    ultimo = indice.index[-1].date()
    # último mes calendario cerrado (si el último dato es fin de mes, es ese mes)
    siguiente = ultimo + dt.timedelta(days=1)
    corte = dt.date(siguiente.year, siguiente.month, 1) - dt.timedelta(days=1)

    def base_meses(n: int) -> dt.date:
        anio, mes0 = divmod(corte.year * 12 + corte.month - 1 - n, 12)
        return _fin_de_mes(anio, mes0 + 1)

    # mes calendario anterior completo (para la columna «mes» y su etiqueta)
    fin_mes_prev = dt.date(ultimo.year, ultimo.month, 1) - dt.timedelta(days=1)
    fin_mes_ante = dt.date(fin_mes_prev.year, fin_mes_prev.month, 1) - dt.timedelta(days=1)
    resultados = {
        "fecha": ultimo.isoformat(),
        "corte": corte.isoformat(),
        "valor_cuota": df["valor_cuota"].iloc[-1],
        "diaria": df["retorno_diario"].iloc[-1],
        "1m": _rent_entre(indice, base_meses(1), corte),
        "mtd": _rent(indice, dt.date(ultimo.year, ultimo.month, 1) - dt.timedelta(days=1)),
        "mes_anterior": _rent_entre(indice, fin_mes_ante, fin_mes_prev),
        "mes_anterior_fecha": fin_mes_prev.isoformat(),
        "3m": _rent_entre(indice, base_meses(3), corte),
        "ytd": _rent_entre(indice, dt.date(corte.year - 1, 12, 31), corte),
        "12m": _rent_entre(indice, base_meses(12), corte),
        "inicio": _rent(indice, None),
        "fecha_inicio": indice.index[0].date().isoformat(),
    }
    # anualizada desde inicio si la serie tiene más de un año
    dias = (indice.index[-1] - indice.index[0]).days
    if dias > 365 and resultados["inicio"] is not None:
        resultados["inicio_anual"] = (1 + resultados["inicio"]) ** (365 / dias) - 1
    return resultados
