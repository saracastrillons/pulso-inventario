"""Utilidades compartidas por las pantallas."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pulso.config import DATOS, NOMBRE_BODEGA
from pulso.db import conectar, ultimo_lote
from pulso.demo import rutas

# Paleta categórica en orden fijo: el color sigue a la entidad, no a su posición.
AZUL, NARANJA, AGUA, GRIS = "#2a78d6", "#eb6834", "#1baf7a", "#898781"
CRITICO = "#d03b3b"
TINTA, TINTA_2, REJILLA, EJE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
COLOR_OPCION = {"esperar": GRIS, "compra_normal": AZUL, "compra_urgente": NARANJA, "traslado": AGUA, "combinada": AZUL}
COLOR_POLITICA = {"Punto de pedido": GRIS, "Motor + pronóstico sencillo": NARANJA, "Motor + aprendizaje automático": AZUL}


@st.cache_resource
def conexion():
    return conectar(rutas(DATOS)["db"])


def cop(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    return "$" + f"{x:,.0f}".replace(",", ".")


def millones(x) -> str:
    return "$" + f"{x / 1e6:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " M"


def pct(x, dec: int = 1) -> str:
    return f"{x * 100:.{dec}f}".replace(".", ",") + " %"


def bodega(b: str) -> str:
    return NOMBRE_BODEGA.get(b, b)


def figura(alto: int = 360, titulo_y: str = "") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(family="Source Sans Pro, sans-serif", size=13, color=TINTA_2), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=TINTA_2)),
        hoverlabel=dict(bgcolor="#ffffff", font=dict(color=TINTA)),
    )
    fig.update_xaxes(showgrid=False, linecolor=EJE, tickfont=dict(color=GRIS))
    fig.update_yaxes(gridcolor=REJILLA, zeroline=True, zerolinecolor=EJE, title=titulo_y, rangemode="tozero",
                     tickfont=dict(color=GRIS), title_font=dict(color=GRIS))
    return fig


def estado_carga(con) -> tuple:
    """Devuelve (último lote exitoso, último intento). Si el intento más reciente falló, los datos están desactualizados."""
    return ultimo_lote(con, solo_ok=True), ultimo_lote(con)


def aviso_desactualizado(con) -> bool:
    ok, ultimo = estado_carga(con)
    if ok is not None and ultimo["estado"] != "ok":
        st.error(
            f"**Datos desactualizados.** La carga del corte {ultimo['fecha_corte']} falló: {ultimo['detalle']}. "
            f"Se muestra la última corrida exitosa (corte {ok['fecha_corte']}); sus recomendaciones pueden no "
            "reflejar el inventario actual. Usa *Procesar día siguiente* para reintentar la carga.", icon="⚠️")
        return True
    return False


def recomendaciones(con, lote_id: str) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT r.*, p.nombre, p.categoria, p.rotacion FROM recomendaciones r JOIN productos p USING (producto_id)
           WHERE r.lote_id = ? ORDER BY r.impacto DESC, r.dias_para_actuar""", con, params=(lote_id,))
