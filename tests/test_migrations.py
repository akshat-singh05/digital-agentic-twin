"""Alembic migration tests — each runs on its own throwaway SQLite file."""

import os
import tempfile

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.database import Base
from app.db_migrations import downgrade, migrate_to_head, upgrade


@pytest.fixture
def scratch_engine():
    path = os.path.join(tempfile.mkdtemp(prefix="twin_mig_"), "mig.db")
    eng = create_engine("sqlite:///" + path.replace("\\", "/"))
    yield eng
    eng.dispose()


def _version(eng):
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_migrations_build_exactly_the_model_schema(scratch_engine):
    migrate_to_head(scratch_engine)
    with scratch_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_migrate_to_head_is_idempotent(scratch_engine):
    migrate_to_head(scratch_engine)
    migrate_to_head(scratch_engine)
    assert _version(scratch_engine) == "0005"


def test_downgrade_to_base_removes_every_table(scratch_engine):
    migrate_to_head(scratch_engine)
    downgrade("base", scratch_engine)
    assert set(inspect(scratch_engine).get_table_names()) == {"alembic_version"}


def test_pre_migration_database_is_stamped_upgraded_and_backfilled(scratch_engine):
    # Recreate a database exactly as the app built it before Alembic:
    # the baseline tables and no alembic_version table.
    upgrade("0001", scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text(
            "INSERT INTO users (id, name, email, created_at) "
            "VALUES (1, 'Legacy', 'legacy@example.com', '2026-01-01')"
        ))
        conn.execute(text(
            "INSERT INTO subscriptions (id, user_id, provider, plan_name, monthly_cost, "
            "is_active, start_date, created_at) VALUES "
            "(10, 1, 'Jio', 'Old', 499, 0, '2025-01-01', '2025-01-01'), "
            "(11, 1, 'Jio', 'Gold', 599, 1, '2026-01-01', '2026-01-01')"
        ))
        conn.execute(text(
            "INSERT INTO usage_data (id, user_id, provider, period_start, period_end, "
            "data_used_gb, created_at) VALUES "
            "(1, 1, 'Jio', '2026-01-01', '2026-01-31', 20, '2026-02-01'), "
            "(2, 1, 'Netflix', '2026-01-01', '2026-01-31', 5, '2026-02-01')"
        ))

    migrate_to_head(scratch_engine)

    assert _version(scratch_engine) == "0005"
    with scratch_engine.connect() as conn:
        linked = dict(conn.execute(text("SELECT id, subscription_id FROM usage_data")).all())
        users = conn.execute(text("SELECT count(*) FROM users")).scalar()
    assert linked == {1: 11, 2: None}   # active Jio plan; no Netflix subscription
    assert users == 1


def test_foreign_keys_are_enforced(scratch_engine):
    migrate_to_head(scratch_engine)
    with pytest.raises(Exception, match="FOREIGN KEY"):
        with scratch_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO subscriptions (user_id, provider, plan_name, monthly_cost, "
                "is_active, start_date, created_at) "
                "VALUES (999, 'Jio', 'Gold', 599, 1, '2026-01-01', '2026-01-01')"
            ))


def test_deleting_a_user_cascades_to_their_data(scratch_engine):
    migrate_to_head(scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, name, email, created_at) VALUES (1, 'U', 'u@x.io', '2026-01-01')"
        ))
        conn.execute(text(
            "INSERT INTO subscriptions (id, user_id, provider, plan_name, monthly_cost, "
            "is_active, start_date, created_at) VALUES (1, 1, 'Jio', 'G', 599, 1, '2026-01-01', '2026-01-01')"
        ))
        conn.execute(text(
            "INSERT INTO consents (user_id, scope, granted, updated_at) "
            "VALUES (1, 'usage_analysis', 1, '2026-01-01')"
        ))
        conn.execute(text("DELETE FROM users WHERE id = 1"))
        remaining = [
            conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            for t in ("subscriptions", "consents")
        ]
    assert remaining == [0, 0]


def test_briefs_migration_replaces_the_simulated_rounds_and_keeps_other_data(scratch_engine):
    upgrade("0003", scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, name, email, role, created_at) "
            "VALUES (1, 'U', 'u@x.io', 'user', '2026-01-01')"))
        conn.execute(text(
            "INSERT INTO subscriptions (id, user_id, provider, plan_name, monthly_cost, "
            "is_active, start_date, created_at) VALUES (1, 1, 'Jio', 'G', 599, 1, '2026-01-01', '2026-01-01')"))
        conn.execute(text(
            "INSERT INTO negotiation_history (subscription_id, round_number, agent_offer, status, created_at) "
            "VALUES (1, 1, 539.1, 'accepted', '2026-01-01')"))

    migrate_to_head(scratch_engine)

    tables = set(inspect(scratch_engine).get_table_names())
    assert "negotiation_briefs" in tables and "negotiation_history" not in tables
    with scratch_engine.connect() as conn:
        assert conn.execute(text("SELECT monthly_cost FROM subscriptions WHERE id = 1")).scalar() == 599

    downgrade("0003", scratch_engine)
    tables = set(inspect(scratch_engine).get_table_names())
    assert "negotiation_history" in tables and "negotiation_briefs" not in tables
    with scratch_engine.connect() as conn:       # the user's own data survives both directions
        assert conn.execute(text("SELECT COUNT(*) FROM subscriptions")).scalar() == 1


def test_a_brief_needs_a_valid_status(scratch_engine):
    migrate_to_head(scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, name, email, role, created_at) VALUES (1, 'U', 'u@x.io', 'user', '2026-01-01')"))
        conn.execute(text(
            "INSERT INTO subscriptions (id, user_id, provider, plan_name, monthly_cost, "
            "is_active, start_date, created_at) VALUES (1, 1, 'Jio', 'G', 599, 1, '2026-01-01', '2026-01-01')"))
    with pytest.raises(Exception, match="CHECK"):
        with scratch_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO negotiation_briefs (user_id, subscription_id, status, current_cost, effective_cost, "
                "opening_ask, walk_away_price, content, draft_message, included_usage, created_at) "
                "VALUES (1, 1, 'sent', 599, 599, 539, 569, '{}', 'hi', 0, '2026-01-01')"))
