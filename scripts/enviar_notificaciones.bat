@echo off
REM RF-25: drena la cola de core.notificacion (envio de correos).
REM Se puede ejecutar a mano o desde el Programador de tareas de Windows.
REM Acepta los argumentos del comando, p.ej.: enviar_notificaciones.bat --limite 50

setlocal

REM Raiz del proyecto = carpeta padre de \scripts (no depende de la ruta de instalacion)
set "PROYECTO=%~dp0.."
pushd "%PROYECTO%" || exit /b 1

REM Interprete: usa el venv si existe, si no el python del PATH
set "PYTHON=%CD%\venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" manage.py enviar_notificaciones %*
set "CODIGO=%ERRORLEVEL%"

popd
exit /b %CODIGO%
