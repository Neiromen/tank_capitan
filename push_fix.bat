@echo off
REM Пуш после HTTP 408: буфер + таймауты, затем force-push.
REM Запускай из Git Bash или cmd (не PowerShell).

git config http.postBuffer 1048576000
git config https.postBuffer 1048576000
git config http.lowSpeedLimit 1000
git config http.lowSpeedTime 600
git push --force origin master
pause
