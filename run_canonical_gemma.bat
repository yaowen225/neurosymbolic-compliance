@echo off
REM 實驗室 gemma 版一鍵跑（可重複執行 = 自動續跑）。KG db 名稱請先在 canonical_gemma.yaml 改好，
REM 或在下面那行加 --kg-dbs db1,db2,db3,db4,db5,db6 覆寫。從 /release 根目錄執行。
chcp 65001 >/dev/null
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
set MAX=30
set /a N=0
:loop
python runners/run_canonical.py --config canonical_gemma.yaml
if %ERRORLEVEL%==0 goto done
set /a N+=1
if %N% GEQ %MAX% goto fail
echo [wrapper] 中斷 (rc=%ERRORLEVEL%)，60 秒後自動續跑（第 %N%/%MAX% 次）...
timeout /t 60 /nobreak >/dev/null
goto loop
:done
echo [wrapper] 全部完成。
goto end
:fail
echo [wrapper] 重試 %MAX% 次仍失敗，請看 results_gemma\RUN_STATE.md。
:end
