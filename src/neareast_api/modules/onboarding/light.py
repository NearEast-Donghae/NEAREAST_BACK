"""Sunrise and sunset for a date, computed from latitude and longitude.

The NOAA solar position formulas (the short form of their sunrise/sunset calculator),
good to about a minute or two at this latitude. It is the time the sun's upper edge
meets a flat horizon: hills, buildings and weather are not known here, and the answer
says so. No network is involved.
"""

import datetime as dt
import math
from dataclasses import dataclass

KST = 9  # hours ahead of UTC; Korea has no summer time
# The middle of Donghae's coast (between 동해역 and 묵호역). Across the city the result
# differs by well under a minute, so one point serves every place.
DONGHAE = (37.52, 129.11)
ZENITH = math.radians(90.833)  # the sun's upper edge on the horizon, with refraction


@dataclass(frozen=True)
class SunTimes:
    sunrise: str  # "HH:MM", Korean time
    sunset: str


def _hhmm(minutes: float) -> str:
    whole = int(round(minutes)) % (24 * 60)
    return f"{whole // 60:02d}:{whole % 60:02d}"


def sun_times(date: dt.date, lat: float = DONGHAE[0], lng: float = DONGHAE[1]) -> SunTimes:
    day = date.timetuple().tm_yday
    year_days = 366 if date.year % 4 == 0 and (date.year % 100 or date.year % 400 == 0) else 365
    g = 2 * math.pi / year_days * (day - 1)
    equation = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                         - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    declination = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
                   - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
                   - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    hour_angle = math.degrees(math.acos(
        math.cos(ZENITH) / (math.cos(phi) * math.cos(declination))
        - math.tan(phi) * math.tan(declination)))
    noon = 720 - 4 * lng - equation + KST * 60  # minutes after midnight, Korean time
    return SunTimes(_hhmm(noon - 4 * hour_angle), _hhmm(noon + 4 * hour_angle))
