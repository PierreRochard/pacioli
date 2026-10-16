-- Pacioli schema at Alembic revision 0001, frozen on 2026-10-16.
--
-- Generated with `pg_dump --schema-only --no-owner --no-privileges` from a
-- database built by the pre-Alembic installers (commit 1de7fd3), then made
-- transaction-safe: psql meta-commands removed and session settings made
-- LOCAL. pg_dump prints a varchar `col IN ('a', 'b')` check as an equivalent
-- ANY(ARRAY[...]) expression that PostgreSQL would store differently; those
-- are written back in their IN form so a rebuilt catalog matches exactly.
-- Do not edit; change the schema in a new revision.

--
-- PostgreSQL database dump
--


-- Dumped from database version 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1)
-- Dumped by pg_dump version 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1)

SET LOCAL statement_timeout = 0;
SET LOCAL lock_timeout = 0;
SET LOCAL idle_in_transaction_session_timeout = 0;
SET LOCAL transaction_timeout = 0;
SET LOCAL client_encoding = 'UTF8';
SET LOCAL standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', true);
SET LOCAL check_function_bodies = false;
SET LOCAL xmloption = content;
SET LOCAL client_min_messages = warning;
SET LOCAL row_security = off;

--
-- Name: admin; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA admin;


--
-- Name: amazon; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA amazon;


--
-- Name: bookkeeping; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA bookkeeping;


--
-- Name: investments; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA investments;


--
-- Name: ofx; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ofx;


--
-- Name: payroll; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA payroll;


--
-- Name: tax; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA tax;


--
-- Name: accttype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.accttype AS ENUM (
    'CHECKING',
    'SAVINGS',
    'MONEYMRKT',
    'CREDITLINE'
);


--
-- Name: assetclass; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.assetclass AS ENUM (
    'DOMESTICBOND',
    'INTLBOND',
    'LARGESTOCK',
    'SMALLSTOCK',
    'INTLSTOCK',
    'MONEYMRKT',
    'OTHER'
);


--
-- Name: baltype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.baltype AS ENUM (
    'DOLLAR',
    'PERCENT',
    'NUMBER'
);


--
-- Name: buytype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.buytype AS ENUM (
    'BUY',
    'BUYTOCOVER'
);


--
-- Name: calltype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.calltype AS ENUM (
    'CALL',
    'PUT',
    'PREFUND',
    'MATURITY'
);


--
-- Name: correctaction; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.correctaction AS ENUM (
    'REPLACE',
    'DELETE'
);


--
-- Name: country; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.country AS ENUM (
    'ABW',
    'AFG',
    'AGO',
    'AIA',
    'ALA',
    'ALB',
    'AND',
    'ANT',
    'ARE',
    'ARG',
    'ARM',
    'ASM',
    'ATA',
    'ATF',
    'ATG',
    'AUS',
    'AUT',
    'AZE',
    'BDI',
    'BEL',
    'BEN',
    'BFA',
    'BGD',
    'BGR',
    'BHR',
    'BHS',
    'BIH',
    'BLM',
    'BLR',
    'BLZ',
    'BM',
    'BOL',
    'BRA',
    'BRB',
    'BRN',
    'BTN',
    'BVT',
    'BWA',
    'CAF',
    'CAN',
    'CCK',
    'CHE',
    'CHL',
    'CHN',
    'CIV',
    'CMR',
    'COD',
    'COG',
    'COK',
    'COL',
    'COM',
    'CPV',
    'CRI',
    'CUB',
    'CXR',
    'CYM',
    'CYP',
    'CZE',
    'DE',
    'DJI',
    'DMA',
    'DNK',
    'DOM',
    'DZA',
    'EC',
    'EGY',
    'ERI',
    'ESH',
    'ESP',
    'EST',
    'ETH',
    'FIN',
    'FJI',
    'FLK',
    'FRA',
    'FRO',
    'FSM',
    'GAB',
    'GBR',
    'GEO',
    'GGY',
    'GHA',
    'GIB',
    'GIN',
    'GLP',
    'GMB',
    'GNB',
    'GNQ',
    'GRC',
    'GRD',
    'GRL',
    'GTM',
    'GUF',
    'GUM',
    'GUY',
    'HKG',
    'HMD',
    'HND',
    'HRV',
    'HTI',
    'HUN',
    'IDN',
    'IMN',
    'IND',
    'IOT',
    'IRL',
    'IRN',
    'IRQ',
    'ISL',
    'ISR',
    'ITA',
    'JAM',
    'JEY',
    'JOR',
    'JPN',
    'KAZ',
    'KEN',
    'KGZ',
    'KHM',
    'KIR',
    'KNA',
    'KOR',
    'KWT',
    'LAO',
    'LBN',
    'LBR',
    'LBY',
    'LCA',
    'LIE',
    'LKA',
    'LSO',
    'LT',
    'LUX',
    'LVA',
    'MAC',
    'MAF',
    'MAR',
    'MCO',
    'MDA',
    'MDG',
    'MDV',
    'MEX',
    'MHL',
    'MKD',
    'MLI',
    'MLT',
    'MMR',
    'MNE',
    'MNG',
    'MNP',
    'MOZ',
    'MRT',
    'MSR',
    'MTQ',
    'MUS',
    'MWI',
    'MYS',
    'MYT',
    'NAM',
    'NCL',
    'NER',
    'NFK',
    'NGA',
    'NIC',
    'NI',
    'NLD',
    'NOR',
    'NPL',
    'NR',
    'NZL',
    'OMN',
    'PAK',
    'PAN',
    'PCN',
    'PER',
    'PHL',
    'PLW',
    'PNG',
    'POL',
    'PRI',
    'PRK',
    'PRT',
    'PRY',
    'PSE',
    'PYF',
    'QAT',
    'RE',
    'RO',
    'RUS',
    'RWA',
    'SA',
    'SDN',
    'SEN',
    'SGP',
    'SGS',
    'SHN',
    'SJM',
    'SLB',
    'SLE',
    'SLV',
    'SMR',
    'SOM',
    'SPM',
    'SRB',
    'STP',
    'SUR',
    'SVK',
    'SVN',
    'SWE',
    'SWZ',
    'SYC',
    'SYR',
    'TCA',
    'TCD',
    'TGO',
    'THA',
    'TJK',
    'TKL',
    'TKM',
    'TLS',
    'TON',
    'TTO',
    'TUN',
    'TUR',
    'TUV',
    'TWN',
    'TZA',
    'UGA',
    'UKR',
    'UMI',
    'URY',
    'USA',
    'UZB',
    'VAT',
    'VCT',
    'VEN',
    'VGB',
    'VIR',
    'VNM',
    'VUT',
    'WLF',
    'WSM',
    'YEM',
    'ZAF',
    'ZMB',
    'ZWE'
);


--
-- Name: couponfreq; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.couponfreq AS ENUM (
    'MONTHLY',
    'QUARTERLY',
    'SEMIANNUAL',
    'ANNUAL',
    'OTHER'
);


--
-- Name: cursym; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.cursym AS ENUM (
    'AE',
    'AFN',
    'ALL',
    'AMD',
    'ANG',
    'AOA',
    'ARS',
    'AUD',
    'AWG',
    'AZN',
    'BAM',
    'BBD',
    'BDT',
    'BGN',
    'BHD',
    'BIF',
    'BMD',
    'BND',
    'BOB',
    'BOV',
    'BRL',
    'BSD',
    'BTN',
    'BWP',
    'BYR',
    'BZD',
    'CAD',
    'CDF',
    'CHE',
    'CHF',
    'CHW',
    'CLF',
    'CLP',
    'CNY',
    'COP',
    'CO',
    'CRC',
    'CUC',
    'CUP',
    'CVE',
    'CZK',
    'DJF',
    'DKK',
    'DOP',
    'DZD',
    'EEK',
    'EGP',
    'ERN',
    'ETB',
    'EUR',
    'FJD',
    'FKP',
    'GBP',
    'GEL',
    'GHS',
    'GIP',
    'GMD',
    'GNF',
    'GTQ',
    'GYD',
    'HKD',
    'HNL',
    'HRK',
    'HTG',
    'HUF',
    'IDR',
    'ILS',
    'INR',
    'IQD',
    'IRR',
    'ISK',
    'JMD',
    'JOD',
    'JPY',
    'KES',
    'KGS',
    'KHR',
    'KMF',
    'KPW',
    'KRW',
    'KWD',
    'KYD',
    'KZT',
    'LAK',
    'LBP',
    'LKR',
    'LRD',
    'LSL',
    'LTL',
    'LVL',
    'LYD',
    'MAD',
    'MDL',
    'MGA',
    'MKD',
    'MMK',
    'MNT',
    'MOP',
    'MRO',
    'MUR',
    'MVR',
    'MWK',
    'MXN',
    'MXV',
    'MYR',
    'MZN',
    'NAD',
    'NGN',
    'NIO',
    'NOK',
    'NPR',
    'NZD',
    'OMR',
    'PAB',
    'PEN',
    'PGK',
    'PHP',
    'PKR',
    'PLN',
    'PYG',
    'QAR',
    'RON',
    'RSD',
    'RUB',
    'RWF',
    'SAR',
    'SBD',
    'SCR',
    'SDG',
    'SEK',
    'SGD',
    'SHP',
    'SLL',
    'SOS',
    'SRD',
    'STD',
    'SVC',
    'SYP',
    'SZL',
    'THB',
    'TJS',
    'TMT',
    'TND',
    'TOP',
    'TRY',
    'TTD',
    'TWD',
    'TZS',
    'UAH',
    'UGX',
    'USD',
    'USN',
    'USS',
    'UYI',
    'UY',
    'UZS',
    'VEF',
    'VND',
    'VUV',
    'WST',
    'XAF',
    'XAG',
    'XA',
    'XBA',
    'XBB',
    'XBC',
    'XBD',
    'XCD',
    'XDR',
    'XF',
    'XOF',
    'XPD',
    'XPF',
    'XPT',
    'XTS',
    'XXX',
    'YER',
    'ZAR',
    'ZMK',
    'ZWL'
);


--
-- Name: curtype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.curtype AS ENUM (
    'CURRENCY',
    'ORIGCURRENCY'
);


--
-- Name: debtclass; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.debtclass AS ENUM (
    'TREASURY',
    'MUNICIPAL',
    'CORPORATE',
    'OTHER'
);


--
-- Name: debttype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.debttype AS ENUM (
    'COUPON',
    'ZERO'
);


--
-- Name: heldinacct; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.heldinacct AS ENUM (
    'CASH',
    'MARGIN',
    'SHORT',
    'OTHER'
);


--
-- Name: incometype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.incometype AS ENUM (
    'CGLONG',
    'CGSHORT',
    'DIV',
    'INTEREST',
    'MISC'
);


--
-- Name: inv401ksource; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.inv401ksource AS ENUM (
    'PRETAX',
    'AFTERTAX',
    'MATCH',
    'PROFITSHARING',
    'ROLLOVER',
    'OTHERVEST',
    'OTHERNONVEST'
);


--
-- Name: mftype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.mftype AS ENUM (
    'OPENEND',
    'CLOSEEND',
    'OTHER'
);


--
-- Name: obtbuytype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.obtbuytype AS ENUM (
    'BUYTOOPEN',
    'BUYTOCLOSE'
);


--
-- Name: optaction; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.optaction AS ENUM (
    'EXERCISE',
    'ASSIGN',
    'EXPIRE'
);


--
-- Name: optselltype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.optselltype AS ENUM (
    'SELLTOCLOSE',
    'SELLTOOPEN'
);


--
-- Name: opttype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.opttype AS ENUM (
    'CALL',
    'PUT'
);


--
-- Name: postype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.postype AS ENUM (
    'SHORT',
    'LONG'
);


--
-- Name: reltype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.reltype AS ENUM (
    'SPREAD',
    'STRADDLE',
    'NONE',
    'OTHER'
);


--
-- Name: secured; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.secured AS ENUM (
    'NAKED',
    'COVERED'
);


--
-- Name: sellreason; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.sellreason AS ENUM (
    'CALL',
    'SELL',
    'MATURITY'
);


--
-- Name: selltype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.selltype AS ENUM (
    'SELL',
    'SELLSHORT'
);


--
-- Name: stocktype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.stocktype AS ENUM (
    'COMMON',
    'PREFERRED',
    'CONVERTIBLE',
    'OTHER'
);


--
-- Name: subacctfrom; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.subacctfrom AS ENUM (
    'CASH',
    'MARGIN',
    'SHORT',
    'OTHER'
);


--
-- Name: subacctfund; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.subacctfund AS ENUM (
    'CASH',
    'MARGIN',
    'SHORT',
    'OTHER'
);


--
-- Name: subacctsec; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.subacctsec AS ENUM (
    'CASH',
    'MARGIN',
    'SHORT',
    'OTHER'
);


--
-- Name: subacctto; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.subacctto AS ENUM (
    'CASH',
    'MARGIN',
    'SHORT',
    'OTHER'
);


--
-- Name: tferaction; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.tferaction AS ENUM (
    'IN',
    'OUT'
);


--
-- Name: trntype; Type: TYPE; Schema: ofx; Owner: -
--

CREATE TYPE ofx.trntype AS ENUM (
    'CREDIT',
    'DEBIT',
    'INT',
    'DIV',
    'FEE',
    'SRVCHG',
    'DEP',
    'ATM',
    'POS',
    'XFER',
    'CHECK',
    'PAYMENT',
    'CASH',
    'DIRECTDEP',
    'DIRECTDEBIT',
    'REPEATPMT',
    'OTHER'
);


--
-- Name: mapping_description_matches(text, text, text); Type: FUNCTION; Schema: admin; Owner: -
--

CREATE FUNCTION admin.mapping_description_matches(description text, keyword text, match_mode text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE STRICT
    AS $$
    DECLARE
        lowered text := lower(description);
        -- Python str.split() recognizes these Unicode whitespace characters,
        -- including NBSP, which libc/POSIX space classes need not recognize.
        whitespace text := chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||
            chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||
            chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||
            chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||
            chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
        terms text[] := regexp_split_to_array(translate(lower(keyword), whitespace,
                                                    repeat(' ', char_length(whitespace))), ' +');
        term text;
        offset_position integer := 1;
        term_position integer;
        has_term boolean := false;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM unnest(terms) value WHERE value <> '') THEN
            RETURN false;
        END IF;
        IF match_mode = 'exact' THEN RETURN lowered = lower(keyword); END IF;
        IF match_mode <> 'keyword' THEN RETURN false; END IF;
        FOREACH term IN ARRAY terms LOOP
            IF term = '' THEN CONTINUE; END IF;
            has_term := true;
            term_position := strpos(substr(lowered, offset_position), term);
            IF term_position = 0 THEN RETURN false; END IF;
            offset_position := offset_position + term_position - 1 + char_length(term);
        END LOOP;
        RETURN has_term;
    END $$;


--
-- Name: reject_item_post_request_mutation(); Type: FUNCTION; Schema: amazon; Owner: -
--

CREATE FUNCTION amazon.reject_item_post_request_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            IF NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.request_key IS DISTINCT FROM OLD.request_key OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Posting authorization is immutable; create a new reviewed request';
            END IF;
            RETURN NEW;
            END $$;


--
-- Name: reject_policy_answer_mutation(); Type: FUNCTION; Schema: amazon; Owner: -
--

CREATE FUNCTION amazon.reject_policy_answer_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            RAISE EXCEPTION 'Policy answers are immutable; save a new answer';
            END $$;


--
-- Name: reject_suspense_evidence_mutation(); Type: FUNCTION; Schema: amazon; Owner: -
--

CREATE FUNCTION amazon.reject_suspense_evidence_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            RAISE EXCEPTION 'Suspense review evidence is immutable; append a new reviewed event';
            END $$;


--
-- Name: reject_suspense_plan_mutation(); Type: FUNCTION; Schema: amazon; Owner: -
--

CREATE FUNCTION amazon.reject_suspense_plan_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Suspense plans are immutable'; END IF;
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.document_digest IS DISTINCT FROM OLD.document_digest
                OR NEW.source_run_id IS DISTINCT FROM OLD.source_run_id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Suspense plan source evidence is immutable';
            END IF;
            RETURN NEW;
            END $$;


