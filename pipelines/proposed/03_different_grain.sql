-- Planted case 3. Expected: NEW_TABLE
-- One row per dasher per day. fact_deliveries is one row per delivery, and
-- agg_merchant_daily_delays is one row per merchant per day: neither matches.
CREATE TABLE analytics.dasher_daily_delays
ENGINE = MergeTree
ORDER BY (delivery_date, dasher_id)
AS
SELECT
    f.dasher_id,
    f.delivery_date,
    count() AS deliveries,
    avg(f.delay_minutes) AS avg_delay_minutes
FROM analytics.fact_deliveries AS f
GROUP BY f.dasher_id, f.delivery_date;
