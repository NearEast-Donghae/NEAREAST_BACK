"""Choosing pictures on the onboarding: what a choice says, and where it leads.

Each rule has a case where it must bite and one where it must not. Pure rules run on
hand-made places and timetables; the API runs with real auth (cookie + CSRF) on the
seeded in-memory database.
"""

import contextlib
import csv
import datetime as dt
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from storage_support import PROJECT_ROOT, createStorageApp

from alembic import command
from neareast_api.core.config import Settings
from neareast_api.db.entities import KnowledgeChunkEntity, OnboardingPhotoEntity
from neareast_api.db.metadata import metadata
from neareast_api.modules.chat.retrieval import Content, Doc
from neareast_api.modules.onboarding import curation, taste
from neareast_api.modules.onboarding.light import sun_times
from neareast_api.modules.onboarding.moods import MOODS
from neareast_api.modules.places.service import Place
from neareast_api.modules.trains import service as trains

API = "/api/v1"
DONGHAE, MUKHO, MANGSANG = "station_donghae", "station_mukho", "station_mangsang"
DAY = dt.date(2026, 11, 21)  # sunrise 07:09, sunset 17:10 in Donghae

PLACES = {p.id: p for p in (
    Place(MUKHO, "묵호역", "station", True, lat=37.5490, lng=129.1090),
    Place(DONGHAE, "동해역", "station", True, lat=37.4990, lng=129.1240),
    Place(MANGSANG, "망상역", "station", False, lat=37.5873, lng=129.0974),
    Place("poi_lighthouse", "묵호등대", "poi", lat=37.5545, lng=129.1180, category="attraction"),
    Place("poi_alley", "논골담길", "poi", lat=37.5535, lng=129.1170, category="attraction"),
    Place("poi_cave", "천곡동굴", "poi", lat=37.5200, lng=129.1100, category="attraction"),
    Place("poi_far", "무릉계곡", "poi", lat=37.4600, lng=129.0100, category="attraction"),
    Place("poi_diner", "항구식당", "poi", lat=37.5500, lng=129.1100, category="restaurant"),
    Place("poi_nowhere", "좌표 없는 곳", "poi", category="attraction"),
)}


def photo(photo_id, *moods, place=None, view=None, position=0, vector=None, model="m"):
    return taste.Photo(photo_id, f"사진 {photo_id}", f"https://img.example/{photo_id}.jpg",
                       "김작가", "202310", "한국관광공사 포토코리아", "공공누리 제1유형",
                       place, view, tuple(moods), position,
                       model if vector is not None else None,
                       tuple(vector) if vector is not None else None)


def stop(name, time, place_id, kind="여객승하차", **extra):
    return {"name": name, "code": f"c-{name}", "arr": time, "dep": time, "stop_type": kind,
            "place_id": place_id, **extra}


def timetable(*rows, stations=None):
    """rows: (train number, station id, "HH:MM", passing?)."""
    meta = {"basis": {"summary": "최근 토요일 3주 운행 기록 기준", "record_dates": [],
                      "plan_date": "2026-12-19", "limits": []},
            "target_date": DAY.isoformat(), "limits": []}
    made = []
    for no, station, time, passing in rows:
        made.append({
            "no": no, "line": "영동선", "direction": "D", "origin": "청량리",
            "destination": "동해", "weekly_saturday": True, "in_plan": True, "spread_min": 0,
            "stops": [stop("x", None, station, "통과(어림)", **{"pass": time}) if passing
                      else stop("x", time, station)]})
    stations = stations or {MUKHO: {"name": "묵호", "records": 3},
                            DONGHAE: {"name": "동해", "records": 3},
                            MANGSANG: {"name": "망상", "records": 0}}
    return trains.Timetable(meta, stations, tuple(made), {"1": "KTX-이음"})


class SunTimesTests(unittest.TestCase):
    """Against published times (KASI): the formula must stay within two minutes."""

    def close(self, got, want):
        minutes = lambda t: int(t[:2]) * 60 + int(t[3:])  # noqa: E731
        self.assertLessEqual(abs(minutes(got) - minutes(want)), 2, f"{got} vs {want}")

    def test_seoul_on_new_years_day(self):
        sun = sun_times(dt.date(2026, 1, 1), 37.5665, 126.9780)
        self.close(sun.sunrise, "07:47")
        self.close(sun.sunset, "17:24")

    def test_seoul_on_the_summer_solstice(self):
        sun = sun_times(dt.date(2026, 6, 21), 37.5665, 126.9780)
        self.close(sun.sunrise, "05:11")
        self.close(sun.sunset, "19:57")

    def test_jeongdongjin_new_year_sunrise(self):
        self.close(sun_times(dt.date(2026, 1, 1), 37.69, 129.03).sunrise, "07:39")

    def test_the_east_coast_sees_the_sun_before_seoul(self):
        seoul = sun_times(DAY, 37.5665, 126.9780).sunrise
        self.assertLess(sun_times(DAY).sunrise, seoul)

    def test_a_leap_day_is_a_day_like_any_other(self):
        self.close(sun_times(dt.date(2028, 2, 29), 37.5665, 126.9780).sunrise, "07:05")


