# oprn-dots worker installer (Windows PowerShell): checks your invite code, prints the connector URL and the standing instructions.
# Usage:  .\install.ps1            (asks for the invite code)
#         .\install.ps1 -Code <invite-code>
# Invite code: dots1.<server address>.<key>. A bare key needs -BaseUrl https://<server address>.
param([string]$Code = $env:DOTS_INVITE, [string]$BaseUrl = $env:DOTS_BASE_URL)
$ErrorActionPreference = 'Stop'
function Die($m) { Write-Host "오류: $m" -ForegroundColor Red; exit 1 }

if (-not $Code) { $sec = Read-Host "초대 코드를 붙여넣고 Enter" -AsSecureString; $Code = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)) }
$Code = ($Code -replace '\s', '')
if (-not $Code) { Die "초대 코드가 비어 있습니다." }

if ($Code -match '^dots1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9]+)$') {
  $b = $Matches[1].Replace('_', '/').Replace('-', '+'); while ($b.Length % 4) { $b += '=' }
  $Origin = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b)); $Key = $Matches[2]
} else {
  $Key = $Code; $Origin = $BaseUrl
  if (-not $Origin) { $Origin = Read-Host "서버 주소(https://... 로 시작)를 입력" }
}
$Origin = $Origin.TrimEnd('/')
if ($Origin -notmatch '^https?://[A-Za-z0-9._:-]+$') { Die "서버 주소 형식이 이상합니다. 초대 코드를 다시 확인해 주세요." }
if ($Key -notmatch '^[A-Za-z0-9]{16,128}$') { Die "초대 코드 형식이 이상합니다. 끝까지 복사했는지 확인해 주세요." }

Write-Host "서버에 초대 코드를 확인하는 중..."
try { $r = Invoke-RestMethod -Uri "$Origin/$Key/health" -TimeoutSec 20 } catch { Die "서버가 이 초대 코드를 받아 주지 않습니다. 코드가 틀렸거나, 정지되었거나, 서버에 닿지 않습니다. 보낸 사람에게 확인해 주세요." }
if (-not $r.ok) { Die "서버 응답이 예상과 다릅니다." }
Write-Host "확인되었습니다. 일꾼 이름: $($r.worker)"
Write-Host ""
Write-Host "=============================================================="
Write-Host "1) 에이전트에 붙일 MCP 커넥터 주소 (이 주소가 곧 당신의 열쇠입니다. 남에게 보이지 마세요)"
Write-Host "=============================================================="
Write-Host "$Origin/$Key/mcp"
Write-Host ""
Write-Host "   - Claude: 설정 > 커넥터 > 사용자 지정 커넥터 추가 > 위 주소 붙여넣기"
Write-Host "   - Codex / 기타: MCP 서버 추가(Streamable HTTP) > 위 주소"
Write-Host "   - 닷츠(Dots): 커넥터 추가 > 위 주소"
Write-Host ""
Write-Host "=============================================================="
Write-Host "2) 에이전트에 붙일 상시 지시문 (아래 줄부터 끝까지 통째로 복사)"
Write-Host "=============================================================="
Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'AGENT_INSTRUCTIONS.md')
Write-Host ""
Write-Host "끝. 에이전트에게 `"get_next_job 으로 작업을 받아서 시작해`" 라고 말하면 됩니다."
