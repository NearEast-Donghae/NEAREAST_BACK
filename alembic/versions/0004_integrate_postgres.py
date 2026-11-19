"""Join the already applied planner schema and the upstream service migrations."""

revision = "0004_integrate_postgres"
down_revision = ("0003_planner_datasets", "knowledge_enrichment")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
