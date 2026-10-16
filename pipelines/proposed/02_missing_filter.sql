-- Planted case 2. Expected: NEW_TABLE
-- Looks like fact_deliveries, but has no status filter, so it also contains
-- cancelled and failed deliveries. Reusing fact_deliveries would change the answer.
CREATE TABLE analytics.delivery_delays_all
ENGINE = MergeTree
ORDER BY (delivery_date, delivery_id)
AS
SELECT
    d.delivery_id,
    toDate(d.actual_time) AS delivery_date,
    dateDiff('minute', d.promised_time, d.actual_time) AS delay_minutes
FROM raw.deliveries AS d;
