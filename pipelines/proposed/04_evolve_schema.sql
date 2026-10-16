-- Planted case 4. Expected: EVOLVE_SCHEMA on analytics.fact_deliveries, new column weather_condition
-- Same grain and filter as fact_deliveries. The LEFT JOIN is many-to-one
-- (weather_hourly is unique on zone_id + hour), so it adds a column without
-- changing which rows exist.
CREATE TABLE analytics.deliveries_with_weather
ENGINE = MergeTree
ORDER BY (delivery_date, delivery_id)
AS
SELECT
    d.delivery_id,
    toDate(d.actual_time) AS delivery_date,
    dateDiff('minute', d.promised_time, d.actual_time) AS delay_minutes,
    w.weather_condition
FROM raw.deliveries AS d
LEFT JOIN raw.weather_hourly AS w
    ON w.zone_id = d.zone_id AND w.hour = toStartOfHour(d.actual_time)
WHERE d.status = 'DELIVERED';
