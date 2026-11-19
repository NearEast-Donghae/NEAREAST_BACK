"""Coordinates for the places seeded in 0002.

The seed file is left as it was: a database that already ran 0002 gets the coordinates
from this revision. A place that is not listed here keeps NULL (no walking time is
computed for it). Positions were looked up by hand on a map.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_place_coordinates"
down_revision = "0002_reference_content"
branch_labels = None
depends_on = None

# place id -> (lat, lng), WGS84
COORDINATES = {
    "station_donghae": (37.4982, 129.1238),
    "station_mukho": (37.5461, 129.1082),
}

places = sa.table("places", sa.column("id", sa.String), sa.column("lat", sa.Float),
                  sa.column("lng", sa.Float))


def upgrade():
    for placeId, (lat, lng) in COORDINATES.items():
        op.execute(places.update().where(places.c.id == placeId).values(lat=lat, lng=lng))


def downgrade():
    for placeId in COORDINATES:
        op.execute(places.update().where(places.c.id == placeId).values(lat=None, lng=None))
