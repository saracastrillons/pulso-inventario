# Pulso Inventario

**Un copiloto que cada mañana recomienda cuándo comprar, trasladar o esperar.**

Una empresa con dos bodegas (Bogotá y Medellín) revisa ventas, existencias y pedidos pendientes en
archivos separados. Pulso Inventario reúne esos datos, anticipa faltantes, compara acciones con su
costo y convierte la opción aprobada en una solicitud trazable. Al final, una simulación comprueba si
esas decisiones mejoran el nivel de servicio y cuánto cuestan.

Proyecto de portafolio de **automatización, analítica de datos e inteligencia artificial aplicada a
la toma de decisiones**. Todos los datos son sintéticos y se generan con una semilla fija: cualquier
persona obtiene exactamente la misma demo.

## Qué hace

| Pantalla | Para qué sirve |
|---|---|
| **Prioridades** | Bandeja de decisiones ordenada por impacto y por tiempo para actuar: producto, bodega, primer día con faltante, unidades en riesgo, acción sugerida y su costo. Muestra la fecha de corte y avisa si la última carga falló. |
| **Detalle de decisión** | Curva de inventario con y sin la acción, día de quiebre, alternativas comparadas (incluidas las descartadas y por qué) y el formulario para aprobar, ajustar la cantidad o rechazar con motivo. |
| **Simulador** | Cambia el plazo del proveedor o la intensidad de la demanda y muestra en qué punto comprar deja de cubrir el faltante y otra opción pasa a ser la mejor. |
| **Impacto** | Compara tres políticas de inventario en el mismo periodo, con costos editables, y reporta la calidad del pronóstico. |

## Cómo ejecutarlo

Requiere Python 3.12 o superior. Desde la carpeta del proyecto, en PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pulso.demo preparar
.\.venv\Scripts\python.exe -m streamlit run app/Pulso.py
```

`preparar` tarda cerca de un minuto: genera 18 meses de historia, la guarda en SQLite, entrena los
modelos, evalúa, simula las políticas y publica el primer lote. Las pruebas se corren con
`.\.venv\Scripts\python.exe -m pytest`.

## Guion de la demo (5 minutos)

1. **Prioridades.** El corte es el 3 de noviembre. La bandeja abre con 8 decisiones. La segunda es un
   traslado: ayer entró un pedido corporativo grande de *Alimento perro 8 kg* en Bogotá.
2. **Detalle.** Bogotá tiene 49 unidades y una recepción de 190 que llega el día 4. El faltante empieza
   el día 3. Una compra normal llegaría el día 5 (tarde), la urgente cuesta $369.557 en total esperado y
   el traslado de 57 unidades desde Medellín $156.925; Medellín conserva su stock de seguridad. Cambia al
   escenario de demanda alta para ver si la cantidad sigue alcanzando.
3. **Aprobar.** Se crea la solicitud `TR-20261103-001` con origen, destino, cantidad y fecha esperada, y
   las unidades quedan reservadas en Medellín. Un doble clic devuelve la misma solicitud.
4. **Procesar día siguiente** (barra lateral). El mismo proceso que correría a las 6:00 a. m.: el
   sistema de origen ejecuta lo aprobado, deja los archivos del día y la bandeja se recalcula. Dos días
   después la solicitud aparece como *recibida* en la fecha prevista.
5. **Simular archivo dañado.** La carga falla, la bandeja muestra la última corrida exitosa y marca sus
   recomendaciones como desactualizadas. No deja aprobar hasta reintentar.
6. **Simulador → Caso guía.** Números redondos para explicar la lógica: 90 unidades, 40 que llegan el
   día 3, demanda acumulada de 180 al día 5, faltante desde el día 4, pedido normal el día 7. El motor
   recomienda trasladar 50; con demanda alta quedan 20 unidades expuestas.
7. **Impacto.** Los resultados, con sus límites (siguiente sección).

## Resultados simulados

Periodo de prueba: los últimos 120 días de la historia. Las tres políticas parten del mismo inventario
y enfrentan la misma demanda, los mismos retrasos y los mismos costos. Los modelos se entrenaron solo
con datos anteriores. Son cifras de una simulación con supuestos de costo editables, no de una
operación real.

**Escenario base**

| Política | Demanda atendida | Días-producto con quiebre | Inventario promedio | Resultado frente a punto de pedido |
|---|---|---|---|---|
| Punto de pedido | 97,57 % | 185 | $230,1 M | — |
| Motor + pronóstico sencillo | 98,50 % | 121 | $238,5 M | +$6,9 M |
| Motor + aprendizaje automático | 98,53 % | 148 | $215,1 M | +$3,8 M |

Qué responde cada comparación:

- **Cuánto aporta comparar compras y traslados** (fila 2 frente a fila 1): casi un punto más de
  servicio y la mitad del margen perdido por faltantes ($18,2 M frente a $39,1 M), pagando $8,4 M en
  urgencias y traslados.
- **Cuánto aporta cambiar el pronóstico** (fila 3 frente a fila 2): el mismo servicio con $23 M menos
  de capital inmovilizado promedio, pero un resultado económico $3,2 M menor. El capital se reporta
  aparte del efecto económico del periodo.

**Donde no gana.** En el escenario favorable (demanda 10 % menor, sin retrasos) la regla de punto de
pedido obtiene el mejor resultado: cuando casi no hay faltantes, las urgencias y traslados no se pagan
solos. En el escenario adverso (demanda 15 % mayor, más retrasos) el motor con pronóstico sencillo gana
con claridad (96,9 % frente a 94,1 %), pero el motor con aprendizaje automático queda por debajo del
punto de pedido en resultado económico: mantiene menos inventario y lo paga cuando la demanda sube de
golpe. Un mejor pronóstico no garantiza por sí solo una mejor decisión.

**Pronóstico.** Evaluado avanzando por fechas contra la demanda total, que incluye la que no se pudo
atender y que el modelo nunca vio:

| Método | Error absoluto medio (unidades/día) | Sesgo |
|---|---|---|
| Promedio de los 4 días equivalentes | 7,66 | −0,18 |
| HistGradientBoostingRegressor | 6,99 (−9 %) | −0,42 |

El escenario alto (P90 de la demanda acumulada a 5 días) cubre el 89,7 % de los casos de prueba, cerca
del 90 % esperado. El modelo reduce el error en los tres segmentos de rotación, pero se queda un poco
más corto que la referencia; esa diferencia de sesgo explica parte del resultado anterior.

## Cómo funciona

```
sistema de origen (simulado)          proceso diario (Python)                 aplicación (Streamlit)
ventas.csv  inventario.csv  ──►  validar ──► SQLite ──► pronóstico ──► motor ──►  bandeja ──► aprobar
pedidos.csv                        │                     central y alto          │
        ▲                          └─ filas rechazadas, lote ok / fallido        ▼
        └──────────────── solicitudes aprobadas (traslados y compras) ◄── solicitud + auditoría
