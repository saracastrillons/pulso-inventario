"""Aprobaciones: una sola solicitud por recomendación y nunca más unidades de las que hay."""
import pytest

from conftest import crear_recomendacion
from pulso.aprobacion import RecomendacionNoVigente, SaldoInsuficiente, aprobar, rechazar


def fijar_origen(con, disponible, seguridad):
    con.execute("UPDATE inventario SET disponible = ?, reservado = 0, stock_seguridad = ? "
                "WHERE producto_id = 'SKU-001' AND bodega_id = 'MED'", (disponible, seguridad))


def reservado(con):
    return con.execute(
        "SELECT reservado FROM inventario WHERE producto_id = 'SKU-001' AND bodega_id = 'MED'").fetchone()[0]


def test_aprobacion_repetida_devuelve_la_misma_solicitud(demo):
    _, con, _ = demo
    fijar_origen(con, 100, 20)
    crear_recomendacion(con, "R-X-1", "traslado", 50, origen="MED")
    primera = aprobar(con, "R-X-1")
    segunda = aprobar(con, "R-X-1")                   # doble clic o reintento tras un error
    assert primera["solicitud_id"] == segunda["solicitud_id"] and segunda["repetida"]
    assert con.execute("SELECT COUNT(*) FROM solicitudes WHERE rec_id = 'R-X-1'").fetchone()[0] == 1
    assert reservado(con) == 50                       # las unidades se reservan una sola vez
    assert con.execute("SELECT COUNT(*) FROM decisiones WHERE rec_id = 'R-X-1'").fetchone()[0] == 1


def test_dos_solicitudes_compiten_por_el_mismo_saldo(demo):
    _, con, _ = demo
    fijar_origen(con, 100, 20)
    crear_recomendacion(con, "R-X-1", "traslado", 50, origen="MED")
    crear_recomendacion(con, "R-X-2", "traslado", 50, origen="MED")
    aprobar(con, "R-X-1")
    with pytest.raises(SaldoInsuficiente, match="Recalcula"):
        aprobar(con, "R-X-2")                         # quedarían 0 libres y hay que conservar 20
    assert reservado(con) == 50
    assert con.execute("SELECT estado FROM recomendaciones WHERE rec_id = 'R-X-2'").fetchone()[0] == "pendiente"
    assert con.execute("SELECT COUNT(*) FROM solicitudes").fetchone()[0] == 1


def test_la_solicitud_guarda_trazabilidad(demo):
    _, con, _ = demo
    fijar_origen(con, 100, 20)
    llegada = crear_recomendacion(con, "R-X-1", "traslado", 50, origen="MED")
    sol = aprobar(con, "R-X-1", cantidad=40)
    assert (sol["origen"], sol["destino"], sol["cantidad"], sol["fecha_esperada"]) == ("MED", "BOG", 40, llegada)
    assert sol["solicitud_id"].startswith("TR-")
    d = con.execute("SELECT * FROM decisiones WHERE rec_id = 'R-X-1'").fetchone()
    assert d["decision"] == "aprobada con ajuste" and d["cantidad_sugerida"] == 50 and d["cantidad_final"] == 40
    assert d["version_pronostico"] and d["lote_id"] and "origen_al_aprobar" in d["datos_json"]


def test_rechazar_exige_motivo_y_no_crea_solicitud(demo):
    _, con, _ = demo
    crear_recomendacion(con, "R-X-1", "compra_urgente", 10)
    with pytest.raises(ValueError):
        rechazar(con, "R-X-1", "  ")
    rechazar(con, "R-X-1", "El cliente canceló el pedido")
    assert con.execute("SELECT COUNT(*) FROM solicitudes").fetchone()[0] == 0
    with pytest.raises(RecomendacionNoVigente):
        aprobar(con, "R-X-1")
