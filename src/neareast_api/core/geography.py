import math
import re


def validCoordinates(latitude, longitude):
    return (
        type(latitude) in (int, float) and type(longitude) in (int, float)
        and math.isfinite(latitude) and math.isfinite(longitude)
        and 37.3 <= latitude <= 37.7 and 128.8 <= longitude <= 129.3
    )


def dateKey(value):
    return re.sub(r"\D", "", str(value or ""))[:14].ljust(14, "0")
