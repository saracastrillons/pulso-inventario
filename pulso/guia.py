"""Caso guía de la demo, con números redondos para explicarlo en una entrevista.

Bogotá tiene 90 unidades y una recepción confirmada de 40 el día 3. Una promoción de cinco días
lleva la demanda a 36 unidades diarias (180 acumuladas al día 5), así que el faltante empieza el
día 4. Un pedido normal llega el día 7; una compra urgente o un traslado llegan el día 2.
Medellín puede ceder 50 unidades sin tocar su stock de seguridad.
"""
from __future__ import annotations

import numpy as np

from .config import HMAX
from .motor import Serie, horizonte_de

NOMBRE = "Caso guía (Bogotá, promoción de 5 días)"
PLAZO = 6


def caso_guia(plazo_normal: int = PLAZO, intensidad: float = 1.0, excedente_origen: float = 50) -> Serie:
    H = horizonte_de(plazo_normal)
    central = np.array([36.0] * 5 + [10.0] * (HMAX - 5)) * intensidad
    alto = np.array([40.0] * 5 + [12.0] * (HMAX - 5)) * intensidad
    recepciones = np.zeros(HMAX)
    recepciones[2], recepciones[5] = 40, 100
    return Serie(stock=90, recepciones=recepciones[:H], central=central[:H], alto=alto[:H], costo=40_000,
                 margen=12_000, plazo_normal=plazo_normal, minimo=100, multiplo=10,
                 excedente_origen=excedente_origen, capacidad=500)
