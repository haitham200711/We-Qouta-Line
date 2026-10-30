@echo off
title WE Quota Monitor - نظام متابعة خطوط وي
cd /d "%~dp0"

echo ========================================================
echo        WE Quota Monitor - Telecom Egypt Landlines
echo             مراقب باقات الانترنت المنزلي - شركة وي
echo ========================================================
echo.

set PYTHON_EXE=C:\Python314\python.exe
if not exist "%PYTHON_EXE%" (
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON_EXE=py
    ) else (
        where python >nul 2>&1
        if %errorlevel% equ 0 (
            set PYTHON_EXE=python
        ) else (
            echo [!] لم يتم العثور على Python في النظام!
            pause
            exit /b 1
        )
    )
)

echo [*] جاري بدء تشغيل السيرفر...
echo [*] الرابط: http://localhost:8000
echo.

:: تشغيل المتصفح تلقائياً بعد ثانيتين
start "" powershell -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000'"

:: تشغيل خادم FastAPI
"%PYTHON_EXE%" app.py

pause
