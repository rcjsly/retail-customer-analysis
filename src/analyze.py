"""Reproducible invoice-line analysis. Requires real UCI data and a hash manifest."""
from pathlib import Path
import hashlib, html, json, re, sqlite3
from datetime import datetime, timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results'

def save_csv(frame,name): frame.to_csv(OUT/name,index=False,encoding='utf-8-sig')
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
def wilson(k,n):
    if n==0:return None,None
    z=1.959963984540054; p=k/n; d=1+z*z/n
    a=(p+z*z/(2*n))/d; b=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return float(a-b),float(a+b)
def main():
    src=ROOT/'data/raw/Online Retail.xlsx'; manifest_path=ROOT/'data/raw/manifest.json'
    if not src.exists() or not manifest_path.exists():
        raise SystemExit('Missing genuine source data or manifest. Run src/download_data.py first. No substitute data will be generated.')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if digest(src)!=manifest['xlsx_sha256']: raise ValueError('Source checksum mismatch; do not proceed')
    raw=pd.read_excel(src,engine='openpyxl',dtype={'InvoiceNo':'string','StockCode':'string','CustomerID':'string'})
    required=['InvoiceNo','StockCode','Description','Quantity','InvoiceDate','UnitPrice','CustomerID','Country']
    if list(raw.columns)!=required: raise ValueError('Unexpected schema: '+repr(list(raw.columns)))
    # This is an integrity check against the published dataset size, not an analysis result.
    if len(raw)!=541909: raise ValueError('Expected published UCI dataset with 541909 invoice lines')
    OUT.mkdir(parents=True,exist_ok=True)
    d=raw.copy(); d.insert(0,'source_row',np.arange(2,len(d)+2))
    rename={'InvoiceNo':'invoice_no','StockCode':'stock_code','Description':'description','Quantity':'quantity',
     'InvoiceDate':'invoice_date','UnitPrice':'unit_price','CustomerID':'customer_id','Country':'country'}
    d=d.rename(columns=rename)
    for c in ['invoice_no','stock_code','customer_id']: d[c]=d[c].str.strip()
    d['invoice_date']=pd.to_datetime(d['invoice_date'],errors='coerce')
    d['quantity']=pd.to_numeric(d['quantity'],errors='coerce')
    d['unit_price']=pd.to_numeric(d['unit_price'],errors='coerce')
    d['amount']=d['quantity']*d['unit_price']
    d['duplicate_candidate']=raw.duplicated(keep='first').to_numpy()
    cancel=d['invoice_no'].str.upper().str.startswith('C',na=False)
    valid=d['invoice_date'].notna() & d['invoice_no'].notna() & d['stock_code'].notna() & d['quantity'].notna() & d['unit_price'].notna()
    # UCI includes service, postage, adjustment and administrative codes.
    # Regex policy is transparent: five digits plus optional letters are the merchandise proxy.
    merchandise=d['stock_code'].str.fullmatch(r'\d{5}[A-Za-z]*',na=False)
    conditions=[
      ~valid,~merchandise,d['unit_price']<=0,d['quantity']==0,
      (d['quantity']<0)&(d['unit_price']>0),
      (d['quantity']>0)&(d['unit_price']>0)&cancel,
      (d['quantity']>0)&(d['unit_price']>0)&~cancel]
    d['record_type']=np.select([c.fillna(False).to_numpy(dtype=bool) for c in conditions],
      ['invalid','non_merchandise','nonpositive_price','zero_quantity','credit','cancellation_conflict','sale'],default='unclassified')
    assert d['record_type'].ne('unclassified').all()
    # Retain duplicate candidates: the source has no unique line ID to prove duplicated ingestion.
    sale=d[d.record_type.eq('sale')].copy(); credit=d[d.record_type.eq('credit')].copy()
    invoice_customers=sale.groupby('invoice_no')['customer_id'].nunique()
    if invoice_customers.gt(1).any(): raise ValueError('Invoice linked to multiple customer IDs')
    known=sale[sale.customer_id.notna()].copy()
    if known.empty: raise ValueError('No identifiable customers')
    end=d.invoice_date.max(); start=d.invoice_date.min()
    if start.strftime('%Y-%m-%d')!='2010-12-01' or end.strftime('%Y-%m-%d')!='2011-12-09':
        raise ValueError('Source date range differs from published dataset')
    sales=float(sale.amount.sum()); credits=float(-credit.amount.sum())
    orders=sale.invoice_no.nunique(); customers=known.customer_id.nunique()
    quality=d.groupby('record_type',dropna=False).agg(rows=('source_row','size'),signed_amount_gbp=('amount','sum')).reset_index()
    save_csv(quality,'quality_partition.csv')
    assert int(quality.rows.sum())==len(raw)
    with sqlite3.connect(OUT/'retail.sqlite') as conn:
        db=d.copy(); db['invoice_date']=db['invoice_date'].dt.strftime('%Y-%m-%d %H:%M:%S')
        db.to_sql('classified_lines',conn,index=False,if_exists='replace')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_customer ON classified_lines(customer_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_date ON classified_lines(invoice_date)')
        sql=(ROOT/'sql/analysis.sql').read_text(encoding='utf-8')
        queries={m.group(1):m.group(2).strip() for m in re.finditer(r'-- query: (\w+)\n(.*?)(?=-- query:|\Z)',sql,re.S)}
        outputs={name:pd.read_sql_query(query,conn) for name,query in queries.items()}
    for name,frame in outputs.items(): save_csv(frame,name+'.csv')
    monthly=outputs['monthly']
    if not np.isclose(monthly.sales_gbp.sum(),sales,atol=.01,rtol=0): raise AssertionError('SQL/Python sales do not reconcile')
    if not np.isclose(monthly.credits_gbp.sum(),credits,atol=.01,rtol=0): raise AssertionError('SQL/Python credits do not reconcile')
    freq=outputs['customer_frequency']; repeat=int(freq.orders.ge(2).sum())
    assert len(freq)==customers
    if not np.isclose(freq.sales_gbp.sum(),known.amount.sum(),atol=.01,rtol=0): raise AssertionError('Customer amount mismatch')
    # 90-day repurchase: distinct invoices within 90 days after first observed invoice.
    inv=known.groupby(['customer_id','invoice_no'],as_index=False).agg(date=('invoice_date','min'),sales_gbp=('amount','sum'))
    inv=inv.sort_values(['customer_id','date','invoice_no'])
    first=inv.groupby('customer_id').date.min()
    cutoff=end.normalize()-pd.Timedelta(days=90)
    eligible=first[first<=cutoff].index
    inv['days_from_first']=(inv.date-inv.customer_id.map(first)).dt.total_seconds()/86400
    first_invoice=inv.drop_duplicates('customer_id').set_index('customer_id').invoice_no
    second=inv[(inv.invoice_no!=inv.customer_id.map(first_invoice)) & inv.days_from_first.between(0,90)]
    repurchased=set(second.customer_id)&set(eligible)
    k=len(repurchased); n=len(eligible)
    if not n: raise ValueError('No customers with complete 90-day observation window')
    lower,upper=wilson(k,n)
    # Chronologically complete monthly cohort grid; blanks for unobserved periods.
    cohort=outputs['cohort']; last_complete=(end.to_period('M')-1)
    cohort_sizes=known.assign(cohort=known.customer_id.map(first).dt.to_period('M').astype(str)).groupby('cohort').customer_id.nunique()
    pivot=cohort.pivot(index='cohort',columns='age_month',values='active_customers')
    for cm in pivot.index:
        observed=int(last_complete.ordinal-pd.Period(cm).ordinal)
        for age in pivot.columns:
            if age<=observed and pd.isna(pivot.loc[cm,age]):pivot.loc[cm,age]=0
    retention=pivot.div(cohort_sizes,axis=0); retention.index.name='cohort'
    retention.reset_index().to_csv(OUT/'cohort_retention.csv',index=False,encoding='utf-8-sig')
    country=sale.groupby('country',dropna=False).agg(sales_gbp=('amount','sum'),orders=('invoice_no','nunique')).sort_values('sales_gbp',ascending=False).reset_index()
    save_csv(country,'country.csv')
    product=sale.groupby('stock_code').agg(sales_gbp=('amount','sum'),units=('quantity','sum'),orders=('invoice_no','nunique')).sort_values('sales_gbp',ascending=False).reset_index()
    product['sales_share']=product.sales_gbp/sales; product['cumulative_share']=product.sales_share.cumsum()
    save_csv(product,'products.csv')
    # RFM is a descriptive, rule-based segmentation, never a predicted response score.
    freq['recency_days']=(end.normalize()-pd.to_datetime(freq.last_purchase).dt.normalize()).dt.days
    freq['segment']=np.select([freq.recency_days.le(90)&freq.orders.ge(2),freq.recency_days.le(90)&freq.orders.eq(1),freq.recency_days.gt(90)&freq.orders.ge(2)],['近期多次购买','近期单次购买','沉默多次购买'],default='沉默单次购买')
    save_csv(freq,'customer_segments_detail.csv')
    segments=freq.groupby('segment').agg(customers=('customer_id','size'),sales_gbp=('sales_gbp','sum')).reset_index()
    save_csv(segments,'customer_segments.csv')
    duplicate_sales=float(sale.loc[sale.duplicate_candidate,'amount'].sum())
    top1pct=max(1,int(np.ceil(customers*.01)))
    top_share=float(freq.nlargest(top1pct,'sales_gbp').sales_gbp.sum()/known.amount.sum())
    month_sales=monthly.set_index('month').sales_gbp
    # Exclude the partial final month. Use an exact order-count/AOV identity, not a causal claim.
    complete=monthly[monthly.month<end.strftime('%Y-%m')].tail(2)
    a,b=complete.iloc[0],complete.iloc[1]
    aov0=a.sales_gbp/a.orders; aov1=b.sales_gbp/b.orders
    order_effect=float((b.orders-a.orders)*aov0)
    aov_effect=float(b.orders*(aov1-aov0))
    assert np.isclose(order_effect+aov_effect,b.sales_gbp-a.sales_gbp,atol=.01,rtol=0)
    summary={'source_rows':len(raw),'start':str(start),'end':str(end),'sales_gbp':sales,'credits_gbp':credits,
      'signed_amount_gbp':sales-credits,'orders':int(orders),'aov_gbp':sales/orders,'known_customers':int(customers),
      'known_customer_sales_share':float(known.amount.sum()/sales),'repeat_customers':repeat,'observed_repeat_rate':repeat/customers,
      'repurchase90_eligible':n,'repurchase90_successes':k,'repurchase90_rate':k/n if n else None,'repurchase90_wilson95':[lower,upper],
      'top1pct_customer_count':top1pct,'top1pct_known_sales_share':top_share,'duplicate_candidate_rows':int(d.duplicate_candidate.sum()),
      'dedup_sensitivity_sales_gbp':sales-duplicate_sales,'excluded_duplicate_candidate_sales_gbp':duplicate_sales,
      'last_complete_month':str(last_complete),'order_effect_gbp':order_effect,'aov_effect_gbp':aov_effect,
      'decomposition_months':[a.month,b.month],'sales_change_gbp':float(b.sales_gbp-a.sales_gbp),
      'built_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':manifest['xlsx_sha256']}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    # Files contain public pseudonymous customer IDs. Never infer natural-person identity.
    note=f'''# 电商经营与客户复购分析

## 数据与分析范围
UCI Online Retail，DOI: https://doi.org/10.24432/C5BW33 。原始数据 {len(raw):,} 行，覆盖 {start} 至 {end}。金额单位为英镑。
仅五位数字加可选字母的商品编码纳入商品经营口径；服务、运费、调整等非商品项另行分区保留。
原始文件不修改；逐行分类保留 source_row。重复候选不直接删除，另做敏感性分析。

## 已计算结果
- 正向商品销售额 £{sales:,.2f}；负向商品交易绝对额 £{credits:,.2f}；按交易记账时点加总的净额 £{sales-credits:,.2f}。
- 正向商品订单 {orders:,} 单，客单价 £{sales/orders:,.2f}。
- 已知客户 {customers:,} 位，其销售额覆盖全部正向商品销售额 {known.amount.sum()/sales:.1%}。
- 观察窗内多单客户比例 {repeat/customers:.1%}，不同客户观察时长不同，不能据此比较获客质量。
- 完整90天观察窗：{k:,}/{n:,} 位客户在首次出现订单后90天内再次下单，比例 {k/n:.1%}；Wilson 95%区间 [{lower:.1%}, {upper:.1%}]。
- 销售额最高的 {top1pct} 位已知客户贡献已知客户销售额 {top_share:.1%}。存在批发客户，不能把大额交易直接判为异常。
- 移除完全相同的重复候选后，正向商品销售额为 £{sales-duplicate_sales:,.2f}，与基准相差 £{duplicate_sales:,.2f}。

## 最近完整月份的变动拆解
{a.month} 至 {b.month}，销售额变化 £{b.sales_gbp-a.sales_gbp:,.2f}。
采用固定顺序恒等分解：订单量影响 = (本期订单−上期订单)×上期客单价 = £{order_effect:,.2f}；客单价影响 = 本期订单×(本期客单价−上期客单价) = £{aov_effect:,.2f}。
这只是描述性归因，不能推出促销、渠道或价格变动的因果效应。最后月份不完整，已从完整月比较及留存矩阵中排除。

## 决策与验证
1. 按 customer_segments.csv 的规则客群制定二购触达、老客维护和沉默唤回方案。规则阈值90天是分析设定，不是企业既有标准。
2. 检查 invoice_ranking.csv 的大额订单及负向交易原始记录，核对批发、取消、纠错和跨期冲销；不自动删除。
3. 经营看板每周复核商品销售额、订单、客单价及已知客户覆盖率；同一口径同时追踪原始口径和去重敏感性。
4. 下一步需真实触达实验：客户级随机分组，对照组不触达，主指标90天内再次购买率，按分组时点统一观察窗；记录毛利和补贴成本后评估增量收益。当前没有实验结果。

## 结论边界
该商家含批发业务；历史英国非门店零售不能代表当下中国消费品牌。没有支付/履约状态，销售额是发票行金额口径；负向交易不等于可核实退款，不能称退款率。没有成本、流量、广告费、库存或获客日期，不能计算利润、转化率、广告ROI、缺货率或真实新客留存。
首次出现是观察窗内首次交易，并非真实首次购买。Wilson区间仅展示二项比例不确定性，不解决客户独立性、选择偏差或外部推广问题。所有建议仍待业务核验或实验，不声称已带来业绩提升。
'''
    (OUT/'分析报告.md').write_text(note,encoding='utf-8')
    resume=f'''# 简历项目表述（完成复核后使用）

**英国电商经营与客户复购分析｜独立公开数据项目｜Python、SQL、客户分群、Cohort**

- 基于 UCI 真实电商 {len(raw):,} 行发票明细，建立商品销售、负向交易、订单及客户指标口径，保留原始数据并记录文件校验值、排除原因和重复记录敏感性。
- 使用 Python 与 SQLite 完成月度经营拆解、商品贡献、客户集中度及 Cohort 分析，以独立聚合核对销售额；识别已知客户覆盖率 {known.amount.sum()/sales:.1%}，完整90天观察窗的再次购买率 {k/n:.1%}（{k:,}/{n:,}）。
- 沉淀可复现脚本、离线 HTML 看板及客群运营实验方案，明确历史数据与批发客户结构的适用边界。未实施真实营销实验，不填写虚构增长业绩。

面试应能解释：为何不直接删重复；为何 CustomerID 缺失仍计入经营盘而不进入客户盘；90天分母怎样处理右截断；负向记录为何不等同退款；SQL怎样防止联表放大；如何区分描述性分解与因果分析。
'''
    (OUT/'简历与面试.md').write_text(resume,encoding='utf-8')
    # A self-contained HTML view; no remote libraries, cookies or services.
    def table(frame):return frame.to_html(index=False,border=0,float_format=lambda x:f'{x:,.2f}')
    bars=''.join('<div class="barrow"><span>'+html.escape(str(r.month))+'</span><div class="track"><div class="bar" style="width:'+str(100*r.sales_gbp/monthly.sales_gbp.max())+'%"></div></div><b>£'+f'{r.sales_gbp:,.0f}'+'</b></div>' for r in monthly.itertuples())
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>电商经营与客户复购分析</title><style>body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:0;background:#f3f5f9;color:#18263c}main{max-width:1160px;margin:auto;padding:36px}h1{font-size:28px}h2{font-size:20px;margin-top:34px}p{line-height:1.7;color:#4d5e73}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}.card,section{background:white;border-radius:10px;padding:22px;margin-top:12px}.card b{display:block;font-size:26px;margin:12px 0;color:#254b97}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;text-align:right;border-bottom:1px solid #e4e9f0}th{background:#edf1f8}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto}.barrow{display:grid;grid-template-columns:90px 1fr 130px;gap:15px;margin:12px 0;align-items:center;font-size:13px}.track{background:#edf1f8}.bar{height:18px;background:#456ec3}.barrow b{text-align:right}summary{cursor:pointer;font-weight:bold;padding:10px 0}a{color:#254b97}@media(max-width:600px){main{padding:16px}.barrow{grid-template-columns:70px 1fr 90px;gap:5px}}</style><main><h1>电商经营与客户复购分析</h1>'''
    page+=f'<p>真实公开交易数据 · {start:%Y-%m-%d} 至 {end:%Y-%m-%d} · 英镑。最近月份不完整。<br>来源：<a href="https://archive.ics.uci.edu/dataset/352/online+retail">UCI Online Retail</a>。商品口径，含批发交易。</p><div class="cards">'
    for label,value in [('正向商品销售额',f'£{sales:,.0f}'),('正向商品订单',f'{orders:,}'),('客单价',f'£{sales/orders:,.2f}'),('90天再次购买率',f'{k/n:.1%}')]:
        page+='<div class="card">'+label+'<b>'+value+'</b></div>'
    page+='</div><section><h2>月度销售额</h2>'+bars+'<p>末月为截至12月9日的部分数据；未用来作完整月环比。</p></section>'
    for title,frame in [('月度经营明细',monthly),('客户规则分群',segments),('商品销售额前10',product.head(10)),('国家销售额前10',country.head(10)),('清洗分区',quality)]:
        page+='<section><details open><summary>'+title+'</summary><div class="scroll">'+table(frame)+'</div></details></section>'
    page+='<section><h2>使用边界</h2><p>负向交易不等于退款；无成本和广告数据，不计算利润或ROI。客户分群为描述性规则。Cohort及90天指标仅适用于已知客户，首次出现不等于真实获客。重复候选保留，敏感性另见报告。当前没有已实施的业务实验。</p></section></main></html>'
    (OUT/'dashboard.html').write_text(page,encoding='utf-8')
    checks={'source_hash_verified':True,'schema_verified':True,'published_row_count_verified':True,'published_date_range_verified':True,
      'partition_rows_reconciled':True,'sql_python_sales_reconciled':True,'sql_python_credits_reconciled':True,
      'customer_amount_reconciled':True,'decomposition_reconciled':True,'analysis_completed_utc':summary['built_utc']}
    (OUT/'validation.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
