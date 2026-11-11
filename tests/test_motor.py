"""Cálculos que cambian la decisión."""
import numpy as np

from pulso.config import Costos
from pulso.guia import caso_guia
from pulso.motor import Serie, decidir, proyectar

C = Costos()


def opcion(res, accion):
    return next(o for o in res.opciones if o.accion == accion)


def test_proyeccion_dia_a_dia():
    inv, falt = proyectar(90, np.array([0, 0, 40, 0, 0.0]), np.array([36.0] * 5))
    assert inv.tolist() == [54, 18, 22, 0, 0]
    assert falt.tolist() == [0, 0, 0, 14, 36]          # la venta no atendida se pierde, no se acumula


def test_caso_guia_recomienda_trasladar_50():
    res = decidir(caso_guia(), C)
    assert res.primer_faltante == 4
    assert round(res.esperar.faltante_central) == 50
    assert res.elegida.acciones == [("traslado", 50, 2)]
    assert round(res.elegida.faltante_central) == 0
    assert round(res.elegida.faltante_alto) == 20      # con demanda alta quedan 20 unidades expuestas
    assert res.elegida.costo_total < opcion(res, "compra_urgente").costo_total


def test_entrega_que_llega_despues_del_quiebre_no_cubre_el_faltante():
    res = decidir(caso_guia(), C)
    normal = opcion(res, "compra_normal")
    assert normal.acciones[0][2] == 7 and not normal.viable
    assert "después del faltante" in normal.motivo
    assert round(normal.faltante_central) == 50        # llega el día 7: el faltante de los días 4 y 5 sigue ahí
    assert normal.costo_total > res.esperar.costo_total


def test_origen_sin_excedente_descarta_el_traslado():
    res = decidir(caso_guia(excedente_origen=0), C)
    traslado = opcion(res, "traslado")
    assert not traslado.viable and "stock de seguridad" in traslado.motivo
    assert res.elegida.accion == "compra_urgente"


def test_traslado_limitado_por_capacidad_de_la_ruta():
    s = caso_guia(excedente_origen=500)
    s.capacidad = 30
    assert opcion(decidir(s, C), "traslado").acciones[0][1] == 30


def test_compra_normal_respeta_minimo_y_multiplo():
    s = Serie(stock=100, recepciones=np.zeros(8), central=np.full(8, 20.0), alto=np.full(8, 24.0), costo=20_000,
              margen=8_000, plazo_normal=6, minimo=150, multiplo=10)
    res = decidir(s, C)
    q = next(q for t, q, _ in res.elegida.acciones if t == "compra_normal")
    assert q >= 150 and q % 10 == 0


def test_sin_faltante_no_hay_decision():
    s = Serie(stock=1000, recepciones=np.zeros(8), central=np.full(8, 20.0), alto=np.full(8, 24.0), costo=20_000,
              margen=8_000, plazo_normal=6)
    assert not decidir(s, C).requiere_decision


def test_alargar_el_plazo_cambia_la_mejor_opcion():
    """Con plazo corto basta comprar normal; si el proveedor tarda más, hace falta un puente rápido."""
    def mejor(plazo):
        H = plazo + 2
        s = Serie(stock=100, recepciones=np.zeros(H), central=np.full(H, 20.0), alto=np.full(H, 22.0), costo=20_000,
                  margen=9_000, plazo_normal=plazo, minimo=100, multiplo=10, excedente_origen=200, capacidad=500)
        return {t for t, _, _ in decidir(s, C).elegida.acciones}
    assert mejor(4) == {"compra_normal"}
    assert "traslado" in mejor(8)
