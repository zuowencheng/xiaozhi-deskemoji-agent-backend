@echo off
chcp 65001 >nul
REM ============================================================
REM  安装第三方 MCP（npm 包）
REM
REM  为什么需要手动跑这个：
REM    WorkBuddy 沙箱把 reg.exe 列入黑名单，npm 装包会被拦，
REM    所以在助手这边装不上。你（主人）双击这个脚本，走的是你自己的权限，能装。
REM
REM  装完之后：
REM    python tools\mcp_toggle.py enable filesystem
REM    然后重启服务端（配置只在连接建立时读一次）
REM ============================================================
setlocal

set NODE_DIR=C:\Users\zuowe\.workbuddy\binaries\node\versions\22.22.2-5
set RUNTIME=%~dp0..\runtime\mcp-node

if not exist "%NODE_DIR%\npm.cmd" (
  echo [X] 没找到 node: %NODE_DIR%
  pause
  exit /b 1
)

set PATH=%NODE_DIR%;%PATH%
if not exist "%RUNTIME%" mkdir "%RUNTIME%"

cd /d "%RUNTIME%"
if not exist package.json (
  echo [1/3] 初始化 package.json ...
  call npm init -y
) else (
  echo [1/3] package.json 已存在，跳过
)

echo.
echo [2/3] 安装 MCP 包（走 npmmirror 国内镜像，官方源在本机很慢）...
call npm install --registry=https://registry.npmmirror.com --no-audit --no-fund ^
  @modelcontextprotocol/server-filesystem ^
  @simonb97/server-win-cli ^
  @modelcontextprotocol/server-memory

if errorlevel 1 (
  echo.
  echo [X] 安装失败。可以试试官方源：
  echo     npm install --no-audit --no-fund @modelcontextprotocol/server-filesystem
  pause
  exit /b 1
)

echo.
echo [3/3] 完成。已装到 %RUNTIME%
echo.
echo 下一步：
echo   python tools\mcp_toggle.py enable filesystem
echo   然后重启服务端
echo.
echo 提示：playwright 还要额外装浏览器内核，命令是 npx playwright install
pause
