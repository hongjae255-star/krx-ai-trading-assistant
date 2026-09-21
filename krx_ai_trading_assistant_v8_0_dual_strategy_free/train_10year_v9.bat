@echo off
setlocal
cd /d %~dp0

echo ==============================================
echo V9 10-year Korea + US historical training
echo ==============================================
echo.
echo 1. Installing/updating dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo.
echo 2. Download Korea 10-year data (resume enabled)...
python run.py history download KR --years 10
if errorlevel 1 goto :err

echo.
echo 3. Build Korea features/labels...
python run.py history build KR
if errorlevel 1 goto :err

echo.
echo 4. Download US 10-year data (resume enabled)...
python run.py history download US --years 10
if errorlevel 1 goto :err

echo.
echo 5. Build US features/labels...
python run.py history build US
if errorlevel 1 goto :err

echo.
echo 6. Train 4 walk-forward validated models...
python run.py history train
if errorlevel 1 goto :err

echo.
echo DONE. Models are in data\models\historical_*.pkl
pause
exit /b 0

:err
echo.
echo FAILED. Check logs/output above. Re-running the same command is safe because downloads resume.
pause
exit /b 1
