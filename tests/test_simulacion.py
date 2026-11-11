"""La comparación de políticas es justa y el inventario cuadra."""
import numpy as np

from pulso.simulacion import PoliticaMotor, PoliticaPuntoPedido, simular


def test_el_inventario_cuadra(mundo_base):
    m = mundo_base[0].copiar()
    inicial, n0 = m.stock.sum(), len(m.recibidos)
    diario = simular(m, PoliticaPuntoPedido(), 15)
    recibido = sum(e.cantidad for e, _ in m.recibidos[n0:])
    assert m.stock.sum() == inicial + recibido - diario.ventas.sum()
    assert (m.stock >= 0).all()


def test_las_politicas_enfrentan_la_misma_demanda(mundo_base, pron):
    a, b = mundo_base[0].copiar(), mundo_base[0].copiar()
    da = simular(a, PoliticaPuntoPedido(), 10)
    db = simular(b, PoliticaMotor(pron, "motor"), 10)
    assert da.demanda.tolist() == db.demanda.tolist()
    assert np.array_equal(a.retrasos, b.retrasos)


def test_el_motor_no_deja_inventario_negativo_al_trasladar(mundo_base, pron):
    m = mundo_base[0].copiar()
    simular(m, PoliticaMotor(pron, "motor"), 20)
    assert (m.stock >= 0).all() and (m.cierre >= 0).all()


def test_ventas_mas_perdidas_igual_a_demanda(mundo_base):
    m = mundo_base[0]
    t = m.dia
    assert (m.ventas[:t] <= m.demanda[:t]).all()
    assert (m.ventas[:t] + m.perdidas[:t] == m.demanda[:t]).all()
