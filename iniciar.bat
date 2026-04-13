@echo off
echo ============================================
echo   SHEKINAH TUTOR ICFES
echo   Comunidad Juvenil Shekinah
echo   Parroquia San Luis Maria de Montfort
echo ============================================
echo.

if "%ANTHROPIC_API_KEY%"=="" (
    echo ERROR: No se encontro la API Key de Anthropic.
    echo.
    echo Configura la variable asi:
    echo   set ANTHROPIC_API_KEY=sk-ant-api03-TU-CLAVE-AQUI
    echo.
    echo Luego ejecuta este archivo de nuevo.
    echo.
    pause
    exit /b 1
)

echo Iniciando servidor...
echo Abre en tu navegador: http://localhost:5000
echo.
echo Presiona Ctrl+C para detener el servidor.
echo.
python app.py
pause
