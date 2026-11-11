"""Pantalla 1. Bandeja de decisiones: qué atender hoy, ordenado por impacto y por tiempo para actuar."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import comun
from comun import bodega, cop
from pulso import resumen
from pulso.aprobacion import exportar_solicitudes
from pulso.config import DATOS
from pulso.demo import rutas

con = comun.conexion()
ok, ultimo = comun.estado_carga(con)

st.title("Prioridades de hoy")
if ok is None:
    st.warning("Todavía no hay una carga exitosa.")
    st.stop()
viejo = comun.aviso_desactualizado(con)
st.caption(f"Datos con corte al **{ok['fecha_corte']}** · lote `{ok['lote_id']}` procesado el "
           f"{ok['ejecutado_en'].replace('T', ' a las ')} · {ok['filas_cargadas']} filas cargadas, "
           f"{ok['filas_rechazadas']} rechazadas")

recs = comun.recomendaciones(con, ok["lote_id"])
pend = recs[recs.estado == "pendiente"]
unicas = pend.drop_duplicates(["producto_id", "bodega_id"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Decisiones pendientes", len(pend))
c2.metric("Margen en riesgo si nadie actúa", cop(unicas.margen_riesgo.sum()),
          help="Margen de contribución de las unidades que quedarían sin atender, ponderando el escenario central y el de demanda alta.")
c3.metric("Costo de las acciones sugeridas", cop(pend.costo_accion.sum()),
          help="Fletes, costos fijos de pedido y sobrecosto de urgencias. No incluye el valor de la mercancía.")
c4.metric("Para decidir hoy", int((unicas.dias_para_actuar == 0).sum()),
          help="Referencias donde esperar un día más ya no permite llegar antes del faltante.")

f1, f2, f3, f4 = st.columns([1, 1.4, 1.2, 1.2])
f_bod = f1.multiselect("Bodega", ["BOG", "MED"], format_func=bodega, placeholder="Todas")
f_cat = f2.multiselect("Categoría", sorted(recs.categoria.unique()), placeholder="Todas")
f_urg = f3.selectbox("Urgencia", ["Todas", "Hoy", "En 1 o 2 días", "3 días o más"])
f_est = f4.selectbox("Estado", ["Pendientes", "Todas"])

vista = recs if f_est == "Todas" else pend
if f_bod:
    vista = vista[vista.bodega_id.isin(f_bod)]
if f_cat:
    vista = vista[vista.categoria.isin(f_cat)]
if f_urg != "Todas":
    d = vista.dias_para_actuar
    vista = vista[{"Hoy": d == 0, "En 1 o 2 días": d.between(1, 2), "3 días o más": d >= 3}[f_urg]]
vista = vista.reset_index(drop=True)

if vista.empty:
    st.info("No hay decisiones con esos filtros. Usa *Procesar día siguiente* para avanzar la demo.")
else:
    corte = pd.Timestamp(ok["fecha_corte"])
    dia = lambda f: (pd.Timestamp(f) - corte).days if f else None
    tabla = pd.DataFrame({
        "Producto": vista.nombre,
        "Bodega": vista.bodega_id.map(bodega),
        "Primer faltante": [f"Día {dia(f)} · {f[5:]}" + (" (solo demanda alta)" if a else "")
                            for f, a in zip(vista.primer_faltante, vista.solo_escenario_alto)],
        "Unidades en riesgo": vista.unidades_riesgo.round(),
        "Con demanda alta": vista.unidades_riesgo_alto.round(),
        "Acción sugerida": [resumen.describir_accion(a, q, o) for a, q, o in zip(vista.accion, vista.cantidad, vista.origen.where(vista.origen.notna(), None))],
        "Llega": [f"Día {dia(f)}" for f in vista.fecha_llegada],
        "Costo de la acción": vista.costo_accion.round(),
        "Si nadie actúa": vista.margen_riesgo.round(),
        "Días para actuar": vista.dias_para_actuar,
        "Estado": vista.estado + (" · desactualizada" if viejo else ""),
        "Registro": vista.rec_id,
    })
    st.caption("Ordenadas por impacto estimado (lo que se evita perder menos lo que cuesta actuar). "
               "El día 1 es hoy. Selecciona una fila para ver de dónde sale la recomendación.")
    evento = st.dataframe(
        tabla, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={
            "Costo de la acción": st.column_config.NumberColumn("Costo de la acción (COP)", format="localized"),
            "Si nadie actúa": st.column_config.NumberColumn("Si nadie actúa (COP)", format="localized", help="Margen en riesgo si se espera"),
            "Unidades en riesgo": st.column_config.NumberColumn(format="%d", help="Escenario central"),
            "Con demanda alta": st.column_config.NumberColumn(format="%d"),
            "Días para actuar": st.column_config.NumberColumn(format="%d", help="0 = hay que decidir hoy"),
        })
    if evento.selection.rows:
        fila = vista.iloc[evento.selection.rows[0]]
        st.session_state["rec_id"] = fila.rec_id
        if st.button(f"Abrir el detalle de {fila.nombre} en {bodega(fila.bodega_id)}", type="primary"):
            st.switch_page(st.session_state["paginas"]["detalle"])

with st.expander("Resumen para la reunión de operaciones"):
    r = resumen.generar(con)
    for titulo, clave in (("Qué cambió", "cambios"), ("Las tres prioridades", "prioridades"),
                          ("Supuestos que podrían cambiar la decisión", "supuestos")):
        if r[clave]:
            st.markdown(f"**{titulo}**\n" + "\n".join(f"- {x}" for x in r[clave]))
    st.caption("Texto generado con plantillas a partir de los resultados del motor; entre corchetes, el registro que respalda cada cifra.")

with st.expander("Solicitudes generadas"):
    sol = pd.read_sql("SELECT * FROM solicitudes ORDER BY creada_en DESC, solicitud_id DESC", con)
    if sol.empty:
        st.write("Aún no se ha aprobado ninguna recomendación.")
    else:
        st.dataframe(sol.drop(columns=["rec_id"]), hide_index=True, width="stretch")
        ruta = exportar_solicitudes(con, rutas(DATOS)["salida"])
        st.download_button("Descargar solicitudes.csv", ruta.read_bytes(), "solicitudes.csv", "text/csv")

with st.expander("Historial de cargas y filas rechazadas"):
    st.dataframe(pd.read_sql("SELECT * FROM lotes ORDER BY fecha_corte DESC LIMIT 15", con), hide_index=True, width="stretch")
    rech = pd.read_sql("SELECT * FROM filas_rechazadas ORDER BY lote_id DESC LIMIT 50", con)
    if not rech.empty:
        st.dataframe(rech, hide_index=True, width="stretch")
