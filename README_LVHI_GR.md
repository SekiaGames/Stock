# FTSE 高股息低波动指数系列 Ground Rules — 中文翻译

**原文**：FTSE High Dividend Low Volatility Index Series Ground Rules, v2.7, May 2026（17 页）
**原文地址**：https://www.lseg.com/content/dam/ftse-russell/en_us/documents/ground-rules/ftse-high-dividend-low-volatility-index-ground-rules.pdf

> 说明：
> - 本翻译仅供个人研究参考，非官方译本，以英文原文为准；版权归 LSEG / FTSE Russell 所有。
> - 该 Ground Rules 是**系列通用规则**，明确列出的指数为：UK、台湾、新兴市场、发达欧洲除英 4 个。
>   **LVHI 追踪的 International 版（QS/Franklin International Low Volatility High Dividend Hedged Index）
>   属 Franklin 定制指数，未列于本文档**，但其选股与加权框架与本文档相同（两步筛选、股息率加权、上限约束），
>   具体数量参数以 Franklin 官方资料为准。
> - 目录沿用原文 Section 1~9 结构，Section 5（定期审核）为方法论核心，全译；其余章节精译要点。

## Section 1 简介

- 本文档规定 FTSE 高股息低波动指数系列（FTSE High Dividend Low Volatility Index Series）的构建与管理规则。
- 该系列旨在代表**高股息率且低波动**股票的表现。
- 指数设计**不考虑 ESG 因素**。
- 系列包含 4 个指数：
  - FTSE UK High Dividend Low Volatility Index
  - FTSE Taiwan High Dividend Low Volatility Index
  - FTSE Emerging High Dividend Low Volatility Index
  - FTSE Developed Europe ex UK High Dividend Low Volatility Index
- 计算价格（Price）、总收益（Total Return）、净总收益（Net Total Return）三种指数，收盘与实时两个频率；
  总收益指数包含基于除息调整的收益。
- 基础货币：UK 版为英镑、台湾版为新台币、新兴市场版与欧洲除英版为美元；也可发布其他币种。
- 指数用户应自行评估该基于规则的方法论的优劣，并独立咨询投资意见；FTSE Russell 对依赖本规则造成的损失不承担责任。
- FTSE Russell 提示：可能因外部事件对指数进行变更甚至终止，引用该指数的金融合约应能承受此类情形。

## Section 2 管理职责

- **FTSE International Limited (FTSE)** 是基准管理人（Benchmark Administrator），负责指数的日常计算、生产与运营：
  维护成分权重记录、按规则调整成分与权重、执行定期审核并应用变更、公布权重变更、发布指数。
- **规则修订**：FTSE Russell 定期审核本规则；重大修改需咨询 FTSE Russell 咨询委员会及其他利益相关方，
  反馈经 **FTSE Russell 指数治理委员会（Index Governance Board）** 批准。
- 规则未覆盖或未明确的情形，按《FTSE Russell 股票指数原则声明（Statement of Principles）》处理，
  并尽快通知市场；该处理不构成先例，但 FTSE Russell 会考虑是否更新规则。
- 注释：Benchmark Administrator 定义依欧盟基准监管条例 (EU) 2016/1011 与英国基准监管条例（脱欧过渡条款）。

## Section 3 FTSE Russell 指数政策

本规则应与以下政策文件一并阅读（链接见原文）：

- 计算指南：FTSE Global Equity Index Guide to Calculation Methods
- 非市值加权指数的公司行为与事件指南：Corporate Actions and Events Guide for Non Market Cap Weighted Indices
- 原则声明：Statement of Principles for FTSE Russell Equity Indices（每年审核，变更需经政策咨询委员会讨论、治理委员会批准）
- 投诉程序：Benchmark Determination Complaints Handling Policy
- 停牌与市场关闭政策：Index Policy for Trading Halts and Market Closures
- 无法交易政策：Index Policy in the Event Clients are Unable to Trade a Market or a Security
- 重算政策：Recalculation Policy and Guidelines（发现数据不准确时按此决定是否重算）
- 基准方法论变更政策：Policy for Benchmark Methodology Changes
- 治理框架：FTSE Russell Governance Framework（涵盖 IOSCO 金融基准原则、EU/UK 基准监管）

## Section 4 合格证券

底层指数的全部成分均符合入选资格：

| 指数 | 底层指数 |
|---|---|
| FTSE UK High Dividend Low Volatility Index | FTSE 350 ex Investment Trusts |
| FTSE Taiwan High Dividend Low Volatility Index | FTSE TWSE Taiwan 50 & FTSE TWSE Taiwan Mid-Cap 100 |
| FTSE Developed Europe ex UK High Dividend Low Volatility Index | FTSE Developed Europe ex UK |
| FTSE Emerging High Dividend Low Volatility Index | FTSE Emerging（沙特 2020 年 3 月起、北向陆股通 A 股 2020 年 3 月起可纳入） |

## Section 5 成分股定期审核（核心）

### 5.1 定期审核

- **每年 3 月审核一次**：
  - 股息率与波动率数据：**2 月最后一个交易日收盘**（数据截止日 data cut-off date）；
  - 价格：**3 月第一个周五之前那个周三的收盘**（价格截止日 price cut-off date）；
  - 成分股：以 3 月第三个周五后的第一个交易日生效（底层指数成分同步）。
- 审核变更在**审核月第三个周五收盘后**实施。

### 5.2 成分审核（两步法）

**第一步 · 股息率筛选（5.2.1~5.2.3）**

- 从底层指数中选出股息率最高的一批股票；各指数的数量见 5.2.6 表。
- 近 12 个月股息率定义：

  $$DY_i^{12m} = \frac{DPS_i^{12m}}{P_i^{cut-off}}$$

  即近 12 个月每股股息 ÷ 数据截止日价格（价格按截止日汇率折算为股息同币种）。
