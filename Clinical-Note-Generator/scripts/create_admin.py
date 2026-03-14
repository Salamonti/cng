# scripts/create_admin.py
import getpass
import sys
import uuid
from pathlib import Path

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.core.db import engine, init_db  # type: ignore  # noqa: E402
from server.core.security import hash_password  # type: ignore  # noqa: E402
from server.models.user import User  # type: ignore  # noqa: E402


def prompt(prompt_text: str, default: str = "") -> str:
    value = input(f"{prompt_text} [{default}]: ").strip()
    return value or default


def create_admin_user(email: str, password: str) -> None:
    with Session(engine) as session:
        # Use SQLModel 0.14+ select() syntax instead of legacy query()
        statement = select(User).where(User.email == email)
        existing = session.exec(statement).first()
        
        if existing:
            print(f"User {email} already exists. Skipping creation.")
            return
        
        admin = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=hash_password(password),
            is_active=True,
            is_admin=True,
            is_approved=True,
        )
        session.add(admin)
        session.commit()
        print(f"Admin user {email} created.")


def main() -> None:
    init_db()
    email = prompt("Admin email", "admin@example.com")
    password = getpass.getpass("Admin password [leave blank to auto-generate strong temp]: ").strip()
    if not password:
        import secrets

        password = secrets.token_urlsafe(16)
        print(f"Generated password: {password}")
    create_admin_user(email, password)
    print("Done. Remember to rotate the password after first login.")


if __name__ == "__main__":
    main()