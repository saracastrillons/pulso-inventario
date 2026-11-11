"""Motor de decisión: proyecta el inventario día a día y compara esperar, comprar y trasladar.

Convención de días: el día 1 es hoy (la mañana en que se decide). Una acción con plazo L
aprobada hoy llega la mañana del día 1 + L y ya puede atender la demanda de ese día.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .config import DIAS_REVISION, HMAX, Costos

ETIQUETA = {"esperar": "Esperar", "compra_normal": "Compra normal", "compra_urgente": "Compra urgente",
            "traslado": "Traslado", "combinada": "Combinada"}


def proyectar(stock: float, recepciones: np.ndarray, demanda: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inventario al cierre y unidades sin atender por día. La venta que no se atiende se pierde."""
    n = len(demanda)
    inv, falt = np.zeros(n), np.zeros(n)
    s = float(stock)
    for i in range(n):
        disp = s + recepciones[i]
        falt[i] = max(demanda[i] - disp, 0.0)
        s = max(disp - demanda[i], 0.0)
        inv[i] = s
    return inv, falt


def horizonte_de(plazo_normal: int) -> int:
    return min(plazo_normal + DIAS_REVISION, HMAX)


@dataclass
class Serie:
    """Datos de un producto en una bodega para decidir hoy."""
    stock: float
    recepciones: np.ndarray      # [H] recepciones confirmadas; índice 0 = día 1
    central: np.ndarray          # [H] demanda proyectada, escenario central
    alto: np.ndarray             # [H] demanda proyectada, escenario alto
    costo: float
    margen: float
    plazo_normal: int
    minimo: int = 1
    multiplo: int = 1
    excedente_origen: float = 0.0   # unidades que la otra bodega puede ceder conservando su stock de seguridad
    capacidad: float = 0.0          # capacidad de transporte disponible hoy en la ruta
    recepciones_alto: np.ndarray | None = None   # recepciones en el escenario alto (pedidos sin confirmar, un día tarde)


@dataclass
class Opcion:
    accion: str
    acciones: list = field(default_factory=list)   # [(tipo, cantidad, día de llegada)]
    viable: bool = True
    motivo: str = ""
    costo_logistico: float = 0.0
    costo_mantener: float = 0.0
    margen_riesgo: float = 0.0
    faltante_central: float = 0.0
    faltante_alto: float = 0.0
    inv_central: np.ndarray | None = None
    inv_alto: np.ndarray | None = None
    falt_central: np.ndarray | None = None
    falt_alto: np.ndarray | None = None

    @property
    def costo_total(self) -> float:
        return self.costo_logistico + self.costo_mantener + self.margen_riesgo

    @property
    def descripcion(self) -> str:
        if not self.acciones:
            return ETIQUETA[self.accion]
        return " + ".join(f"{ETIQUETA[t]} {q}" for t, q, _ in self.acciones)

    def resumen(self) -> dict:
        return dict(
            accion=self.accion, descripcion=self.descripcion, viable=self.viable, motivo=self.motivo,
            cantidad=sum(q for _, q, _ in self.acciones),
            llegada=min((d for _, _, d in self.acciones), default=None),
            costo_logistico=round(self.costo_logistico), costo_mantener=round(self.costo_mantener),
            margen_riesgo=round(self.margen_riesgo), costo_total=round(self.costo_total),
            faltante_central=round(self.faltante_central, 1), faltante_alto=round(self.faltante_alto, 1),
        )


@dataclass
class Resultado:
    requiere_decision: bool
    elegida: Opcion | None = None
    esperar: Opcion | None = None
    opciones: list = field(default_factory=list)
    primer_faltante: int | None = None     # día (1 = hoy) del primer faltante si nadie actúa
    solo_escenario_alto: bool = False      # el faltante solo aparece con demanda alta
    dias_para_actuar: int = 0
    impacto: float = 0.0                   # ahorro esperado frente a esperar


