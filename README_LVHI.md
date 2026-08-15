# LVHI — 美股低波动高股息 ETF（含 A 股版构想）

LVHI 全称 **Franklin International Low Volatility High Dividend Index ETF**，是一只投资
"美国以外发达市场、高股息且低波动"股票的指数基金，是"红利低波"策略在美股市场的最典型代表。
本仓库后续计划以同样的思路，结合本工具的高股息列表制作 **A 股版 LVHI**（见文末构想）。

## 基本信息

| 项目 | 内容 |
|---|---|
| 代码 / 交易所 | LVHI / Cboe BZX |
| 发行商 | Franklin Templeton（原 Legg Mason） |
| 成立日期 | 2016-07-27 |
| 费率 | 0.40%（同类基金平均约 0.96%） |
| 分红频率 | 季度 |
| 标的指数 | FTSE International Low Volatility High Dividend Index（美元对冲版，即 QS/Franklin International Low Volatility High Dividend Hedged Index） |
| 投资范围 | 美国以外发达市场（MSCI World ex-USA IMI） |

## 指数方法论

LVHI 追踪的指数基于 FTSE Russell 的"高股息低波动"指数系列（FTSE High Dividend Low Volatility Index Series）
方法论框架（LVHI 的 International 版为 Franklin 定制指数，具体数量参数以 Franklin 官方资料为准），
选股是**两步顺序筛选**，不是把两个因子做加权打分：

1. **股息率筛选**：以近 12 个月股息率降序排列，取股息率最高的一批股票（如 UK 版取前 75 只）。
2. **波动率筛选**：在上述子集中，取近一年（数据截止日前 252 个交易日总收益、本币计）实现波动率
   **最低**的一批入选（如 UK 版 75 只取 50 只，各指数两阶段数量比例均为 2/3）。
   低波动是准入条件，不参与打分；收益历史不足 126 天的股票不入选。

其他规则：

- **加权**：按近 12 个月股息率加权；**单家公司 ≤3%、单个 ICB 行业 ≤25%**（无法满足时公司上限
  每次 +0.5% 放宽至 4.5%，再不行行业上限每次 +0.5%，直到可行）。
- **调仓**：每年 3 月一次——股息率与波动率数据以 2 月最后一个交易日收盘为准，价格以 3 月第一个
  周五前的周三收盘为准，成分变更在 3 月第三个周五收盘后生效。与 A 股年报季后调整的节奏相似。
- **货币对冲**：指数为美元对冲版，消除美元与外币汇率的波动，让投资人只承担股票风险。
- 审核期内不新增成分股（只在下次年度审核考虑）；成分从底层指数剔除时同步剔除、权重按比例分给
  其余成分。注意：官方系列规则中"停牌 40 个交易日以上移除"适用于新兴市场版等具体规则，
  以 Ground Rules 为准。
- 完整规则见 **[Ground Rules 中文翻译](README_LVHI_GR.md)**（官方文档 v2.7, 2026-05 全译），
  官方原文 PDF 见 [ftse-high-dividend-low-volatility-index-ground-rules.pdf](ftse-high-dividend-low-volatility-index-ground-rules.pdf)。

## 策略理念

### 1. 低波动异象（Low Volatility Anomaly）

传统金融理论认为"高风险高收益"，但实证恰恰相反：**低波动股票的风险调整后收益长期优于高波动股票**。
原因之一是行为偏差——机构投资者追逐故事、排名、彩票型收益，系统性冷落那些"平淡无奇"的公司，
把它们价格压低、股息率抬高；市场流动性枯竭时，这类公司只能靠真金白银的派息吸引股东。
这个现象在美国、国际、新兴市场（以及 A 股）都被反复验证。

### 2. 高股息的债券属性

低波动股票恰好往往是高股息股票：成熟行业、现金流稳定、成长性平淡但分红可持续。
这类股票的股息率（dividend-price ratio）与债券收益率走势高度相关，带有"类债券"属性——
下跌有限、有现金流打底，天然是防守型仓位。

### 3. 两者叠加

高股息提供**安全边际与现金流**，低波动提供**回撤控制**。
"红利低波"组合的长期特征：收益与宽基相当、波动率和最大回撤显著更低、
下跌市场超额明显（下行捕获率约 75%）。
对以股息为生、需要年年有收入的全职投资者（如本仓库"高股息之家"六大原则的实践者），
这是一个比纯高股息更稳的进化方向：**股息为锚，波动为盾**。

## 学术论文支撑

FTSE 指数本身是规则驱动的产品，没有专门的"策略论文"；但其理念有明确的学术来源：

- **Ang, Hodrick, Xing & Zhang (2006)**：The Cross-Section of Volatility and Expected Returns，*Journal of Finance* 61(1)。
  奠基之作：特质波动率高的股票收益异常低。
- **Ang, Hodrick, Xing & Zhang (2009)**：High Idiosyncratic Volatility and Low Returns: International and Further U.S. Evidence，*Journal of Financial Economics* 91(1)。
  把"高波动低收益"推广到国际市场。
- **Blitz & van Vliet (2007)**：The Volatility Effect: Lower Risk without Lower Return，*Journal of Portfolio Management* 34(1)。
  低波动组合在不大幅牺牲收益的前提下显著降低风险，是"波动率效应"的经典表述。
- **Blitz, van Vliet & Baltussen (2023)**：The Volatility Effect Revisited。
  对波动率效应的更新检验。
