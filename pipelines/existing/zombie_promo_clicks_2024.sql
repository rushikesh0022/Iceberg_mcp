CREATE TABLE analytics.zombie_promo_clicks_2024
ENGINE = MergeTree
ORDER BY (click_date, promo_id)
AS
SELECT
    c.promo_id,
    toDate(c.clicked_at) AS click_date,
    count() AS clicks
FROM raw.promo_clicks AS c
WHERE toYear(c.clicked_at) = 2024
GROUP BY c.promo_id, click_date;
