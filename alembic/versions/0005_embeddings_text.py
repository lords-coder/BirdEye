"""students.embeddings: JSON -> TEXT (align schema with the ORM model)

Revision ID: 0005_embeddings_text
Revises: 0004_users
Create Date: 2026-10-14

0001_initial created ``students.embeddings`` as JSON (server default '[]'),
but the ORM model (app/models.py) declares it as String (default '').  The
column stores a **Fernet ciphertext string**, never a JSON document:

* On PostgreSQL every enrollment insert would be coerced through ``json``
  and rejected - a Fernet token is not valid JSON (or, for defaults, ``''``
  is not valid JSON either).
* SQLite only appeared to work because it does not enforce column types,
  which is why the create_all-based test suite never saw the drift.

This migration converts the column to TEXT and carries the server default
in line with the model (``''``):

* PostgreSQL: ``USING embeddings::text`` - existing rows can only contain
  valid JSON (that is all a ``json`` column ever accepted), typically the
  default ``[]``.  ``decrypt_embeddings('[]')`` fails soft and the ORM
  reports "no embeddings", matching the empty-string default's behaviour.
* SQLite: batch mode rebuilds the table and copies values verbatim.

Compatibility: additive at the application level - the ORM already treats
this column as String, so no application code changes are required and old
readers/writers are unaffected on SQLite.  On PostgreSQL this migration is
what makes enrollment work at all.

Downgrade: on SQLite values round-trip byte-for-byte.  On PostgreSQL the
reverse cast uses ``to_json(embeddings)`` so the conversion always succeeds;
values then become JSON-encoded strings (valid JSON, and the only thing the
old ``json`` column could hold), so ciphertexts need re-encoding if the old
String-on-JSON behaviour is ever restored.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_embeddings_text"
down_revision: Union[str, None] = "0004_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE students "
            "ALTER COLUMN embeddings TYPE TEXT "
            "USING embeddings::text"
        )
        op.execute("ALTER TABLE students ALTER COLUMN embeddings SET DEFAULT ''")
    else:
        # sa.String() (unlengthened), NOT sa.Text(): SQLite renders String
        # as VARCHAR, exactly like the ORM model, so `alembic check` stays
        # clean (TEXT vs VARCHAR is otherwise reported as type drift).
        with op.batch_alter_table("students") as batch_op:
            batch_op.alter_column(
                "embeddings",
                existing_type=sa.JSON(),
                type_=sa.String(),
                existing_nullable=False,
                server_default="",
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # to_json() always succeeds (wraps any text); plain ::json would
        # raise on ciphertext values.
        op.execute(
            "ALTER TABLE students "
            "ALTER COLUMN embeddings TYPE JSON "
            "USING to_json(embeddings)"
        )
        op.execute("ALTER TABLE students ALTER COLUMN embeddings SET DEFAULT '[]'")
    else:
        with op.batch_alter_table("students") as batch_op:
            batch_op.alter_column(
                "embeddings",
                existing_type=sa.String(),
                type_=sa.JSON(),
                existing_nullable=False,
                server_default="[]",
            )
