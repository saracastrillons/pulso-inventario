"""Políticas de inventario y simulación día por día para compararlas en igualdad de condiciones."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import HMAX, Costos
from .motor import recomendar, stock_seguridad
from .mundo import Accion, Mundo
from .pronostico import Pronosticador, ajustar_ventas

VENTANA = 56


def estadisticas(obs, media_inicial: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Media y desviación diaria de las ventas ajustadas en las últimas 8 semanas."""
    if obs.corte + 1 < 28 and media_inicial is not None:
        return media_inicial, np.sqrt(media_inicial) * 1.2
    adj = ajustar_ventas(obs.ventas, obs.cierre)[-VENTANA:]
    return adj.mean(axis=0), adj.std(axis=0)


class PoliticaPuntoPedido:
    """Reposición clásica: si la posición (existencias + pedidos en camino) cae al punto de pedido,
    se compra en plazo normal hasta cubrir una semana adicional. No usa urgencias ni traslados."""

    nombre = "Punto de pedido"

    def __init__(self, media_inicial: np.ndarray | None = None, dias_cobertura: int = 7):
        self.media_inicial = media_inicial
        self.dias_cobertura = dias_cobertura

    def decidir(self, mundo: Mundo) -> list[Accion]:
        m, c = mundo.meta, mundo.costos
        mu, sd = estadisticas(mundo.observar(), self.media_inicial)
        plazo = m["plazo"] + 1
        punto = mu * plazo + c.z_punto_pedido * sd * np.sqrt(plazo)
        posicion = mundo.stock.reshape(-1).astype(float)
        for e in mundo.envios:
            posicion[e.p * mundo.B + e.b] += e.cantidad
        acciones = []
        for s in np.flatnonzero(posicion <= punto):
            falta = punto[s] + mu[s] * self.dias_cobertura - posicion[s]
            q = max(m["minimo"][s], int(np.ceil(falta / m["multiplo"][s]) * m["multiplo"][s]))
            acciones.append(Accion("compra_normal", int(m["prod"][s]), int(m["bod"][s]), int(q)))
        return acciones


class PoliticaMotor:
    """Motor de decisiones (esperar / comprar / trasladar) alimentado por un pronóstico."""

    def __init__(self, pronosticador: Pronosticador, nombre: str):
        self.pron = pronosticador
        self.nombre = nombre

    def evaluar(self, mundo: Mundo) -> dict:
        m, c = mundo.meta, mundo.costos
        obs = mundo.observar()
        central, alto = self.pron.predecir(obs)
        _, sd = estadisticas(obs)
        seguridad = stock_seguridad(sd, m["plazo"], c.z_seguridad)
        rec, rec_alto = mundo.recepciones_conocidas(HMAX)
        return recomendar(mundo.stock.reshape(-1), rec, central, alto, m, seguridad, c, rec_alto)

    def decidir(self, mundo: Mundo) -> list[Accion]:
        m = mundo.meta
        acciones = []
        for s, r in self.evaluar(mundo).items():
            if r.requiere_decision:
                for tipo, q, _ in r.elegida.acciones:
                    p, b = int(m["prod"][s]), int(m["bod"][s])
                    acciones.append(Accion(tipo, p, b, int(q), origen=(1 - b) if tipo == "traslado" else -1))
        return acciones


def simular(mundo: Mundo, politica, dias: int) -> pd.DataFrame:
    """Avanza `dias` días aplicando cada mañana las decisiones de la política."""
    for _ in range(dias):
        mundo.paso(politica.decidir(mundo))
    return pd.DataFrame(mundo.diario[-dias:])


def resumir(diario: pd.DataFrame) -> dict:
    d = diario
    logistica = d.costo_pedidos.sum() + d.costo_urgencias.sum() + d.costo_traslados.sum()
    return dict(
        nivel_servicio=d.ventas.sum() / d.demanda.sum(),
        unidades_perdidas=int(d.perdidas.sum()),
        dias_quiebre=int(d.quiebres.sum()),
        inventario_promedio=float(d.valor_inventario.mean()),
        margen=float(d.margen.sum()),
        margen_perdido=float(d.margen_perdido.sum()),
        costo_pedidos=float(d.costo_pedidos.sum()),
        gasto_urgencias=float(d.costo_urgencias.sum()),
        gasto_traslados=float(d.costo_traslados.sum()),
        costo_mantener=float(d.costo_mantener.sum()),
        n_normales=int(d.n_normales.sum()), n_urgentes=int(d.n_urgentes.sum()), n_traslados=int(d.n_traslados.sum()),
        resultado=float(d.margen.sum() - logistica - d.costo_mantener.sum()),
    )


ESCENARIOS = {
    "base": dict(demanda=1.00, retraso_extra=0.0, sin_retrasos=False,
                 nota="Demanda y retrasos tal como ocurrieron en el periodo de prueba."),
    "favorable": dict(demanda=0.90, retraso_extra=0.0, sin_retrasos=True,
                      nota="Demanda 10 % menor y proveedores sin retrasos."),
    "adverso": dict(demanda=1.15, retraso_extra=0.30, sin_retrasos=False,
                    nota="Demanda 15 % mayor y 30 % de los pedidos con dos días adicionales de retraso."),
}


def aplicar_escenario(mundo: Mundo, escenario: str, dias: int, semilla: int = 7) -> Mundo:
    """Modifica la demanda y los retrasos del periodo que falta por simular. Igual para todas las políticas."""
    e = ESCENARIOS[escenario]
    rng = np.random.default_rng(semilla)
    t0, t1 = mundo.dia, mundo.dia + dias
    if e["demanda"] != 1.0:
        d = mundo.demanda[t0:t1] * e["demanda"]
        mundo.demanda[t0:t1] = np.floor(d + rng.random(d.shape))   # redondeo aleatorio, sin sesgo
    if e["sin_retrasos"]:
        mundo.retrasos[t0:t1] = 0
    if e["retraso_extra"]:
        mundo.retrasos[t0:t1] += 2 * (rng.random(mundo.retrasos[t0:t1].shape) < e["retraso_extra"])
    return mundo


def comparar_politicas(mundo_base: Mundo, pron_sencillo: Pronosticador, pron_ml: Pronosticador,
                       dias: int, costos: Costos | None = None, escenario: str = "base"):
    """Corre las tres políticas desde el mismo inventario, con la misma demanda y los mismos retrasos."""
    politicas = [
        PoliticaPuntoPedido(),
        PoliticaMotor(pron_sencillo, "Motor + pronóstico sencillo"),
        PoliticaMotor(pron_ml, "Motor + aprendizaje automático"),
    ]
    resumenes, diarios = [], []
    for pol in politicas:
        mundo = aplicar_escenario(mundo_base.copiar(), escenario, dias)
        if costos is not None:
            mundo.costos = costos
        d = simular(mundo, pol, dias)
        resumenes.append(dict(escenario=escenario, politica=pol.nombre, **resumir(d)))
        diarios.append(d.assign(escenario=escenario, politica=pol.nombre))
    return pd.DataFrame(resumenes), pd.concat(diarios, ignore_index=True)
