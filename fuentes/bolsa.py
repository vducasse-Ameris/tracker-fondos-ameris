"""Dividendos de cuotas de fondos de inversión desde la API de la Bolsa de Santiago (nuam).

El sitio está protegido por un anti-bot que filtra por huella TLS; `curl_cffi`
con impersonate="chrome" lo atraviesa. La API es LoopBack: POST JSON con token
CSRF obtenido de /api/Securities/csrfToken.
"""
from __future__ import annotations

import re
import time

from curl_cffi import requests as creq

_BASE = "https://www.bolsadesantiago.com"


class ClienteBolsa:
    def __init__(self) -> None:
        self._nueva_sesion()

    def _nueva_sesion(self) -> None:
        """Abre una sesión nueva y renueva el token CSRF. Se re-llama cuando el
        anti-bot corta la sesión a mitad de una corrida larga."""
        self._sesion = creq.Session(impersonate="chrome")
        token = self._sesion.get(_BASE + "/api/Securities/csrfToken",
                                 timeout=30).json()["csrf"]
        self._headers = {
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": _BASE,
            "Referer": _BASE + "/dividendos",
        }

    def _post(self, ruta: str, payload: dict) -> dict | list:
        """POST con reintentos. Tras muchas peticiones seguidas el anti-bot puede
        responder vacío/HTML (JSONDecodeError) o cortar la sesión; se reintenta
        con backoff renovando la sesión y el token CSRF."""
        ultimo = None
        for intento in range(4):
            try:
                r = self._sesion.post(_BASE + ruta, headers=self._headers,
                                      json=payload, timeout=60)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # HTTP, timeout o respuesta no-JSON
                ultimo = e
                if intento < 3:
                    time.sleep(2 * (intento + 1))  # 2s, 4s, 6s
                    try:
                        self._nueva_sesion()
                    except Exception:
                        pass
        raise ultimo

    def obtener_dividendos(self, nemo: str) -> list[dict]:
        """Historial de dividendos de un nemo. La API devuelve el historial
        completo del instrumento independiente del rango consultado."""
        datos = self._post("/api/RV_ResumenMercado/getDividendos",
                           {"fec_pagoini": "2000-01-01",
                            "fec_pagofin": "2100-12-31",
                            "nemo": nemo})
        lista = datos.get("listaResult", []) if isinstance(datos, dict) else datos
        dividendos = []
        for d in lista or []:
            desc = d.get("descrip_vc", "") or ""
            # Un "reparto equivalente en cuotas" NO es dividendo en efectivo: el
            # partícipe recibe más cuotas y el valor cuota no baja. Además el monto
            # informado son CUOTAS, no $/cuota, así que sumarlo al índice como
            # efectivo lo dispara (ej. BTG Deuda Inmob. I: +524% el 23-abr-2025).
            # Se descartan estos registros.
            if "CUOTA" in desc.upper():
                continue
            dividendos.append({
                "nemo": d["nemo"],
                "fecha_limite": d.get("fec_lim"),
                "fecha_pago": d.get("fec_pago"),
                "monto": _monto_preciso(d),
                "moneda": d.get("moneda"),
                "descripcion": desc,
            })
        return dividendos

    def listar_cfi(self) -> list[dict]:
        """Listado completo de cuotas de fondos de inversión (para buscar nemos)."""
        datos = self._post("/api/RV_ResumenMercado/getCFI", {})
        return datos.get("listaResult", datos) if isinstance(datos, dict) else datos

    def precio_transado(self, nemo: str) -> dict | None:
        """Último precio transado en Bolsa de un nemo, o None si no transó.

        Usa `getResumenPrecios`, cuya sección "montos" trae filas etiquetadas
        'Hoy'/'Ayer'/'Mes'/'Año'. Devuelve el precio de cierre de 'Hoy' solo si
        hubo monto transado (evita reportar un precio viejo como si fuera fresco).
        Para cuotas de fondos casi ilíquidas (series que no transan) devuelve None
        y el cálculo cae al valor cuota oficial de CMF.
        """
        datos = self._post("/api/RV_Instrumentos/getResumenPrecios", {"nemo": nemo})
        lista = datos.get("listaResult", []) if isinstance(datos, dict) else datos
        for d in lista or []:
            if d.get("tipo_dato") == "montos" and d.get("descripcion") == "Hoy":
                precio, monto = d.get("pre_cie"), d.get("monto") or 0
                if precio and monto > 0:
                    return {"precio": float(precio), "monto": float(monto)}
                return None
        return None

    def volumen_12m(self, nemo: str) -> float | None:
        """Monto transado en Bolsa en los últimos 12 meses de un nemo (proxy de
        liquidez secundaria), desde la fila 'Año' de `getResumenPrecios`. None si
        no responde; 0 si no hubo transacciones."""
        datos = self._post("/api/RV_Instrumentos/getResumenPrecios", {"nemo": nemo})
        lista = datos.get("listaResult", []) if isinstance(datos, dict) else datos
        for d in lista or []:
            if d.get("tipo_dato") == "montos" and d.get("descripcion") == "Año":
                return float(d.get("monto") or 0)
        return None

    def presencia_ajustada(self, fecha: str) -> dict[str, float]:
        """{nemo: % de presencia bursátil ajustada (NCG 327)} para 'YYYY-MM-DD'.

        Un solo POST trae todos los instrumentos con presencia; la respuesta
        incluye las cuotas de fondos (CFI). No expone market maker (ese dato solo
        está en el Informe Bursátil Diario). Una fecha inválida da HTTP 500.
        """
        datos = self._post("/api/RV_ResumenMercado/getPresenciaBursatil",
                           {"mercado": "AC", "bolsa": "TT", "fecha": fecha, "todas": "N"})
        lista = datos.get("listaResult", []) if isinstance(datos, dict) else datos
        out: dict[str, float] = {}
        for d in lista or []:
            try:
                out[d["nemo"]] = float(d["presen_aju"])
            except (KeyError, TypeError, ValueError):
                pass
        return out


def _monto_preciso(dividendo: dict) -> float | None:
    """El campo val_acc viene redondeado a 3 decimales; la descripción trae el
    monto completo: 'DIVIDENDO PROV. $ 14,368593033'."""
    m = re.search(r"\$\s*([\d.]+,\d+|[\d.]+)", dividendo.get("descrip_vc", ""))
    if m:
        texto = m.group(1)
        if "," in texto:  # coma decimal, puntos de miles
            return float(texto.replace(".", "").replace(",", "."))
        return float(texto)
    return dividendo.get("val_acc")
