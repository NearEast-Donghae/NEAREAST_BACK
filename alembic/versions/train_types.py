"""Train types: only five names are allowed, and each is stored with where it was confirmed.

Types are not in the collected timetable. They are confirmed by a person and written
here and in the seed file; a train without a confirmed type stays NULL and is shown by
number and line. The timetable import carries types over by train number, so collecting
the timetable again does not lose them.
"""

import sqlalchemy as sa
from alembic import op

revision = "train_types"
down_revision = "knowledge_enrichment"
branch_labels = None
depends_on = None

TRAIN_TYPES = ("KTX-이음", "ITX-마음", "ITX-새마을", "누리로", "무궁화호")
RULE = "train_type IS NULL OR train_type IN ({})".format(
    ", ".join(f"'{name}'" for name in TRAIN_TYPES)
)
CONSTRAINT = "ck_train_services_train_type"
# Spellings seen in other sources that mean one of the five.
SPELLINGS = {"무궁화": "무궁화호", "ITX 새마을": "ITX-새마을", "ITX새마을": "ITX-새마을",
             "ITX 마음": "ITX-마음", "KTX 이음": "KTX-이음"}

SOURCE = "레일블루 화면에서 수동 확인, 2026-11-20"
# train number -> confirmed type. Only confirmed trains are listed; the rest stay NULL.
CONFIRMED: dict[str, str] = {}


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
    for trainNumber, name in CONFIRMED.items():
        op.execute(
            sa.text(
                "UPDATE train_services SET train_type = :name, train_type_source = :source "
                "WHERE train_number = :number"
            ).bindparams(name=name, source=SOURCE, number=trainNumber)
        )


def downgrade():
    # Types this revision wrote are removed; types that were there before are kept.
    op.execute(
        sa.text(
            "UPDATE train_services SET train_type = NULL WHERE train_type_source = :source"
        ).bindparams(source=SOURCE)
    )
    with op.batch_alter_table("train_services") as table:
        table.drop_constraint(CONSTRAINT, type_="check")
        table.drop_column("train_type_source")
