# 이커머스 트렌드 신호 판별 스킬

검색 관심도 급증이 실제 거래액 반응과 함께 관찰되는지 판단하는 단일
이커머스 카테고리 분석 스킬입니다.

## 폴더 구조

```
skills/data-analysis/
├── SKILL.md
├── README.md
├── design-wise.md        # 리포트 디자인 시스템 스펙 (Wise 브랜드 기반)
├── reference/
├── scripts/
├── templates/
├── examples/
├── sample-data/
└── config/
```

## 왜 매번 같은 결과가 나오나

리포트의 판정·수치·문구는 `scripts/run_pipeline.py`와
`templates/report-template.html`이라는 **결정론적 코드**가 만든다.
LLM이 매번 새로 글을 쓰는 방식이 아니라서, 어떤 에이전트/플랫폼에서
실행하든 같은 날·같은 파라미터·같은 API 키 유무 조건이면 같은 결과가
나온다. 그래서 이 스킬을 호출하는 에이전트는 스크립트를 정확히 실행하고
그 결과 HTML 파일을 그대로 전달해야 한다 — 내용을 요약하거나 다른
말로 바꿔 쓰면 이 일관성이 깨진다.

## 실행 방법

```
pip install -r requirements.txt
python scripts/run_pipeline.py --category fashion_apparel --period-months 48
python scripts/run_pipeline.py --category 패션의류 --period-months 48   # 자연어도 가능
python scripts/run_pipeline.py --list-categories                        # 사용 가능한 카테고리 확인
```

리포트는 `data/report_{category_key}.html`에 저장됩니다. `--category`에
넘긴 문자열이 `reference/category-mapping.md`의 category_key/별칭/명칭
어디에도 해석되지 않으면 HTML을 만들지 않고 종료 코드 1과 함께 후보
목록을 stdout에 출력합니다 (임의 추정으로 진행하지 않습니다).

## 실제 연동 환경변수

아래 값은 커밋되는 소스 코드 밖에서 설정합니다.

```
KOSIS_BASE_URL=https://kosis.kr/openapi/Param/statisticsParameterData.do
KOSIS_API_KEY=
NAVER_API_PROVIDER=api_hub
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
```

API 키가 없거나 요청이 실패하면 파이프라인은 데모 데이터로 대체하고,
그 사유를 리포트에 기록합니다.

## 안전 규칙

- 매핑된 카테고리 1개만 분석합니다.
- 카테고리 매핑을 임의 추정하지 않습니다.
- 별도 결합 규칙이 설계되지 않은 상태에서 네이버 상대지수 카테고리 코드를
  여러 개 합치지 않습니다 — 이 경우(예: `shoes`, `bags`) 실 API를 아예
  시도하지 않고 데모 데이터로 대체합니다. 이건 API 실패가 아니라 의도된
  동작입니다(`examples/shoes-multi-code-demo-fallback.md` 참고).
- 라벨링된 이벤트가 `min_sample`보다 적으면 신호확률을 출력하지 않습니다.
- 함께 움직인 현상을 인과관계처럼 표현하지 않습니다.
- "신호"/"노이즈"/"데모 데이터" 같은 내부 용어는 리포트에 쉬운 말로
  풀어 설명합니다(리포트 상단 글로서리).
