"""Proceso diario: validación de archivos, reproceso sin duplicar y entregas en la fecha prevista."""
from conftest import crear_recomendacion
from pulso.aprobacion import aprobar
from pulso.demo import escribir_archivos, procesar_dia_siguiente, rutas
from pulso.ingesta import procesar_lote

TABLAS = ("ventas", "inventario", "pedidos", "pedidos_por_corte", "pronosticos", "recomendaciones", "lotes")


def contar(con):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLAS}


def test_reprocesar_el_mismo_lote_deja_un_solo_juego_de_resultados(demo, pron):
    base, con, mundo = demo
    antes = contar(con)
    salida = procesar_lote(con, escribir_archivos(mundo, rutas(base)["entrada"]), mundo.dia - 1, pron)
    assert salida["estado"] == "ok" and contar(con) == antes
    assert con.execute("SELECT intentos FROM lotes").fetchone()[0] == 2


def test_reprocesar_conserva_las_decisiones_ya_tomadas(demo, pron):
    base, con, mundo = demo
    crear_recomendacion(con, "R-X-1", "compra_urgente", 10)
    aprobar(con, "R-X-1")
    procesar_lote(con, escribir_archivos(mundo, rutas(base)["entrada"]), mundo.dia - 1, pron)
    assert con.execute("SELECT estado FROM recomendaciones WHERE rec_id = 'R-X-1'").fetchone()[0] == "aprobada"
    assert con.execute("SELECT COUNT(*) FROM solicitudes").fetchone()[0] == 1


def test_archivo_danado_no_reemplaza_la_ultima_corrida_buena(demo):
    base, con, _ = demo
    bueno = con.execute("SELECT lote_id FROM lotes").fetchone()[0]
    pendientes = con.execute("SELECT COUNT(*) FROM recomendaciones WHERE estado = 'pendiente'").fetchone()[0]
    salida = procesar_dia_siguiente(base, defecto="columna")
    assert salida["estado"] == "fallido" and "unidades" in salida["detalle"]
    assert [r[0] for r in con.execute("SELECT lote_id FROM lotes WHERE estado = 'ok'")] == [bueno]
    assert con.execute("SELECT COUNT(*) FROM recomendaciones WHERE estado = 'pendiente' AND lote_id = ?",
                       (bueno,)).fetchone()[0] == pendientes
    # Al reintentar con el archivo corregido se publica el corte que había fallado, sin saltarse un día.
    reintento = procesar_dia_siguiente(base)
    assert reintento["estado"] == "ok" and reintento["fecha_corte"] == salida["fecha_corte"]


def test_filas_sucias_se_rechazan_y_quedan_registradas(demo):
    base, con, _ = demo
    for _ in range(3):                                 # en tres días seguidos hay al menos un duplicado
        assert procesar_dia_siguiente(base)["estado"] == "ok"
    motivos = {r[0] for r in con.execute("SELECT motivo FROM filas_rechazadas")}
    assert "Fila duplicada" in motivos
    series = con.execute("SELECT COUNT(*) FROM productos").fetchone()[0] * 2
    assert all(n == series for (n,) in con.execute("SELECT COUNT(*) FROM ventas GROUP BY fecha"))


def test_la_solicitud_aprobada_llega_en_la_fecha_prevista(demo):
    base, con, _ = demo
    crear_recomendacion(con, "R-X-1", "compra_urgente", 10)
    sol = aprobar(con, "R-X-1")
    procesar_dia_siguiente(base)
    estado = lambda: tuple(con.execute("SELECT estado, fecha_recepcion FROM solicitudes").fetchone())
    assert estado() == ("en_transito", None)
    procesar_dia_siguiente(base)
    assert estado() == ("recibida", sol["fecha_esperada"])


def test_el_traslado_aprobado_sale_del_origen_y_libera_la_reserva(demo):
    base, con, mundo = demo
    p = max(range(mundo.P), key=lambda i: mundo.stock[i, 1])       # la referencia con más inventario en Medellín
    sku = mundo.cat.producto_id.iloc[p]
    antes = int(mundo.stock[p, 1])
    con.execute("UPDATE inventario SET stock_seguridad = 0 WHERE producto_id = ?", (sku,))
    crear_recomendacion(con, "R-X-1", "traslado", 5, producto=sku, origen="MED")
    aprobar(con, "R-X-1")
    reserva = lambda: con.execute(
        "SELECT reservado FROM inventario WHERE producto_id = ? AND bodega_id = 'MED'", (sku,)).fetchone()[0]
    assert reserva() == 5
    procesar_dia_siguiente(base)
    assert reserva() == 0                              # ya salió físicamente: deja de estar reservado
    assert con.execute("SELECT estado FROM solicitudes").fetchone()[0] == "en_transito"
    assert antes > 5
