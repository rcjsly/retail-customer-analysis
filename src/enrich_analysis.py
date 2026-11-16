from pathlib import Path
import json, sqlite3
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parents[1]; O=R/'results'
def main():
    s=json.loads((O/'summary.json').read_text(encoding='utf-8'))
    with sqlite3.connect(O/'retail.sqlite') as c:
        d=pd.read_sql_query('SELECT * FROM classified_lines',c)
        independent=pd.read_sql_query('''WITH orders AS (
          SELECT customer_id,invoice_no,MIN(invoice_date) AS order_date
          FROM classified_lines WHERE record_type='sale' AND customer_id IS NOT NULL
          GROUP BY customer_id,invoice_no
        ), ordered AS (
          SELECT *,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY order_date,invoice_no) rn,
          MIN(order_date) OVER(PARTITION BY customer_id) first_date FROM orders
        ), eligible AS (
          SELECT customer_id,MAX(CASE WHEN rn>1 AND julianday(order_date)-julianday(first_date)<=90 THEN 1 ELSE 0 END) success
          FROM ordered WHERE first_date<=datetime(date((SELECT MAX(invoice_date) FROM classified_lines)),'-90 days') GROUP BY customer_id
        ) SELECT COUNT(*) n,SUM(success) k FROM eligible''',c).iloc[0]
    assert int(independent.n)==s['repurchase90_eligible'] and int(independent.k)==s['repurchase90_successes']
    d['invoice_date']=pd.to_datetime(d.invoice_date); d['month']=d.invoice_date.dt.strftime('%Y-%m')
    sale=d[d.record_type.eq('sale')]; credits=d[d.record_type.eq('credit')]
    a,b=s['decomposition_months']; subset=sale[sale.month.isin([a,b])]
    product=subset.pivot_table(index='stock_code',columns='month',values='amount',aggfunc='sum',fill_value=0)
    product['change_gbp']=product[b]-product[a]
    # Representative description is latest observed nonmissing label, never an invented category.
    names=sale.dropna(subset=['description']).sort_values('invoice_date').drop_duplicates('stock_code',keep='last').set_index('stock_code').description
    product['description']=product.index.map(names)
    product=product.reset_index().sort_values('change_gbp',ascending=False)
    product.to_csv(O/'product_growth_contribution.csv',index=False,encoding='utf-8-sig')
    first=sale.dropna(subset=['customer_id']).groupby('customer_id').invoice_date.min()
    sub=subset.copy(); sub['first_month']=sub.customer_id.map(first).dt.strftime('%Y-%m')
    sub['customer_type']=np.select([sub.customer_id.isna(),sub.first_month.eq(sub.month)],['未知客户','当月首次观察客户'],default='此前已观察客户')
    types=sub.groupby(['customer_type','month']).amount.sum().unstack(fill_value=0)
    types['change_gbp']=types[b]-types[a]
    types.reset_index().to_csv(O/'customer_growth_contribution.csv',index=False,encoding='utf-8-sig')
    assert np.isclose(types.change_gbp.sum(),s['sales_change_gbp'],atol=.01,rtol=0)
    assert np.isclose(product.change_gbp.sum(),s['sales_change_gbp'],atol=.01,rtol=0)
    credit_orders=credits.groupby('invoice_no').agg(credit_gbp=('amount',lambda x:-x.sum()),date=('invoice_date','min'),customer_id=('customer_id','first')).sort_values('credit_gbp',ascending=False)
    credit_orders.reset_index().to_csv(O/'credit_invoice_ranking.csv',index=False,encoding='utf-8-sig')
    dup=sale[sale.duplicate_candidate.eq(1)].groupby('month').amount.sum()
    m=pd.read_csv(O/'monthly.csv'); m['aov_gbp']=m.sales_gbp/m.orders
    m['known_sales_gbp']=m.month.map(sale[sale.customer_id.notna()].groupby('month').amount.sum())
    m['known_sales_share']=m.known_sales_gbp/m.sales_gbp
    m['dedup_sales_gbp']=m.sales_gbp-m.month.map(dup).fillna(0)
    m['sales_mom']=m.sales_gbp.pct_change();m['orders_mom']=m.orders.pct_change();m['aov_mom']=m.aov_gbp.pct_change()
    m['complete_month']=m.month.ne(s['end'][:7])
    m.loc[~m.complete_month,['sales_mom','orders_mom','aov_mom']]=np.nan
    m.to_csv(O/'monthly_enriched.csv',index=False,encoding='utf-8-sig')
    prev,last=m.set_index('month').loc[a],m.set_index('month').loc[b]
    growth={'sales_mom':float(last.sales_gbp/prev.sales_gbp-1),'orders_mom':float(last.orders/prev.orders-1),'aov_mom':float(last.aov_gbp/prev.aov_gbp-1),
      'order_contribution_share':s['order_effect_gbp']/s['sales_change_gbp'],
      'dedup_sales_mom':float(last.dedup_sales_gbp/prev.dedup_sales_gbp-1),
      'duplicate_sales_share':s['excluded_duplicate_candidate_sales_gbp']/s['sales_gbp'],
      'top2_credit_gbp':float(credit_orders.head(2).credit_gbp.sum()),'top2_credit_share':float(credit_orders.head(2).credit_gbp.sum()/s['credits_gbp']),
      'missing_customer_rows':int(d.customer_id.isna().sum()),'missing_description_rows':int(d.description.isna().sum()),
      'raw_signed_amount_gbp':float(d.amount.sum()),'source_metadata_missing_values_claim':'no; actual file has missing values',
      'first_observed_growth':types.reset_index().to_dict(orient='records'),'top_growth_products':product.head(5).to_dict(orient='records'),
      'recent_repeat_customer_share':int(pd.read_csv(O/'customer_segments.csv').set_index('segment').loc['近期多次购买','customers'])/s['known_customers']}
    (O/'deep_findings.json').write_text(json.dumps(growth,ensure_ascii=False,indent=2),encoding='utf-8')
    # Remove an entirely unobserved row introduced by index union when normalizing cohorts.
    cr=pd.read_csv(O/'cohort_retention.csv'); cr=cr[cr.cohort<=s['last_complete_month']]
    cr.to_csv(O/'cohort_retention.csv',index=False,encoding='utf-8-sig')
    # Use counts as inputs and formulas for the Excel retention grid.
    active=pd.read_csv(O/'cohort.csv')
    cohort_counts=active.pivot(index='cohort',columns='age_month',values='active_customers')
    for cm in cohort_counts.index:
        observable=pd.Period(s['last_complete_month']).ordinal-pd.Period(cm).ordinal
        for age in cohort_counts.columns:
            if age<=observable and pd.isna(cohort_counts.loc[cm,age]):cohort_counts.loc[cm,age]=0
    cohort_counts.reset_index().to_csv(O/'cohort_counts.csv',index=False,encoding='utf-8-sig')
    bundle={'summary':s,'deep':growth,'monthly':m.replace({np.nan:None}).to_dict(orient='records'),
      'segments':pd.read_csv(O/'customer_segments.csv').to_dict(orient='records'),
      'cohort_counts':cohort_counts.reset_index().replace({np.nan:None}).to_dict(orient='records'),
      'cohort_retention':cr.replace({np.nan:None}).to_dict(orient='records'),
      'quality':pd.read_csv(O/'quality_partition.csv').to_dict(orient='records')}
    (O/'workbook_inputs.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')
    check=json.loads((O/'validation.json').read_text());check.update({'independent_sql_90day_repurchase_verified':True,'customer_growth_reconciled':True,'product_growth_reconciled':True})
    (O/'validation.json').write_text(json.dumps(check,indent=2),encoding='utf-8')
    print(json.dumps(growth,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
