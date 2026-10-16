"""Add source identities without rewriting historical journals or evidence.

Version 1 preserves each unambiguous legacy public key on installation. New
sources receive sequence-backed public keys, independent of provider strings.
Journal associations are separate so closed journals need no UPDATE/backfill.
"""
from sqlalchemy import text
from pacioli.extensions import db


def install_source_identities():
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout='10s'")
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
        connection.exec_driver_sql('LOCK TABLE ofx.stmttrn, bookkeeping.journal_entries IN SHARE ROW EXCLUSIVE MODE')
        connection.exec_driver_sql('''CREATE TABLE IF NOT EXISTS bookkeeping.ofx_source_identities (
            source_id bigserial PRIMARY KEY,
            public_id text NOT NULL UNIQUE,
            acctfrom_id integer NOT NULL,
            fitid varchar NOT NULL,
            UNIQUE (acctfrom_id, fitid),
            FOREIGN KEY (acctfrom_id, fitid) REFERENCES ofx.stmttrn(acctfrom_id, fitid) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS bookkeeping.ofx_journal_sources (
            journal_entry_id integer PRIMARY KEY REFERENCES bookkeeping.journal_entries(id) ON DELETE CASCADE,
            source_id bigint NOT NULL REFERENCES bookkeeping.ofx_source_identities(source_id)
        );
        CREATE INDEX IF NOT EXISTS ofx_journal_sources_source ON bookkeeping.ofx_journal_sources(source_id);
        CREATE TABLE IF NOT EXISTS admin.integrity_migrations (
            version text PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now()
        )''')
        ambiguous = connection.exec_driver_sql('''WITH collisions AS (
            SELECT concat(fitid,acctfrom_id) AS legacy FROM ofx.stmttrn
            GROUP BY concat(fitid,acctfrom_id) HAVING count(*) > 1)
            SELECT collisions.legacy FROM ofx.stmttrn s
            JOIN collisions ON collisions.legacy=concat(s.fitid,s.acctfrom_id)
            WHERE NOT EXISTS (SELECT 1 FROM bookkeeping.ofx_source_identities i
                              WHERE i.acctfrom_id=s.acctfrom_id AND i.fitid=s.fitid)
            LIMIT 10''').fetchall()
        if ambiguous:
            raise ValueError('Ambiguous legacy OFX identities require review; migration was rolled back')
        connection.exec_driver_sql('''INSERT INTO bookkeeping.ofx_source_identities(public_id,acctfrom_id,fitid)
            SELECT concat(s.fitid,s.acctfrom_id),s.acctfrom_id,s.fitid FROM ofx.stmttrn s
            WHERE NOT EXISTS (SELECT 1 FROM bookkeeping.ofx_source_identities i
                              WHERE i.acctfrom_id=s.acctfrom_id AND i.fitid=s.fitid)''')
        orphaned = connection.exec_driver_sql('''SELECT j.id FROM bookkeeping.journal_entries j
            LEFT JOIN bookkeeping.ofx_source_identities s ON s.public_id=j.transaction_id
            WHERE j.transaction_source='ofx' AND s.source_id IS NULL ORDER BY j.id LIMIT 10''').fetchall()
        if orphaned:
            raise ValueError('OFX journals have missing source identities: {}'.format([r[0] for r in orphaned]))
        wrong = connection.exec_driver_sql('''SELECT j.id FROM bookkeeping.ofx_journal_sources l
            JOIN bookkeeping.journal_entries j ON j.id=l.journal_entry_id
            JOIN bookkeeping.ofx_source_identities s ON s.source_id=l.source_id
            WHERE j.transaction_source IS DISTINCT FROM 'ofx' OR j.transaction_id IS DISTINCT FROM s.public_id
            LIMIT 10''').fetchall()
        if wrong:
            raise ValueError('OFX journal associations do not match their historical references')
        connection.exec_driver_sql('''INSERT INTO bookkeeping.ofx_journal_sources(journal_entry_id,source_id)
            SELECT j.id,s.source_id FROM bookkeeping.journal_entries j
            JOIN bookkeeping.ofx_source_identities s ON s.public_id=j.transaction_id
            WHERE j.transaction_source='ofx' ON CONFLICT (journal_entry_id) DO NOTHING''')
        connection.exec_driver_sql('''CREATE OR REPLACE FUNCTION bookkeeping.lock_source_activity()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
            IF TG_OP='UPDATE' AND (NEW.acctfrom_id,NEW.fitid) IS DISTINCT FROM (OLD.acctfrom_id,OLD.fitid) THEN
                RAISE EXCEPTION 'Provider source identity cannot be changed';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW; END $$;
            DROP TRIGGER IF EXISTS source_activity_lock ON ofx.stmttrn;
            CREATE TRIGGER source_activity_lock BEFORE INSERT OR UPDATE OR DELETE ON ofx.stmttrn
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.lock_source_activity();
            CREATE OR REPLACE FUNCTION bookkeeping.lock_amazon_source_activity()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
                IF TG_OP='DELETE' THEN RETURN OLD; END IF;
                RETURN NEW; END $$;
            DROP TRIGGER IF EXISTS source_activity_lock ON amazon.items;
            CREATE TRIGGER source_activity_lock BEFORE INSERT OR UPDATE OR DELETE ON amazon.items
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.lock_amazon_source_activity();
            CREATE OR REPLACE FUNCTION bookkeeping.register_ofx_source()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE identifier bigint; key text;
            BEGIN
                LOOP
                    identifier := nextval('bookkeeping.ofx_source_identities_source_id_seq');
                    key := 'ofx:v1:' || identifier::text;
                    EXIT WHEN NOT EXISTS (SELECT 1 FROM bookkeeping.ofx_source_identities WHERE public_id=key);
                END LOOP;
                INSERT INTO bookkeeping.ofx_source_identities(source_id,public_id,acctfrom_id,fitid)
                VALUES(identifier,key,NEW.acctfrom_id,NEW.fitid);
                RETURN NEW;
            END $$;
            DROP TRIGGER IF EXISTS register_ofx_source ON ofx.stmttrn;
            CREATE TRIGGER register_ofx_source AFTER INSERT ON ofx.stmttrn
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.register_ofx_source();
            CREATE OR REPLACE FUNCTION bookkeeping.preserve_ofx_identity()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Source identity cannot be changed'; END $$;
            DROP TRIGGER IF EXISTS ofx_identity_immutable ON bookkeeping.ofx_source_identities;
            CREATE TRIGGER ofx_identity_immutable BEFORE UPDATE ON bookkeeping.ofx_source_identities
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.preserve_ofx_identity();
            CREATE OR REPLACE FUNCTION bookkeeping.link_ofx_journal()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE identifier bigint;
            BEGIN
                IF NEW.transaction_source='ofx' THEN
                    SELECT source_id INTO identifier FROM bookkeeping.ofx_source_identities
                    WHERE public_id=NEW.transaction_id;
                    IF identifier IS NULL THEN
                        RAISE EXCEPTION 'OFX journal references a missing source' USING ERRCODE='23503';
                    END IF;
                    INSERT INTO bookkeeping.ofx_journal_sources(journal_entry_id,source_id)
                    VALUES(NEW.id,identifier) ON CONFLICT(journal_entry_id)
                    DO UPDATE SET source_id=excluded.source_id;
                ELSE
                    DELETE FROM bookkeeping.ofx_journal_sources WHERE journal_entry_id=NEW.id;
                END IF;
                RETURN NULL;
            END $$;
            DROP TRIGGER IF EXISTS journal_source_link ON bookkeeping.journal_entries;
            CREATE TRIGGER journal_source_link AFTER INSERT OR UPDATE ON bookkeeping.journal_entries
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.link_ofx_journal();
            CREATE OR REPLACE FUNCTION bookkeeping.validate_ofx_journal_link()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM bookkeeping.journal_entries j
                    JOIN bookkeeping.ofx_source_identities s ON s.public_id=j.transaction_id
                    WHERE j.id=NEW.journal_entry_id AND j.transaction_source='ofx' AND s.source_id=NEW.source_id) THEN
                    RAISE EXCEPTION 'Source association does not match journal reference' USING ERRCODE='23503';
                END IF;
                RETURN NEW; END $$;
            DROP TRIGGER IF EXISTS validate_ofx_journal_link ON bookkeeping.ofx_journal_sources;
            CREATE TRIGGER validate_ofx_journal_link BEFORE INSERT OR UPDATE ON bookkeeping.ofx_journal_sources
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.validate_ofx_journal_link();
            INSERT INTO admin.integrity_migrations(version) VALUES('ofx-source-identity-v1')
            ON CONFLICT DO NOTHING''')