- **Blitz, Hanauer & van Vliet (2021)**：The Volatility Effect in China。
  **波动率效应在中国 A 股市场同样成立**，是 A 股版 LVHI 最重要的理论依据。

## 基金实际特征

- **持仓**：约 180~220 只，以大型股为主（平均市值约 570~770 亿美元），典型持仓如
  Suncor、Canadian Natural Resources、Novartis、Intesa Sanpaolo、Shell、Allianz、Rio Tinto、Mitsubishi、Roche、BHP、Unilever。
- **国别**：加拿大 ~15%、日本 ~13~15%、英国 ~11~15%、法国 ~9~10%、瑞士 ~6~9%、澳大利亚 ~7%、意大利 ~6% 等。
- **行业**：金融 ~24~26%、能源 ~14~16%、工业 ~11~12%、公用事业 ~9~11%、必需消费 ~9~10%、医疗 ~7~8%、材料 ~6~7%。
  清一色"现金牛"行业，几乎没有科技股。
- **收益与风险**：SEC 30 天收益率约 3.7%（基准 MSCI World ex-USA IMI 约 2.8%）；
  年化波动率约 10.8~11.8%，低于基准；**下行捕获率约 75%**，熊市明显抗跌。

## A 股版 LVHI 构想（计划）

### 与 A 股已有"红利低波"指数的对比

A 股已有两个官方红利低波指数，采用与 FTSE 相同的两步筛选结构：

| | LVHI | 中证红利低波动（H30269） | 红利低波100（930955） |
|---|---|---|---|
| 样本数 | 约 180~220 | 50 | 100 |
| 选样 | 股息率前 N → 低波 2/3 | 三年平均股息率前 75 → 近一年波动率最低 50 只 | 三年平均股息率前 300 → 近一年波动率最低 100 只 |
| 加权 | 股息率加权 | 股息率加权 | 股息率/波动率加权（波动越低权重越高） |
| 调仓 | 每年 3 月 | 每年 6 月 | 每季度 |
| 其他约束 | 美元对冲 | 剔除每股股利增长率 ≤0 | 单中证二级行业 ≤20% |

参考数据（930955）：近 1 年年化波动率 11.81%、近 3 年 14.72%，明显低于沪深 300（约 18%）；
2014-2025 价格指数年化约 9.4%。可见红利低波在 A 股同样有效，且已有成熟指数可作基准对比。

### 本工具方案的定位

已有官方指数作为基准，但本工具的优势是**更强的质量过滤与择时**，拟按以下方式制作 A 股版 LVHI：

1. **第一步（已具备）**：高股息池 = 沪深主板，股息率 ≥3%（可调），
   叠加现有过滤器：扣非 ≥-10%（暴雷过滤）、息增年 ≥0（分红持续）、FCF/股息 ≥50%（分红可持续）、行业屏蔽。
   这比官方指数单纯按"三年平均股息率"筛选多了一道质量把关。
2. **第二步（需新增）**：近一年日线年化波动率，取最低 2/3（或按波动率排序取前 100 只）。
   数据现成：K 线缓存（cn_high_dividend_kline_cache，每股 125 根日线）足以计算年化波动率，无需新数据源。
3. **权重**：先等权展示（与现有列表一致），后续可参考 930955 改为股息率/波动率加权。
4. **调仓**：每年 4 月底年报季结束后更新一次（与现有"每年更新一次列表"节奏一致，也接近 FTSE 的每年一次）。
5. **择时**：沿用现有 MA120 买卖点体系，在低波池内低吸高抛，回撤进一步收窄。
6. **行业约束**：A 股红利低波池银行权重往往过高（H30269 银行占比一度超 50%），
   沿用现有屏蔽策略（银行业建议直接参考银行ETF）或设行业权重上限。

### 预期效果

相比纯高股息列表：组合波动率与最大回撤更低、下跌年份跌幅更小；
相比官方红利低波指数：多了一道扣非/息增年/FCF 质量过滤和 MA120 择时，理论上更抗暴雷。
与"高股息之家"六大原则（安全边际、季报原则、不择时、分散持股）完全兼容，
只是把"波动率"显式化为第二个因子。

## 参考链接

- **[Ground Rules 官方文档中文翻译](README_LVHI_GR.md)**（v2.7, 2026-05）
- Ground Rules 官方原文 PDF（仓库本地）：[ftse-high-dividend-low-volatility-index-ground-rules.pdf](ftse-high-dividend-low-volatility-index-ground-rules.pdf)
- LVHI 基金资料：https://www.franklintempleton.com/forms-literature/download/91481-SI、https://www.etfrc.com/LVHI、https://wh.etfrc.com/LVHI
- FTSE 高股息低波动指数系列 Ground Rules（官方 PDF 原链接）：https://www.lseg.com/content/dam/ftse-russell/en_us/documents/ground-rules/ftse-high-dividend-low-volatility-index-ground-rules.pdf
- FTSE 系列介绍页：https://www.lseg.com/en/ftse-russell/indices/high-div-low-vol
- 红利低波100（930955）指数数据：https://www.lixinger.com/equity/index/detail/csi/930955/930955/exchange-traded-fund-shares-list
- 中证红利/红利低波指数解读：https://m.jrj.com.cn/madapter/fund/2024/07/12132341533294.shtml
- 低波并不只是噱头（红利低波100 波动与回撤分析）：https://cj.sina.com.cn/articles/view/5182171545/134e1a99902002hlou
