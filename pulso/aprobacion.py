"""Aprobar, ajustar o rechazar una recomendación. Cada aprobación genera una solicitud trazable.

La aprobación ocurre en una sola transacción: vuelve a comprobar el saldo de la bodega de origen,
reserva las unidades, crea la solicitud y guarda la auditoría. Repetirla devuelve la misma solicitud.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import indice

PREFIJO = {"traslado": "TR", "compra_normal": "OC", "compra_urgente": "OU"}


class SaldoInsuficiente(Exception):
    """Otra aprobación consumió las unidades: hay que recalcular la recomendación."""


class RecomendacionNoVigente(Exception):
    pass


def _auditar(con, rec, decision, cantidad, motivo, usuario, extra, ahora) -> None:
    version = con.execute("SELECT version_pronostico FROM lotes WHERE lote_id = ?", (rec["lote_id"],)).fetchone()
    datos = json.loads(rec["detalle_json"])
    datos.update(extra)
    con.execute(
        """INSERT INTO decisiones (rec_id, decision, cantidad_sugerida, cantidad_final, motivo, usuario, lote_id,
                                   version_pronostico, datos_json, creada_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (rec["rec_id"], decision, rec["cantidad"], cantidad, motivo, usuario, rec["lote_id"],
         version["version_pronostico"] if version else None, json.dumps(datos, ensure_ascii=False), ahora))


def aprobar(con, rec_id: str, cantidad: int | None = None, usuario: str = "analista",
            ahora: str | None = None) -> dict:
    """Aprueba una recomendación (con la cantidad sugerida u otra) y devuelve la solicitud generada."""
    ahora = ahora or datetime.now().isoformat(timespec="seconds")
    con.execute("BEGIN IMMEDIATE")
    try:
        existente = con.execute("SELECT * FROM solicitudes WHERE rec_id = ?", (rec_id,)).fetchone()
        if existente:                      # doble clic o reintento tras un error: misma solicitud
            con.execute("COMMIT")
            return dict(existente, repetida=True)
        rec = con.execute("SELECT * FROM recomendaciones WHERE rec_id = ?", (rec_id,)).fetchone()
        if rec is None:
            raise RecomendacionNoVigente(f"No existe la recomendación {rec_id}")
        if rec["estado"] != "pendiente":
            raise RecomendacionNoVigente(f"La recomendación {rec_id} está {rec['estado']}: revisa la bandeja actual")
        q = int(cantidad if cantidad is not None else rec["cantidad"])
        if q <= 0:
            raise ValueError("La cantidad debe ser mayor que cero")

        extra = {}
        if rec["accion"] == "traslado":
            inv = con.execute("SELECT * FROM inventario WHERE producto_id = ? AND bodega_id = ?",
                              (rec["producto_id"], rec["origen"])).fetchone()
            libre = inv["disponible"] - inv["reservado"]
            if libre - q < inv["stock_seguridad"]:
                raise SaldoInsuficiente(
                    f"{rec['origen']} tiene {libre} unidades libres de {rec['producto_id']} y debe conservar "
                    f"{inv['stock_seguridad']} de seguridad: no alcanza para trasladar {q}. Recalcula la recomendación.")
            con.execute("UPDATE inventario SET reservado = reservado + ? WHERE producto_id = ? AND bodega_id = ?",
                        (q, rec["producto_id"], rec["origen"]))
            extra["origen_al_aprobar"] = dict(disponible=inv["disponible"], reservado_antes=inv["reservado"],
                                              stock_seguridad=inv["stock_seguridad"])

        prefijo = f"{PREFIJO[rec['accion']]}-{rec['lote_id'][2:]}-"
        n = con.execute("SELECT COUNT(*) FROM solicitudes WHERE solicitud_id LIKE ?", (prefijo + "%",)).fetchone()[0]
        sol = dict(solicitud_id=f"{prefijo}{n + 1:03d}", rec_id=rec_id, tipo=rec["accion"],
                   producto_id=rec["producto_id"], origen=rec["origen"], destino=rec["bodega_id"], cantidad=q,
                   fecha_esperada=rec["fecha_llegada"], estado="aprobada", creada_en=ahora, fecha_recepcion=None)
        con.execute("INSERT INTO solicitudes VALUES (:solicitud_id,:rec_id,:tipo,:producto_id,:origen,:destino,"
                    ":cantidad,:fecha_esperada,:estado,:creada_en,:fecha_recepcion)", sol)
        con.execute("UPDATE recomendaciones SET estado = 'aprobada' WHERE rec_id = ?", (rec_id,))
        extra["solicitud_id"] = sol["solicitud_id"]
        _auditar(con, rec, "aprobada" if q == rec["cantidad"] else "aprobada con ajuste", q, None, usuario, extra, ahora)
        con.execute("COMMIT")
        return dict(sol, repetida=False)
    except Exception:
        con.execute("ROLLBACK")
        raise


def rechazar(con, rec_id: str, motivo: str, usuario: str = "analista", ahora: str | None = None) -> None:
    if not motivo or not motivo.strip():
        raise ValueError("Para rechazar hay que indicar un motivo")
    ahora = ahora or datetime.now().isoformat(timespec="seconds")
    con.execute("BEGIN IMMEDIATE")
    try:
        rec = con.execute("SELECT * FROM recomendaciones WHERE rec_id = ?", (rec_id,)).fetchone()
        if rec is None or rec["estado"] != "pendiente":
            raise RecomendacionNoVigente(f"La recomendación {rec_id} ya no está pendiente")
        con.execute("UPDATE recomendaciones SET estado = 'rechazada' WHERE rec_id = ?", (rec_id,))
        _auditar(con, rec, "rechazada", 0, motivo.strip(), usuario, {}, ahora)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def exportar_solicitudes(con, carpeta: Path | str) -> Path:
    """Escribe todas las solicitudes en un CSV que otra persona o sistema puede revisar y continuar."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "solicitudes.csv"
    pd.read_sql("SELECT * FROM solicitudes ORDER BY creada_en, solicitud_id", con).to_csv(ruta, index=False)
    return ruta