--
-- Name: link_ofx_journal(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.link_ofx_journal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: lock_amazon_source_activity(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.lock_amazon_source_activity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
                PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
                IF TG_OP='DELETE' THEN RETURN OLD; END IF;
                RETURN NEW; END $$;


--
-- Name: lock_source_activity(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.lock_source_activity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
            IF TG_OP='UPDATE' AND (NEW.acctfrom_id,NEW.fitid) IS DISTINCT FROM (OLD.acctfrom_id,OLD.fitid) THEN
                RAISE EXCEPTION 'Provider source identity cannot be changed';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW; END $$;


--
-- Name: preserve_mapping_decision(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.preserve_mapping_decision() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            IF OLD.mapping_decision IS NOT NULL AND
               OLD.mapping_decision::jsonb IS DISTINCT FROM NEW.mapping_decision::jsonb THEN
                RAISE EXCEPTION 'Mapping decision evidence cannot be changed';
            END IF;
            RETURN NEW; END $$;


--
-- Name: preserve_ofx_identity(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.preserve_ofx_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
                RAISE EXCEPTION 'Source identity cannot be changed'; END $$;


--
-- Name: record_journal_event(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.record_journal_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      INSERT INTO bookkeeping.journal_entry_events
        (journal_entry_id, action, happened_at, actor, reason, before_row, after_row)
      VALUES
        (COALESCE(NEW.id, OLD.id), TG_OP, clock_timestamp(),
         NULLIF(current_setting('pacioli.actor', true), ''),
         NULLIF(current_setting('pacioli.reason', true), ''),
         CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) ELSE NULL END,
         CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) ELSE NULL END);
      RETURN NULL;
    END;
    $$;


--
-- Name: register_ofx_source(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.register_ofx_source() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: reject_closed_period_change(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.reject_closed_period_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      last_closed text;
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
      SELECT max(period) INTO last_closed FROM bookkeeping.closed_periods;
      IF last_closed IS NOT NULL THEN
        IF TG_OP IN ('UPDATE', 'DELETE') AND
           to_char(OLD.timestamp AT TIME ZONE 'UTC', 'YYYY-MM') <= last_closed THEN
          RAISE EXCEPTION 'Cannot change a journal in or before closed month %', last_closed;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') AND
           to_char(NEW.timestamp AT TIME ZONE 'UTC', 'YYYY-MM') <= last_closed THEN
          RAISE EXCEPTION 'Cannot post a journal in or before closed month %', last_closed;
        END IF;
      END IF;
      IF TG_OP = 'DELETE' THEN
        RETURN OLD;
      END IF;
      RETURN NEW;
    END;
    $$;


--
-- Name: reject_closed_record_change(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.reject_closed_record_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RAISE EXCEPTION 'Closed month records cannot be changed';
    END;
    $$;


--
-- Name: reject_evidence_mutation(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.reject_evidence_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
            RAISE EXCEPTION 'Accounting evidence is immutable; record a new reviewed event'; END $$;


--
-- Name: reject_journal_event_change(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.reject_journal_event_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RAISE EXCEPTION 'Journal audit events cannot be changed';
    END;
    $$;


--
-- Name: subaccount_insert_triggered(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.subaccount_insert_triggered() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bookkeeping'
    AS $$
          DECLARE
          period_intervals VARCHAR[5] := '{"YYYY", "YYYY-Q", "YYYY-MM",
                                           "YYYY-WW", "YYYY-MM-DD"}';
          period_interval_name VARCHAR;
          period_name VARCHAR;
          subaccount_name VARCHAR;
          affected_subaccounts VARCHAR[];
          affected_since TIMESTAMP WITH TIME ZONE;
          BEGIN
            -- Serializing recalculation prevents concurrent inserts from
            -- overwriting each other's aggregate with a partial snapshot.
            PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
            IF TG_OP = 'INSERT' THEN
              affected_subaccounts := ARRAY[NEW.debit_subaccount, NEW.credit_subaccount];
              affected_since := NEW.timestamp;
            ELSIF TG_OP = 'DELETE' THEN
              affected_subaccounts := ARRAY[OLD.debit_subaccount, OLD.credit_subaccount];
              affected_since := OLD.timestamp;
            ELSE
              affected_subaccounts := ARRAY[OLD.debit_subaccount, OLD.credit_subaccount,
                                            NEW.debit_subaccount, NEW.credit_subaccount];
              affected_since := least(OLD.timestamp, NEW.timestamp);
            END IF;

            FOREACH period_interval_name IN ARRAY period_intervals
            LOOP
              FOR period_name IN
                SELECT period FROM (
                  SELECT to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) AS period
                    FROM bookkeeping.journal_entries
                    WHERE timestamp >= affected_since
                  UNION
                  SELECT period FROM bookkeeping.trial_balances
                    WHERE period_interval = period_interval_name
                ) affected_periods
                WHERE period >= to_char(affected_since AT TIME ZONE 'UTC', period_interval_name)
              LOOP
                IF NOT EXISTS (
                  SELECT 1 FROM bookkeeping.journal_entries
                  WHERE to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name
                ) THEN
                  DELETE FROM bookkeeping.trial_balances
                    WHERE period_interval = period_interval_name
                      AND period = period_name;
                ELSE
                  -- A new global period needs a carried-forward row for every
                  -- account with earlier activity, even if the new entry does
                  -- not mention that account. Existing rows only need updating
                  -- for accounts touched by this journal mutation.
                  FOR subaccount_name IN
                    SELECT DISTINCT account FROM (
                      SELECT unnest(affected_subaccounts) AS account
                      UNION
                      SELECT debit_subaccount AS account
                        FROM bookkeeping.journal_entries entry
                        WHERE to_char(entry.timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name
                          AND NOT EXISTS (
                            SELECT 1 FROM bookkeeping.trial_balances balance
                            WHERE balance.subaccount = entry.debit_subaccount
                              AND balance.period_interval = period_interval_name
                              AND balance.period = period_name)
                      UNION
                      SELECT credit_subaccount AS account
                        FROM bookkeeping.journal_entries entry
                        WHERE to_char(entry.timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name
                          AND NOT EXISTS (
                            SELECT 1 FROM bookkeeping.trial_balances balance
                            WHERE balance.subaccount = entry.credit_subaccount
                              AND balance.period_interval = period_interval_name
                              AND balance.period = period_name)
                    ) accounts_to_update
                  LOOP
                    IF EXISTS (
                      SELECT 1 FROM bookkeeping.journal_entries entry
                      WHERE (entry.debit_subaccount = subaccount_name
                             OR entry.credit_subaccount = subaccount_name)
                        AND to_char(entry.timestamp AT TIME ZONE 'UTC',
                                    period_interval_name) <= period_name
                    ) THEN
                      PERFORM bookkeeping.update_trial_balance(
                        subaccount_name, period_interval_name, period_name);
                    ELSE
                      DELETE FROM bookkeeping.trial_balances
                        WHERE subaccount = subaccount_name
                          AND period_interval = period_interval_name
                          AND period = period_name;
                    END IF;
                  END LOOP;
                END IF;
              END LOOP;
            END LOOP;
            IF TG_OP = 'DELETE' THEN
              RETURN OLD;
            END IF;
            RETURN NEW;
          END;
        $$;


--
-- Name: update_trial_balance(character varying, character varying, character varying); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.update_trial_balance(_subaccount character varying, period_interval_name character varying, period_name character varying) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bookkeeping'
    AS $$
      DECLARE
          debit_balance_amount NUMERIC;
          debit_changes_amount NUMERIC;
          credit_balance_amount NUMERIC;
          credit_changes_amount NUMERIC;
      BEGIN
      SELECT coalesce(sum(functional_amount), 0) INTO debit_balance_amount
        FROM bookkeeping.journal_entries
        WHERE debit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO debit_changes_amount
        FROM bookkeeping.journal_entries
        WHERE debit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO credit_balance_amount
        FROM bookkeeping.journal_entries
        WHERE credit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO credit_changes_amount
        FROM bookkeeping.journal_entries
        WHERE credit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name;

      INSERT INTO bookkeeping.trial_balances
        (subaccount, debit_balance, credit_balance, net_balance,
         debit_changes, credit_changes, net_changes, period, period_interval)
      VALUES
        (_subaccount, debit_balance_amount, credit_balance_amount,
         debit_balance_amount - credit_balance_amount,
         debit_changes_amount, credit_changes_amount,
         debit_changes_amount - credit_changes_amount,
         period_name, period_interval_name)
      ON CONFLICT ON CONSTRAINT trial_balances_unique_constraint
      DO UPDATE SET
        debit_balance = EXCLUDED.debit_balance,
        credit_balance = EXCLUDED.credit_balance,
        net_balance = EXCLUDED.net_balance,
        debit_changes = EXCLUDED.debit_changes,
        credit_changes = EXCLUDED.credit_changes,
        net_changes = EXCLUDED.net_changes;

      RETURN;
      END;
    $$;


--
-- Name: validate_ofx_journal_link(); Type: FUNCTION; Schema: bookkeeping; Owner: -
--

CREATE FUNCTION bookkeeping.validate_ofx_journal_link() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM bookkeeping.journal_entries j
                    JOIN bookkeeping.ofx_source_identities s ON s.public_id=j.transaction_id
                    WHERE j.id=NEW.journal_entry_id AND j.transaction_source='ofx' AND s.source_id=NEW.source_id) THEN
                    RAISE EXCEPTION 'Source association does not match journal reference' USING ERRCODE='23503';
                END IF;
                RETURN NEW; END $$;


--
-- Name: append_only_universe(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.append_only_universe() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
    RAISE EXCEPTION 'The price request universe is append-only';
END $$;


--
-- Name: assign_public_id(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.assign_public_id() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE prefix text;
BEGIN
    SELECT provider INTO prefix FROM investments.brokerage_accounts WHERE id=NEW.account_id;
    NEW.public_id := prefix || ':v1:' || NEW.source_id::text;
    RETURN NEW;
END $$;


--
-- Name: freeze_brokerage_account(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.freeze_brokerage_account() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
    IF TG_OP='DELETE' OR EXISTS (SELECT 1 FROM investments.brokerage_sources WHERE account_id=OLD.id) THEN
        RAISE EXCEPTION 'A brokerage account with sources cannot be changed';
    END IF;
    RETURN NEW;
END $$;


--
-- Name: guard_brokerage_subaccounts(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.guard_brokerage_subaccounts() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
    IF NEW.transaction_source IS DISTINCT FROM 'schwab' AND EXISTS (
        SELECT 1 FROM investments.brokerage_accounts a
        WHERE NEW.debit_subaccount IN (a.cash_subaccount, a.securities_subaccount, a.valuation_subaccount, a.oci_subaccount)
           OR NEW.credit_subaccount IN (a.cash_subaccount, a.securities_subaccount, a.valuation_subaccount, a.oci_subaccount)) THEN
        RAISE EXCEPTION 'Brokerage subaccounts are posted only from brokerage sources';
    END IF;
    RETURN NEW;
END $$;


--
-- Name: link_brokerage_journal(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.link_brokerage_journal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE identifier bigint;
BEGIN
    IF NEW.transaction_source='schwab' THEN
        SELECT source_id INTO identifier FROM investments.brokerage_sources
        WHERE public_id=NEW.transaction_id;
        IF identifier IS NULL THEN
            RAISE EXCEPTION 'Brokerage journal references a missing source' USING ERRCODE='23503';
        END IF;
        INSERT INTO investments.brokerage_journal_sources(journal_entry_id, source_id)
        VALUES(NEW.id, identifier) ON CONFLICT (journal_entry_id)
        DO UPDATE SET source_id=excluded.source_id;
    ELSE
        DELETE FROM investments.brokerage_journal_sources WHERE journal_entry_id=NEW.id;
    END IF;
    RETURN NULL;
END $$;


--
-- Name: lock_brokerage_sources(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.lock_brokerage_sources() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;


--
-- Name: reject_evidence_mutation(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.reject_evidence_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
    RAISE EXCEPTION 'Brokerage evidence is immutable; import a new export instead';
END $$;


--
-- Name: validate_brokerage_journal_link(); Type: FUNCTION; Schema: investments; Owner: -
--

CREATE FUNCTION investments.validate_brokerage_journal_link() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM bookkeeping.journal_entries j
        JOIN investments.brokerage_sources s ON s.public_id=j.transaction_id
        WHERE j.id=NEW.journal_entry_id AND j.transaction_source='schwab' AND s.source_id=NEW.source_id) THEN
        RAISE EXCEPTION 'Brokerage association does not match journal reference' USING ERRCODE='23503';
    END IF;
    RETURN NEW;
END $$;


SET LOCAL default_tablespace = '';

SET LOCAL default_table_access_method = heap;

--
-- Name: admin_setup_links; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.admin_setup_links (
    id integer NOT NULL,
    token_digest character varying(64) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    revoked_at timestamp with time zone
);


--
-- Name: admin_setup_links_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.admin_setup_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_setup_links_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.admin_setup_links_id_seq OWNED BY admin.admin_setup_links.id;


--
-- Name: brokerage_credentials; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.brokerage_credentials (
    provider character varying NOT NULL,
    username text NOT NULL,
    password text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT brokerage_credentials_encrypted CHECK (((username ~~ 'fernet:%'::text) AND (password ~~ 'fernet:%'::text))),
    CONSTRAINT brokerage_credentials_provider CHECK (((provider)::text = 'schwab'::text))
);


--
-- Name: chase_browser_credentials; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.chase_browser_credentials (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    username text NOT NULL,
    password text NOT NULL,
    selected_account_labels json,
    selected_ofx_account_ids json,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chase_browser_credentials_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.chase_browser_credentials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chase_browser_credentials_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.chase_browser_credentials_id_seq OWNED BY admin.chase_browser_credentials.id;


--
-- Name: chase_browser_runs; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.chase_browser_runs (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    requested_by_id integer NOT NULL,
    singleton_slot integer NOT NULL,
    status character varying(32) NOT NULL,
    mode character varying(16) DEFAULT 'interactive'::character varying NOT NULL,
    cancel_requested boolean NOT NULL,
    reviewed_usd boolean NOT NULL,
    account_choices json,
    selected_accounts json,
    mfa_prompt character varying(16),
    mfa_code text,
    browser_requested boolean DEFAULT false NOT NULL,
    browser_visible boolean DEFAULT false NOT NULL,
    imported_count integer NOT NULL,
    duplicate_count integer NOT NULL,
    error_code character varying(48),
    diagnostics text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT chase_browser_singleton_slot CHECK ((singleton_slot = 1))
);


--
-- Name: chase_browser_runs_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.chase_browser_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chase_browser_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.chase_browser_runs_id_seq OWNED BY admin.chase_browser_runs.id;


--
-- Name: chase_export_coverage; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.chase_export_coverage (
    id integer NOT NULL,
    file_import_id integer NOT NULL,
    start date NOT NULL,
    "end" date NOT NULL,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chase_export_coverage_dates CHECK ((start <= "end"))
);


--
-- Name: chase_export_coverage_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.chase_export_coverage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chase_export_coverage_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.chase_export_coverage_id_seq OWNED BY admin.chase_export_coverage.id;


--
-- Name: chase_local_events; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.chase_local_events (
    id bigint NOT NULL,
    run_id integer NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    kind character varying(48) NOT NULL,
    details text NOT NULL
);


--
-- Name: chase_local_events_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.chase_local_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chase_local_events_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.chase_local_events_id_seq OWNED BY admin.chase_local_events.id;


--
-- Name: chase_local_preferences; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.chase_local_preferences (
    user_id integer NOT NULL,
    account_ids json NOT NULL,
    overlap_days integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chase_overlap_days CHECK (((overlap_days >= 1) AND (overlap_days <= 90)))
);


--
-- Name: connection_responses; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.connection_responses (
    id integer NOT NULL,
    connection_id integer,
    connected_at timestamp with time zone,
    response text,
    import_status character varying(16) DEFAULT 'unreviewed'::character varying NOT NULL,
    import_error character varying(64)
);


--
-- Name: connection_responses_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.connection_responses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: connection_responses_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.connection_responses_id_seq OWNED BY admin.connection_responses.id;


--
-- Name: connections; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.connections (
    id integer NOT NULL,
    source character varying,
    currency character varying,
    type character varying,
    url character varying,
    org character varying,
    fid character varying,
    routing_number character varying,
    account_number character varying,
    "user" character varying,
    password text,
    clientuid character varying,
    created_at timestamp with time zone,
    synced_at timestamp with time zone
);


--
-- Name: connections_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.connections_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: connections_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.connections_id_seq OWNED BY admin.connections.id;


--
-- Name: integrity_migrations; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.integrity_migrations (
    version text NOT NULL,
    installed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mappings; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.mappings (
    id integer NOT NULL,
    source character varying,
    keyword character varying,
    match_mode character varying DEFAULT 'keyword'::character varying NOT NULL,
    review_required boolean DEFAULT false NOT NULL,
    review_note text,
    provenance json,
    positive_debit_subaccount_id character varying,
    positive_credit_subaccount_id character varying,
    negative_debit_subaccount_id character varying,
    negative_credit_subaccount_id character varying,
    CONSTRAINT mapping_match_mode CHECK ((match_mode IN ('keyword', 'exact')))
);


--
-- Name: acctfrom; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.acctfrom (
    name text,
    subclass character varying(32) NOT NULL,
    id integer NOT NULL
);


--
-- Name: stmttrn; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.stmttrn (
    acctfrom_id integer NOT NULL,
    fitid character varying(255) NOT NULL,
    srvrtid character varying(10),
    trntype ofx.trntype NOT NULL,
    dtposted timestamp without time zone NOT NULL,
    dtuser timestamp without time zone,
    dtavail timestamp without time zone,
    trnamt numeric NOT NULL,
    correctfitid numeric,
    correctaction ofx.correctaction,
    checknum character varying(12),
    refnum character varying(32),
    sic integer,
    payeeid character varying(12),
    name character varying(32),
    memo character varying(255),
    inv401ksource ofx.inv401ksource,
    acctto_id integer,
    payee_name character varying(32),
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric
);


--
-- Name: mapping_overlaps; Type: VIEW; Schema: admin; Owner: -
--

CREATE VIEW admin.mapping_overlaps AS
 WITH sources AS (
         SELECT DISTINCT concat(stmttrn.name, stmttrn.memo) AS description,
            COALESCE((stmttrn.trnamt > (0)::numeric), false) AS positive,
            acctfrom.id AS account_id,
            acctfrom.name AS account_name
           FROM (ofx.stmttrn
             JOIN ofx.acctfrom ON ((acctfrom.id = stmttrn.acctfrom_id)))
        ), matches AS (
         SELECT sources.description,
            sources.positive,
            sources.account_id,
            sources.account_name,
            mappings.id,
            mappings.keyword,
            mappings.source,
                CASE
                    WHEN sources.positive THEN mappings.positive_debit_subaccount_id
                    ELSE mappings.negative_credit_subaccount_id
                END AS scope
           FROM (sources
             JOIN admin.mappings ON ((((mappings.source)::text = 'ofx'::text) AND ((mappings.match_mode)::text = 'exact'::text) AND (lower((mappings.keyword)::text) = lower(sources.description)) AND admin.mapping_description_matches(sources.description, (mappings.keyword)::text, (mappings.match_mode)::text))))
        UNION ALL
         SELECT sources.description,
            sources.positive,
            sources.account_id,
            sources.account_name,
            mappings.id,
            mappings.keyword,
            mappings.source,
                CASE
                    WHEN sources.positive THEN mappings.positive_debit_subaccount_id
                    ELSE mappings.negative_credit_subaccount_id
                END AS negative_credit_subaccount_id
           FROM (sources
             JOIN admin.mappings ON ((((mappings.source)::text = 'ofx'::text) AND ((mappings.match_mode)::text <> 'exact'::text) AND admin.mapping_description_matches(sources.description, (mappings.keyword)::text, (mappings.match_mode)::text))))
        ), eligible AS (
         SELECT matches.description,
            matches.positive,
            matches.account_id,
            matches.account_name,
            matches.id,
            matches.keyword,
            matches.source,
            matches.scope
           FROM matches
          WHERE ((matches.scope IS NULL) OR ((matches.scope)::text = matches.account_name))
        )
 SELECT DISTINCT first.description,
    first.id AS mapping_id_1,
    first.keyword AS mapping_keyword_1,
    second.id AS mapping_id_2,
    second.keyword AS mapping_keyword_2,
    second.source
   FROM (eligible first
     JOIN eligible second ON (((second.description = first.description) AND (second.positive = first.positive) AND (second.account_id = first.account_id) AND (first.id < second.id))))
  ORDER BY first.description;


--
-- Name: mappings_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.mappings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mappings_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.mappings_id_seq OWNED BY admin.mappings.id;


--
-- Name: ofx_file_imports; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.ofx_file_imports (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    response_id integer NOT NULL,
    ofx_account_id integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    transaction_count integer NOT NULL,
    coverage_start timestamp with time zone,
    coverage_end timestamp with time zone,
    first_transaction timestamp with time zone,
    last_transaction timestamp with time zone,
    balance_amount numeric NOT NULL,
    balance_asof timestamp with time zone NOT NULL
);


--
-- Name: ofx_file_imports_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.ofx_file_imports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ofx_file_imports_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.ofx_file_imports_id_seq OWNED BY admin.ofx_file_imports.id;


--
-- Name: roles; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.roles (
    id integer NOT NULL,
    name character varying,
    description character varying
);


--
-- Name: roles_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.roles_id_seq OWNED BY admin.roles.id;


--
-- Name: roles_users; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.roles_users (
    users_id integer,
    roles_id integer
);


--
-- Name: users; Type: TABLE; Schema: admin; Owner: -
--

CREATE TABLE admin.users (
    id integer NOT NULL,
    email character varying,
    password character varying,
    active boolean,
    confirmed_at timestamp without time zone,
    last_login_at timestamp without time zone,
    current_login_at timestamp without time zone,
    last_login_ip character varying,
    current_login_ip character varying,
    login_count integer,
    fs_uniquifier character varying(64) NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: admin; Owner: -
--

CREATE SEQUENCE admin.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: admin; Owner: -
--

ALTER SEQUENCE admin.users_id_seq OWNED BY admin.users.id;


--
-- Name: items; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.items (
    asin_isbn character varying,
    buyer_name character varying,
    carrier_name_and_tracking_number character varying,
    category_id character varying,
    condition character varying,
    currency character varying,
    id integer NOT NULL,
    item_subtotal numeric,
    item_subtotal_tax numeric,
    item_total numeric,
    list_price_per_unit numeric,
    order_id character varying,
    order_status character varying,
    ordering_customer_email character varying,
    payment_instrument_type character varying,
    purchase_price_per_unit numeric,
    quantity integer,
    release_date date,
    seller character varying,
    shipment_date date,
    shipping_address_city character varying,
    shipping_address_name character varying,
    shipping_address_state character varying,
    shipping_address_street_1 character varying,
    shipping_address_street_2 character varying,
    shipping_address_zip character varying,
    title character varying,
    unspsc_code integer,
    website character varying
);


--
-- Name: journal_entries; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.journal_entries (
    id integer NOT NULL,
    transaction_id character varying,
    transaction_source character varying,
    allocation_key character varying DEFAULT '0'::character varying NOT NULL,
    mapping_id integer,
    mapping_decision json,
    "timestamp" timestamp with time zone NOT NULL,
    debit_subaccount character varying NOT NULL,
    credit_subaccount character varying NOT NULL,
    functional_amount numeric NOT NULL,
    functional_currency character varying NOT NULL,
    source_amount numeric NOT NULL,
    source_currency character varying NOT NULL,
    conversion_rate numeric,
    conversion_source character varying,
    conversion_date date,
    memo character varying,
    CONSTRAINT journal_allocation_key CHECK ((((allocation_key)::text <> ''::text) IS TRUE)),
    CONSTRAINT journal_conversion_evidence CHECK (((((((source_currency)::text = 'USD'::text) AND (source_amount = functional_amount) AND (conversion_rate IS NULL) AND (conversion_source IS NULL) AND (conversion_date IS NULL)) OR ((source_currency IN ('EUR', 'BTC')) AND (conversion_rate IS NOT NULL) AND (conversion_rate > (0)::numeric) AND ((conversion_rate)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])) AND (NULLIF(btrim((conversion_source)::text), ''::text) IS NOT NULL) AND (conversion_date IS NOT NULL) AND (functional_amount = round((source_amount * conversion_rate), 2)))) IS TRUE) IS TRUE)),
    CONSTRAINT journal_functional_money CHECK (((((functional_currency)::text = 'USD'::text) AND (functional_amount > (0)::numeric) AND ((functional_amount)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])) AND (functional_amount = round(functional_amount, 2))) IS TRUE)),
    CONSTRAINT journal_source_money CHECK ((((source_currency IN ('USD', 'EUR', 'BTC')) AND (source_amount > (0)::numeric) AND ((source_amount)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])) AND (source_amount = round(source_amount,
CASE
    WHEN ((source_currency)::text = 'BTC'::text) THEN 8
    ELSE 2
END))) IS TRUE)),
    CONSTRAINT journal_source_pair CHECK ((((transaction_id IS NULL) = (transaction_source IS NULL)) IS TRUE))
);


--
-- Name: amazon_transactions; Type: VIEW; Schema: amazon; Owner: -
--

CREATE VIEW amazon.amazon_transactions AS
 SELECT items.asin_isbn,
    items.buyer_name,
    items.carrier_name_and_tracking_number,
    items.category_id,
    items.condition,
    items.currency,
    items.id,
    items.item_subtotal,
    items.item_subtotal_tax,
    items.item_total,
    items.list_price_per_unit,
    items.order_id,
    items.order_status,
    items.ordering_customer_email,
    items.payment_instrument_type,
    items.purchase_price_per_unit,
    items.quantity,
    items.release_date,
    items.seller,
    items.shipment_date,
    items.shipping_address_city,
    items.shipping_address_name,
    items.shipping_address_state,
    items.shipping_address_street_1,
    items.shipping_address_street_2,
    items.shipping_address_zip,
    items.title,
    items.unspsc_code,
    items.website,
    posted.id AS journal_entry_id
   FROM (amazon.items
     LEFT JOIN ( SELECT journal_entries.transaction_id,
            min(journal_entries.id) AS id
           FROM bookkeeping.journal_entries
          WHERE ((journal_entries.transaction_source)::text = 'amazon'::text)
          GROUP BY journal_entries.transaction_id) posted ON ((((items.id)::character varying)::text = (posted.transaction_id)::text)))
  ORDER BY items.shipment_date DESC;


--
-- Name: categories; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.categories (
    name character varying NOT NULL
);


--
-- Name: collection_runs; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.collection_runs (
    id integer NOT NULL,
    state character varying NOT NULL,
    progress character varying NOT NULL,
    documents integer NOT NULL,
    checkpoint text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewer character varying NOT NULL
);


--
-- Name: collection_runs_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.collection_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: collection_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.collection_runs_id_seq OWNED BY amazon.collection_runs.id;


--
-- Name: evidence_bundles; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.evidence_bundles (
    id integer NOT NULL,
    external_id character varying NOT NULL,
    evidence_id integer NOT NULL,
    details text NOT NULL,
    transaction_id character varying,
    posting_digest character varying(64)
);


--
-- Name: evidence_bundles_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.evidence_bundles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidence_bundles_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.evidence_bundles_id_seq OWNED BY amazon.evidence_bundles.id;


--
-- Name: item_jobs; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.item_jobs (
    id integer NOT NULL,
    run_id integer NOT NULL,
    item_id character varying(64) NOT NULL,
    record_key character varying(64) NOT NULL,
    state character varying NOT NULL,
    proposal text,
    decision_id integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT item_jobs_state_check CHECK ((state IN ('queued', 'running', 'suggested', 'held', 'failed')))
);


--
-- Name: item_jobs_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.item_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.item_jobs_id_seq OWNED BY amazon.item_jobs.id;


--
-- Name: item_post_runs; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.item_post_runs (
    id integer NOT NULL,
    run_id integer NOT NULL,
    evidence_id integer NOT NULL,
    request_key character varying(64) NOT NULL,
    state character varying NOT NULL,
    progress character varying NOT NULL,
    result text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT item_post_runs_state_check CHECK ((state IN ('queued', 'running', 'completed', 'failed')))
);


--
-- Name: item_post_runs_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.item_post_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_post_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.item_post_runs_id_seq OWNED BY amazon.item_post_runs.id;


--
-- Name: item_runs; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.item_runs (
    id integer NOT NULL,
    manifest_id integer NOT NULL,
    input_digest character varying(64) NOT NULL,
    state character varying NOT NULL,
    progress character varying NOT NULL,
    reviewer character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT item_runs_state_check CHECK ((state IN ('queued', 'running', 'completed', 'needs_attention')))
);


--
-- Name: item_runs_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.item_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.item_runs_id_seq OWNED BY amazon.item_runs.id;


--
-- Name: items_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: items_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.items_id_seq OWNED BY amazon.items.id;


--
-- Name: orders; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.orders (
    id character varying NOT NULL,
    order_date date
);


--
-- Name: policy_answers; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.policy_answers (
    id integer NOT NULL,
    plan_id integer NOT NULL,
    question_key character varying(128) NOT NULL,
    preview_evidence_id integer NOT NULL,
    evidence_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: policy_answers_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.policy_answers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: policy_answers_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.policy_answers_id_seq OWNED BY amazon.policy_answers.id;


--
-- Name: product_rules; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.product_rules (
    id integer NOT NULL,
    product_digest character varying(64) NOT NULL,
    evidence_id integer NOT NULL,
    supersedes_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: product_rules_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.product_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: product_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.product_rules_id_seq OWNED BY amazon.product_rules.id;


--
-- Name: suspense_facts; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.suspense_facts (
    id integer NOT NULL,
    plan_id integer NOT NULL,
    item_id character varying(64) NOT NULL,
    evidence_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: suspense_facts_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.suspense_facts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: suspense_facts_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.suspense_facts_id_seq OWNED BY amazon.suspense_facts.id;


--
-- Name: suspense_items; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.suspense_items (
    id integer NOT NULL,
    plan_id integer NOT NULL,
    item_id character varying(64) NOT NULL,
    source_job_id integer NOT NULL,
    amount numeric NOT NULL,
    remediation_group character varying NOT NULL,
    proposed_category character varying
);


--
-- Name: suspense_items_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.suspense_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: suspense_items_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.suspense_items_id_seq OWNED BY amazon.suspense_items.id;


--
-- Name: suspense_plans; Type: TABLE; Schema: amazon; Owner: -
--

CREATE TABLE amazon.suspense_plans (
    id integer NOT NULL,
    evidence_id integer NOT NULL,
    document_digest character varying(64) NOT NULL,
    source_run_id integer NOT NULL,
    review_run_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: suspense_plans_id_seq; Type: SEQUENCE; Schema: amazon; Owner: -
--

CREATE SEQUENCE amazon.suspense_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: suspense_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: amazon; Owner: -
--

ALTER SEQUENCE amazon.suspense_plans_id_seq OWNED BY amazon.suspense_plans.id;


--
-- Name: accounting_evaluation_cases; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.accounting_evaluation_cases (
    id integer NOT NULL,
    source_id character varying NOT NULL,
    merchant character varying NOT NULL,
    partition character varying NOT NULL,
    record text NOT NULL,
    expected character varying NOT NULL,
    evidence text NOT NULL,
    reviewer character varying NOT NULL
);


--
-- Name: accounting_evaluation_cases_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.accounting_evaluation_cases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accounting_evaluation_cases_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.accounting_evaluation_cases_id_seq OWNED BY bookkeeping.accounting_evaluation_cases.id;


--
-- Name: accounting_evidence; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.accounting_evidence (
    id integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    kind character varying NOT NULL,
    payload text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewer character varying NOT NULL
);


--
-- Name: accounting_evidence_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.accounting_evidence_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accounting_evidence_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.accounting_evidence_id_seq OWNED BY bookkeeping.accounting_evidence.id;


--
-- Name: accounting_reviews; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.accounting_reviews (
    id integer NOT NULL,
    mapping_id integer NOT NULL,
    snapshot text NOT NULL,
    facts text NOT NULL,
    proposal text,
    decision text,
    state character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewer character varying NOT NULL
);


--
-- Name: accounting_reviews_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.accounting_reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accounting_reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.accounting_reviews_id_seq OWNED BY bookkeeping.accounting_reviews.id;


--
-- Name: accounts; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.accounts (
    name character varying NOT NULL,
    cash_source character varying,
    is_cash boolean DEFAULT false NOT NULL,
    parent character varying
);


--
-- Name: accruals; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.accruals (
    id integer NOT NULL,
    name character varying NOT NULL,
    description character varying,
    accrual_type character varying NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    business_days boolean NOT NULL,
    daily_amount numeric NOT NULL,
    income_statement_subaccount character varying NOT NULL,
    balance_sheet_subaccount character varying NOT NULL
);


--
-- Name: accruals_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.accruals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accruals_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.accruals_id_seq OWNED BY bookkeeping.accruals.id;


--
-- Name: bank_feed_accounts; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.bank_feed_accounts (
    ofx_account_id integer NOT NULL,
    currency character varying NOT NULL,
    confirmed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bank_feed_currency_supported CHECK ((currency IN ('USD', 'EUR', 'BTC')))
);


--
-- Name: bank_feed_accounts_ofx_account_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.bank_feed_accounts_ofx_account_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bank_feed_accounts_ofx_account_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.bank_feed_accounts_ofx_account_id_seq OWNED BY bookkeeping.bank_feed_accounts.ofx_account_id;


--
-- Name: bank_statements; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.bank_statements (
    id integer NOT NULL,
    account_id integer NOT NULL,
    evidence_id integer NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    opening numeric NOT NULL,
    closing numeric NOT NULL,
    details text NOT NULL,
    CONSTRAINT bank_statements_check CHECK ((start_date <= end_date))
);


--
-- Name: bank_statements_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.bank_statements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bank_statements_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.bank_statements_id_seq OWNED BY bookkeeping.bank_statements.id;


--
-- Name: classifications; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.classifications (
    name character varying NOT NULL,
    parent character varying
);


--
-- Name: closed_periods; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.closed_periods (
    period character varying NOT NULL,
    closed_at timestamp with time zone DEFAULT now() NOT NULL,
    journal_count integer NOT NULL,
    journal_digest character varying NOT NULL,
    statement_snapshot json NOT NULL
);


--
-- Name: ofx_journal_sources; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.ofx_journal_sources (
    journal_entry_id integer NOT NULL,
    source_id bigint NOT NULL
);


--
-- Name: ofx_source_identities; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.ofx_source_identities (
    source_id bigint NOT NULL,
    public_id text NOT NULL,
    acctfrom_id integer NOT NULL,
    fitid character varying NOT NULL
);


--
-- Name: detailed_journal_entries; Type: VIEW; Schema: bookkeeping; Owner: -
--

CREATE VIEW bookkeeping.detailed_journal_entries AS
 SELECT journal_entries.id,
    journal_entries.transaction_source,
    journal_entries.transaction_id,
    journal_entries."timestamp",
    journal_entries.debit_subaccount,
    journal_entries.credit_subaccount,
    journal_entries.functional_amount,
        CASE journal_entries.transaction_source
            WHEN 'ofx'::text THEN concat(stmttrn.name, stmttrn.memo)
            WHEN 'amazon'::text THEN (items.title)::text
            ELSE NULL::text
        END AS description,
    journal_entries.mapping_id,
    journal_entries.mapping_decision
   FROM (((((bookkeeping.journal_entries
     LEFT JOIN bookkeeping.ofx_journal_sources association ON ((association.journal_entry_id = journal_entries.id)))
     LEFT JOIN bookkeeping.ofx_source_identities identity ON ((identity.source_id = association.source_id)))
     LEFT JOIN ofx.stmttrn ON (((identity.acctfrom_id = stmttrn.acctfrom_id) AND ((identity.fitid)::text = (stmttrn.fitid)::text) AND ((journal_entries.transaction_source)::text = 'ofx'::text))))
     LEFT JOIN amazon.items ON (((((items.id)::character varying)::text = (journal_entries.transaction_id)::text) AND ((journal_entries.transaction_source)::text = 'amazon'::text))))
     LEFT JOIN ofx.acctfrom ON ((acctfrom.id = stmttrn.acctfrom_id)))
  ORDER BY journal_entries."timestamp" DESC;


--
-- Name: elements; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.elements (
    name character varying NOT NULL
);


--
-- Name: journal_entries_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.journal_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: journal_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.journal_entries_id_seq OWNED BY bookkeeping.journal_entries.id;


--
-- Name: journal_entry_events; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.journal_entry_events (
    id bigint NOT NULL,
    journal_entry_id integer NOT NULL,
    action character varying NOT NULL,
    happened_at timestamp with time zone NOT NULL,
    actor character varying,
    reason character varying,
    before_row json,
    after_row json
);


--
-- Name: journal_entry_events_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.journal_entry_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: journal_entry_events_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.journal_entry_events_id_seq OWNED BY bookkeeping.journal_entry_events.id;


--
-- Name: ofx_source_identities_source_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.ofx_source_identities_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ofx_source_identities_source_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.ofx_source_identities_source_id_seq OWNED BY bookkeeping.ofx_source_identities.source_id;


--
-- Name: opening_balances; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.opening_balances (
    account_id integer NOT NULL,
    cutover date NOT NULL,
    amount numeric NOT NULL,
    evidence_digest character varying(64) NOT NULL,
    details text NOT NULL,
    journal_id integer,
    reviewer character varying NOT NULL
);


--
-- Name: opening_balances_account_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.opening_balances_account_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: opening_balances_account_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.opening_balances_account_id_seq OWNED BY bookkeeping.opening_balances.account_id;


--
-- Name: subaccounts; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.subaccounts (
    id integer NOT NULL,
    name character varying,
    description character varying,
    parent character varying
);


--
-- Name: subaccounts_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.subaccounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subaccounts_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.subaccounts_id_seq OWNED BY bookkeeping.subaccounts.id;


--
-- Name: transfer_match_legs; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.transfer_match_legs (
    journal_id integer NOT NULL,
    match_id integer NOT NULL
);


--
-- Name: transfer_matches; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.transfer_matches (
    id integer NOT NULL,
    fingerprint character varying(64) NOT NULL,
    details text NOT NULL,
    reviewer character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: transfer_matches_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.transfer_matches_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transfer_matches_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.transfer_matches_id_seq OWNED BY bookkeeping.transfer_matches.id;


--
-- Name: trial_balances; Type: TABLE; Schema: bookkeeping; Owner: -
--

CREATE TABLE bookkeeping.trial_balances (
    id integer NOT NULL,
    subaccount character varying,
    period character varying,
    period_interval character varying,
    debit_balance numeric NOT NULL,
    credit_balance numeric NOT NULL,
    net_balance numeric NOT NULL,
    debit_changes numeric NOT NULL,
    credit_changes numeric NOT NULL,
    net_changes numeric NOT NULL
);


--
-- Name: trial_balances_id_seq; Type: SEQUENCE; Schema: bookkeeping; Owner: -
--

CREATE SEQUENCE bookkeeping.trial_balances_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: trial_balances_id_seq; Type: SEQUENCE OWNED BY; Schema: bookkeeping; Owner: -
--

ALTER SEQUENCE bookkeeping.trial_balances_id_seq OWNED BY bookkeeping.trial_balances.id;


--
-- Name: activity_observations; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.activity_observations (
    export_id integer NOT NULL,
    source_id bigint NOT NULL
);


--
-- Name: brokerage_accounts; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_accounts (
    id integer NOT NULL,
    provider character varying NOT NULL,
    label character varying NOT NULL,
    masked_number character varying(8) NOT NULL,
    currency character varying DEFAULT 'USD'::character varying NOT NULL,
    opened_on date NOT NULL,
    cash_subaccount character varying NOT NULL,
    securities_subaccount character varying NOT NULL,
    valuation_subaccount character varying NOT NULL,
    oci_subaccount character varying NOT NULL,
    reviewer character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tax_treatment character varying DEFAULT 'taxable'::character varying NOT NULL,
    CONSTRAINT brokerage_accounts_check CHECK ((((cash_subaccount)::text <> (securities_subaccount)::text) AND ((cash_subaccount)::text <> (valuation_subaccount)::text) AND ((cash_subaccount)::text <> (oci_subaccount)::text) AND ((securities_subaccount)::text <> (valuation_subaccount)::text) AND ((securities_subaccount)::text <> (oci_subaccount)::text) AND ((valuation_subaccount)::text <> (oci_subaccount)::text))),
    CONSTRAINT brokerage_accounts_currency_check CHECK (((currency)::text = 'USD'::text)),
    CONSTRAINT brokerage_accounts_provider_check CHECK (((provider)::text = 'schwab'::text)),
    CONSTRAINT brokerage_tax_treatment CHECK ((tax_treatment IN ('taxable', 'retirement')))
);


--
-- Name: brokerage_accounts_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.brokerage_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: brokerage_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.brokerage_accounts_id_seq OWNED BY investments.brokerage_accounts.id;


--
-- Name: brokerage_activity; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_activity (
    source_id bigint NOT NULL,
    account_id integer NOT NULL,
    posted_on date NOT NULL,
    as_of date,
    action character varying NOT NULL,
    symbol character varying,
    description character varying DEFAULT ''::character varying NOT NULL,
    quantity numeric,
    price numeric,
    fees numeric,
    amount numeric,
    economic_digest character(64) NOT NULL,
    day_ordinal integer NOT NULL,
    first_export_id integer NOT NULL,
    CONSTRAINT brokerage_activity_amount_check CHECK (((amount IS NULL) OR (amount = round(amount, 2)))),
    CONSTRAINT brokerage_activity_day_ordinal_check CHECK ((day_ordinal >= 1))
);


--
-- Name: brokerage_exports; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_exports (
    id integer NOT NULL,
    account_id integer NOT NULL,
    kind character varying NOT NULL,
    format character varying NOT NULL,
    evidence_id integer NOT NULL,
    sha256 character(64) NOT NULL,
    window_start date,
    window_end date,
    provisional_from date,
    as_of timestamp with time zone,
    row_count integer NOT NULL,
    state character varying NOT NULL,
    reviewer character varying NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT brokerage_exports_check CHECK (((window_start IS NULL) OR (window_start <= window_end))),
    CONSTRAINT brokerage_exports_format_check CHECK ((format IN ('json', 'csv', 'xml'))),
    CONSTRAINT brokerage_exports_kind_check CHECK ((kind IN ('transactions', 'realized_lots', 'positions'))),
    CONSTRAINT brokerage_exports_state_check CHECK ((state IN ('active', 'disagreement')))
);


--
-- Name: brokerage_exports_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.brokerage_exports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: brokerage_exports_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.brokerage_exports_id_seq OWNED BY investments.brokerage_exports.id;


--
-- Name: brokerage_journal_sources; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_journal_sources (
    journal_entry_id integer NOT NULL,
    source_id bigint NOT NULL
);


--
-- Name: brokerage_opening_lines; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_opening_lines (
    source_id bigint NOT NULL,
    symbol character varying NOT NULL,
    quantity numeric NOT NULL,
    cost_basis numeric NOT NULL,
    CONSTRAINT brokerage_opening_lines_cost_basis_check CHECK (((cost_basis >= (0)::numeric) AND (cost_basis = round(cost_basis, 2)))),
    CONSTRAINT brokerage_opening_lines_quantity_check CHECK ((quantity > (0)::numeric))
);


--
-- Name: brokerage_openings; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_openings (
    source_id bigint NOT NULL,
    account_id integer NOT NULL,
    as_of date NOT NULL,
    cash numeric NOT NULL,
    cost_basis numeric NOT NULL,
    snapshot_id integer NOT NULL,
    evidence_id integer NOT NULL,
    reviewer character varying NOT NULL,
    reason text NOT NULL,
    CONSTRAINT brokerage_openings_cash_check CHECK ((cash = round(cash, 2))),
    CONSTRAINT brokerage_openings_cost_basis_check CHECK (((cost_basis >= (0)::numeric) AND (cost_basis = round(cost_basis, 2)))),
    CONSTRAINT brokerage_openings_reason_check CHECK ((length(TRIM(BOTH FROM reason)) > 0))
);


--
-- Name: brokerage_sources; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.brokerage_sources (
    source_id bigint NOT NULL,
    public_id text NOT NULL,
    account_id integer NOT NULL,
    kind character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT brokerage_sources_kind_check CHECK ((kind IN ('activity', 'wash', 'valuation', 'opening')))
);


--
-- Name: brokerage_sources_source_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.brokerage_sources_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: brokerage_sources_source_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.brokerage_sources_source_id_seq OWNED BY investments.brokerage_sources.source_id;


--
-- Name: position_lines; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.position_lines (
    id integer NOT NULL,
    snapshot_id integer NOT NULL,
    symbol character varying,
    description character varying DEFAULT ''::character varying NOT NULL,
    security_type character varying,
    quantity numeric,
    price numeric,
    market_value numeric,
    cost_basis numeric,
    is_cash boolean DEFAULT false NOT NULL
);


--
-- Name: position_lines_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.position_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: position_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.position_lines_id_seq OWNED BY investments.position_lines.id;


--
-- Name: position_snapshots; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.position_snapshots (
    id integer NOT NULL,
    account_id integer NOT NULL,
    export_id integer NOT NULL,
    as_of timestamp with time zone NOT NULL,
    market_date date NOT NULL,
    end_of_day boolean NOT NULL,
    total_market_value numeric
);


--
-- Name: position_snapshots_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.position_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: position_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.position_snapshots_id_seq OWNED BY investments.position_snapshots.id;


--
-- Name: price_request_universe; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.price_request_universe (
    ticker character varying NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: realized_lots; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.realized_lots (
    id integer NOT NULL,
    account_id integer NOT NULL,
    export_id integer NOT NULL,
    symbol character varying NOT NULL,
    quantity numeric NOT NULL,
    opened_on date,
    closed_on date NOT NULL,
    proceeds numeric NOT NULL,
    cost_basis numeric NOT NULL,
    disallowed_loss numeric DEFAULT 0 NOT NULL,
    term character varying NOT NULL,
    lot_digest character(64) NOT NULL,
    lot_ordinal integer NOT NULL,
    supersedes_id integer,
    CONSTRAINT realized_lots_cost_basis_check CHECK ((cost_basis = round(cost_basis, 2))),
    CONSTRAINT realized_lots_disallowed_loss_check CHECK ((disallowed_loss >= (0)::numeric)),
    CONSTRAINT realized_lots_lot_ordinal_check CHECK ((lot_ordinal >= 1)),
    CONSTRAINT realized_lots_proceeds_check CHECK ((proceeds = round(proceeds, 2))),
    CONSTRAINT realized_lots_quantity_check CHECK ((quantity > (0)::numeric)),
    CONSTRAINT realized_lots_term_check CHECK ((term IN ('short', 'long')))
);


--
-- Name: realized_lots_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.realized_lots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: realized_lots_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.realized_lots_id_seq OWNED BY investments.realized_lots.id;


--
-- Name: security_prices; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.security_prices (
    id integer NOT NULL,
    ticker character varying,
    date date,
    adjusted_close numeric,
    close numeric,
    high numeric,
    low numeric,
    open numeric,
    volume numeric
);


--
-- Name: security_prices_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.security_prices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: security_prices_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.security_prices_id_seq OWNED BY investments.security_prices.id;


--
-- Name: share_move_proposals; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.share_move_proposals (
    id integer NOT NULL,
    signature character(64) NOT NULL,
    action character varying NOT NULL,
    symbol character varying NOT NULL,
    description character varying NOT NULL,
    direction character varying NOT NULL,
    row_count integer NOT NULL,
    facts jsonb NOT NULL,
    classification character varying,
    reason text,
    validation_errors jsonb NOT NULL,
    model character varying NOT NULL,
    model_digest character(64) NOT NULL,
    policy_version character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT share_move_proposals_direction_check CHECK ((direction IN ('in', 'out'))),
    CONSTRAINT share_move_proposals_row_count_check CHECK ((row_count > 0))
);


--
-- Name: share_move_proposals_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.share_move_proposals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: share_move_proposals_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.share_move_proposals_id_seq OWNED BY investments.share_move_proposals.id;


--
-- Name: share_move_rules; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.share_move_rules (
    id integer NOT NULL,
    action character varying NOT NULL,
    symbol character varying NOT NULL,
    description character varying NOT NULL,
    direction character varying NOT NULL,
    resolution character varying NOT NULL,
    proposal_id integer NOT NULL,
    reviewer character varying NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT share_move_rules_direction_check CHECK ((direction IN ('in', 'out'))),
    CONSTRAINT share_move_rules_reason_check CHECK ((length(TRIM(BOTH FROM reason)) > 0)),
    CONSTRAINT share_move_rules_resolution_check CHECK (((resolution)::text = 'memo'::text))
);


--
-- Name: share_move_rules_id_seq; Type: SEQUENCE; Schema: investments; Owner: -
--

CREATE SEQUENCE investments.share_move_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: share_move_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: investments; Owner: -
--

ALTER SEQUENCE investments.share_move_rules_id_seq OWNED BY investments.share_move_rules.id;


--
-- Name: symbol_map; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.symbol_map (
    provider_symbol character varying NOT NULL,
    market_ticker character varying NOT NULL,
    reviewer character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: valuation_points; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.valuation_points (
    source_id bigint NOT NULL,
    account_id integer NOT NULL,
    kind character varying NOT NULL,
    valuation_date date NOT NULL,
    valued_at timestamp with time zone NOT NULL,
    market_value numeric NOT NULL,
    cost_basis numeric NOT NULL,
    target numeric NOT NULL,
    evidence_id integer NOT NULL,
    CONSTRAINT valuation_points_check CHECK ((target = (market_value - cost_basis))),
    CONSTRAINT valuation_points_cost_basis_check CHECK ((cost_basis = round(cost_basis, 2))),
    CONSTRAINT valuation_points_kind_check CHECK ((kind IN ('positions', 'market_close'))),
    CONSTRAINT valuation_points_market_value_check CHECK ((market_value = round(market_value, 2)))
);


--
-- Name: wash_adjustments; Type: TABLE; Schema: investments; Owner: -
--

CREATE TABLE investments.wash_adjustments (
    source_id bigint NOT NULL,
    lot_id integer NOT NULL,
    amount numeric NOT NULL,
    trigger_on date NOT NULL,
    trigger_source_id bigint,
    CONSTRAINT wash_adjustments_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2))))
);


--
-- Name: acctfrom_id_seq; Type: SEQUENCE; Schema: ofx; Owner: -
--

CREATE SEQUENCE ofx.acctfrom_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: acctfrom_id_seq; Type: SEQUENCE OWNED BY; Schema: ofx; Owner: -
--

ALTER SEQUENCE ofx.acctfrom_id_seq OWNED BY ofx.acctfrom.id;


--
-- Name: acctto; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.acctto (
    name text,
    subclass character varying(32) NOT NULL,
    id integer NOT NULL
);


--
-- Name: acctto_id_seq; Type: SEQUENCE; Schema: ofx; Owner: -
--

CREATE SEQUENCE ofx.acctto_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: acctto_id_seq; Type: SEQUENCE OWNED BY; Schema: ofx; Owner: -
--

ALTER SEQUENCE ofx.acctto_id_seq OWNED BY ofx.acctto.id;


--
-- Name: availbal; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.availbal (
    balamt numeric NOT NULL,
    acctfrom_id integer NOT NULL,
    dtasof timestamp without time zone NOT NULL
);


--
-- Name: bal; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.bal (
    name character varying(32) NOT NULL,
    "desc" character varying(80) NOT NULL,
    baltype ofx.baltype NOT NULL,
    value numeric NOT NULL,
    acctfrom_id integer NOT NULL,
    dtasof timestamp without time zone NOT NULL,
    cursym ofx.cursym,
    currate numeric
);


--
-- Name: bankacctfrom; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.bankacctfrom (
    bankid character varying(9) NOT NULL,
    branchid character varying(22),
    acctid character varying(22) NOT NULL,
    accttype ofx.accttype NOT NULL,
    acctkey character varying(22),
    id integer NOT NULL
);


--
-- Name: bankacctto; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.bankacctto (
    bankid character varying(9) NOT NULL,
    branchid character varying(22),
    acctid character varying(22) NOT NULL,
    accttype ofx.accttype NOT NULL,
    acctkey character varying(22),
    id integer NOT NULL
);


--
-- Name: buydebt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.buydebt (
    accrdint numeric,
    markup numeric,
    loanid character varying(32),
    loanprincipal numeric,
    loaninterest numeric,
    dtpayroll timestamp without time zone,
    prioryearcontrib boolean,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT buydebt_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice + markup)) - (((commission + fees) + load) + taxes)))),
    CONSTRAINT buydebt_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT buydebt_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT buydebt_markup_check CHECK ((markup >= (0)::numeric)),
    CONSTRAINT buydebt_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT buydebt_unitprice_check CHECK ((unitprice >= (0)::numeric))
);


--
-- Name: buymf; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.buymf (
    buytype ofx.buytype NOT NULL,
    relfitid character varying(255),
    markup numeric,
    loanid character varying(32),
    loanprincipal numeric,
    loaninterest numeric,
    dtpayroll timestamp without time zone,
    prioryearcontrib boolean,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT buymf_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice + markup)) - (((commission + fees) + load) + taxes)))),
    CONSTRAINT buymf_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT buymf_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT buymf_markup_check CHECK ((markup >= (0)::numeric)),
    CONSTRAINT buymf_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT buymf_unitprice_check CHECK ((unitprice >= (0)::numeric))
);


--
-- Name: buyopt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.buyopt (
    optbuytype ofx.obtbuytype NOT NULL,
    shperctrct integer NOT NULL,
    markup numeric,
    loanid character varying(32),
    loanprincipal numeric,
    loaninterest numeric,
    dtpayroll timestamp without time zone,
    prioryearcontrib boolean,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT buyopt_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice + markup)) - (((commission + fees) + load) + taxes)))),
    CONSTRAINT buyopt_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT buyopt_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT buyopt_markup_check CHECK ((markup >= (0)::numeric)),
    CONSTRAINT buyopt_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT buyopt_unitprice_check CHECK ((unitprice >= (0)::numeric))
);