- **没有近 12 个月股息率的股票不合格**。
- 新兴市场版额外要求流动性：近 3 个月平均日成交额（ADTV）——
  现成分股低于 **375 万美元**剔除；非现成分股低于 **500 万美元**不合格。

**第二步 · 波动率筛选（5.2.4~5.2.5）**

- 从股息率最高子集中，选取**实现波动率最低**的一批（新兴市场版按 5.5.2 部分实施）。
- 波动率定义：股票总收益（**本币**）在数据截止日前 **252 个交易日**的实现波动率；
  收益历史不足 252 天按可用历史计算；**不足 126 天不入选**。

**各阶段选股数量（5.2.6）**

| 指数 | 股息率筛选（5.2.1） | 低波动筛选（5.2.4） |
|---|---|---|
| FTSE UK | 75 | 50 |
| FTSE Taiwan | 60 | 40 |
| FTSE Developed Europe ex UK | 75 | 50 |
| FTSE Emerging | 225 | 150* |

\* 新兴市场版经部分实施（5.5.2）后，成分数预计超过 150。
注：各指数两阶段数量比例均为 2/3（75→50、60→40、225→150）。

### 5.3 加权方法（UK、欧洲除英版等）

- 成分按**近 12 个月股息率加权**（每次年度审核时确定）。
- **上限（每年复核）**：
  - 单家公司权重 ≤ **3%**；
  - 单个 ICB 行业权重 ≤ **25%**。
- 若无法同时满足公司与行业上限：公司上限每次 +0.5% 重算，直到可行或公司上限达到 **4.5%**。
- 公司上限已达 4.5% 仍不可行：行业上限每次 +0.5% 重算，直到可行。

### 5.4 加权方法（台湾版）

- 按**可投资市值**加权；每季度设上限：单家公司 ≤ **10%**。
- 上限因子按审核月第二个周五收盘价格计算，于第三个周五收盘后生效；
  计算时计入第二个周五收盘前已公告确认的公司行为，之后公告的不再调整。

### 5.5 加权方法（新兴市场版）

- 成分按近 12 个月股息率加权。
- **部分实施（partial implementation，2024 年 3 月起）**：为控制换手率，按下表给每只股票设置调整因子，
  作用于设上限前的股息率权重：

| 情形（以 150 只名单为参照） | 调整因子 |
|---|---|
| 本期与上期均在 150 名单 | 1 |
| 仅本期在 150 名单 | 0.5 |
| 仅上期在 150 名单 | 0.5 |
| 两期均不在 | 0 |
| 仅上期在，但本期已不属于合格池 | 0 |
| 仅上期在，但本期近 12 个月股息率为 0 或流动性不达标 | 0 |

- 部分实施后成分数若超过 200：设上限 200，在设上限前**先移除权重最低的成分**直至 200。
- 上限（每年复核）：
  - 单家公司 ≤ **4.5%**；
  - 单个 ICB 行业 ≤ **25%**；
  - **容量比 40**：个股最大权重 = min(4.5%, 40 × 该股在底层指数（FTSE Emerging）中的权重)。
- 无法同时满足时：行业上限每次 +0.5% 重算，直到可行。

## Section 6 成分公司变更

- **审核期内不新增成分**：候选新增成分只在下次年度审核时考虑。
- **审核期内剔除**：成分一旦从底层指数剔除，同步从本系列指数剔除，
  其权重按比例（pro-rata）分配给其余成分。

## Section 7 公司行为与事件

- 拆股、并股、配股、送股、股本变动、自由流通比例变动（要约收购除外）：
  事件前后成分权重保持不变。
- 公司"行为"（Action，有确定除权日）：资本返还、配股/权益发行、股票转换、拆细/合并、送股/资本化发行，
  指数按除权日调整。
- 公司"事件"（Event，公司消息引发的变动）：如战略股东二次发售导致自由流通权重变化，
  需调整时 FTSE Russell 会提前通知调整时点。
- 详细规则见非市值加权指数公司行为指南。

## Section 8 股息处理

- 已宣布股息用于计算标准总收益指数，全部按**除息日（ex-div date）**计入。
- 另计算净（税后）总收益指数：按机构投资者可获得的最**高预扣税率**（不考虑双重征税协定、
  以非派息国居民为前提）扣税。

## Section 9 指数计算

- 价格：采用本地市场实际收盘中间价或最新成交价。
- 指数公式：

  $$Index = \frac{\sum_{i=1}^{N}(p_i \times e_i \times s_i \times f_i \times c_i)}{d}$$

  - p：成分最新成交价（或前一交易日收盘价）；
  - e：折算为基础货币的汇率；
  - s：发行股本；
  - f：可投资权重因子（自由流通比例，0~1）；
  - c：权重调整因子；
  - d：除数（基准日总发行股本，用于股本变动时维持指数连续）。

## Appendix A 其他信息

- 术语表（Glossary）与联系方式见原文；网站：www.lseg.com/en/ftse-russell/

## 免责声明要点（原文节译）

- © 2026 London Stock Exchange Group plc（LSEG）及其集团关联公司，版权所有。
- FTSE International Limited 依英国基准监管条例由英国金融行为监管局（FCA）监管；FTSE EU SAS 依欧盟基准监管条例由法国 AMF 监管。
- 本文件信息仅供参考，不构成金融或投资建议；指数不可直接投资，指数成分不代表买入/卖出/持有建议；
  投资决策应咨询持牌专业人士的意见。
- 未经 LSEG 书面许可不得复制、传播本文件任何部分。
