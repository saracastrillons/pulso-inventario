"""Arma la demo reproducible y simula el paso de los días.

El "sistema de origen" es el mundo sintético: cada día deja tres archivos CSV en data/entrada/<fecha>/
y ejecuta las solicitudes aprobadas. La aplicación solo conoce esos archivos.

Uso:
    python -m pulso.demo preparar      # genera datos, entrena, evalúa y publica el primer lote
    python -m pulso.demo siguiente     # procesa el día siguiente (lo que corre el programador de tareas)
"""
from __future__ import annotations

import pickle
import shutil
import sys
import time
from pathlib import Path

import joblib
import pandas as pd

from .aprobacion import SaldoInsuficiente, aprobar, exportar_solicitudes
from .config import BODEGAS, DATOS, DIAS_FUTURO, DIAS_HISTORIA, DIAS_PRUEBA, SEMILLA, Costos, fecha, indice
from .db import cargar_catalogo, cargar_historia, conectar, crear_esquema, guardar_parametro, guardar_tabla, ultimo_lote
from .ingesta import id_lote, procesar_lote
from .mundo import Accion, Mundo, crear_mundo
from .pronostico import Pronosticador, evaluar
from .simulacion import ESCENARIOS, PoliticaMotor, PoliticaPuntoPedido, comparar_politicas, simular


def rutas(base: Path | str = DATOS) -> dict:
    base = Path(base)
    return dict(base=base, db=base / "pulso.sqlite", entrada=base / "entrada", salida=base / "salida",
                mundo=base / "mundo" / "mundo_vivo.pkl", prueba=base / "mundo" / "mundo_prueba.pkl",
                pron=base / "modelos" / "pronosticadores.joblib")


def _guardar(obj, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "wb") as f:
        pickle.dump(obj, f)


def _cargar(ruta: Path):
    with open(ruta, "rb") as f:
        return pickle.load(f)


def escribir_archivos(mundo: Mundo, carpeta: Path, defecto: str | None = None) -> Path:
    """Deja los archivos del último día cerrado, como lo haría el sistema de origen.

    Algunos días incluyen filas sucias (un duplicado, un producto que no existe) para mostrar la validación.
    `defecto='columna'` simula un archivo dañado: ventas.csv llega sin la columna de unidades.
    """
    t = mundo.dia - 1
    hoy, ids = fecha(t).isoformat(), mundo.cat.producto_id.values
    carpeta = Path(carpeta) / hoy
    carpeta.mkdir(parents=True, exist_ok=True)
    series = [(p, b) for p in range(mundo.P) for b in range(mundo.B)]
    ventas = pd.DataFrame([dict(fecha=hoy, producto_id=ids[p], bodega_id=BODEGAS[b],
                                unidades=int(mundo.ventas[t, p, b]), stock_cierre=int(mundo.cierre[t, p, b]))
                           for p, b in series])
    if t % 3 == 0:
        ventas = pd.concat([ventas, ventas.iloc[[4]]], ignore_index=True)
    if t % 5 == 0:
        ventas = pd.concat([ventas, pd.DataFrame([dict(fecha=hoy, producto_id="SKU-999", bodega_id="BOG",
                                                       unidades=3, stock_cierre=0)])], ignore_index=True)
    if defecto == "columna":
        ventas = ventas.drop(columns=["unidades"])
    ventas.to_csv(carpeta / "ventas.csv", index=False)
    pd.DataFrame([dict(producto_id=ids[p], bodega_id=BODEGAS[b], disponible=int(mundo.stock[p, b]))
                  for p, b in series]).to_csv(carpeta / "inventario.csv", index=False)

    def fila(e, estado, eta):
        return dict(pedido_id=f"P{e.id:06d}", tipo=e.tipo, producto_id=ids[e.p], bodega_id=BODEGAS[e.b],
                    origen=BODEGAS[e.origen] if e.origen >= 0 else "", cantidad_pendiente=e.cantidad,
                    fecha_pedido=fecha(e.dia_pedido).isoformat(), fecha_esperada=fecha(eta).isoformat(),
                    estado=estado, referencia=e.ref)
    pedidos = [fila(e, "abierto", e.eta_conocida(t)) for e in mundo.envios]
    pedidos += [fila(e, "recibido", d) for e, d in mundo.recibidos if d == t]
    columnas = ["pedido_id", "tipo", "producto_id", "bodega_id", "origen", "cantidad_pendiente", "fecha_pedido",
                "fecha_esperada", "estado", "referencia"]
    pd.DataFrame(pedidos, columns=columnas).to_csv(carpeta / "pedidos.csv", index=False)
    return carpeta