--
-- Name: buyother; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.buyother (
    markup numeric,
    loanid character varying(32),
    loanprincipal numeric,
    loaninterest numeric,
    dtpayroll timestamp without time zone,
    prioryearcontrib boolean,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT buyother_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice + markup)) - (((commission + fees) + load) + taxes)))),
    CONSTRAINT buyother_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT buyother_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT buyother_markup_check CHECK ((markup >= (0)::numeric)),
    CONSTRAINT buyother_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT buyother_unitprice_check CHECK ((unitprice >= (0)::numeric))
);


--
-- Name: buystock; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.buystock (
    buytype ofx.buytype NOT NULL,
    markup numeric,
    loanid character varying(32),
    loanprincipal numeric,
    loaninterest numeric,
    dtpayroll timestamp without time zone,
    prioryearcontrib boolean,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT buystock_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice + markup)) - (((commission + fees) + load) + taxes)))),
    CONSTRAINT buystock_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT buystock_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT buystock_markup_check CHECK ((markup >= (0)::numeric)),
    CONSTRAINT buystock_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT buystock_unitprice_check CHECK ((unitprice >= (0)::numeric))
);


--
-- Name: ccacctfrom; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.ccacctfrom (
    acctid character varying(22) NOT NULL,
    acctkey character varying(22),
    id integer NOT NULL
);


