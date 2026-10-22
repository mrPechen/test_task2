"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-22

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # === users ===
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("balance >= 0", name="ck_users_balance_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )

    # === models_catalog ===
    op.create_table(
        "models_catalog",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("price_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("slug"),
    )

    # === legs ===
    op.create_table(
        "legs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_slug", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_ref_images", sa.Integer(), nullable=False),
        sa.Column("skip_on_moderation", sa.Boolean(), nullable=False),
        sa.CheckConstraint("max_ref_images >= 0", name="ck_legs_max_ref_images"),
        sa.ForeignKeyConstraint(
            ["model_slug"], ["models_catalog.slug"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_slug", "priority", name="uq_legs_model_priority"),
    )

    # === tasks ===
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("model_slug", sa.String(length=64), nullable=False),
        sa.Column("price_tokens", sa.BigInteger(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "ref_images", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("ref_images_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_leg_id", sa.Uuid(), nullable=True),
        sa.Column("result_url", sa.String(length=512), nullable=True),
        sa.Column("error_type", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["model_slug"], ["models_catalog.slug"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_user_status", "tasks", ["user_id", "status"])
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    # === attempts ===
    op.create_table(
        "attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("leg_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_type", sa.String(length=20), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("result_url", sa.String(length=512), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["leg_id"], ["legs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", name="uq_attempts_attempt_id"),
        sa.UniqueConstraint("task_id", "leg_id", name="uq_attempts_task_leg"),
    )
    op.create_index("ix_attempts_status", "attempts", ["status"])
    op.create_index("ix_attempts_task_id", "attempts", ["task_id"])

    # === ledger_entries ===
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("payment_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount != 0", name="ck_ledger_amount_non_zero"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_user_id", "ledger_entries", ["user_id"])
    op.create_index(
        "uq_ledger_task_kind",
        "ledger_entries",
        ["task_id", "kind"],
        unique=True,
        postgresql_where=sa.text("kind IN ('debit', 'refund')"),
    )
    op.create_index(
        "uq_ledger_payment_kind",
        "ledger_entries",
        ["payment_id", "kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'topup'"),
    )

    # === payments ===
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_payments_payment_id"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])

    # === webhook_events ===
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_id", sa.String(length=64), nullable=True),
        sa.Column("payload_status", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=20), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )

    # === data: стартовый каталог ===
    op.bulk_insert(
        sa.table(
            "models_catalog",
            sa.column("slug", sa.String()),
            sa.column("name", sa.String()),
            sa.column("price_tokens", sa.BigInteger()),
        ),
        [
            {"slug": "image-fast", "name": "Image Fast", "price_tokens": 2},
            {"slug": "video-long", "name": "Video Long", "price_tokens": 25},
        ],
    )
    op.bulk_insert(
        sa.table(
            "legs",
            sa.column("id", sa.Uuid()),
            sa.column("model_slug", sa.String()),
            sa.column("provider", sa.String()),
            sa.column("priority", sa.Integer()),
            sa.column("max_ref_images", sa.Integer()),
            sa.column("skip_on_moderation", sa.Boolean()),
        ),
        [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "model_slug": "image-fast",
                "provider": "alpha",
                "priority": 1,
                "max_ref_images": 4,
                "skip_on_moderation": False,
            },
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "model_slug": "image-fast",
                "provider": "beta",
                "priority": 2,
                "max_ref_images": 10,
                "skip_on_moderation": False,
            },
            {
                "id": "00000000-0000-0000-0000-000000000003",
                "model_slug": "video-long",
                "provider": "beta",
                "priority": 1,
                "max_ref_images": 2,
                "skip_on_moderation": False,
            },
            {
                "id": "00000000-0000-0000-0000-000000000004",
                "model_slug": "video-long",
                "provider": "alpha",
                "priority": 2,
                "max_ref_images": 1,
                "skip_on_moderation": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("payments")
    op.drop_table("ledger_entries")
    op.drop_table("attempts")
    op.drop_table("tasks")
    op.drop_table("legs")
    op.drop_table("models_catalog")
    op.drop_table("users")