class BoardTests(unittest.TestCase):
    def test_moods_take_turns_and_the_curated_order_is_kept_within_one(self):
        photos = [photo("a", "sea", position=1), photo("b", "sea", position=2),
                  photo("c", "station", position=3), photo("d", "sea", position=4)]
        self.assertEqual([p.id for p in taste.board_order(photos)], ["c", "a", "b", "d"])

    def test_a_picture_without_a_mood_still_shows_last_in_its_round(self):
        photos = [photo("a"), photo("b", "sea")]
        self.assertEqual([p.id for p in taste.board_order(photos)], ["b", "a"])

    def test_credit_names_the_source_and_the_photographer(self):
        board = taste.board([photo("a", "sea", place="poi_alley")], PLACES)
        shown = board.photos[0]
        self.assertEqual(shown.credit.text, "ⓒ한국관광공사 포토코리아-김작가")
        self.assertEqual((shown.placeId, shown.placeName), ("poi_alley", "논골담길"))
        self.assertEqual([m.id for m in board.moods], list(MOODS))

    def test_a_place_the_service_does_not_know_is_not_named(self):
        shown = taste.board([photo("a", "sea", place="poi_gone")], PLACES).photos[0]
        self.assertEqual((shown.placeId, shown.placeName), (None, None))


class TasteReadingTests(unittest.TestCase):
    def test_moods_are_counted_over_the_chosen_pictures(self):
        moods = taste.mood_shares([photo("a", "sea", "sunrise"), photo("b", "sea"),
                                   photo("c", "station")])
        self.assertEqual([(m.id, m.count, m.share) for m in moods],
                         [("sea", 2, 0.67), ("station", 1, 0.33), ("sunrise", 1, 0.33)])

    def test_summary_names_the_moods_chosen_a_lot_and_counts_rail_pictures(self):
        picked = [photo("a", "sea", "sunrise"), photo("b", "sea"), photo("c", "station")]
        text = taste.summary(taste.mood_shares(picked), picked)
        self.assertEqual(text, "‘바다’, ‘역’ 사진을 많이 고르셨어요. 열차나 역이 담긴 사진은 1장이에요.")

    def test_summary_without_a_rail_picture_says_the_way_there_is_by_train(self):
        picked = [photo("a", "sea"), photo("b", "sea"), photo("c", "mountain")]
        text = taste.summary(taste.mood_shares(picked), picked)
        self.assertIn("열차가 담긴 사진은 없지만", text)
        self.assertNotIn("장이에요", text)

    def test_a_scattered_choice_is_not_called_a_lot_of_one_mood(self):
        picked = [photo(str(i), mood) for i, mood in enumerate(
            ("sea", "station", "retro", "night", "snow"))]
        text = taste.summary(taste.mood_shares(picked), picked)
        self.assertTrue(text.startswith("여러 분위기의 사진을 고루 고르셨어요."), text)
        self.assertNotIn("많이", text)


class PlaceRankingTests(unittest.TestCase):
    CATALOG = [
        photo("alley1", "retro", "portrait", place="poi_alley"),
        photo("alley2", "retro", place="poi_alley"),
        photo("light1", "sea", "sunrise", place="poi_lighthouse"),
        photo("cave1", "mountain", place="poi_cave"),
        photo("mukho1", "station", "retro", place=MUKHO),
        photo("loose", "sea"),  # taken somewhere the service has no place for
    ]

    def rank(self, *ids, catalog=None, content=None, floor=taste.MIN_SIMILARITY):
        catalog = catalog or self.CATALOG
        picked = [p for p in catalog if p.id in ids]
        return taste.rank_places(picked, catalog, PLACES, content, floor)

    def test_the_place_a_chosen_picture_was_taken_at_comes_first(self):
        rows = self.rank("alley1", "light1", "alley2")
        self.assertEqual([(r.place.id, r.match, r.photo_ids) for r in rows[:2]], [
            ("poi_alley", "photo", ("alley1", "alley2")), ("poi_lighthouse", "photo", ("light1",))])

    def test_a_place_with_the_same_mood_is_offered_without_a_chosen_picture(self):
        rows = {r.place.id: r for r in self.rank("alley1", "alley2", "loose")}
        self.assertEqual((rows[MUKHO].match, rows[MUKHO].moods), ("mood", ("retro",)))
        self.assertEqual(rows["poi_lighthouse"].moods, ("sea",))  # through the loose sea picture

    def test_a_place_sharing_no_mood_is_not_offered(self):
        self.assertNotIn("poi_cave", [r.place.id for r in self.rank("alley1", "alley2", "loose")])

    def test_more_of_the_taste_answered_ranks_higher(self):
        # two of three chosen pictures are retro: the alley (all retro) beats the lighthouse
        rows = [r.place.id for r in self.rank("mukho1", "loose", "alley2") if r.match == "mood"]
        self.assertEqual(rows, ["poi_lighthouse"])
        catalog = [*self.CATALOG, photo("r1", "retro"), photo("r2", "retro"), photo("s1", "sea")]
        rows = self.rank("r1", "r2", "s1", catalog=catalog)
        self.assertEqual([r.place.id for r in rows][:2], ["poi_alley", MUKHO])

    def test_cover_is_a_chosen_picture_of_the_place_when_there_is_one(self):
        rows = {r.place.id: r for r in self.rank("alley2", "light1", "loose")}
        self.assertEqual(rows["poi_alley"].cover, "alley2")
        self.assertEqual(rows[MUKHO].cover, "mukho1")

    def test_at_most_eight_places(self):
        places = {**PLACES, **{f"poi_{i}": Place(f"poi_{i}", f"곳{i}", "poi",
                                               category="attraction") for i in range(12)}}
        catalog = [photo(f"p{i}", "sea", place=f"poi_{i}") for i in range(12)]
        rows = taste.rank_places(catalog[:3], catalog, places)
        self.assertEqual(len(rows), taste.MAX_PLACES)
        self.assertEqual([r.match for r in rows[:3]], ["photo"] * 3)


