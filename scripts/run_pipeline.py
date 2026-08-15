"""
ecommerce-trend-signal Skill의 진입점(entry point).

사용법:
    python3 run_pipeline.py --category fashion_apparel
    python3 run_pipeline.py --category fashion_apparel --z-threshold 2.0 --min-sample 6

SKILL.md의 절차를 그대로 코드로 옮긴 것이므로, SKILL.md가 바뀌면
이 파일도 같이 갱신해야 한다.
"""
import argparse
import json
import math
import os
import re
import sys
from calendar import monthrange
from datetime import date, datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from generate_demo_data import generate_demo_timeseries
from kosis_client import fetch_online_shopping_revenue
from naver_client import fetch_shopping_category_trend

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def first_existing_path(*relative_paths):
    for rel in relative_paths:
        path = os.path.join(BASE_DIR, rel)
        if os.path.exists(path):
            return path
    return os.path.join(BASE_DIR, relative_paths[0])


MAPPING_PATH = first_existing_path(
    os.path.join("reference", "category-mapping.md"),
    os.path.join("references", "category-mapping.md"),
)
TEMPLATE_PATH = first_existing_path(
    os.path.join("templates", "report-template.html"),
    os.path.join("assets", "report-template.html"),
)

DEFAULT_Z_THRESHOLD = 2.0
DEFAULT_MIN_SAMPLE = 6
DEFAULT_REVENUE_GROWTH_THRESHOLD = 0.10  # 계절 기대치 대비 +10%
DEFAULT_PERIOD_MONTHS = 48
# 로지스틱회귀 확률을 등급형 판정으로 나누는 경계. render_report()의 판정
# 분기와 리포트의 확률 게이지가 같은 값을 써야 하므로 상수로 뺐다.
PROB_SIGNAL_THRESHOLD = 60.0
PROB_NOISE_THRESHOLD = 35.0

DEFAULTS_YAML_PATH = first_existing_path(os.path.join("config", "defaults.yaml"))

# config/defaults.yaml의 한글 키 -> 이 스크립트의 파라미터 이름 매핑.
# PyYAML 의존성 없이 이 파일이 실제로 정의하는 얕은 key: value 구조만 읽는다
# (일반 YAML 파서가 아님 — defaults.yaml 구조가 바뀌면 이 매핑도 같이 갱신).
_DEFAULTS_KEY_MAP = {
    "분석_개월수": "period_months",
    "이상탐지_z_임계치": "z_threshold",
    "최소_표본수": "min_sample",
    "거래액_성장_임계치": "revenue_growth_threshold",
}


