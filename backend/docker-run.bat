@echo off
setlocal

set "IMAGE_NAME=diploma-assistant"
set "CONTAINER_NAME=diploma-assistant"
set "HOST_PORT=8000"
set "CONTAINER_PORT=8000"
set "EXTRA_ARGS="

if /I "%~1"=="gpu" (
    set "EXTRA_ARGS=--gpus all -e APP_DEVICE=cuda"
)

where docker >nul 2>&1
if errorlevel 1 (
    echo Docker не найден. Установи Docker Desktop и запусти его, затем повтори команду.
    exit /b 1
)

echo [1/3] Сборка Docker-образа %IMAGE_NAME%...
docker build -t %IMAGE_NAME% .
if errorlevel 1 (
    echo Сборка Docker-образа завершилась с ошибкой.
    exit /b 1
)

echo [2/3] Удаление старого контейнера %CONTAINER_NAME%, если он существует...
docker rm -f %CONTAINER_NAME% >nul 2>&1

echo [3/3] Запуск контейнера %CONTAINER_NAME%...
docker run --name %CONTAINER_NAME% -p %HOST_PORT%:%CONTAINER_PORT% %EXTRA_ARGS% %IMAGE_NAME%
if errorlevel 1 (
    echo Запуск контейнера завершился с ошибкой.
    exit /b 1
)

endlocal
