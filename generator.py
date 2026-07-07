"""Claude API 기반 SEO 블로그 글 생성기.

핸드오프 원칙 반영:
- 2트랙 중 "정보/비교/구매가이드형" (경험 주장 안 함, 대량생산 주력)
- 6종 필수 골격 슬롯: 롱테일 제목 / 두괄식 3줄요약 / 질문형 H2 / 스펙 표 / FAQ / 대가성 문구
- 검증 루프: 팩트체크(과장·단정·검증불가·안전누락) + AI 문체 교정
- 입력: coupang.py 로 수집한 로켓배송 상품

흐름: 상품수집 → 초안 생성 → 검증·교정(2차 호출) → output/ 저장
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re

import anthropic
import markdown
from dotenv import load_dotenv

from coupang import CoupangClient

load_dotenv()

# 모델은 .env 에서 바꿀 수 있음 (Phase 2 대량생산 시 비용 낮추려면 claude-sonnet-5 등)
MODEL = os.environ.get("GENERATOR_MODEL", "claude-opus-4-8")
OUTPUT_DIR = pathlib.Path(__file__).parent / "output"

# 구글 애드센스 인아티클 광고 (본문 중간 삽입).
# 로더(AD_LOADER)는 페이지당 한 번만, 광고 유닛(AD_UNIT)은 위치마다 반복.
AD_LOADER = (
    '<script async '
    'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
    '?client=ca-pub-3490922798785546" crossorigin="anonymous"></script>'
)
AD_UNIT = """<ins class="adsbygoogle"
     style="display:block; text-align:center;"
     data-ad-layout="in-article"
     data-ad-format="fluid"
     data-ad-client="ca-pub-3490922798785546"
     data-ad-slot="4352988067"></ins>
<script>
(adsbygoogle = window.adsbygoogle || []).push({});
</script>"""

DRAFT_SYSTEM = """당신은 한국어 생활/가전 블로그 SEO 전문 작가입니다.
쿠팡 파트너스 로켓배송 상품 목록을 받아, 구글 상위노출에 강한 "정보/비교/구매가이드형" 글을 씁니다.

## 절대 규칙
- **경험 주장 금지**: "내가 써봤다/2주 사용기" 같은 1인칭 실사용 경험을 지어내지 마세요.
  대신 "이런 기준으로 골랐다", 스펙·가격·특징 비교 관점으로 씁니다.
