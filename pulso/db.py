"""Esquema SQLite y utilidades de acceso."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .config import BODEGAS, HMAX, NOMBRE_BODEGA, Costos, fecha, indice
from .mundo import SIN_ANUNCIO, Mundo, Observacion, meta_series

ESQUEMA = """
CREATE TABLE IF NOT EXISTS productos (
    producto_id TEXT PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT NOT NULL, rotacion TEXT NOT NULL,
    costo_unitario REAL NOT NULL, margen_unitario REAL NOT NULL, precio REAL NOT NULL,
    plazo_normal INTEGER NOT NULL, minimo_compra INTEGER NOT NULL, multiplo INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bodegas (bodega_id TEXT PRIMARY KEY, ciudad TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rutas (
    origen TEXT NOT NULL, destino TEXT NOT NULL, plazo_dias INTEGER NOT NULL, costo_fijo REAL NOT NULL,
    costo_unidad REAL NOT NULL, capacidad_dia INTEGER NOT NULL, PRIMARY KEY (origen, destino));
CREATE TABLE IF NOT EXISTS promociones (
    producto_id TEXT NOT NULL, bodega_id TEXT NOT NULL, fecha_inicio TEXT NOT NULL, fecha_fin TEXT NOT NULL,
    descuento REAL NOT NULL, fecha_anuncio TEXT NOT NULL, PRIMARY KEY (producto_id, bodega_id, fecha_inicio));
CREATE TABLE IF NOT EXISTS ventas (
    fecha TEXT NOT NULL, producto_id TEXT NOT NULL, bodega_id TEXT NOT NULL,
    unidades INTEGER NOT NULL CHECK (unidades >= 0), stock_cierre INTEGER NOT NULL CHECK (stock_cierre >= 0),
    PRIMARY KEY (fecha, producto_id, bodega_id));
CREATE TABLE IF NOT EXISTS inventario (
    producto_id TEXT NOT NULL, bodega_id TEXT NOT NULL, disponible INTEGER NOT NULL,
    reservado INTEGER NOT NULL DEFAULT 0 CHECK (reservado >= 0), stock_seguridad INTEGER NOT NULL DEFAULT 0,
    fecha_corte TEXT NOT NULL, PRIMARY KEY (producto_id, bodega_id));
CREATE TABLE IF NOT EXISTS pedidos (
    pedido_id TEXT PRIMARY KEY, tipo TEXT NOT NULL, producto_id TEXT NOT NULL, bodega_id TEXT NOT NULL,
    origen TEXT, cantidad_pendiente INTEGER NOT NULL, fecha_pedido TEXT NOT NULL, fecha_esperada TEXT NOT NULL,
    estado TEXT NOT NULL, referencia TEXT);
-- Fecha esperada tal como se conocía en cada corte: evita usar hacia atrás un retraso que nadie conocía.
CREATE TABLE IF NOT EXISTS pedidos_por_corte (
    fecha_corte TEXT NOT NULL, pedido_id TEXT NOT NULL, tipo TEXT NOT NULL, producto_id TEXT NOT NULL,
    bodega_id TEXT NOT NULL, cantidad_pendiente INTEGER NOT NULL, fecha_pedido TEXT NOT NULL,
    fecha_esperada TEXT NOT NULL, PRIMARY KEY (fecha_corte, pedido_id));
CREATE TABLE IF NOT EXISTS lotes (
    lote_id TEXT PRIMARY KEY, fecha_corte TEXT NOT NULL, ejecutado_en TEXT NOT NULL, estado TEXT NOT NULL,
    filas_cargadas INTEGER NOT NULL DEFAULT 0, filas_rechazadas INTEGER NOT NULL DEFAULT 0,
    detalle TEXT, version_pronostico TEXT, intentos INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS filas_rechazadas (
    lote_id TEXT NOT NULL, archivo TEXT NOT NULL, fila INTEGER, motivo TEXT NOT NULL, contenido TEXT);
CREATE TABLE IF NOT EXISTS pronosticos (
    lote_id TEXT NOT NULL, producto_id TEXT NOT NULL, bodega_id TEXT NOT NULL, fecha TEXT NOT NULL,
    horizonte INTEGER NOT NULL, central REAL NOT NULL, alto REAL NOT NULL, metodo TEXT NOT NULL,
    PRIMARY KEY (lote_id, producto_id, bodega_id, fecha));
CREATE TABLE IF NOT EXISTS recomendaciones (
    rec_id TEXT PRIMARY KEY, lote_id TEXT NOT NULL, producto_id TEXT NOT NULL, bodega_id TEXT NOT NULL,
    accion TEXT NOT NULL, cantidad INTEGER NOT NULL, origen TEXT, fecha_llegada TEXT NOT NULL,
    primer_faltante TEXT, solo_escenario_alto INTEGER NOT NULL DEFAULT 0, dias_para_actuar INTEGER NOT NULL,
    unidades_riesgo REAL NOT NULL, unidades_riesgo_alto REAL NOT NULL, margen_riesgo REAL NOT NULL,
    costo_accion REAL NOT NULL, impacto REAL NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente',
    detalle_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solicitudes (
    solicitud_id TEXT PRIMARY KEY, rec_id TEXT NOT NULL UNIQUE REFERENCES recomendaciones(rec_id),
    tipo TEXT NOT NULL, producto_id TEXT NOT NULL, origen TEXT, destino TEXT NOT NULL,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0), fecha_esperada TEXT NOT NULL, estado TEXT NOT NULL,
    creada_en TEXT NOT NULL, fecha_recepcion TEXT);
CREATE TABLE IF NOT EXISTS decisiones (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT, rec_id TEXT NOT NULL, decision TEXT NOT NULL,
    cantidad_sugerida INTEGER, cantidad_final INTEGER, motivo TEXT, usuario TEXT NOT NULL,
    lote_id TEXT NOT NULL, version_pronostico TEXT, datos_json TEXT NOT NULL, creada_en TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS parametros (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_recs_lote ON recomendaciones (lote_id, estado);
CREATE INDEX IF NOT EXISTS ix_ventas_serie ON ventas (producto_id, bodega_id, fecha);
"""


def conectar(ruta: Path | str) -> sqlite3.Connection:
    """Conexión en modo autocommit: las transacciones se abren de forma explícita con BEGIN IMMEDIATE."""
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), isolation_level=None, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def crear_esquema(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA)


def guardar_parametro(con, clave: str, valor) -> None:
    con.execute("INSERT OR REPLACE INTO parametros VALUES (?, ?)", (clave, json.dumps(valor)))


def leer_parametro(con, clave: str, defecto=None):
    f = con.execute("SELECT valor FROM parametros WHERE clave = ?", (clave,)).fetchone()
    return json.loads(f["valor"]) if f else defecto


def leer_costos(con) -> Costos:
    return Costos(**(leer_parametro(con, "costos") or {}))


def guardar_tabla(con, nombre: str, df: pd.DataFrame) -> None:
    """Reemplaza una tabla de resultados (evaluación, impacto)."""
    df.to_sql(nombre, con, if_exists="replace", index=False)


def cargar_catalogo(con, cat: pd.DataFrame, promos: pd.DataFrame, costos: Costos) -> None:
    cols = ["producto_id", "nombre", "categoria", "rotacion", "costo_unitario", "margen_unitario", "precio",
            "plazo_normal", "minimo_compra", "multiplo"]
    con.execute("BEGIN IMMEDIATE")
    con.executemany(f"INSERT OR REPLACE INTO productos VALUES ({','.join('?' * len(cols))})",
                    cat[cols].itertuples(index=False, name=None))
    con.executemany("INSERT OR REPLACE INTO bodegas VALUES (?, ?)", list(NOMBRE_BODEGA.items()))
    for o, d in (("BOG", "MED"), ("MED", "BOG")):
        con.execute("INSERT OR REPLACE INTO rutas VALUES (?,?,?,?,?,?)",
                    (o, d, costos.plazo_traslado, costos.costo_fijo_traslado, costos.costo_unidad_traslado,
                     costos.capacidad_ruta))
    con.executemany(
        "INSERT OR REPLACE INTO promociones VALUES (?,?,?,?,?,?)",
        [(cat.producto_id.iloc[int(r.p)], BODEGAS[int(r.b)], fecha(r.inicio).isoformat(), fecha(r.fin).isoformat(),
          float(r.descuento), fecha(r.anuncio).isoformat()) for r in promos.itertuples()])
    guardar_parametro(con, "costos", costos.dict())
    con.execute("COMMIT")


def cargar_historia(con, mundo: Mundo, hasta: int) -> None:
    """Carga masiva de la historia (días 0..hasta-1): ventas y pedidos tal como se conocían en cada corte."""
    ids = mundo.cat.producto_id.values
    ventas = [(fecha(t).isoformat(), ids[p], BODEGAS[b], int(mundo.ventas[t, p, b]), int(mundo.cierre[t, p, b]))
              for t in range(hasta) for p in range(mundo.P) for b in range(mundo.B)]
    cortes = [(fecha(t).isoformat(), f"P{i:06d}", tipo, ids[p], BODEGAS[b], int(q), fecha(dp).isoformat(),
               fecha(eta).isoformat())
              for (t, i, tipo, p, b, q, dp, eta) in mundo.instantaneas if t < hasta]
    con.execute("BEGIN IMMEDIATE")
    con.executemany("INSERT OR REPLACE INTO ventas VALUES (?,?,?,?,?)", ventas)
    con.executemany("INSERT OR REPLACE INTO pedidos_por_corte VALUES (?,?,?,?,?,?,?,?)", cortes)
    con.execute("COMMIT")


def catalogo(con) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM productos ORDER BY producto_id", con)


def observacion_desde_db(con, corte: int) -> tuple[Observacion, dict, pd.DataFrame]:
    """Reconstruye lo que el analista conoce al cierre del día `corte`, solo con tablas SQL."""
    cat = catalogo(con)
    meta = meta_series(cat)
    S, B = len(cat) * len(BODEGAS), len(BODEGAS)
    pid = {p: i for i, p in enumerate(cat.producto_id)}
    bid = {b: i for i, b in enumerate(BODEGAS)}
    v = pd.read_sql("SELECT * FROM ventas WHERE fecha <= ?", con, params=(fecha(corte).isoformat(),))
    dias = pd.to_datetime(v.fecha).dt.date.map(indice).values
    serie = v.producto_id.map(pid).values * B + v.bodega_id.map(bid).values
    ventas, cierre = np.zeros((corte + 1, S)), np.zeros((corte + 1, S))
    ventas[dias, serie], cierre[dias, serie] = v.unidades.values, v.stock_cierre.values
    T = corte + HMAX + 2
    promo, anuncio = np.zeros((T, S)), np.full((T, S), SIN_ANUNCIO, dtype=np.int64)
    for r in con.execute("SELECT * FROM promociones"):
        s = pid[r["producto_id"]] * B + bid[r["bodega_id"]]
        i0, i1 = indice(r["fecha_inicio"]), min(indice(r["fecha_fin"]), T - 1)
        if i0 < T:
            promo[i0:i1 + 1, s], anuncio[i0:i1 + 1, s] = r["descuento"], indice(r["fecha_anuncio"])
    obs = Observacion(ventas, cierre, promo, anuncio, meta["prod"], meta["bod"], meta["categ"], meta["rot"])
    return obs, meta, cat


def ultimo_lote(con, solo_ok: bool = False):
    q = "SELECT * FROM lotes" + (" WHERE estado = 'ok'" if solo_ok else "") + " ORDER BY fecha_corte DESC, ejecutado_en DESC LIMIT 1"
    return con.execute(q).fetchone()
