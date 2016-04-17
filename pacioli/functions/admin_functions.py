from sqlalchemy.exc import ProgrammingError

from pacioli import db


def create_mappings_views():
    try:
        db.engine.execute('DROP VIEW admin.mapping_overlaps;')
    except ProgrammingError:
        pass
    db.engine.execute("""
    CREATE VIEW admin.mapping_overlaps
    AS SELECT mappings_table_1.id AS mapping_id_1,
              mappings_table_1.keyword AS mapping_keyword_1,
              mappings_table_2.id AS mapping_id_2,
              mappings_table_2.keyword AS mapping_keyword_2,
              mappings_table_1.source AS source
      FROM admin.mappings mappings_table_1
      JOIN admin.mappings mappings_table_2 on mappings_table_2.keyword  LIKE '%%' || mappings_table_1.keyword || '%%'
                                          AND mappings_table_1.source = mappings_table_2.source
      WHERE mappings_table_1.keyword != mappings_table_2.keyword
      ORDER BY mappings_table_1.keyword;
    """)
