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
import json
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
    html = enhance_tables(html, products)  # 표 제품명 링크화 + 좌우 스크롤
    html = insert_ads(html)  # 섹션 사이 애드센스
    html_path.write_text(html, encoding="utf-8")

    # 태그 추천 저장 (티스토리 태그 칸에 붙여넣기용, 쉼표 구분)
    tags = suggest_tags(keyword, products)
    (OUTPUT_DIR / f"{stamp}_{safe}_태그.txt").write_text(", ".join(tags), encoding="utf-8")

    # 사용한 상품 데이터 저장 (나중에 재조립해도 카드·표·링크가 어긋나지 않게)
    (OUTPUT_DIR / f"{stamp}_{safe}_products.json").write_text(
        json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return html_path


def md_to_html(md_text: str) -> str:
    """마크다운을 티스토리 HTML 모드에 붙여넣을 HTML 조각으로 변환.

    표(tables)와 리스트를 제대로 렌더링하도록 확장 지정.
    """
    return markdown.markdown(md_text, extensions=["tables", "sane_lists"])


def suggest_tags(keyword: str, products: list[dict], limit: int = 12) -> list[str]:
    """키워드 + 검색의도 + 브랜드명으로 티스토리 태그를 추천."""
    tags = [keyword]
    for modifier in ("추천", "비교", "순위", "가격", "후기"):
        tags.append(f"{keyword}{modifier}")

    # 상품명 첫 단어를 브랜드 후보로 (한글/영문 2~6자만, 잡토큰 제외)
    for p in products:
        name = p.get("productName", "").split()
        first = name[0] if name else ""
        if 2 <= len(first) <= 6 and re.fullmatch(r"[A-Za-z가-힣]+", first):
            brand_tag = f"{first}{keyword}"
            if brand_tag not in tags:
                tags.append(brand_tag)

    # 중복 제거(순서 유지) 후 제한
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


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
        '<h3 style="margin:28px 0 8px;">이 글에서 소개한 상품 바로가기</h3>'
        '<div style="display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 20px;'
        'justify-content:center;">' + "".join(cards) + "</div>"
    )


def insert_product_cards(html: str, products: list[dict]) -> str:
    """스펙 비교표(첫 </table>) 아래에 상품 카드 갤러리를 삽입."""
    gallery = product_cards_html(products)
    if not gallery:
        return html
    marker = "</table>"
    idx = html.find(marker)
    if idx != -1:  # 비교표 바로 다음
        pos = idx + len(marker)
        return f"{html[:pos]}\n{gallery}{html[pos:]}"
    # 표가 없으면 첫 <h2> 앞(폴백)
    m = re.search(r"<h2", html)
    pos = m.start() if m else len(html)
    return f"{html[:pos]}{gallery}\n{html[pos:]}"


def _match_product_url(cell_text: str, products: list[dict]) -> str | None:
    """표 셀 텍스트를 상품과 매칭해 URL 반환 (모델명·토큰 겹침 기준)."""
    best_url, best_score = None, 0
    ctokens = set(re.findall(r"[A-Za-z가-힣0-9]+", cell_text))
    for p in products:
        name = p.get("productName", "")
        url = p.get("productUrl", "")
        if not url:
            continue
        score = 0
        # 모델명 토큰(영문+숫자, 예: LM-CSJ-01, DB-DH7)이 셀에 그대로 있으면 강한 매칭
        for mt in re.findall(r"[A-Za-z][-A-Za-z0-9]*\d[-A-Za-z0-9]*", name):
            if len(mt) >= 4 and mt in cell_text:
                score += 5
        # 브랜드(첫 단어)가 셀에 있으면 강한 신호 (브랜드는 대체로 고유)
        first_word = name.split()[0] if name.split() else ""
        if first_word and first_word in ctokens:
            score += 3
        # 이름 토큰 겹침
        ptokens = set(re.findall(r"[A-Za-z가-힣0-9]+", name))
        score += len(ptokens & ctokens)
        if score > best_score:
            best_score, best_url = score, url
    return best_url if best_score >= 2 else None


def _linkify_first_cell(row_html: str, products: list[dict]) -> str:
    """행의 첫 <td> 텍스트를 상품 링크로 감싼다 (이미 링크면 skip)."""
    m = re.search(r"(<td[^>]*>)(.*?)(</td>)", row_html, flags=re.S)
    if not m:
        return row_html  # 헤더행(<th>) 등은 건너뜀
    inner = m.group(2)
    if "<a" in inner:
        return row_html
    cell_text = re.sub(r"<[^>]+>", "", inner)
    url = _match_product_url(cell_text, products)
    if not url:
        return row_html
    linked = (
        f'<a href="{url}" target="_blank" rel="nofollow sponsored" '
        f'style="color:#2980b9;text-decoration:underline;">{inner}</a>'
    )
    return row_html[: m.start(2)] + linked + row_html[m.end(2) :]


def enhance_tables(html: str, products: list[dict]) -> str:
    """표: 제품명 링크화 + 좌우 스크롤 + 줄바꿈 방지 + 셀 스타일."""

    def process(match: "re.Match") -> str:
        table = match.group(0)
        table = table.replace(
            "<td>", '<td style="padding:8px 12px;border:1px solid #ddd;">'
        ).replace(
            "<th>",
            '<th style="padding:8px 12px;border:1px solid #ddd;background:#f6f6f6;">',
        )
        table = re.sub(
            r"<tr>.*?</tr>",
            lambda r: _linkify_first_cell(r.group(0), products),
            table,
            flags=re.S,
        )
        table = re.sub(
            r"<table[^>]*>",
            '<table style="border-collapse:collapse;min-width:640px;'
            'white-space:nowrap;">',
            table,
        )
        return (
            '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;'
            f'margin:16px 0;">{table}</div>'
        )

    return re.sub(r"<table.*?</table>", process, html, flags=re.S)


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
    tag_file = out.parent / f"{out.stem}_태그.txt"
    if tag_file.exists():
        print(f"   추천 태그: {tag_file.read_text(encoding='utf-8')}")
