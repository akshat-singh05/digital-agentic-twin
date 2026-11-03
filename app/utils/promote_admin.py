"""
Make an existing account an administrator:

    python -m app.utils.promote_admin you@example.com

Administrators can import the shared plan catalog and read every user's data
(they still cannot act for other users).  This is a command-line step on
purpose: there is no API for it, so nobody can promote themselves.
"""

import sys

from app.database import SessionLocal
from app.db_migrations import migrate_to_head
from app.services.base_service import transactional
from app.services.user_service import get_user_by_email


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m app.utils.promote_admin <email>")
        return 2
    migrate_to_head()
    with SessionLocal() as db:
        user = get_user_by_email(db, args[0].strip().lower())
        if user is None:
            print(f"No account with email {args[0]!r}. Register it first.")
            return 1
        if user.role == "admin":
            print(f"{user.email} is already an administrator.")
            return 0
        with transactional(db):
            user.role = "admin"
        print(f"{user.email} is now an administrator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