--
-- Name: ccacctto; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.ccacctto (
    acctid character varying(22) NOT NULL,
    acctkey character varying(22),
    id integer NOT NULL
);


--
-- Name: closureopt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.closureopt (
    optaction ofx.optaction NOT NULL,
    units numeric NOT NULL,
    shperctrct integer NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    relfitid character varying(255),
    gain numeric,
    secinfo_id integer NOT NULL,
    id integer NOT NULL
);


--
-- Name: invtran; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.invtran (
    acctfrom_id integer NOT NULL,
    fitid character varying(255) NOT NULL,
    srvrtid character varying(10),
    dttrade timestamp without time zone NOT NULL,
    dtsettle timestamp without time zone,
    reversalfitid character varying(255),
    memo character varying(255),
    subclass character varying(32) NOT NULL,
    id integer NOT NULL
);


--
-- Name: reinvest; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.reinvest (
    incometype ofx.incometype NOT NULL,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    taxexempt boolean,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT reinvest_check CHECK ((total = ((units * unitprice) + (((commission + fees) + load) + taxes)))),
    CONSTRAINT reinvest_commission_check CHECK ((commission >= (0)::numeric)),
    CONSTRAINT reinvest_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT reinvest_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT reinvest_taxes_check CHECK ((taxes >= (0)::numeric))
);


--
-- Name: secinfo; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.secinfo (
    uniqueidtype character varying(10) NOT NULL,
    uniqueid character varying(32) NOT NULL,
    secname text NOT NULL,
    ticker text,
    fiid character varying(32),
    rating character varying(10),
    unitprice numeric,
    dtasof timestamp without time zone,
    memo character varying(255),
    subclass character varying(32) NOT NULL,
    id integer NOT NULL,
    cursym ofx.cursym,
    currate numeric
);


--
-- Name: sellmf; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.sellmf (
    selltype ofx.selltype NOT NULL,
    avgcostbasis numeric,
    relfitid character varying(255),
    markdown numeric,
    withholding numeric,
    taxexempt boolean,
    gain numeric,
    loanid character varying(32),
    statewithholding numeric,
    penalty numeric,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT sellmf_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice - markdown)) - ((((((commission + fees) + load) + taxes) + penalty) + withholding) + statewithholding)))),
    CONSTRAINT sellmf_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT sellmf_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT sellmf_markdown_check CHECK ((markdown >= (0)::numeric)),
    CONSTRAINT sellmf_statewithholding_check CHECK ((statewithholding >= (0)::numeric)),
    CONSTRAINT sellmf_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT sellmf_unitprice_check CHECK ((unitprice >= (0)::numeric)),
    CONSTRAINT sellmf_withholding_check CHECK ((withholding >= (0)::numeric))
);


--
-- Name: investment_transactions; Type: VIEW; Schema: ofx; Owner: -
--

CREATE VIEW ofx.investment_transactions AS
 SELECT invtran.acctfrom_id,
    invtran.fitid,
    invtran.srvrtid,
    invtran.dttrade,
    invtran.dtsettle,
    invtran.reversalfitid,
    invtran.memo,
    invtran.subclass,
    invtran.id,
    acctfrom.name AS account_name,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf_secinfo.secname
            WHEN 'sellmf'::text THEN sellmf_secinfo.secname
            WHEN 'reinvest'::text THEN reinvest_secinfo.secname
            ELSE NULL::text
        END AS secname,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf_secinfo.ticker
            WHEN 'sellmf'::text THEN sellmf_secinfo.ticker
            WHEN 'reinvest'::text THEN reinvest_secinfo.ticker
            ELSE NULL::text
        END AS ticker,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf.units
            WHEN 'sellmf'::text THEN sellmf.units
            WHEN 'reinvest'::text THEN reinvest.units
            ELSE NULL::numeric
        END AS units,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf.unitprice
            WHEN 'sellmf'::text THEN sellmf.unitprice
            WHEN 'reinvest'::text THEN reinvest.unitprice
            ELSE NULL::numeric
        END AS unitprice,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN (buymf.total * ('-1'::integer)::numeric)
            WHEN 'sellmf'::text THEN (sellmf.total * ('-1'::integer)::numeric)
            WHEN 'reinvest'::text THEN (reinvest.total * ('-1'::integer)::numeric)
            ELSE NULL::numeric
        END AS total
   FROM (((((((ofx.invtran
     LEFT JOIN ofx.buymf ON (((buymf.id = invtran.id) AND ((invtran.subclass)::text = 'buymf'::text))))
     LEFT JOIN ofx.sellmf ON (((sellmf.id = invtran.id) AND ((invtran.subclass)::text = 'sellmf'::text))))
     LEFT JOIN ofx.reinvest ON (((reinvest.id = invtran.id) AND ((invtran.subclass)::text = 'reinvest'::text))))
     LEFT JOIN ofx.secinfo buymf_secinfo ON ((buymf_secinfo.id = buymf.secinfo_id)))
     LEFT JOIN ofx.secinfo sellmf_secinfo ON ((sellmf_secinfo.id = sellmf.secinfo_id)))
     LEFT JOIN ofx.secinfo reinvest_secinfo ON ((reinvest_secinfo.id = reinvest.secinfo_id)))
     JOIN ofx.acctfrom ON ((acctfrom.id = invtran.acctfrom_id)))
  ORDER BY invtran.dttrade DESC;


--
-- Name: cost_bases; Type: VIEW; Schema: ofx; Owner: -
--

CREATE VIEW ofx.cost_bases AS
 SELECT investment_transactions.secname,
    sum(investment_transactions.units) AS total_units,
    sum(investment_transactions.total) AS cost_basis,
    q1.ticker,
    q1.adjusted_close AS close,
    (q1.adjusted_close * sum(investment_transactions.units)) AS market_value,
    ((q1.adjusted_close * sum(investment_transactions.units)) - sum(investment_transactions.total)) AS pnl,
    (((q1.adjusted_close * sum(investment_transactions.units)) - sum(investment_transactions.total)) / sum(investment_transactions.total)) AS pnl_percent,
    q2.date AS price_date
   FROM ((ofx.investment_transactions
     JOIN ( SELECT security_prices.ticker,
            max(security_prices.date) AS date
           FROM investments.security_prices
          GROUP BY security_prices.ticker) q2 ON (((q2.ticker)::text = investment_transactions.ticker)))
     JOIN investments.security_prices q1 ON ((((q1.ticker)::text = investment_transactions.ticker) AND (q2.date = q1.date))))
  GROUP BY investment_transactions.secname, q1.ticker, q2.date, q1.adjusted_close
  ORDER BY (sum(investment_transactions.total));


--
-- Name: debtinfo; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.debtinfo (
    parvalue numeric NOT NULL,
    debttype ofx.debttype NOT NULL,
    debtclass ofx.debtclass,
    couponrt numeric,
    dtcoupon timestamp without time zone,
    couponfreq ofx.couponfreq,
    callprice numeric,
    yieldtocall numeric,
    dtcall timestamp without time zone,
    calltype ofx.calltype,
    ytmat numeric,
    dtmat timestamp without time zone,
    assetclass ofx.assetclass,
    fiassetclass character varying(32),
    id integer NOT NULL
);


--
-- Name: fiportion; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.fiportion (
    mfinfo_id integer NOT NULL,
    fiassetclass ofx.assetclass NOT NULL,
    percent numeric NOT NULL
);


--
-- Name: income; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.income (
    incometype ofx.incometype NOT NULL,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    taxexempt boolean,
    withholding numeric,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT income_withholding_check CHECK ((withholding >= (0)::numeric))
);


--
-- Name: invacctfrom; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.invacctfrom (
    brokerid character varying(22) NOT NULL,
    acctid character varying(22) NOT NULL,
    id integer NOT NULL
);


--
-- Name: invbal; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.invbal (
    acctfrom_id integer NOT NULL,
    dtasof timestamp without time zone NOT NULL,
    availcash numeric NOT NULL,
    marginbalance numeric NOT NULL,
    shortbalance numeric NOT NULL,
    buypower numeric
);


--
-- Name: invbanktran; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.invbanktran (
    acctfrom_id integer NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    fitid character varying(255) NOT NULL,
    srvrtid character varying(10),
    trntype ofx.trntype NOT NULL,
    dtposted timestamp without time zone NOT NULL,
    dtuser timestamp without time zone,
    dtavail timestamp without time zone,
    trnamt numeric NOT NULL,
    correctfitid numeric,
    correctaction ofx.correctaction,
    checknum character varying(12),
    refnum character varying(32),
    sic integer,
    payeeid character varying(12),
    name character varying(32),
    memo character varying(255),
    inv401ksource ofx.inv401ksource,
    acctto_id integer,
    payee_name character varying(32),
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric
);


--
-- Name: invexpense; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.invexpense (
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL
);


--
-- Name: invpos; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.invpos (
    acctfrom_id integer NOT NULL,
    dtasof timestamp without time zone,
    heldinacct ofx.heldinacct NOT NULL,
    postype ofx.postype NOT NULL,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    mktval numeric NOT NULL,
    dtpriceasof timestamp without time zone NOT NULL,
    memo character varying(255),
    inv401ksource ofx.inv401ksource,
    subclass character varying(32) NOT NULL,
    id integer NOT NULL,
    secinfo_id integer NOT NULL,
    cursym ofx.cursym,
    currate numeric
);


--
-- Name: invpos_id_seq; Type: SEQUENCE; Schema: ofx; Owner: -
--

