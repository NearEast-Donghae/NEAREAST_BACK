"""Neary's train tools on the shared timetable, and the example-data fallback without it."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app
from neareast_api.modules.chat import llm

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/v1/chat/messages"
BASIS = "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준의 어림 시각이에요."


def ask(client, message, **context):
    return client.post(URL, json={"message": message, "context": context}).json()


class SnapshotToolTests(unittest.TestCase):
    """Built-in responder (mock mode) over the committed snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(Settings()))

    def station_times(self, station_id):
        body = self.client.get(f"/api/v1/stations/{station_id}/trains").json()
        return [t["time"] for t in body["trains"]], body

    def test_answer_uses_the_same_trains_as_the_trains_api(self):
        times, api = self.station_times("station_chuam")
        body = ask(self.client, "추암역 열차 목록", focused="station_chuam")
        self.assertEqual(body["tool_used"], "search_trains")
        for time in times:
            self.assertIn(time, body["reply"])
        self.assertIn(api["trains"][0]["no"], body["reply"])

    def test_snapshot_answers_state_their_basis_and_are_not_called_example_data(self):
        body = ask(self.client, "여기 열차 언제 몰려?", focused="station_donghae")
        self.assertTrue(body["reply"].endswith(BASIS))
        self.assertNotIn("(예시 데이터)", body["reply"])
        self.assertTrue(body["proposals"])
        self.assertEqual({p["certainty"] for p in body["proposals"]}, {"estimated"})

    def test_gap_in_the_answer_is_a_real_gap_of_the_snapshot(self):
        _, api = self.station_times("station_donghae")
        body = ask(self.client, "여기 열차 언제 몰려?", focused="station_donghae")
        longest = max(api["rhythm"]["gaps"], key=lambda g: g["minutes"])
        self.assertIn(f"{longest['start']}~{longest['end']}", body["reply"])

    def test_passing_station_answer_says_times_are_interpolated(self):
        body = ask(self.client, "망상역 열차 리듬")
        self.assertIn("앞뒤 정차역 시각으로 어림했어요", body["reply"])
        self.assertIn("관찰만", body["reply"])
        self.assertIn("safety_passing_station", body["sources"])
        stopping = ask(self.client, "묵호역 열차 리듬")
        self.assertNotIn("앞뒤 정차역", stopping["reply"])

    def test_freight_station_gets_no_invented_trains(self):
        body = ask(self.client, "삼화역 열차 시간 알려줘")
        self.assertIsNone(body["tool_used"])
        self.assertIn("화물열차 시각은 공식 데이터가 없어서", body["reply"])
        self.assertNotRegex(body["reply"], r"\d{1,2}:\d{2}")

    def test_no_freight_train_in_any_answer_or_example(self):
        from neareast_api.modules.chat import tools
        self.assertFalse([t for trains in tools._TIMETABLE.values() for t in trains if "화물" in t.kind])
        self.assertNotIn("화물", ask(self.client, "동해역 열차 목록")["reply"])


class NoSnapshotFallbackTests(unittest.TestCase):
    def test_without_a_snapshot_example_data_is_used_and_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "knowledge", root / "knowledge")
            (root / "data").mkdir()
            shutil.copy(ROOT / "data" / "places.json", root / "data" / "places.json")
            client = TestClient(create_app(Settings(content_dir=str(root))))
            body = ask(client, "여기 열차 언제 몰려?", focused="station_donghae")
            self.assertTrue(body["reply"].startswith("(예시 데이터) "))
            self.assertNotIn("토요일 3주", body["reply"])
            self.assertEqual(body["tool_used"], "station_rhythm")
            self.assertEqual(client.get("/api/v1/stations/station_donghae/trains").status_code, 503)


