"""
Catalog Service — Read access to the plan catalog.

The catalog lists plans providers offer.  It is the only source of
alternative plans; today it holds demo data seeded by
app/utils/seed_data.py (``source="demo_seed"``).
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import PlanCatalog


def list_plans(
    db: Session,
    provider: Optional[str] = None,
    category: Optional[str] = None,
    include_unavailable: bool = False,
) -> List[PlanCatalog]:
    """Return catalog plans, cheapest first, optionally filtered."""
    query = db.query(PlanCatalog)
    if provider:
        query = query.filter(PlanCatalog.provider == provider)
    if category:
        query = query.filter(PlanCatalog.category == category)
    if not include_unavailable:
        query = query.filter(PlanCatalog.is_available == True)
    return query.order_by(PlanCatalog.monthly_cost, PlanCatalog.id).all()
