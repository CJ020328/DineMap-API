-- 创建数据库
CREATE DATABASE subway_db;

-- 使用数据库
\c subway_db

-- 创建表
CREATE TABLE IF NOT EXISTS subway_outlets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    address TEXT NOT NULL,
    operating_hours TEXT,
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    waze_link TEXT,
    google_maps_link TEXT,
    street_address VARCHAR(255),
    district VARCHAR(100),
    city VARCHAR(100),
    postcode VARCHAR(10),
    opening_hours JSONB,
    is_24hours BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, address)
);

-- 创建触发器：自动更新 updated_at 字段
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_timestamp
BEFORE UPDATE ON subway_outlets
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- 创建索引
CREATE INDEX idx_subway_outlets_name ON subway_outlets (name);
CREATE INDEX idx_subway_outlets_address ON subway_outlets (address);
CREATE INDEX idx_subway_outlets_latitude ON subway_outlets (latitude);
CREATE INDEX idx_subway_outlets_longitude ON subway_outlets (longitude);
CREATE INDEX idx_subway_outlets_city ON subway_outlets (city);
CREATE INDEX idx_subway_outlets_postcode ON subway_outlets (postcode);
