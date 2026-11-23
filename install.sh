#!/usr/bin/env bash
# oprn-dots worker installer: checks your invite code and prints (1) the connector URL and (2) the standing instructions.
# Usage:  ./install.sh                (asks for the invite code)
#         ./install.sh <invite-code>
#         DOTS_INVITE=<code> ./install.sh
# Invite code: dots1.<server address>.<key>  (the address is inside the code). If you were given only a bare key,
# also set DOTS_BASE_URL=https://<server address> (or the script asks).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "오류: $*" >&2; exit 1; }
command -v curl >/dev/null || die "curl 이 필요합니다. 먼저 설치해 주세요."

CODE="${1:-${DOTS_INVITE:-}}"
if [ -z "$CODE" ]; then read -r -s -p "초대 코드를 붙여넣고 Enter: " CODE; echo; fi
CODE="$(printf '%s' "$CODE" | tr -d '[:space:]')"
[ -n "$CODE" ] || die "초대 코드가 비어 있습니다."

b64url_decode() { # base64url (no padding) -> text
  local s; s="$(printf '%s' "$1" | tr '_-' '/+')"
  while [ $(( ${#s} % 4 )) -ne 0 ]; do s="${s}="; done
  printf '%s' "$s" | base64 -d 2>/dev/null
}

if [[ "$CODE" == dots1.*.* ]]; then
  rest="${CODE#dots1.}"; ORIGIN="$(b64url_decode "${rest%%.*}")"; KEY="${rest#*.}"
else
  KEY="$CODE"; ORIGIN="${DOTS_BASE_URL:-}"
  if [ -z "$ORIGIN" ]; then read -r -p "서버 주소(https://... 로 시작)를 입력: " ORIGIN || true; fi
fi
ORIGIN="${ORIGIN%/}"
[[ "$ORIGIN" =~ ^https?://[A-Za-z0-9._:-]+$ ]] || die "서버 주소 형식이 이상합니다. 초대 코드를 다시 확인해 주세요."
[[ "$KEY" =~ ^[A-Za-z0-9]{16,128}$ ]] || die "초대 코드 형식이 이상합니다. 끝까지 복사했는지 확인해 주세요."

echo "서버에 초대 코드를 확인하는 중..."
RESP="$(curl -fsS -m 20 "$ORIGIN/$KEY/health" 2>/dev/null)" || die "서버가 이 초대 코드를 받아 주지 않습니다. 코드가 틀렸거나, 정지되었거나, 서버에 닿지 않습니다. 보낸 사람에게 확인해 주세요."
case "$RESP" in *'"ok": true'*|*'"ok":true'*) ;; *) die "서버 응답이 예상과 다릅니다.";; esac
NAME="$(printf '%s' "$RESP" | sed -n 's/.*"worker": *"\([^"]*\)".*/\1/p')"
case "$NAME" in *'\u'*) NAME="$(printf '%b' "$NAME" 2>/dev/null || printf '%s' "$NAME")" ;; esac   # server sends non-ASCII names as \uXXXX
echo "확인되었습니다. 일꾼 이름: ${NAME:-?}"
echo
echo "=============================================================="
echo "1) 에이전트에 붙일 MCP 커넥터 주소 (이 주소가 곧 당신의 열쇠입니다. 남에게 보이지 마세요)"
echo "=============================================================="
echo "$ORIGIN/$KEY/mcp"
echo
echo "   - Claude: 설정 > 커넥터 > 사용자 지정 커넥터 추가 > 위 주소 붙여넣기"
echo "   - Codex / 기타: MCP 서버 추가(Streamable HTTP) > 위 주소"
echo "   - 닷츠(Dots): 커넥터 추가 > 위 주소"
echo
echo "=============================================================="
echo "2) 에이전트에 붙일 상시 지시문 (아래 줄부터 끝까지 통째로 복사)"
echo "=============================================================="
cat "$HERE/AGENT_INSTRUCTIONS.md"
echo
echo "=============================================================="
echo "끝. 에이전트에게 \"get_next_job 으로 작업을 받아서 시작해\" 라고 말하면 됩니다."
