-- sql/schema.sql (D.4.2 section 4.1, FR-8, FR-9)
--
-- Template for bench.sbtest1.
--   {{ENGINE}}        -> "InnoDB" or "MEMORY"
--   {{PRIMARY_USING}} -> "" for InnoDB, " USING BTREE" / " USING HASH" for MEMORY
--   {{INDEX}}         -> "BTREE" or "HASH"
--
-- Created BEFORE bulk loading so batched INSERTs exercise index maintenance.

DROP TABLE IF EXISTS bench.sbtest1;

CREATE TABLE bench.sbtest1 (
    id      INT NOT NULL,
    k       INT NOT NULL,
    payload CHAR(120) NOT NULL,
    PRIMARY KEY (id){{PRIMARY_USING}},
    KEY idx_k (k) USING {{INDEX}}
) ENGINE={{ENGINE}};
