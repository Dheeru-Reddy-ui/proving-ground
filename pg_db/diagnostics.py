"""Safe descriptions of database connection problems, for server logs only.

libpq's messages name the host, port and reason ("Network is unreachable", "password
authentication failed", "could not translate host name") but not the password. A malformed
connection string can make the driver quote parts of it, so anything that looks like
credentials is masked, and parse errors are replaced by a generic hint.
"""

from __future__ import annotations

import re

from sqlalchemy.engine import make_url

MAX_CHARS = 300
PARSE_HINT = "the connection string could not be parsed (check its format; URL-encode the password)"
_CREDENTIALS = re.compile(r"://[^@\s/]*@")
_PASSWORD = re.compile(r"password\s*=\s*\S+", re.IGNORECASE)
_AT_TOKEN = re.compile(r"'[^']*@[^']*'|\"[^\"]*@[^\"]*\"|\S*@\S*")
AT_HINT = " (an '@' inside the host usually means the password has an unencoded '@')"
_PARSE = ("invalid dsn", "invalid connection option", 'missing "="', "conninfo", "invalid uri")


def db_reason(exc: BaseException) -> str:
    """One masked line saying why the database could not be used."""
    orig = getattr(exc, "orig", None) or exc
    text = " ".join(str(orig).split())
    if not text:
        return type(orig).__name__
    if any(marker in text.lower() for marker in _PARSE):
        return PARSE_HINT
    text = _PASSWORD.sub("password=***", _CREDENTIALS.sub("://***@", text))
    if "@" in text:  # e.g. a host read as "<end of password>@host": never echo either part
        text = _AT_TOKEN.sub("<redacted>", text) + AT_HINT
    return text[:MAX_CHARS]


def db_target(database_url: str) -> dict[str, object]:
    """Where a connection string points (host, port, database, pooler or not), no secrets."""
    try:
        url = make_url(database_url)
    except Exception:  # an unparseable URL: say so without echoing it
        return {"parse_error": True}
    host = url.host or ""
    if "@" in host:  # the password's tail was read as part of the host: never log it
        return {"host": "<redacted>", "host_has_at_sign": True, "password_set": bool(url.password)}
    return {
        "host": host,
        "port": url.port,
        "database": url.database,
        "supabase_pooler": host.endswith("pooler.supabase.com"),
        "supabase_direct": host.startswith("db.") and host.endswith(".supabase.co"),
        "password_set": bool(url.password),
    }
