"""Pantalla 3. Simulador: cambia un supuesto y mira si cambia la recomendación."""
from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import streamlit as st

import comun
from comun import COLOR_OPCION, bodega, cop
from pulso import guia
from pulso.db import leer_costos
from pulso.motor import ETIQUETA, Serie, decidir, horizonte_de

con = comun.conexion()
ok, _ = comun.estado_carga(con)
costos = leer_costos(con)
st.title("Simulador de supuestos")
st.caption("Mueve el plazo del proveedor o la intensidad de la demanda y observa en qué punto comprar deja de cubrir "
           "el faltante y otra opción pasa a ser la mejor. Nada de lo que hagas aquí modifica la bandeja.")

recs = comun.recomendaciones(con, ok["lote_id"]).drop_duplicates(["producto_id", "bodega_id"]) if ok else pd.DataFrame()
casos = {guia.NOMBRE: None}
casos.update({f"{r.nombre} · {bodega(r.bodega_id)}": r for r in recs.itertuples()})
actual = st.session_state.get("rec_id")
inicial = next((i for i, r in enumerate(casos.values()) if r is not None and r.rec_id == actual), 0)
nombre = st.selectbox("Caso", list(casos), index=inicial)
rec = casos[nombre]

if rec is None:
    plazo_base, exc_base = guia.PLAZO, 50
else:
    d = json.loads(rec.detalle_json)
    prod = con.execute("SELECT * FROM productos WHERE producto_id = ?", (rec.producto_id,)).fetchone()
    plazo_base, exc_base = d["plazo_normal"], int(d["origen"]["excedente"])

a, b, c, e = st.columns(4)
plazo = a.slider("Plazo del proveedor (días)", 3, 9, plazo_base, key=f"plazo-{nombre}")
intensidad = b.slider("Intensidad de la demanda", 50, 200, 100, 5, format="%d %%", key=f"int-{nombre}",
                      help="100 % es el pronóstico actual. Súbelo para simular una promoción más fuerte.") / 100
excedente = c.number_input("Unidades que puede ceder la otra bodega", 0, 2000, exc_base, key=f"exc-{nombre}")
recargo = e.slider("Sobrecosto de la compra urgente", 5, 40, int(costos.recargo_urgente * 100), format="%d %%",
                   key=f"rec-{nombre}") / 100
costos = replace(costos, recargo_urgente=recargo)


def serie(L: int) -> Serie:
    if rec is None:
        return guia.caso_guia(L, intensidad, excedente)
    H = horizonte_de(L)
    return Serie(stock=d["stock"], recepciones=np.array(d["recepciones_completo"][:H]),
                 central=np.array(d["central_completo"][:H]) * intensidad, alto=np.array(d["alto_completo"][:H]) * intensidad,
                 costo=prod["costo_unitario"], margen=prod["margen_unitario"], plazo_normal=L,
                 minimo=prod["minimo_compra"], multiplo=prod["multiplo"], excedente_origen=excedente,
                 capacidad=costos.capacidad_ruta)


res = decidir(serie(plazo), costos)
if not res.requiere_decision:
    st.success(f"Con estos supuestos no se proyecta ningún faltante en los próximos {horizonte_de(plazo)} días: "
               "la recomendación es esperar.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Mejor opción", res.elegida.descripcion)
m2.metric("Primer día con faltante", f"Día {res.primer_faltante}", "solo con demanda alta" if res.solo_escenario_alto else None, delta_color="off")
m3.metric("Costo total esperado", cop(res.elegida.costo_total), f"{cop(res.impacto)} menos que esperar", delta_color="off")

