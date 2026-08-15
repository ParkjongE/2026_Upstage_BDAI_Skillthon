"""
네이버 데이터랩 - 쇼핑인사이트(카테고리 클릭추이) API 클라이언트.

실제 응답 구조 (2026-07 확인, POST 요청):
{
  "startDate": "...", "endDate": "...", "timeUnit": "...",
  "results": [
    {
      "title": "패션의류",          # 카테고리명 (요청 시 넣은 name 그대로 돌아옴)
      "category": ["50000000"],    # 카테고리 코드
      "data": [{"period": "2026-07-21", "ratio": 78.46801}, ...]
    }
  ]
}

ratio는 절대 검색량이 아니라, 조회 구간 전체에서 최댓값을 100으로 하는
상대지수다. 카테고리 간 절대 비교 금지, 같은 카테고리의 시간 흐름
비교에만 사용해야 한다 (SKILL.md BOUNDARY 참고).

⚠️ 카테고리 코드(param) 전체 목록은 네이버 데이터랩 API 개발가이드
문서의 표로 제공되며, 이 파일에는 확인된 일부만 category-mapping.md에
반영돼 있다. 새 카테고리를 추가하려면 개발가이드에서 정확한 코드를
확인해야 한다 (임의 추측 금지 - SKILL.md BOUNDARY 원칙과 동일).
"""
import json
import os

import pandas as pd
import requests

SHOPPING_INSIGHT_URL = "https://openapi.naver.com/v1/datalab/shopping/categories"
SEARCH_TREND_URL = "https://openapi.naver.com/v1/datalab/search"

API_HUB_SHOPPING_INSIGHT_URL = "https://naverapihub.apigw.ntruss.com/shopping/v1/categories"
API_HUB_SEARCH_TREND_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"

def _request_with_proxy_fallback(method, url, **kwargs):
    """
    kosis_client.py의 동일 함수와 같은 목적: 죽어있는 시스템/환경변수
    프록시 때문에 ProxyError가 나면, 프록시를 무시한 직접 연결로 한 번 더
    시도한다(정상적으로 프록시가 필요한 환경은 첫 시도에서 이미 성공).
    """
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.ProxyError:
        session = requests.Session()
        session.trust_env = False
        return session.request(method, url, **kwargs)


PROVIDER_CONFIG = {
    "developers": {
        "shopping_url": SHOPPING_INSIGHT_URL,
        "search_url": SEARCH_TREND_URL,
        "id_header": "X-Naver-Client-Id",
        "secret_header": "X-Naver-Client-Secret",
    },
    "api_hub": {
        "shopping_url": API_HUB_SHOPPING_INSIGHT_URL,
        "search_url": API_HUB_SEARCH_TREND_URL,
        "id_header": "X-NCP-APIGW-API-KEY-ID",
        "secret_header": "X-NCP-APIGW-API-KEY",
    },
}


def get_provider_config(provider: str = None) -> dict:
    provider = (provider or os.environ.get("NAVER_API_PROVIDER", "developers")).strip()
    if provider not in PROVIDER_CONFIG:
        raise RuntimeError(
            f"NAVER_API_PROVIDER={provider!r}는 지원하지 않습니다. "
            f"가능한 값: {', '.join(PROVIDER_CONFIG)}"
        )
    return PROVIDER_CONFIG[provider]


def build_shopping_category_body(category_name: str, category_code: str,
                                  start_date: str, end_date: str,
                                  time_unit: str = "month") -> dict:
    """
    start_date/end_date: 'YYYY-MM-DD' 형식.
    time_unit: 'date' | 'week' | 'month'
    """
    return {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": time_unit,
        "category": [{"name": category_name, "param": [category_code]}],
    }


def parse_shopping_insight_response(resp_json: dict) -> pd.DataFrame:
    """results[].data[] 를 (period, category_title, category_code, ratio) 표로 변환"""
    rows = []
    for result in resp_json.get("results", []):
        title = result.get("title")
        codes = result.get("category", [])
        code = codes[0] if codes else None
        for point in result.get("data", []):
            rows.append({
                "period": point["period"],
                "category_title": title,
                "category_code": code,
                "search_ratio": point["ratio"],
            })
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("네이버 응답에서 파싱된 데이터가 없습니다.")
    return df.sort_values(["category_title", "period"]).reset_index(drop=True)


def fetch_shopping_category_trend(category_name: str, category_code: str,
                                   start_date: str, end_date: str,
                                   time_unit: str = "month",
                                   client_id: str = None, client_secret: str = None,
                                   timeout: int = 15, provider: str = None) -> pd.DataFrame:
    client_id = client_id or os.environ.get("NAVER_CLIENT_ID")
    client_secret = client_secret or os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정되지 않았습니다.")

    config = get_provider_config(provider)
    body = build_shopping_category_body(category_name, category_code, start_date, end_date, time_unit)
    headers = {
        config["id_header"]: client_id,
        config["secret_header"]: client_secret,
        "Content-Type": "application/json",
    }
    resp = _request_with_proxy_fallback(
        "post", config["shopping_url"], headers=headers, data=json.dumps(body), timeout=timeout
    )
    resp.raise_for_status()
    return parse_shopping_insight_response(resp.json())


if __name__ == "__main__":
    # 네트워크 없이, 실제 응답 샘플로 파서 검증
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample_path = os.path.join(base_dir, "sample-data", "naver_sample_response.json")
    if not os.path.exists(sample_path):
        sample_path = os.path.join(base_dir, "data", "samples", "naver_sample_response.json")
    with open(sample_path, encoding="utf-8") as f:
        samples = json.load(f)

    df = parse_shopping_insight_response(samples["shopping_insight_sample"])
    print("[쇼핑인사이트 파싱 결과]")
    print(df)

    # ratio가 상대지수(0~100)인지 확인 - 여러 그룹을 같이 조회하면 그중 최댓값이 100
    from_search = samples["search_trend_sample"]["results"]
    max_ratio = max(p["ratio"] for r in from_search for p in r["data"])
    print(f"\n검색어트렌드 샘플 내 최댓값: {max_ratio} (100이면 상대지수 가정이 맞음)")
