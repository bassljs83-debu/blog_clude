"""쿠팡 파트너스 Open API 클라이언트.

- HMAC-SHA256 서명 인증
- ⚠️ 서명 메시지는 `signed_date + method + path + query` 이며 path와 query 사이에
  `?` 를 넣지 않는다 (공식 가이드 기준). 이걸 넣으면 401 이 난다. (과거 버그 원인)
- 로켓배송만 쓰기 위해 `isRocket == True` 하드 필터 제공.

API 키는 .env 에서 로드 (COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY).
"""

from __future__ import annotations  # Python 3.9 에서 str | None 등 최신 타입 문법 허용

import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api-gateway.coupang.com"
API_PREFIX = "/v2/providers/affiliate_open_api/apis/openapi/v1"

# 상품 검색 API 의 limit 최대값 (실측: 10까지만 허용, 11부터 400)
SEARCH_MAX_LIMIT = 10


def _generate_authorization(
    method: str,
    path_with_query: str,
    access_key: str,
    secret_key: str,
    signed_date: str | None = None,
) -> str:
    """CEA HMAC-SHA256 Authorization 헤더 문자열을 생성한다.

    signed_date 를 넘기면 그대로 사용 (테스트용). 안 넘기면 현재 GMT 사용.
    """
    path, _, query = path_with_query.partition("?")  # ← query 앞의 '?' 는 서명에서 제외
    if signed_date is None:
        signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")

    message = signed_date + method + path + query
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return (
        f"CEA algorithm=HmacSHA256, access-key={access_key}, "
        f"signed-date={signed_date}, signature={signature}"
    )


class CoupangClient:
    def __init__(self, access_key: str | None = None, secret_key: str | None = None):
        self.access_key = access_key or os.environ.get("COUPANG_ACCESS_KEY", "")
        self.secret_key = secret_key or os.environ.get("COUPANG_SECRET_KEY", "")
        if not self.access_key or not self.secret_key:
            raise RuntimeError(
                "쿠팡 API 키가 없습니다. .env 에 COUPANG_ACCESS_KEY / "
                "COUPANG_SECRET_KEY 를 설정하세요."
            )

    def _request(self, method: str, path_with_query: str, body: dict | None = None) -> dict:
        authorization = _generate_authorization(
            method, path_with_query, self.access_key, self.secret_key
        )
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json;charset=UTF-8",
        }
        resp = requests.request(
            method,
            BASE_URL + path_with_query,
            headers=headers,
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        # 쿠팡은 HTTP 200 안에 rCode 로 에러를 담아 보낸다. rCode 0 이 성공.
        # (rMessage 는 성공 시에도 대가성 문구 안내가 담겨오므로 rCode 로만 판단)
        r_code = payload.get("rCode")
        if r_code is not None and str(r_code) != "0":
            raise RuntimeError(
                f"쿠팡 API 오류 [rCode={r_code}]: {payload.get('rMessage')}"
            )
        return payload

    # ------------------------------------------------------------------ #
    # 상품 조회 API
    # ------------------------------------------------------------------ #
    def search_products(
        self, keyword: str, limit: int = SEARCH_MAX_LIMIT, rocket_only: bool = True
    ) -> list[dict]:
        """키워드로 상품 검색. rocket_only=True 면 로켓배송만 반환.

        limit 은 SEARCH_MAX_LIMIT(10) 을 넘으면 400 이므로 자동으로 클램프한다.
        """
        limit = min(limit, SEARCH_MAX_LIMIT)
        query = urlencode({"keyword": keyword, "limit": limit})
        path = f"{API_PREFIX}/products/search?{query}"
        data = self._request("GET", path)
        products = data.get("data", {}).get("productData", [])
        return _filter_rocket(products) if rocket_only else products

    def get_goldbox(self, limit: int = 50, rocket_only: bool = True) -> list[dict]:
        """골드박스(오늘의 특가) 상품."""
        query = urlencode({"limit": limit})
        path = f"{API_PREFIX}/products/goldbox?{query}"
        data = self._request("GET", path)
        products = data.get("data", [])
        return _filter_rocket(products) if rocket_only else products

    def get_best_category(
        self, category_id: int, limit: int = 50, rocket_only: bool = True
    ) -> list[dict]:
        """카테고리별 베스트 상품. category_id 는 쿠팡 카테고리 ID."""
        query = urlencode({"limit": limit})
        path = f"{API_PREFIX}/products/bestcategories/{category_id}?{query}"
        data = self._request("GET", path)
        products = data.get("data", [])
        return _filter_rocket(products) if rocket_only else products

    def create_deeplinks(self, coupang_urls: list[str]) -> list[dict]:
        """일반 쿠팡 URL 목록을 파트너스 수익 딥링크로 변환."""
        path = f"{API_PREFIX}/deeplink"
        data = self._request("POST", path, body={"coupangUrls": coupang_urls})
        return data.get("data", [])


def _filter_rocket(products: list[dict]) -> list[dict]:
    """isRocket == True 인 상품만 남긴다 (하드 필터)."""
    return [p for p in products if p.get("isRocket")]


if __name__ == "__main__":
    # 실제 API 호출 테스트 (키가 .env 에 있어야 동작)
    import sys

    keyword = sys.argv[1] if len(sys.argv) > 1 else "제습기"
    client = CoupangClient()
    items = client.search_products(keyword, limit=20, rocket_only=True)
    print(f"'{keyword}' 로켓배송 상품 {len(items)}개\n")
    for p in items[:10]:
        price = p.get("productPrice", "?")
        print(f"  - {p.get('productName')} / {price}원")
        print(f"    {p.get('productUrl')}")
