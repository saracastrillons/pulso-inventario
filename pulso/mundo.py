"""Mundo sintético: catálogo, demanda oculta, promociones, retrasos y la simulación diaria.

El mundo conoce la demanda total. Las políticas y el pronóstico solo reciben lo que
habría visto un analista: ventas atendidas, inventario de cierre, pedidos abiertos con la
fecha esperada conocida en cada corte y promociones ya anunciadas.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import BODEGAS, DIAS_HISTORIA, ROTACIONES, Costos

SIN_ANUNCIO = 10**6

_CATALOGO = {
    "Aseo hogar": ["Detergente líquido 3 L", "Lavaloza 750 ml", "Limpiapisos lavanda 2 L",
                   "Suavizante 1,5 L", "Blanqueador 1 L", "Esponjas x6"],
    "Bebidas": ["Agua 600 ml x24", "Gaseosa cola 1,5 L x12", "Jugo de naranja 1 L",
                "Té frío 500 ml x12", "Bebida energizante x6", "Café molido 500 g"],
    "Snacks": ["Papas fritas 150 g", "Galletas de avena x12", "Maní salado 200 g",
               "Barras de cereal x6", "Chocolatina x24", "Mix de frutos secos 250 g"],
    "Cuidado personal": ["Shampoo 400 ml", "Jabón en barra x3", "Crema dental 150 ml",
                         "Desodorante roll-on", "Crema corporal 400 ml", "Protector solar FPS 50"],
    "Mascotas": ["Alimento perro 8 kg", "Alimento gato 3 kg", "Arena para gato 10 kg",
                 "Snack para perro 200 g", "Pipeta antipulgas", "Champú para mascotas 500 ml"],
}
_ROT = {
    "Aseo hogar": "AAAMMI", "Bebidas": "AAAMMI", "Snacks": "AAMMII",
    "Cuidado personal": "AAMMII", "Mascotas": "AAMMII",
}
_PERFIL_SEMANA = {  # lunes..domingo
    "Aseo hogar": [1.05, 0.95, 0.95, 1.0, 1.1, 1.25, 0.7],
    "Bebidas": [0.85, 0.85, 0.9, 1.0, 1.25, 1.35, 0.8],
    "Snacks": [0.9, 0.9, 0.95, 1.0, 1.2, 1.3, 0.75],
    "Cuidado personal": [1.1, 1.0, 1.0, 1.0, 1.05, 1.15, 0.7],
    "Mascotas": [1.15, 1.0, 0.95, 0.95, 1.0, 1.2, 0.75],
}


def generar_catalogo(rng: np.random.Generator) -> pd.DataFrame:
    filas = []
    n = 0
    for categoria, nombres in _CATALOGO.items():
        for nombre, r in zip(nombres, _ROT[categoria]):
            n += 1
            rot = {"A": "alta", "M": "media", "I": "intermitente"}[r]
            if rot == "alta":
                base, costo, multiplo = rng.uniform(18, 55), rng.uniform(6_000, 40_000), 10
                prob, tam = 1.0, 0.0
            elif rot == "media":
                base, costo, multiplo = rng.uniform(5, 14), rng.uniform(15_000, 90_000), 5
                prob, tam = 1.0, 0.0
            else:
                prob, tam = rng.uniform(0.15, 0.35), rng.uniform(3, 8)
                base, costo, multiplo = prob * tam, rng.uniform(60_000, 250_000), 1
            costo = round(costo, -2)
            margen = round(costo * rng.uniform(0.25, 0.55), -2)
            minimo = int(max(multiplo, np.ceil(base * 8 / multiplo) * multiplo))
            filas.append(dict(
                producto_id=f"SKU-{n:03d}", nombre=nombre, categoria=categoria, rotacion=rot,
                costo_unitario=costo, margen_unitario=margen, precio=costo + margen,
                plazo_normal=int(rng.integers(4, 8)), minimo_compra=minimo, multiplo=multiplo,
                demanda_base=base, prob_venta=prob, tam_venta=tam,
            ))
    return pd.DataFrame(filas)


def generar_demanda(cat: pd.DataFrame, rng: np.random.Generator, dias: int):
    """Devuelve demanda[T,P,B], promo[T,P,B] (descuento), anuncio[T,P,B], tabla de promociones y media[P,B]."""
    P, B = len(cat), len(BODEGAS)
    t = np.arange(dias)
    ciudad = np.stack([rng.uniform(0.9, 1.4, P), rng.uniform(0.6, 1.1, P)], axis=1)
    amp, fase = rng.uniform(0.05, 0.18, P), rng.uniform(0, 365, P)
    crec = rng.uniform(-0.10, 0.25, P)
    elast = rng.uniform(3.0, 5.5, P)
    disp = np.where(cat.rotacion.values == "alta", rng.uniform(6, 14, P), rng.uniform(3, 6, P))

    promo = np.zeros((dias, P, B))
    anuncio = np.full((dias, P, B), SIN_ANUNCIO, dtype=np.int64)
    promos = []
    altas = np.flatnonzero(cat.rotacion.values == "alta")
    medias = np.flatnonzero(cat.rotacion.values == "media")
    con_promo = list(rng.choice(altas, 7, replace=False)) + list(rng.choice(medias, 4, replace=False))
    estrella = int(altas[3])  # promoción fija al inicio de la demo: solo Bogotá

    def registrar(p, bodegas, ini, dur, desc):
        fin = min(ini + dur - 1, dias - 1)
        for b in bodegas:
            promo[ini:fin + 1, p, b] = desc
            anuncio[ini:fin + 1, p, b] = ini - 14
            promos.append(dict(p=p, b=b, inicio=ini, fin=fin, descuento=desc, anuncio=ini - 14))

    for p in con_promo:
        ini = int(rng.integers(20, 60))
        while ini < dias - 8:
            dur = int(rng.integers(5, 9))
            desc = float(rng.choice([0.10, 0.15, 0.20, 0.25]))
            bodegas = [[0, 1], [0, 1], [0], [1]][int(rng.integers(0, 4))]
            cerca_estrella = p == estrella and abs(ini - DIAS_HISTORIA) < 40
            if not cerca_estrella:
                registrar(p, bodegas, ini, dur, desc)
            ini += dur + int(rng.integers(55, 100))
    if DIAS_HISTORIA + 8 < dias:
        registrar(estrella, [0], DIAS_HISTORIA + 2, 6, 0.25)

    perfil = np.array([_PERFIL_SEMANA[c] for c in cat.categoria])  # [P,7]
    forma = (perfil[:, t % 7].T[:, :, None]
             * (1 + amp[None, :, None] * np.sin(2 * np.pi * (t[:, None, None] + fase[None, :, None]) / 365))
             * (1 + crec[None, :, None] * t[:, None, None] / dias)
             * (1 + elast[None, :, None] * promo)
             * ciudad[None, :, :])
    lam = cat.demanda_base.values[None, :, None] * forma

    demanda = np.zeros((dias, P, B), dtype=np.int64)
    for p in range(P):
        if cat.rotacion.iloc[p] == "intermitente":
            prob = np.clip(cat.prob_venta.iloc[p] * forma[:, p, :], 0, 0.95)
            ocurre = rng.random((dias, B)) < prob
            tam = 1 + rng.poisson(cat.tam_venta.iloc[p] - 1, (dias, B))
            demanda[:, p, :] = ocurre * tam
        else:
            k = disp[p]
            demanda[:, p, :] = rng.poisson(rng.gamma(k, lam[:, p, :] / k))
    media = lam[:DIAS_HISTORIA].mean(axis=0)
    return demanda, promo, anuncio, pd.DataFrame(promos), media


def generar_retrasos(rng: np.random.Generator, dias: int, P: int) -> np.ndarray:
    """Retraso (días) de un pedido normal según el día en que se pide. Igual para todas las políticas."""
    return rng.choice([0, 1, 2, 4], size=(dias, P, len(BODEGAS)), p=[0.72, 0.15, 0.09, 0.04])


@dataclass
class Accion:
    tipo: str            # compra_normal | compra_urgente | traslado
    p: int
    b: int               # bodega que recibe
    cantidad: int
    origen: int = -1     # bodega que despacha (solo traslados)
    ref: str = ""        # identificador de la solicitud que la originó


@dataclass
class Envio:
    id: int
    tipo: str
    p: int
    b: int
    cantidad: int
    dia_pedido: int
    dia_esperado: int    # fecha prometida al pedir
    dia_real: int        # fecha en que realmente llega (oculta hasta la víspera de la fecha prometida)
    origen: int = -1
    ref: str = ""

    def eta_conocida(self, corte: int) -> int:
        """Fecha esperada que se conocía al cierre del día `corte` (el proveedor avisa el retraso la víspera)."""
        return self.dia_esperado if corte < self.dia_esperado - 1 else self.dia_real


@dataclass
class Observacion:
    """Lo que ve el analista al cierre del día `corte`. No incluye la demanda no atendida."""
    ventas: np.ndarray    # [corte+1, S]
    cierre: np.ndarray    # [corte+1, S] inventario al cierre de cada día
    promo: np.ndarray     # [T, S] calendario de descuentos
    anuncio: np.ndarray   # [T, S] día en que se anunció cada promoción
    prod: np.ndarray      # [S]
    bod: np.ndarray
    categ: np.ndarray
    rot: np.ndarray

    @property
    def corte(self) -> int:
        return len(self.ventas) - 1

    def promo_conocida(self) -> np.ndarray:
        return np.where(self.anuncio <= self.corte, self.promo, 0.0)


def meta_series(cat: pd.DataFrame) -> dict:
    """Atributos por serie (producto x bodega). Serie s = p * 2 + b."""
    B = len(BODEGAS)
    rep = lambda v: np.repeat(np.asarray(v), B)
    return dict(
        prod=rep(np.arange(len(cat))), bod=np.tile(np.arange(B), len(cat)),
        categ=rep(pd.factorize(cat.categoria)[0]),
        rot=rep([ROTACIONES.index(r) for r in cat.rotacion]),
        costo=rep(cat.costo_unitario.values.astype(float)),
        margen=rep(cat.margen_unitario.values.astype(float)),
        plazo=rep(cat.plazo_normal.values.astype(int)),
        minimo=rep(cat.minimo_compra.values.astype(int)),
        multiplo=rep(cat.multiplo.values.astype(int)),
    )


@dataclass
class Mundo:
    cat: pd.DataFrame
    demanda: np.ndarray
    promo: np.ndarray
    anuncio: np.ndarray
    retrasos: np.ndarray
    costos: Costos
    stock: np.ndarray
    dia: int = 0                      # próximo día por simular
    envios: list = field(default_factory=list)
    recibidos: list = field(default_factory=list)   # (Envio, día de llegada)
    diario: list = field(default_factory=list)
    instantaneas: list = field(default_factory=list)  # pedidos abiertos tal como se conocían en cada corte
    seq: int = 0

    def __post_init__(self):
        self.T, self.P, self.B = self.demanda.shape
        self.S = self.P * self.B
        if not hasattr(self, "ventas"):
            self.ventas = np.zeros_like(self.demanda)
            self.cierre = np.zeros_like(self.demanda)
            self.perdidas = np.zeros_like(self.demanda)
        self.meta = meta_series(self.cat)

    def copiar(self) -> "Mundo":
        return copy.deepcopy(self)

    def observar(self) -> Observacion:
        t, m = self.dia, self.meta
        return Observacion(
            ventas=self.ventas[:t].reshape(t, self.S), cierre=self.cierre[:t].reshape(t, self.S),
            promo=self.promo.reshape(self.T, self.S), anuncio=self.anuncio.reshape(self.T, self.S),
            prod=m["prod"], bod=m["bod"], categ=m["categ"], rot=m["rot"],
        )

    def recepciones_conocidas(self, horizonte: int) -> tuple[np.ndarray, np.ndarray]:
        """Recepciones esperadas por serie para los días 1..horizonte, con la fecha conocida al último cierre.

        Devuelve (central, alto). En el escenario alto, las compras normales que el proveedor aún no ha
        confirmado (llegan del día 2 en adelante) se asumen `retraso_alto` días tarde.
        """
        rec, rec_alto = np.zeros((self.S, horizonte)), np.zeros((self.S, horizonte))
        for e in self.envios:
            d = max(e.eta_conocida(self.dia - 1) - self.dia, 0)  # 0 = llega hoy (día 1)
            tarde = d + (self.costos.retraso_alto if e.tipo == "compra_normal" and d >= 1 else 0)
            if d < horizonte:
                rec[e.p * self.B + e.b, d] += e.cantidad
            if tarde < horizonte:
                rec_alto[e.p * self.B + e.b, tarde] += e.cantidad
        return rec, rec_alto

    def _envio(self, a: Accion, esperado: int, real: int) -> None:
        self.seq += 1
        self.envios.append(Envio(self.seq, a.tipo, a.p, a.b, int(a.cantidad), self.dia, esperado, real, a.origen, a.ref))

    def paso(self, acciones: list[Accion]) -> dict:
        """Simula un día: aplica las acciones de la mañana, recibe mercancía, atiende la demanda."""
        t, c = self.dia, self.costos
        costo_u = self.cat.costo_unitario.values
        margen_u = self.cat.margen_unitario.values
        fila = dict(dia=t, costo_pedidos=0.0, costo_urgencias=0.0, costo_traslados=0.0,
                    n_normales=0, n_urgentes=0, n_traslados=0, u_urgentes=0, u_traslados=0)
        for a in acciones:
            if a.cantidad <= 0:
                continue
            if a.tipo == "compra_normal":
                plazo = int(self.cat.plazo_normal.iloc[a.p])
                self._envio(a, t + plazo, t + plazo + int(self.retrasos[t, a.p, a.b]))
                fila["costo_pedidos"] += c.costo_pedido_normal
                fila["n_normales"] += 1
            elif a.tipo == "compra_urgente":
                self._envio(a, t + c.plazo_urgente, t + c.plazo_urgente)
                fila["costo_urgencias"] += c.costo_fijo_urgente + c.recargo_urgente * costo_u[a.p] * a.cantidad
                fila["n_urgentes"] += 1
                fila["u_urgentes"] += a.cantidad
            elif a.tipo == "traslado":
                a.cantidad = int(min(a.cantidad, self.stock[a.p, a.origen]))
                if a.cantidad <= 0:
                    continue
                self.stock[a.p, a.origen] -= a.cantidad
                self._envio(a, t + c.plazo_traslado, t + c.plazo_traslado)
                fila["costo_traslados"] += c.costo_fijo_traslado + c.costo_unidad_traslado * a.cantidad
                fila["n_traslados"] += 1
                fila["u_traslados"] += a.cantidad
            else:
                raise ValueError(f"Acción desconocida: {a.tipo}")

        pendientes = []
        for e in self.envios:
            if e.dia_real <= t:
                self.stock[e.p, e.b] += e.cantidad
                self.recibidos.append((e, t))
            else:
                pendientes.append(e)
        self.envios = pendientes

        d = self.demanda[t]
        v = np.minimum(self.stock, d)
        self.stock = self.stock - v
        self.ventas[t], self.cierre[t], self.perdidas[t] = v, self.stock, d - v
        for e in self.envios:
            self.instantaneas.append((t, e.id, e.tipo, e.p, e.b, e.cantidad, e.dia_pedido, e.eta_conocida(t)))

        valor = float((self.stock * costo_u[:, None]).sum())
        fila.update(
            demanda=int(d.sum()), ventas=int(v.sum()), perdidas=int((d - v).sum()),
            quiebres=int(((d - v) > 0).sum()), valor_inventario=valor,
            costo_mantener=valor * c.tasa_mantener_anual / 365,
            margen=float((v * margen_u[:, None]).sum()),
            margen_perdido=float(((d - v) * margen_u[:, None]).sum()),
        )
        self.diario.append(fila)
        self.dia += 1
        return fila


def crear_mundo(semilla: int, dias: int, costos: Costos | None = None) -> tuple[Mundo, pd.DataFrame]:
    rng = np.random.default_rng(semilla)
    cat = generar_catalogo(rng)
    demanda, promo, anuncio, promos, media = generar_demanda(cat, rng, dias)
    retrasos = generar_retrasos(rng, dias, len(cat))
    stock = np.ceil(media * 12).astype(np.int64)
    mundo = Mundo(cat, demanda, promo, anuncio, retrasos, costos or Costos(), stock)
    mundo.media_inicial = media.reshape(-1)
    return mundo, promos
