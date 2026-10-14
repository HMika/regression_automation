@echo off
rem Usage:
rem   csvtool extract     -i C:\EOD     -o C:\out
rem   csvtool extract-adj -i C:\EOD     -o C:\out
rem   csvtool merge       -i C:\csv_dir -o C:\out [--no-validate]
rem   csvtool merge       -i C:\csv_dir -o C:\out\my_report.csv
python "%~dp0csvtool.py" %*
exit /b %ERRORLEVEL%
