# 자연어 카테고리 해석 예시

내부 `category_key`(예: `fashion_apparel`)를 몰라도, 사용자가 실제로 쓸 법한
한국어 표현을 그대로 `--category`에 넘기면 파이프라인이 자동으로 해석한다.

## 성공 케이스

## 요청 예시

```
옷 카테고리 요즘 검색 늘어난 게 진짜 뜨는 건지 봐줘.
```

## 실행 명령

```
python scripts/run_pipeline.py --category 옷 --period-months 48
```

## 기대 로그

```
[0] 카테고리 해석: 입력='옷'
    -> '옷'는 별칭 목록에서 category_key 'fashion_apparel'와 일치합니다.
[1] 카테고리 검증: fashion_apparel
    ...
리포트 저장: .../data/report_fashion_apparel.html
```

`옷`이라는 표현은 `reference/category-mapping.md`의 `fashion_apparel` 행
별칭 칸에 등록되어 있어 정확히 한 곳으로 좁혀진다. 종료 코드는 0이고
HTML 리포트가 생성된다.

## 실패(해석 불가) 케이스 — 이때는 임의로 진행하지 않는다

## 요청 예시

```
이커머스 카테고리 트렌드 봐줘.
```

## 실행 명령

```
python scripts/run_pipeline.py --category 이커머스 --period-months 48
```

## 기대 로그 (HTML은 생성되지 않고 종료 코드 1)

```
[0] 카테고리 해석: 입력='이커머스'
    -> 중단: '이커머스'를 category_key로 확정할 수 없습니다 (일치하는 항목 없음).
    -> 사용 가능한 category_key 목록:
      - bags (...)
      - beauty (...)
      ...
    -> 다음 중 하나로 --category를 정확히 지정해 다시 시도하세요.
```

이 스킬을 호출하는 에이전트는 이 상황에서 리포트가 없다고 그냥 실패
처리하지 말고, 로그에 출력된 category_key 후보 목록을 사용자에게 보여주고
"어떤 카테고리를 말씀하시는 건가요?"라고 되물어야 한다. `category-mapping.md`
에 없는 카테고리를 이름이 비슷하다는 이유로 임의로 매칭하지 않는다.

## 새 별칭 추가

자주 쓰이는데 아직 해석이 안 되는 표현이 발견되면, 코드를 고치는 대신
`reference/category-mapping.md`의 해당 category_key 행 `자연어 별칭` 칸에
쉼표로 추가하면 된다.