- **6종 골격을 반드시 모두 포함** (아래 순서):
  1. 롱테일 제목 (# 제목): `[상황]+[대상]+[상품군]+[비교/추천/가이드]` 형태, 빅키워드 말고 구체적으로
  2. 두괄식 3줄 요약 (> 인용 블록, 결론 먼저)
  3. 질문형 H2 (## 로 시작, 실제 검색 질문 형태) — 각 H2의 첫 문장은 질문에 대한 직답
  4. 스펙/특징 비교 표 (마크다운 표, 실제 상품들로)
  5. 자주 묻는 질문 FAQ (## 자주 묻는 질문, Q/A 3개 이상)
  6. 맨 끝 대가성 문구 (아래 지정 문구 그대로)
- 상품 링크는 제공된 URL 그대로 사용 (파트너스 수익 링크 포함됨)

## 팩트체크 (스스로 지킬 것)
- 과장 수치 금지 (근거 없는 "90% 효과" 등)
- 단정 금지 ("무조건 최고", "정석"은 이유를 붙이거나 완화)
- 검증불가 주장 금지 (조건에 따라 다른 결과는 조건 명시)
- 안전/한계 누락 금지 (단점·주의점 솔직히)

## AI 문체 회피
- "~할 수 있습니다" 반복 금지
- 문단 길이를 균일하게 만들지 말 것 (짧은 문장·긴 문장 섞기)
- "또한/게다가/따라서" 같은 접속사 남발 금지
- 리스트 남발 금지 (핵심 외에는 문장으로 녹이기)

## 대가성 문구 (맨 끝에 그대로)
*이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.*

출력은 마크다운 본문만. 설명·머리말 없이 제목(#)부터 시작하세요."""

CRITIC_SYSTEM = """당신은 위 블로그 초안을 검수하는 편집자입니다. 두 가지를 적대적으로 잡아 고칩니다.

1. 팩트체크: 과장 수치 / 단정 표현 / 검증불가 주장 / 안전·한계 누락 을 찾아 수정
2. AI 문체: "~할 수 있습니다" 반복 / 균일 문단 / 접속사 남발 / 리스트 남발 을 교정
3. 6종 골격(롱테일 제목·두괄식 3줄요약·질문형 H2·비교표·FAQ·대가성 문구) 누락 시 보강

수정된 **최종 완성본만** 마크다운으로 출력하세요. 지적 목록이나 설명 없이 글 전체(제목부터 대가성 문구까지)를 다시 출력합니다."""


def _format_products(products: list[dict]) -> str:
    """상품 목록을 프롬프트용 텍스트로 정리."""
    lines = []
    for i, p in enumerate(products, 1):
        name = p.get("productName", "?")
        price = p.get("productPrice", "?")
        url = p.get("productUrl", "")
        lines.append(f"{i}. {name} / {price}원\n   링크: {url}")
    return "\n".join(lines)


def _complete(client: anthropic.Anthropic, system: str, user: str) -> str:
    """스트리밍으로 호출하고 텍스트만 합쳐서 반환 (긴 출력 타임아웃 방지)."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        message = stream.get_final_message()
    return "".join(b.text for b in message.content if b.type == "text").strip()


def generate_article(keyword: str, product_limit: int = 10, verify: bool = True) -> pathlib.Path:
    """키워드로 로켓배송 상품을 모아 SEO 글을 생성하고 output/ 에 저장."""
    coupang = CoupangClient()
    products = coupang.search_products(keyword, limit=product_limit, rocket_only=True)
    if not products:
        raise RuntimeError(f"'{keyword}' 로켓배송 상품이 없습니다. 다른 키워드를 시도하세요.")

    client = anthropic.Anthropic()
    product_text = _format_products(products)

    # 1차: 초안
    draft = _complete(
        client,
        DRAFT_SYSTEM,
        f"키워드: {keyword}\n\n다음 로켓배송 상품들로 정보/비교형 글을 써주세요:\n\n{product_text}",
    )

    # 2차: 검증·교정
    final = _complete(client, CRITIC_SYSTEM, draft) if verify else draft

    # 저장: .md(편집·참고용) + .html(티스토리 HTML 모드 붙여넣기용)
    OUTPUT_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w가-힣]+", "_", keyword).strip("_")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = OUTPUT_DIR / f"{stamp}_{safe}.md"
    html_path = OUTPUT_DIR / f"{stamp}_{safe}.html"
    md_path.write_text(final, encoding="utf-8")
    html = md_to_html(final)
    html = insert_product_cards(html, products)  # 도입부 뒤 상품 카드 갤러리
    html = insert_ads(html)  # 섹션 사이 애드센스
    html_path.write_text(html, encoding="utf-8")
    return html_path


def md_to_html(md_text: str) -> str:
    """마크다운을 티스토리 HTML 모드에 붙여넣을 HTML 조각으로 변환.

    표(tables)와 리스트를 제대로 렌더링하도록 확장 지정.
    """
    return markdown.markdown(md_text, extensions=["tables", "sane_lists"])


def product_cards_html(products: list[dict]) -> str:
    """상품 목록을 썸네일 카드 갤러리 HTML 로 변환 (이미지+상품명+가격+구매버튼)."""
    cards = []
    for p in products:
        name = p.get("productName", "")
        img = p.get("productImage", "")
        url = p.get("productUrl", "")
        price = p.get("productPrice")
        price_str = f"{int(price):,}원" if isinstance(price, (int, float)) else ""
        if not (img and url):
            continue
        cards.append(
            '<div style="flex:1 1 150px;max-width:180px;border:1px solid #eee;'
            'border-radius:8px;padding:10px;text-align:center;">'
            f'<a href="{url}" target="_blank" rel="nofollow sponsored" '
            'style="text-decoration:none;color:inherit;">'
            f'<img src="{img}" alt="{name}" '
            'style="width:100%;height:auto;border-radius:4px;" loading="lazy">'
            f'<div style="font-size:13px;margin-top:8px;line-height:1.3;">{name}</div>'
            f'<div style="font-weight:bold;margin-top:4px;color:#c0392b;">{price_str}</div>'
            '<div style="margin-top:6px;font-size:12px;color:#2980b9;">최저가 보기 ›</div>'
            "</a></div>"
        )
    if not cards:
        return ""
    return (
        '<div style="display:flex;flex-wrap:wrap;gap:12px;margin:20px 0;'
        'justify-content:center;">' + "".join(cards) + "</div>"
    )


def insert_product_cards(html: str, products: list[dict]) -> str:
    """도입부(첫 요약) 다음, 첫 <h2> 앞에 상품 카드 갤러리를 삽입."""
    gallery = product_cards_html(products)
    if not gallery:
        return html
    m = re.search(r"<h2", html)
    if not m:
        return html + gallery
    pos = m.start()
    return f"{html[:pos]}{gallery}\n{html[pos:]}"


def insert_ads(html: str) -> str:
    """본문 섹션(<h2>) 사이 클릭 잘 나오는 위치에 애드센스 광고를 삽입.

    - 제목/도입부 위에는 넣지 않음 (애드센스 정책·UX)
    - 한 글에 최대 3개, 첫 섹션 뒤 / 중간 / FAQ 직전에 분산
    - 로더는 맨 위 광고 1회만
    """
    positions = [m.start() for m in re.finditer(r"<h2", html)]
    if len(positions) < 2:  # 섹션이 거의 없으면 끝에 하나만
        return f"{html}\n{AD_LOADER}\n{AD_UNIT}"

    n = len(positions)
    targets = sorted({1, n // 2, n - 1})  # 첫 섹션 뒤·중간·마지막(FAQ) 앞
    targets = [i for i in targets if 1 <= i <= n - 1][:3]
    first = targets[0]

    out = html
    for i in sorted(targets, reverse=True):  # 뒤에서부터 삽입해 앞 오프셋 유지
        block = f"{AD_LOADER}\n{AD_UNIT}" if i == first else AD_UNIT
        pos = positions[i]
        out = f"{out[:pos]}{block}\n{out[pos:]}"
    return out


if __name__ == "__main__":
    import sys

    kw = sys.argv[1] if len(sys.argv) > 1 else "제습기"
    print(f"'{kw}' 글 생성 중... (모델: {MODEL})")
    out = generate_article(kw)
    print(f"✅ 저장됨(HTML, 티스토리 HTML 모드에 붙여넣기): {out}")
    print(f"   원본 마크다운: {out.with_suffix('.md')}")
