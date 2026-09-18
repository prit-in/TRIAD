"""Seed the users the platform needs before anyone can log in.

    python scripts/seed_demo.py [--password ...] [--force]

There is deliberately no "create the first admin" endpoint: an unauthenticated route that
mints an administrator is the kind of convenience that survives into production. The first
accounts are created here, by someone with shell access to the deployment.

Passwords come from `TRIAD_SEED_PASSWORD` or `--password`. If neither is given a random one is
generated and printed **once**, to standard output only - it is never written to a file, never
logged, and never stored anywhere but as an argon2 hash.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.app.security import hash_password
from triad_core import audit
from triad_core.db import session_scope
from triad_core.models import User

# One account per role in PRD section 9, so every role check can actually be exercised.
SEED_USERS = [
    ("admin@triad.test", "Platform Admin", "admin"),
    ("engineer@triad.test", "Data Engineer", "data_engineer"),
    ("scientist@triad.test", "Data Scientist", "data_scientist"),
    ("ml@triad.test", "ML Engineer", "ml_engineer"),
    ("approver@triad.test", "Second ML Engineer", "ml_engineer"),
    ("analyst@triad.test", "Business Analyst", "business_analyst"),
    ("viewer@triad.test", "Viewer", "viewer"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the initial TRIAD accounts.")
    parser.add_argument("--password", default=os.environ.get("TRIAD_SEED_PASSWORD"),
                        help="password for every seeded account; generated when omitted")
    parser.add_argument("--force", action="store_true",
                        help="reset the password of accounts that already exist")
    args = parser.parse_args()

    password = args.password or secrets.token_urlsafe(18)
    generated = args.password is None

    created, reset, skipped = [], [], []
    with session_scope() as session:
        for email, display_name, role in SEED_USERS:
            existing = session.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()

            if existing is None:
                session.add(User(email=email, display_name=display_name, role=role,
                                 password_hash=hash_password(password), is_active=True))
                created.append(email)
            elif args.force:
                existing.password_hash = hash_password(password)
                existing.is_active = True
                reset.append(email)
            else:
                skipped.append(email)

        session.flush()
        audit.record(
            session,
            actor_type="system",
            actor_id="seed_demo",
            action="users.seeded",
            resource_type="user",
            resource_id="seed",
            outcome="created" if created else "unchanged",
            # The audit entry records who exists, never the secret.
            details={"created": created, "reset": reset, "skipped": skipped},
        )

    for email in created:
        print(f"created  {email}")
    for email in reset:
        print(f"reset    {email}")
    for email in skipped:
        print(f"exists   {email}  (use --force to reset the password)")

    if created or reset:
        if generated:
            print(f"\npassword: {password}")
            print("This is shown once and is not stored anywhere but as an argon2 hash.")
        else:
            print("\npassword: the value you supplied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
