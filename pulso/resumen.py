"""Resumen para la reunión de operaciones, armado solo con resultados del motor.

Cada cifra cita el registro que la respalda (identificador de la recomendación). No usa un modelo de
lenguaje: si se quisiera redactar con IA generativa, este diccionario sería su única fuente.
"""
from __future__ import annotations

import json

import pandas as pd

from .config import NOMBRE_BODEGA
from .motor import ETIQUETA


def _cop(x: float) -> str:
    return "$" + f"{x:,.0f}".replace(",", ".")


def _recs(con, lote: str) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT r.*, p.nombre FROM recomendaciones r JOIN productos p USING (producto_id)
           WHERE lote_id = ? ORDER BY impacto DESC""", con, params=(lote,))


def generar(con) -> dict:
    lotes = con.execute("SELECT * FROM lotes WHERE estado = 'ok' ORDER BY fecha_corte DESC LIMIT 2").fetchall()
    if not lotes:
        return dict(cambios=[], prioridades=[], supuestos=[])
    hoy = _recs(con, lotes[0]["lote_id"])
    ayer = _recs(con, lotes[1]["lote_id"]) if len(lotes) > 1 else hoy.iloc[0:0]
    serie = lambda df: set(zip(df.producto_id, df.bodega_id))
    nuevas, resueltas = serie(hoy) - serie(ayer), serie(ayer) - serie(hoy)
    unicas = hoy.drop_duplicates(["producto_id", "bodega_id"])

    cambios = [f"Corte {lotes[0]['fecha_corte']}: {len(hoy)} acciones sugeridas en {len(unicas)} referencias, "
               f"con {_cop(unicas.margen_riesgo.sum())} de margen en riesgo si nadie actúa."]
    if len(lotes) > 1:
        cambios.append(f"Frente al corte anterior ({lotes[1]['fecha_corte']}): {len(nuevas)} referencias nuevas en la "
                       f"bandeja y {len(resueltas)} que salieron de ella.")
        aprobadas = int((ayer.estado == "aprobada").sum())
        if len(ayer):
            cambios.append(f"Del lote anterior se aprobaron {aprobadas} de {len(ayer)} acciones.")
    recibidas = con.execute("SELECT COUNT(*) FROM solicitudes WHERE fecha_recepcion = ?", (lotes[0]["fecha_corte"],)).fetchone()[0]
    if recibidas:
        cambios.append(f"Llegaron {recibidas} solicitudes en la fecha del corte.")

    prioridades, supuestos = [], []
    for r in unicas.head(3).itertuples():
        d = json.loads(r.detalle_json)
        donde = NOMBRE_BODEGA[r.bodega_id]
        prioridades.append(
            f"{r.nombre} en {donde}: {d['plan']['descripcion'].lower()}. Sin acción quedarían {r.unidades_riesgo:.0f} "
            f"unidades sin atender ({r.unidades_riesgo_alto:.0f} con demanda alta) desde el {r.primer_faltante}. "
            f"[{', '.join(d['recomendaciones_del_plan'])}]")
        expuestas = sum(d["plan"]["falt_alto"])
        if expuestas >= 1:
            supuestos.append(f"{r.nombre} ({donde}): con demanda alta el plan aún deja {expuestas:.0f} unidades "
                             f"expuestas. [{r.rec_id}]")
        if sum(d["recepciones"]) > 0:
            supuestos.append(f"{r.nombre} ({donde}): el plan cuenta con {sum(d['recepciones']):.0f} unidades de "
                             f"recepciones ya pedidas; un retraso del proveedor lo cambiaría. [{r.rec_id}]")
        if r.accion == "traslado":
            o = d["origen"]
            supuestos.append(f"{r.nombre}: el traslado supone que {NOMBRE_BODEGA[o['bodega']]} conserva "
                             f"{o['seguridad']:.0f} unidades de seguridad; hoy puede ceder {o['excedente']:.0f}. [{r.rec_id}]")
    return dict(cambios=cambios, prioridades=prioridades, supuestos=supuestos)


def describir_accion(accion: str, cantidad: int, origen: str | None) -> str:
    texto = f"{ETIQUETA[accion]} de {cantidad}"
    return texto + (f" desde {NOMBRE_BODEGA[origen]}" if isinstance(origen, str) and origen else "")
