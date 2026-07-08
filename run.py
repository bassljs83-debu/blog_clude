"""블로그 글 생성 실행창 (대화형).

터미널 명령어를 몰라도, 이 파일을 더블클릭(블로그생성.command)하면
주제만 입력하면 글 + 태그 + 썸네일이 한 번에 나온다.
"""

from __future__ import annotations

import pathlib
import random
import subprocess
import sys

from generator import generate_article

CATEGORIES_FILE = pathlib.Path(__file__).parent / "categories.txt"


def auto_keyword() -> str:
    """categories.txt 에서 주제를 하나 자동 선택."""
    lines = [
        ln.strip()
        for ln in CATEGORIES_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    return random.choice(lines) if lines else "제습기"


def main() -> None:
    print("=" * 44)
    print("   📝 쿠팡 블로그 글 생성기")
    print("=" * 44)
    print("주제(키워드)를 입력하면 글·태그·썸네일이 자동 생성됩니다.")
    print("예: 제습기, 무선청소기, 가습기, 에어프라이어 ...")
    print("• 그냥 엔터 = 자동으로 주제 골라서 생성")
    print("• 'q' 입력 = 종료\n")

    while True:
        raw = input("👉 주제 키워드 (엔터=자동, q=종료): ").strip()
        if raw.lower() == "q":
            print("종료합니다.")
            return
        keyword = raw or auto_keyword()
        if not raw:
            print(f"🎲 자동 선택된 주제: {keyword}")

        print(f"\n⏳ '{keyword}' 글 생성 중... (1~2분 걸립니다. 잠시만요)\n")
        try:
            html_path = generate_article(keyword)
        except Exception as e:
            print(f"❌ 실패: {e}\n")
            continue

        stem = html_path.stem
        folder = html_path.parent
        print("\n✅ 완료!")
        print(f"   📄 글(HTML)   : {html_path.name}")
        print(f"   🏷  태그        : {stem}_태그.txt")
        print(f"   🖼  썸네일      : {stem}_thumb.png")
        print(f"   📁 폴더        : {folder}")

        # 결과 폴더 열기 (맥)
        try:
            subprocess.run(["open", str(folder)], check=False)
        except Exception:
            pass

        print("\n다른 주제로 계속 만들려면 키워드를 입력하세요. (엔터 = 종료)\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n종료합니다.")
        sys.exit(0)
