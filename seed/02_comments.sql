-- Descriptions for the tables that pipelines/existing/*.sql creates.
ALTER TABLE analytics.fact_deliveries MODIFY COMMENT 'Completed deliveries with delay in minutes. One row per delivered order.';
ALTER TABLE analytics.agg_merchant_daily_delays MODIFY COMMENT 'Average delivery delay per merchant per day.';
ALTER TABLE analytics.zombie_promo_clicks_2024 MODIFY COMMENT 'Daily clicks per 2024 promotion. Still written nightly; nothing reads it (Milestone 2 case).';
