from sqlalchemy.exc import ProgrammingError

from pacioli import db


def create_mappings_views():
    try:
        db.engine.execute('DROP VIEW admin.mapping_overlaps;')
    except ProgrammingError:
        pass
    db.engine.execute("""
    CREATE VIEW admin.mapping_overlaps
    AS SELECT DISTINCT concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
              mappings_table_1.id AS mapping_id_1,
              mappings_table_1.keyword AS mapping_keyword_1,
              mappings_table_2.id AS mapping_id_2,
              mappings_table_2.keyword AS mapping_keyword_2,
              mappings_table_2.source AS source
      FROM ofx.stmttrn
      JOIN admin.mappings mappings_table_1 on lower(concat(ofx.stmttrn.name, ofx.stmttrn.memo)) LIKE '%%' || lower(mappings_table_1.keyword) || '%%'
                                          AND mappings_table_1.source = 'ofx'
      JOIN admin.mappings mappings_table_2 on lower(concat(ofx.stmttrn.name, ofx.stmttrn.memo))  LIKE '%%' || lower(mappings_table_2.keyword) || '%%'
                                          AND mappings_table_1.keyword != mappings_table_2.keyword
                                          AND mappings_table_2.source = 'ofx'
      ORDER BY description;
    """)

    # Todo: have view updates propagate to underlying physical tables
    # try:
    #     db.engine.execute('DROP RULE admin.ofx_mapping_overlaps_keyword_1_update_rule')
    # except ProgrammingError:
    #     pass
    # db.engine.execute("""
    # CREATE RULE ofx_mapping_overlaps_keyword_1_update_rule AS
    # ON UPDATE TO admin.ofx_mapping_overlaps WHERE NEW.mapping_keyword_1 <> OLD.mapping_keyword_1
    #     DO INSTEAD UPDATE admin.mappings SET keyword = NEW.mapping_keyword_1 WHERE id = NEW.mapping_id_1;
    # """)
    #
    # try:
    #     db.engine.execute('DROP RULE admin.ofx_mapping_overlaps_keyword_2_update_rule')
    # except ProgrammingError:
    #     pass
    # db.engine.execute("""
    # CREATE RULE ofx_mapping_overlaps_keyword_2_update_rule AS
    # ON UPDATE TO admin.ofx_mapping_overlaps WHERE NEW.mapping_keyword_2 <> OLD.mapping_keyword_2
    #     DO INSTEAD UPDATE admin.mappings SET keyword = NEW.mapping_keyword_2 WHERE id = NEW.mapping_id_2;
    # """)