```

**Datos.** 30 productos, 2 bodegas y 18 meses diarios (`pulso/mundo.py`): alta rotación, rotación media
y ventas intermitentes; promociones conocidas de antemano; diferencias entre ciudades; retrasos de
proveedores. El simulador conoce la demanda total; el modelo solo recibe las ventas atendidas y el
inventario de cierre. Si una bodega vende cero porque está agotada, ese cero no se toma como falta de
demanda: los días con quiebre se corrigen con los días equivalentes anteriores.

**SQL.** SQLite guarda catálogo y costos, ventas, inventario, pedidos, rutas, promociones, pronósticos,
recomendaciones, solicitudes y decisiones (`pulso/db.py`). La tabla `pedidos_por_corte` conserva la fecha
esperada que se conocía en cada corte, para no usar hacia atrás un retraso que nadie conocía.

**Automatización.** `pulso/ingesta.py` comprueba columnas, fechas, cantidades, referencias y duplicados.
Si el lote pasa, publica pronósticos y recomendaciones en una transacción; si falla, la corrida
anterior sigue vigente. Reprocesar el mismo corte deja un solo juego de resultados. Para programarlo a
las 6:00 a. m. en Windows: `.\scripts\registrar_tarea.ps1`.

**Pronóstico.** `pulso/pronostico.py`. Referencia: promedio de los últimos cuatro días equivalentes de la
semana. Modelo: un `HistGradientBoostingRegressor` compartido por todos los productos y bodegas, con
ventas rezagadas, promedios móviles, día de la semana, producto, bodega y promociones ya anunciadas en
la fecha de corte. Si la referencia gana en un segmento de rotación durante la validación, se usa la
referencia en ese segmento. El escenario alto sale del cuantil 90 del error de la demanda *acumulada*
(sumar cuantiles diarios lo exageraría) y su cobertura se comprueba en el periodo de prueba.

**Decisión.** `pulso/motor.py`, reglas explícitas en Python. Proyecta el inventario día a día (saldo +
recepciones en su fecha − demanda) y evalúa esperar, compra normal, compra urgente, traslado y la
combinación de un puente rápido más una compra normal. Descarta lo que incumple el stock de seguridad
del origen, la capacidad de la ruta o llega después del faltante, y muestra el motivo. Entre las
viables minimiza costo logístico + costo de mantener + margen en riesgo, ponderando 70 % el escenario
central y 30 % el alto. Cada traslado aceptado se descuenta del origen antes de evaluar la siguiente
referencia.

**Aprobación.** `pulso/aprobacion.py`. En una sola transacción vuelve a comprobar el saldo del origen,
reserva las unidades, crea la solicitud y guarda la versión del pronóstico, los datos usados y la
decisión final. Repetirla devuelve la misma solicitud; si otra aprobación consumió las unidades, pide
recalcular.

## Pruebas

28 pruebas con pytest, que GitHub Actions ejecuta en cada cambio. Las que más importan son las que
afectan la decisión:

- una entrega que llega después del quiebre no cubre el faltante;
- dos solicitudes que compiten por el mismo saldo: la segunda se rechaza;
- una aprobación repetida devuelve la misma solicitud y reserva una sola vez;
- el pronóstico no cambia si se altera cualquier dato posterior a la fecha de corte;
- reprocesar un lote no duplica resultados y un archivo dañado no reemplaza la última corrida buena;
- una solicitud aprobada llega en la fecha prevista.

## Límites conocidos

- Datos sintéticos: el tamaño del beneficio depende de los supuestos de costo, por eso son editables.
- El motor decide referencia por referencia con una regla de costo esperado de dos escenarios; no es
  una optimización conjunta de toda la red.
- El costo que un traslado le genera después a la bodega de origen no entra en la comparación de
  opciones; sí queda reflejado en la simulación de impacto.
- El modelo de la demo se entrena una vez; en la simulación queda congelado al inicio del periodo de
  prueba.
- El resumen para la reunión se arma con plantillas sobre resultados estructurados. Redactarlo con IA
  generativa sería una extensión: recibiría ese mismo diccionario, con los registros que respaldan
  cada cifra.

## Tecnologías

Python · pandas · NumPy · scikit-learn · SQLite · Streamlit · Plotly · pytest · GitHub Actions ·
Programador de tareas de Windows
