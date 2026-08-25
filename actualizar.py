"""Actualización diaria del tracker: trae valores cuota (CMF) y dividendos (Bolsa).

Uso:
    python actualizar.py            # incremental desde la última fecha guardada
    python actualizar.py --full     # recarga completa desde la fecha de inicio
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import db
from fuentes import bolsa, cmf

RUTA_FONDOS = Path(__file__).parent / "fondos.json"


def actualizar(recarga_completa: bool = False) -> None:
    fondos = json.loads(RUTA_FONDOS.read_text(encoding="utf-8"))
    con = db.conectar()
    hoy = dt.date.today()

    cliente_bolsa = bolsa.ClienteBolsa()

    for fondo_id, ficha in fondos.items():
        print(f"\n=== {ficha['nombre']} ===")

        # 1) valores cuota CMF
        inicio = dt.date.fromisoformat(ficha["inicio"])
        if not recarga_completa:
            ultima = db.ultima_fecha(con, fondo_id)
            if ultima:
                # re-consulta unos días hacia atrás por si CMF corrige valores
                inicio = dt.date.fromisoformat(ultima) - dt.timedelta(days=7)
        print(f"  CMF: consultando desde {inicio} hasta {hoy}...")
        filas = cmf.obtener_valores_cuota(ficha["cmf"]["rut"],
                                          ficha["cmf"]["tipoentidad"], inicio, hoy)
        n = db.guardar_valores_cuota(con, fondo_id, filas)
        print(f"  CMF: {len(filas)} filas obtenidas, {n} guardadas/actualizadas")

        # 2) dividendos Bolsa por serie (una serie puede tener más de un nemo
        #    si la Bolsa renombró el instrumento)
        for serie, cfg in ficha["series"].items():
            nemos = cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else [])
            for nemo in nemos:
                divs = cliente_bolsa.obtener_dividendos(nemo)
                if divs:
                    db.guardar_dividendos(con, fondo_id, serie, divs)
                # volumen transado 12m (liquidez secundaria)
                try:
                    vol = cliente_bolsa.volumen_12m(nemo)
                    if vol is not None:
                        db.guardar_volumen(con, nemo, hoy.isoformat(), vol)
                except Exception:
                    vol = None
                vol_txt = f" · vol12m ${vol/1e6:,.0f} MM" if vol else ""
                print(f"  Bolsa {nemo} (serie {serie}): {len(divs)} dividendos{vol_txt}")

        # 2b) precio transado en Bolsa (solo fondos con valorización mensual
        #     rezagada, ej. deuda inmobiliaria): un punto más fresco que CMF
        if ficha.get("precio_bolsa"):
            for serie, cfg in ficha["series"].items():
                for nemo in (cfg.get("nemos") or ([cfg["nemo"]] if cfg.get("nemo") else [])):
                    try:
                        pt = cliente_bolsa.precio_transado(nemo)
                    except Exception:
                        pt = None
                    if pt:
                        db.guardar_precio_bolsa(con, nemo, hoy.isoformat(),
                                                pt["precio"], pt["monto"])
                        print(f"  Bolsa precio {nemo} (serie {serie}): "
                              f"{pt['precio']:,.2f} (monto {pt['monto']:,.0f})")

        # 3) mayores aportantes (CMF pestaña 27, último trimestre declarado)
        try:
            ap = cmf.obtener_mayores_aportantes(ficha["cmf"]["rut"],
                                                ficha["cmf"]["tipoentidad"])
            if ap:
                n = db.guardar_mayores_aportantes(con, fondo_id, ap["trimestre"], ap["filas"])
                print(f"  Aportantes: {n} mayores ({ap['trimestre']}, "
                      f"total {ap['total_aportantes']})")
            else:
                print("  Aportantes: sin declaración reciente")
        except Exception as e:  # no bloquear la actualización por esta fuente
            print(f"  Aportantes: error ({type(e).__name__})")

    # 3) presencia bursátil (un POST trae todos los nemos con presencia)
    try:
        for atras in range(7):
            f = (hoy - dt.timedelta(days=atras)).isoformat()
            pres = cliente_bolsa.presencia_ajustada(f)
            if pres:
                db.guardar_presencia(con, f, pres)
                print(f"\nPresencia bursátil: {len(pres)} nemos ({f})")
                break
    except Exception as e:
        print(f"\nPresencia bursátil: error ({type(e).__name__})")

    # 4) control de frescura: última fecha de valor cuota por fondo. CMF publica
    #    con 1-2 días hábiles de rezago, así que se marca "ATRASADO" solo si el
    #    último dato tiene más de 4 días corridos (probable falla de la fuente).
    #    Los fondos de valor cuota mensual (deuda inmobiliaria) publican con
    #    rezago de semanas: su umbral de atraso es mayor (45 días).
    print("\n--- Control de frescura (última fecha de valor cuota) ---")
    atrasados = []
    for fondo_id, ficha in fondos.items():
        ultima = db.ultima_fecha(con, fondo_id)
        umbral = 45 if ficha.get("frecuencia") == "mensual" else 4
        if ultima:
            dias = (hoy - dt.date.fromisoformat(ultima)).days
            marca = "  <<< ATRASADO" if dias > umbral else ""
            if dias > umbral:
                atrasados.append(ficha["nombre_corto"])
            print(f"  {ficha['nombre_corto']:<24} {ultima}  ({dias}d){marca}")
        else:
            atrasados.append(ficha["nombre_corto"])
            print(f"  {ficha['nombre_corto']:<24} SIN DATOS  <<< ATRASADO")
    if atrasados:
        print(f"  ATENCIÓN: {len(atrasados)} fondo(s) atrasado(s): {', '.join(atrasados)}")

    # 5) reporte HTML
    import reporte
    ruta = reporte.generar(con, fondos)
    print(f"\nReporte generado: {ruta}")
    con.close()

    # copia estable en OneDrive (solo en el equipo local; en la nube/CI no aplica)
    try:
        import shutil
        compartir = ruta.parent.parent.parent / "link HTML" / "Dashboard Fondos Ameris.html"
        compartir.parent.mkdir(exist_ok=True)
        shutil.copyfile(ruta, compartir)
        print(f"Copia para compartir: {compartir}")
    except Exception as e:
        print(f"Copia OneDrive omitida ({type(e).__name__})")

    # 6) publicación web local (clon Windows). En la nube la maneja el workflow
    #    de GitHub Actions, y este paso se omite solo (falta publicar.config).
    try:
        import publicar
        publicar.publicar(ruta)
    except Exception as e:
        print(f"Publicación web: error ({type(e).__name__})")

    if "--sin-abrir" not in sys.argv:
        import os
        os.startfile(ruta)  # abre el reporte en el navegador


if __name__ == "__main__":
    actualizar(recarga_completa="--full" in sys.argv)
