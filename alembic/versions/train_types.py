"""Train types: only six names are allowed, and each is stored with where it was confirmed.

Types are not in the collected timetable. They are confirmed by a person and written
here and in the seed file; a train without a confirmed type stays NULL and is shown by
number and line. The timetable import carries types over by train number, so collecting
the timetable again does not lose them.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "train_types"
down_revision = "knowledge_enrichment"
branch_labels = None
depends_on = None

TRAIN_TYPES = ("KTX-이음", "ITX-마음", "ITX-새마을", "새마을호", "누리로", "무궁화호")
RULE = "train_type IS NULL OR train_type IN ({})".format(
    ", ".join(f"'{name}'" for name in TRAIN_TYPES)
)
CONSTRAINT = "ck_train_services_train_type"
# Spellings seen in other sources that mean one of the five.
SPELLINGS = {"무궁화": "무궁화호", "ITX 새마을": "ITX-새마을", "ITX새마을": "ITX-새마을",
             "ITX 마음": "ITX-마음", "KTX 이음": "KTX-이음"}

SOURCE = "레일블루 화면에서 수동 확인, 2026-11-20"
# train number -> confirmed type, read off the RailBlue screen for 동해역 on 2026-11-21 by
# a person. Only confirmed trains are listed; a train not listed stays NULL.
CONFIRMED: dict[str, str] = {
    "751": "KTX-이음", "752": "KTX-이음", "753": "KTX-이음",
    "754": "KTX-이음", "755": "KTX-이음", "756": "KTX-이음",
    "840": "KTX-이음", "841": "KTX-이음", "842": "KTX-이음",
    "843": "KTX-이음", "844": "KTX-이음", "845": "KTX-이음",
    "846": "KTX-이음", "847": "KTX-이음", "848": "KTX-이음",
    "849": "KTX-이음", "881": "KTX-이음", "883": "KTX-이음",
    "884": "KTX-이음", "886": "KTX-이음", "887": "KTX-이음",
    "888": "KTX-이음", "1641": "ITX-마음", "1642": "ITX-마음",
    "1671": "무궁화호", "1672": "무궁화호", "1673": "무궁화호",
    "1674": "무궁화호", "1675": "무궁화호", "1676": "무궁화호",
    "1677": "무궁화호", "1678": "무궁화호", "1681": "무궁화호",
    "1682": "무궁화호", "1721": "ITX-마음", "1771": "무궁화호",
    "1772": "무궁화호", "1773": "무궁화호", "1774": "무궁화호",
    "1775": "무궁화호", "1776": "무궁화호", "1777": "무궁화호",
    "1778": "무궁화호", "1781": "누리로", "1782": "누리로",
    "1801": "ITX-마음", "1802": "ITX-마음", "1803": "ITX-마음",
    "1804": "ITX-마음", "1805": "ITX-마음", "1806": "ITX-마음",
    "1807": "ITX-마음", "1822": "ITX-마음", "1824": "ITX-마음",
    "1831": "ITX-마음", "1855": "누리로", "1856": "누리로",
    "1861": "누리로", "1862": "누리로", "1863": "누리로",
    "1864": "누리로", "2502": "새마을호", "2503": "새마을호",
}
# collected number -> (number shown for the target date, why). The same pair is applied by
# the timetable import (ingestion.NUMBER_OVERRIDES) so collecting again keeps it.
RENUMBERED = {"1814": ("1804", "11/21 레일블루 기준, 3주 기록은 1814")}


def upgrade():
    connection = op.get_bind()
    with op.batch_alter_table("train_services") as table:
        table.add_column(sa.Column("train_type_source", sa.String(length=200), nullable=True))
    for spelling, name in SPELLINGS.items():
        op.execute(
            sa.text("UPDATE train_services SET train_type = :name WHERE train_type = :spelling")
            .bindparams(name=name, spelling=spelling)
        )
    if not op.get_context().as_sql:
        # A value that is none of the five is not dropped silently: someone typed it in.
        unknown = connection.execute(sa.text(
            "SELECT DISTINCT train_type FROM train_services WHERE NOT ({})".format(RULE)
        )).scalars().all()
        if unknown:
            raise RuntimeError(
                f"train_services.train_type has values outside {TRAIN_TYPES}: {unknown}. "
                "Correct or clear them, then run the upgrade again."
            )
    with op.batch_alter_table("train_services") as table:
        table.create_check_constraint(CONSTRAINT, RULE)
    renumber(RENUMBERED)
    # The seed file runs in revision 0002, before the source column exists, so it can only
    # carry the type. The source is written here for new and existing databases alike.
    for trainNumber, name in CONFIRMED.items():
        op.execute(
            sa.text(
                "UPDATE train_services SET train_type = :name, train_type_source = :source "
                "WHERE train_number = :number"
            ).bindparams(name=name, source=SOURCE, number=trainNumber)
        )


def renumber(pairs):
    """Give a train another number: a new row under the new id, its stops moved over, the
    old row removed. Skipped where the new number already exists in that snapshot."""
    postgresql = op.get_bind().dialect.name == "postgresql"
    for old, (new, note) in pairs.items():
        patch = sa.literal(json.dumps({"no": new, "number_note": note}, ensure_ascii=False))
        details = (sa.text("details || CAST(:patch AS JSONB)") if postgresql
                   else sa.text("json_patch(details, :patch)"))
        free = ("NOT EXISTS (SELECT 1 FROM train_services other WHERE other.train_number = :new "
                "AND other.snapshot_id = train_services.snapshot_id)")
        op.execute(sa.text(
            "INSERT INTO train_services (id, snapshot_id, train_number, train_type, "
            "train_type_source, details) "
            f"SELECT snapshot_id || ':' || :new, snapshot_id, :new, train_type, "
            f"train_type_source, {details.text} FROM train_services "
            f"WHERE train_number = :old AND {free}"
        ).bindparams(new=new, old=old, patch=patch.value))
        op.execute(sa.text(
            "UPDATE train_stops SET id = replace(id, ':' || :old || ':', ':' || :new || ':'), "
            "service_id = replace(service_id, ':' || :old, ':' || :new) "
            "WHERE service_id IN (SELECT id FROM train_services WHERE train_number = :old) "
            "AND replace(service_id, ':' || :old, ':' || :new) IN (SELECT id FROM train_services)"
        ).bindparams(new=new, old=old))
        op.execute(sa.text(
            "DELETE FROM train_services WHERE train_number = :old AND EXISTS "
            "(SELECT 1 FROM train_services other WHERE other.train_number = :new "
            "AND other.snapshot_id = train_services.snapshot_id)"
        ).bindparams(new=new, old=old))


def downgrade():
    renumber({new: (old, None) for old, (new, _) in RENUMBERED.items()})
    # Types this revision wrote are removed; types that were there before are kept.
    op.execute(
        sa.text(
            "UPDATE train_services SET train_type = NULL WHERE train_type_source = :source"
        ).bindparams(source=SOURCE)
    )
    with op.batch_alter_table("train_services") as table:
        table.drop_constraint(CONSTRAINT, type_="check")
        table.drop_column("train_type_source")
