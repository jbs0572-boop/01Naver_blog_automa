# 네이버 Creator Advisor 기반 트렌드 키워드 선정 모델 연구

## 연구 기준

- 기준 시각: 2026-09-01 KST
- 목적: 네이버 Creator Advisor에 표시되는 트렌드 키워드 후보 중, 향후 블로그 유입 가능성이 높은 키워드를 선택하는 함수·통계 모델 설계
- 핵심 제약: 임의의 키워드를 생성하는 것이 아니라 Creator Advisor가 제공한 키워드 목록을 1차 후보로 사용한다.
- 주의: Creator Advisor의 추세·노출 데이터는 기간 내 최대값을 100으로 정규화한 상대값이다. 절대 검색량으로 해석하지 않는다.

## 직접 답변

가장 현실적인 구조는 `후보 수집 → 상대 추세 정규화 → 급상승·지속성·예측·블로그 성과를 분리 계산 → 시간순 검증으로 가중치 학습 → 상위 키워드 선택`이다. 처음부터 대형 딥러닝 모델을 쓰기보다, 설명 가능한 통계 점수와 시계열 예측을 기준선으로 만들고 충분한 과거 실행·유입 데이터가 쌓이면 랭킹 모델과 확률적 예측 모델을 추가하는 방식이 적합하다.

## 네이버 리소스에서 확보할 변수

Creator Advisor 공식 안내에 따르면 검색 유입 상위 검색어의 기간별 변화, 내 채널 유입 트렌드와 전체 검색 횟수 트렌드의 비교, 검색 노출·유입·평균 노출 순위를 확인할 수 있다. 검색 노출·유입 트렌드는 선택 기간의 최댓값을 100으로 둔 상대값이며, 검색 노출 분석은 네이버 검색·뷰·동영상·인플루언서 검색을 포함하고 다른 검색 서비스는 포함하지 않는다.

따라서 키워드 `k`, 시점 `t`마다 다음을 저장한다.

```text
trend_index[k,t]       Creator Advisor의 전체 검색 추세 상대값
channel_inflow[k,t]    해당 검색어에서 내 채널로 유입된 수 또는 상대 추세
exposure[k,t]          검색 노출 수 또는 상대 추세
avg_rank[k,t]          평균 노출 순위
capture_time[t]        조회 시각, 집계 기간, 기준 시간대
candidate_rank[k,t]    Creator Advisor 화면의 후보 순위
```

일간·주간·월간 집계의 반영 지연도 함께 기록한다. 공식 안내상 일간 데이터는 다음날 0시 이후, 주간은 다음 주 0시 이후, 월간은 다음 달 0시 이후 반영될 수 있으므로 최신 관측치를 미래 정보처럼 사용하면 안 된다.

## 권장 1단계 함수

Creator Advisor 후보를 그대로 선택하되, 화면 순위 하나에 의존하지 않도록 다음 점수를 계산한다.

```text
Score(k,t) =
    w1 * Surge(k,t)
  + w2 * Momentum(k,t)
  + w3 * Persistence(k,t)
  + w4 * Forecast(k,t+h)
  + w5 * ChannelCapture(k,t)
  - w6 * VolatilityPenalty(k,t)
```

권장 초기값은 `w1=.25, w2=.20, w3=.20, w4=.20, w5=.15`이며, 실제 과거 유입을 목표변수로 삼아 시간순 교차검증으로 다시 학습한다. 이는 최종 고정값이 아니라 초기 기준선이다.

각 항목은 다음처럼 정의할 수 있다.

```text
Surge      = robust_z(log1p(trend_index[t]) - rolling_median_28d)
Momentum   = slope(log1p(trend_index[t-6:t]))
Persistence= count(trend_index[t-j] > baseline[t-j] for j=0..6) / 7
Forecast   = median predicted trend at t+h, or upper quantile if exploration is desired
ChannelCapture = log1p(channel_inflow) / max(log1p(exposure), 1)
VolatilityPenalty = MAD(residuals) / max(abs(level), 1)
```

`ChannelCapture`는 내 블로그가 이미 해당 키워드에서 검색 유입을 얻는지를 반영하는 선택적 항목이다. 블로그 자체의 과거 데이터가 없으면 이 항목은 제외하고 Creator Advisor 후보의 추세만으로 점수를 계산한다. 화면의 상대값은 서로 다른 조회 기간·시점에서 직접 이어 붙이지 말고, 같은 기간·같은 기준일의 관측끼리 비교한다.

## 논문에서 가져올 방법

