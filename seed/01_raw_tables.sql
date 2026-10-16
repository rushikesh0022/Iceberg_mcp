-- Source tables for the demo marketplace, filled inside ClickHouse with numbers().
-- Fixed dates and cityHash64() make every run produce the same rows.
-- Deliveries span 60 days (2026-08-01 .. 2026-09-29), so date-partitioned
-- tables get two monthly partitions.

CREATE TABLE raw.merchants
(
    merchant_id UInt32,
    merchant_name String,
    zone_id UInt16,
    cuisine String
)
ENGINE = MergeTree
ORDER BY merchant_id
COMMENT 'Current merchant attributes.';

INSERT INTO raw.merchants
SELECT
    toUInt32(number + 1) AS merchant_id,
    concat('merchant_', toString(number + 1)) AS merchant_name,
    toUInt16(number % 50 + 1) AS zone_id,
    ['pizza', 'burgers', 'sushi', 'indian', 'mexican', 'salads'][cityHash64(number, 'cuisine') % 6 + 1] AS cuisine
FROM numbers(2000);

CREATE TABLE raw.deliveries
(
    delivery_id UInt64,
    merchant_id UInt32,
    dasher_id UInt32,
    zone_id UInt16,
    status String,
    promised_time DateTime,
    actual_time DateTime,
    order_value Decimal(10, 2)
)
ENGINE = MergeTree
ORDER BY (promised_time, delivery_id)
COMMENT 'One row per delivery attempt, as emitted by the dispatch service.';

INSERT INTO raw.deliveries
SELECT
    number + 1 AS delivery_id,
    toUInt32(cityHash64(number, 'merchant') % 2000 + 1) AS merchant_id,
    toUInt32(cityHash64(number, 'dasher') % 5000 + 1) AS dasher_id,
    toUInt16((merchant_id - 1) % 50 + 1) AS zone_id,
    multiIf(cityHash64(number, 'status') % 100 < 90, 'DELIVERED',
            cityHash64(number, 'status') % 100 < 96, 'CANCELLED', 'FAILED') AS status,
    toDateTime('2026-08-01 00:00:00', 'UTC') + toUInt32(intDiv(number * 5184000, 500000)) AS promised_time,
    promised_time + toIntervalSecond(toInt32(cityHash64(number, 'late') % 3600) - 900) AS actual_time,
    toDecimal64(10 + (cityHash64(number, 'value') % 9000) / 100, 2) AS order_value
FROM numbers(500000);

CREATE TABLE raw.weather_hourly
(
    zone_id UInt16,
    hour DateTime,
    weather_condition String,
    precipitation_mm Float32
)
ENGINE = MergeTree
ORDER BY (zone_id, hour)
COMMENT 'Observed weather per delivery zone per hour.';

-- 50 zones x 61 days x 24 hours: one row per zone per hour, so a join on
-- (zone_id, hour) matches at most one row.
INSERT INTO raw.weather_hourly
SELECT
    toUInt16(intDiv(number, 1464) + 1) AS zone_id,
    toDateTime('2026-08-01 00:00:00', 'UTC') + toUInt32((number % 1464) * 3600) AS hour,
    ['clear', 'cloudy', 'rain', 'storm'][cityHash64(number, 'weather') % 4 + 1] AS weather_condition,
    toFloat32(if(weather_condition IN ('rain', 'storm'), (cityHash64(number, 'mm') % 200) / 10, 0)) AS precipitation_mm
FROM numbers(50 * 1464);

CREATE TABLE raw.promo_clicks
(
    click_id UInt64,
    promo_id UInt32,
    user_id UInt64,
    clicked_at DateTime
)
ENGINE = MergeTree
ORDER BY (clicked_at, click_id)
COMMENT 'One row per promotion click.';

-- Clicks from 2024-01-01 to 2026-09-29, so the 2024-only zombie table has data.
INSERT INTO raw.promo_clicks
SELECT
    number + 1 AS click_id,
    toUInt32(cityHash64(number, 'promo') % 300 + 1) AS promo_id,
    cityHash64(number, 'user') % 200000 + 1 AS user_id,
    toDateTime('2024-01-01 00:00:00', 'UTC') + toUInt32(intDiv(number * 86140800, 200000)) AS clicked_at
FROM numbers(200000);
