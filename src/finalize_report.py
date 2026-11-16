from pathlib import Path
import json, html, sqlite3
import pandas as pd
R=Path(__file__).resolve().parents[1];O=R/'results'
def main():
    s=json.loads((O/'summary.json').read_text(encoding='utf-8'));f=json.loads((O/'deep_findings.json').read_text(encoding='utf-8'));inp=json.loads((O/'workbook_inputs.json').read_text(encoding='utf-8'))
    monthly=pd.read_csv(O/'monthly_enriched.csv'); a,b=monthly.iloc[-3],monthly.iloc[-2]
    cg=pd.read_csv(O/'customer_growth_contribution.csv'); pg=pd.read_csv(O/'product_growth_contribution.csv'); seg=pd.read_csv(O/'customer_segments.csv');cohort=pd.read_csv(O/'cohort_retention.csv')
    unknown=cg[cg.customer_type.eq('未知客户')].iloc[0];known_share_prev=a.known_sales_share;known_share_last=b.known_sales_share
    credit=pd.read_csv(O/'credit_invoice_ranking.csv')
    # Actual data examples are traceable invoice IDs, not labeled real refunds.
    def md(frame):
        return '| '+' | '.join(map(str,frame.columns))+' |\n| '+' | '.join(['---']*len(frame.columns))+' |\n'+'\n'.join('| '+' | '.join(str(v) if not isinstance(v,float) else f'{v:,.2f}' for v in row)+' |' for row in frame.itertuples(index=False,name=None))
    report=f'''# 英国电商经营增长与客户复购分析

## 决策摘要
2011年11月商品发票销售额 £{b.sales_gbp:,.2f}，较10月增长 {f['sales_mom']:.1%}，其中订单数增长 {f['orders_mom']:.1%}、客单价下降 {abs(f['aov_mom']):.1%}。增长主要体现为订单规模扩张。

但客户ID缺失交易贡献了增量的 {unknown.change_gbp/s['sales_change_gbp']:.1%}，已知客户销售覆盖率由 {known_share_prev:.1%} 降至 {known_share_last:.1%}，下降 {(known_share_prev-known_share_last)*100:.1f} 个百分点。当前优先事项是核查ID缺失来源、订单类型与批发结构，避免把增长直接解释为新客或老客运营效果。

对已知客户，拥有完整90天观察窗口的 {s['repurchase90_eligible']:,} 位客户中，有 {s['repurchase90_successes']:,} 位在首次观察发票后90天内出现另一笔正向商品发票，比例 {s['repurchase90_rate']:.1%}。可据此设计二购触达试验，但没有触达数据和真实新客历史，不能声称已经实现增长。

## 业务问题与数据来源
业务问题：最近完整月增长来自哪里？哪些客户和商品解释了金额变化？复购运营有哪些可检验机会？

数据由用户从 UCI 官方下载页取得。UCI Online Retail，Daqing Chen，DOI https://doi.org/10.24432/C5BW33 ，官方页面 https://archive.ics.uci.edu/dataset/352/online+retail 。是一家英国非门店零售商的历史交易，主要售卖礼品，包含批发客户，不是中国品牌内部经营数据。

实际读取 {s['source_rows']:,} 行、8个原始字段，时间为 {s['start']} 至 {s['end']}。源Excel SHA-256：{s['source_sha256']}。该校验值已与用户下载时生成的manifest一致；它用于检测文件变化，不是UCI签名。原文件保持不变。

## 口径与质量治理
- 原始粒度是发票行，订单按发票号去重；金额为数量×单价，单位英镑。
- 商品代理编码为五位数字加可选字母。运费、手续费、手工调整、礼券等特殊项目保留在非商品分区，不混入商品经营指标。该编码规则是分析规则，详细排除项可在SQLite逐行追溯。
- 正向销售为数量及单价均为正、发票非C开头的商品行。负向商品行为credit，不能等同可核实退款。
- 原数据中 CustomerID 缺失 {f['missing_customer_rows']:,} 行，Description 缺失 {f['missing_description_rows']:,} 行。UCI元信息标注无缺失，与实际文件不符，本项目以文件实测为准。缺失ID交易仍计入经营金额，但不进入客户留存分母。
- 完全相同的重复候选 {s['duplicate_candidate_rows']:,} 行。无唯一行ID，基准保留，并给出去除候选的敏感性。
- 2011年12月只有截至9日的交易，不参加完整月环比和自然月留存比较。所有客户首次购买均指本窗口首次观察，不等于真实获客。

清洗分区：

{md(pd.read_csv(O/'quality_partition.csv'))}

全原始带符号金额为 £{f['raw_signed_amount_gbp']:,.2f}；商品销售扣除负向记录为 £{s['signed_amount_gbp']:,.2f}。两者口径不同，差额为非商品记账项目等分区，不应混称营收。

## 发现一：11月增长由订单扩张推动，客单价形成抵消
|指标|2011年10月|2011年11月|变化|
|---|---:|---:|---:|
|商品销售额GBP|{a.sales_gbp:,.2f}|{b.sales_gbp:,.2f}|{f['sales_mom']:.1%}|
|正向商品订单|{int(a.orders):,}|{int(b.orders):,}|{f['orders_mom']:.1%}|
|客单价GBP|{a.aov_gbp:,.2f}|{b.aov_gbp:,.2f}|{f['aov_mom']:.1%}|
|已知客户销售覆盖率|{a.known_sales_share:.1%}|{b.known_sales_share:.1%}|-{(a.known_sales_share-b.known_sales_share)*100:.1f}个百分点|

固定先订单后客单的分解：订单数影响 (2751−2005)×10月客单价 = **£{s['order_effect_gbp']:,.2f}**；客单价影响 2751×(11月客单价−10月客单价) = **-£{abs(s['aov_effect_gbp']):,.2f}**；加总等于实际销售额增量 **£{s['sales_change_gbp']:,.2f}**。交互项归入客单影响，改变分解顺序会改变两部分分配，不能当因果贡献。

去除完全重复候选后，11月增长率仍为 {f['dedup_sales_mom']:.1%}，与基准 {f['sales_mom']:.1%} 接近，增长方向对该去重选择稳健。全窗口去重销售额差异仅占基准 {f['duplicate_sales_share']:.2%}。

## 发现二：未知客户贡献大部分增量，客户归因存在盲区

{md(cg)}

未知客户贡献 £{unknown.change_gbp:,.2f}，占增量 {unknown.change_gbp/s['sales_change_gbp']:.1%}；此前已观察客户贡献约46.9%；当月首次观察客户贡献约-8.1%。三类互斥，加总为全部增量。

这意味着“增长来自拉新”缺乏数据支持。未知客户可能来自匿名结账、系统导出或其他业务流程，但数据不含来源系统信息，不能确定机制。应要求数据/业务团队按日期、国家、来源系统、客户类型与发票核对ID缺失，补齐可验证关联，禁止用均值或虚拟ID填造客户。

## 发现三：增长商品可定位，促销和季节性仍只是待验证假设

以下是11月减10月金额增量最高的5个商品，描述使用源数据最近有效名称：

{md(pg.head(5))}

商品23084（RABBIT NIGHT LIGHT）贡献 £{pg.iloc[0].change_gbp:,.2f}，商品22086的圣诞纸链套件贡献 £{pg.iloc[1].change_gbp:,.2f}。可据此排查礼品需求和商品结构，但没有多年度完整同期、价格策略、促销和库存数据，不能确认季节性或补货效果。建议核对真实采购、促销和履约记录后决定备货。

## 发现四：客户复购和集中度支持分层运营，不能直接外推到全体客户
- 已知客户 {s['known_customers']:,} 位，覆盖正向商品销售额 {s['known_customer_sales_share']:.1%}。
- 完整90天观察窗复购率 {s['repurchase90_rate']:.1%}（{s['repurchase90_successes']:,}/{s['repurchase90_eligible']:,}），Wilson 95%区间 [{s['repurchase90_wilson95'][0]:.1%}, {s['repurchase90_wilson95'][1]:.1%}]。按客户去重发票，同日不同发票也计再次下单。该区间不解决选择偏差和批发客户依赖。
- 不同客户观察时长不等的“窗口内多单客户比例”为 {s['observed_repeat_rate']:.1%}，只能描述本窗口，不能替代统一90天复购率。
- 头部 {s['top1pct_customer_count']} 位（向上取整的前1%）客户贡献已知客户销售额 {s['top1pct_known_sales_share']:.1%}。应核对批发身份和复购周期，不能把大额一律删成异常。
- 近期多次购买客户2234位，占已知客户51.5%，贡献已知客户销售额约85.4%；适合优先做大客户维护与流失风险排查。规则分群阈值90天是分析设定。

{md(seg)}

Cohort按首次观察月份分组，M0人数为分母，之后每个自然月看是否再次出现正向商品交易。观察不到的未来格子空白，已观察但无交易的格子为0。2010年12月首批客户可能含大量原有老客，与后续首次出现客户的M1留存差异存在左截断与构成差异，不能直接比较获客渠道质量。

## 发现五：负向交易高度集中，须先核查大额记账记录
负向商品金额共 £{s['credits_gbp']:,.2f}。金额最高两笔负向发票合计 £{f['top2_credit_gbp']:,.2f}，占 {f['top2_credit_share']:.1%}。

{md(credit.head(5))}

当前没有原订单匹配和真实退款流水，不能据此计算退款率。先核验发票、批发订单、录入纠错或冲销原因，再评估退货风险。

## 决策建议与验证安排
|优先顺序|建议负责角色|具体动作|验收方式|
|---|---|---|---|
|1|业务数据负责人|追查11月未知客户金额增长，按来源系统和订单类型补齐客户关联|重算已知客户覆盖率，回溯增量归因，保留无法恢复部分|
|2|财务与订单运营|核查前两笔负向发票及相关正向订单|凭证匹配，明确取消/冲销/退款含义，不自动删极值|
|3|客户运营与销售|核对44位头部客户批发身份，区分大客户维护和零售二购|客户类型与历史订单核验后形成运营名单|
|4|商品运营|复核前5增长商品的采购、促销、履约记录|有真实库存与销售预测后再作补货建议|
|5|客户运营与分析|设计二购触达实验，客户级随机分组，完整观察90天|ITT二购率及置信区间，补齐成本后看增量收益，检查投诉/退订护栏|

上述负责角色和执行顺序是建议，未与真实企业协作或实施。没有A/B分组和干预数据，本项目不填虚构营销提升。

## 校验与可复现性
已经执行：源Excel哈希核对、8字段结构与541909行验证、日期范围核对、全部清洗分区行数核对、SQL/Python销售及负向金额独立对账、已知客户金额核对、订单/客单恒等分解核对、商品和客户增量加总核对、90天复购独立SQL核对。金额容差0.01GBP，计数精确匹配。详见 validation.json。

Excel看板由汇总输入→月度经营/客户留存→经营看板组成，含公式与原生折线图。已做输入变更传播验证、公式错误扫描、各工作表渲染检查与导出检查；未在本机Microsoft Excel里运行验收。工作簿输入是SQL/Python聚合结果，新增交易需重跑脚本并重建工作簿，不宣称实时连接数据库。

## 简历可用边界
定位为“独立公开数据项目”。它展示经营问题定义、指标口径、数据质量、SQL/Python分析、Excel可视化和验证设计，不代表公司实习或真实业务落地。
'''
    (O/'分析报告.md').write_text(report,encoding='utf-8')
    resume=f'''# 简历与面试材料

## 简历可直接使用的版本
**英国电商经营增长与客户复购分析｜独立公开数据项目**  
技术：Python（pandas）、SQLite SQL、Excel、Cohort分析、指标拆解

- 基于UCI公开真实零售交易541,909行，搭建商品销售、订单、客单价和客户复购指标，保留原始数据、排除分区及重复记录敏感性，并用SQL/Python交叉核对经营金额与复购人数。
- 拆解11月销售额环比增长31.7%，识别订单数增长37.2%、客单价下降4.0%；进一步定位客户编号缺失交易贡献61.1%的销售增量，已知客户销售覆盖率由90.9%降至78.4%，提出客户关联与来源系统核查方案。
- 对3,368名拥有完整90天观察窗的客户计算再次购买率47.3%，完成Cohort和客户分群分析，交付Excel/HTML看板、经营报告及客户级随机触达实验方案。

此处数字是分析发现，不是你推动的企业业绩。没有实际执行营销活动，不能改写成“提升复购率47.3%”或“推动营收增长31.7%”。

## 90秒项目介绍
我做的是英国礼品电商的公开真实数据项目，目标是解释经营增长并寻找复购运营机会。数据来自UCI，有54万多条发票明细。首先我把订单与交易行区分开，把服务费、运费和负向记账项目分开，缺失客户ID保留在经营盘，但不进入客户留存分母。

分析中我发现11月销售额增长31.7%，订单增长37.2%，客单价下降4.0%。如果只看总销售额，容易认为运营变好了；但继续按客户分解，61.1%的增量来自缺失客户ID的交易，客户销售覆盖率下降了12.5个百分点，所以不能直接归因于拉新或老客运营。

客户分析方面，我用完整90天观察窗避免最近进入的客户没有足够时间复购，对3368名客户算出47.3%的再次购买率，并用独立SQL验证。最后做了Excel和离线HTML看板，建议先核查客户关联和大额负向发票，再设计客户级随机触达实验。我没有把描述性发现当成因果，也没有宣称实际提升了公司业绩。

## 面试追问与回答
**为什么不用国内数据？** 选择有公开原始文件和稳定官方来源的真实交易数据，能展示完整链路；历史英国和批发业务限制了外推，所以简历明确数据背景。

**为何不直接用原始全部金额？** 原始含运费、手续费、折扣和手工调整，原始带符号金额约974.77万英镑；本项目分析商品经营，商品正向销售约1027.10万，扣负向记账约979.23万，三者不能混用。

**元数据说无缺失，你为何说有缺失？** 实测源文件CustomerID缺失135080行，描述缺失1454行。来源说明不替代质量核验，代码保留质量证据。

**为什么不删所有重复和大额记录？** 原数据没有唯一行ID，完全相同的行也可能是合法重复购买。基准保留，去重敏感性显示全期商品销售仅差0.24%，11月增长方向不变。批发客户可能有大额订单，先核原单。

**31.7%的增长怎么分解？** 销售额=订单数×客单价；固定先订单后客单，订单影响41.18万英镑、客单影响-6.07万英镑，加总35.11万。交互项计入客单部分，不是因果结论。

**61.1%怎么算？** 缺失ID交易11月销售315596.47减10月101023.63，增量214572.84英镑；除全部增量351075.71，得到61.1%。该群不是“新客”，只是身份未知。

**47.3%和65.3%有什么不同？** 65.3%是在整个数据窗口里至少两单的客户占比，不同人的观察时间不同。47.3%只纳入完整90天窗口，首次观察订单后90天内另有发票才算再次购买。

**如何防止SQL多表连接放大？** 月指标直接在行表聚合；客户活跃先去重到客户-月，first_seen表每客户一行，连接键唯一；金额聚合与Python独立结果核对到0.01英镑。

**Cohort能证明哪个渠道质量好吗？** 不能，没有渠道字段；首次观察也不是实际获客。首批客户左截断和批发构成可能造成差异。未来月空白，已观察无回购为0。

**建议怎样落地？** 先查客户ID缺失和负向凭证，补齐客户类型，再客户级随机试验；完整90天观察，主指标ITT二购率，成本补齐后看增量利润，护栏含投诉和退订。目前只是设计。

**项目如何复现？** 先用download_data.py下载真实数据，再运行run_analysis.cmd，依次执行原始哈希验证、清洗分区、SQL/Python、扩展归因和报告/HTML生成。Excel用artifact-tool构建，工作簿已有交付；无需它也能复现CSV和报告。

## 学习与展示顺序
1. 阅读分析报告中的“决策摘要”和“口径与质量治理”。
2. 在Excel月度经营表手算11月增长、客单价与未知客户贡献。
3. 阅读SQL并说明每个查询的粒度、键与分母。
4. 打开SQLite，按source_row抽查几笔原始发票。
5. 自己重跑Python，确认validation.json通过，再将上述内容放进简历。
'''
    (O/'简历与面试.md').write_text(resume,encoding='utf-8')
    # Standalone interactive HTML, with data serialized from verified outputs.
    payload=json.dumps(inp,ensure_ascii=False).replace('</','<\\/')
    css='''body{margin:0;background:#f2f5fa;color:#20324d;font:15px Arial,"Microsoft YaHei",sans-serif}main{max-width:1180px;margin:auto;padding:32px}h1{font-size:28px;margin-bottom:10px}h2{font-size:20px}p{line-height:1.75;color:#53647b}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:15px}.card,section{background:#fff;border-radius:12px;padding:22px;margin:18px 0;box-shadow:0 2px 12px #253b6207}.card b{display:block;font-size:26px;color:#355fac;margin-top:12px}.card small{display:block;margin-top:10px;color:#63738c}button,select{font:inherit;border:1px solid #ced8e7;background:#fff;border-radius:6px;padding:8px;margin:4px;cursor:pointer}button.active{background:#355fac;color:white}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:11px;border-bottom:1px solid #e9edf4;text-align:right;white-space:nowrap}th{background:#edf2fa}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto}.callout{border-left:4px solid #d29a37;padding:12px 18px;background:#fff8e8;line-height:1.8}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.barrow{display:grid;grid-template-columns:80px 1fr 120px;gap:14px;align-items:center;margin:12px 0;font-size:13px}.track{background:#eff3fa;border-radius:3px}.bar{height:19px;background:#436fc2;border-radius:3px}.bar.partial{background:#acb9ce}.barrow b{text-align:right}.delta{font-size:24px;color:#355fac}a{color:#355fac}summary{cursor:pointer;font-size:18px;font-weight:bold;padding:8px 0}.tag{font-size:12px;background:#e9eef8;padding:5px 9px;border-radius:5px}footer{color:#63738c;line-height:1.7;font-size:13px;margin:22px 0}@media(max-width:800px){main{padding:16px}.cards,.grid{grid-template-columns:1fr 1fr}.barrow{grid-template-columns:70px 1fr 100px}}@media(max-width:500px){.cards,.grid{grid-template-columns:1fr}}'''
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>电商经营与客户复购分析</title><style>'+css+'</style><main><span class="tag">独立公开数据项目</span><h1>电商经营增长与客户复购分析</h1><p>UCI真实历史交易 · 2010-12-01 至 2011-12-09 · 金额GBP · 商品口径，含批发业务</p><div class="cards">'
    for label,value,desc in [('商品销售额',f'£{s["sales_gbp"]:,.0f}','正向商品发票金额'),('正向商品订单',f'{s["orders"]:,}','发票号去重，非交易行数'),('已知客户销售覆盖率',f'{s["known_customer_sales_share"]:.1%}','未知ID交易仍计入经营金额'),('90天再次购买率',f'{s["repurchase90_rate"]:.1%}',f'{s["repurchase90_successes"]:,} / {s["repurchase90_eligible"]:,}，完整窗口')]:
        page+=f'<div class="card">{label}<b>{value}</b><small>{desc}</small></div>'
    page+='</div><div class="callout"><b>经营判断：先核对增长的客户身份，再评估运营效果。</b><br>11月销售额增长31.7%，订单增长37.2%，客单价下降4.0%。未知客户交易贡献61.1%增量，客户销售覆盖率由90.9%降至78.4%。</div>'
    page+='<section><h2>月度趋势</h2><label>指标 <select id="metric"><option value="sales_gbp">商品销售额（GBP）</option><option value="orders">正向商品订单</option><option value="aov_gbp">客单价（GBP）</option><option value="known_sales_share">已知客户销售覆盖率</option></select></label><label><input type="checkbox" id="partial">显示不完整的2011年12月</label><div id="bars"></div><p>灰色为部分月；部分月与完整月不作环比。</p></section>'
    page+='<div class="grid"><section><h2>11月销售额增量拆解</h2><p>订单规模影响 <b>+£411,757.22</b><br>客单价影响 <b>−£60,681.51</b><br>合计 <b>+£351,075.71</b></p><p>固定先订单后客单的恒等分解，包含分解顺序约定，不是促销或渠道的因果效果。</p></section><section><h2>客户身份与增量</h2><p>未知客户 <b>+£214,572.84（61.1%）</b><br>此前已观察客户 <b>+£164,799.49（46.9%）</b><br>当月首次观察客户 <b>−£28,296.62（−8.1%）</b></p><p>首次观察不等于真实新客；不能把缺失ID交易认定为拉新成功。</p></section></div>'
    page+='<section><h2>Cohort自然月留存</h2><p>M0为首次观察购买月份，分母为该月客户数；未来尚未观察的格子留空，完整月无人购买则为0。首批客户存在左截断，不能直接比较获客质量。</p><div class="scroll" id="cohort"></div></section>'
    page+='<section><h2>客户分群与运营方向</h2><div class="scroll">'+seg.to_html(index=False,border=0,float_format=lambda x:f'{x:,.2f}')+'</div><p>近期单次购买651位：二购触达候选；沉默多次购买595位：核查批发周期与唤回机会。90天为分析规则；实际触达需补齐客户类型、许可和成本。</p></section>'
    page+='<section><details><summary>原始质量与复核证据</summary><p>CustomerID缺失135,080行，描述缺失1,454行。UCI元数据标注无缺失，与实测不同。重复候选5,268行，保留基准并做敏感性。去重后11月销售额仍增长31.6%。负向商品金额前两笔占51.3%，需核对原单，不能直接称退款。</p><p>源Excel SHA-256：'+s['source_sha256']+'</p><p>SQL/Python金额对账、90天复购独立SQL核对、客户/商品增量核对已通过。详见validation.json。</p></details></section>'
    page+='<section><details><summary>月度经营明细</summary><div class="scroll">'+monthly[['month','sales_gbp','credits_gbp','orders','aov_gbp','known_sales_share']].to_html(index=False,border=0,float_format=lambda x:f'{x:,.4f}')+'</div></details></section>'
    page+='<footer>来源：<a href="https://archive.ics.uci.edu/dataset/352/online+retail">UCI Online Retail</a>，Daqing Chen，<a href="https://doi.org/10.24432/C5BW33">DOI 10.24432/C5BW33</a>。本看板可离线使用。<br>没有成本、投放、流量、库存或履约状态，不计算利润、广告ROI或真实退款率。营销实验尚未实施。</footer></main>'
    js='''const DATA=PAYLOAD;const fmt=(v,key)=>key==='known_sales_share'?(v*100).toFixed(1)+'%':(key==='orders'?'':'£')+v.toLocaleString('en-GB',{maximumFractionDigits:key==='aov_gbp'?2:0});function draw(){const key=document.querySelector('#metric').value,rows=DATA.monthly.filter(r=>document.querySelector('#partial').checked||r.complete_month),max=Math.max(...rows.map(r=>r[key]));document.querySelector('#bars').innerHTML=rows.map(r=>'<div class="barrow"><span>'+r.month+'</span><div class="track"><div class="bar '+(r.complete_month?'':'partial')+'" style="width:'+100*r[key]/max+'%"></div></div><b>'+fmt(r[key],key)+'</b></div>').join('');}document.querySelector('#metric').addEventListener('change',draw);document.querySelector('#partial').addEventListener('change',draw);draw();document.querySelector('#cohort').innerHTML='<table><thead><tr><th>首次观察月</th>'+Array.from({length:12},(_,i)=>'<th>M'+i+'</th>').join('')+'</tr></thead><tbody>'+DATA.cohort_retention.map(r=>'<tr><td>'+r.cohort+'</td>'+Array.from({length:12},(_,i)=>r[i]==null?'<td></td>':'<td style="background:rgba(67,111,194,'+Math.max(.06,r[i]*.65)+')">'+(r[i]*100).toFixed(1)+'%</td>').join('')+'</tr>').join('')+'</tbody></table>';'''
    page+='<script>'+js.replace('PAYLOAD',payload)+'</script></html>'
    (O/'dashboard.html').write_text(page,encoding='utf-8')
    print('Final report, resume and interactive HTML generated')
if __name__=='__main__':main()
