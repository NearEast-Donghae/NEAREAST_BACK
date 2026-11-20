"""The moods a picture can carry. A fixed list: the same words are used when the
pictures are curated, when a taste is summed up and when the frontend filters."""

MOODS: dict[str, str] = {  # id -> the word shown to the user
    "sea_train": "바다와 열차",
    "train": "열차·선로",
    "station": "역",
    "sunrise": "일출·노을",
    "sea": "바다",
    "retro": "레트로·골목",
    "night": "야경",
    "snow": "눈·겨울",
    "mountain": "산·계곡",
    "portrait": "인물이 잘 나오는 곳",
}
# Pictures with a train or a station in them: the ones the timetable has something to
# say about.
RAIL_MOODS = frozenset({"sea_train", "train", "station"})
LABEL_TO_ID = {label: mood for mood, label in MOODS.items()}
