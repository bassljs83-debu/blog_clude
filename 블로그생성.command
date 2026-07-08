#!/bin/bash
# 더블클릭하면 블로그 글 생성기가 실행됩니다.
cd "$(dirname "$0")"
"./.venv/bin/python" run.py
echo ""
echo "창을 닫으려면 아무 키나 누르세요..."
read -n 1 -s
