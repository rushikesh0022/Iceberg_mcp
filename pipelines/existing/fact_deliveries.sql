CREATE TABLE analytics.fact_deliveries
ENGINE = MergeTree
PARTITION BY toYYYYMM(delivery_date)
ORDER BY (delivery_date, delivery_id)
AS
SELECT
    d.delivery_id,
    d.merchant_id,
    d.dasher_id,
    d.zone_id,
    toDate(d.actual_time) AS delivery_date,
    dateDiff('minute', d.promised_time, d.actual_time) AS delay_minutes,
    d.status
FROM raw.deliveries AS d
WHERE d.status = 'DELIVERED';
