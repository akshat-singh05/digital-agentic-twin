"""
Data Seeding Utility — Populate the database with realistic demo data.

Generates:
  - 5 users with realistic names, emails, and phone numbers
  - 1–3 subscriptions per user across various providers
  - 3–6 usage records per subscription (monthly billing periods)

Usage:
    python -m app.utils.seed_data          # from project root
    python app/utils/seed_data.py          # direct execution

Flags:
    --reset   Drop and recreate all tables before seeding (fresh start)
"""

import random
import sys
import os
import argparse
from datetime import datetime, timedelta, timezone

# ── Ensure project root is on sys.path when run directly ─────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.database import Base, engine, SessionLocal
from app.models import User, Subscription, UsageData


# ═════════════════════════════════════════════════════════════
#  DATA POOLS — curated for realistic, varied demo data
# ═════════════════════════════════════════════════════════════

USERS = [
    {"name": "Aarav Sharma",   "email": "aarav.sharma@example.com",   "phone": "+91-98765-43210"},
    {"name": "Priya Patel",    "email": "priya.patel@example.com",    "phone": "+91-87654-32109"},
    {"name": "Rohan Gupta",    "email": "rohan.gupta@example.com",    "phone": "+91-76543-21098"},
    {"name": "Ananya Singh",   "email": "ananya.singh@example.com",   "phone": "+91-65432-10987"},
    {"name": "Vikram Reddy",   "email": "vikram.reddy@example.com",   "phone": "+91-54321-09876"},
]

# Provider catalog: (provider, plan_name, monthly_cost, data_limit_gb, call_minutes_limit, features)
PLANS = [
    ("Jio",        "Gold 599",       599.0,  100.0, 500,  '{"5g": true, "hotspot": true}'),
    ("Jio",        "Silver 399",     399.0,   50.0, 300,  '{"5g": false, "hotspot": true}'),
    ("Jio",        "Platinum 999",   999.0,  200.0, None, '{"5g": true, "hotspot": true, "ott": "all"}'),
    ("Airtel",     "Infinity 499",   499.0,   75.0, 400,  '{"5g": true, "wifi_calling": true}'),
    ("Airtel",     "Smart 299",      299.0,   40.0, 200,  '{"5g": false}'),
    ("Airtel",     "Xstream 799",    799.0,  150.0, None, '{"5g": true, "ott": "airtel_xstream"}'),
    ("Vi",         "Hero 449",       449.0,   60.0, 350,  '{"weekend_data": true}'),
    ("Vi",         "Max 599",        599.0,   90.0, 500,  '{"weekend_data": true, "binge_all_night": true}'),
    ("BSNL",       "Bharat Fiber",   349.0,   30.0, None, '{"landline": true}'),
    ("Netflix",    "Premium",        649.0, None,   None, '{"screens": 4, "uhd": true}'),
    ("Spotify",    "Individual",     119.0, None,   None, '{"ad_free": true, "offline": true}'),
    ("AWS",        "Developer",     1499.0, None,   None, '{"ec2": true, "s3": true, "rds": true}'),
]

# ── Usage profile archetypes ─────────────────────────────────
# Each archetype defines a (data_mean, data_spread, calls_mean, calls_spread)
# relative to the plan's limits.  This creates realistic diversity.
USAGE_PROFILES = {
    "heavy":       (0.90, 0.15, 0.85, 0.20),   # near or over limits
    "moderate":    (0.55, 0.15, 0.50, 0.15),   # comfortable middle
    "light":       (0.25, 0.10, 0.20, 0.10),   # well under limits
    "spiky":       (0.60, 0.35, 0.40, 0.30),   # unpredictable swings
}


# ═════════════════════════════════════════════════════════════
#  GENERATORS
# ═════════════════════════════════════════════════════════════

def _generate_usage_records(
    user_id: int,
    provider: str,
    data_limit_gb: float,
    call_minutes_limit: int,
    monthly_cost: float,
    num_months: int,
    profile: str,
) -> list:
    """
    Generate `num_months` usage records for a subscription.

    Uses the usage profile archetype to create realistic variation
    around a central tendency, with slight month-over-month drift.
    """
    data_mean, data_spread, calls_mean, calls_spread = USAGE_PROFILES[profile]

    # Default limits for services without data/call caps (Netflix, Spotify etc.)
    effective_data_limit = data_limit_gb or 50.0
    effective_call_limit = call_minutes_limit or 0

    records = []
    base_date = datetime(2025, 7, 1, tzinfo=timezone.utc)

    for month_offset in range(num_months):
        period_start = base_date + timedelta(days=30 * month_offset)
        period_end = period_start + timedelta(days=29, hours=23, minutes=59, seconds=59)

        # Data usage: mean ± spread, with progressive drift
        drift = random.uniform(-0.05, 0.05) * month_offset
        data_ratio = max(0.01, data_mean + drift + random.gauss(0, data_spread))
        data_used = round(effective_data_limit * data_ratio, 2)

        # Call usage (skip for non-telecom providers)
        if effective_call_limit > 0:
            call_ratio = max(0, calls_mean + drift + random.gauss(0, calls_spread))
            calls_used = max(0, int(effective_call_limit * call_ratio))
        else:
            calls_used = 0

        # Billing amount: base cost + potential overage
        overage = 0.0
        if data_limit_gb and data_used > data_limit_gb:
            overage += round((data_used - data_limit_gb) * 10, 2)  # ₹10/GB overage
        if call_minutes_limit and calls_used > call_minutes_limit:
            overage += round((calls_used - call_minutes_limit) * 1.5, 2)  # ₹1.5/min overage
        billing = round(monthly_cost + overage, 2)

        records.append(UsageData(
            user_id=user_id,
            provider=provider,
            period_start=period_start,
            period_end=period_end,
            data_used_gb=data_used,
            call_minutes_used=calls_used,
            billing_amount=billing,
        ))

    return records


