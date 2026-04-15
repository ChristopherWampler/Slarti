@echo off
rem Image watcher — runs every 1 minute via Task Scheduler
rem Checks recent sessions for [DESIGN_REQUEST] and [MOCKUP_REQUEST] markers
rem and spawns image_agent.py when found
python3 C:\Openclaw\slarti\scripts\image_watcher.py >> C:\Openclaw\slarti\logs\daily\image_watcher.log 2>&1