class SimilarityTests(unittest.TestCase):
    """Likeness of meaning from stored vectors: used only when it can be compared."""

    DOCS = {"d_cave": Doc("d_cave", "천곡동굴", "poi", ("poi_cave",), (), "동굴"),
            "d_far": Doc("d_far", "무릉계곡", "poi", ("poi_far",), (), "계곡"),
            "d_diner": Doc("d_diner", "항구식당", "poi", ("poi_diner",), (), "식당"),
            "d_two": Doc("d_two", "두 곳", "poi", ("poi_alley", "poi_lighthouse"), (), "둘")}
    VECTORS = {"d_cave": (1.0, 0.0), "d_far": (0.0, 1.0), "d_diner": (1.0, 0.0),
               "d_two": (1.0, 0.0)}

    def content(self, model="m"):
        return Content(PLACES, self.DOCS, model, self.VECTORS)

    def rank(self, picked, content, catalog=None):
        return {r.place.id: r for r in
                taste.rank_places(picked, catalog or picked, PLACES, content)}

    def picked(self, vector=(1.0, 0.0), model="m"):
        return [photo(str(i), "sea", vector=vector, model=model) for i in range(3)]

    def test_a_place_described_like_the_chosen_pictures_is_offered(self):
        rows = self.rank(self.picked(), self.content())
        self.assertEqual(rows["poi_cave"].match, "similar")

    def test_a_place_described_differently_is_not(self):
        self.assertNotIn("poi_far", self.rank(self.picked(), self.content()))

    def test_a_restaurant_is_not_offered_on_likeness(self):
        self.assertNotIn("poi_diner", self.rank(self.picked(), self.content()))

    def test_a_document_about_two_places_speaks_for_neither(self):
        rows = self.rank(self.picked(), self.content())
        self.assertNotIn("poi_alley", rows)
        self.assertNotIn("poi_lighthouse", rows)

    def test_vectors_of_another_model_are_not_compared(self):
        self.assertEqual(self.rank(self.picked(), self.content(model="other")), {})

    def test_one_chosen_picture_without_a_vector_turns_the_step_off(self):
        picked = [*self.picked()[:2], photo("bare", "sea")]
        self.assertEqual(self.rank(picked, self.content()), {})

    def test_without_knowledge_the_step_is_skipped_not_failed(self):
        self.assertEqual(self.rank(self.picked(), None), {})

    def test_other_pictures_of_a_place_speak_for_it(self):
        picked = self.picked()
        catalog = [*picked, photo("far1", "mountain", place="poi_far", vector=(1.0, 0.05))]
        rows = self.rank(picked, None, catalog)
        self.assertEqual(rows["poi_far"].match, "similar")

    def test_at_most_three_places_on_likeness_alone(self):
        places = {**PLACES, **{f"poi_{i}": Place(f"poi_{i}", f"곳{i}", "poi",
                                               category="attraction") for i in range(6)}}
        docs = {f"d{i}": Doc(f"d{i}", "곳", "poi", (f"poi_{i}",), (), "곳") for i in range(6)}
        content = Content(places, docs, "m", {d: (1.0, 0.0) for d in docs})
        rows = taste.rank_places(self.picked(), self.picked(), places, content)
        self.assertEqual([r.match for r in rows], ["similar"] * taste.MAX_SIMILAR)