CREATE SEQUENCE ofx.invpos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invpos_id_seq; Type: SEQUENCE OWNED BY; Schema: ofx; Owner: -
--

ALTER SEQUENCE ofx.invpos_id_seq OWNED BY ofx.invpos.id;


--
-- Name: invtran_id_seq; Type: SEQUENCE; Schema: ofx; Owner: -
--

CREATE SEQUENCE ofx.invtran_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invtran_id_seq; Type: SEQUENCE OWNED BY; Schema: ofx; Owner: -
--

ALTER SEQUENCE ofx.invtran_id_seq OWNED BY ofx.invtran.id;


--
-- Name: jrnlfund; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.jrnlfund (
    subacctto ofx.subacctto NOT NULL,
    subacctfrom ofx.subacctfrom NOT NULL,
    total numeric NOT NULL,
    id integer NOT NULL
);


--
-- Name: jrnlsec; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.jrnlsec (
    subacctto ofx.subacctto NOT NULL,
    subacctfrom ofx.subacctfrom NOT NULL,
    units numeric NOT NULL,
    secinfo_id integer NOT NULL,
    id integer NOT NULL
);


--
-- Name: ledgerbal; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.ledgerbal (
    balamt numeric NOT NULL,
    acctfrom_id integer NOT NULL,
    dtasof timestamp without time zone NOT NULL
);


--
-- Name: margininterest; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.margininterest (
    total numeric NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL
);


--
-- Name: mfinfo; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.mfinfo (
    mftype ofx.mftype,
    yld numeric,
    dtyieldasof timestamp without time zone,
    id integer NOT NULL
);


--
-- Name: optinfo; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.optinfo (
    opttype ofx.opttype NOT NULL,
    strikeprice numeric NOT NULL,
    dtexpire timestamp without time zone NOT NULL,
    shperctrct integer NOT NULL,
    assetclass ofx.assetclass,
    fiassetclass character varying(32),
    id integer NOT NULL
);


--
-- Name: otherinfo; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.otherinfo (
    typedesc character varying(32),
    assetclass ofx.assetclass,
    fiassetclass character varying(32),
    percent numeric,
    id integer NOT NULL
);


--
-- Name: payee; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.payee (
    name text NOT NULL,
    addr1 character varying(32) NOT NULL,
    addr2 character varying(32),
    addr3 character varying(32),
    city character varying(32) NOT NULL,
    state character varying(5) NOT NULL,
    postalcode character varying(11) NOT NULL,
    country ofx.country,
    phone character varying(32) NOT NULL
);


--
-- Name: portion; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.portion (
    mfinfo_id integer NOT NULL,
    assetclass ofx.assetclass NOT NULL,
    percent numeric NOT NULL
);


--
-- Name: posdebt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.posdebt (
    id integer NOT NULL
);


--
-- Name: posmf; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.posmf (
    unitsstreet numeric,
    unitsuser numeric,
    reinvdiv boolean,
    reinvcg boolean,
    id integer NOT NULL
);


--
-- Name: posopt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.posopt (
    secured ofx.secured,
    id integer NOT NULL
);


--
-- Name: posother; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.posother (
    id integer NOT NULL
);


--
-- Name: posstock; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.posstock (
    unitsstreet numeric,
    unitsuser numeric,
    reinvdiv boolean,
    id integer NOT NULL
);


--
-- Name: retofcap; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.retofcap (
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL
);


--
-- Name: secinfo_id_seq; Type: SEQUENCE; Schema: ofx; Owner: -
--

CREATE SEQUENCE ofx.secinfo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: secinfo_id_seq; Type: SEQUENCE OWNED BY; Schema: ofx; Owner: -
--

ALTER SEQUENCE ofx.secinfo_id_seq OWNED BY ofx.secinfo.id;


--
-- Name: selldebt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.selldebt (
    sellreason ofx.sellreason NOT NULL,
    accrdint numeric,
    markdown numeric,
    withholding numeric,
    taxexempt boolean,
    gain numeric,
    loanid character varying(32),
    statewithholding numeric,
    penalty numeric,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT selldebt_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice - markdown)) - ((((((commission + fees) + load) + taxes) + penalty) + withholding) + statewithholding)))),
    CONSTRAINT selldebt_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT selldebt_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT selldebt_markdown_check CHECK ((markdown >= (0)::numeric)),
    CONSTRAINT selldebt_statewithholding_check CHECK ((statewithholding >= (0)::numeric)),
    CONSTRAINT selldebt_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT selldebt_unitprice_check CHECK ((unitprice >= (0)::numeric)),
    CONSTRAINT selldebt_withholding_check CHECK ((withholding >= (0)::numeric))
);


--
-- Name: sellopt; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.sellopt (
    optselltype ofx.optselltype NOT NULL,
    shperctrct integer NOT NULL,
    relfitid character varying(255),
    reltype ofx.reltype,
    secured ofx.secured,
    markdown numeric,
    withholding numeric,
    taxexempt boolean,
    gain numeric,
    loanid character varying(32),
    statewithholding numeric,
    penalty numeric,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT sellopt_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice - markdown)) - ((((((commission + fees) + load) + taxes) + penalty) + withholding) + statewithholding)))),
    CONSTRAINT sellopt_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT sellopt_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT sellopt_markdown_check CHECK ((markdown >= (0)::numeric)),
    CONSTRAINT sellopt_statewithholding_check CHECK ((statewithholding >= (0)::numeric)),
    CONSTRAINT sellopt_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT sellopt_unitprice_check CHECK ((unitprice >= (0)::numeric)),
    CONSTRAINT sellopt_withholding_check CHECK ((withholding >= (0)::numeric))
);


--
-- Name: sellother; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.sellother (
    markdown numeric,
    withholding numeric,
    taxexempt boolean,
    gain numeric,
    loanid character varying(32),
    statewithholding numeric,
    penalty numeric,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT sellother_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice - markdown)) - ((((((commission + fees) + load) + taxes) + penalty) + withholding) + statewithholding)))),
    CONSTRAINT sellother_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT sellother_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT sellother_markdown_check CHECK ((markdown >= (0)::numeric)),
    CONSTRAINT sellother_statewithholding_check CHECK ((statewithholding >= (0)::numeric)),
    CONSTRAINT sellother_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT sellother_unitprice_check CHECK ((unitprice >= (0)::numeric)),
    CONSTRAINT sellother_withholding_check CHECK ((withholding >= (0)::numeric))
);


--
-- Name: sellstock; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.sellstock (
    selltype ofx.selltype NOT NULL,
    markdown numeric,
    withholding numeric,
    taxexempt boolean,
    gain numeric,
    loanid character varying(32),
    statewithholding numeric,
    penalty numeric,
    units numeric NOT NULL,
    unitprice numeric NOT NULL,
    commission numeric,
    taxes numeric,
    fees numeric,
    load numeric,
    total numeric NOT NULL,
    subacctsec ofx.subacctsec NOT NULL,
    subacctfund ofx.subacctfund NOT NULL,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    curtype ofx.curtype,
    cursym ofx.cursym,
    currate numeric,
    id integer NOT NULL,
    CONSTRAINT sellstock_check CHECK ((total = (((('-1'::integer)::numeric * units) * (unitprice - markdown)) - ((((((commission + fees) + load) + taxes) + penalty) + withholding) + statewithholding)))),
    CONSTRAINT sellstock_fees_check CHECK ((fees >= (0)::numeric)),
    CONSTRAINT sellstock_load_check CHECK ((load >= (0)::numeric)),
    CONSTRAINT sellstock_markdown_check CHECK ((markdown >= (0)::numeric)),
    CONSTRAINT sellstock_statewithholding_check CHECK ((statewithholding >= (0)::numeric)),
    CONSTRAINT sellstock_taxes_check CHECK ((taxes >= (0)::numeric)),
    CONSTRAINT sellstock_unitprice_check CHECK ((unitprice >= (0)::numeric)),
    CONSTRAINT sellstock_withholding_check CHECK ((withholding >= (0)::numeric))
);


--
-- Name: split; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.split (
    subacctsec ofx.subacctsec NOT NULL,
    oldunits numeric NOT NULL,
    newunits numeric NOT NULL,
    numerator numeric NOT NULL,
    denominator numeric NOT NULL,
    fraccash numeric,
    subacctfund ofx.subacctfund,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    id integer NOT NULL
);


--
-- Name: stockinfo; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.stockinfo (
    typedesc character varying(32),
    stocktype ofx.stocktype,
    yld numeric,
    dtyieldasof timestamp without time zone,
    assetclass ofx.assetclass,
    fiassetclass character varying(32),
    id integer NOT NULL
);


--
-- Name: transactions; Type: VIEW; Schema: ofx; Owner: -
--

CREATE VIEW ofx.transactions AS
 SELECT identity.public_id AS id,
    stmttrn.dtposted AS date,
    stmttrn.trnamt AS amount,
    concat(stmttrn.name, stmttrn.memo) AS description,
    stmttrn.trntype AS type,
    acctfrom.name AS account,
    stmttrn.acctfrom_id AS account_id,
    posted.id AS journal_entry_id,
    posted.debit_subaccount,
    posted.credit_subaccount
   FROM (((ofx.stmttrn
     JOIN bookkeeping.ofx_source_identities identity ON (((identity.acctfrom_id = stmttrn.acctfrom_id) AND ((identity.fitid)::text = (stmttrn.fitid)::text))))
     LEFT JOIN ( SELECT association.source_id,
            min(journal.id) AS id,
            (
                CASE
                    WHEN (count(*) = 1) THEN min((journal.debit_subaccount)::text)
                    ELSE '[split]'::text
                END)::character varying AS debit_subaccount,
            (
                CASE
                    WHEN (count(*) = 1) THEN min((journal.credit_subaccount)::text)
                    ELSE '[split]'::text
                END)::character varying AS credit_subaccount
           FROM (bookkeeping.journal_entries journal
             JOIN bookkeeping.ofx_journal_sources association ON ((association.journal_entry_id = journal.id)))
          WHERE ((journal.transaction_source)::text = 'ofx'::text)
          GROUP BY association.source_id) posted ON ((posted.source_id = identity.source_id)))
     JOIN ofx.acctfrom ON ((acctfrom.id = stmttrn.acctfrom_id)))
  ORDER BY stmttrn.dtposted DESC;


--
-- Name: transfer; Type: TABLE; Schema: ofx; Owner: -
--

CREATE TABLE ofx.transfer (
    subacctsec ofx.subacctsec NOT NULL,
    units numeric NOT NULL,
    tferaction ofx.tferaction NOT NULL,
    postype ofx.postype NOT NULL,
    avgcostbasis numeric,
    unitprice numeric,
    dtpurchase timestamp without time zone,
    inv401ksource ofx.inv401ksource,
    secinfo_id integer NOT NULL,
    id integer NOT NULL
);


--
-- Name: paystub_items; Type: TABLE; Schema: payroll; Owner: -
--

CREATE TABLE payroll.paystub_items (
    id integer NOT NULL,
    description character varying,
    rate numeric,
    hours numeric,
    this_period numeric,
    year_to_date numeric,
    statutory boolean,
    paystub_id integer
);


--
-- Name: paystub_items_id_seq; Type: SEQUENCE; Schema: payroll; Owner: -
--

CREATE SEQUENCE payroll.paystub_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paystub_items_id_seq; Type: SEQUENCE OWNED BY; Schema: payroll; Owner: -
--

ALTER SEQUENCE payroll.paystub_items_id_seq OWNED BY payroll.paystub_items.id;


--
-- Name: paystubs; Type: TABLE; Schema: payroll; Owner: -
--

CREATE TABLE payroll.paystubs (
    id integer NOT NULL,
    employer_name character varying,
    period_beginning date,
    period_ending date,
    pay_date date
);


--
-- Name: paystubs_id_seq; Type: SEQUENCE; Schema: payroll; Owner: -
--

CREATE SEQUENCE payroll.paystubs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paystubs_id_seq; Type: SEQUENCE OWNED BY; Schema: payroll; Owner: -
--

ALTER SEQUENCE payroll.paystubs_id_seq OWNED BY payroll.paystubs.id;


--
-- Name: subaccounts_tax_tags; Type: TABLE; Schema: tax; Owner: -
--

CREATE TABLE tax.subaccounts_tax_tags (
    subaccount_name character varying,
    tax_tag_id integer
);


--
-- Name: tax_tags; Type: TABLE; Schema: tax; Owner: -
--

CREATE TABLE tax.tax_tags (
    id integer NOT NULL,
    name character varying,
    description character varying
);


--
-- Name: tax_tags_id_seq; Type: SEQUENCE; Schema: tax; Owner: -
--

CREATE SEQUENCE tax.tax_tags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tax_tags_id_seq; Type: SEQUENCE OWNED BY; Schema: tax; Owner: -
--

ALTER SEQUENCE tax.tax_tags_id_seq OWNED BY tax.tax_tags.id;


--
-- Name: admin_setup_links id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.admin_setup_links ALTER COLUMN id SET DEFAULT nextval('admin.admin_setup_links_id_seq'::regclass);


--
-- Name: chase_browser_credentials id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_credentials ALTER COLUMN id SET DEFAULT nextval('admin.chase_browser_credentials_id_seq'::regclass);


--
-- Name: chase_browser_runs id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_runs ALTER COLUMN id SET DEFAULT nextval('admin.chase_browser_runs_id_seq'::regclass);


--
-- Name: chase_export_coverage id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_export_coverage ALTER COLUMN id SET DEFAULT nextval('admin.chase_export_coverage_id_seq'::regclass);


--
-- Name: chase_local_events id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_local_events ALTER COLUMN id SET DEFAULT nextval('admin.chase_local_events_id_seq'::regclass);


--
-- Name: connection_responses id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.connection_responses ALTER COLUMN id SET DEFAULT nextval('admin.connection_responses_id_seq'::regclass);


--
-- Name: connections id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.connections ALTER COLUMN id SET DEFAULT nextval('admin.connections_id_seq'::regclass);


--
-- Name: mappings id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings ALTER COLUMN id SET DEFAULT nextval('admin.mappings_id_seq'::regclass);


--
-- Name: ofx_file_imports id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.ofx_file_imports ALTER COLUMN id SET DEFAULT nextval('admin.ofx_file_imports_id_seq'::regclass);