def _caso_apertura(mundo: Mundo, regla, pron) -> tuple[int, int] | None:
    """Guion de la demo: el último día de la historia entra un pedido corporativo grande en Bogotá.

    Se elige la referencia de alta rotación donde ese pedido deja un faltante a pocos días que el motor
    resuelve con un traslado desde Medellín. El pedido es parte de la demanda de todas las políticas.
    """
    t, mejor = mundo.dia, None
    for p in (i for i, rot in enumerate(mundo.cat.rotacion) if rot == "alta"):
        for veces in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            extra = int(round(veces * mundo.demanda[t - 28:t, p, 0].mean()))
            m = mundo.copiar()
            m.demanda[t, p, 0] += extra
            m.paso(regla.decidir(m))
            res = PoliticaMotor(pron, "").evaluar(m)[p * 2]
            if (res.requiere_decision and not res.solo_escenario_alto and (res.primer_faltante or 0) >= 3
                    and any(a[0] == "traslado" for a in res.elegida.acciones)
                    and (mejor is None or res.impacto > mejor[0])):
                mejor = (res.impacto, p, extra)
    return (mejor[1], mejor[2]) if mejor else None


def preparar(base: Path | str = DATOS, semilla: int = SEMILLA, decir=print) -> None:
    """Genera 18 meses de historia, entrena, evalúa en el periodo de prueba y publica el primer lote."""
    r = rutas(base)
    t0 = time.time()
    if r["base"].exists():
        shutil.rmtree(r["base"])
    costos = Costos()

    decir("1/6 Generando 30 productos, 2 bodegas y 18 meses de historia con la regla de punto de pedido...")
    mundo, promos = crear_mundo(semilla, DIAS_HISTORIA + DIAS_FUTURO, costos)
    regla = PoliticaPuntoPedido(mundo.media_inicial)
    inicio_prueba = DIAS_HISTORIA - DIAS_PRUEBA
    simular(mundo, regla, inicio_prueba)
    prueba = mundo.copiar()

    decir("2/6 Entrenando los pronósticos con datos anteriores al periodo de prueba...")
    sencillo = Pronosticador("sencillo").entrenar(prueba.observar())
    ml = Pronosticador("ml").entrenar(prueba.observar())

    simular(mundo, regla, DIAS_PRUEBA - 1)
    # El modelo de la demo se entrena la víspera del último corte, con toda la historia disponible.
    vivo = Pronosticador("ml").entrenar(mundo.observar())
    caso = _caso_apertura(mundo, regla, vivo)
    if caso:
        p, extra = caso
        for m in (mundo, prueba):          # el mismo pedido para todas las políticas
            m.demanda[DIAS_HISTORIA - 1, p, 0] += extra
        decir(f"    Caso de apertura: pedido corporativo de {extra} unidades de {mundo.cat.nombre.iloc[p]} en Bogotá.")
    simular(mundo, regla, 1)

    decir("3/6 Guardando la historia en SQLite...")
    con = conectar(r["db"])
    crear_esquema(con)
    cargar_catalogo(con, mundo.cat, promos, costos)
    cargar_historia(con, mundo, DIAS_HISTORIA)
    guardar_parametro(con, "caso_apertura", dict(producto_id=mundo.cat.producto_id.iloc[caso[0]], unidades=caso[1]) if caso else None)

    decir("4/6 Evaluando el pronóstico avanzando por fechas...")
    err, cob = evaluar(mundo, {"Sencillo": sencillo, "Aprendizaje automático": ml}, inicio_prueba - 1, DIAS_HISTORIA - 1)
    guardar_tabla(con, "eval_error", err)
    guardar_tabla(con, "eval_cobertura", cob)
    guardar_parametro(con, "validacion_prueba", ml.validacion)

    decir("5/6 Simulando las tres políticas en los escenarios base, favorable y adverso...")
    res, dia = zip(*(comparar_politicas(prueba, sencillo, ml, DIAS_PRUEBA, costos, e) for e in ESCENARIOS))
    guardar_tabla(con, "impacto", pd.concat(res, ignore_index=True))
    guardar_tabla(con, "impacto_diario", pd.concat(dia, ignore_index=True))

    decir("6/6 Publicando el primer lote con el modelo de la demo...")
    guardar_parametro(con, "validacion_vivo", vivo.validacion)
    r["pron"].parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(dict(sencillo=sencillo, ml=ml, vivo=vivo), r["pron"])
    _guardar(prueba, r["prueba"])
    _guardar(mundo, r["mundo"])
    carpeta = escribir_archivos(mundo, r["entrada"])
    out = procesar_lote(con, carpeta, mundo.dia - 1, vivo)
    con.close()
    decir(f"Listo en {time.time() - t0:.0f} s. Corte {fecha(mundo.dia - 1)}: {out['recomendaciones']} recomendaciones.")


