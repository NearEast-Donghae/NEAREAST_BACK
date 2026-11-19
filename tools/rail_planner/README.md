# 철도 여행 플래너

이 폴더에서 Python 3.11 이상으로 실행합니다.

```sh
python -m pip install -r backend/requirements.txt
python -m scripts.build_dataset
```

원본 자료는 data/raw/README.md, 출처는 docs/data_sources.md를 참고하세요. 도보 경로는 scripts.check_walking_route, 방문 순서는 backend/app/services/route_optimizer.py의 optimizeWalkingRoute를 사용합니다.
