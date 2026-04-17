-- migrate.sql  —  PostgreSQL schema for Cargo-Ledger
-- Run this manually ONLY if you need to create tables without the app
-- (Normally tables are created automatically by SQLAlchemy on startup)

CREATE TABLE IF NOT EXISTS admins (
    id       SERIAL PRIMARY KEY,
    username VARCHAR(80)  UNIQUE NOT NULL,
    password VARCHAR(64)  NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(120) NOT NULL,
    email      VARCHAR(200) UNIQUE NOT NULL,
    password   VARCHAR(64)  NOT NULL,
    role       VARCHAR(20)  NOT NULL DEFAULT 'manager',
    site_ids   TEXT         DEFAULT 'WB001,WB002,WB003',
    is_active  BOOLEAN      DEFAULT TRUE,
    created_at TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weighbridge_records (
    id             SERIAL PRIMARY KEY,
    site_id        VARCHAR(10)  NOT NULL DEFAULT 'WB001',
    challan_id     VARCHAR(50),
    date           VARCHAR(20),
    vehicle_number VARCHAR(50),
    party_name     VARCHAR(120),
    material       VARCHAR(120),
    gross_weight   FLOAT,
    tare_weight    FLOAT,
    net_weight     FLOAT,
    rfid_tag       VARCHAR(100),
    gross_datetime VARCHAR(30),
    tare_datetime  VARCHAR(30),
    net_datetime   VARCHAR(30),
    slip_type      VARCHAR(20)  DEFAULT 'CHALLAN',
    driver         VARCHAR(100),
    synced_at      TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rst_slips (
    id         SERIAL PRIMARY KEY,
    site_id    VARCHAR(10) NOT NULL,
    date       DATE        NOT NULL,
    drive_link TEXT        NOT NULL,
    added_at   TIMESTAMP   DEFAULT NOW(),
    CONSTRAINT uq_rst_site_date UNIQUE (site_id, date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_wr_site    ON weighbridge_records(site_id);
CREATE INDEX IF NOT EXISTS idx_wr_date    ON weighbridge_records(date);
CREATE INDEX IF NOT EXISTS idx_wr_challan ON weighbridge_records(challan_id);

-- Default seeds (run once)
INSERT INTO admins (username, password)
VALUES ('admin', encode(sha256('admin@cargo2024'::bytea), 'hex'))
ON CONFLICT (username) DO NOTHING;
