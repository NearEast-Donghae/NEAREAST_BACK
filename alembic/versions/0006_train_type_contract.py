"""Train type schema only; reviewed values and number corrections come from the seed."""

import sqlalchemy as sa

from alembic import op

revision = "0006_train_type_contract"
down_revision = "0005_reference_records"
branch_labels = None
depends_on = None

RULE = (
    "train_type IS NULL OR train_type IN "
    "('KTX-이음', 'ITX-마음', 'ITX-새마을', '새마을호', '누리로', '무궁화호')"
)


def upgrade():
    with op.batch_alter_table("train_services") as table:
        table.add_column(sa.Column("train_type_source", sa.String(200), nullable=True))
    for old, new in {"무궁화": "무궁화호", "ITX 새마을": "ITX-새마을", "ITX새마을": "ITX-새마을",
                     "ITX 마음": "ITX-마음", "KTX 이음": "KTX-이음"}.items():
        op.execute(sa.text("UPDATE train_services SET train_type=:new WHERE train_type=:old")
                   .bindparams(new=new, old=old))
    with op.batch_alter_table("train_services") as table:
        table.create_check_constraint("ck_train_services_train_type", RULE)


def downgrade():
    with op.batch_alter_table("train_services") as table:
        table.drop_constraint("ck_train_services_train_type", type_="check")
        table.drop_column("train_type_source")
