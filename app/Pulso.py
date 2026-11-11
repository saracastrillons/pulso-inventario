"""Pulso Inventario: punto de entrada.  Ejecutar con:  streamlit run app/Pulso.py"""
from __future__ import annotations

import streamlit as st

import comun
from pulso.config import DATOS
from pulso.demo import preparar, procesar_dia_siguiente, rutas

st.set_page_config(page_title="Pulso Inventario", page_icon="📦", layout="wide")

if not rutas(DATOS)["db"].exists():
    with st.status("Preparando la demo por primera vez (cerca de un minuto)...", expanded=True) as estado:
        preparar(DATOS, decir=st.write)
        estado.update(label="Demo lista", state="complete")
    st.rerun()

paginas = {
    "prioridades": st.Page("vistas/prioridades.py", title="Prioridades", icon=":material/inbox:", default=True),
    "detalle": st.Page("vistas/detalle.py", title="Detalle de decisión", icon=":material/query_stats:"),
    "simulador": st.Page("vistas/simulador.py", title="Simulador", icon=":material/tune:"),
    "impacto": st.Page("vistas/impacto.py", title="Impacto", icon=":material/monitoring:"),
}
st.session_state["paginas"] = paginas
navegacion = st.navigation(list(paginas.values()))

with st.sidebar:
    ok, ultimo = comun.estado_carga(comun.conexion())
    st.caption("PROCESO DIARIO")
    if ok:
        st.markdown(f"Corte de datos: **{ok['fecha_corte']}**  \nPronóstico: `{ok['version_pronostico']}`")
    st.caption("En producción lo ejecuta el Programador de tareas de Windows a las 6:00 a. m. "
               "Aquí puedes adelantar el reloj.")
    auto = st.checkbox("Aprobar antes las pendientes", value=False,
                       help="Aprueba todas las recomendaciones pendientes, como si el analista aceptara el plan completo.")
    falla = st.checkbox("Simular archivo dañado", value=False,
                        help="El archivo de ventas llega sin una columna: la carga falla y la bandeja queda marcada como desactualizada.")
    if st.button("Procesar día siguiente", type="primary", width="stretch"):
        with st.spinner("Ejecutando solicitudes, cargando archivos y recalculando..."):
            st.session_state["ultimo_proceso"] = procesar_dia_siguiente(DATOS, auto, "columna" if falla else None)
        st.session_state.pop("rec_id", None)
        st.rerun()
    r = st.session_state.get("ultimo_proceso")
    if r:
        if r["estado"] == "ok":
            st.success(f"Corte {r['fecha_corte']}: {r['recomendaciones']} recomendaciones, "
                       f"{r.get('rechazadas', 0)} filas rechazadas.")
        else:
            st.error(r["detalle"])
    with st.expander("Reiniciar la demo"):
        st.caption("Vuelve al corte inicial y borra las aprobaciones. Tarda cerca de un minuto.")
        if st.button("Reiniciar", width="stretch"):
            comun.conexion().close()
            comun.conexion.clear()
            st.session_state.clear()
            with st.spinner("Regenerando datos, modelos y primer lote..."):
                preparar(DATOS, decir=lambda *_: None)
            st.rerun()

navegacion.run()