def _evaluar(s: Serie, c: Costos, rapida: tuple | None, normal: int | None, base: Opcion | None) -> Opcion:
    H = len(s.central)
    rec = np.array(s.recepciones, dtype=float)
    rec_a = rec.copy() if s.recepciones_alto is None else np.array(s.recepciones_alto, dtype=float)
    op = Opcion("esperar")
    if rapida:
        tipo, q = rapida
        plazo = c.plazo_urgente if tipo == "compra_urgente" else c.plazo_traslado
        if plazo < H:
            rec[plazo] += q
            rec_a[plazo] += q
        op.acciones.append((tipo, int(q), 1 + plazo))
        op.costo_logistico += (c.costo_fijo_urgente + c.recargo_urgente * s.costo * q if tipo == "compra_urgente"
                               else c.costo_fijo_traslado + c.costo_unidad_traslado * q)
        op.accion = tipo
    if normal:
        if s.plazo_normal < H:
            rec[s.plazo_normal] += normal
        if s.plazo_normal + c.retraso_alto < H:
            rec_a[s.plazo_normal + c.retraso_alto] += normal
        op.acciones.append(("compra_normal", int(normal), 1 + s.plazo_normal))
        op.costo_logistico += c.costo_pedido_normal
        op.accion = "combinada" if rapida else "compra_normal"
    op.inv_central, op.falt_central = proyectar(s.stock, rec, s.central)
    op.inv_alto, op.falt_alto = proyectar(s.stock, rec_a, s.alto)
    w = c.peso_alto
    op.faltante_central, op.faltante_alto = op.falt_central.sum(), op.falt_alto.sum()
    op.margen_riesgo = s.margen * ((1 - w) * op.faltante_central + w * op.faltante_alto)
    mantener = c.tasa_mantener_anual / 365 * s.costo * ((1 - w) * op.inv_central.sum() + w * op.inv_alto.sum())
    op.costo_mantener = mantener - (base._mantener_bruto if base else mantener)
    op._mantener_bruto = mantener
    return op


def _arriba(q: float, multiplo: int = 1) -> int:
    return int(math.ceil(q / multiplo - 1e-9) * multiplo)


def decidir(s: Serie, c: Costos) -> Resultado:
    """Compara esperar, compra normal, compra urgente y traslado (y un puente rápido más compra normal)."""
    H = len(s.central)
    esperar = _evaluar(s, c, None, None, None)
    if esperar.faltante_alto < 0.5:
        return Resultado(False, esperar=esperar, opciones=[esperar])

    fc, fa = esperar.falt_central, esperar.falt_alto
    i_n = s.plazo_normal                       # índice del día en que llegaría una compra normal

    q_normal = set()
    if i_n < H:
        for f in (fc, fa):
            if f[i_n:].sum() >= 0.5:
                q_normal.add(max(s.minimo, _arriba(f[i_n:].sum(), s.multiplo)))

    def q_rapidas(plazo: int) -> set:
        qs = set()
        for f in (fc, fa):
            for hasta in (min(i_n, H), H):     # puente hasta que llegue una compra normal, o todo el horizonte
                if f[plazo:hasta].sum() >= 0.5:
                    qs.add(_arriba(f[plazo:hasta].sum()))
        return qs

    rapidas: list[tuple] = [("compra_urgente", q) for q in sorted(q_rapidas(c.plazo_urgente))]
    motivo_traslado = ""
    q_t = q_rapidas(c.plazo_traslado)
    tope = math.floor(min(s.excedente_origen, s.capacidad))
    if not q_t:
        motivo_traslado = "Llegaría después del faltante"
    elif s.excedente_origen < 1:
        motivo_traslado = "La bodega de origen quedaría por debajo de su stock de seguridad"
    elif s.capacidad < 1:
        motivo_traslado = "No queda capacidad de transporte hoy en la ruta"
    else:
        rapidas += [("traslado", q) for q in sorted({min(q, tope) for q in q_t})]

    planes = [esperar]
    for r in [None] + rapidas:
        for n in [None] + sorted(q_normal):
            if r or n:
                planes.append(_evaluar(s, c, r, n, esperar))
    elegida = min(planes, key=lambda o: (round(o.costo_total), len(o.acciones)))

    def mejor(accion: str) -> Opcion | None:
        cand = [o for o in planes if o.accion == accion]
        return min(cand, key=lambda o: o.costo_total) if cand else None

    opciones = [esperar]
    normal = mejor("compra_normal")
    if normal is None:
        normal = _evaluar(s, c, None, s.minimo, esperar)
        normal.viable = False
        normal.motivo = f"Llega el día {1 + i_n}, después del faltante"
    elif normal.acciones[0][1] == s.minimo and fa[i_n:].sum() < s.minimo:
        normal.motivo = "Cantidad ajustada al mínimo de compra"
    opciones.append(normal)
    opciones.append(mejor("compra_urgente") or Opcion("compra_urgente", viable=False, motivo="Llegaría después del faltante"))
    traslado = mejor("traslado")
    if traslado is None:
        traslado = Opcion("traslado", viable=False, motivo=motivo_traslado)
    elif traslado.acciones[0][1] < min(q_t):
        traslado.motivo = "Cantidad limitada por el excedente del origen o la capacidad de la ruta"
    opciones.append(traslado)
    if elegida.accion == "combinada":
        opciones.append(elegida)

    en_central = np.flatnonzero(fc >= 0.5)
    primeros = en_central if len(en_central) else np.flatnonzero(fa >= 0.5)
    primer = int(primeros[0]) + 1 if len(primeros) else None
    llegada_rapida = 1 + min(c.plazo_urgente, c.plazo_traslado)
    return Resultado(
        True, elegida=elegida, esperar=esperar, opciones=opciones, primer_faltante=primer,
        solo_escenario_alto=len(en_central) == 0,
        dias_para_actuar=max((primer or llegada_rapida) - llegada_rapida, 0),
        impacto=esperar.costo_total - elegida.costo_total,
    )