def load_runtime_defaults():
    """
    config/defaults.yaml이 있으면 그 값으로 하드코딩된 기본값을 덮어써서,
    이 파일이 "참고 문서"가 아니라 실제로 파이프라인 동작을 바꾸게 한다.
    파일이 없거나 파싱 가능한 줄이 없으면 하드코딩된 기본값을 그대로 쓴다
    (import 실패나 예외로 파이프라인 자체가 멈추면 안 됨).
    """
    defaults = {
        "period_months": DEFAULT_PERIOD_MONTHS,
        "z_threshold": DEFAULT_Z_THRESHOLD,
        "min_sample": DEFAULT_MIN_SAMPLE,
        "revenue_growth_threshold": DEFAULT_REVENUE_GROWTH_THRESHOLD,
    }
    if not os.path.exists(DEFAULTS_YAML_PATH):
        return defaults
    try:
        with open(DEFAULTS_YAML_PATH, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                param = _DEFAULTS_KEY_MAP.get(key)
                if not param or not value or value.startswith("["):
                    continue
                try:
                    defaults[param] = int(value) if param in ("period_months", "min_sample") else float(value)
                except ValueError:
                    continue
    except OSError:
        pass
    return defaults


RUNTIME_DEFAULTS = load_runtime_defaults()


def missing_required_assets():
    required = [
        ("카테고리 매핑표", MAPPING_PATH),
        ("HTML 리포트 템플릿", TEMPLATE_PATH),
    ]
    return [(label, path) for label, path in required if not os.path.exists(path)]


# ---------- 1. 입력 검증 ----------
def load_local_env_files():
    """로컬 실행 편의를 위해 .env를 읽되, 비어 있지 않은 시스템 값은 유지한다."""
    env_paths = []
    seen = set()

    def add_env_chain(start_dir, depth=6):
        current = os.path.abspath(start_dir)
        for _ in range(depth):
            path = os.path.join(current, ".env")
            if path not in seen:
                env_paths.append(path)
                seen.add(path)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    add_env_chain(os.getcwd())
    add_env_chain(BASE_DIR)
    add_env_chain(os.path.dirname(__file__))

    for path in env_paths:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and not os.environ.get(key):
                    os.environ[key] = value


def redact_sensitive(text):
    """로그/리포트에 API 키나 시크릿이 섞이지 않게 실패 메시지를 마스킹한다."""
    safe = str(text)
    for env_key in ("KOSIS_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        value = os.environ.get(env_key)
        if value:
            safe = safe.replace(value, "<redacted>")
    safe = re.sub(r"(apiKey=)[^&\s)]+", r"\1<redacted>", safe)
    return safe


def strip_markdown(value):
    value = re.sub(r"\*\*", "", value)
    value = value.replace("✅", "").strip()
    return value


def parse_naver_cell(value):
    clean = strip_markdown(value)
    codes = re.findall(r"\b\d{8}\b", clean)
    # "화장품/미용 / 50000002"처럼 카테고리명 자체에 "/"가 들어갈 수 있다.
    # 코드 앞 구분자만 제거해야 하며, 단순 split("/")를 쓰면 "화장품"처럼
    # 앞 조각만 남아 자연어 매칭과 API 요청명이 어색해진다.
    name_part = re.sub(r"\s*/\s*\d{8}(?:\s*,\s*\d{8})*.*$", "", clean).strip()
    name_part = re.sub(r"\s+", " ", name_part)
    return name_part, codes


def load_category_mapping():
    """
    reference/category-mapping.md의 표를 파싱해 category_key -> row dict로 반환.
    표는 `| category_key | 네이버 ... | KOSIS 상품군 | 자연어 별칭 | 검증 상태 |`
    5칸 형식을 기대하지만, 별칭 칸이 없는 4칸 표도 하위호환으로 허용한다
    (그 경우 별칭은 빈 목록).
    """
    with open(MAPPING_PATH, encoding="utf-8") as f:
        text = f.read()
    rows = {}
    for line in text.splitlines():
        if not line.startswith("|") or "category_key" in line or set(line.strip()) <= {"|", "-"}:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        naver_path = strip_markdown(cells[1])
        naver_name, naver_codes = parse_naver_cell(cells[1])
        aliases = [a.strip() for a in cells[3].split(",") if a.strip()] if len(cells) >= 5 else []
        key = cells[0]
        rows[key] = {
            "naver_path": naver_path,
            "naver_name": naver_name,
            "naver_codes": naver_codes,
            "kosis_category": cells[2],
            "aliases": aliases,
            "status": cells[-1],
        }
    return rows


def _normalize_for_match(text):
    """대소문자·공백·구분기호 차이를 무시하고 비교하기 위한 정규화."""
    text = strip_markdown(text or "")
    text = re.sub(r"[\s\-_/·>()]+", "", text)
    return text.lower()


def resolve_category_key(user_input, mapping):
    """
    사용자가 자연어로 준 카테고리 표현(예: '패션의류', '신발 카테고리', 'Shoes')을
    category-mapping.md의 내부 category_key로 변환한다.

    이 해석 단계가 없으면, 에이전트가 자연어 카테고리명을 그대로
    --category 인자로 넘겼을 때 validate_category가 실패해 리포트가
    전혀 생성되지 않는 문제(가장 흔한 실패 원인)가 생긴다.

    반환값: (resolved_key 또는 None, 후보 category_key 목록, 사용자에게 보여줄 메모)
    - 정확히 1개로 좁혀지면 그 key를 반환한다.
    - 0개 또는 2개 이상이면 None과 후보 목록을 반환해, 호출자가 사용자에게
      명확한 후보를 되물을 수 있게 한다 (임의로 하나를 추측하지 않는다).
    """
    if not user_input:
        return None, [], "카테고리 값이 비어 있습니다."

    norm_input = _normalize_for_match(user_input)

    # 1) category_key 자체와 정확히 일치 (가장 신뢰할 수 있는 경로)
    for key in mapping:
        if _normalize_for_match(key) == norm_input:
            return key, [key], f"'{user_input}'는 category_key '{key}'와 정확히 일치합니다."

    # 2) 자연어 별칭과 정확히 일치
    exact_alias_hits = [
        key for key, row in mapping.items()
        if any(_normalize_for_match(alias) == norm_input for alias in row["aliases"])
    ]
    if len(exact_alias_hits) == 1:
        key = exact_alias_hits[0]
        return key, [key], f"'{user_input}'는 별칭 목록에서 category_key '{key}'와 일치합니다."

    # 3) 네이버 표기·통계청 상품군명과 정확히 일치
    exact_name_hits = [
        key for key, row in mapping.items()
        if _normalize_for_match(row["naver_name"]) == norm_input
        or _normalize_for_match(row["kosis_category"]) == norm_input
    ]
    if len(exact_name_hits) == 1:
        key = exact_name_hits[0]
        return key, [key], f"'{user_input}'는 명칭 기준으로 category_key '{key}'와 일치합니다."

    # 4) 부분 일치(포함 관계) — 후보가 정확히 하나로 좁혀질 때만 채택
    partial_hits = set(exact_alias_hits) | set(exact_name_hits)
    if len(norm_input) >= 2:
        for key, row in mapping.items():
            haystacks = [row["naver_name"], row["kosis_category"], key] + row["aliases"]
            for h in haystacks:
                nh = _normalize_for_match(h)
                if nh and (norm_input in nh or nh in norm_input):
                    partial_hits.add(key)
                    break
    if len(partial_hits) == 1:
        key = next(iter(partial_hits))
        return key, [key], f"'{user_input}'는 부분 일치로 category_key '{key}'로 해석했습니다."

    candidates = sorted(partial_hits) if partial_hits else sorted(mapping.keys())
    return None, candidates, (
        f"'{user_input}'를 category_key로 확정할 수 없습니다 "
        f"({'후보가 여러 개' if len(partial_hits) > 1 else '일치하는 항목 없음'})."
    )


def format_category_catalog(mapping):
    """--list-categories 및 해석 실패 메시지에 쓰는 사람이 읽을 수 있는 카탈로그."""
    lines = []
    for key in sorted(mapping.keys()):
        row = mapping[key]
        alias_text = f", 별칭: {', '.join(row['aliases'])}" if row["aliases"] else ""
        lines.append(f"  - {key} ({row['naver_name']} / {row['kosis_category']}){alias_text} [{row['status']}]")
    return "\n".join(lines)


def validate_category(category_key, mapping):
    if category_key not in mapping:
        return False, f"'{category_key}'는 category-mapping.md에 없습니다. 매핑 추가가 먼저 필요합니다."
    return True, mapping[category_key]


# ---------- 2. 데이터 로드 ----------
def add_months(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year = month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def date_window_for_months(period_months):
    end_month = add_months(date.today().replace(day=1), -1)
    start_month = add_months(end_month, -(period_months - 1))
    end_date = date(end_month.year, end_month.month, monthrange(end_month.year, end_month.month)[1])
    return (
        start_month.strftime("%Y%m"),
        end_month.strftime("%Y%m"),
        start_month.strftime("%Y-%m-01"),
        end_date.strftime("%Y-%m-%d"),
    )


def normalize_naver_months(df):
    out = df.copy()
    out["month"] = pd.to_datetime(out["period"]).dt.strftime("%Y-%m")
    out = out.rename(columns={"search_ratio": "search_index"})
    return out[["month", "search_index"]]


def normalize_kosis_months(df, kosis_category):
    out = df[df["category_kosis"] == kosis_category].copy()
    if out.empty:
        available = ", ".join(sorted(df["category_kosis"].dropna().unique().tolist()))
        raise ValueError(f"KOSIS 응답에 '{kosis_category}' 상품군이 없습니다. 응답 상품군: {available}")
    out["month"] = pd.to_datetime(out["month"], format="%Y%m").dt.strftime("%Y-%m")
    # KOSIS 원 단위는 백만원이다. 리포트 규칙에 맞춰 억원으로 변환한다.
    out["revenue"] = out["revenue_million_krw"] / 100.0
    out = out.dropna(subset=["revenue"])
    return out[["month", "revenue"]]


def classify_fetch_error(exc):
    """
    실 API 연동 예외를 리포트에 그대로 노출하기엔 너무 긴/기술적인 원문
    대신, 짧고 사람이 읽을 수 있는 한국어 사유로 분류한다. 원문 정보는
    잃지 않되(뒤에 짧게 덧붙임), ProxyError 스택트레이스 전체가 리포트
    상단 배지에 그대로 찍히는 문제를 막는다.
    """
    name = type(exc).__name__
    detail = redact_sensitive(exc)[:100]
    if "Proxy" in name:
        return "로컬/시스템 프록시가 응답하지 않아 API 접속이 막혔습니다"
    if "SSLError" in name:
        return "TLS/SSL 연결에 실패했습니다"
    if "ConnectTimeout" in name or "Timeout" in name:
        return "API 응답이 시간 내에 오지 않았습니다(타임아웃)"
    if "ConnectionError" in name:
        return "네트워크 연결에 실패했습니다(인터넷 연결 또는 방화벽 확인 필요)"
    if "HTTPError" in name:
        return f"API가 오류 응답을 반환했습니다 ({detail})"
    if isinstance(exc, ValueError):
        return f"API 응답을 해석할 수 없습니다 ({detail})"
    return f"{name} ({detail})"


def try_fetch_real_data(category_key, period_months):
    """
    실제 API(네이버 데이터랩, 통계청 온라인쇼핑동향) 연동 지점.
    단일 검증 코드만 자동 분석한다. 다중 코드나 검증 보류 항목은
    임의 합산하지 않고 데모 데이터 폴백 사유로 남긴다.
    """
    load_local_env_files()
    mapping = load_category_mapping()
    if category_key not in mapping:
        return None, f"'{category_key}'는 category-mapping.md에 없습니다. 매핑 추가가 먼저 필요합니다."

    row = mapping[category_key]
    if "초안" in row["status"] or "확인 필요" in row["naver_path"]:
        return None, f"카테고리 매핑이 아직 검증되지 않았습니다: {row['status']}"
    if "보류" in row["status"] or "매칭불가" in row["status"]:
        return None, f"카테고리 매핑이 실데이터 자동 분석 대상이 아닙니다: {row['status']}"
    if len(row["naver_codes"]) != 1:
        return None, (
            f"네이버 코드가 {len(row['naver_codes'])}개라 상대지수를 임의 합산하지 않습니다 "
            f"({', '.join(row['naver_codes'])})"
        )

    naver_key = os.environ.get("NAVER_CLIENT_ID")
    naver_secret = os.environ.get("NAVER_CLIENT_SECRET")
    kosis_key = os.environ.get("KOSIS_API_KEY")
    missing = []
    if not naver_key or not naver_secret:
        missing.append("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET")
    if not kosis_key:
        missing.append("KOSIS_API_KEY")
    if missing:
        return None, "API 키 미설정 (" + ", ".join(missing) + ")"

    start_prd_de, end_prd_de, start_date, end_date = date_window_for_months(period_months)
    try:
        kosis_raw = fetch_online_shopping_revenue(start_prd_de, end_prd_de, api_key=kosis_key)
        naver_raw = fetch_shopping_category_trend(
            row["naver_name"],
            row["naver_codes"][0],
            start_date,
            end_date,
            time_unit="month",
            client_id=naver_key,
            client_secret=naver_secret,
        )
        kosis_df = normalize_kosis_months(kosis_raw, row["kosis_category"])
        naver_df = normalize_naver_months(naver_raw)
        merged = pd.merge(naver_df, kosis_df, on="month", how="inner").sort_values("month")
        match_base = max(len(kosis_df), len(naver_df), 1)
        match_rate = len(merged) / match_base
        if merged.empty:
            return None, "실 API 응답은 받았지만 네이버·KOSIS 월 기준 매칭 결과가 없습니다."
        reason = (
            f"실 API 연동 성공: {match_base}개월 후보 중 {len(merged)}개월 매칭, "
            f"매칭률 {match_rate:.0%}; KOSIS 최근 1~2개월은 잠정치일 수 있음"
        )
        return merged[["month", "search_index", "revenue"]].reset_index(drop=True), reason
    except Exception as exc:
        return None, f"실 API 로드 실패: {classify_fetch_error(exc)}"


def load_data(category_key, period_months):
    df, reason = try_fetch_real_data(category_key, period_months)
    if df is not None:
        return df, False, reason
    # 데모로 폴백할 때도 실데이터 요청과 같은 "오늘 기준 최근 N개월" 창을
    # 쓴다. 예전에는 데모 데이터가 항상 2022-01부터 고정 시작이라, 같은
    # 카테고리를 실데이터/데모로 번갈아 돌리면 기간 표시가 서로 달라
    # ("왜 01이 아니라 07부터냐") 혼란을 줬다.
    n_months = max(period_months, 48)
    start_prd_de, _, _, _ = date_window_for_months(n_months)
    start_year_month = f"{start_prd_de[:4]}-{start_prd_de[4:]}"
    df = generate_demo_timeseries(
        category_key, n_months=n_months, n_labeled_events=6, start_year_month=start_year_month
    )
    return df, True, reason


# ---------- 3. 계절성 분해 ----------
def decompose(values):
    """
    추세는 전역 선형회귀로, 계절성분은 월별 '중앙값'(평균이 아닌)으로 산출한다.

    처음엔 이동평균(window=5)으로 추세를 구했으나, 국소 이동평균은 급증
    이벤트 자체를 그 달의 추세 계산에 포함시켜버려(로컬 오염) 정작 그
    이벤트의 잔차(residual)를 과소평가하는 문제가 있었다. 예: 어떤 달에
    검색량이 튀면 그 달 자체가 이동평균 창에 들어가 추세도 같이 튀어
    올라, "튀었다"는 신호가 흐려짐. 전역 선형추세로 바꾸면 한두 달의
    이상치가 전체 추세 기울기에 주는 영향이 작아 이 문제가 줄어든다.
    계절성분도 평균 대신 중앙값을 써서 같은 이유로 이상치에 덜 흔들리게 한다.
    """
    n = len(values)
    x = np.arange(n)
    y = np.array(values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    trend = (slope * x + intercept).tolist()

    detrended = [v - t for v, t in zip(values, trend)]
    monthly_buckets = [[] for _ in range(12)]
    for i, d in enumerate(detrended):
        monthly_buckets[i % 12].append(d)
    monthly_median = [float(np.median(b)) if b else 0.0 for b in monthly_buckets]
    seasonal = [monthly_median[i % 12] for i in range(n)]
    residual = [v - t - s for v, t, s in zip(values, trend, seasonal)]
    return trend, seasonal, residual


# ---------- 4. 이상탐지 ----------
def robust_z_scores(values):
    """
    median/MAD 기반 로버스트 Z-score.
    평균/표준편차 기반 Z-score는 이상치가 여러 개 섞이면 표준편차 자체가
    커져 서로의 이상치 정도를 희석시키는 문제(masking)가 있어,
    이상 이벤트가 한 시계열에 여러 번 있을 수 있는 이 Skill에는
    median/MAD 방식이 더 안정적이다.
    """
    arr = np.array(values, dtype=float)
    median = np.median(arr)
    mad = np.median(np.abs(arr - median)) or 1e-6
    return 0.6745 * (arr - median) / mad


def detect_anomalies(residual, z_threshold=DEFAULT_Z_THRESHOLD):
    z = robust_z_scores(residual)
    idxs = [i for i, val in enumerate(z) if abs(val) > z_threshold]
    return idxs, z.tolist()


# ---------- 5. 과거 이벤트 라벨링 ----------
def label_events(anomaly_idxs, search_index, revenue, seasonal_rev, trend_rev, n_months,
                  growth_threshold=DEFAULT_REVENUE_GROWTH_THRESHOLD):
    labeled, unlabeled_latest = [], []
    for idx in anomaly_idxs:
        if idx > n_months - 3:
            unlabeled_latest.append(idx)  # 후행 반응을 볼 여유가 없는 최신 이벤트
            continue
        signal = False
        for lag in (1, 2):
            j = idx + lag
            if j >= n_months:
                continue
            expected = trend_rev[j] + seasonal_rev[j]
            if expected <= 0:
                continue
            growth = (revenue[j] - expected) / expected
            if growth >= growth_threshold:
                signal = True
                break
        labeled.append((idx, "signal" if signal else "noise"))
    return labeled, unlabeled_latest


# ---------- 6. 분류 모델 ----------
def _sigmoid(value):
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def fit_logistic_predict_proba(train_x, train_y, pred_x):
    """
    scikit-learn 없이 동작하는 작은 L2 정규화 로지스틱 회귀.
    표본 수가 작고 피처가 4개뿐인 이 스킬의 판정 보조 모델에 맞춘 구현이다.
    """
    x = np.asarray(train_x, dtype=float)
    y = np.asarray(train_y, dtype=float)
    pred = np.asarray(pred_x, dtype=float)

    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0

    x_scaled = (x - mean) / scale
    pred_scaled = (pred - mean) / scale

    x_design = np.column_stack([np.ones(len(x_scaled)), x_scaled])
    pred_design = np.concatenate([[1.0], pred_scaled])

    weights = np.zeros(x_design.shape[1], dtype=float)
    regularization = 1.0
    reg_matrix = np.eye(x_design.shape[1])
    reg_matrix[0, 0] = 0.0

    for _ in range(80):
        logits = x_design @ weights
        probs = np.array([_sigmoid(float(v)) for v in logits])
        gradient = x_design.T @ (probs - y) + regularization * (reg_matrix @ weights)
        curvature = np.clip(probs * (1 - probs), 1e-6, None)
        hessian = (x_design.T * curvature) @ x_design + regularization * reg_matrix
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        weights -= step
        if float(np.linalg.norm(step)) < 1e-6:
            break

    return _sigmoid(float(pred_design @ weights))


def train_and_predict(labeled, unlabeled_latest, search_index, residual_z, n_months,
                       min_sample=DEFAULT_MIN_SAMPLE):
    if len(labeled) < min_sample:
        return None, f"라벨링된 과거 이벤트가 {len(labeled)}건으로 최소 표본({min_sample}건) 미만"

    labels = [1 if lb == "signal" else 0 for _, lb in labeled]
    if len(set(labels)) < 2:
        return None, "라벨이 한쪽 클래스(신호 또는 노이즈)로만 치우쳐 있어 학습 불가"

    def features(idx):
        m = idx % 12
        return [
            residual_z[idx],
            search_index[idx],
            math.sin(2 * math.pi * m / 12),
            math.cos(2 * math.pi * m / 12),
        ]

    X = np.array([features(idx) for idx, _ in labeled])
    y = np.array(labels)

    if not unlabeled_latest:
        return {"model_trained": True, "n_sample": len(labeled), "predictions": []}, None

    preds = []
    for idx in unlabeled_latest:
        prob = fit_logistic_predict_proba(X, y, features(idx))
        preds.append((idx, round(float(prob) * 100, 1)))
    return {"model_trained": True, "n_sample": len(labeled), "predictions": preds}, None


# ---------- 7. 리포트 렌더링 ----------
# design-wise.md(Wise 디자인 시스템) 팔레트에 맞춘 존 색상.
# 신호=positive(초록), 노이즈=mute(회색), 최신(미라벨)=warning(노랑, "판단 대기중"의 의미).
ZONE_COLOR_SIGNAL = "#2ead4b"
ZONE_COLOR_NOISE = "#868685"
ZONE_COLOR_LATEST = "#b86700"


def build_zones(labeled, unlabeled_latest, months):
    """
    차트 위에 그릴 이상탐지 구간. label은 SVG에 보이는 텍스트가 아니라
    <title> 툴팁으로만 쓴다 — 이전 버전은 구간마다 텍스트를 차트 위에 직접
    그렸는데, 실데이터처럼 이상 이벤트가 연달아 붙어 있으면(예: 2022-12,
    2023-01, 2023-02, 2023-03) 라벨 텍스트끼리 겹쳐서 읽을 수 없는 문제가
    있었다. 구간 강조(색 배경)는 차트에 남기고, 월/라벨 텍스트는 hover
    툴팁과 "판정 근거" 목록(이미 있음)에서만 보여줘 이 문제를 근본적으로
    없앤다(이벤트가 몇 개가 겹치든 항상 안전).
    """
    zones = []
    for idx, lb in labeled:
        color = ZONE_COLOR_SIGNAL if lb == "signal" else ZONE_COLOR_NOISE
        tag = "신호" if lb == "signal" else "노이즈"
        zones.append({"i": idx, "color": color, "label": f"{tag}·{months[idx]}"})
    for idx in unlabeled_latest:
        zones.append({"i": idx, "color": ZONE_COLOR_LATEST, "label": f"최신·{months[idx]}"})
    return zones


def render_report(category_key, mapping_row, df, trend, seasonal, residual,
                   z_threshold, labeled, unlabeled_latest, model_result, model_error,
                   is_demo, fetch_reason, min_sample):
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(
            f"HTML 리포트 템플릿이 없습니다: {TEMPLATE_PATH}. "
            "templates/report-template.html을 포함해 스킬을 다시 배포하세요."
        )
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    months = df["month"].tolist()
    n = len(months)

    # 판정 문구 결정 (verdict_class는 design-wise.md의 의미색 등급에 맞춘 CSS 클래스:
    # positive=초록/go, warning=노랑/ambiguous, negative=빨강/avoid, neutral=회색/판단 불가)
    history_summary = summarize_history(labeled, trend)

    focus_idx = unlabeled_latest[-1] if unlabeled_latest else None
    gauge_prob, gauge_n_sample = None, None  # 확률 게이지용 — 값이 없으면 게이지를 숨긴다
    if focus_idx is None:
        verdict_label, verdict_sub, verdict_class = (
            "최근 이상 신호 없음",
            f"이번 기간에는 임계치를 넘는 급증 이벤트가 감지되지 않았습니다. {history_summary}",
            "neutral",
        )
    elif model_result is None:
        verdict_label, verdict_sub, verdict_class = "판단 보류", model_error, "neutral"
    else:
        prob_map = dict(model_result["predictions"])
        prob = prob_map.get(focus_idx)
        if prob is None:
            verdict_label, verdict_sub, verdict_class = (
                "판단 보류", "최신 이벤트에 대한 예측값을 얻지 못했습니다.", "neutral"
            )
        else:
            gauge_prob, gauge_n_sample = prob, model_result["n_sample"]
            if prob >= PROB_SIGNAL_THRESHOLD:
                verdict_label, verdict_sub, verdict_class = (
                    f"관심 있게 지켜볼 만함 ({prob}%)",
                    f"과거 유사 이벤트 {model_result['n_sample']}건 기반 로지스틱회귀 예측",
                    "positive",
                )
            elif prob <= PROB_NOISE_THRESHOLD:
                verdict_label, verdict_sub, verdict_class = (
                    f"반짝 관심(노이즈)일 가능성 높음 ({prob}%)",
                    f"검색만 늘고 거래액 반응은 없었던 과거 사례와 비슷함 · "
                    f"과거 유사 이벤트 {model_result['n_sample']}건 기반 로지스틱회귀 예측",
                    "negative",
                )
            else:
                verdict_label, verdict_sub, verdict_class = (
                    f"모호함, 추가 관찰 필요 ({prob}%)",
                    f"과거 유사 이벤트 {model_result['n_sample']}건 기반 로지스틱회귀 예측",
                    "warning",
                )

    zones = build_zones(labeled, unlabeled_latest, months)

    evidence_items = []
    for idx, lb in labeled:
        tag = "신호" if lb == "signal" else "노이즈"
        badge_class = "signal" if lb == "signal" else "noise"
        evidence_items.append(
            f'<li><span class="tag">{months[idx]}</span>검색지수 급증(Z-score {residual_z_at(residual, idx):.2f}) '
            f'이후 후행 거래액 반응 {"확인됨" if lb == "signal" else "확인 안 됨"} → '
            f'<span class="badge {badge_class}">{tag}</span>로 라벨링</li>'
        )
    if unlabeled_latest:
        idx = unlabeled_latest[-1]
        evidence_items.append(
            f'<li><span class="tag">{months[idx]}</span>최신 이상 이벤트, 아직 후행 데이터 없음 → '
            f'<span class="badge latest">모델 예측 대상</span></li>'
        )
    if model_result:
        evidence_items.append(
            f'<li><span class="tag">모델</span>과거 이벤트 {model_result["n_sample"]}건으로 학습한 로지스틱회귀 사용</li>'
        )
    evidence_html = "\n".join(evidence_items) if evidence_items else "<li>표시할 이벤트가 없습니다.</li>"

    sample_limit_text = (
        f"라벨링된 과거 이벤트 표본이 {len(labeled)}건입니다. "
        f"{min_sample}건 미만이면 확률 대신 판단 보류로 처리합니다."
    )

    naver_path = mapping_row["naver_path"]
    kosis_cat = mapping_row["kosis_category"]
    # naver_path에서 끝의 " / <코드>" 부분만 떼어내 사람이 읽을 만한 이름으로 쓴다
    # (naver_name은 첫 "/" 앞부분만 잘라 쓰기 때문에 "생활/건강 > 자동차용품" 같은
    # 카테고리에서 "생활"만 남는 문제가 있어 제목 표시에는 쓰지 않는다).
    naver_display = re.sub(r"\s*/\s*\d{6,}(?:\s*,\s*\d{6,})*\s*$", "", naver_path).strip() or mapping_row["naver_name"]
    display_name = f"{naver_display} ({kosis_cat}) · {category_key}"

    # 데이터 출처는 "무슨 API의 무슨 통계"인지 정확한 이름으로 표기한다.
    # (주의) 이 파이프라인은 네이버 데이터랩의 "쇼핑인사이트"만 쓴다 —
    # naver_client.py에 "검색어트렌드" 엔드포인트 상수가 있지만 실제로
    # 호출하는 코드는 없으므로, 실제로 쓰지 않는 API를 출처에 적지 않는다.
    naver_codes_text = ", ".join(mapping_row["naver_codes"]) if mapping_row["naver_codes"] else "미지정"
    naver_source = f"네이버 데이터랩 쇼핑인사이트 · {naver_display} (코드 {naver_codes_text})"
    kosis_source = f"통계청 온라인쇼핑동향조사 · 온라인쇼핑몰 취급상품범위별/상품군별거래액(DT_1KE10041) · {kosis_cat}"

    residual_z = robust_z_scores(residual).tolist()

    replacements = {
        "__CATEGORY_NAME__": display_name,
        "__PERIOD__": f"{months[0]} - {months[-1]}",
        "__NAVER_SOURCE__": naver_source,
        "__KOSIS_SOURCE__": kosis_source,
        "__SAMPLE_INFO__": f"{n}개월 · 이상이벤트 {len(labeled) + len(unlabeled_latest)}건",
        "__DATA_FLAG_CLASS__": "demo" if is_demo else "real",
        "__DATA_FLAG_TEXT__": (
            f"⚠ 데모(가상) 데이터 사용 — 실제 API 연동에 실패해 계산 검증용 가상 데이터로 대체함. 사유: {fetch_reason}"
            if is_demo else "✓ 실제 API 연동 데이터 — 네이버·통계청에서 직접 받아온 값"
        ),
        "__VERDICT_LABEL__": verdict_label,
        "__VERDICT_SUB__": verdict_sub,
        "__VERDICT_CLASS__": verdict_class,
        "__EVIDENCE_ITEMS__": evidence_html,
        "__SAMPLE_LIMIT_TEXT__": sample_limit_text,
        "__RUN_TIMESTAMP__": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "__FOOTER_NOTE__": "데모 데이터 기반 실행" if is_demo else "실 데이터 기반 실행",
        "__Z_THRESHOLD_TEXT__": f"±{z_threshold:g}",
        "__HISTORY_SUMMARY__": history_summary,
        "__MONTHS_JSON__": json.dumps(months, ensure_ascii=False),
        "__SEARCH_JSON__": json.dumps(df["search_index"].tolist()),
        "__REVENUE_JSON__": json.dumps(df["revenue"].tolist()),
        "__TREND_JSON__": json.dumps([round(v, 1) for v in trend]),
        "__SEASONAL_JSON__": json.dumps([round(v, 1) for v in seasonal]),
        "__RESIDUAL_JSON__": json.dumps([round(v, 1) for v in residual]),
        "__RESIDUAL_Z_JSON__": json.dumps([round(v, 2) for v in residual_z]),
        "__Z_THRESHOLD_JSON__": json.dumps(z_threshold),
        "__ZONES_JSON__": json.dumps(zones, ensure_ascii=False),
        "__MODEL_PROB_JSON__": json.dumps(gauge_prob),
        "__MODEL_N_SAMPLE_JSON__": json.dumps(gauge_n_sample),
        "__PROB_SIGNAL_THRESHOLD_JSON__": json.dumps(PROB_SIGNAL_THRESHOLD),
        "__PROB_NOISE_THRESHOLD_JSON__": json.dumps(PROB_NOISE_THRESHOLD),
    }
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


def residual_z_at(residual, idx):
    z = robust_z_scores(residual)
    return z[idx]


def summarize_history(labeled, trend_si):
    """
    "지금 당장 급증이 없으면 이 리포트가 아무 정보도 안 준다"는 문제를
    막기 위한 요약. 현재 이상 이벤트가 없어도, (1) 이 카테고리가 과거에
    급증했을 때 실제로 매출로 얼마나 이어졌는지(기저율), (2) 지금 추세
    자체는 어느 방향인지는 항상 계산할 수 있고 유용한 정보다.
    """
    total = len(labeled)
    if total == 0:
        history_text = "과거 구간에서 라벨링된 급증 이벤트가 아직 없어 이 카테고리의 과거 전환율은 계산할 수 없습니다."
    else:
        signal_count = sum(1 for _, lb in labeled if lb == "signal")
        rate = round(signal_count / total * 100)
        history_text = f"과거 {total}건의 검색 급증 이벤트 중 {signal_count}건({rate}%)이 실제 거래액 증가로 이어졌습니다."

    delta = trend_si[-1] - trend_si[0]
    if abs(delta) < 3:
        trend_text = "같은 기간 검색지수의 전반적 추세는 뚜렷한 변화 없이 보합입니다."
    elif delta > 0:
        trend_text = f"같은 기간 검색지수의 전반적 추세는 완만히 상승했습니다(+{delta:.1f}p)."
    else:
        trend_text = f"같은 기간 검색지수의 전반적 추세는 완만히 하락했습니다({delta:.1f}p)."
    return f"{history_text} {trend_text}"


# ---------- 8. 실행 ----------
def run(category_input, period_months=DEFAULT_PERIOD_MONTHS, z_threshold=DEFAULT_Z_THRESHOLD,
        min_sample=DEFAULT_MIN_SAMPLE):
    log = []
    missing_assets = missing_required_assets()
    if missing_assets:
        log.append("[0] 필수 파일 검증")
        for label, path in missing_assets:
            log.append(f"    -> 누락: {label} ({path})")
        log.append("    -> 중단: 스킬 패키지가 불완전합니다. templates/와 reference/ 폴더를 포함해 다시 배포하세요.")
        log.append("    -> 샘플 HTML을 보고 임시 템플릿을 만들지 마세요. 리포트는 공식 템플릿으로만 생성합니다.")
        return None, log, None

    log.append(f"[0] 카테고리 해석: 입력='{category_input}'")
    mapping = load_category_mapping()
    resolved_key, candidates, note = resolve_category_key(category_input, mapping)
    if resolved_key is None:
        log.append(f"    -> 중단: {note}")
        log.append("    -> 사용 가능한 category_key 목록:")
        log.append(format_category_catalog(mapping))
        if candidates and candidates != sorted(mapping.keys()):
            log.append(f"    -> 입력과 관련 있어 보이는 후보: {', '.join(candidates)}")
        log.append("    -> 다음 중 하나로 --category를 정확히 지정해 다시 시도하세요.")
        return None, log, None
    log.append(f"    -> {note}")
    category_key = resolved_key

    log.append(f"[1] 카테고리 검증: {category_key}")
    ok, info = validate_category(category_key, mapping)
    if not ok:
        log.append(f"    -> 중단: {info}")
        return None, log, category_key
    log.append(f"    -> 매핑 확인됨: {info['naver_path']} <-> {info['kosis_category']}")

    log.append("[2] 데이터 로드")
    df, is_demo, fetch_reason = load_data(category_key, period_months)
    log.append(f"    -> {'데모 데이터 사용: ' + str(fetch_reason) if is_demo else '실제 API 데이터 사용: ' + str(fetch_reason)} (n={len(df)}개월)")

    log.append("[3] 계절성 분해 (trend/seasonal/residual)")
    trend_si, seasonal_si, residual_si = decompose(df["search_index"].tolist())
    trend_rev, seasonal_rev, _ = decompose(df["revenue"].tolist())

    log.append(f"[4] 이상탐지 (Z-threshold={z_threshold})")
    anomaly_idxs, z_scores = detect_anomalies(residual_si, z_threshold)
    log.append(f"    -> 이상 이벤트 {len(anomaly_idxs)}건 감지: {[df['month'][i] for i in anomaly_idxs]}")

    log.append("[5] 과거 이벤트 라벨링")
    labeled, unlabeled_latest = label_events(
        anomaly_idxs, df["search_index"].tolist(), df["revenue"].tolist(),
        seasonal_rev, trend_rev, len(df)
    )
    log.append(f"    -> 라벨링 {len(labeled)}건, 최신(미라벨) {len(unlabeled_latest)}건")

    log.append(f"[6] 분류 모델 (min_sample={min_sample})")
    model_result, model_error = train_and_predict(
        labeled, unlabeled_latest, df["search_index"].tolist(), residual_si, len(df), min_sample
    )
    if model_result:
        log.append(f"    -> 학습 완료 (n={model_result['n_sample']}), 예측 {model_result['predictions']}")
    else:
        log.append(f"    -> 판단 보류: {model_error}")

    log.append("[7] 리포트 렌더링")
    html = render_report(category_key, info, df, trend_si, seasonal_si, residual_si,
                          z_threshold, labeled, unlabeled_latest, model_result, model_error,
                          is_demo, fetch_reason, min_sample)

    log.append("[8] 검증 체크리스트")
    checks = {
        "데모 데이터 여부 명시": "data-flag" in html,
        "표본 수 명시": "표본" in html,
        "한계 문단 포함": "한계 및 유의사항" in html,
        "확정형 표현 없음(간이체크)": ("확실히" not in html and "무조건" not in html),
    }
    for k, v in checks.items():
        log.append(f"    - {k}: {'통과' if v else '실패'}")

    return html, log, category_key


def _ensure_utf8_stdout():
    """
    Windows 콘솔(cp949 등)에서 한글 로그가 깨져 보이는 문제를 막는다.
    파일 출력(UTF-8)과 무관한 콘솔 표시 문제일 뿐이지만, 사람이 직접
    실행해서 확인할 때 "깨졌으니 실패한 것 같다"는 오판을 막기 위해 고정한다.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


if __name__ == "__main__":
    _ensure_utf8_stdout()

    parser = argparse.ArgumentParser(
        description="이커머스 카테고리 트렌드 신호 판별 파이프라인. "
                     "--category에는 내부 category_key(예: fashion_apparel)뿐 아니라 "
                     "자연어 카테고리명(예: 패션의류)도 줄 수 있으며, 자동으로 해석을 시도한다."
    )
    parser.add_argument("--category", required=False, default=None,
                         help="분석할 카테고리. category_key, 네이버/통계청 명칭, 또는 등록된 별칭.")
    parser.add_argument("--period-months", type=int, default=RUNTIME_DEFAULTS["period_months"])
    parser.add_argument("--z-threshold", type=float, default=RUNTIME_DEFAULTS["z_threshold"])
    parser.add_argument("--min-sample", type=int, default=RUNTIME_DEFAULTS["min_sample"])
    parser.add_argument("--out", default=None)
    parser.add_argument("--list-categories", action="store_true",
                         help="분석 가능한 category_key/별칭 목록을 출력하고 종료한다.")
    args = parser.parse_args()

    if args.list_categories:
        print(format_category_catalog(load_category_mapping()))
        sys.exit(0)

    if not args.category:
        parser.error("--category가 필요합니다 (또는 --list-categories로 후보를 먼저 확인하세요).")

    html, log, resolved_category_key = run(args.category, args.period_months, args.z_threshold, args.min_sample)
    print("\n".join(log))

    if html is None:
        sys.exit(1)

    out_name = f"report_{resolved_category_key or args.category}.html"
    out_path = args.out or os.path.join(BASE_DIR, "data", out_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n리포트 저장: {out_path}")
