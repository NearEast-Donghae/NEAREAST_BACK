from typing import Literal, get_args

TrainType = Literal["KTX-이음", "ITX-마음", "ITX-새마을", "새마을호", "누리로", "무궁화호"]
TRAIN_TYPES = get_args(TrainType)
