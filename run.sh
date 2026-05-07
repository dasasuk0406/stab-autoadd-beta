#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
    echo "[오류] 가상환경이 없습니다. install.sh 를 먼저 실행하세요."
    exit 1
fi

# 기존 Chrome 창 사용 옵션: --attach 또는 --attach=포트번호
CHROME_PORT=9222
if [[ "$1" == "--attach" || "$1" == "--attach="* ]]; then
    if [[ "$1" == "--attach="* ]]; then
        CHROME_PORT="${1#--attach=}"
    fi
    echo "Chrome 디버그 모드로 실행 중 (포트 $CHROME_PORT)..."
    open -a "Google Chrome" --args --remote-debugging-port=$CHROME_PORT
    echo "Chrome이 열렸습니다. nearminton에 로그인 후 UI에서 '기존 Chrome 창 사용'을 체크하세요."
    sleep 2
fi

echo "STAB AutoAdd System 시작 중..."
.venv/bin/python autoadd_ui_v2.py
