"""Database problems are described for the logs without leaking credentials."""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

from pg_db.diagnostics import db_reason, db_target


def operational(message: str) -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception(message))


def test_the_reason_keeps_host_and_cause() -> None:
    exc = operational(
        'connection to server at "db.abc.supabase.co" (2406:da18::1), port 5432 failed: '
        "Network is unreachable\n\tIs the server running on that host?"
    )
    reason = db_reason(exc)
    assert reason.startswith('connection to server at "db.abc.supabase.co"')
    assert "Network is unreachable" in reason
    assert "\n" not in reason


def test_credentials_are_masked() -> None:
    assert "s3cret" not in db_reason(operational("failed for postgresql://u:s3cret@h:5432/db"))
    assert "s3cret" not in db_reason(operational("password=s3cret host=h"))
    parse = db_reason(operational('invalid dsn: missing "=" after "postgresql://u:s3cret@h"'))
    assert "s3cret" not in parse
    assert "URL-encode the password" in parse


def test_the_target_names_the_host_but_not_the_password() -> None:
    pooler = db_target(
        "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    )
    assert pooler == {
        "host": "aws-0-ap-south-1.pooler.supabase.com",
        "port": 5432,
        "database": "postgres",
        "supabase_pooler": True,
        "supabase_direct": False,
        "password_set": True,
    }
    direct = db_target("postgresql://postgres:pw@db.ref.supabase.co:5432/postgres")
    assert direct["supabase_direct"] is True
    assert "pw" not in str(direct.values())
    assert db_target("::not a url::") == {"parse_error": True}
