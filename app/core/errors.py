"""
Domain exceptions shared by the service layer.

Services raise plain ``ValueError`` for missing records (→ 404).  The
subclasses below let the route layer pick a more precise status code.
"""


class ConflictError(ValueError):
    """The request is valid but cannot be carried out in the current state (→ 409)."""


class ConsentRequiredError(ValueError):
    """The user has not granted the consent scope an agent needs (→ 403)."""

    def __init__(self, scope: str, agent: str):
        self.scope = scope
        self.agent = agent
        super().__init__(
            f"Consent '{scope}' is required for {agent}. "
            f"Grant it with PUT /api/users/{{user_id}}/consents/{scope}."
        )