class RailwayTests(unittest.TestCase):
    TABLE = timetable(("1", MUKHO, "07:30", False), ("2", MUKHO, "12:00", False),
                      ("3", MUKHO, "17:45", False), ("4", MANGSANG, "06:28", True),
                      ("5", MANGSANG, "17:51", True))

    def describe(self, *ids, catalog, table=TABLE):
        return taste.describe(ids, catalog, PLACES, table, DAY)

    def place(self, answer, place_id):
        return next(p for p in answer.places if p.placeId == place_id)

    def test_a_place_names_the_nearest_station_one_can_get_off_at(self):
        stations = taste.boarding_stations(PLACES, self.TABLE)
        self.assertEqual({s.id for s in stations}, {MUKHO, DONGHAE})  # 망상 has no stops
        got = taste.access(PLACES["poi_lighthouse"], stations)
        self.assertEqual((got.stationId, got.stationName, got.withinWalk), (MUKHO, "묵호역", True))
        self.assertGreater(got.walkMinutes, 0)

    def test_a_long_walk_is_given_but_not_called_walkable(self):
        stations = taste.boarding_stations(PLACES, self.TABLE)
        got = taste.access(PLACES["poi_cave"], stations)  # between the two stations
        self.assertGreater(got.walkMinutes, taste.ACCESS_WALK_MINUTES)
        self.assertFalse(got.withinWalk)

    def test_no_station_is_named_for_a_station_or_beyond_a_walk_or_without_coordinates(self):
        stations = taste.boarding_stations(PLACES, self.TABLE)
        self.assertIsNone(taste.access(PLACES[MUKHO], stations))
        self.assertIsNone(taste.access(PLACES["poi_far"], stations))
        self.assertIsNone(taste.access(PLACES["poi_nowhere"], stations))

    def test_a_station_lists_the_trains_around_sunrise_and_sunset(self):
        catalog = [photo("m", "station", place=MUKHO), photo("x", "sea"), photo("y", "sea")]
        watch = self.place(self.describe("m", "x", "y", catalog=catalog), MUKHO).watch
        self.assertEqual((watch.stationId, watch.boarding, watch.trainsPerDay), (MUKHO, True, 3))
        self.assertEqual([(g.trainNo, g.near, g.minutesFromSun, g.trainType) for g in watch.golden],
                         [("1", "sunrise", 21, "KTX-이음"), ("3", "sunset", 35, None)])
        self.assertEqual(watch.basis, "묵호역의 시각이에요.")

    def test_only_the_four_trains_nearest_to_the_sun_are_listed_in_time_order(self):
        table = timetable(*[(str(i), MUKHO, f"07:{m:02d}", False)
                            for i, m in enumerate((0, 5, 8, 10, 20, 40))])
        catalog = [photo("m", "station", place=MUKHO), photo("x", "sea"), photo("y", "sea")]
        watch = self.place(self.describe("m", "x", "y", catalog=catalog, table=table), MUKHO).watch
        self.assertEqual([g.time for g in watch.golden], ["07:00", "07:05", "07:08", "07:10"])

    def test_a_train_more_than_forty_minutes_from_the_sun_is_not_golden(self):
        catalog = [photo("m", "station", place=MANGSANG), photo("x", "sea"), photo("y", "sea")]
        watch = self.place(self.describe("m", "x", "y", catalog=catalog), MANGSANG).watch
        self.assertEqual([g.trainNo for g in watch.golden], [])  # 41 minutes before, 41 after
        self.assertEqual((watch.boarding, watch.trainsPerDay), (False, 2))
        self.assertIsNotNone(watch.note)  # watching only, said in the timetable's own words

    def test_a_tourist_spot_has_no_trains_unless_its_picture_names_the_station(self):
        plain = [photo("a", "sea", place="poi_lighthouse"), photo("x", "sea"), photo("y", "sea")]
        self.assertIsNone(self.place(self.describe("a", "x", "y", catalog=plain),
                                     "poi_lighthouse").watch)
        named = [photo("a", "sea_train", place="poi_lighthouse", view=MUKHO),
                 photo("x", "sea"), photo("y", "sea")]
        watch = self.place(self.describe("a", "x", "y", catalog=named), "poi_lighthouse").watch
        self.assertEqual((watch.stationId, len(watch.golden)), (MUKHO, 2))
        self.assertEqual(watch.basis, "묵호역 기준 시각이에요. 이 지점을 지나는 시각은 몇 분 달라요.")

    def test_without_a_timetable_the_taste_still_answers(self):
        catalog = [photo("m", "station", place=MUKHO), photo("x", "sea"), photo("y", "sea")]
        answer = taste.describe(["m", "x", "y"], catalog, PLACES, None, DAY)
        self.assertEqual((answer.certainty, answer.basis), (None, None))
        self.assertIsNone(answer.places[0].watch)
        self.assertEqual((answer.light.sunrise, answer.light.sunset), ("07:09", "17:10"))

    def test_pictures_gone_from_the_catalog_are_left_out_and_none_left_is_no_taste(self):
        catalog = [photo("a", "sea")]
        self.assertEqual(taste.describe(["gone", "a"], catalog, PLACES, None, DAY).photoIds, ["a"])
        self.assertIsNone(taste.describe(["gone"], catalog, PLACES, None, DAY))


