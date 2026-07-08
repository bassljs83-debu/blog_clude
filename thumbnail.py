"""블로그 대표 썸네일 자동 생성 (Pillow).

AI 그림이 아니라, 제목이 크게 보이는 디자인 카드형 썸네일 (검색·SNS 클릭률에 유리).
그라데이션 배경 + 카테고리 뱃지 + 큰 제목 + (선택) 상품 사진.
"""

from __future__ import annotations

import io
import pathlib

import requests
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
W, H = 1200, 630  # 대표 이미지 표준 비율(1.91:1)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def _gradient(top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        col = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=col)
    return img


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        if draw.textlength(word, font=font) > max_w:  # 단어 자체가 길면 글자 단위로
            s = ""
            for ch in word:
                if draw.textlength(s + ch, font=font) <= max_w:
                    s += ch
                else:
                    lines.append(s)
                    s = ch
            cur = s
        else:
            cur = word
    if cur:
        lines.append(cur)
    return lines


def make_thumbnail(
    title: str,
    out_path: str | pathlib.Path,
    keyword: str = "",
    product_image_url: str | None = None,
    top: tuple = (37, 99, 235),
    bottom: tuple = (13, 148, 136),
) -> pathlib.Path:
    img = _gradient(top, bottom)
    draw = ImageDraw.Draw(img)

    text_max = W - 120
    # (선택) 우측에 상품 사진 카드
    if product_image_url:
        try:
            resp = requests.get(product_image_url, timeout=10)
            resp.raise_for_status()
            pim = Image.open(io.BytesIO(resp.content)).convert("RGB")
            card = 400
            pim.thumbnail((card - 50, card - 50))
            cx, cy = W - card - 50, (H - card) // 2
            draw.rounded_rectangle([cx, cy, cx + card, cy + card], radius=28, fill="white")
            img.paste(pim, (cx + (card - pim.width) // 2, cy + (card - pim.height) // 2))
            text_max = cx - 110
        except Exception:
            text_max = W - 120

    x = 60
    y = 80
    # 카테고리 뱃지
    if keyword:
        bf = _font(34)
        btxt = f"  {keyword} 추천·비교  "
        bw = draw.textlength(btxt, font=bf)
        draw.rounded_rectangle([x, y, x + bw + 16, y + 58], radius=29, fill=(255, 255, 255))
        draw.text((x + 8, y + 10), btxt, font=bf, fill=top)
        y += 96

    # 제목 (줄 수에 맞춰 폰트 크기 자동 조정)
    for size in (76, 66, 58, 50):
        tf = _font(size)
        lines = _wrap(draw, title, tf, text_max)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    line_h = size + 16
    ty = y + 10
    for ln in lines:
        draw.text((x, ty), ln, font=tf, fill="white", stroke_width=2, stroke_fill=(0, 0, 0))
        ty += line_h

    out_path = pathlib.Path(out_path)
    img.save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    make_thumbnail(
        "장마철 원룸 제습기 비교와 구매 가이드",
        "output/thumb_sample.png",
        keyword="제습기",
    )
    print("생성: output/thumb_sample.png")
