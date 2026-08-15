# 데이터 및 오픈소스 출처

제출 전 확인용 출처 정리입니다. 실제 산출 리포트(`data/report_*.html`)에도
검색지수/거래액 출처와 실제 API/데모 데이터 여부가 상단에 표시됩니다.

## 데이터/API 출처

- 검색지수: 네이버 데이터랩 쇼핑인사이트 카테고리 클릭추이 API
  - 사용 엔드포인트: `shopping/v1/categories`
  - 리포트 표기 예: `네이버 데이터랩 쇼핑인사이트 · 패션의류 (코드 50000000)`
  - 주의: 네이버 검색어트렌드 API는 실제 분석에 사용하지 않았으므로 출처로 표기하지 않습니다.
- 거래액: 통계청/KOSIS 온라인쇼핑동향조사
  - 사용 통계표: `온라인쇼핑몰 취급상품범위별/상품군별거래액`
  - 통계표 ID: `DT_1KE10041`
  - 리포트 표기 예: `통계청 온라인쇼핑동향조사 · 온라인쇼핑몰 취급상품범위별/상품군별거래액(DT_1KE10041) · 의복`
- 데모 데이터: `scripts/generate_demo_data.py`에서 재현 가능하게 생성한 계산 검증용 가상 데이터
  - 외부 데이터셋을 복사한 것이 아니며, 실제 값이 아닙니다.
  - 리포트가 데모 데이터 기반이면 상단의 `데모(가상) 데이터 사용` 배지에 사유가 표시됩니다.

## 오픈소스 사용 출처

- Python 패키지: `requirements.txt`
  - `pandas` - BSD 3-Clause License
  - `numpy` - BSD 3-Clause License
  - `requests` - Apache License 2.0
- 웹 리포트 폰트: Google Fonts
  - `Inter` - SIL Open Font License 1.1
  - `IBM Plex Mono` - SIL Open Font License 1.1

## 제출용 요약 문구

본 프로젝트는 네이버 데이터랩 쇼핑인사이트 카테고리 클릭추이 API와
통계청/KOSIS 온라인쇼핑동향조사 통계표 `DT_1KE10041`을 활용했습니다.
API 키 미설정 또는 안전한 자동 매핑이 어려운 카테고리에서는 외부 실제
데이터가 아닌 자체 생성 데모 데이터를 사용하며, 해당 여부와 사유를
리포트 상단에 명시했습니다. 구현에는 `pandas`, `numpy`, `requests` 및
Google Fonts의 `Inter`, `IBM Plex Mono`를 사용했습니다.
