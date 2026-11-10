"""动态分类层级：spaces.levels + categories 邻接表；intents.l1/l2 → category_id

存量数据按「大类 / 问题桶 / 意图」三层迁移：taxonomy_l1 → depth 1，taxonomy_l2 → depth 2，
意图挂到对应 depth 2 节点；意图引用了不存在的 L1/L2 时自动补建节点，保证一个不丢。

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('spaces', sa.Column('levels', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                                      server_default=sa.text("""'["大类", "问题桶", "意图"]'::jsonb""")))
    op.create_table(
        'categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.String(length=32), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('old_l2_id', sa.Integer(), nullable=True),  # 迁移用临时列，结尾删除
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['categories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_categories_space_parent', 'categories', ['space_id', 'parent_id'])
    op.create_index('uq_categories_code', 'categories', ['space_id', 'code'], unique=True,
                    postgresql_where=sa.text('code IS NOT NULL'))
    op.create_index('uq_categories_sibling_name', 'categories',
                    ['space_id', sa.text('coalesce(parent_id, 0)'), 'name'], unique=True)
    op.add_column('intents', sa.Column('category_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_intents_category', 'intents', 'categories', ['category_id'], ['id'], ondelete='RESTRICT')

    # 1) 意图引用但分类表里没有的 L1 / L2，先补齐
    op.execute("""
        INSERT INTO taxonomy_l1 (space_id, code, name, position)
        SELECT DISTINCT i.space_id, i.l1, i.l1, 999 FROM intents i
        WHERE NOT EXISTS (SELECT 1 FROM taxonomy_l1 a WHERE a.space_id = i.space_id AND a.code = i.l1)
    """)
    op.execute("""
        INSERT INTO taxonomy_l2 (l1_id, name, position)
        SELECT DISTINCT a.id, i.l2, 999 FROM intents i JOIN taxonomy_l1 a ON a.space_id = i.space_id AND a.code = i.l1
        WHERE NOT EXISTS (SELECT 1 FROM taxonomy_l2 b WHERE b.l1_id = a.id AND b.name = i.l2)
    """)
    # 2) L1 → depth 1（保留编码），L2 → depth 2
    op.execute("""
        INSERT INTO categories (space_id, parent_id, depth, code, name, position)
        SELECT space_id, NULL, 1, code, name, position FROM taxonomy_l1 ORDER BY id
    """)
    op.execute("""
        INSERT INTO categories (space_id, parent_id, depth, code, name, position, old_l2_id)
        SELECT a.space_id, c.id, 2, NULL, b.name, b.position, b.id
        FROM taxonomy_l2 b JOIN taxonomy_l1 a ON a.id = b.l1_id
        JOIN categories c ON c.space_id = a.space_id AND c.depth = 1 AND c.code = a.code
        ORDER BY b.id
    """)
    # 3) 意图挂到 depth 2
    op.execute("""
        UPDATE intents i SET category_id = c.id
        FROM taxonomy_l1 a JOIN taxonomy_l2 b ON b.l1_id = a.id JOIN categories c ON c.old_l2_id = b.id
        WHERE a.space_id = i.space_id AND a.code = i.l1 AND b.name = i.l2
    """)
    op.drop_column('categories', 'old_l2_id')
    op.drop_index('ix_intents_space_l1', table_name='intents')
    op.drop_column('intents', 'l1')
    op.drop_column('intents', 'l2')
    op.create_index('ix_intents_space_category', 'intents', ['space_id', 'category_id'])
    op.drop_index(op.f('ix_taxonomy_l2_l1_id'), table_name='taxonomy_l2')
    op.drop_table('taxonomy_l2')
    op.drop_index(op.f('ix_taxonomy_l1_space_id'), table_name='taxonomy_l1')
    op.drop_table('taxonomy_l1')


def downgrade() -> None:
    """仅支持「2 层分类」的空间回退（取路径第 1 层为 L1、最末层为 L2）。"""
    bind = op.get_bind()
    bad = bind.execute(sa.text("SELECT id FROM spaces WHERE jsonb_array_length(levels) <> 3")).scalars().all()
    if bad:
        raise RuntimeError(f"以下空间的分类层数不是 2，无法回退到 L1/L2 结构：{bad}")
    op.create_table(
        'taxonomy_l1',
        sa.Column('id', sa.Integer(), nullable=False), sa.Column('space_id', sa.String(length=32), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False), sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('space_id', 'code'), sa.UniqueConstraint('space_id', 'name'))
    op.create_index(op.f('ix_taxonomy_l1_space_id'), 'taxonomy_l1', ['space_id'])
    op.create_table(
        'taxonomy_l2',
        sa.Column('id', sa.Integer(), nullable=False), sa.Column('l1_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False), sa.Column('position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['l1_id'], ['taxonomy_l1.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('l1_id', 'name'))
    op.create_index(op.f('ix_taxonomy_l2_l1_id'), 'taxonomy_l2', ['l1_id'])
    op.add_column('intents', sa.Column('l1', sa.String(length=32), nullable=True))
    op.add_column('intents', sa.Column('l2', sa.String(length=100), nullable=True))
    op.execute("""INSERT INTO taxonomy_l1 (space_id, code, name, position)
                  SELECT space_id, coalesce(code, 'C' || id), name, position FROM categories WHERE depth = 1""")
    op.execute("""INSERT INTO taxonomy_l2 (l1_id, name, position)
                  SELECT a.id, c.name, c.position FROM categories c JOIN categories p ON p.id = c.parent_id
                  JOIN taxonomy_l1 a ON a.space_id = p.space_id AND a.name = p.name WHERE c.depth = 2""")
    op.execute("""UPDATE intents i SET l1 = coalesce(p.code, 'C' || p.id), l2 = c.name
                  FROM categories c JOIN categories p ON p.id = c.parent_id WHERE c.id = i.category_id""")
    op.alter_column('intents', 'l1', nullable=False)
    op.alter_column('intents', 'l2', nullable=False)
    op.drop_index('ix_intents_space_category', table_name='intents')
    op.drop_constraint('fk_intents_category', 'intents', type_='foreignkey')
    op.drop_column('intents', 'category_id')
    op.create_index('ix_intents_space_l1', 'intents', ['space_id', 'l1'])
    op.drop_table('categories')
    op.drop_column('spaces', 'levels')
