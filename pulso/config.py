"""Constantes y supuestos de costo de Pulso Inventario."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = Path(os.environ.get("PULSO_DATOS", RAIZ / "data"))

SEMILLA = 42
FECHA_INICIO = date(2025, 5, 5)  # lunes
DIAS_HISTORIA = 548              # 18 meses que ve el analista
DIAS_FUTURO = 90                 # reserva oculta para "Procesar día siguiente"
DIAS_PRUEBA = 120                # últimos días de la historia: periodo de prueba
HMAX = 12                        # horizonte máximo de pronóstico (días)
DIAS_REVISION = 2                # días que se miran después de la llegada de un pedido normal

BODEGAS = ["BOG", "MED"]
NOMBRE_BODEGA = {"BOG": "Bogotá", "MED": "Medellín"}
ROTACIONES = ["alta", "media", "intermitente"]


def fecha(dia: int) -> date:
    return FECHA_INICIO + timedelta(days=int(dia))


def indice(f: date | str) -> int:
    if isinstance(f, str):
        f = date.fromisoformat(f[:10])
    return (f - FECHA_INICIO).days


@dataclass
class Costos:
    """Supuestos editables. Todas las cifras en COP."""

    tasa_mantener_anual: float = 0.24      # costo de mantener inventario (% del costo al año)
    costo_pedido_normal: float = 40_000    # costo fijo por orden de compra normal
    costo_fijo_urgente: float = 150_000    # costo fijo por compra urgente
    recargo_urgente: float = 0.15          # sobrecosto por unidad (% del costo unitario)
    costo_fijo_traslado: float = 120_000   # flete fijo por traslado entre bodegas
    costo_unidad_traslado: float = 600     # manejo por unidad trasladada
    capacidad_ruta: int = 500              # unidades por día y por ruta
    plazo_urgente: int = 1                 # días: aprobada hoy (día 1), llega el día 2
    plazo_traslado: int = 1
    peso_alto: float = 0.30                # peso del escenario de demanda alta en el costo esperado
    retraso_alto: int = 0                  # días de retraso supuestos, en el escenario alto, para compras sin confirmar
    z_punto_pedido: float = 1.65           # nivel de servicio de la política de punto de pedido
    z_seguridad: float = 1.28              # stock de seguridad que debe conservar la bodega de origen

    def dict(self) -> dict:
        return asdict(self)
