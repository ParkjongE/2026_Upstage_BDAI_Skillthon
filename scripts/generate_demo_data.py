"""
재현 가능한 데모 데이터 생성기.
실제 API(네이버 데이터랩 / 통계청 온라인쇼핑동향) 연동 전까지
run_pipeline.py가 폴백으로 사용한다.

카테고리명을 시드로 써서, 카테고리마다 다르지만 같은 카테고리는
항상 같은 데이터가 나오도록(재현 가능성) 설계했다.
"""
import hashlib
import math
from datetime import date

import pandas as pd


def _seed_from_category(category: str) -> int:
    h = hashlib.sha256(category.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _add_months(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _default_start_year_month(n_months):
    """실 API 창(run_pipeline.date_window_for_months)과 동일한 규칙: '오늘' 기준
    마지막으로 끝난 달까지 n_months개월을 거슬러 올라간다. 예전에는 이 값과
    무관하게 항상 2022-01부터 시작해서, 실데이터(예: 2022-07 시작)와 데모
    데이터(2022-01 시작)의 기간 표시가 달라 보여 혼란을 줬다."""
    last_full_month = _add_months(date.today().replace(day=1), -1)
    start = _add_months(last_full_month, -(n_months - 1))
    return start.year, start.month


def generate_demo_timeseries(category: str, n_months: int = 24,
                              n_labeled_events: int = 6,
                              start_year_month: str = None) -> pd.DataFrame:
    """
    n_labeled_events: 학습용으로 라벨링 가능한(=충분한 후행 개월이 남은)
    과거 이상 이벤트를 몇 개 주입할지. 신호/노이즈를 절반씩 섞는다.
    마지막에 라벨 없는 '최신 이벤트' 1개를 추가로 주입해 분류 예측 대상으로 삼는다.

    start_year_month: "YYYY-MM" 형식으로 시작월을 고정하고 싶을 때 지정한다
    (run_pipeline.py가 실데이터 창과 정확히 같은 기간을 보여주려고 넘긴다).
    생략하면 오늘 날짜 기준으로 계산한다.
    """
    seed = _seed_from_category(category)
    rng_state = seed

    def rand():
        # 외부 라이브러리 없이 재현 가능한 간단 의사난수 (LCG)
        nonlocal rng_state
        rng_state = (1103515245 * rng_state + 12345) % (2 ** 31)
        return rng_state / (2 ** 31)

    # 카테고리별 계절 진폭을 시드에서 유도 (겨울/여름 성수기 등 다양화)
    # 진폭·추세를 크게 잡으면 베이스라인이 100에 가까워져 이후 스파이크가
    # 상한(100)에 잘려 이상탐지 신호가 사라지므로 헤드룸을 넉넉히 남긴다.
    phase = (seed % 12)
    amplitude = 8 + (seed % 12)

    months = []
    search_index = []
    revenue = []

    if start_year_month:
        base_year, base_month = (int(p) for p in start_year_month.split("-"))
    else:
        base_year, base_month = _default_start_year_month(n_months)
    for i in range(n_months):
        m = (base_month - 1 + i) % 12
        y = base_year + (base_month - 1 + i) // 12
        months.append(f"{y}-{m+1:02d}")  # 실데이터(YYYY-MM)와 같은 포맷으로 통일

        seasonal_val = 45 + amplitude * math.sin((m - phase) / 12 * 2 * math.pi)
        trend_val = i * (0.05 + (seed % 5) * 0.02)  # 완만한 추세로 제한 (헤드룸 확보)
        noise = (rand() - 0.5) * 6
        si = max(5, min(100, round(seasonal_val + trend_val + noise)))
        search_index.append(si)

        rev_seasonal = 150 + amplitude * 3 * math.sin((m - phase) / 12 * 2 * math.pi)
        rev_trend = i * (1.2 + (seed % 5) * 0.2)
        rev_noise = (rand() - 0.5) * 15
        rv = max(20, round(rev_seasonal + rev_trend + rev_noise))
        revenue.append(rv)

    # 라벨링 가능한 과거 이벤트를 시계열 앞 구간에 배치한다.
    # 주의: 이벤트 간격을 12의 배수로 두면, 서로 다른 이벤트의 '후행 1~2개월'
    # 반응이 같은 달(month%12)에 겹쳐서 그 달의 계절 중앙값 자체가 이벤트로
    # 오염되고, 결과적으로 "기대값 대비 급증"이 상쇄되어 사라져 버린다.
    # 12와 서로소인 간격(7)을 쓰면 각 이벤트가 서로 다른 달에 떨어져 이 문제를
    # 피할 수 있다.
    usable_end = n_months - 3
    step = 7  # gcd(7,12)=1
    start = 3 + (seed % 4)
    while start + step * (n_labeled_events - 1) > usable_end and step > 1:
        step -= 1
    event_indices = [start + step * k for k in range(n_labeled_events)]
    event_indices = [i for i in event_indices if i <= usable_end]

    labels = []
    for k, idx in enumerate(event_indices):
        is_signal = (k % 2 == 0)  # 절반은 신호, 절반은 노이즈로 교대 배치
        headroom = 100 - search_index[idx]
        spike = max(18, min(38, headroom - 3))  # 100 상한에 잘리지 않게 헤드룸 기반 산정
        search_index[idx] = search_index[idx] + spike
        if is_signal:
            for lag in (1, 2):
                if idx + lag < n_months:
                    revenue[idx + lag] = round(revenue[idx + lag] * (1.22 + rand() * 0.1))
        labels.append("signal" if is_signal else "noise")

    # 최신 이벤트(라벨 없음, 분류 예측 대상) — 시계열 맨 끝 부근에 배치
    latest_idx = n_months - 2
    headroom_latest = 100 - search_index[latest_idx]
    search_index[latest_idx] += max(18, min(38, headroom_latest - 3))

    df = pd.DataFrame({
        "month": months,
        "search_index": search_index,
        "revenue": revenue,
    })
    df.attrs["labeled_events"] = list(zip(event_indices, labels))
    df.attrs["latest_event_idx"] = latest_idx
    return df


if __name__ == "__main__":
    df = generate_demo_timeseries("fashion_outer", n_months=60, n_labeled_events=6)
    print(df)
    print("labeled events (idx,label):", df.attrs["labeled_events"])
    print("latest (unlabeled) event idx:", df.attrs["latest_event_idx"])
