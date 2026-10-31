"""
Consent Scopes — the permissions a user can grant the digital twin.

Pure definitions only: no database access, no framework imports.
A scope that was never granted is denied.
"""

from typing import Dict

CONSENT_SCOPES: Dict[str, str] = {
    "usage_analysis":
        "Analyze my usage history to find savings opportunities.",
    "share_usage_with_provider":
        "Share privacy-protected (noised) usage summaries with providers during negotiation.",
    "negotiate_on_behalf":
        "Negotiate prices with providers on my behalf.",
    "execute_plan_changes":
        "Apply plan or price changes I have approved to my subscriptions.",
}


def is_known_scope(scope: str) -> bool:
    """True when ``scope`` is one of CONSENT_SCOPES."""
    return scope in CONSENT_SCOPES