class PhotoApiTests(unittest.TestCase):
    def setUp(self):
        self.app = createStorageApp(Settings(chat_mock=True, llm_usage_log="/dev/null"))
        self.addCleanup(self.app.state.databaseEngine.dispose)
        with self.app.state.sessionFactory() as session:
            for i, (photo_id, moods, place) in enumerate((
                    ("photo_1", ["station", "retro"], MUKHO), ("photo_2", ["sea"], None),
                    ("photo_3", ["sea", "sunrise"], None), ("photo_4", ["retro"], DONGHAE))):
                session.add(OnboardingPhotoEntity(
                    id=photo_id, title=f"사진 {i}", imageUrl=f"https://img.example/{i}.jpg",
                    photographer="김작가", shotMonth="202310", location="강원특별자치도 동해시",
                    source="한국관광공사 포토코리아", license="공공누리 제1유형", placeId=place,
                    moods=moods, keywords=[], position=i))
            session.commit()
        self.client = self.login("owner@example.com")

    def login(self, email):
        client = TestClient(self.app)
        self.addCleanup(client.close)
        response = client.post(API + "/auth/signup", json={
            "email": email, "nickname": "여행자", "password": "password-for-tests"})
        self.assertEqual(response.status_code, 201, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        return client

    def put(self, *ids, client=None, **params):
        return (client or self.client).put(API + "/onboarding/taste", params=params,
                                           json={"photoIds": list(ids)})

    def test_the_board_is_public_and_carries_credits(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        body = anonymous.get(API + "/onboarding/photos")
        self.assertEqual(body.status_code, 200, body.text)
        body = body.json()
        self.assertEqual([p["id"] for p in body["photos"]],
                         ["photo_1", "photo_2", "photo_4", "photo_3"])
        self.assertEqual(body["photos"][0]["credit"]["license"], "공공누리 제1유형")
        self.assertEqual((body["minPicks"], body["maxPicks"]), (3, 12))

    def test_choosing_needs_a_login_and_the_csrf_token(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(self.put("photo_1", "photo_2", "photo_3", client=anonymous).status_code,
                         401)
        self.assertEqual(anonymous.get(API + "/onboarding/taste").status_code, 401)
        self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.put("photo_1", "photo_2", "photo_3").status_code, 403)

    def test_a_choice_is_kept_and_read_back(self):
        answer = self.put("photo_1", "photo_2", "photo_3")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["photoIds"], ["photo_1", "photo_2", "photo_3"])
        self.assertEqual(body["moods"][0], {"id": "sea", "label": "바다", "count": 2, "share": 0.67})
        self.assertEqual(body["places"][0]["placeId"], MUKHO)
        self.assertEqual(body["places"][0]["match"], "photo")
        self.assertEqual(body["places"][1]["placeId"], DONGHAE)  # retro, like the 묵호역 picture
        self.assertEqual(body["certainty"], "estimated")
        self.assertEqual(body["light"]["date"], "2026-11-21")  # the timetable's day by default
        watch = body["places"][0]["watch"]
        self.assertEqual(watch["stationId"], MUKHO)
        self.assertGreater(watch["trainsPerDay"], 0)
        for train in watch["golden"]:
            self.assertLessEqual(abs(train["minutesFromSun"]), 40)
        again = self.client.get(API + "/onboarding/taste").json()
        self.assertEqual({**again, "updatedAt": None}, {**body, "updatedAt": None})

    def test_another_date_moves_the_light(self):
        self.put("photo_1", "photo_2", "photo_3")
        summer = self.client.get(API + "/onboarding/taste", params={"date": "2026-06-21"}).json()
        self.assertEqual(summer["light"]["date"], "2026-06-21")
        self.assertLess(summer["light"]["sunrise"], "05:30")
        self.assertEqual(self.client.get(API + "/onboarding/taste",
                                         params={"date": "someday"}).status_code, 422)

    def test_choosing_again_replaces_the_choice(self):
        self.put("photo_1", "photo_2", "photo_3")
        self.put("photo_2", "photo_3", "photo_4")
        self.assertEqual(self.client.get(API + "/onboarding/taste").json()["photoIds"],
                         ["photo_2", "photo_3", "photo_4"])

    def test_each_user_has_their_own_choice(self):
        self.put("photo_1", "photo_2", "photo_3")
        other = self.login("other@example.com")
        self.assertEqual(other.get(API + "/onboarding/taste").status_code, 404)

    def test_too_few_too_many_unknown_or_repeated_pictures_are_refused(self):
        self.assertEqual(self.put("photo_1", "photo_2").status_code, 422)
        self.assertEqual(self.put(*["photo_1"] * 13).status_code, 422)
        unknown = self.put("photo_1", "photo_2", "photo_9")
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unknown.json()["detail"]["unknown"], ["photo_9"])
        self.assertEqual(self.put("photo_1", "photo_1", "photo_2").status_code, 422)
        self.assertEqual(self.client.put(API + "/onboarding/taste", json={
            "photoIds": ["photo_1", "photo_2", "photo_3"], "extra": 1}).status_code, 422)
        self.assertEqual(self.client.get(API + "/onboarding/taste").status_code, 404)  # none kept

    def test_forgetting_the_choice(self):
        self.put("photo_1", "photo_2", "photo_3")
        self.assertEqual(self.client.delete(API + "/onboarding/taste").status_code, 204)
        self.assertEqual(self.client.get(API + "/onboarding/taste").status_code, 404)
        self.assertEqual(self.client.delete(API + "/onboarding/taste").status_code, 204)

    def test_a_picture_removed_from_the_board_drops_out_of_a_kept_choice(self):
        self.put("photo_1", "photo_2", "photo_3")
        with self.app.state.sessionFactory() as session:
            session.delete(session.get(OnboardingPhotoEntity, "photo_1"))
            session.commit()
        self.assertEqual(self.client.get(API + "/onboarding/taste").json()["photoIds"],
                         ["photo_2", "photo_3"])

    def test_an_empty_board_answers_with_no_pictures(self):
        with self.app.state.sessionFactory() as session:
            for row in session.query(OnboardingPhotoEntity):
                session.delete(row)
            session.commit()
        self.assertEqual(self.client.get(API + "/onboarding/photos").json()["photos"], [])

    def test_deleting_the_account_removes_the_choice(self):
        self.put("photo_1", "photo_2", "photo_3")
        response = self.client.delete(API + "/users/me")
        self.assertEqual(response.status_code, 204, response.text)
        with self.app.state.sessionFactory() as session:
            self.assertEqual(session.execute(
                metadata.tables["user_tastes"].select()).all(), [])


