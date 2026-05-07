#!/bin/bash
set -e

echo ""
echo "========================================"
echo "  STAB AutoAdd System - Mac 설치"
echo "  Made by SGI"
echo "========================================"
echo ""

# Python 확인
if ! command -v python3 &>/dev/null; then
    echo "[오류] Python 3이 설치되어 있지 않습니다."
    echo "아래 명령으로 설치하세요 (Homebrew):"
    echo "  brew install python"
    echo "또는 https://www.python.org/downloads/"
    exit 1
fi

PYVER=$(python3 --version 2>&1)
echo "$PYVER 확인 완료"

# 가상환경 생성
if [ -d ".venv" ]; then
    echo "기존 가상환경이 있습니다. 재사용합니다."
else
    echo "가상환경 생성 중..."
    python3 -m venv .venv
fi

# 패키지 설치
echo ""
echo "패키지 설치 중 (최초 설치 시 수 분 소요)..."
echo "easyocr / torch 다운로드가 포함되어 시간이 걸릴 수 있습니다."
echo ""
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt

# 실행 권한
chmod +x run.sh

echo ""
echo "----------------------------------------"
echo "Chrome 브라우저가 필요합니다."
echo "없으면 https://www.google.com/chrome 에서 설치하세요."
echo "----------------------------------------"
echo ""
echo "설치 완료!"
echo "아래 명령으로 프로그램을 시작하세요:"
echo "  ./run.sh"
echo ""
