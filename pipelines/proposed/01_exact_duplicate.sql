-- Planted case 1. Expected: REUSE analytics.fact_deliveries
-- Same rows and same logic as fact_deliveries, written with a different alias
-- and column order, and asking for fewer columns.
CREATE TABLE analytics.delivery_delays
ENGINE = MergeTree
ORDER BY (delivery_date, delivery_id)
AS
SELECT
    dateDiff('minute', del.promised_time, del.actual_time) AS delay_minutes,
    del.delivery_id,
    toDate(del.actual_time) AS delivery_date
FROM raw.deliveries AS del
WHERE del.status = 'DELIVERED';
