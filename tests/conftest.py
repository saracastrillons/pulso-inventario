"""Demo pequeña (150 días, pronóstico sencillo) en una carpeta temporal para probar el flujo completo."""
import json
import sys
from pathlib import Path

import joblib
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pulso.config import fecha, indice
from pulso.db import cargar_catalogo, cargar_historia, conectar, crear_esquema
from pulso.demo import _guardar, escribir_archivos, rutas
from pulso.ingesta import procesar_lote
from pulso.mundo import crear_mundo
from pulso.pronostico import Pronosticador
from pulso.simulacion import PoliticaPuntoPedido, simular

DIAS = 120


@pytest.fixture(scope="session")
def mundo_base():
    mundo, promos = crear_mundo(1, 150)
    simular(mundo, PoliticaPuntoPedido(mundo.media_inicial), DIAS)
    return mundo, promos


@pytest.fixture(scope="session")
def pron(mundo_base):
    return Pronosticador("sencillo").entrenar(mundo_base[0].observar())


@pytest.fixture
def demo(tmp_path, mundo_base, pron):
    """Devuelve (carpeta base, conexión, mundo) con el primer lote ya publicado."""
    mundo, promos = mundo_base[0].copiar(), mundo_base[1]
    r = rutas(tmp_path)
    con = conectar(r["db"])
    crear_esquema(con)
    cargar_catalogo(con, mundo.cat, promos, mundo.costos)
    cargar_historia(con, mundo, DIAS)
    r["pron"].parent.mkdir(parents=True)
    joblib.dump(dict(vivo=pron), r["pron"])
    _guardar(mundo, r["mundo"])
    salida = procesar_lote(con, escribir_archivos(mundo, r["entrada"]), mundo.dia - 1, pron)
    assert salida["estado"] == "ok"
    yield tmp_path, con, mundo
    con.close()


def crear_recomendacion(con, rec_id, accion, cantidad, producto="SKU-001", destino="BOG", origen=None, llegada_en=2):
    """Inserta una recomendación pendiente en el lote vigente y devuelve su fecha de llegada."""
    lote = con.execute("SELECT * FROM lotes ORDER BY fecha_corte DESC LIMIT 1").fetchone()
    llegada = fecha(indice(lote["fecha_corte"]) + llegada_en).isoformat()
    con.execute(f"INSERT INTO recomendaciones VALUES ({','.join('?' * 18)})",
                (rec_id, lote["lote_id"], producto, destino, accion, cantidad, origen, llegada, llegada, 0, 1, 10.0,
                 20.0, 1000.0, 500.0, 500.0, "pendiente", json.dumps({"nota": "prueba"})))
    return llegada
