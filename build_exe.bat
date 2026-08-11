@echo off
rem 构建明阴全自动小说电脑版（单 exe，内嵌 embedding / torch）
rem 产物：dist\novel_app\novel_app.exe
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 安装 PyInstaller ...
pip install pyinstaller
echo [2/3] 打包（含 torch，约需 20~30 分钟）...
python -m PyInstaller novel-app.spec --noconfirm --clean
if errorlevel 1 goto :err
echo [3/3] 完成。
echo 产物目录: %~dp0dist\novel_app\
echo 运行: dist\novel_app\novel_app.exe
pause
exit /b 0

:err
echo 构建失败。
pause
exit /b 1