--
-- Name: roles id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.roles ALTER COLUMN id SET DEFAULT nextval('admin.roles_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.users ALTER COLUMN id SET DEFAULT nextval('admin.users_id_seq'::regclass);


--
-- Name: collection_runs id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.collection_runs ALTER COLUMN id SET DEFAULT nextval('amazon.collection_runs_id_seq'::regclass);


--
-- Name: evidence_bundles id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.evidence_bundles ALTER COLUMN id SET DEFAULT nextval('amazon.evidence_bundles_id_seq'::regclass);


--
-- Name: item_jobs id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_jobs ALTER COLUMN id SET DEFAULT nextval('amazon.item_jobs_id_seq'::regclass);


--
-- Name: item_post_runs id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_post_runs ALTER COLUMN id SET DEFAULT nextval('amazon.item_post_runs_id_seq'::regclass);


--
-- Name: item_runs id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_runs ALTER COLUMN id SET DEFAULT nextval('amazon.item_runs_id_seq'::regclass);


--
-- Name: items id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.items ALTER COLUMN id SET DEFAULT nextval('amazon.items_id_seq'::regclass);


--
-- Name: policy_answers id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers ALTER COLUMN id SET DEFAULT nextval('amazon.policy_answers_id_seq'::regclass);


--
-- Name: product_rules id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.product_rules ALTER COLUMN id SET DEFAULT nextval('amazon.product_rules_id_seq'::regclass);


--
-- Name: suspense_facts id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_facts ALTER COLUMN id SET DEFAULT nextval('amazon.suspense_facts_id_seq'::regclass);


--
-- Name: suspense_items id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_items ALTER COLUMN id SET DEFAULT nextval('amazon.suspense_items_id_seq'::regclass);


--
-- Name: suspense_plans id; Type: DEFAULT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans ALTER COLUMN id SET DEFAULT nextval('amazon.suspense_plans_id_seq'::regclass);


--
-- Name: accounting_evaluation_cases id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_evaluation_cases ALTER COLUMN id SET DEFAULT nextval('bookkeeping.accounting_evaluation_cases_id_seq'::regclass);


--
-- Name: accounting_evidence id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_evidence ALTER COLUMN id SET DEFAULT nextval('bookkeeping.accounting_evidence_id_seq'::regclass);


--
-- Name: accounting_reviews id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_reviews ALTER COLUMN id SET DEFAULT nextval('bookkeeping.accounting_reviews_id_seq'::regclass);


--
-- Name: accruals id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accruals ALTER COLUMN id SET DEFAULT nextval('bookkeeping.accruals_id_seq'::regclass);


--
-- Name: bank_feed_accounts ofx_account_id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.bank_feed_accounts ALTER COLUMN ofx_account_id SET DEFAULT nextval('bookkeeping.bank_feed_accounts_ofx_account_id_seq'::regclass);


--
-- Name: bank_statements id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.bank_statements ALTER COLUMN id SET DEFAULT nextval('bookkeeping.bank_statements_id_seq'::regclass);


--
-- Name: journal_entries id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entries ALTER COLUMN id SET DEFAULT nextval('bookkeeping.journal_entries_id_seq'::regclass);


--
-- Name: journal_entry_events id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entry_events ALTER COLUMN id SET DEFAULT nextval('bookkeeping.journal_entry_events_id_seq'::regclass);


--
-- Name: ofx_source_identities source_id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_source_identities ALTER COLUMN source_id SET DEFAULT nextval('bookkeeping.ofx_source_identities_source_id_seq'::regclass);


--
-- Name: opening_balances account_id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.opening_balances ALTER COLUMN account_id SET DEFAULT nextval('bookkeeping.opening_balances_account_id_seq'::regclass);


--
-- Name: subaccounts id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.subaccounts ALTER COLUMN id SET DEFAULT nextval('bookkeeping.subaccounts_id_seq'::regclass);


--
-- Name: transfer_matches id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.transfer_matches ALTER COLUMN id SET DEFAULT nextval('bookkeeping.transfer_matches_id_seq'::regclass);


--
-- Name: trial_balances id; Type: DEFAULT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.trial_balances ALTER COLUMN id SET DEFAULT nextval('bookkeeping.trial_balances_id_seq'::regclass);


--
-- Name: brokerage_accounts id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts ALTER COLUMN id SET DEFAULT nextval('investments.brokerage_accounts_id_seq'::regclass);


--
-- Name: brokerage_exports id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_exports ALTER COLUMN id SET DEFAULT nextval('investments.brokerage_exports_id_seq'::regclass);


--
-- Name: brokerage_sources source_id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_sources ALTER COLUMN source_id SET DEFAULT nextval('investments.brokerage_sources_source_id_seq'::regclass);


--
-- Name: position_lines id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_lines ALTER COLUMN id SET DEFAULT nextval('investments.position_lines_id_seq'::regclass);


--
-- Name: position_snapshots id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_snapshots ALTER COLUMN id SET DEFAULT nextval('investments.position_snapshots_id_seq'::regclass);


--
-- Name: realized_lots id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.realized_lots ALTER COLUMN id SET DEFAULT nextval('investments.realized_lots_id_seq'::regclass);


--
-- Name: security_prices id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.security_prices ALTER COLUMN id SET DEFAULT nextval('investments.security_prices_id_seq'::regclass);


--
-- Name: share_move_proposals id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.share_move_proposals ALTER COLUMN id SET DEFAULT nextval('investments.share_move_proposals_id_seq'::regclass);


--
-- Name: share_move_rules id; Type: DEFAULT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.share_move_rules ALTER COLUMN id SET DEFAULT nextval('investments.share_move_rules_id_seq'::regclass);


--
-- Name: acctfrom id; Type: DEFAULT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.acctfrom ALTER COLUMN id SET DEFAULT nextval('ofx.acctfrom_id_seq'::regclass);


--
-- Name: acctto id; Type: DEFAULT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.acctto ALTER COLUMN id SET DEFAULT nextval('ofx.acctto_id_seq'::regclass);


--
-- Name: invpos id; Type: DEFAULT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invpos ALTER COLUMN id SET DEFAULT nextval('ofx.invpos_id_seq'::regclass);


--
-- Name: invtran id; Type: DEFAULT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invtran ALTER COLUMN id SET DEFAULT nextval('ofx.invtran_id_seq'::regclass);


--
-- Name: secinfo id; Type: DEFAULT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.secinfo ALTER COLUMN id SET DEFAULT nextval('ofx.secinfo_id_seq'::regclass);


--
-- Name: paystub_items id; Type: DEFAULT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystub_items ALTER COLUMN id SET DEFAULT nextval('payroll.paystub_items_id_seq'::regclass);


--
-- Name: paystubs id; Type: DEFAULT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystubs ALTER COLUMN id SET DEFAULT nextval('payroll.paystubs_id_seq'::regclass);


--
-- Name: tax_tags id; Type: DEFAULT; Schema: tax; Owner: -
--

ALTER TABLE ONLY tax.tax_tags ALTER COLUMN id SET DEFAULT nextval('tax.tax_tags_id_seq'::regclass);


--
-- Name: admin_setup_links admin_setup_links_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.admin_setup_links
    ADD CONSTRAINT admin_setup_links_pkey PRIMARY KEY (id);


--
-- Name: admin_setup_links admin_setup_links_token_digest_key; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.admin_setup_links
    ADD CONSTRAINT admin_setup_links_token_digest_key UNIQUE (token_digest);


--
-- Name: brokerage_credentials brokerage_credentials_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.brokerage_credentials
    ADD CONSTRAINT brokerage_credentials_pkey PRIMARY KEY (provider);


--
-- Name: chase_browser_credentials chase_browser_credentials_connection_id_key; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_credentials
    ADD CONSTRAINT chase_browser_credentials_connection_id_key UNIQUE (connection_id);


--
-- Name: chase_browser_credentials chase_browser_credentials_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_credentials
    ADD CONSTRAINT chase_browser_credentials_pkey PRIMARY KEY (id);


--
-- Name: chase_browser_runs chase_browser_runs_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_runs
    ADD CONSTRAINT chase_browser_runs_pkey PRIMARY KEY (id);


--
-- Name: chase_export_coverage chase_export_coverage_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_export_coverage
    ADD CONSTRAINT chase_export_coverage_pkey PRIMARY KEY (id);


--
-- Name: chase_export_coverage chase_export_coverage_unique; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_export_coverage
    ADD CONSTRAINT chase_export_coverage_unique UNIQUE (file_import_id, start, "end");


--
-- Name: chase_local_events chase_local_events_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_local_events
    ADD CONSTRAINT chase_local_events_pkey PRIMARY KEY (id);


--
-- Name: chase_local_preferences chase_local_preferences_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_local_preferences
    ADD CONSTRAINT chase_local_preferences_pkey PRIMARY KEY (user_id);


--
-- Name: connection_responses connection_responses_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.connection_responses
    ADD CONSTRAINT connection_responses_pkey PRIMARY KEY (id);


--
-- Name: connections connections_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.connections
    ADD CONSTRAINT connections_pkey PRIMARY KEY (id);


--
-- Name: integrity_migrations integrity_migrations_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.integrity_migrations
    ADD CONSTRAINT integrity_migrations_pkey PRIMARY KEY (version);


--
-- Name: mappings mappings_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings
    ADD CONSTRAINT mappings_pkey PRIMARY KEY (id);


--
-- Name: mappings mappings_unique_constraint; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings
    ADD CONSTRAINT mappings_unique_constraint UNIQUE (source, keyword);


--
-- Name: ofx_file_imports ofx_file_imports_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.ofx_file_imports
    ADD CONSTRAINT ofx_file_imports_pkey PRIMARY KEY (id);


--
-- Name: ofx_file_imports ofx_file_imports_sha256_unique; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.ofx_file_imports
    ADD CONSTRAINT ofx_file_imports_sha256_unique UNIQUE (sha256);


--
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_fs_uniquifier_key; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.users
    ADD CONSTRAINT users_fs_uniquifier_key UNIQUE (fs_uniquifier);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: items amazon_items_unique_constraint; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.items
    ADD CONSTRAINT amazon_items_unique_constraint UNIQUE (order_id, title);


--
-- Name: categories categories_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (name);


--
-- Name: collection_runs collection_runs_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.collection_runs
    ADD CONSTRAINT collection_runs_pkey PRIMARY KEY (id);


--
-- Name: evidence_bundles evidence_bundles_external_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.evidence_bundles
    ADD CONSTRAINT evidence_bundles_external_id_key UNIQUE (external_id);


--
-- Name: evidence_bundles evidence_bundles_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.evidence_bundles
    ADD CONSTRAINT evidence_bundles_pkey PRIMARY KEY (id);


--
-- Name: evidence_bundles evidence_bundles_transaction_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.evidence_bundles
    ADD CONSTRAINT evidence_bundles_transaction_id_key UNIQUE (transaction_id);


--
-- Name: item_jobs item_jobs_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_jobs
    ADD CONSTRAINT item_jobs_pkey PRIMARY KEY (id);


--
-- Name: item_jobs item_jobs_run_id_item_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_jobs
    ADD CONSTRAINT item_jobs_run_id_item_id_key UNIQUE (run_id, item_id);


--
-- Name: item_post_runs item_post_runs_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_post_runs
    ADD CONSTRAINT item_post_runs_pkey PRIMARY KEY (id);


--
-- Name: item_post_runs item_post_runs_request_key_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_post_runs
    ADD CONSTRAINT item_post_runs_request_key_key UNIQUE (request_key);


--
-- Name: item_runs item_runs_input_digest_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_runs
    ADD CONSTRAINT item_runs_input_digest_key UNIQUE (input_digest);


--
-- Name: item_runs item_runs_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_runs
    ADD CONSTRAINT item_runs_pkey PRIMARY KEY (id);


--
-- Name: items items_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.items
    ADD CONSTRAINT items_pkey PRIMARY KEY (id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: policy_answers policy_answers_evidence_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers
    ADD CONSTRAINT policy_answers_evidence_id_key UNIQUE (evidence_id);


--
-- Name: policy_answers policy_answers_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers
    ADD CONSTRAINT policy_answers_pkey PRIMARY KEY (id);


--
-- Name: policy_answers policy_answers_preview_evidence_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers
    ADD CONSTRAINT policy_answers_preview_evidence_id_key UNIQUE (preview_evidence_id);


--
-- Name: product_rules product_rules_evidence_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.product_rules
    ADD CONSTRAINT product_rules_evidence_id_key UNIQUE (evidence_id);


--
-- Name: product_rules product_rules_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.product_rules
    ADD CONSTRAINT product_rules_pkey PRIMARY KEY (id);


--
-- Name: product_rules product_rules_supersedes_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.product_rules
    ADD CONSTRAINT product_rules_supersedes_id_key UNIQUE (supersedes_id);


--
-- Name: suspense_facts suspense_facts_evidence_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_facts
    ADD CONSTRAINT suspense_facts_evidence_id_key UNIQUE (evidence_id);


--
-- Name: suspense_facts suspense_facts_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_facts
    ADD CONSTRAINT suspense_facts_pkey PRIMARY KEY (id);


--
-- Name: suspense_items suspense_items_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_items
    ADD CONSTRAINT suspense_items_pkey PRIMARY KEY (id);


--
-- Name: suspense_items suspense_items_plan_id_item_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_items
    ADD CONSTRAINT suspense_items_plan_id_item_id_key UNIQUE (plan_id, item_id);


--
-- Name: suspense_plans suspense_plans_document_digest_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans
    ADD CONSTRAINT suspense_plans_document_digest_key UNIQUE (document_digest);


--
-- Name: suspense_plans suspense_plans_evidence_id_key; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans
    ADD CONSTRAINT suspense_plans_evidence_id_key UNIQUE (evidence_id);


--
-- Name: suspense_plans suspense_plans_pkey; Type: CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans
    ADD CONSTRAINT suspense_plans_pkey PRIMARY KEY (id);


--
-- Name: accounting_evaluation_cases accounting_evaluation_cases_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_evaluation_cases
    ADD CONSTRAINT accounting_evaluation_cases_pkey PRIMARY KEY (id);


--
-- Name: accounting_evaluation_cases accounting_evaluation_cases_source_id_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_evaluation_cases
    ADD CONSTRAINT accounting_evaluation_cases_source_id_key UNIQUE (source_id);


--
-- Name: accounting_evidence accounting_evidence_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_evidence
    ADD CONSTRAINT accounting_evidence_pkey PRIMARY KEY (id);


--
-- Name: accounting_evidence accounting_evidence_sha256_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_evidence
    ADD CONSTRAINT accounting_evidence_sha256_key UNIQUE (sha256);


--
-- Name: accounting_reviews accounting_reviews_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_reviews
    ADD CONSTRAINT accounting_reviews_pkey PRIMARY KEY (id);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (name);


--
-- Name: accruals accruals_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accruals
    ADD CONSTRAINT accruals_pkey PRIMARY KEY (id);


--
-- Name: accruals accruals_unique_constraint; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accruals
    ADD CONSTRAINT accruals_unique_constraint UNIQUE (id, name, start_date, end_date);


--
-- Name: bank_feed_accounts bank_feed_accounts_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.bank_feed_accounts
    ADD CONSTRAINT bank_feed_accounts_pkey PRIMARY KEY (ofx_account_id);


--
-- Name: bank_statements bank_statements_account_id_end_date_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.bank_statements
    ADD CONSTRAINT bank_statements_account_id_end_date_key UNIQUE (account_id, end_date);


--
-- Name: bank_statements bank_statements_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.bank_statements
    ADD CONSTRAINT bank_statements_pkey PRIMARY KEY (id);


--
-- Name: classifications classifications_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.classifications
    ADD CONSTRAINT classifications_pkey PRIMARY KEY (name);


--
-- Name: closed_periods closed_periods_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.closed_periods
    ADD CONSTRAINT closed_periods_pkey PRIMARY KEY (period);


--
-- Name: elements elements_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.elements
    ADD CONSTRAINT elements_pkey PRIMARY KEY (name);


--
-- Name: journal_entries journal_entries_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entries
    ADD CONSTRAINT journal_entries_pkey PRIMARY KEY (id);


--
-- Name: journal_entries journal_entries_source_allocation_unique; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entries
    ADD CONSTRAINT journal_entries_source_allocation_unique UNIQUE (transaction_id, transaction_source, allocation_key);


--
-- Name: journal_entry_events journal_entry_events_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entry_events
    ADD CONSTRAINT journal_entry_events_pkey PRIMARY KEY (id);


--
-- Name: ofx_journal_sources ofx_journal_sources_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_journal_sources
    ADD CONSTRAINT ofx_journal_sources_pkey PRIMARY KEY (journal_entry_id);


--
-- Name: ofx_source_identities ofx_source_identities_acctfrom_id_fitid_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_source_identities
    ADD CONSTRAINT ofx_source_identities_acctfrom_id_fitid_key UNIQUE (acctfrom_id, fitid);


--
-- Name: ofx_source_identities ofx_source_identities_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_source_identities
    ADD CONSTRAINT ofx_source_identities_pkey PRIMARY KEY (source_id);


--
-- Name: ofx_source_identities ofx_source_identities_public_id_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_source_identities
    ADD CONSTRAINT ofx_source_identities_public_id_key UNIQUE (public_id);


--
-- Name: opening_balances opening_balances_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.opening_balances
    ADD CONSTRAINT opening_balances_pkey PRIMARY KEY (account_id);


--
-- Name: subaccounts subaccounts_name_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.subaccounts
    ADD CONSTRAINT subaccounts_name_key UNIQUE (name);


--
-- Name: subaccounts subaccounts_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.subaccounts
    ADD CONSTRAINT subaccounts_pkey PRIMARY KEY (id);


--
-- Name: transfer_match_legs transfer_match_legs_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.transfer_match_legs
    ADD CONSTRAINT transfer_match_legs_pkey PRIMARY KEY (journal_id);


--
-- Name: transfer_matches transfer_matches_fingerprint_key; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.transfer_matches
    ADD CONSTRAINT transfer_matches_fingerprint_key UNIQUE (fingerprint);


--
-- Name: transfer_matches transfer_matches_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.transfer_matches
    ADD CONSTRAINT transfer_matches_pkey PRIMARY KEY (id);


--
-- Name: trial_balances trial_balances_pkey; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.trial_balances
    ADD CONSTRAINT trial_balances_pkey PRIMARY KEY (id);


--
-- Name: trial_balances trial_balances_unique_constraint; Type: CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.trial_balances
    ADD CONSTRAINT trial_balances_unique_constraint UNIQUE (subaccount, period, period_interval);


--
-- Name: activity_observations activity_observations_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.activity_observations
    ADD CONSTRAINT activity_observations_pkey PRIMARY KEY (export_id, source_id);


--
-- Name: brokerage_accounts brokerage_accounts_label_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_label_key UNIQUE (label);


--
-- Name: brokerage_accounts brokerage_accounts_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_pkey PRIMARY KEY (id);


--
-- Name: brokerage_activity brokerage_activity_account_id_posted_on_economic_digest_day_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_activity
    ADD CONSTRAINT brokerage_activity_account_id_posted_on_economic_digest_day_key UNIQUE (account_id, posted_on, economic_digest, day_ordinal);


--
-- Name: brokerage_activity brokerage_activity_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_activity
    ADD CONSTRAINT brokerage_activity_pkey PRIMARY KEY (source_id);


--
-- Name: brokerage_exports brokerage_exports_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_exports
    ADD CONSTRAINT brokerage_exports_pkey PRIMARY KEY (id);


--
-- Name: brokerage_exports brokerage_exports_sha256_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_exports
    ADD CONSTRAINT brokerage_exports_sha256_key UNIQUE (sha256);


--
-- Name: brokerage_journal_sources brokerage_journal_sources_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_journal_sources
    ADD CONSTRAINT brokerage_journal_sources_pkey PRIMARY KEY (journal_entry_id);


--
-- Name: brokerage_opening_lines brokerage_opening_lines_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_opening_lines
    ADD CONSTRAINT brokerage_opening_lines_pkey PRIMARY KEY (source_id, symbol);


--
-- Name: brokerage_openings brokerage_openings_account_id_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_openings
    ADD CONSTRAINT brokerage_openings_account_id_key UNIQUE (account_id);


--
-- Name: brokerage_openings brokerage_openings_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_openings
    ADD CONSTRAINT brokerage_openings_pkey PRIMARY KEY (source_id);


--
-- Name: brokerage_sources brokerage_sources_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_sources
    ADD CONSTRAINT brokerage_sources_pkey PRIMARY KEY (source_id);


--
-- Name: brokerage_sources brokerage_sources_public_id_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_sources
    ADD CONSTRAINT brokerage_sources_public_id_key UNIQUE (public_id);


--
-- Name: position_lines position_lines_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_lines
    ADD CONSTRAINT position_lines_pkey PRIMARY KEY (id);


--
-- Name: position_snapshots position_snapshots_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_snapshots
    ADD CONSTRAINT position_snapshots_pkey PRIMARY KEY (id);


--
-- Name: price_request_universe price_request_universe_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.price_request_universe
    ADD CONSTRAINT price_request_universe_pkey PRIMARY KEY (ticker);


--
-- Name: realized_lots realized_lots_account_id_lot_digest_lot_ordinal_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.realized_lots
    ADD CONSTRAINT realized_lots_account_id_lot_digest_lot_ordinal_key UNIQUE (account_id, lot_digest, lot_ordinal);


--
-- Name: realized_lots realized_lots_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.realized_lots
    ADD CONSTRAINT realized_lots_pkey PRIMARY KEY (id);


--
-- Name: security_prices security_prices_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.security_prices
    ADD CONSTRAINT security_prices_pkey PRIMARY KEY (id);


--
-- Name: security_prices security_prices_unique_constraint; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.security_prices
    ADD CONSTRAINT security_prices_unique_constraint UNIQUE (ticker, date);


--
-- Name: share_move_proposals share_move_proposals_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.share_move_proposals
    ADD CONSTRAINT share_move_proposals_pkey PRIMARY KEY (id);


--
-- Name: share_move_rules share_move_rules_action_symbol_description_direction_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.share_move_rules
    ADD CONSTRAINT share_move_rules_action_symbol_description_direction_key UNIQUE (action, symbol, description, direction);


--
-- Name: share_move_rules share_move_rules_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.share_move_rules
    ADD CONSTRAINT share_move_rules_pkey PRIMARY KEY (id);


--
-- Name: symbol_map symbol_map_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.symbol_map
    ADD CONSTRAINT symbol_map_pkey PRIMARY KEY (provider_symbol);


--
-- Name: valuation_points valuation_points_account_id_valuation_date_key; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.valuation_points
    ADD CONSTRAINT valuation_points_account_id_valuation_date_key UNIQUE (account_id, valuation_date);


--
-- Name: valuation_points valuation_points_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.valuation_points
    ADD CONSTRAINT valuation_points_pkey PRIMARY KEY (source_id);


--
-- Name: wash_adjustments wash_adjustments_pkey; Type: CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.wash_adjustments
    ADD CONSTRAINT wash_adjustments_pkey PRIMARY KEY (source_id);


--
-- Name: acctfrom acctfrom_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.acctfrom
    ADD CONSTRAINT acctfrom_pkey PRIMARY KEY (id);


--
-- Name: acctto acctto_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.acctto
    ADD CONSTRAINT acctto_pkey PRIMARY KEY (id);


--
-- Name: availbal availbal_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.availbal
    ADD CONSTRAINT availbal_pkey PRIMARY KEY (acctfrom_id, dtasof);


--
-- Name: bal bal_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bal
    ADD CONSTRAINT bal_pkey PRIMARY KEY (name, acctfrom_id, dtasof);


--
-- Name: bankacctfrom bankacctfrom_bankid_acctid_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bankacctfrom
    ADD CONSTRAINT bankacctfrom_bankid_acctid_key UNIQUE (bankid, acctid);


--
-- Name: bankacctfrom bankacctfrom_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bankacctfrom
    ADD CONSTRAINT bankacctfrom_pkey PRIMARY KEY (id);


--
-- Name: bankacctto bankacctto_bankid_acctid_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bankacctto
    ADD CONSTRAINT bankacctto_bankid_acctid_key UNIQUE (bankid, acctid);


--
-- Name: bankacctto bankacctto_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bankacctto
    ADD CONSTRAINT bankacctto_pkey PRIMARY KEY (id);


--
-- Name: buydebt buydebt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buydebt
    ADD CONSTRAINT buydebt_pkey PRIMARY KEY (id);


--
-- Name: buymf buymf_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buymf
    ADD CONSTRAINT buymf_pkey PRIMARY KEY (id);


--
-- Name: buyopt buyopt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buyopt
    ADD CONSTRAINT buyopt_pkey PRIMARY KEY (id);


--
-- Name: buyother buyother_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buyother
    ADD CONSTRAINT buyother_pkey PRIMARY KEY (id);


--
-- Name: buystock buystock_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buystock
    ADD CONSTRAINT buystock_pkey PRIMARY KEY (id);


--
-- Name: ccacctfrom ccacctfrom_acctid_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ccacctfrom
    ADD CONSTRAINT ccacctfrom_acctid_key UNIQUE (acctid);


--
-- Name: ccacctfrom ccacctfrom_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ccacctfrom
    ADD CONSTRAINT ccacctfrom_pkey PRIMARY KEY (id);


--
-- Name: ccacctto ccacctto_acctid_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ccacctto
    ADD CONSTRAINT ccacctto_acctid_key UNIQUE (acctid);


--
-- Name: ccacctto ccacctto_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ccacctto
    ADD CONSTRAINT ccacctto_pkey PRIMARY KEY (id);


--
-- Name: closureopt closureopt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.closureopt
    ADD CONSTRAINT closureopt_pkey PRIMARY KEY (id);


--
-- Name: debtinfo debtinfo_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.debtinfo
    ADD CONSTRAINT debtinfo_pkey PRIMARY KEY (id);


--
-- Name: fiportion fiportion_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.fiportion
    ADD CONSTRAINT fiportion_pkey PRIMARY KEY (mfinfo_id, fiassetclass);


--
-- Name: income income_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.income
    ADD CONSTRAINT income_pkey PRIMARY KEY (id);


--
-- Name: invacctfrom invacctfrom_brokerid_acctid_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invacctfrom
    ADD CONSTRAINT invacctfrom_brokerid_acctid_key UNIQUE (brokerid, acctid);


--
-- Name: invacctfrom invacctfrom_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invacctfrom
    ADD CONSTRAINT invacctfrom_pkey PRIMARY KEY (id);


--
-- Name: invbal invbal_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invbal
    ADD CONSTRAINT invbal_pkey PRIMARY KEY (acctfrom_id, dtasof);


--
-- Name: invbanktran invbanktran_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invbanktran
    ADD CONSTRAINT invbanktran_pkey PRIMARY KEY (acctfrom_id, fitid);


--
-- Name: invexpense invexpense_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invexpense
    ADD CONSTRAINT invexpense_pkey PRIMARY KEY (id);


--
-- Name: invpos invpos_acctfrom_id_secinfo_id_dtasof_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invpos
    ADD CONSTRAINT invpos_acctfrom_id_secinfo_id_dtasof_key UNIQUE (acctfrom_id, secinfo_id, dtasof);


--
-- Name: invpos invpos_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invpos
    ADD CONSTRAINT invpos_pkey PRIMARY KEY (id);


--
-- Name: invtran invtran_acctfrom_id_fitid_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invtran
    ADD CONSTRAINT invtran_acctfrom_id_fitid_key UNIQUE (acctfrom_id, fitid);


--
-- Name: invtran invtran_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invtran
    ADD CONSTRAINT invtran_pkey PRIMARY KEY (id);


--
-- Name: jrnlfund jrnlfund_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.jrnlfund
    ADD CONSTRAINT jrnlfund_pkey PRIMARY KEY (id);


--
-- Name: jrnlsec jrnlsec_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.jrnlsec
    ADD CONSTRAINT jrnlsec_pkey PRIMARY KEY (id);


--
-- Name: ledgerbal ledgerbal_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ledgerbal
    ADD CONSTRAINT ledgerbal_pkey PRIMARY KEY (acctfrom_id, dtasof);


--
-- Name: margininterest margininterest_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.margininterest
    ADD CONSTRAINT margininterest_pkey PRIMARY KEY (id);


--
-- Name: mfinfo mfinfo_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.mfinfo
    ADD CONSTRAINT mfinfo_pkey PRIMARY KEY (id);


--
-- Name: optinfo optinfo_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.optinfo
    ADD CONSTRAINT optinfo_pkey PRIMARY KEY (id);


--
-- Name: otherinfo otherinfo_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.otherinfo
    ADD CONSTRAINT otherinfo_pkey PRIMARY KEY (id);


--
-- Name: payee payee_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.payee
    ADD CONSTRAINT payee_pkey PRIMARY KEY (name);


--
-- Name: portion portion_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.portion
    ADD CONSTRAINT portion_pkey PRIMARY KEY (mfinfo_id, assetclass);


--
-- Name: posdebt posdebt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posdebt
    ADD CONSTRAINT posdebt_pkey PRIMARY KEY (id);


--
-- Name: posmf posmf_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posmf
    ADD CONSTRAINT posmf_pkey PRIMARY KEY (id);


--
-- Name: posopt posopt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posopt
    ADD CONSTRAINT posopt_pkey PRIMARY KEY (id);


--
-- Name: posother posother_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posother
    ADD CONSTRAINT posother_pkey PRIMARY KEY (id);


--
-- Name: posstock posstock_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posstock
    ADD CONSTRAINT posstock_pkey PRIMARY KEY (id);


--
-- Name: reinvest reinvest_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.reinvest
    ADD CONSTRAINT reinvest_pkey PRIMARY KEY (id);


--
-- Name: retofcap retofcap_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.retofcap
    ADD CONSTRAINT retofcap_pkey PRIMARY KEY (id);


--
-- Name: secinfo secinfo_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.secinfo
    ADD CONSTRAINT secinfo_pkey PRIMARY KEY (id);


--
-- Name: secinfo secinfo_uniqueid_uniqueidtype_key; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.secinfo
    ADD CONSTRAINT secinfo_uniqueid_uniqueidtype_key UNIQUE (uniqueid, uniqueidtype);


--
-- Name: selldebt selldebt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.selldebt
    ADD CONSTRAINT selldebt_pkey PRIMARY KEY (id);


--
-- Name: sellmf sellmf_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellmf
    ADD CONSTRAINT sellmf_pkey PRIMARY KEY (id);


--
-- Name: sellopt sellopt_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellopt
    ADD CONSTRAINT sellopt_pkey PRIMARY KEY (id);


--
-- Name: sellother sellother_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellother
    ADD CONSTRAINT sellother_pkey PRIMARY KEY (id);


--
-- Name: sellstock sellstock_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellstock
    ADD CONSTRAINT sellstock_pkey PRIMARY KEY (id);


--
-- Name: split split_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.split
    ADD CONSTRAINT split_pkey PRIMARY KEY (id);


--
-- Name: stmttrn stmttrn_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.stmttrn
    ADD CONSTRAINT stmttrn_pkey PRIMARY KEY (acctfrom_id, fitid);


--
-- Name: stockinfo stockinfo_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.stockinfo
    ADD CONSTRAINT stockinfo_pkey PRIMARY KEY (id);


--
-- Name: transfer transfer_pkey; Type: CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.transfer
    ADD CONSTRAINT transfer_pkey PRIMARY KEY (id);


--
-- Name: paystub_items earnings_unique_constraint; Type: CONSTRAINT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystub_items
    ADD CONSTRAINT earnings_unique_constraint UNIQUE (paystub_id, description);


--
-- Name: paystub_items paystub_items_pkey; Type: CONSTRAINT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystub_items
    ADD CONSTRAINT paystub_items_pkey PRIMARY KEY (id);


--
-- Name: paystubs paystubs_pkey; Type: CONSTRAINT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystubs
    ADD CONSTRAINT paystubs_pkey PRIMARY KEY (id);


--
-- Name: paystubs paystubs_unique_constraint; Type: CONSTRAINT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystubs
    ADD CONSTRAINT paystubs_unique_constraint UNIQUE (employer_name, period_beginning, period_ending);


--
-- Name: tax_tags tax_tags_name_key; Type: CONSTRAINT; Schema: tax; Owner: -
--

ALTER TABLE ONLY tax.tax_tags
    ADD CONSTRAINT tax_tags_name_key UNIQUE (name);


--
-- Name: tax_tags tax_tags_pkey; Type: CONSTRAINT; Schema: tax; Owner: -
--

ALTER TABLE ONLY tax.tax_tags
    ADD CONSTRAINT tax_tags_pkey PRIMARY KEY (id);


--
-- Name: chase_browser_one_active_run; Type: INDEX; Schema: admin; Owner: -
--

CREATE UNIQUE INDEX chase_browser_one_active_run ON admin.chase_browser_runs USING btree (singleton_slot) WHERE (status IN ('queued', 'signing_in', 'waiting_for_mfa', 'choosing_accounts', 'exporting', 'importing'));


--
-- Name: chase_local_events_run_cursor; Type: INDEX; Schema: admin; Owner: -
--

CREATE INDEX chase_local_events_run_cursor ON admin.chase_local_events USING btree (run_id, id);


--
-- Name: amazon_item_one_active_post_run; Type: INDEX; Schema: amazon; Owner: -
--

CREATE UNIQUE INDEX amazon_item_one_active_post_run ON amazon.item_post_runs USING btree (run_id) WHERE (state IN ('queued', 'running'));


--
-- Name: amazon_one_active_collection; Type: INDEX; Schema: amazon; Owner: -
--

CREATE UNIQUE INDEX amazon_one_active_collection ON amazon.collection_runs USING btree (state) WHERE ((state)::text = 'running'::text);


--
-- Name: ix_amazon_item_jobs_run_id; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_item_jobs_run_id ON amazon.item_jobs USING btree (run_id);


--
-- Name: ix_amazon_item_post_runs_run_id; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_item_post_runs_run_id ON amazon.item_post_runs USING btree (run_id);


--
-- Name: ix_amazon_policy_answers_plan_id; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_policy_answers_plan_id ON amazon.policy_answers USING btree (plan_id);


--
-- Name: ix_amazon_policy_answers_question_key; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_policy_answers_question_key ON amazon.policy_answers USING btree (question_key);


--
-- Name: ix_amazon_product_rules_product_digest; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_product_rules_product_digest ON amazon.product_rules USING btree (product_digest);


--
-- Name: ix_amazon_suspense_facts_item_id; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_suspense_facts_item_id ON amazon.suspense_facts USING btree (item_id);


--
-- Name: ix_amazon_suspense_facts_plan_id; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_suspense_facts_plan_id ON amazon.suspense_facts USING btree (plan_id);


--
-- Name: ix_amazon_suspense_items_plan_id; Type: INDEX; Schema: amazon; Owner: -
--

CREATE INDEX ix_amazon_suspense_items_plan_id ON amazon.suspense_items USING btree (plan_id);


--
-- Name: ofx_journal_sources_source; Type: INDEX; Schema: bookkeeping; Owner: -
--

CREATE INDEX ofx_journal_sources_source ON bookkeeping.ofx_journal_sources USING btree (source_id);


--
-- Name: brokerage_journal_sources_source; Type: INDEX; Schema: investments; Owner: -
--

CREATE INDEX brokerage_journal_sources_source ON investments.brokerage_journal_sources USING btree (source_id);


--
-- Name: item_post_runs immutable_item_post_request; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER immutable_item_post_request BEFORE UPDATE ON amazon.item_post_runs FOR EACH ROW EXECUTE FUNCTION amazon.reject_item_post_request_mutation();


--
-- Name: policy_answers immutable_policy_answer; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER immutable_policy_answer BEFORE DELETE OR UPDATE ON amazon.policy_answers FOR EACH ROW EXECUTE FUNCTION amazon.reject_policy_answer_mutation();


--
-- Name: product_rules immutable_suspense_evidence; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER immutable_suspense_evidence BEFORE DELETE OR UPDATE ON amazon.product_rules FOR EACH ROW EXECUTE FUNCTION amazon.reject_suspense_evidence_mutation();


--
-- Name: suspense_facts immutable_suspense_evidence; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER immutable_suspense_evidence BEFORE DELETE OR UPDATE ON amazon.suspense_facts FOR EACH ROW EXECUTE FUNCTION amazon.reject_suspense_evidence_mutation();


--
-- Name: suspense_items immutable_suspense_evidence; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER immutable_suspense_evidence BEFORE DELETE OR UPDATE ON amazon.suspense_items FOR EACH ROW EXECUTE FUNCTION amazon.reject_suspense_evidence_mutation();


--
-- Name: suspense_plans immutable_suspense_plan; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER immutable_suspense_plan BEFORE DELETE OR UPDATE ON amazon.suspense_plans FOR EACH ROW EXECUTE FUNCTION amazon.reject_suspense_plan_mutation();


--
-- Name: items source_activity_lock; Type: TRIGGER; Schema: amazon; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT OR DELETE OR UPDATE ON amazon.items FOR EACH ROW EXECUTE FUNCTION bookkeeping.lock_amazon_source_activity();


--
-- Name: journal_entries brokerage_journal_link; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER brokerage_journal_link AFTER INSERT OR UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION investments.link_brokerage_journal();


--
-- Name: closed_periods closed_period_immutable; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER closed_period_immutable BEFORE DELETE OR UPDATE ON bookkeeping.closed_periods FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_closed_record_change();


--
-- Name: journal_entries guard_brokerage_subaccounts; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER guard_brokerage_subaccounts BEFORE INSERT OR UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION investments.guard_brokerage_subaccounts();


--
-- Name: accounting_evaluation_cases immutable_evidence; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON bookkeeping.accounting_evaluation_cases FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation();


--
-- Name: accounting_evidence immutable_evidence; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON bookkeeping.accounting_evidence FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation();


--
-- Name: bank_statements immutable_evidence; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON bookkeeping.bank_statements FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation();


--
-- Name: opening_balances immutable_evidence; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON bookkeeping.opening_balances FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation();


--
-- Name: transfer_match_legs immutable_evidence; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON bookkeeping.transfer_match_legs FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation();


--
-- Name: transfer_matches immutable_evidence; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON bookkeeping.transfer_matches FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation();


--
-- Name: journal_entries journal_closed_period_guard; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER journal_closed_period_guard BEFORE INSERT OR DELETE OR UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_closed_period_change();


--
-- Name: journal_entry_events journal_event_immutable; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER journal_event_immutable BEFORE DELETE OR UPDATE ON bookkeeping.journal_entry_events FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_journal_event_change();


--
-- Name: journal_entries journal_event_trigger; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER journal_event_trigger AFTER INSERT OR DELETE OR UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION bookkeeping.record_journal_event();


--
-- Name: journal_entries journal_source_link; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER journal_source_link AFTER INSERT OR UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION bookkeeping.link_ofx_journal();


--
-- Name: journal_entries mapping_decision_immutable; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER mapping_decision_immutable BEFORE UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION bookkeeping.preserve_mapping_decision();


--
-- Name: ofx_source_identities ofx_identity_immutable; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER ofx_identity_immutable BEFORE UPDATE ON bookkeeping.ofx_source_identities FOR EACH ROW EXECUTE FUNCTION bookkeeping.preserve_ofx_identity();


--
-- Name: journal_entries subaccount_insert_trigger; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER subaccount_insert_trigger AFTER INSERT OR DELETE OR UPDATE ON bookkeeping.journal_entries FOR EACH ROW EXECUTE FUNCTION bookkeeping.subaccount_insert_triggered();


--
-- Name: ofx_journal_sources validate_ofx_journal_link; Type: TRIGGER; Schema: bookkeeping; Owner: -
--

CREATE TRIGGER validate_ofx_journal_link BEFORE INSERT OR UPDATE ON bookkeeping.ofx_journal_sources FOR EACH ROW EXECUTE FUNCTION bookkeeping.validate_ofx_journal_link();


--
-- Name: price_request_universe append_only_universe; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER append_only_universe BEFORE DELETE OR UPDATE ON investments.price_request_universe FOR EACH ROW EXECUTE FUNCTION investments.append_only_universe();


--
-- Name: brokerage_sources assign_public_id; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER assign_public_id BEFORE INSERT ON investments.brokerage_sources FOR EACH ROW EXECUTE FUNCTION investments.assign_public_id();


--
-- Name: brokerage_accounts freeze_brokerage_account; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER freeze_brokerage_account BEFORE DELETE OR UPDATE ON investments.brokerage_accounts FOR EACH ROW EXECUTE FUNCTION investments.freeze_brokerage_account();


--
-- Name: activity_observations immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.activity_observations FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: brokerage_activity immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.brokerage_activity FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: brokerage_exports immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.brokerage_exports FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: brokerage_opening_lines immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.brokerage_opening_lines FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: brokerage_openings immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.brokerage_openings FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: brokerage_sources immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.brokerage_sources FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: position_lines immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.position_lines FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: position_snapshots immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.position_snapshots FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: realized_lots immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.realized_lots FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: share_move_proposals immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.share_move_proposals FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: share_move_rules immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.share_move_rules FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: valuation_points immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.valuation_points FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: wash_adjustments immutable_evidence; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER immutable_evidence BEFORE DELETE OR UPDATE ON investments.wash_adjustments FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();


--
-- Name: activity_observations source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.activity_observations FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: brokerage_activity source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.brokerage_activity FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: brokerage_exports source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.brokerage_exports FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: brokerage_opening_lines source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.brokerage_opening_lines FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: brokerage_openings source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.brokerage_openings FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: brokerage_sources source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.brokerage_sources FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: position_lines source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.position_lines FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: position_snapshots source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.position_snapshots FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: realized_lots source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.realized_lots FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: share_move_proposals source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.share_move_proposals FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: share_move_rules source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.share_move_rules FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: valuation_points source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.valuation_points FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: wash_adjustments source_activity_lock; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.wash_adjustments FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();


--
-- Name: brokerage_journal_sources validate_brokerage_journal_link; Type: TRIGGER; Schema: investments; Owner: -
--

CREATE TRIGGER validate_brokerage_journal_link BEFORE INSERT OR UPDATE ON investments.brokerage_journal_sources FOR EACH ROW EXECUTE FUNCTION investments.validate_brokerage_journal_link();


--
-- Name: stmttrn register_ofx_source; Type: TRIGGER; Schema: ofx; Owner: -
--

CREATE TRIGGER register_ofx_source AFTER INSERT ON ofx.stmttrn FOR EACH ROW EXECUTE FUNCTION bookkeeping.register_ofx_source();


--
-- Name: stmttrn source_activity_lock; Type: TRIGGER; Schema: ofx; Owner: -
--

CREATE TRIGGER source_activity_lock BEFORE INSERT OR DELETE OR UPDATE ON ofx.stmttrn FOR EACH ROW EXECUTE FUNCTION bookkeeping.lock_source_activity();


--
-- Name: chase_browser_credentials chase_browser_credentials_connection_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_credentials
    ADD CONSTRAINT chase_browser_credentials_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES admin.connections(id);


--
-- Name: chase_browser_runs chase_browser_runs_connection_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_runs
    ADD CONSTRAINT chase_browser_runs_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES admin.connections(id);


--
-- Name: chase_browser_runs chase_browser_runs_requested_by_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_browser_runs
    ADD CONSTRAINT chase_browser_runs_requested_by_id_fkey FOREIGN KEY (requested_by_id) REFERENCES admin.users(id);


--
-- Name: chase_export_coverage chase_export_coverage_file_import_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_export_coverage
    ADD CONSTRAINT chase_export_coverage_file_import_id_fkey FOREIGN KEY (file_import_id) REFERENCES admin.ofx_file_imports(id) ON DELETE CASCADE;


--
-- Name: chase_local_events chase_local_events_run_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_local_events
    ADD CONSTRAINT chase_local_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES admin.chase_browser_runs(id) ON DELETE CASCADE;


--
-- Name: chase_local_preferences chase_local_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.chase_local_preferences
    ADD CONSTRAINT chase_local_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES admin.users(id);


--
-- Name: connection_responses connection_responses_connection_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.connection_responses
    ADD CONSTRAINT connection_responses_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES admin.connections(id);


--
-- Name: mappings mappings_negative_credit_subaccount_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings
    ADD CONSTRAINT mappings_negative_credit_subaccount_id_fkey FOREIGN KEY (negative_credit_subaccount_id) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: mappings mappings_negative_debit_subaccount_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings
    ADD CONSTRAINT mappings_negative_debit_subaccount_id_fkey FOREIGN KEY (negative_debit_subaccount_id) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: mappings mappings_positive_credit_subaccount_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings
    ADD CONSTRAINT mappings_positive_credit_subaccount_id_fkey FOREIGN KEY (positive_credit_subaccount_id) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: mappings mappings_positive_debit_subaccount_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.mappings
    ADD CONSTRAINT mappings_positive_debit_subaccount_id_fkey FOREIGN KEY (positive_debit_subaccount_id) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: ofx_file_imports ofx_file_imports_connection_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.ofx_file_imports
    ADD CONSTRAINT ofx_file_imports_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES admin.connections(id);


--
-- Name: ofx_file_imports ofx_file_imports_response_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.ofx_file_imports
    ADD CONSTRAINT ofx_file_imports_response_id_fkey FOREIGN KEY (response_id) REFERENCES admin.connection_responses(id);


--
-- Name: roles_users roles_users_roles_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.roles_users
    ADD CONSTRAINT roles_users_roles_id_fkey FOREIGN KEY (roles_id) REFERENCES admin.roles(id);


--
-- Name: roles_users roles_users_users_id_fkey; Type: FK CONSTRAINT; Schema: admin; Owner: -
--

ALTER TABLE ONLY admin.roles_users
    ADD CONSTRAINT roles_users_users_id_fkey FOREIGN KEY (users_id) REFERENCES admin.users(id);


--
-- Name: evidence_bundles evidence_bundles_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.evidence_bundles
    ADD CONSTRAINT evidence_bundles_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: item_jobs item_jobs_decision_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_jobs
    ADD CONSTRAINT item_jobs_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: item_jobs item_jobs_run_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_jobs
    ADD CONSTRAINT item_jobs_run_id_fkey FOREIGN KEY (run_id) REFERENCES amazon.item_runs(id);


--
-- Name: item_post_runs item_post_runs_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_post_runs
    ADD CONSTRAINT item_post_runs_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: item_post_runs item_post_runs_run_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_post_runs
    ADD CONSTRAINT item_post_runs_run_id_fkey FOREIGN KEY (run_id) REFERENCES amazon.item_runs(id);


--
-- Name: item_runs item_runs_manifest_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.item_runs
    ADD CONSTRAINT item_runs_manifest_id_fkey FOREIGN KEY (manifest_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: items items_category_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.items
    ADD CONSTRAINT items_category_id_fkey FOREIGN KEY (category_id) REFERENCES amazon.categories(name);


--
-- Name: items items_order_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.items
    ADD CONSTRAINT items_order_id_fkey FOREIGN KEY (order_id) REFERENCES amazon.orders(id);


--
-- Name: policy_answers policy_answers_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers
    ADD CONSTRAINT policy_answers_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: policy_answers policy_answers_plan_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers
    ADD CONSTRAINT policy_answers_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES amazon.suspense_plans(id);


--
-- Name: policy_answers policy_answers_preview_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.policy_answers
    ADD CONSTRAINT policy_answers_preview_evidence_id_fkey FOREIGN KEY (preview_evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: product_rules product_rules_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.product_rules
    ADD CONSTRAINT product_rules_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: product_rules product_rules_supersedes_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.product_rules
    ADD CONSTRAINT product_rules_supersedes_id_fkey FOREIGN KEY (supersedes_id) REFERENCES amazon.product_rules(id);


--
-- Name: suspense_facts suspense_facts_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_facts
    ADD CONSTRAINT suspense_facts_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: suspense_facts suspense_facts_plan_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_facts
    ADD CONSTRAINT suspense_facts_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES amazon.suspense_plans(id);


--
-- Name: suspense_items suspense_items_plan_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_items
    ADD CONSTRAINT suspense_items_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES amazon.suspense_plans(id);


--
-- Name: suspense_items suspense_items_source_job_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_items
    ADD CONSTRAINT suspense_items_source_job_id_fkey FOREIGN KEY (source_job_id) REFERENCES amazon.item_jobs(id);


--
-- Name: suspense_plans suspense_plans_evidence_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans
    ADD CONSTRAINT suspense_plans_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: suspense_plans suspense_plans_review_run_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans
    ADD CONSTRAINT suspense_plans_review_run_id_fkey FOREIGN KEY (review_run_id) REFERENCES amazon.item_runs(id);


--
-- Name: suspense_plans suspense_plans_source_run_id_fkey; Type: FK CONSTRAINT; Schema: amazon; Owner: -
--

ALTER TABLE ONLY amazon.suspense_plans
    ADD CONSTRAINT suspense_plans_source_run_id_fkey FOREIGN KEY (source_run_id) REFERENCES amazon.item_runs(id);


--
-- Name: accounting_reviews accounting_reviews_mapping_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounting_reviews
    ADD CONSTRAINT accounting_reviews_mapping_id_fkey FOREIGN KEY (mapping_id) REFERENCES admin.mappings(id);


--
-- Name: accounts accounts_parent_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accounts
    ADD CONSTRAINT accounts_parent_fkey FOREIGN KEY (parent) REFERENCES bookkeeping.classifications(name);


--
-- Name: accruals accruals_balance_sheet_subaccount_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accruals
    ADD CONSTRAINT accruals_balance_sheet_subaccount_fkey FOREIGN KEY (balance_sheet_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: accruals accruals_income_statement_subaccount_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.accruals
    ADD CONSTRAINT accruals_income_statement_subaccount_fkey FOREIGN KEY (income_statement_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: bank_statements bank_statements_evidence_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.bank_statements
    ADD CONSTRAINT bank_statements_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: classifications classifications_parent_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.classifications
    ADD CONSTRAINT classifications_parent_fkey FOREIGN KEY (parent) REFERENCES bookkeeping.elements(name);


--
-- Name: journal_entries journal_entries_credit_subaccount_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entries
    ADD CONSTRAINT journal_entries_credit_subaccount_fkey FOREIGN KEY (credit_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: journal_entries journal_entries_debit_subaccount_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entries
    ADD CONSTRAINT journal_entries_debit_subaccount_fkey FOREIGN KEY (debit_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: journal_entries journal_entries_mapping_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.journal_entries
    ADD CONSTRAINT journal_entries_mapping_id_fkey FOREIGN KEY (mapping_id) REFERENCES admin.mappings(id);


--
-- Name: ofx_journal_sources ofx_journal_sources_journal_entry_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_journal_sources
    ADD CONSTRAINT ofx_journal_sources_journal_entry_id_fkey FOREIGN KEY (journal_entry_id) REFERENCES bookkeeping.journal_entries(id) ON DELETE CASCADE;


--
-- Name: ofx_journal_sources ofx_journal_sources_source_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_journal_sources
    ADD CONSTRAINT ofx_journal_sources_source_id_fkey FOREIGN KEY (source_id) REFERENCES bookkeeping.ofx_source_identities(source_id);


--
-- Name: ofx_source_identities ofx_source_identities_acctfrom_id_fitid_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.ofx_source_identities
    ADD CONSTRAINT ofx_source_identities_acctfrom_id_fitid_fkey FOREIGN KEY (acctfrom_id, fitid) REFERENCES ofx.stmttrn(acctfrom_id, fitid) ON DELETE CASCADE;


--
-- Name: opening_balances opening_balances_journal_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.opening_balances
    ADD CONSTRAINT opening_balances_journal_id_fkey FOREIGN KEY (journal_id) REFERENCES bookkeeping.journal_entries(id);


--
-- Name: subaccounts subaccounts_parent_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.subaccounts
    ADD CONSTRAINT subaccounts_parent_fkey FOREIGN KEY (parent) REFERENCES bookkeeping.accounts(name);


--
-- Name: transfer_match_legs transfer_match_legs_journal_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.transfer_match_legs
    ADD CONSTRAINT transfer_match_legs_journal_id_fkey FOREIGN KEY (journal_id) REFERENCES bookkeeping.journal_entries(id);


--
-- Name: transfer_match_legs transfer_match_legs_match_id_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.transfer_match_legs
    ADD CONSTRAINT transfer_match_legs_match_id_fkey FOREIGN KEY (match_id) REFERENCES bookkeeping.transfer_matches(id);


--
-- Name: trial_balances trial_balances_subaccount_fkey; Type: FK CONSTRAINT; Schema: bookkeeping; Owner: -
--

ALTER TABLE ONLY bookkeeping.trial_balances
    ADD CONSTRAINT trial_balances_subaccount_fkey FOREIGN KEY (subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: activity_observations activity_observations_export_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.activity_observations
    ADD CONSTRAINT activity_observations_export_id_fkey FOREIGN KEY (export_id) REFERENCES investments.brokerage_exports(id);


--
-- Name: activity_observations activity_observations_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.activity_observations
    ADD CONSTRAINT activity_observations_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_activity(source_id);


--
-- Name: brokerage_accounts brokerage_accounts_cash_subaccount_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_cash_subaccount_fkey FOREIGN KEY (cash_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: brokerage_accounts brokerage_accounts_oci_subaccount_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_oci_subaccount_fkey FOREIGN KEY (oci_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: brokerage_accounts brokerage_accounts_securities_subaccount_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_securities_subaccount_fkey FOREIGN KEY (securities_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: brokerage_accounts brokerage_accounts_valuation_subaccount_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_valuation_subaccount_fkey FOREIGN KEY (valuation_subaccount) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: brokerage_activity brokerage_activity_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_activity
    ADD CONSTRAINT brokerage_activity_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: brokerage_activity brokerage_activity_first_export_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_activity
    ADD CONSTRAINT brokerage_activity_first_export_id_fkey FOREIGN KEY (first_export_id) REFERENCES investments.brokerage_exports(id);


--
-- Name: brokerage_activity brokerage_activity_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_activity
    ADD CONSTRAINT brokerage_activity_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_sources(source_id);


--
-- Name: brokerage_exports brokerage_exports_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_exports
    ADD CONSTRAINT brokerage_exports_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: brokerage_exports brokerage_exports_evidence_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_exports
    ADD CONSTRAINT brokerage_exports_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: brokerage_journal_sources brokerage_journal_sources_journal_entry_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_journal_sources
    ADD CONSTRAINT brokerage_journal_sources_journal_entry_id_fkey FOREIGN KEY (journal_entry_id) REFERENCES bookkeeping.journal_entries(id) ON DELETE CASCADE;


--
-- Name: brokerage_journal_sources brokerage_journal_sources_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_journal_sources
    ADD CONSTRAINT brokerage_journal_sources_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_sources(source_id);


--
-- Name: brokerage_opening_lines brokerage_opening_lines_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_opening_lines
    ADD CONSTRAINT brokerage_opening_lines_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_openings(source_id);


--
-- Name: brokerage_openings brokerage_openings_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_openings
    ADD CONSTRAINT brokerage_openings_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: brokerage_openings brokerage_openings_evidence_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_openings
    ADD CONSTRAINT brokerage_openings_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: brokerage_openings brokerage_openings_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_openings
    ADD CONSTRAINT brokerage_openings_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES investments.position_snapshots(id);


--
-- Name: brokerage_openings brokerage_openings_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_openings
    ADD CONSTRAINT brokerage_openings_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_sources(source_id);


--
-- Name: brokerage_sources brokerage_sources_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.brokerage_sources
    ADD CONSTRAINT brokerage_sources_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: position_lines position_lines_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_lines
    ADD CONSTRAINT position_lines_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES investments.position_snapshots(id);


--
-- Name: position_snapshots position_snapshots_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_snapshots
    ADD CONSTRAINT position_snapshots_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: position_snapshots position_snapshots_export_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.position_snapshots
    ADD CONSTRAINT position_snapshots_export_id_fkey FOREIGN KEY (export_id) REFERENCES investments.brokerage_exports(id);


--
-- Name: realized_lots realized_lots_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.realized_lots
    ADD CONSTRAINT realized_lots_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: realized_lots realized_lots_export_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.realized_lots
    ADD CONSTRAINT realized_lots_export_id_fkey FOREIGN KEY (export_id) REFERENCES investments.brokerage_exports(id);


--
-- Name: realized_lots realized_lots_supersedes_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.realized_lots
    ADD CONSTRAINT realized_lots_supersedes_id_fkey FOREIGN KEY (supersedes_id) REFERENCES investments.realized_lots(id);


--
-- Name: share_move_rules share_move_rules_proposal_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.share_move_rules
    ADD CONSTRAINT share_move_rules_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES investments.share_move_proposals(id);


--
-- Name: valuation_points valuation_points_account_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.valuation_points
    ADD CONSTRAINT valuation_points_account_id_fkey FOREIGN KEY (account_id) REFERENCES investments.brokerage_accounts(id);


--
-- Name: valuation_points valuation_points_evidence_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.valuation_points
    ADD CONSTRAINT valuation_points_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES bookkeeping.accounting_evidence(id);


--
-- Name: valuation_points valuation_points_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.valuation_points
    ADD CONSTRAINT valuation_points_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_sources(source_id);


--
-- Name: wash_adjustments wash_adjustments_lot_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.wash_adjustments
    ADD CONSTRAINT wash_adjustments_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES investments.realized_lots(id);


--
-- Name: wash_adjustments wash_adjustments_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.wash_adjustments
    ADD CONSTRAINT wash_adjustments_source_id_fkey FOREIGN KEY (source_id) REFERENCES investments.brokerage_sources(source_id);


--
-- Name: wash_adjustments wash_adjustments_trigger_source_id_fkey; Type: FK CONSTRAINT; Schema: investments; Owner: -
--

ALTER TABLE ONLY investments.wash_adjustments
    ADD CONSTRAINT wash_adjustments_trigger_source_id_fkey FOREIGN KEY (trigger_source_id) REFERENCES investments.brokerage_activity(source_id);


--
-- Name: availbal availbal_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.availbal
    ADD CONSTRAINT availbal_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: bal bal_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bal
    ADD CONSTRAINT bal_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: bankacctfrom bankacctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bankacctfrom
    ADD CONSTRAINT bankacctfrom_id_fkey FOREIGN KEY (id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: bankacctto bankacctto_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.bankacctto
    ADD CONSTRAINT bankacctto_id_fkey FOREIGN KEY (id) REFERENCES ofx.acctto(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buydebt buydebt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buydebt
    ADD CONSTRAINT buydebt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buydebt buydebt_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buydebt
    ADD CONSTRAINT buydebt_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buymf buymf_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buymf
    ADD CONSTRAINT buymf_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buymf buymf_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buymf
    ADD CONSTRAINT buymf_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buyopt buyopt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buyopt
    ADD CONSTRAINT buyopt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buyopt buyopt_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buyopt
    ADD CONSTRAINT buyopt_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buyother buyother_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buyother
    ADD CONSTRAINT buyother_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buyother buyother_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buyother
    ADD CONSTRAINT buyother_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buystock buystock_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buystock
    ADD CONSTRAINT buystock_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: buystock buystock_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.buystock
    ADD CONSTRAINT buystock_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: ccacctfrom ccacctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ccacctfrom
    ADD CONSTRAINT ccacctfrom_id_fkey FOREIGN KEY (id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: ccacctto ccacctto_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ccacctto
    ADD CONSTRAINT ccacctto_id_fkey FOREIGN KEY (id) REFERENCES ofx.acctto(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: closureopt closureopt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.closureopt
    ADD CONSTRAINT closureopt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: closureopt closureopt_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.closureopt
    ADD CONSTRAINT closureopt_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: debtinfo debtinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.debtinfo
    ADD CONSTRAINT debtinfo_id_fkey FOREIGN KEY (id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: fiportion fiportion_mfinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.fiportion
    ADD CONSTRAINT fiportion_mfinfo_id_fkey FOREIGN KEY (mfinfo_id) REFERENCES ofx.mfinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: income income_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.income
    ADD CONSTRAINT income_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: income income_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.income
    ADD CONSTRAINT income_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invacctfrom invacctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invacctfrom
    ADD CONSTRAINT invacctfrom_id_fkey FOREIGN KEY (id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invbal invbal_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invbal
    ADD CONSTRAINT invbal_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.invacctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invbanktran invbanktran_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invbanktran
    ADD CONSTRAINT invbanktran_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.invacctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invbanktran invbanktran_acctto_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invbanktran
    ADD CONSTRAINT invbanktran_acctto_id_fkey FOREIGN KEY (acctto_id) REFERENCES ofx.acctto(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invbanktran invbanktran_payee_name_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invbanktran
    ADD CONSTRAINT invbanktran_payee_name_fkey FOREIGN KEY (payee_name) REFERENCES ofx.payee(name) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invexpense invexpense_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invexpense
    ADD CONSTRAINT invexpense_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invexpense invexpense_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invexpense
    ADD CONSTRAINT invexpense_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invpos invpos_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invpos
    ADD CONSTRAINT invpos_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.invacctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invpos invpos_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invpos
    ADD CONSTRAINT invpos_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invtran invtran_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.invtran
    ADD CONSTRAINT invtran_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.invacctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: jrnlfund jrnlfund_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.jrnlfund
    ADD CONSTRAINT jrnlfund_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: jrnlsec jrnlsec_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.jrnlsec
    ADD CONSTRAINT jrnlsec_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: jrnlsec jrnlsec_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.jrnlsec
    ADD CONSTRAINT jrnlsec_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: ledgerbal ledgerbal_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.ledgerbal
    ADD CONSTRAINT ledgerbal_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: margininterest margininterest_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.margininterest
    ADD CONSTRAINT margininterest_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: mfinfo mfinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.mfinfo
    ADD CONSTRAINT mfinfo_id_fkey FOREIGN KEY (id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: optinfo optinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.optinfo
    ADD CONSTRAINT optinfo_id_fkey FOREIGN KEY (id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: otherinfo otherinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.otherinfo
    ADD CONSTRAINT otherinfo_id_fkey FOREIGN KEY (id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: portion portion_mfinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.portion
    ADD CONSTRAINT portion_mfinfo_id_fkey FOREIGN KEY (mfinfo_id) REFERENCES ofx.mfinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: posdebt posdebt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posdebt
    ADD CONSTRAINT posdebt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invpos(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: posmf posmf_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posmf
    ADD CONSTRAINT posmf_id_fkey FOREIGN KEY (id) REFERENCES ofx.invpos(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: posopt posopt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posopt
    ADD CONSTRAINT posopt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invpos(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: posother posother_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posother
    ADD CONSTRAINT posother_id_fkey FOREIGN KEY (id) REFERENCES ofx.invpos(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: posstock posstock_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.posstock
    ADD CONSTRAINT posstock_id_fkey FOREIGN KEY (id) REFERENCES ofx.invpos(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: reinvest reinvest_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.reinvest
    ADD CONSTRAINT reinvest_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: reinvest reinvest_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.reinvest
    ADD CONSTRAINT reinvest_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: retofcap retofcap_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.retofcap
    ADD CONSTRAINT retofcap_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: retofcap retofcap_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.retofcap
    ADD CONSTRAINT retofcap_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: selldebt selldebt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.selldebt
    ADD CONSTRAINT selldebt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: selldebt selldebt_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.selldebt
    ADD CONSTRAINT selldebt_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellmf sellmf_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellmf
    ADD CONSTRAINT sellmf_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellmf sellmf_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellmf
    ADD CONSTRAINT sellmf_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellopt sellopt_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellopt
    ADD CONSTRAINT sellopt_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellopt sellopt_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellopt
    ADD CONSTRAINT sellopt_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellother sellother_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellother
    ADD CONSTRAINT sellother_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellother sellother_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellother
    ADD CONSTRAINT sellother_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellstock sellstock_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellstock
    ADD CONSTRAINT sellstock_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: sellstock sellstock_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.sellstock
    ADD CONSTRAINT sellstock_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: split split_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.split
    ADD CONSTRAINT split_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: split split_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.split
    ADD CONSTRAINT split_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: stmttrn stmttrn_acctfrom_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.stmttrn
    ADD CONSTRAINT stmttrn_acctfrom_id_fkey FOREIGN KEY (acctfrom_id) REFERENCES ofx.acctfrom(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: stmttrn stmttrn_acctto_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.stmttrn
    ADD CONSTRAINT stmttrn_acctto_id_fkey FOREIGN KEY (acctto_id) REFERENCES ofx.acctto(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: stmttrn stmttrn_payee_name_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.stmttrn
    ADD CONSTRAINT stmttrn_payee_name_fkey FOREIGN KEY (payee_name) REFERENCES ofx.payee(name) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: stockinfo stockinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.stockinfo
    ADD CONSTRAINT stockinfo_id_fkey FOREIGN KEY (id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: transfer transfer_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.transfer
    ADD CONSTRAINT transfer_id_fkey FOREIGN KEY (id) REFERENCES ofx.invtran(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: transfer transfer_secinfo_id_fkey; Type: FK CONSTRAINT; Schema: ofx; Owner: -
--

ALTER TABLE ONLY ofx.transfer
    ADD CONSTRAINT transfer_secinfo_id_fkey FOREIGN KEY (secinfo_id) REFERENCES ofx.secinfo(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: paystub_items paystub_items_paystub_id_fkey; Type: FK CONSTRAINT; Schema: payroll; Owner: -
--

ALTER TABLE ONLY payroll.paystub_items
    ADD CONSTRAINT paystub_items_paystub_id_fkey FOREIGN KEY (paystub_id) REFERENCES payroll.paystubs(id);


--
-- Name: subaccounts_tax_tags subaccounts_tax_tags_subaccount_name_fkey; Type: FK CONSTRAINT; Schema: tax; Owner: -
--

ALTER TABLE ONLY tax.subaccounts_tax_tags
    ADD CONSTRAINT subaccounts_tax_tags_subaccount_name_fkey FOREIGN KEY (subaccount_name) REFERENCES bookkeeping.subaccounts(name);


--
-- Name: subaccounts_tax_tags subaccounts_tax_tags_tax_tag_id_fkey; Type: FK CONSTRAINT; Schema: tax; Owner: -
--

ALTER TABLE ONLY tax.subaccounts_tax_tags
    ADD CONSTRAINT subaccounts_tax_tags_tax_tag_id_fkey FOREIGN KEY (tax_tag_id) REFERENCES tax.tax_tags(id);


--
-- PostgreSQL database dump complete
--

--
-- Integrity upgrades that the pre-Alembic installers recorded as applied.
--

INSERT INTO admin.integrity_migrations (version) VALUES
    ('ofx-source-identity-v1'), ('brokerage-ledger-v1'), ('journal-money-v2');