def sheet_row(number="101", **cells):
    row = {"사진번호": number, "제목": "추암 촛대바위 일출", "촬영자": "김작가", "촬영연월": "202310",
           "촬영지역": "강원특별자치도 동해시", "라이선스": "공공누리 제1유형(출처표시) — API 설명 기준",
           "원본 링크": "https://img.example/101.jpg", "키워드": "촛대바위, 일출, 동해시",
           "분위기 제안": "일출·노을, 바다", "장소id 제안": "", "선택": "o", "분위기": "",
           "장소id": "", "열차역id": "", "설명": ""}
    return {**row, **cells}


class CurationTests(unittest.TestCase):
    """The review sheet: only what a person marked is loaded, and only when it is sound."""

    def parse(self, *rows):
        return curation.parse(rows, PLACES)

    def test_suggestions_read_the_words_of_the_title_and_keywords(self):
        self.assertEqual(curation.suggest_moods("추암 촛대바위 일출"), ["sunrise", "sea"])
        self.assertEqual(curation.suggest_moods("천곡황금박쥐동굴 내부"), [])
        self.assertEqual(curation.suggest_place("논골담길 벽화", PLACES), "poi_alley")
        self.assertEqual(curation.suggest_place("논골 담길에서 본 묵호", PLACES), "poi_alley")
        self.assertIsNone(curation.suggest_place("이름 없는 해변", PLACES))

    def test_only_pictures_taken_in_donghae_reach_the_sheet(self):
        rows = curation.review_rows([
            {"galContentId": 1, "galTitle": "묵호등대", "galPhotographyLocation": "강원특별자치도 동해시",
             "galWebImageUrl": "https://img.example/1.jpg", "galSearchKeyword": "등대, 바다"},
            {"galContentId": 2, "galTitle": "동해 일출", "galPhotographyLocation": "강원특별자치도 강릉시"},
        ], PLACES)
        self.assertEqual([r["사진번호"] for r in rows], ["1"])
        self.assertEqual((rows[0]["분위기 제안"], rows[0]["장소id 제안"], rows[0]["선택"]),
                         ("바다", "poi_lighthouse", ""))
        self.assertEqual(tuple(rows[0]), curation.COLUMNS)

    def test_fetching_again_keeps_what_the_reviewer_filled_in(self):
        fresh = [sheet_row("1", 선택="", 제목="새 제목"), sheet_row("2", 선택="")]
        earlier = [sheet_row("1", 선택="o", 분위기="역", 제목="옛 제목"),
                   sheet_row("9", 선택="o"), sheet_row("8", 선택="")]
        merged = curation.merge_review(fresh, earlier)
        self.assertEqual([(r["사진번호"], r["선택"]) for r in merged],
                         [("1", "o"), ("2", ""), ("9", "o")])  # 9: marked, kept; 8: dropped
        self.assertEqual((merged[0]["제목"], merged[0]["분위기"]), ("새 제목", "역"))

    def test_an_unmarked_row_is_not_loaded_whatever_it_holds(self):
        records, problems = self.parse(sheet_row(선택="", 라이선스="알 수 없음"))
        self.assertEqual((records, problems), ([], []))

    def test_a_marked_row_becomes_a_record(self):
        (record,), problems = self.parse(sheet_row(장소id="poi_lighthouse", 열차역id=MUKHO))
        self.assertEqual(problems, [])
        self.assertEqual((record.id, record.moods, record.license, record.place_id,
                          record.view_station_id, record.position),
                         ("photo_kto_101", ("sunrise", "sea"), "공공누리 제1유형",
                          "poi_lighthouse", MUKHO, 0))
        self.assertEqual(record.keywords, ("촛대바위", "일출", "동해시"))

    def test_the_reviewers_moods_and_place_win_over_the_suggestions(self):
        (record,), _ = self.parse(sheet_row(분위기="station, 레트로·골목", 장소id="-",
                                            **{"장소id 제안": "poi_alley"}))
        self.assertEqual((record.moods, record.place_id), (("station", "retro"), None))

    def test_any_other_licence_is_refused(self):
        for cell in ("공공누리 제2유형", "공공누리 제4유형", "", "저작권자 문의"):
            with self.subTest(cell):
                records, problems = self.parse(sheet_row(라이선스=cell))
                self.assertEqual(records, [])
                self.assertIn("라이선스", problems[0])
        (record,), problems = self.parse(sheet_row(라이선스="공공누리 제0유형"))
        self.assertEqual((record.license, problems), ("공공누리 제0유형", []))

    def test_one_bad_row_stops_every_row(self):
        records, problems = self.parse(sheet_row("1"), sheet_row("2", 분위기="몽환"),
                                       sheet_row("3", 장소id="poi_gone"),
                                       sheet_row("4", 열차역id="poi_alley"),
                                       sheet_row("5", **{"원본 링크": "사진.jpg"}),
                                       sheet_row("1"), sheet_row("x"),
                                       sheet_row("7", 분위기="", **{"분위기 제안": ""}))
        self.assertEqual(records, [])
        self.assertEqual([p.split(": ")[1][:6] for p in problems], [
            "목록에 없는", "모르는 장소", "열차역id가", "원본 링크가", "같은 사진번", "사진번호가 ",
            "분위기가 하"])
        self.assertTrue(problems[0].startswith("3행(사진번호 2)"))

    def test_the_words_a_picture_is_embedded_from(self):
        (record,), _ = self.parse(sheet_row(장소id="poi_lighthouse", 설명="등대 위로 뜨는 해"))
        self.assertEqual(curation.embedding_text(record, PLACES),
                         "추암 촛대바위 일출\n일출·노을, 바다\n묵호등대\n촛대바위, 일출, 동해시\n등대 위로 뜨는 해")
        (plain,), _ = self.parse(sheet_row(장소id="poi_lighthouse"))
        self.assertNotEqual(curation.content_hash(record, PLACES),
                            curation.content_hash(plain, PLACES))


