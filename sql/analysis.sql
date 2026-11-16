-- SQLite 3.25+. Source: derived classified_lines, created by src/analyze.py.
-- Dates are ISO8601 local timestamps. Monetary unit: GBP.
-- No many-to-many joins to avoid duplicating invoice-line amounts.

-- query: monthly
SELECT substr(invoice_date,1,7) AS month,
       SUM(CASE WHEN record_type='sale' THEN amount ELSE 0 END) AS sales_gbp,
       SUM(CASE WHEN record_type='credit' THEN -amount ELSE 0 END) AS credits_gbp,
       SUM(amount) AS signed_amount_gbp,
       COUNT(DISTINCT CASE WHEN record_type='sale' THEN invoice_no END) AS orders,
       COUNT(DISTINCT CASE WHEN record_type='sale' THEN customer_id END) AS known_customers
FROM classified_lines
WHERE record_type IN ('sale','credit')
GROUP BY 1 ORDER BY 1;

-- query: customer_frequency
SELECT customer_id, COUNT(DISTINCT invoice_no) AS orders,
       SUM(amount) AS sales_gbp, MAX(invoice_date) AS last_purchase
FROM classified_lines
WHERE record_type='sale' AND customer_id IS NOT NULL
GROUP BY customer_id;

-- query: cohort
WITH active AS (
 SELECT DISTINCT customer_id,substr(invoice_date,1,7) AS month
 FROM classified_lines WHERE record_type='sale' AND customer_id IS NOT NULL
), first_seen AS (
 SELECT customer_id,MIN(month) AS cohort FROM active GROUP BY customer_id
)
SELECT f.cohort,
 (CAST(substr(a.month,1,4) AS INTEGER)-CAST(substr(f.cohort,1,4) AS INTEGER))*12
 +CAST(substr(a.month,6,2) AS INTEGER)-CAST(substr(f.cohort,6,2) AS INTEGER) AS age_month,
 COUNT(*) AS active_customers
FROM active a JOIN first_seen f ON a.customer_id=f.customer_id
-- Exclude the incomplete last month for a comparable calendar-month retention grid.
WHERE a.month < (SELECT substr(MAX(invoice_date),1,7) FROM classified_lines)
GROUP BY 1,2 ORDER BY 1,2;

-- query: invoice_ranking
SELECT invoice_no,MIN(invoice_date) AS invoice_date,MIN(customer_id) AS customer_id,
       SUM(amount) AS sales_gbp,SUM(quantity) AS units,COUNT(*) AS lines
FROM classified_lines WHERE record_type='sale'
GROUP BY invoice_no ORDER BY sales_gbp DESC LIMIT 20;
