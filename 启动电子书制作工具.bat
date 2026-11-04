@echo off
chcp 65001 >nul
python "%~dp0epub_maker.py" --gui
if errorlevel 1 pause
