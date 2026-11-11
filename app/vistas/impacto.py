"""Pantalla 4. Impacto: comparación simulada de políticas de inventario y calidad del pronóstico."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import streamlit as st

import comun
from comun import COLOR_POLITICA, cop, millones, pct
from pulso.config import DATOS, DIAS_PRUEBA
from pulso.db import leer_costos, leer_parametro
from pulso.demo import recalcular_impacto
from pulso.simulacion import ESCENARIOS

con = comun.conexion()
st.title("Impacto de las políticas de inventario")
st.caption(f"Resultados **simulados** sobre los últimos {DIAS_PRUEBA} días de la historia. Las tres políticas parten del mismo "
           "inventario y enfrentan la misma demanda, los mismos retrasos de proveedores y los mismos costos. "
           "Los modelos se entrenaron solo con datos anteriores a ese periodo.")

nombres = {"base": "Base", "favorable": "Favorable", "adverso": "Adverso"}
esc = st.segmented_control("Escenario", list(nombres), default="base", format_func=nombres.get) or "base"
st.caption(ESCENARIOS[esc]["nota"])

propio = st.session_state.get("impacto_propio")
if propio and propio["escenario"] == esc:
    res, diario = propio["res"], propio["diario"]
    st.info("Mostrando el recálculo con tus supuestos de costo. Recarga la página para volver a los valores originales.")
else:
    res = pd.read_sql("SELECT * FROM impacto WHERE escenario = ?", con, params=(esc,))
    diario = pd.read_sql("SELECT * FROM impacto_diario WHERE escenario = ?", con, params=(esc,))
res = res.set_index("politica")
ref = res.loc["Punto de pedido"]

cols = st.columns(3)
for col, (pol, r) in zip(cols, res.iterrows()):
    with col.container(border=True):
        st.markdown(f"<span style='color:{COLOR_POLITICA[pol]}'>●</span> **{pol}**", unsafe_allow_html=True)
        es_ref = pol == "Punto de pedido"
        st.metric("Unidades demandadas que se atendieron", pct(r.nivel_servicio, 2),
                  None if es_ref else f"{(r.nivel_servicio - ref.nivel_servicio) * 100:+.2f} puntos".replace(".", ","))
        st.metric("Días-producto con quiebre", f"{int(r.dias_quiebre)}",
                  None if es_ref else f"{int(r.dias_quiebre - ref.dias_quiebre):+d}", delta_color="inverse")
        st.metric("Inventario promedio al costo", millones(r.inventario_promedio),
                  None if es_ref else f"{(r.inventario_promedio - ref.inventario_promedio) / 1e6:+.1f} M".replace(".", ","),
                  delta_color="off")
        st.metric("Gasto en urgencias y traslados", millones(r.gasto_urgencias + r.gasto_traslados))

st.subheader("Efecto económico del periodo")
st.caption("Se calcula aparte del capital: margen de lo vendido menos costos de pedir, urgencias, traslados y mantener inventario.")
eco = pd.DataFrame({
    "Margen de lo vendido": res.margen, "Costo de pedidos normales": -res.costo_pedidos,
    "Urgencias": -res.gasto_urgencias, "Traslados": -res.gasto_traslados,
    "Mantener inventario": -res.costo_mantener, "Resultado del periodo": res.resultado,
    "Frente a punto de pedido": res.resultado - ref.resultado,
    "Margen perdido por faltantes": res.margen_perdido,
    "Capital inmovilizado promedio vs. punto de pedido": res.inventario_promedio - ref.inventario_promedio,
}).T
st.dataframe(eco.map(cop), width="stretch")

mejor = res.resultado.idxmax()
capital = res.inventario_promedio - ref.inventario_promedio
frases = [f"En este escenario el mejor resultado económico lo obtiene **{mejor}**."]
for pol in res.index[1:]:
    dif, cap = res.resultado[pol] - ref.resultado, capital[pol]
    frases.append(f"**{pol}**: {'gana' if dif >= 0 else 'pierde'} {millones(abs(dif))} frente al punto de pedido en "
                  f"{DIAS_PRUEBA} días y mantiene {millones(abs(cap))} {'menos' if cap < 0 else 'más'} de capital inmovilizado promedio.")
st.markdown("  \n".join(frases))

st.subheader("Ventas perdidas acumuladas")
fig = comun.figura(340, "Unidades sin atender (acumulado)")
for pol in res.index:
    dd = diario[diario.politica == pol].sort_values("dia")
    fig.add_scatter(x=list(range(1, len(dd) + 1)), y=dd.perdidas.cumsum(), name=pol, mode="lines",
                    line=dict(color=COLOR_POLITICA[pol], width=2))
fig.update_xaxes(title="Día del periodo de prueba", title_font=dict(color=comun.GRIS))
st.plotly_chart(fig, width="stretch")

with st.expander("Cuestionar los supuestos de costo y recalcular"):
    base = leer_costos(con)
    with st.form("costos"):
        a, b, c = st.columns(3)
        tasa = a.number_input("Costo anual de mantener inventario (%)", 5.0, 60.0, base.tasa_mantener_anual * 100)
        pedido = a.number_input("Costo fijo de un pedido normal", 0, 500_000, int(base.costo_pedido_normal), 5_000)
        fijo_u = b.number_input("Costo fijo de una compra urgente", 0, 1_000_000, int(base.costo_fijo_urgente), 10_000)
        recargo = b.number_input("Sobrecosto por unidad urgente (%)", 0.0, 60.0, base.recargo_urgente * 100)
        fijo_t = c.number_input("Flete fijo de un traslado", 0, 1_000_000, int(base.costo_fijo_traslado), 10_000)
        unidad_t = c.number_input("Manejo por unidad trasladada", 0, 10_000, int(base.costo_unidad_traslado), 100)
        if st.form_submit_button("Recalcular este escenario (unos 10 segundos)"):
            nuevos = replace(base, tasa_mantener_anual=tasa / 100, costo_pedido_normal=pedido, costo_fijo_urgente=fijo_u,
                             recargo_urgente=recargo / 100, costo_fijo_traslado=fijo_t, costo_unidad_traslado=unidad_t)
            with st.spinner("Simulando las tres políticas..."):
                r2, d2 = recalcular_impacto(DATOS, nuevos, esc)
            st.session_state["impacto_propio"] = dict(escenario=esc, res=r2, diario=d2)
            st.rerun()

st.subheader("Calidad del pronóstico")
st.caption("Evaluación avanzando por fechas: en cada corte se pronostican los días siguientes con lo que se sabía hasta ese día "
           "y se compara con la demanda total (incluida la que no se pudo atender, que el modelo nunca vio).")
err = pd.read_sql("SELECT * FROM eval_error", con)
cob = pd.read_sql("SELECT * FROM eval_cobertura", con)
orden = ["alta", "media", "intermitente", "todas"]
t = err.pivot_table(index=["rotacion", "horizonte"], columns="metodo", values=["mae", "sesgo"]).reindex(orden, level=0)
t.columns = [f"{'Error absoluto' if a == 'mae' else 'Sesgo'} · {b}" for a, b in t.columns]
t["Mejora del error"] = 1 - t["Error absoluto · Aprendizaje automático"] / t["Error absoluto · Sencillo"]
t.index.names = ["Rotación", "Horizonte"]
st.dataframe(t.style.format("{:.2f}").format({"Mejora del error": "{:.1%}"}), width="stretch")
st.caption("Error y sesgo en unidades por día. Sesgo negativo: el pronóstico se queda corto.")

val = leer_parametro(con, "validacion_vivo") or {}
if val.get("usa_ml"):
    usa = ", ".join(f"{k}: {'aprendizaje automático' if v else 'referencia sencilla'}" for k, v in val["usa_ml"].items())
    st.markdown(f"**Método en uso por segmento** (el que tuvo menor error en validación): {usa}.")

c5 = cob[cob.dias == 5].pivot_table(index="rotacion", columns="metodo", values="cobertura_alto").reindex(orden)
c5.index.name = "Rotación"
st.markdown("**Cobertura del escenario alto (P90) sobre la demanda acumulada a 5 días.** Debería estar cerca de 90 %: "
            "si queda muy por debajo, el escenario alto subestima los picos.")
st.dataframe(c5.style.format("{:.1%}"), width="stretch")
