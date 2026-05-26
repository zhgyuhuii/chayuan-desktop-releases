@echo off
setlocal

call conda activate py312
if errorlevel 1 (
  echo [ERROR] conda activate py312 failed.
  exit /b 1
)

cd /d "D:\code\chayuan\chayuan-server\libs\chayuan-server\chayuan"
if errorlevel 1 (
  echo [ERROR] cd failed: D:\code\chayuan\chayuan-server\libs\chayuan-server\chayuan
  exit /b 1
)

python cli.py start -a
