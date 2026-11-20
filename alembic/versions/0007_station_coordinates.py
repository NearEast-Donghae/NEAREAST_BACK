"""Coordinates for 추암역 and 망상역, which the seed leaves empty.

Without them the two stations cannot be an end of a walking-time calculation, so "near
추암역" fell back to example data. Values were supplied by the project owner on 2026-11-20.
Only empty coordinates are filled: a database that already has values for these stations
(a later seed) keeps them. The seed import keeps existing valid coordinates as well, so
loading neareast.seed.gz after this revision does not empty them again.
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_station_coordinates"
down_revision = "0006_train_type_contract"
branch_labels = None
depends_on = None

COORDINATES = {
    "station_chuam": (37.478041986238, 129.157596122332),
    "station_mangsang": (37.5873289165971, 129.097389363461),
}


def upgrade():
    for place, (lat, lng) in COORDINATES.items():
        op.execute(sa.text(
            "UPDATE places SET lat = :lat, lng = :lng "
            "WHERE id = :id AND (lat IS NULL OR lng IS NULL)"
        ).bindparams(lat=lat, lng=lng, id=place))


def downgrade():
    # Only values this revision wrote are removed.
    for place, (lat, lng) in COORDINATES.items():
        op.execute(sa.text(
            "UPDATE places SET lat = NULL, lng = NULL WHERE id = :id AND lat = :lat AND lng = :lng"
        ).bindparams(lat=lat, lng=lng, id=place))
