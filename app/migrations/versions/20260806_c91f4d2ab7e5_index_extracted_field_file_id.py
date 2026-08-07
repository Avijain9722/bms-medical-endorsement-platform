"""index extracted_fields.file_id

The one foreign key the earlier "index every foreign key predicate" pass left
out, and the busiest of them: every document's extracted fields are looked up by
it whenever a case is reprocessed. Without it that lookup is a full scan of the
whole extracted_fields table, which grows with every document ever uploaded.

Revision ID: c91f4d2ab7e5
Revises: 58dc647b3fcb
Created: 2026-08-06 11:55:00.000000
"""
from __future__ import annotations

from alembic import op


revision = 'c91f4d2ab7e5'
down_revision = '58dc647b3fcb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('extracted_fields', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_extracted_fields_file_id'), ['file_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('extracted_fields', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_extracted_fields_file_id'))
