"""
KOSIS(국가통계포털) 온라인쇼핑동향조사 API 클라이언트.

통계표: DT_1KE10041 (온라인쇼핑몰 취급상품범위별/상품군별거래액)
실제 응답 필드 구조 (2026-07 확인):
    C1_OBJ_NM : 분류1 축 이름 ("상품군별")
    C1_NM     : 분류1 값 (카테고리명, 예: "합계", "컴퓨터 및 주변기기")
    C2_OBJ_NM : 분류2 축 이름 ("범위별")
    C2_NM     : 분류2 값 ("계" / "종합몰" / "전문몰")
    PRD_DE    : 시점 (YYYYMM)
    DT        : 거래액 값 (백만원, 문자열)
    UNIT_NM   : 단위명 ("백만원")
    ITM_NM    : 항목명 ("거래액")

주의: 카테고리 전체 거래액을 보려면 반드시 C2_NM == "계" 인 행만 써야 한다.
      "종합몰"이나 "전문몰"만 보면 그 카테고리의 일부만 보게 된다.
"""
import json
import os
import re

import pandas as pd
import requests

DEFAULT_KOSIS_BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
TBL_ID = "DT_1KE10041"   # 온라인쇼핑몰 취급상품범위별/상품군별거래액
ORG_ID = "101"
ITM_ID = "T20"           # 거래액


def _request_with_proxy_fallback(method, url, **kwargs):
    """
    Windows 시스템 설정(제어판 인터넷 옵션)이나 환경변수에 남아있는 프록시가
    죽어 있으면(예: 껐다 켠 VPN/사내망 도구), requests가 그 프록시로만
    연결을 시도하다 ProxyError로 실패한다 — 인터넷 자체는 멀쩡한데도.
    이 경우에만 프록시를 무시한 직접 연결로 한 번 더 시도한다. 프록시가
    실제로 필요한 환경에서는 첫 시도가 성공하므로 동작이 그대로다.
    """
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.ProxyError:
        session = requests.Session()
        session.trust_env = False
        return session.request(method, url, **kwargs)


def build_kosis_url(api_key: str, start_prd_de: str, end_prd_de: str) -> str:
    """실제 사용한 URL 패턴을 그대로 재현 (파라미터만 교체 가능하게)"""
    base_url = os.environ.get("KOSIS_BASE_URL", DEFAULT_KOSIS_BASE_URL)
    params = {
        "method": "getList",
        "apiKey": api_key,
        "itmId": ITM_ID + "+",
        "objL1": "ALL",
        "objL2": "ALL",
        "format": "json",
        "jsonVD": "Y",
        "prdSe": "M",
        "startPrdDe": start_prd_de,
        "endPrdDe": end_prd_de,
        "outputFields": "OBJ_NM+NM+ITM_NM+UNIT_NM+PRD_DE+",
        "orgId": ORG_ID,
        "tblId": TBL_ID,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base_url}?{query}"


def parse_kosis_response(raw_text: str) -> pd.DataFrame:
    """
    KOSIS 응답 텍스트를 DataFrame으로 변환.
    끝부분이 잘려서 마지막 객체가 불완전해도(네트워크 중간 절단 등)
    완전한 객체만 골라내 파싱하는 방어적 처리를 포함한다.
    """
    try:
        records = json.loads(raw_text)
    except json.JSONDecodeError:
        # 완전한 {...} 객체만 정규식으로 추출해서 개별 파싱 (끝부분 잘림 대응)
        records = []
        for m in re.finditer(r"\{[^{}]*\}", raw_text):
            try:
                records.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("KOSIS 응답에서 파싱된 레코드가 없습니다. 원본 형식을 확인하세요.")

    required_cols = {"C1_NM", "C2_NM", "PRD_DE", "DT"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"KOSIS 응답에 필요한 필드가 없습니다: {missing}")

    # 범위별 "계"만 사용 (종합몰+전문몰 합산된 카테고리 전체 거래액)
    df = df[df["C2_NM"] == "계"].copy()
    df["revenue_million_krw"] = pd.to_numeric(df["DT"], errors="coerce")
    df = df.rename(columns={"C1_NM": "category_kosis", "PRD_DE": "month"})
    df = df[["month", "category_kosis", "revenue_million_krw"]]
    df = df.sort_values(["category_kosis", "month"]).reset_index(drop=True)
    return df


def fetch_online_shopping_revenue(start_prd_de: str, end_prd_de: str,
                                   api_key: str = None, timeout: int = 15) -> pd.DataFrame:
    """실제 API 호출. api_key 없으면 환경변수 KOSIS_API_KEY 사용."""
    api_key = api_key or os.environ.get("KOSIS_API_KEY")
    if not api_key:
        raise RuntimeError("KOSIS_API_KEY가 설정되지 않았습니다.")
    url = build_kosis_url(api_key, start_prd_de, end_prd_de)
    resp = _request_with_proxy_fallback("get", url, timeout=timeout)
    resp.raise_for_status()
    return parse_kosis_response(resp.text)


if __name__ == "__main__":
    # 네트워크 없이, 사용자가 제공한 실제 응답 샘플로 파서 검증
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample_path = os.path.join(base_dir, "sample-data", "kosis_sample_raw.txt")
    if not os.path.exists(sample_path):
        sample_path = os.path.join(base_dir, "data", "samples", "kosis_sample_raw.txt")
    with open(sample_path, encoding="utf-8") as f:
        raw = f.read()
    df = parse_kosis_response(raw)
    print(df)
    print("\n카테고리 목록:", df["category_kosis"].unique().tolist())
