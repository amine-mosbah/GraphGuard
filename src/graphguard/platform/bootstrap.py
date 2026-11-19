"""First-run provisioning — creating the initial administrator.

Role
----
A deployment with no accounts cannot be signed into, and the endpoint that creates accounts is
itself admin-only. Something has to break that circularity, and it must not be an unauthenticated
"create the first user" HTTP route: such a route is a permanent privilege-escalation hole if the
"is this the first user?" check is ever wrong, and it is reachable by anyone who finds the service
before the operator does.

Bootstrapping through the CLI instead means the capability requires shell access to the host, and
disappears from the attack surface entirely.

Inputs / outputs
----------------
Input: an email and password supplied by the operator.
Output: an ``admin``-role row in ``users``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from graphguard.platform.models import Role, User
from graphguard.platform.security import hash_password

MIN_PASSWORD_LENGTH = 12


class BootstrapError(Exception):
    """Raised when the initial administrator cannot be created."""


def create_admin(session: Session, email: str, password: str, full_name: str = "") -> User:
    """Create an administrator account.

    Raises
    ------
    BootstrapError
        If the password is too short or the email is already registered.

    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BootstrapError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    normalised = email.strip().lower()
    if session.scalar(select(User).where(User.email == normalised)) is not None:
        raise BootstrapError(f"an account already exists for {normalised}")

    user = User(
        email=normalised,
        full_name=full_name,
        hashed_password=hash_password(password),
        role=Role.ADMIN,
    )
    session.add(user)
    session.flush()
    return user


def user_count(session: Session) -> int:
    """Return how many accounts exist, so callers can detect a fresh deployment."""
    return int(session.scalar(select(func.count()).select_from(User)) or 0)