izq, der = st.columns(2)
with izq:
    st.markdown("**Inventario proyectado con cada opción** (escenario central)")
    H = len(res.esperar.inv_central)
    dias = [f"Día {i + 1}" for i in range(H)]
    fig = comun.figura(360, "Unidades al cierre")
    for o in res.opciones:
        if o.inv_central is None or o.accion == "combinada":
            continue
        es_mejor = o.accion in {t for t, _, _ in res.elegida.acciones} or o is res.elegida
        fig.add_scatter(x=dias, y=o.inv_central, name=o.descripcion, mode="lines+markers",
                        line=dict(color=COLOR_OPCION[o.accion], width=3 if es_mejor else 2,
                                  dash="solid" if o.viable else "dot"),
                        marker=dict(size=8))
    fig.add_vline(x=res.primer_faltante - 1, line=dict(color=comun.CRITICO, width=1, dash="dash"))
    fig.add_annotation(x=res.primer_faltante - 1, y=1, yref="paper", text="primer faltante", showarrow=False,
                       xanchor="left", xshift=4, font=dict(color=comun.TINTA_2))
    st.plotly_chart(fig, width="stretch")
    st.caption("Línea punteada: la opción no cubre el faltante (llega tarde) o incumple una restricción.")

with der:
    st.markdown("**Costo total esperado según el plazo del proveedor**")
    plazos = list(range(3, 10))
    barrido = {L: decidir(serie(L), costos) for L in plazos}
    fig2 = comun.figura(360, "COP")
    for accion in ("esperar", "compra_normal", "compra_urgente", "traslado"):
        y = []
        for L in plazos:
            o = next((o for o in barrido[L].opciones if o.accion == accion), None)
            y.append(round(o.costo_total) if o is not None and o.inv_central is not None else None)
        if any(v is not None for v in y):
            fig2.add_scatter(x=plazos, y=y, name=ETIQUETA[accion], mode="lines+markers", connectgaps=False,
                             line=dict(color=COLOR_OPCION[accion], width=2), marker=dict(size=8))
    fig2.add_vline(x=plazo, line=dict(color=comun.EJE, width=1, dash="dash"))
    fig2.update_xaxes(title="Plazo del proveedor (días)", dtick=1, title_font=dict(color=comun.GRIS))
    fig2.update_yaxes(tickprefix="$", separatethousands=True)
    st.plotly_chart(fig2, width="stretch")
    mejores = pd.DataFrame({"Plazo (días)": plazos,
                            "Mejor opción": [barrido[L].elegida.descripcion if barrido[L].requiere_decision else "Esperar"
                                             for L in plazos]}).set_index("Plazo (días)").T
    st.dataframe(mejores, width="stretch")

st.markdown("**Alternativas con los supuestos actuales**")
filas = pd.DataFrame([o.resumen() for o in res.opciones])
tabla = pd.DataFrame({
    "Opción": filas.descripcion,
    "Resultado": ["Recomendada" if o is res.elegida or (len(res.elegida.acciones) == 1 and o.acciones == res.elegida.acciones)
                  else ("Descartada" if not o.viable else "Viable") for o in res.opciones],
    "Llega": [f"Día {int(x)}" if pd.notna(x) else "—" for x in filas.llegada],
    "Costo logístico": filas.costo_logistico, "Margen en riesgo": filas.margen_riesgo,
    "Costo total esperado": filas.costo_total, "Sin atender (central)": filas.faltante_central,
    "Sin atender (alta)": filas.faltante_alto, "Nota": filas.motivo,
})
vacias = [o.inv_central is None for o in res.opciones]
tabla.loc[vacias, ["Costo logístico", "Margen en riesgo", "Costo total esperado", "Sin atender (central)", "Sin atender (alta)"]] = None
for col in ("Costo logístico", "Margen en riesgo", "Costo total esperado"):
    tabla[col] = tabla[col].map(cop)
st.dataframe(tabla, hide_index=True, width="stretch")
if rec is None:
    st.info("**Caso guía.** Bogotá tiene 90 unidades y recibe 40 el día 3. La demanda acumulada llega a 180 el día 5, "
            "así que el faltante empieza el día 4. El pedido normal llega el día 7: tarde. El traslado de 50 desde "
            "Medellín cuesta menos que la compra urgente y Medellín conserva su stock de seguridad; con demanda alta "
            "todavía quedan 20 unidades expuestas.")
