"""
Domain exceptions shared by the service layer.

Services raise plain ``ValueError`` for missing records (→ 404).  The
subclasses below let the route layer pick a more precise status code.
"""


class ConflictError(ValueError):
    """The request is valid but cannot be carried out in the current state (→ 409)."""
