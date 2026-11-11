"""Pantalla 2. Detalle de una decisión: curva de inventario con y sin la acción, alternativas y aprobación."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import comun
from comun import AZUL, CRITICO, GRIS, bodega, cop
from pulso.aprobacion import RecomendacionNoVigente, SaldoInsuficiente, aprobar, rechazar
from pulso.db import leer_costos
from pulso.resumen import describir_accion

con = comun.conexion()
ok, _ = comun.estado_carga(con)
st.title("Detalle de decisión")
if ok is None:
    st.stop()
viejo = comun.aviso_desactualizado(con)
recs = comun.recomendaciones(con, ok["lote_id"])
if recs.empty:
    st.info("El lote actual no tiene recomendaciones.")
    st.stop()

ids = list(recs.rec_id)
actual = st.session_state.get("rec_id")
etiqueta = {r.rec_id: f"{r.nombre} · {bodega(r.bodega_id)} · {describir_accion(r.accion, r.cantidad, r.origen if isinstance(r.origen, str) else None)}"
            for r in recs.itertuples()}
rec_id = st.selectbox("Recomendación", ids, index=ids.index(actual) if actual in ids else 0, format_func=etiqueta.get)
st.session_state["rec_id"] = rec_id
rec = recs[recs.rec_id == rec_id].iloc[0]
d = json.loads(rec.detalle_json)
origen = rec.origen if isinstance(rec.origen, str) else None
n = len(d["dias"])
dias = [f"Día {i + 1}<br>{f[5:]}" for i, f in enumerate(d["dias"])]

primer = d["dias"].index(rec.primer_faltante) + 1 if rec.primer_faltante in d["dias"] else None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Disponible hoy", f"{d['stock']:.0f} un.")
c2.metric("Primer día con faltante", f"Día {primer}" if primer else "—",
          "solo con demanda alta" if rec.solo_escenario_alto else None, delta_color="off")
c3.metric("Unidades en riesgo", f"{rec.unidades_riesgo:.0f}", f"{rec.unidades_riesgo_alto:.0f} con demanda alta", delta_color="off")
c4.metric("Si nadie actúa", cop(rec.margen_riesgo), help="Margen en riesgo ponderando ambos escenarios")

escenario = st.segmented_control("Escenario de demanda", ["Central", "Demanda alta"], default="Central") or "Central"
clave = "central" if escenario == "Central" else "alto"
esp, plan = d["esperar"], d["plan"]

fig = comun.figura(380, "Unidades en inventario al cierre")
fig.add_scatter(x=dias, y=esp[f"inv_{clave}"], name="Si nadie actúa", mode="lines+markers",
                line=dict(color=GRIS, width=2), marker=dict(size=8), fill="tozeroy", fillcolor="rgba(137,135,129,0.10)")
fig.add_scatter(x=dias, y=plan[f"inv_{clave}"], name=f"Con el plan: {plan['descripcion']}", mode="lines+markers",
                line=dict(color=AZUL, width=2), marker=dict(size=8))
falt = esp[f"falt_{clave}"]
if sum(falt) >= 0.5:
    fig.add_bar(x=dias, y=[-f for f in falt], name="Demanda sin atender si nadie actúa", marker_color=CRITICO,
                marker_line_width=0, width=0.35, hovertemplate="%{customdata:.0f} unidades sin atender<extra></extra>",
                customdata=falt)
    fig.update_yaxes(rangemode="normal")
for a in plan["acciones"]:
    if a["dia_llegada"] <= n:
        fig.add_annotation(x=dias[a["dia_llegada"] - 1], y=plan[f"inv_{clave}"][a["dia_llegada"] - 1],
                           text=f"Llegan {a['cantidad']}", showarrow=True, arrowhead=0, ay=-32, font=dict(color=comun.TINTA_2))
st.plotly_chart(fig, width="stretch")

expuestas = sum(plan[f"falt_{clave}"])
rec_previas = sum(d["recepciones"])
texto = (f"Hoy hay **{d['stock']:.0f}** unidades" + (f" y **{rec_previas:.0f}** ya pedidas por llegar" if rec_previas else "")
         + f". La demanda proyectada del horizonte es de **{sum(d[clave]):.0f}** unidades ({escenario.lower()}). ")
texto += (f"Con el plan sugerido quedan **{expuestas:.0f}** unidades expuestas en este escenario."
          if expuestas >= 0.5 else "El plan sugerido cubre todo el faltante de este escenario.")
st.markdown(texto)
st.caption(f"Pronóstico usado: {'modelo de aprendizaje automático' if d['metodo_pronostico'] == 'ml' else 'referencia sencilla'} "
           f"({ok['version_pronostico']}). Las barras rojas bajo el eje son ventas que se perderían.")

st.subheader("Alternativas comparadas")
op = pd.DataFrame(d["opciones"])
elegida = plan["descripcion"]
tabla = pd.DataFrame({
    "Opción": op.descripcion,
    "Resultado": ["Recomendada" if x == elegida else ("Descartada" if not v else "Viable") for x, v in zip(op.descripcion, op.viable)],
    "Llega": [f"Día {int(x)}" if pd.notna(x) else "—" for x in op.llegada],
    "Costo logístico": op.costo_logistico, "Mantener inventario": op.costo_mantener,
    "Margen en riesgo": op.margen_riesgo, "Costo total esperado": op.costo_total,
    "Sin atender (central)": op.faltante_central, "Sin atender (alta)": op.faltante_alto,
    "Nota": op.motivo,
})
sin_dato = (op.cantidad == 0) & (op.accion != "esperar")
tabla.loc[sin_dato, ["Costo logístico", "Mantener inventario", "Margen en riesgo", "Costo total esperado",
                     "Sin atender (central)", "Sin atender (alta)"]] = None
for col in ("Costo logístico", "Mantener inventario", "Margen en riesgo", "Costo total esperado"):
    tabla[col] = tabla[col].map(cop)
st.dataframe(tabla, hide_index=True, width="stretch")
peso = leer_costos(con).peso_alto
o = d["origen"]
st.caption(f"Costo total esperado = costo logístico + costo adicional de mantener inventario + margen de las unidades que "
           f"quedarían sin atender ({1 - peso:.0%} escenario central, {peso:.0%} demanda alta). "
           f"{bodega(o['bodega'])} tiene {o['stock']:.0f} unidades, debe conservar {o['seguridad']:.0f} de seguridad "
           f"y puede ceder {o['excedente']:.0f}.")

st.subheader("Decisión")
sol = con.execute("SELECT * FROM solicitudes WHERE rec_id = ?", (rec_id,)).fetchone()
if rec.estado == "pendiente":
    with st.form("decision"):
        a, b = st.columns([1, 2])
        cantidad = a.number_input("Cantidad", min_value=1, value=int(rec.cantidad), step=1)
        decision = b.radio("Qué hacer", ["Aprobar", "Rechazar"], horizontal=True)
        motivo = st.text_input("Motivo (obligatorio para rechazar)")
        enviar = st.form_submit_button("Confirmar decisión",
                                       type="primary", disabled=viejo)
    if viejo:
        st.caption("No se puede aprobar sobre datos desactualizados: primero reintenta la carga.")
    if enviar:
        try:
            if decision == "Aprobar":
                s = aprobar(con, rec_id, int(cantidad))
                st.session_state["aviso"] = f"Solicitud {s['solicitud_id']} creada."
            else:
                rechazar(con, rec_id, motivo)
                st.session_state["aviso"] = "Recomendación rechazada y registrada."
            st.rerun()
        except SaldoInsuficiente as e:
            st.error(f"{e}")
        except (RecomendacionNoVigente, ValueError) as e:
            st.warning(str(e))
else:
    if st.session_state.get("aviso"):
        st.success(st.session_state.pop("aviso"))
    if sol:
        st.markdown(
            f"**Solicitud `{sol['solicitud_id']}`** · {describir_accion(sol['tipo'], sol['cantidad'], sol['origen'])} "
            f"hacia {bodega(sol['destino'])} · llegada esperada **{sol['fecha_esperada']}** · estado **{sol['estado']}**"
            + (f" · recibida el {sol['fecha_recepcion']}" if sol["fecha_recepcion"] else ""))
        if sol["tipo"] == "traslado" and sol["estado"] == "aprobada":
            st.caption(f"Las {sol['cantidad']} unidades quedaron reservadas en {bodega(sol['origen'])}.")
    else:
        st.markdown(f"Esta recomendación está **{rec.estado}**.")
    aud = pd.read_sql("SELECT decision, cantidad_sugerida, cantidad_final, motivo, usuario, version_pronostico, lote_id, creada_en, datos_json "
                      "FROM decisiones WHERE rec_id = ?", con, params=(rec_id,))
    if not aud.empty:
        st.dataframe(aud.drop(columns=["datos_json"]), hide_index=True, width="stretch")
        with st.expander("Datos guardados con la decisión (auditoría)"):
            st.json(json.loads(aud.datos_json.iloc[0]), expanded=False)