def stock_seguridad(desv: np.ndarray, plazo: np.ndarray, z: float) -> np.ndarray:
    return np.ceil(z * desv * np.sqrt(plazo))


def recomendar(stock, recepciones, central, alto, meta: dict, seguridad, c: Costos,
               recepciones_alto=None) -> dict[int, Resultado]:
    """Evalúa todas las series. Atiende primero las de mayor margen en riesgo y descuenta de la bodega de
    origen (y de la capacidad de la ruta) cada traslado aceptado, para que dos recomendaciones no cuenten
    con las mismas unidades.

    stock[S], recepciones[S,HMAX], central/alto[HMAX,S]. Serie s = producto * 2 + bodega.
    """
    S = len(stock)
    stock = np.array(stock, dtype=float)
    capacidad = {0: float(c.capacidad_ruta), 1: float(c.capacidad_ruta)}   # por bodega de origen
    H = [horizonte_de(int(p)) for p in meta["plazo"]]

    if recepciones_alto is None:
        recepciones_alto = recepciones

    def riesgo(s: int) -> float:
        _, f = proyectar(stock[s], recepciones_alto[s, :H[s]], alto[:H[s], s])
        return f.sum() * meta["margen"][s]

    resultados: dict[int, Resultado] = {}
    for s in sorted(range(S), key=riesgo, reverse=True):
        o = s ^ 1                                 # misma referencia en la otra bodega
        plazo_o = int(meta["plazo"][o])
        inv_o, _ = proyectar(stock[o], recepciones[o, :plazo_o], central[:plazo_o, o])
        excedente = max(inv_o.min() - seguridad[o], 0.0)
        serie = Serie(
            stock=stock[s], recepciones=recepciones[s, :H[s]], central=central[:H[s], s], alto=alto[:H[s], s],
            costo=float(meta["costo"][s]), margen=float(meta["margen"][s]), plazo_normal=int(meta["plazo"][s]),
            minimo=int(meta["minimo"][s]), multiplo=int(meta["multiplo"][s]),
            excedente_origen=excedente, capacidad=capacidad[o % 2], recepciones_alto=recepciones_alto[s, :H[s]],
        )
        res = decidir(serie, c)
        res.excedente_origen, res.seguridad_origen, res.stock_origen = excedente, float(seguridad[o]), float(stock[o])
        if res.requiere_decision:
            for tipo, q, _ in res.elegida.acciones:
                if tipo == "traslado":
                    stock[o] -= q
                    capacidad[o % 2] -= q
        resultados[s] = res
    return resultados