### 1. 검색 데이터의 현재 상태 추정과 변수 선택

Choi와 Varian은 Google Trends를 기존 시계열에 외생 변수로 넣어 현재 상태를 추정하는 방법을 제시했다. 네이버 적용 시에는 `trend_index`를 단독 예측값으로 믿기보다, 블로그 유입의 과거 시계열과 함께 쓰는 기준선으로 삼는다. [Predicting the Present with Google Trends](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1475-4932.2012.00809.x)

Ginsberg 등은 검색어를 자동 선택하고 선형회귀와 교차검증으로 실제 관측값을 추정했다. 이 연구는 검색어 선택과 별도 검증 구간을 둬야 한다는 설계 근거가 된다. 동시에 특정 사건으로 검색어가 오염될 수 있다고 경고하므로, 키워드 점수에 이상치·오탐 검사를 넣어야 한다. [Detecting influenza epidemics using search engine query data](https://www.nature.com/articles/nature07634)

### 2. 급상승·버스트 탐지

Kleinberg의 버스트 모델은 단어 빈도가 낮은 상태와 높은 상태를 갖는 확률적 오토마톤으로 보고, 상태 전이를 버스트 시작·종료로 탐지한다. Creator Advisor의 급상승 키워드에 `Surge`와 버스트 지속 구간을 계산하는 근거로 적합하다. [Bursty and Hierarchical Structure in Streams](https://doi.org/10.1145/775047.775061)

Kong 등은 버스트가 발생할지, 얼마나 일찍 예측할지, 발생하면 얼마나 커질지를 분리했다. 키워드 선정에서도 `지금 뜨는가`, `얼마나 지속될까`, `게시 시점에 아직 유효할까`를 하나의 점수로 뭉개지 말고 별도 출력해야 한다. [Predicting Bursts and Popularity of Hashtags in Real-Time](https://doi.org/10.1145/2600428.2609476)

Peetz, Meij, de Rijke는 결과 문서의 시간 분포에서 버스트를 검출해 시간적 질의 모델에 반영했다. 트렌드 키워드의 최신성뿐 아니라 그 키워드와 관련된 콘텐츠가 실제로 어느 시기에 집중되는지 확인하는 보조 방법으로 쓸 수 있다. [Using temporal bursts for query modeling](https://doi.org/10.1007/S10791-013-9227-2)

### 3. 검색량 총량과 상대지수의 한계

Lillo와 Ruggieri는 관측된 검색어 표본으로 전체 검색 질의 수를 추정할 때 Zipf 분포와 표본 편향을 고려했다. Creator Advisor의 상대지수를 절대 검색량으로 환산할 수 있다는 뜻이 아니라, 상대값만으로 시장 전체 수요를 주장하면 안 된다는 통계적 경계선이다. [Estimating the Total Volume of Queries to a Search Engine](https://arxiv.org/abs/2101.09807)

Medeiros와 Pires는 Google Trends 표본이 동일한 검색어·기간·지역을 다시 조회해도 달라질 수 있고, 이를 무시하면 우연한 결과를 얻을 수 있다고 보였다. 네이버 데이터에도 재조회 변동 가능성이 있는지 확인하기 위해 동일 시점 반복 수집, 원시 응답 보존, 앵커 키워드 비교를 운영 규칙으로 둔다. [The Proper Use of Google Trends in Forecasting Models](https://arxiv.org/abs/2104.03065)

### 4. 유입·클릭을 목표로 할 때의 편향 보정

클릭·유입은 관심뿐 아니라 노출 위치, 검색 화면 구성, 기존 랭킹의 영향을 받는다. Joachims, Swaminathan, Schnabel은 암묵적 피드백을 그대로 학습하면 위치 편향이 생기며 propensity weighting으로 보정할 수 있음을 보였다. [Unbiased Learning-to-Rank with Biased Feedback](https://www.ijcai.org/proceedings/2018/738)

Wang 등은 개인화 검색의 희소 클릭과 질의별 선택 편향을 다뤘다. 블로그별 유입 데이터가 적을 때는 키워드별 독립 모델보다 유사 키워드군을 묶어 계층·베이지안 방식으로 정보를 공유하는 편이 안정적이다. [Learning to Rank with Selection Bias in Personal Search](https://research.google/pubs/learning-to-rank-with-selection-bias-in-personal-search/)

Luo 등은 위치 편향 보정에서 relevance와 propensity가 서로 얽히는 교란 문제를 지적했다. 따라서 `유입 수가 높음 = 키워드 수요가 높음`으로 단정하지 않고, 노출량과 평균 노출 순위를 함께 보정해야 한다. [Unbiased Learning-to-Rank Needs Unconfounded Propensity Estimation](https://doi.org/10.1145/3626772.3657772)

### 5. 예측 모델 후보

- 기준선: 계절 나이브, 이동중앙값, ETS/ARIMA. 데이터가 적은 초기 단계에서 반드시 포함한다.
- 해석 가능한 확률 모델: 추세·요일·월·휴일·외부 이벤트를 넣는 동적 회귀 또는 상태공간 모델. 예측 중앙값뿐 아니라 10·50·90% 구간을 산출한다.
- 다수 키워드 공동 학습: 관련 키워드 시계열이 충분하면 DeepAR를 검토한다. [DeepAR: Probabilistic Forecasting with Autoregressive Recurrent Networks](https://arxiv.org/abs/1704.04110)
- 정적 키워드 속성, 알려진 미래 변수, 관측 시계열을 함께 쓰고 설명 가능성을 유지하려면 Temporal Fusion Transformer를 검토한다. 다만 데이터가 충분하지 않으면 기준선보다 우월하다고 가정하지 않는다. [Temporal Fusion Transformers for Interpretable Multi-horizon Time Series Forecasting](https://doi.org/10.1016/j.ijforecast.2021.03.012)

## 실제 도입 순서

1. 매일 또는 가능한 주기로 Creator Advisor 후보·기간·조회 시각·원시 화면 또는 응답을 저장한다.
2. 같은 기준일·같은 기간의 후보만 비교하고, 상대지수의 기간 간 연결에는 앵커 키워드와 재조회 검사를 둔다.
3. `Surge`, `Momentum`, `Persistence`를 먼저 계산해 후보를 줄인다.
4. 과거에 선택한 키워드의 노출·유입·평균 순위·게시 시점 데이터를 연결한다.
5. 목표변수를 `게시 후 7일 검색 유입`, `게시 후 28일 누적 유입`, 또는 두 값을 함께 반영한 사업 목표로 명시한다.
6. 시간 순서를 지키는 rolling-origin 검증으로 가중치를 선택한다. 랜덤 분할은 미래 정보를 섞으므로 사용하지 않는다.
7. 상위 키워드만 선택하되, 최상위 하나만 반복하지 않도록 같은 사건·동의어·중복 키워드 클러스터별 상한을 둔다.
8. 매 실행 결과에 후보 점수, 사용한 기간, 예측 구간, 선택·탈락 사유를 기록한다.

## 검증 지표

- 선정 품질: Precision@K, Recall@K, NDCG@K
- 실제 유입: 게시 후 7일·28일 유입의 로그 평균, 중앙값, 상위 분위수
- 예측 품질: MAE/RMSE와 함께 pinball loss, prediction interval coverage
- 운영 품질: 트렌드 감지 리드타임, 오래된 키워드 선택률, 중복률, 데이터 결측률
- 비교군: Creator Advisor 화면 순위 그대로 선택, 이동평균 순위, 제안 점수 모델

## 결론과 한계

가장 먼저 만들 모델은 `Creator Advisor 후보를 그대로 입력받아 상대 추세·버스트·지속성·향후 추세를 점수화하는 설명 가능한 통계 랭커`다. 이후 실제 블로그 유입 로그가 충분해지면 위치·노출 편향을 보정한 학습-to-랭크 모델을 붙이고, 키워드별 시계열이 충분할 때만 DeepAR나 TFT를 추가한다.

이 모델은 Creator Advisor의 상대지수만으로 절대 검색량이나 네이버 검색 순위를 보장할 수 없다. 또한 블로그 글의 품질, 발행 시점, 제목·본문 적합성, 검색 결과 경쟁도와 같은 후속 요인이 유입에 영향을 준다. 따라서 목표는 “키워드가 뜰 확률”과 “그 키워드로 우리 글이 유입을 얻을 확률”을 분리 추정하는 것이다.

## 출처 목록

네이버 공식 자료: [Creator Advisor 소개](https://help.naver.com/service/23038/contents/19001?lang=ko&osType=MOBILE), [유입 검색어 트렌드 안내](https://help.naver.com/service/23038/contents/14623?lang=ko&osType=MOBILE), [검색 노출 분석 안내](https://help.naver.com/service/23038/contents/14624?lang=ko&osType=MOBILE), [데이터 반영 주기 안내](https://help.naver.com/service/23038/contents/11389?lang=ko&osType=MOBILE)
