CREATE TABLE analytics.agg_merchant_daily_delays
ENGINE = MergeTree
PARTITION BY toYYYYMM(delivery_date)
ORDER BY (delivery_date, merchant_id)
AS
SELECT
    f.merchant_id,
    f.delivery_date,
    count() AS deliveries,
    avg(f.delay_minutes) AS avg_delay_minutes
FROM analytics.fact_deliveries AS f
GROUP BY f.merchant_id, f.delivery_date;
