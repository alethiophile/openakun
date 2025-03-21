"""chat message thread id

Revision ID: d3930fd73fb2
Revises: 116e5a66d9ed
Create Date: 2025-03-21 14:27:04.289642

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3930fd73fb2'
down_revision = '116e5a66d9ed'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_messages', sa.Column('thread_id', sa.Integer(), nullable=True))
    op.create_foreign_key(op.f('fk_chat_messages_thread_id_chat_messages'), 'chat_messages', 'chat_messages', ['thread_id'], ['id'])

    op.execute("""
CREATE OR REPLACE FUNCTION validate_thread_id()
RETURNS trigger AS $$
BEGIN
    IF NEW.thread_id IS NOT NULL THEN
        IF (SELECT thread_id FROM chat_messages WHERE id = NEW.thread_id) IS NOT NULL THEN
            RAISE EXCEPTION 'Thread ID must reference a top-level message with NULL thread_id';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
    op.execute("""
CREATE TRIGGER check_thread_id_before_insert
BEFORE INSERT ON chat_messages
FOR EACH ROW EXECUTE FUNCTION validate_thread_id();
""")


def downgrade():
    op.execute("DROP TRIGGER check_thread_id_before_insert ON chat_messages")
    op.execute("DROP FUNCTION validate_thread_id")

    op.drop_constraint(op.f('fk_chat_messages_thread_id_chat_messages'), 'chat_messages', type_='foreignkey')
    op.drop_column('chat_messages', 'thread_id')
