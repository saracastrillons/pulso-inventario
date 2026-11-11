# Registra el proceso diario en el Programador de tareas de Windows: todos los días a las 6:00 a. m.
# Ejecutar una sola vez desde la carpeta del proyecto:  .\scripts\registrar_tarea.ps1
# Para quitarla:  schtasks /Delete /TN "PulsoInventario-ProcesoDiario" /F

$proyecto = Split-Path -Parent $PSScriptRoot
$python = Join-Path $proyecto ".venv\Scripts\python.exe"
$accion = New-ScheduledTaskAction -Execute $python -Argument "-m pulso.demo siguiente" -WorkingDirectory $proyecto
$horario = New-ScheduledTaskTrigger -Daily -At "06:00"
Register-ScheduledTask -TaskName "PulsoInventario-ProcesoDiario" -Action $accion -Trigger $horario `
    -Description "Carga los archivos del corte, valida y publica pronosticos y recomendaciones." -Force
