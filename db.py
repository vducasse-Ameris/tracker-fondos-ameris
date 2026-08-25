"""Almacenamiento SQLite del tracker: valores cuota y dividendos por fondo/serie."""
from __future__ import annotations

import sqlite3
from pathlib import Path

RUTA_DB = Path(__file__).parent / "datos.db"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS valores_cuota (
    fondo_id        TEXT NOT NULL,
    fecha           TEXT NOT NULL,   -- ISO YYYY-MM-DD
    serie           TEXT NOT NULL,
    valor_cuota     REAL,
    patrimonio_neto REAL,            -- patrimonio neto de la serie
    activo_total    REAL,            -- activo total del fondo (igual en todas las series)
    aportantes      INTEGER,
    PRIMARY KEY (fondo_id, fecha, serie)
);
CREATE TABLE IF NOT EXISTS dividendos (
    fondo_id     TEXT NOT NULL,
    serie        TEXT NOT NULL,
    nemo         TEXT NOT NULL,
    fecha_limite TEXT,
    fecha_pago   TEXT NOT NULL,
    monto        REAL NOT NULL,
    moneda       TEXT,
    descripcion  TEXT,
    PRIMARY KEY (nemo, fecha_pago, monto)
);
CREATE TABLE IF NOT EXISTS aportantes_mayores (
    fondo_id   TEXT NOT NULL,
    trimestre  TEXT NOT NULL,   -- 'YYYY-MM' del cierre declarado
    posicion   INTEGER NOT NULL,
    nombre     TEXT,
    tipo       TEXT,            -- letra tipo persona CMF (A..G)
    rut        TEXT,
    pct        REAL,            -- % de propiedad
    PRIMARY KEY (fondo_id, trimestre, posicion)
);
CREATE TABLE IF NOT EXISTS presencia_bursatil (
    nemo       TEXT PRIMARY KEY,
    fecha      TEXT,            -- fecha bursátil del dato (YYYY-MM-DD)
    presen_aju REAL             -- % de presencia bursátil ajustada (NCG 327)
);
CREATE TABLE IF NOT EXISTS precio_bolsa (
    nemo   TEXT NOT NULL,
    fecha  TEXT NOT NULL,       -- fecha del cierre transado (YYYY-MM-DD)
    precio REAL NOT NULL,       -- precio cierre transado en Bolsa
    monto  REAL,                -- monto transado ese día (0 = no transó)
    PRIMARY KEY (nemo, fecha)
);
CREATE TABLE IF NOT EXISTS volumen_bolsa (
    nemo     TEXT PRIMARY KEY,
    fecha    TEXT,              -- fecha de la captura (YYYY-MM-DD)
    monto12m REAL               -- monto transado acumulado 12 meses (liquidez secundaria)
);
"""


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.executescript(_ESQUEMA)
    # migración: agregar activo_total a bases antiguas (se llena en la próxima carga)
    try:
        con.execute("ALTER TABLE valores_cuota ADD COLUMN activo_total REAL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # la columna ya existe
    return con


def guardar_valores_cuota(con: sqlite3.Connection, fondo_id: str, filas: list[dict]) -> int:
    cur = con.executemany(
        """INSERT INTO valores_cuota (fondo_id, fecha, serie, valor_cuota,
                                      patrimonio_neto, activo_total, aportantes)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(fondo_id, fecha, serie) DO UPDATE SET
               valor_cuota=excluded.valor_cuota,
               patrimonio_neto=excluded.patrimonio_neto,
               activo_total=excluded.activo_total,
               aportantes=excluded.aportantes""",
        [(fondo_id, f["fecha"], f["serie"], f["valor_cuota"],
          f["patrimonio_neto"], f.get("activo_total"), f["aportantes"]) for f in filas])
    con.commit()
    return cur.rowcount


def guardar_dividendos(con: sqlite3.Connection, fondo_id: str, serie: str,
                       dividendos: list[dict]) -> int:
    # algunos repartos vienen sin fecha de pago (solo fecha límite): se usa la
    # límite como respaldo (la PK y el cálculo la necesitan); si faltan ambas o
    # el monto, se descarta la fila.
    filas = []
    for d in dividendos:
        fecha_pago = d["fecha_pago"] or d["fecha_limite"]
        if not fecha_pago or d["monto"] is None:
            continue
        filas.append((fondo_id, serie, d["nemo"], d["fecha_limite"], fecha_pago,
                      d["monto"], d["moneda"], d["descripcion"]))
    if not filas:
        return 0
    cur = con.executemany(
        """INSERT OR REPLACE INTO dividendos
           (fondo_id, serie, nemo, fecha_limite, fecha_pago, monto, moneda, descripcion)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", filas)
    con.commit()
    return cur.rowcount


def guardar_presencia(con: sqlite3.Connection, fecha: str,
                      presencias: dict[str, float]) -> int:
    cur = con.executemany(
        """INSERT OR REPLACE INTO presencia_bursatil (nemo, fecha, presen_aju)
           VALUES (?, ?, ?)""",
        [(nemo, fecha, val) for nemo, val in presencias.items()])
    con.commit()
    return cur.rowcount


def guardar_precio_bolsa(con: sqlite3.Connection, nemo: str, fecha: str,
                         precio: float, monto: float | None) -> int:
    cur = con.execute(
        """INSERT OR REPLACE INTO precio_bolsa (nemo, fecha, precio, monto)
           VALUES (?, ?, ?, ?)""",
        (nemo, fecha, precio, monto))
    con.commit()
    return cur.rowcount


def guardar_volumen(con: sqlite3.Connection, nemo: str, fecha: str,
                    monto12m: float) -> int:
    cur = con.execute(
        """INSERT OR REPLACE INTO volumen_bolsa (nemo, fecha, monto12m)
           VALUES (?, ?, ?)""", (nemo, fecha, monto12m))
    con.commit()
    return cur.rowcount


def guardar_mayores_aportantes(con: sqlite3.Connection, fondo_id: str,
                               trimestre: str, filas: list[dict]) -> int:
    con.execute("DELETE FROM aportantes_mayores WHERE fondo_id=? AND trimestre=?",
                (fondo_id, trimestre))
    # el RUT del aportante NO se guarda (no se muestra en el reporte y la base
    # puede terminar en la nube/CI): se almacena NULL para no exponer dato personal.
    cur = con.executemany(
        """INSERT INTO aportantes_mayores
           (fondo_id, trimestre, posicion, nombre, tipo, rut, pct)
           VALUES (?, ?, ?, ?, ?, NULL, ?)""",
        [(fondo_id, trimestre, f["posicion"], f["nombre"], f["tipo"], f["pct"])
         for f in filas])
    con.commit()
    return cur.rowcount


def ultima_fecha(con: sqlite3.Connection, fondo_id: str) -> str | None:
    fila = con.execute("SELECT MAX(fecha) FROM valores_cuota WHERE fondo_id=?",
                       (fondo_id,)).fetchone()
    return fila[0] if fila else None
