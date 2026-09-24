import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Railway emits postgres://, which SQLAlchemy 2.x no longer accepts.
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    return url


# pool_pre_ping replaces connections Railway has dropped instead of failing the request;
# connect_timeout makes an unreachable database fail fast rather than hang the voice call.
engine = create_engine(
    _database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 5}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # close() rolls back anything uncommitted, so a failed write leaves no partial state.
        db.close()


def create_tables() -> None:
    # Models must be imported before this runs so they are registered on Base.metadata.
    Base.metadata.create_all(bind=engine)
    upgrade_existing_tables()


def upgrade_existing_tables() -> None:
    """Apply schema changes made after the tables were first created.

    create_all() never alters an existing table and there is no migrations framework, so
    each later change is an idempotent statement here, safe to run on every start:

    - patients.reason_for_visit: a column added after launch.
    - appointments.cancelled_at: cancelling keeps the row, so "one booking per time" must
      only count live bookings. The plain unique constraint on starts_at is swapped for a
      partial unique index that ignores cancelled rows.
    """
    statements = (
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS reason_for_visit VARCHAR(200)",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ",
        "ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_starts_at_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_live_starts_at "
        "ON appointments (starts_at) WHERE cancelled_at IS NULL",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
