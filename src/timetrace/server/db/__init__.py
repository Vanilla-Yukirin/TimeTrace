"""Database tier: persistence implementations.

Today only SqliteDatabase exists; PostgresDatabase will land at P5. Until then
`Database` is exported as an alias for SqliteDatabase so callers don't need to
choose between Protocol-typed annotation and concrete construction. As soon
as a second implementation arrives, this alias becomes a `typing.Protocol`
and callers split into "annotate as Database" vs "construct as SqliteDatabase".
"""

from timetrace.server.db.sqlite import SqliteDatabase

Database = SqliteDatabase

__all__ = ["Database", "SqliteDatabase"]
