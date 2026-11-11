"""Pronóstico de demanda: referencia sencilla, modelo de aprendizaje automático y escenario alto.

Todas las funciones reciben los datos disponibles hasta un corte y pronostican los días
siguientes. Nada posterior al corte entra en las variables (ver tests/test_pronostico.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .config import HMAX, ROTACIONES, fecha
from .mundo import Observacion

COLUMNAS = ["horizonte", "dia_semana", "producto", "bodega", "categoria", "rotacion",
            "ref_sencilla", "ultimo_mismo_dia", "media_7", "media_28", "media_56", "ultimo",
            "frac_cero_56", "frac_quiebre_28", "promo_objetivo", "frac_promo_28", "frac_promo_ref"]
CATEGORICAS = [1, 2, 3, 4, 5]
VALIDACION = 56


def ajustar_ventas(ventas: np.ndarray, cierre: np.ndarray) -> np.ndarray:
    """Corrige los días con quiebre: si la bodega cerró en cero, la venta es un piso de la demanda.

    En esos días se usa el mayor valor entre la venta registrada y el promedio de los cuatro
    días equivalentes anteriores. El ajuste de cada día solo usa días anteriores.
    """
    adj = ventas.astype(float).copy()
    quiebre = cierre <= 0
    for t in np.flatnonzero(quiebre.any(axis=1)):
        ant = [t - 7 * k for k in range(1, 5) if t - 7 * k >= 0]
        if ant:
            adj[t] = np.where(quiebre[t], np.maximum(adj[t], adj[ant].mean(axis=0)), adj[t])
    return adj


def _dias_equivalentes(corte: int, objetivo: int) -> list[int]:
    return [d for d in (objetivo - 7 * k for k in range(1, 9)) if 0 <= d <= corte][:4]


def referencia_sencilla(adj: np.ndarray, corte: int, horizonte: int) -> np.ndarray:
    """Promedio de los últimos cuatro días equivalentes de la semana. Devuelve [horizonte, S]."""
    return np.stack([adj[_dias_equivalentes(corte, corte + h)].mean(axis=0) for h in range(1, horizonte + 1)])


def construir_variables(adj, quiebre, promo, obs: Observacion, corte: int, horizonte: int) -> np.ndarray:
    """Variables para pronosticar corte+1..corte+horizonte. `promo` es el calendario conocido al corte."""
    hist = adj[:corte + 1]
    S = hist.shape[1]
    fijo = [hist[-7:].mean(0), hist[-28:].mean(0), hist[-56:].mean(0), hist[-1],
            (hist[-56:] == 0).mean(0), quiebre[max(0, corte - 27):corte + 1].mean(0)]
    frac_promo = (promo[max(0, corte - 27):corte + 1] > 0).mean(0)
    bloques = []
    for h in range(1, horizonte + 1):
        obj = corte + h
        eq = _dias_equivalentes(corte, obj)
        bloques.append(np.column_stack([
            np.full(S, h), np.full(S, obj % 7), obs.prod, obs.bod, obs.categ, obs.rot,
            hist[eq].mean(0), hist[eq[0]], *fijo,
            promo[obj], frac_promo, (promo[eq] > 0).mean(0),
        ]))
    return np.vstack(bloques).astype(float)


def _nuevo_modelo() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="poisson", max_iter=200, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=40,
        l2_regularization=1.0, categorical_features=CATEGORICAS, random_state=0)


class Pronosticador:
    """metodo='sencillo' usa solo la referencia. metodo='ml' usa el modelo y, en los segmentos de
    rotación donde la referencia fue mejor en validación, se queda con la referencia."""

    def __init__(self, metodo: str = "ml"):
        assert metodo in ("sencillo", "ml")
        self.metodo = metodo
        self.modelo = None
        self.usa_ml = np.zeros(len(ROTACIONES), dtype=bool)
        self.q90 = np.zeros((len(ROTACIONES), HMAX))
        self.validacion: dict = {}
        self.version = "sin entrenar"

    # ---------- entrenamiento ----------
    def _ajustar_modelo(self, obs, adj, quiebre, ultimo: int, paso: int = 2):
        """Entrena con objetivos hasta el día `ultimo` (inclusive)."""
        X, y = [], []
        for c in range(VALIDACION, ultimo, paso):
            h = min(HMAX, ultimo - c)
            promo_c = np.where(obs.anuncio <= c, obs.promo, 0.0)
            X.append(construir_variables(adj, quiebre, promo_c, obs, c, h))
            y.append(adj[c + 1:c + h + 1].reshape(-1))
        return _nuevo_modelo().fit(np.vstack(X), np.concatenate(y))

    def _central(self, modelo, obs, adj, quiebre, corte: int) -> np.ndarray:
        base = referencia_sencilla(adj, corte, HMAX)
        if modelo is None or not self.usa_ml.any():
            return base
        promo_c = np.where(obs.anuncio <= corte, obs.promo, 0.0)
        X = construir_variables(adj, quiebre, promo_c, obs, corte, HMAX)
        ml = np.clip(modelo.predict(X), 0, None).reshape(HMAX, -1)
        return np.where(self.usa_ml[obs.rot][None, :], ml, base)

    def entrenar(self, obs: Observacion) -> "Pronosticador":
        """Valida avanzando por fechas en los últimos 56 días, calibra el escenario alto y reentrena con todo."""
        corte = obs.corte
        adj = ajustar_ventas(obs.ventas, obs.cierre)
        quiebre = obs.cierre <= 0
        ini_val = corte - VALIDACION
        cortes = list(range(ini_val, corte - HMAX + 1))
        real = np.stack([adj[c + 1:c + HMAX + 1] for c in cortes])            # [n, H, S]
        base = np.stack([referencia_sencilla(adj, c, HMAX) for c in cortes])

        mae = {"sencillo": [float(np.abs(base - real)[:, :, obs.rot == r].mean()) for r in range(len(ROTACIONES))]}
        elegido = base
        if self.metodo == "ml":
            m_val = self._ajustar_modelo(obs, adj, quiebre, ini_val)
            self.usa_ml[:] = True
            ml = np.stack([self._central(m_val, obs, adj, quiebre, c) for c in cortes])
            mae["ml"] = [float(np.abs(ml - real)[:, :, obs.rot == r].mean()) for r in range(len(ROTACIONES))]
            self.usa_ml = np.array(mae["ml"]) <= np.array(mae["sencillo"])
            elegido = np.where(self.usa_ml[obs.rot][None, None, :], ml, base)

        # Escenario alto: cuantil 90 del error estandarizado de la demanda ACUMULADA a k días.
        acum_p, acum_r = elegido.cumsum(axis=1), real.cumsum(axis=1)
        z = (acum_r - acum_p) / np.sqrt(acum_p + 1)
        for r in range(len(ROTACIONES)):
            self.q90[r] = np.maximum(np.quantile(z[:, :, obs.rot == r], 0.9, axis=(0, 2)), 0)

        self.validacion = {"mae": mae, "usa_ml": {ROTACIONES[r]: bool(self.usa_ml[r]) for r in range(len(ROTACIONES))}}
        if self.metodo == "ml" and self.usa_ml.any():
            self.modelo = self._ajustar_modelo(obs, adj, quiebre, corte)
        self.version = f"{'hgb' if self.metodo == 'ml' else 'sencillo'}-v1-corte-{fecha(corte).isoformat()}"
        return self

    # ---------- predicción ----------
    def predecir(self, obs: Observacion) -> tuple[np.ndarray, np.ndarray]:
        """Devuelve (central, alto), cada uno [HMAX, S], para los días corte+1..corte+HMAX."""
        adj = ajustar_ventas(obs.ventas, obs.cierre)
        central = self._central(self.modelo, obs, adj, obs.cierre <= 0, obs.corte)
        acum = central.cumsum(axis=0)
        acum_alto = np.maximum.accumulate(acum + self.q90[obs.rot].T * np.sqrt(acum + 1), axis=0)
        alto = np.diff(acum_alto, axis=0, prepend=0.0)
        return central, alto

    def metodo_por_serie(self, obs: Observacion) -> list[str]:
        return ["ml" if (self.modelo is not None and self.usa_ml[r]) else "sencillo" for r in obs.rot]


def evaluar(mundo, pronosticadores: dict, c0: int, c1: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evalúa avanzando por fechas entre los cortes c0..c1 contra la demanda total (que el modelo nunca vio).

    Devuelve (error por método/rotación/horizonte, cobertura del escenario alto).
    """
    S = mundo.S
    demanda = mundo.demanda.reshape(mundo.T, S)
    completo = Observacion(
        mundo.ventas.reshape(mundo.T, S), mundo.cierre.reshape(mundo.T, S),
        mundo.promo.reshape(mundo.T, S), mundo.anuncio.reshape(mundo.T, S),
        mundo.meta["prod"], mundo.meta["bod"], mundo.meta["categ"], mundo.meta["rot"])
    grupos = {"1-3 días": slice(0, 3), "4-7 días": slice(3, 7), f"8-{HMAX} días": slice(7, HMAX)}
    err, cob = [], []
    for nombre, pron in pronosticadores.items():
        cen, alt, real = [], [], []
        for c in range(c0, c1 - HMAX + 1):
            obs = Observacion(completo.ventas[:c + 1], completo.cierre[:c + 1], completo.promo, completo.anuncio,
                              completo.prod, completo.bod, completo.categ, completo.rot)
            a, b = pron.predecir(obs)
            cen.append(a); alt.append(b); real.append(demanda[c + 1:c + HMAX + 1])
        cen, alt, real = np.stack(cen), np.stack(alt), np.stack(real)
        for r, rot in enumerate(ROTACIONES + ["todas"]):
            m = np.ones(S, bool) if rot == "todas" else completo.rot == r
            for g, sl in grupos.items():
                e = (cen - real)[:, sl][:, :, m]
                err.append(dict(metodo=nombre, rotacion=rot, horizonte=g, mae=float(np.abs(e).mean()),
                                sesgo=float(e.mean()), demanda_media=float(real[:, sl][:, :, m].mean())))
            for k in (5, 8):
                ar, aa, ac = (x[:, :k].sum(axis=1)[:, m] for x in (real, alt, cen))
                cob.append(dict(metodo=nombre, rotacion=rot, dias=k, cobertura_alto=float((ar <= aa).mean()),
                                bajo_central=float((ar <= ac).mean())))
    return pd.DataFrame(err), pd.DataFrame(cob)