def procesar_dia_siguiente(base: Path | str = DATOS, auto_aprobar: bool = False, defecto: str | None = None) -> dict:
    """Avanza un día: el sistema de origen ejecuta lo aprobado, deja los archivos y se corre el proceso diario.

    Si la última carga falló, no avanza: vuelve a pedir los archivos del mismo corte y reprocesa.
    """
    r = rutas(base)
    con = conectar(r["db"])
    try:
        mundo: Mundo = _cargar(r["mundo"])
        pron = joblib.load(r["pron"])["vivo"]
        ok = ultimo_lote(con, solo_ok=True)
        al_dia = ok is not None and indice(ok["fecha_corte"]) == mundo.dia - 1
        if al_dia:
            if mundo.dia >= mundo.T:
                return dict(estado="fin", detalle="Se acabaron los días reservados para la demo. Vuelve a prepararla.")
            if auto_aprobar:
                for rec in con.execute("SELECT rec_id FROM recomendaciones WHERE estado = 'pendiente' ORDER BY impacto DESC").fetchall():
                    try:
                        aprobar(con, rec["rec_id"], usuario="aprobación automática")
                    except SaldoInsuficiente:
                        pass
            exportar_solicitudes(con, r["salida"])
            pid = {p: i for i, p in enumerate(mundo.cat.producto_id)}
            acciones = [Accion(s["tipo"], pid[s["producto_id"]], BODEGAS.index(s["destino"]), s["cantidad"],
                               BODEGAS.index(s["origen"]) if s["origen"] else -1, s["solicitud_id"])
                        for s in con.execute("SELECT * FROM solicitudes WHERE estado = 'aprobada'")]
            mundo.paso(acciones)
            _guardar(mundo, r["mundo"])
        carpeta = escribir_archivos(mundo, r["entrada"], defecto)
        out = procesar_lote(con, carpeta, mundo.dia - 1, pron)
        out["fecha_corte"] = fecha(mundo.dia - 1).isoformat()
        return out
    finally:
        con.close()


def recalcular_impacto(base: Path | str, costos: Costos, escenario: str = "base"):
    """Repite la comparación de políticas con otros supuestos de costo."""
    r = rutas(base)
    pron = joblib.load(r["pron"])
    return comparar_politicas(_cargar(r["prueba"]), pron["sencillo"], pron["ml"], DIAS_PRUEBA, costos, escenario)


if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden == "preparar":
        preparar()
    elif orden == "siguiente":
        print(procesar_dia_siguiente(auto_aprobar="--auto" in sys.argv))
    else:
        print(__doc__)
