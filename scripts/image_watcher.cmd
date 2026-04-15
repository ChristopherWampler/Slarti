@echo off
rem Image watcher daemon — polls every 3 seconds for image generation markers
rem Runs as a background process, started by restart.sh or manually
rem Logs to logs/daily/image_watcher.log
cd /d C:\Openclaw\slarti
python scripts\image_watcher.py >> logs\daily\image_watcher.log 2>&1
