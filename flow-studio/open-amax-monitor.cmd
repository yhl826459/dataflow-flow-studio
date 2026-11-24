@echo off
setlocal
where ssh.exe >nul 2>&1
if errorlevel 1 (
  echo Windows OpenSSH Client is missing. See WORKBENCH.md.
  pause
  exit /b 1
)
echo Connecting through SSH alias amax-monitor. See WORKBENCH.md for first-time setup.
echo Enter your own amax SSH password if prompted.
echo The read-only workbench opens when the tunnel is ready. Keep this window open.

powershell.exe -NoLogo -NoProfile -Command ^
  "$url = 'http://127.0.0.1:18768/';" ^
  "$port = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 18768);" ^
  "try { $port.ExclusiveAddressUse = $true; $port.Start() } catch { Write-Host 'Local port 18768 is unavailable. Close the previous workbench tunnel or other program using it.'; exit 1 } finally { $port.Stop() };" ^
  "$browser = Start-Job -ArgumentList $url -ScriptBlock { param($url) $deadline = [DateTime]::UtcNow.AddMinutes(3); while ([DateTime]::UtcNow -lt $deadline) { try { $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; if ($page.Content.Contains('./shared-monitor.js')) { Start-Process $url; return } } catch {} Start-Sleep -Milliseconds 500 } };" ^
  "try { & ssh.exe -N -o StrictHostKeyChecking=ask -o HostKeyAlgorithms=ssh-ed25519 -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -L '127.0.0.1:18768:/tmp/flow-studio-yanghonglei-monitor/monitor.sock' 'amax-monitor'; $result = $LASTEXITCODE } finally { Stop-Job -Job $browser -ErrorAction SilentlyContinue; Remove-Job -Job $browser -Force -ErrorAction SilentlyContinue }; exit $result"

set "result=%errorlevel%"
if not "%result%"=="0" (
  echo SSH or the launcher exited with an error. See the message above and WORKBENCH.md.
  pause
)
exit /b %result%
