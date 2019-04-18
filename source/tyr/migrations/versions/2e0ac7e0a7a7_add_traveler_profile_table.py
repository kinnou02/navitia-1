"""Add traveler profile table

Revision ID: 2e0ac7e0a7a7
Revises: 1414da92b3ca
Create Date: 2015-08-18 17:44:32.460413

"""

# revision identifiers, used by Alembic.
revision = '2e0ac7e0a7a7'
down_revision = '1414da92b3ca'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, ARRAY


def upgrade():
    # Managing fallback_mode enum as tested in sqlalchemy's repo:
    # https://github.com/sqlalchemy/alembic/blob/7257a5b306385318f19d5d16d2196371bb637d66/tests/test_postgresql.py#L328-L333
    fallback_mode = ENUM('walking', 'car', 'bss', 'bike', name='fallback_mode', create_type=False)
    fallback_mode.create(bind=op.get_bind(), checkfirst=False)

    ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        'traveler_profile',
        sa.Column('coverage_id', sa.Integer(), nullable=False),
        sa.Column(
            'traveler_type',
            sa.Enum(
                'standard',
                'slow_walker',
                'fast_walker',
                'luggage',
                'wheelchair',
                'cyclist',
                'motorist',
                name='traveler_type',
            ),
            nullable=False,
        ),
        sa.Column('walking_speed', sa.Float(), nullable=False),
        sa.Column('bike_speed', sa.Float(), nullable=False),
        sa.Column('bss_speed', sa.Float(), nullable=False),
        sa.Column('car_speed', sa.Float(), nullable=False),
        sa.Column('wheelchair', sa.Boolean(), nullable=False),
        sa.Column('max_walking_duration_to_pt', sa.Integer(), nullable=False),
        sa.Column('max_bike_duration_to_pt', sa.Integer(), nullable=False),
        sa.Column('max_bss_duration_to_pt', sa.Integer(), nullable=False),
        sa.Column('max_car_duration_to_pt', sa.Integer(), nullable=False),
        sa.Column('first_section_mode', ARRAY(fallback_mode), nullable=False),
        sa.Column('last_section_mode', ARRAY(fallback_mode), nullable=False),
        sa.ForeignKeyConstraint(['coverage_id'], ['instance.id']),
        sa.PrimaryKeyConstraint('coverage_id', 'traveler_type'),
    )
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('traveler_profile')
    ### end Alembic commands ###

    # https://github.com/sqlalchemy/alembic/blob/7257a5b306385318f19d5d16d2196371bb637d66/tests/test_postgresql.py#L336-L338
    sa.Enum(name='fallback_mode').drop(op.get_bind(), checkfirst=False)
    sa.Enum(name='traveler_type').drop(op.get_bind(), checkfirst=False)
