"""Shared timetable: API contract, queries over the committed snapshot, snapshot building."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app
from neareast_api.modules.trains import service

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"


def minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


class TrainsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(Settings()))

    def get(self, path):
        return self.client.get(API + path)

    def test_station_trains_are_ordered_and_carry_the_basis(self):
        body = self.get("/stations/station_donghae/trains").json()
        self.assertEqual(body["station_name"], "동해")
        self.assertTrue(body["boarding"])
        times = [t["time"] for t in body["trains"]]
        self.assertGreater(len(times), 30)
        self.assertEqual(times, sorted(times, key=minutes))
        self.assertEqual(body["basis"]["certainty"], "estimated")
        self.assertEqual(body["basis"]["record_dates"], ["2026-11-14", "2026-11-07", "2026-10-31"])
        self.assertIn("최근 토요일 3주", body["basis"]["summary"])
        self.assertTrue(any("당일 운행 여부" in limit for limit in body["basis"]["limits"]))
        first = body["trains"][0]
        self.assertEqual(set(first), {
            "no", "type", "line", "direction", "origin", "destination", "weekly_saturday",
            "in_plan", "spread_min", "time", "arr", "dep", "passing", "stop_type", "time_basis"})

    def test_rhythm_counts_match_the_list_and_gaps_are_real(self):
        body = self.get("/stations/station_chuam/trains").json()
        rhythm = body["rhythm"]
        self.assertEqual(sum(rhythm["hourly"].values()), len(body["trains"]))
        times = sorted(minutes(t["time"]) for t in body["trains"])
        for gap in rhythm["gaps"]:
            self.assertGreaterEqual(gap["minutes"], service.GAP_MINUTES)
            self.assertIn(minutes(gap["start"]), times)
            # nothing runs inside a gap
            self.assertFalse([t for t in times if minutes(gap["start"]) < t < minutes(gap["end"])])
        busy = self.get("/stations/station_donghae/trains").json()["rhythm"]
        self.assertEqual(busy["hourly"][busy["busiest_hour"]], max(busy["hourly"].values()))

    def test_filters_narrow_the_list_and_bad_values_are_rejected(self):
        everything = self.get("/stations/station_donghae/trains").json()["trains"]
        later = self.get("/stations/station_donghae/trains?after=16:00").json()["trains"]
        down = self.get("/stations/station_donghae/trains?direction=D").json()["trains"]
        self.assertTrue(later and all(minutes(t["time"]) >= 16 * 60 for t in later))
        self.assertTrue(down and all(t["direction"] == "D" for t in down))
        self.assertLess(len(later), len(everything))
        self.assertLess(len(down), len(everything))
        for bad in ("after=4pm", "after=25:00", "direction=X"):
            with self.subTest(bad):
                self.assertEqual(self.get(f"/stations/station_donghae/trains?{bad}").status_code, 422)

    def test_passing_station_times_are_estimates_with_their_basis(self):
        body = self.get("/stations/station_mangsang/trains").json()
        self.assertFalse(body["boarding"])
        self.assertIn("어림", body["note"])
        self.assertTrue(body["trains"])
        for train in body["trains"]:
            self.assertTrue(train["passing"])
            self.assertIsNone(train["arr"])
            self.assertIn("거리 비율", train["time_basis"])
        stopping = self.get("/stations/station_mukho/trains").json()
        self.assertIsNone(stopping["note"])
        self.assertFalse(any(t["passing"] for t in stopping["trains"]))

    def test_passing_estimate_lies_between_its_neighbours(self):
        for number in ("1861", "752"):  # one each way
            stops = {s["name"]: s for s in self.get(f"/trains/{number}").json()["stops"]}
            around = sorted([minutes(stops["묵호"]["dep"] or stops["묵호"]["arr"]),
                             minutes(stops["정동진"]["dep"] or stops["정동진"]["arr"])])
            self.assertLess(around[0], minutes(stops["망상"]["pass_time"]))
            self.assertLess(minutes(stops["망상"]["pass_time"]), around[1])

    def test_freight_station_has_no_records_and_unknown_ids_are_404(self):
        freight = self.get("/stations/station_samhwa/trains")
        self.assertEqual(freight.status_code, 200)
        self.assertEqual(freight.json()["trains"], [])
        self.assertIn("화물열차 시각은 공식 데이터가 없어요", freight.json()["note"])
        for path in ("/stations/station_nowhere/trains", "/stations/poi_nongoldam/trains",
                     "/trains?from=station_donghae&to=station_nowhere", "/trains/99999"):
            with self.subTest(path):
                self.assertEqual(self.get(path).status_code, 404)

    def test_search_only_returns_rideable_trains_in_the_right_direction(self):
        body = self.get("/trains?from=station_donghae&to=station_mukho&after=16:00").json()
        self.assertTrue(body["trains"])
        for option in body["trains"]:
            self.assertGreaterEqual(minutes(option["dep"]), 16 * 60)
            self.assertGreater(minutes(option["arr"]), minutes(option["dep"]))
            self.assertEqual(option["minutes"], minutes(option["arr"]) - minutes(option["dep"]))
        forward = {o["no"] for o in self.get("/trains?from=station_donghae&to=station_mukho").json()["trains"]}
        back = {o["no"] for o in self.get("/trains?from=station_mukho&to=station_donghae").json()["trains"]}
        self.assertTrue(forward and back)
        self.assertFalse(forward & back)
        # nobody can get off at a passing station
        self.assertEqual(self.get("/trains?from=station_donghae&to=station_mangsang").json()["trains"], [])
        self.assertEqual(self.get("/trains?from=station_donghae").status_code, 422)

    def test_train_detail_lists_every_stop_in_order(self):
        body = self.get("/trains/00841").json()  # leading zeros as printed on tickets
        self.assertEqual(body["no"], "841")
        self.assertEqual((body["stops"][0]["name"], body["stops"][-1]["name"]),
                         (body["origin"], body["destination"]))
        self.assertEqual(body["stops"][-1]["place_id"], "station_donghae")
        timeline = [minutes(s["pass_time"] or s["dep"] or s["arr"]) for s in body["stops"]]
        self.assertEqual(timeline, sorted(timeline))

    def test_missing_snapshot_is_503_not_a_crash(self):
        with tempfile.TemporaryDirectory() as empty:
            client = TestClient(create_app(Settings(content_dir=empty)))
            response = client.get(API + "/trains/841")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "timetable unavailable"})
            # the place catalog is missing too: still a clean 503
            self.assertEqual(client.get(API + "/stations/station_donghae/trains").status_code, 503)


class TrainTypeTests(unittest.TestCase):
    def test_type_is_shown_only_when_the_table_has_it(self):
        timetable = service.load(str(ROOT))
        train = timetable.trains[0]
        blank = service.detail(timetable, train["no"])
        self.assertEqual(blank.type, timetable.types.get(train["no"]))
        filled = service.Timetable(timetable.meta, timetable.stations, timetable.trains,
                                   {train["no"]: "무궁화"})
        named = service.detail(filled, train["no"])
        self.assertEqual(named.type, "무궁화")
        self.assertTrue(service.describe(named).startswith(f"무궁화 {train['no']} "))
        if blank.type is None:
            self.assertTrue(service.describe(blank).startswith(f"{train['no']}({train['line']})"))


class SnapshotBuildTests(unittest.TestCase):
    """Pure functions of scripts/fetch_timetable.py (no network)."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("fetch", ROOT / "scripts" / "fetch_timetable.py")
        cls.fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fetch)

    def test_typical_time_ignores_a_one_off_delay(self):
        typical = self.fetch.typical
        self.assertEqual(typical(["10:23", "10:23", "10:31"], "earliest"), ("10:23", 8))
        self.assertEqual(typical(["09:29", "09:33", "09:32"], "middle"), ("09:32", 4))
        self.assertEqual(typical([None, None], "middle"), (None, 0))
        self.assertEqual(typical(["07:05"], "earliest"), ("07:05", 0))

    def stop(self, name, arr, dep):
        return {"name": name, "arr": arr, "dep": dep, "stop_type": "여객승하차"}

    def test_passing_station_is_interpolated_by_distance_both_ways(self):
        kilo = {"묵호": 601536, "망상": 601596, "정동진": 601765, "동해": 601476}
        north = self.fetch.interpolate_passes(
            [self.stop("묵호", "10:34", "10:37"), self.stop("정동진", "10:57", "10:57")], kilo)
        self.assertEqual([s["name"] for s in north], ["묵호", "망상", "정동진"])
        self.assertEqual(north[1]["pass"], "10:42")  # 10:37 + 20 min * 60/229
        self.assertEqual(north[1]["place_id"], "station_mangsang")
        south = self.fetch.interpolate_passes(
            [self.stop("정동진", "09:50", "09:53"), self.stop("묵호", "10:13", "10:14")], kilo)
        self.assertEqual(south[1]["pass"], "10:08")

    def test_nothing_is_interpolated_when_the_station_is_not_between(self):
        kilo = {"묵호": 601536, "망상": 601596, "정동진": 601765, "동해": 601476, "서울": 10000}
        for stops in (
            [self.stop("동해", "10:20", "10:23"), self.stop("묵호", "10:30", "10:31")],  # south of it
            [self.stop("서울", None, "05:13"), self.stop("정동진", "08:00", "08:01")],  # another line
            [self.stop("묵호", "10:34", "10:37"), self.stop("미상역", "10:57", "10:57")],  # no kilopost
        ):
            with self.subTest(stops[0]["name"]):
                self.assertEqual(len(self.fetch.interpolate_passes(stops, kilo)), 2)

    def test_committed_snapshot_matches_what_the_api_promises(self):
        timetable = service.load(str(ROOT))
        self.assertEqual(timetable.meta["certainty"], "estimated")
        self.assertEqual(set(timetable.stations), {"station_donghae", "station_mukho",
                                                   "station_chuam", "station_mangsang"})
        for train in timetable.trains:
            with self.subTest(train["no"]):
                self.assertTrue(train["saturdays_run"])
                self.assertEqual(train["weekly_saturday"], len(train["saturdays_run"]) == 3)
                for s in train["stops"]:
                    if s["arr"] and s["dep"]:
                        self.assertLessEqual(minutes(s["arr"]), minutes(s["dep"]) + 0, s)


if __name__ == "__main__":
    unittest.main()
