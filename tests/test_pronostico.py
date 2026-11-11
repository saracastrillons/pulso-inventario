"""El pronóstico solo puede usar lo que se sabía en la fecha de corte."""
import numpy as np

from pulso.config import HMAX
from pulso.mundo import Envio, Observacion
from pulso.pronostico import ajustar_ventas, construir_variables, referencia_sencilla


def recortar(obs, corte):
    return Observacion(obs.ventas[:corte + 1], obs.cierre[:corte + 1], obs.promo, obs.anuncio,
                       obs.prod, obs.bod, obs.categ, obs.rot)


def test_las_variables_no_usan_datos_posteriores_al_corte(mundo_base):
    obs = mundo_base[0].observar()
    corte = 90
    alterado = Observacion(obs.ventas.copy(), obs.cierre.copy(), obs.promo, obs.anuncio,
                           obs.prod, obs.bod, obs.categ, obs.rot)
    alterado.ventas[corte + 1:] += 500               # cambia todo el futuro
    alterado.cierre[corte + 1:] = 0
    variables = []
    for o in (obs, alterado):
        adj = ajustar_ventas(o.ventas, o.cierre)
        promo = np.where(o.anuncio <= corte, o.promo, 0.0)
        variables.append(construir_variables(adj, o.cierre <= 0, promo, o, corte, HMAX))
    assert np.array_equal(variables[0], variables[1])


def test_el_pronostico_no_cambia_si_cambia_el_futuro(mundo_base, pron):
    obs = mundo_base[0].observar()
    a = pron.predecir(recortar(obs, 90))
    futuro = Observacion(obs.ventas * 3, obs.cierre, obs.promo, obs.anuncio, obs.prod, obs.bod, obs.categ, obs.rot)
    futuro.ventas[:91] = obs.ventas[:91]
    b = pron.predecir(recortar(futuro, 90))
    assert np.allclose(a[0], b[0]) and np.allclose(a[1], b[1])


def test_promocion_no_anunciada_no_es_visible(mundo_base):
    obs = recortar(mundo_base[0].observar(), 50)
    obs.promo = np.zeros_like(obs.promo)
    obs.anuncio = np.full_like(obs.anuncio, 10**6)
    obs.promo[60, 0], obs.anuncio[60, 0] = 0.2, 55    # se anunciará el día 55, después del corte
    assert obs.promo_conocida()[60, 0] == 0
    obs.anuncio[60, 0] = 50                           # anunciada el mismo día del corte
    assert obs.promo_conocida()[60, 0] == 0.2


def test_dia_con_quiebre_no_se_toma_como_demanda_cero():
    ventas = np.full((36, 1), 10.0)
    cierre = np.full((36, 1), 50.0)
    ventas[35], cierre[35] = 0, 0                    # se agotó: vender cero no significa que nadie quiso comprar
    adj = ajustar_ventas(ventas, cierre)
    assert adj[35, 0] == 10
    assert referencia_sencilla(adj, 35, 7)[6, 0] == 10
    ventas[20] = 0                                   # cero con inventario disponible: sí es demanda cero
    assert ajustar_ventas(ventas, cierre)[20, 0] == 0


def test_el_escenario_alto_no_queda_por_debajo_del_central(mundo_base, pron):
    central, alto = pron.predecir(mundo_base[0].observar())
    assert (alto.cumsum(0) >= central.cumsum(0) - 1e-9).all()


def test_el_retraso_del_proveedor_solo_se_conoce_la_vispera():
    e = Envio(1, "compra_normal", 0, 0, 100, dia_pedido=10, dia_esperado=16, dia_real=18)
    assert e.eta_conocida(13) == 16                  # el día 13 nadie sabía del retraso
    assert e.eta_conocida(15) == 18