class SnapshotLlmTests(unittest.TestCase):
    TOOL = {"name": "search_trains", "station_id": "station_chuam", "place_id": None, "after": "16:00"}

    def setUp(self):
        settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
        self.client = TestClient(create_app(settings))

    def ask(self, final_reply):
        first = {"intent": "train_lookup", "reply": "", "chips": [], "proposals": [], "sources": [],
                 "tool_request": self.TOOL}
        final = {**first, "reply": final_reply, "tool_request": None}
        with patch.object(llm, "chat_json", side_effect=[first, final]) as chat:
            body = self.client.post(URL, json={"message": "추암역 오후 열차",
                                               "context": {"focused": "station_chuam"}}).json()
        result = json.loads(chat.call_args_list[1].args[1][-1]["content"].split("\n")[1])
        return body, result

    def test_model_gets_compact_snapshot_lines_and_real_times_are_kept(self):
        body, result = self.ask("추암역은 16:07에 강릉행 열차가 있어요.")
        self.assertEqual(result["source"], "snapshot")
        self.assertEqual(result["after"], "16:00")
        self.assertEqual([line[:5] for line in result["trains"]], ["16:07", "17:28", "18:34"])
        self.assertIn("토요일 3주", result["basis"])
        self.assertEqual(body["reply"], f"추암역은 16:07에 강릉행 열차가 있어요. {BASIS}")
        self.assertEqual(body["mode"], "llm")

    def rhythm(self, station_id, message, final_reply, sources=()):
        first = {"intent": "train_lookup", "reply": "", "chips": [], "proposals": [], "sources": [],
                 "tool_request": {"name": "station_rhythm", "station_id": station_id,
                                  "place_id": None, "after": None}}
        final = {**first, "reply": final_reply, "sources": list(sources), "tool_request": None}
        with patch.object(llm, "chat_json", side_effect=[first, final]) as chat:
            body = self.client.post(URL, json={"message": message, "context": {}}).json()
        return body, json.loads(chat.call_args_list[1].args[1][-1]["content"].split("\n")[1])

    def test_busy_station_is_given_as_a_digest_but_verified_against_the_whole_day(self):
        reply = "동해역은 13:16에도 부전행 열차가 서요."  # not in the digest, but a real train
        body, result = self.rhythm("station_donghae", "동해역 열차 리듬", reply)
        self.assertEqual(result["total_trains"], 63)
        self.assertNotIn("trains", result)  # no 63-line list for the model to recite
        self.assertLessEqual(len(result["busiest_trains"]), 10)
        self.assertTrue(all(key.endswith("시대") for key in result["hourly_counts"]))
        self.assertNotIn("13:16", json.dumps(result, ensure_ascii=False))
        self.assertTrue(body["reply"].startswith(reply))  # verified against the full day
        body, _ = self.rhythm("station_donghae", "동해역 열차 리듬", "동해역은 13:19에 열차가 서요.")
        self.assertIn("확인된 시간표", body["reply"])  # 13:19 is nobody's time

    def test_long_station_list_is_capped_for_the_model(self):
        self.TOOL = {**self.TOOL, "station_id": "station_donghae", "after": None}
        with patch.object(llm, "chat_json", side_effect=[
            {"intent": "train_lookup", "reply": "", "chips": [], "proposals": [], "sources": [],
             "tool_request": self.TOOL},
            {"intent": "train_lookup", "reply": "동해역 첫 열차는 05:55예요.", "chips": [],
             "proposals": [], "sources": [], "tool_request": None}]) as chat:
            self.client.post(URL, json={"message": "동해역 열차 목록", "context": {}})
        result = json.loads(chat.call_args_list[1].args[1][-1]["content"].split("\n")[1])
        self.assertEqual((len(result["trains"]), result["total_trains"], result["more"]), (10, 63, 53))

    def test_both_basis_sentences_are_added_even_if_the_model_mentions_one(self):
        said_one = "망상역은 최근 토요일 3주 기록으로 보면 10:42쯤 열차가 지나가요. 관찰만 할 수 있어요."
        body, _ = self.rhythm("station_mangsang", "망상역 열차 몇 시에 지나가?", said_one)
        self.assertEqual(body["reply"].count("토요일 3주"), 1)  # not repeated
        self.assertTrue(body["reply"].endswith("통과 시각은 앞뒤 정차역 시각으로 어림했어요."))
        said_none = "망상역은 10:42쯤 열차가 지나가요. 관찰만 할 수 있어요."
        body, _ = self.rhythm("station_mangsang", "망상역 열차 몇 시에 지나가?", said_none)
        self.assertTrue(body["reply"].endswith(
            f"{BASIS} 통과 시각은 앞뒤 정차역 시각으로 어림했어요."))

    def test_count_from_a_given_document_is_kept_even_without_citing_it(self):
        reply = "추암역은 하루 6편이 서요. 강릉 방면 3편, 동대구·부전 방면 3편이에요."
        with patch.object(llm, "chat_json", return_value={
            "intent": "train_lookup", "reply": reply, "chips": [], "proposals": [],
            "sources": [], "tool_request": None}):
            body = self.client.post(URL, json={"message": "추암역 하루에 몇 편 다녀?",
                                               "context": {}}).json()
        self.assertEqual(body["reply"], reply)
        with patch.object(llm, "chat_json", return_value={
            "intent": "train_lookup", "reply": "추암역은 하루 12편이 서요.", "chips": [],
            "proposals": [], "sources": [], "tool_request": None}):
            body = self.client.post(URL, json={"message": "추암역 하루에 몇 편 다녀?",
                                               "context": {}}).json()
        self.assertIn("확인된 시간표", body["reply"])  # 12편 is in no document or tool result

    def test_time_outside_the_snapshot_is_replaced_without_a_basis_claim(self):
        body, _ = self.ask("추암역은 16:30에 강릉행 열차가 있어요.")
        self.assertIn("확인된 시간표", body["reply"])
        self.assertNotIn("16:30", body["reply"])
        self.assertNotIn("토요일 3주", body["reply"])  # no basis sentence for a withdrawn claim


if __name__ == "__main__":
    unittest.main()