def _pick_plans_for_user(exclude_providers: set) -> list:
    """
    Select 1–3 random plans for a user, avoiding duplicate providers.
    """
    num_plans = random.choices([1, 2, 3], weights=[0.2, 0.5, 0.3])[0]
    available = [p for p in PLANS if p[0] not in exclude_providers]
    random.shuffle(available)

    chosen = []
    seen_providers = set()
    for plan in available:
        if plan[0] not in seen_providers:
            chosen.append(plan)
            seen_providers.add(plan[0])
        if len(chosen) >= num_plans:
            break

    return chosen


# ═════════════════════════════════════════════════════════════
#  MAIN SEEDER
# ═════════════════════════════════════════════════════════════

def seed(reset: bool = False):
    """
    Populate the database with demo data.

    Args:
        reset: If True, drop and recreate all tables first.
    """
    if reset:
        print("🔄  Dropping all tables...")
        Base.metadata.drop_all(bind=engine)
        print("🔄  Recreating tables...")
        Base.metadata.create_all(bind=engine)

    # Ensure tables exist even without reset
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        # ── Check for existing seed data ─────────────────────
        existing = db.query(User).filter(
            User.email.in_([u["email"] for u in USERS])
        ).count()
        if existing > 0 and not reset:
            print(
                f"⚠️  Found {existing} existing seed user(s). "
                f"Use --reset to start fresh, or seed will add on top."
            )

        profiles = list(USAGE_PROFILES.keys())
        total_subs = 0
        total_usage = 0

        print(f"\n{'='*60}")
        print(f"  🌱  SEEDING DATABASE")
        print(f"{'='*60}\n")

        for i, user_data in enumerate(USERS):
            # ── Create user ──────────────────────────────────
            # Add timestamp suffix to avoid email collisions on re-seed
            email = user_data["email"]
            if not reset:
                ts = datetime.now().strftime("%H%M%S")
                name_part = email.split("@")[0]
                email = f"{name_part}_{ts}_{i}@example.com"

            user = User(
                name=user_data["name"],
                email=email,
                phone=user_data["phone"],
            )
            db.add(user)
            db.flush()
            db.refresh(user)

            print(f"  👤  User {user.id}: {user.name} ({user.email})")

            # ── Create subscriptions ─────────────────────────
            plans = _pick_plans_for_user(exclude_providers=set())

            for provider, plan_name, cost, data_limit, call_limit, features in plans:
                sub = Subscription(
                    user_id=user.id,
                    provider=provider,
                    plan_name=plan_name,
                    monthly_cost=cost,
                    data_limit_gb=data_limit,
                    call_minutes_limit=call_limit,
                    features=features,
                    is_active=True,
                )
                db.add(sub)
                db.flush()
                db.refresh(sub)
                total_subs += 1

                # ── Create usage records ─────────────────────
                profile = random.choice(profiles)
                num_months = random.randint(3, 6)

                records = _generate_usage_records(
                    user_id=user.id,
                    provider=provider,
                    data_limit_gb=data_limit or 0,
                    call_minutes_limit=call_limit or 0,
                    monthly_cost=cost,
                    num_months=num_months,
                    profile=profile,
                )
                for rec in records:
                    db.add(rec)
                total_usage += len(records)

                print(
                    f"      📱  {provider} / {plan_name} "
                    f"(₹{cost}/mo, {num_months} months, profile={profile})"
                )

        db.commit()

        print(f"\n{'─'*60}")
        print(f"  ✅  Seeding complete!")
        print(f"      Users:         {len(USERS)}")
        print(f"      Subscriptions: {total_subs}")
        print(f"      Usage records: {total_usage}")
        print(f"{'='*60}\n")

    except Exception as e:
        db.rollback()
        print(f"\n  ❌  Seeding failed: {e}")
        raise
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Seed the Agentic Digital Twin database with demo data.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables before seeding (fresh start).",
    )
    args = parser.parse_args()
    seed(reset=args.reset)


if __name__ == "__main__":
    main()