class LoadScriptTests(unittest.TestCase):
    """scripts/onboarding_photos.py load, on the seeded in-memory database."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "onboarding_photos_script", PROJECT_ROOT / "scripts" / "onboarding_photos.py")
        cls.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.script)

    def setUp(self):
        self.app = createStorageApp()
        self.addCleanup(self.app.state.databaseEngine.dispose)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.sheet = Path(self.folder.name) / "sheet.csv"
        self.settings = Settings(chat_mock=True, llm_usage_log="/dev/null",
                                 embedding_model="test-model")

    def write(self, *rows):
        with self.sheet.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=curation.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def load(self, **options):
        options = {"dry_run": False, "prune": False, "with_vectors": False, **options}
        with self.app.state.sessionFactory() as session, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.script.load(session, self.settings, self.sheet, **options)
        return json.loads(out.getvalue())

    def stored(self):
        with self.app.state.sessionFactory() as session:
            return {row.id: row for row in session.scalars(select(OnboardingPhotoEntity))}

    def test_marked_rows_are_stored_and_loading_twice_changes_nothing(self):
        self.write(sheet_row("1", 장소id=MUKHO), sheet_row("2", 선택=""), sheet_row("3"))
        self.assertEqual(self.load()["new"], 2)
        rows = self.stored()
        self.assertEqual(sorted(rows), ["photo_kto_1", "photo_kto_3"])
        self.assertEqual((rows["photo_kto_1"].placeId, rows["photo_kto_1"].moods,
                          rows["photo_kto_1"].source), (MUKHO, ["sunrise", "sea"],
                                                        "한국관광공사 포토코리아"))
        report = self.load()
        self.assertEqual((report["marked"], report["new"]), (2, 0))

    def test_a_problem_stops_before_the_database_is_touched(self):
        self.write(sheet_row("1"), sheet_row("2", 라이선스="공공누리 제3유형"))
        with self.assertRaises(SystemExit) as stopped:
            self.load()
        self.assertIn("라이선스", str(stopped.exception))
        self.assertEqual(self.stored(), {})

    def test_a_dry_run_reports_and_stores_nothing(self):
        self.write(sheet_row("1"))
        self.assertEqual(self.load(dry_run=True)["new"], 1)
        self.assertEqual(self.stored(), {})

    def test_unmarking_keeps_the_picture_unless_pruned(self):
        self.write(sheet_row("1"), sheet_row("2"))
        self.load()
        self.write(sheet_row("1"), sheet_row("2", 선택=""))
        self.assertEqual(self.load()["no_longer_marked_but_kept"], 1)
        self.assertEqual(len(self.stored()), 2)
        self.assertEqual(self.load(prune=True)["removed"], 1)
        self.assertEqual(sorted(self.stored()), ["photo_kto_1"])

    def test_only_pictures_whose_words_changed_are_embedded_again(self):
        asked = []

        def fake(settings, texts):
            asked.append(texts)
            return [[0.5] * 1536 for _ in texts]

        self.write(sheet_row("1"), sheet_row("2"))
        with patch.object(self.script, "embed", fake):
            self.assertEqual(self.load(with_vectors=True)["embedded"], 2)
            self.assertEqual(self.load(with_vectors=True)["embedded"], 0)
            self.write(sheet_row("1"), sheet_row("2", 설명="새 설명"))
            self.assertEqual(self.load(with_vectors=True)["embedded"], 1)
        self.assertEqual([len(texts) for texts in asked], [2, 1])
        self.assertIn("새 설명", asked[1][0])
        rows = self.stored()
        self.assertEqual({r.embeddingModel for r in rows.values()}, {"test-model"})

    def test_embedding_with_another_model_than_the_documents_is_refused(self):
        with self.app.state.sessionFactory() as session:
            chunk = session.scalars(select(KnowledgeChunkEntity)).first()
            chunk.embeddingModel, chunk.embedding = "doc-model", [0.1] * 1536
            session.commit()
        self.write(sheet_row("1"))
        with self.assertRaises(SystemExit) as stopped:
            self.load(with_vectors=True)
        self.assertIn("doc-model", str(stopped.exception))
        self.assertEqual(self.stored(), {})
        self.assertEqual(self.load()["new"], 1)  # without vectors the model does not matter

    def test_changed_words_without_embedding_drop_the_old_vector(self):
        self.write(sheet_row("1"))
        with patch.object(self.script, "embed", lambda s, texts: [[0.5] * 1536 for _ in texts]):
            self.load(with_vectors=True)
        self.write(sheet_row("1", 설명="바뀐 설명"))
        self.assertEqual(self.load()["without_vector"], 1)
        self.assertIsNone(self.stored()["photo_kto_1"].embedding)


class OnboardingPhotosMigrationTests(unittest.TestCase):
    """The revision creates exactly the two tables the entities describe."""

    TABLES = ("onboarding_photos", "user_tastes")

    def setUp(self):
        path = PROJECT_ROOT / "alembic" / "versions" / "onboarding_photos.py"
        spec = importlib.util.spec_from_file_location("onboarding_photos", path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        for name in ("users", "places"):
            metadata.tables[name].create(self.engine)

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def test_it_is_the_one_head_after_the_train_type_revision(self):
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        self.assertEqual(ScriptDirectory.from_config(config).get_heads(), ["onboarding_photos"])
        self.assertEqual(self.migration.down_revision, "0006_train_type_contract")

    def test_postgresql_sql_only_adds_the_two_tables(self):
        output = io.StringIO()
        config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        with patch.dict(os.environ, {}, clear=True), patch("sqlalchemy.create_engine") as engine:
            command.upgrade(config, "0006_train_type_contract:onboarding_photos", sql=True)
        engine.assert_not_called()
        sql = output.getvalue()
        self.assertEqual(sql.count("CREATE TABLE"), 2)
        self.assertIn("embedding VECTOR(1536)", sql)
        self.assertIn("moods JSONB NOT NULL", sql)
        self.assertNotIn("ALTER TABLE", sql)

    def test_upgrade_matches_the_entities_and_downgrade_removes_the_tables(self):
        self.run_step(self.migration.upgrade)
        inspector = inspect(self.engine)
        for name in self.TABLES:
            with self.subTest(name):
                model = metadata.tables[name]
                self.assertEqual({c["name"]: c["nullable"] for c in inspector.get_columns(name)},
                                 {c.name: c.nullable for c in model.columns})
                self.assertEqual({(tuple(f["constrained_columns"]), f["referred_table"],
                                   f["options"].get("ondelete"))
                                  for f in inspector.get_foreign_keys(name)},
                                 {((f.parent.name,), f.column.table.name, f.ondelete)
                                  for f in model.foreign_keys})
        self.run_step(self.migration.downgrade)
        self.assertFalse(set(self.TABLES) & set(inspect(self.engine).get_table_names()))


if __name__ == "__main__":
    unittest.main()
