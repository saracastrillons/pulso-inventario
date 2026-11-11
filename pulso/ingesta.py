"""Proceso diario: carga los archivos del corte, los valida y publica pronósticos y recomendaciones.

Reprocesar el mismo corte deja un solo juego de resultados: el lote se identifica por su fecha.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import BODEGAS, HMAX, Costos, fecha, indice
from .db import leer_costos, observacion_desde_db
from .motor import Resultado, recomendar, stock_seguridad
from .pronostico import Pronosticador
from .simulacion import estadisticas

COLUMNAS = {
    "ventas.csv": ["fecha", "producto_id", "bodega_id", "unidades", "stock_cierre"],
    "inventario.csv": ["producto_id", "bodega_id", "disponible"],
    "pedidos.csv": ["pedido_id", "tipo", "producto_id", "bodega_id", "origen", "cantidad_pendiente",
                    "fecha_pedido", "fecha_esperada", "estado", "referencia"],
}
MAX_RECHAZO = 0.05
SIGLA = {"compra_normal": "N", "compra_urgente": "U", "traslado": "T"}


class LoteInvalido(Exception):
    pass


def id_lote(corte: int) -> str:
    return f"L-{fecha(corte):%Y%m%d}"


def _leer(carpeta: Path, archivo: str) -> pd.DataFrame:
    ruta = carpeta / archivo
    if not ruta.exists():
        raise LoteInvalido(f"No llegó el archivo {archivo}")
    df = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    faltan = [c for c in COLUMNAS[archivo] if c not in df.columns]
    if faltan:
        raise LoteInvalido(f"{archivo}: faltan las columnas {', '.join(faltan)}")
    return df[COLUMNAS[archivo]]


def _filtrar(df: pd.DataFrame, archivo: str, reglas: list, clave: list, rechazadas: list) -> pd.DataFrame:
    """Aplica reglas fila a fila. Cada regla es (motivo, máscara de filas malas). Registra lo rechazado."""
    mala = pd.Series("", index=df.index)
    for motivo, mascara in reglas:
        mala = mala.mask((mala == "") & mascara, motivo)
    mala = mala.mask((mala == "") & df.duplicated(clave, keep="first"), "Fila duplicada")
    for i in df.index[mala != ""]:
        rechazadas.append((archivo, int(i) + 2, mala[i], json.dumps(df.loc[i].to_dict(), ensure_ascii=False)))
    return df[mala == ""].copy()


def _entero(col: pd.Series) -> pd.Series:
    return pd.to_numeric(col, errors="coerce")


def validar(carpeta: Path, corte: int, productos: set) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list]:
    """Comprueba columnas, fechas, cantidades, referencias conocidas y duplicados."""
    hoy = fecha(corte).isoformat()
    rech: list = []
    series = len(productos) * len(BODEGAS)

    def desconocida(df):
        return [("Producto desconocido", ~df.producto_id.isin(productos)),
                ("Bodega desconocida", ~df.bodega_id.isin(BODEGAS))]

    v = _leer(carpeta, "ventas.csv")
    n_v = len(v)
    u, sc = _entero(v.unidades), _entero(v.stock_cierre)
    v = _filtrar(v, "ventas.csv", desconocida(v) + [
        ("Fecha distinta a la del corte", v.fecha != hoy),
        ("Cantidad inválida", u.isna() | (u < 0) | (u % 1 != 0) | sc.isna() | (sc < 0)),
    ], ["fecha", "producto_id", "bodega_id"], rech)

    inv = _leer(carpeta, "inventario.csv")
    d = _entero(inv.disponible)
    inv = _filtrar(inv, "inventario.csv", desconocida(inv) + [("Cantidad inválida", d.isna() | (d < 0))],
                   ["producto_id", "bodega_id"], rech)

    ped = _leer(carpeta, "pedidos.csv")
    q = _entero(ped.cantidad_pendiente)
    fe = pd.to_datetime(ped.fecha_esperada, errors="coerce", format="%Y-%m-%d")
    ped = _filtrar(ped, "pedidos.csv", desconocida(ped) + [
        ("Cantidad inválida", q.isna() | (q <= 0)),
        ("Fecha esperada inválida", fe.isna()),
        ("Pedido abierto con fecha esperada vencida", (ped.estado == "abierto") & (ped.fecha_esperada <= hoy)),
    ], ["pedido_id"], rech)

    if len(v) < series or len(inv) < series:
        raise LoteInvalido(f"Datos incompletos: {len(v)} de {series} series en ventas y {len(inv)} en inventario")
    if len([r for r in rech if r[0] == "ventas.csv"]) > MAX_RECHAZO * max(n_v, 1):
        raise LoteInvalido(f"Se rechazó más del {MAX_RECHAZO:.0%} de las filas de ventas")
    for df, cols in ((v, ["unidades", "stock_cierre"]), (inv, ["disponible"]), (ped, ["cantidad_pendiente"])):
        df[cols] = df[cols].astype(float).astype(int)
    return v, inv, ped, rech


def _recepciones(con, corte: int, pid: dict, costos: Costos) -> tuple[np.ndarray, np.ndarray]:
    S = len(pid) * len(BODEGAS)
    rec, rec_alto = np.zeros((S, HMAX)), np.zeros((S, HMAX))
    for r in con.execute("SELECT * FROM pedidos WHERE estado = 'abierto'"):
        s = pid[r["producto_id"]] * len(BODEGAS) + BODEGAS.index(r["bodega_id"])
        d = max(indice(r["fecha_esperada"]) - (corte + 1), 0)
        tarde = d + (costos.retraso_alto if r["tipo"] == "compra_normal" and d >= 1 else 0)
        if d < HMAX:
            rec[s, d] += r["cantidad_pendiente"]
        if tarde < HMAX:
            rec_alto[s, tarde] += r["cantidad_pendiente"]
    return rec, rec_alto


def _costo_accion(tipo: str, q: int, costo_u: float, c: Costos) -> float:
    if tipo == "compra_normal":
        return c.costo_pedido_normal
    if tipo == "compra_urgente":
        return c.costo_fijo_urgente + c.recargo_urgente * costo_u * q
    return c.costo_fijo_traslado + c.costo_unidad_traslado * q


def filas_recomendacion(res: Resultado, s: int, corte: int, lote: str, cat: pd.DataFrame, c: Costos,
                        serie: dict, metodo: str) -> list[tuple]:
    """Convierte el resultado del motor en filas de la bandeja (una por acción) con su respaldo completo."""
    p, b = s // len(BODEGAS), s % len(BODEGAS)
    prod, bodega, otra = cat.producto_id.iloc[p], BODEGAS[b], BODEGAS[1 - b]
    H = len(res.esperar.inv_central)
    lista = lambda a: [round(float(x), 2) for x in a]
    ids = [f"R-{fecha(corte):%Y%m%d}-{prod[4:]}-{bodega}-{SIGLA[t]}" for t, _, _ in res.elegida.acciones]
    detalle = dict(
        dias=[fecha(corte + 1 + i).isoformat() for i in range(H)],
        stock=float(serie["stock"]), recepciones=lista(serie["recepciones"]),
        recepciones_alto=lista(serie["recepciones_alto"]),
        central=lista(serie["central"]), alto=lista(serie["alto"]),
        central_completo=lista(serie["central_completo"]), alto_completo=lista(serie["alto_completo"]),
        recepciones_completo=lista(serie["recepciones_completo"]),
        esperar=dict(inv_central=lista(res.esperar.inv_central), inv_alto=lista(res.esperar.inv_alto),
                     falt_central=lista(res.esperar.falt_central), falt_alto=lista(res.esperar.falt_alto)),
        plan=dict(descripcion=res.elegida.descripcion,
                  acciones=[dict(tipo=t, cantidad=q, dia_llegada=d) for t, q, d in res.elegida.acciones],
                  inv_central=lista(res.elegida.inv_central), inv_alto=lista(res.elegida.inv_alto),
                  falt_central=lista(res.elegida.falt_central), falt_alto=lista(res.elegida.falt_alto)),
        opciones=[o.resumen() for o in res.opciones],
        origen=dict(bodega=otra, stock=res.stock_origen, seguridad=res.seguridad_origen,
                    excedente=round(res.excedente_origen, 1)),
        plazo_normal=int(cat.plazo_normal.iloc[p]), metodo_pronostico=metodo, recomendaciones_del_plan=ids,
    )
    filas = []
    for rec_id, (tipo, q, llegada) in zip(ids, res.elegida.acciones):
        filas.append((
            rec_id, lote, prod, bodega, tipo, int(q), otra if tipo == "traslado" else None,
            fecha(corte + llegada).isoformat(),
            fecha(corte + res.primer_faltante).isoformat() if res.primer_faltante else None,
            int(res.solo_escenario_alto), int(res.dias_para_actuar),
            float(res.esperar.faltante_central), float(res.esperar.faltante_alto),
            float(res.esperar.margen_riesgo), float(_costo_accion(tipo, q, cat.costo_unitario.iloc[p], c)),
            float(res.impacto), "pendiente", json.dumps(detalle, ensure_ascii=False),
        ))
    return filas


def publicar(con, corte: int, lote: str, pron: Pronosticador, costos: Costos) -> int:
    """Calcula pronósticos y recomendaciones con lo que hay en la base. Debe llamarse dentro de una transacción."""
    obs, meta, cat = observacion_desde_db(con, corte)
    pid = {p: i for i, p in enumerate(cat.producto_id)}
    S = len(cat) * len(BODEGAS)
    central, alto = pron.predecir(obs)
    metodos = pron.metodo_por_serie(obs)
    _, sd = estadisticas(obs)
    seguridad = stock_seguridad(sd, meta["plazo"], costos.z_seguridad)

    stock = np.zeros(S)
    for r in con.execute("SELECT * FROM inventario"):
        s = pid[r["producto_id"]] * len(BODEGAS) + BODEGAS.index(r["bodega_id"])
        stock[s] = r["disponible"] - r["reservado"]
        con.execute("UPDATE inventario SET stock_seguridad = ? WHERE producto_id = ? AND bodega_id = ?",
                    (int(seguridad[s]), r["producto_id"], r["bodega_id"]))
    rec, rec_alto = _recepciones(con, corte, pid, costos)

    con.execute("DELETE FROM pronosticos WHERE lote_id = ?", (lote,))
    con.executemany("INSERT INTO pronosticos VALUES (?,?,?,?,?,?,?,?)", [
        (lote, cat.producto_id.iloc[s // 2], BODEGAS[s % 2], fecha(corte + h + 1).isoformat(), h + 1,
         round(float(central[h, s]), 3), round(float(alto[h, s]), 3), metodos[s])
        for s in range(S) for h in range(HMAX)])

    resultados = recomendar(stock, rec, central, alto, meta, seguridad, costos, rec_alto)
    filas = []
    for s, res in resultados.items():
        if res.requiere_decision and res.elegida.acciones:
            H = len(res.esperar.inv_central)
            serie = dict(stock=stock[s], recepciones=rec[s, :H], recepciones_alto=rec_alto[s, :H],
                         central=central[:H, s], alto=alto[:H, s], central_completo=central[:, s],
                         alto_completo=alto[:, s], recepciones_completo=rec[s])
            filas += filas_recomendacion(res, s, corte, lote, cat, costos, serie, metodos[s])
    con.execute("UPDATE recomendaciones SET estado = 'vencida' WHERE estado = 'pendiente' AND lote_id <> ?", (lote,))
    con.execute("DELETE FROM recomendaciones WHERE lote_id = ? AND estado = 'pendiente'", (lote,))
    con.executemany(f"INSERT OR IGNORE INTO recomendaciones VALUES ({','.join('?' * 18)})", filas)
    return len(filas)


def _registrar_lote(con, lote, corte, estado, cargadas, rechazadas, detalle, version, ahora) -> None:
    con.execute(
        """INSERT INTO lotes VALUES (?,?,?,?,?,?,?,?,1)
           ON CONFLICT(lote_id) DO UPDATE SET ejecutado_en = excluded.ejecutado_en, estado = excluded.estado,
               filas_cargadas = excluded.filas_cargadas, filas_rechazadas = excluded.filas_rechazadas,
               detalle = excluded.detalle, version_pronostico = excluded.version_pronostico,
               intentos = lotes.intentos + 1""",
        (lote, fecha(corte).isoformat(), ahora, estado, cargadas, len(rechazadas), detalle, version))
    con.execute("DELETE FROM filas_rechazadas WHERE lote_id = ?", (lote,))
    con.executemany("INSERT INTO filas_rechazadas VALUES (?,?,?,?,?)", [(lote, *r) for r in rechazadas])


def procesar_lote(con, carpeta: Path | str, corte: int, pron: Pronosticador, ahora: str | None = None) -> dict:
    """Valida y carga los archivos del corte. Si pasan, publica un lote nuevo; si no, deja el anterior vigente."""
    carpeta, lote = Path(carpeta), id_lote(corte)
    ahora = ahora or datetime.now().isoformat(timespec="seconds")
    hoy = fecha(corte).isoformat()
    productos = {r[0] for r in con.execute("SELECT producto_id FROM productos")}
    rech: list = []
    try:
        v, inv, ped, rech = validar(carpeta, corte, productos)
    except LoteInvalido as e:
        _registrar_lote(con, lote, corte, "fallido", 0, rech, str(e), None, ahora)
        return dict(lote_id=lote, estado="fallido", detalle=str(e), recomendaciones=0)

    costos = leer_costos(con)
    con.execute("BEGIN IMMEDIATE")
    try:
        con.executemany("INSERT OR REPLACE INTO ventas VALUES (?,?,?,?,?)", v.itertuples(index=False, name=None))
        con.execute("DELETE FROM inventario")
        con.executemany("INSERT INTO inventario VALUES (?,?,?,0,0,?)",
                        [(r.producto_id, r.bodega_id, r.disponible, hoy) for r in inv.itertuples()])
        con.execute("DELETE FROM pedidos")
        con.executemany("INSERT INTO pedidos VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [tuple(r[c] if r[c] != "" else None for c in COLUMNAS["pedidos.csv"])
                         for r in ped.to_dict("records")])
        con.execute("DELETE FROM pedidos_por_corte WHERE fecha_corte = ?", (hoy,))
        con.execute("""INSERT INTO pedidos_por_corte
                       SELECT ?, pedido_id, tipo, producto_id, bodega_id, cantidad_pendiente, fecha_pedido, fecha_esperada
                       FROM pedidos WHERE estado = 'abierto'""", (hoy,))
        # Las solicitudes que el sistema de origen ya ejecutó dejan de reservar inventario.
        con.execute("""UPDATE solicitudes SET estado = 'en_transito' WHERE estado = 'aprobada' AND solicitud_id IN
                       (SELECT referencia FROM pedidos WHERE estado = 'abierto')""")
        con.execute("""UPDATE solicitudes SET estado = 'recibida', fecha_recepcion = ?
                       WHERE estado IN ('aprobada', 'en_transito') AND solicitud_id IN
                       (SELECT referencia FROM pedidos WHERE estado = 'recibido')""", (hoy,))
        con.execute("""UPDATE inventario SET reservado = COALESCE((
                           SELECT SUM(s.cantidad) FROM solicitudes s
                           WHERE s.estado = 'aprobada' AND s.tipo = 'traslado'
                             AND s.producto_id = inventario.producto_id AND s.origen = inventario.bodega_id), 0)""")
        n = publicar(con, corte, lote, pron, costos)
        _registrar_lote(con, lote, corte, "ok", len(v) + len(inv) + len(ped), rech, None, pron.version, ahora)
        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        _registrar_lote(con, lote, corte, "fallido", 0, rech, f"Error al publicar: {e}", None, ahora)
        raise
    return dict(lote_id=lote, estado="ok", detalle=None, recomendaciones=n, rechazadas=len(rech))
