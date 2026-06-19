@echo off
REM mini 版一鍵跑(可重複執行 = 自動續跑)。從本檔所在的 /release 根目錄執行。
chcp 65001 >/dev/null
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
set MAX=30
set /a N=0
:loop
python runners/run_canonical.py --config canonical_mini.yaml
if %ERRORLEVEL%==0 goto done
set /a N+=1
if %N% GEQ %MAX% goto fail
echo [wrapper] 中斷 (rc=%ERRORLEVEL%)，60 秒後自動從斷點續跑（第 %N%/%MAX% 次）...
timeout /t 60 /nobreak >/dev/null
goto loop
:done
echo [wrapper] 全部完成。接著跑: python runners/build_final_report.py
goto end
:fail
echo [wrapper] 重試 %MAX% 次仍失敗，請看 results\RUN_STATE.md 後再執行本檔續跑。
:end
