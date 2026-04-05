# 智慧芽查用查询语法总结

本文用于补充 `agents/common/search_clients/zhihuiya.py` 的检索语法速查。

参考来源：

- 智慧芽 Search Helper 页面：<https://analytics.zhihuiya.com/search_helper>
- Search Helper 前端源码（通过页面 bundle 的 source map 提取）
- 本仓库实现：[zhihuiya.py](/Users/yanhao/Documents/codes/patent/agents/common/search_clients/zhihuiya.py)

说明：

- 下文优先记录已从 Search Helper 页面和源码中确认的语法。
- 少量字段语义按字段命名和业内常见缩写做了推断，已明确标注为“推断”。
- 本文聚焦“查用/检索式编写”，不是完整字段字典。

## 1. 基本写法

智慧芽常见检索式形式：

```text
关键词
字段:关键词
字段:(复合表达式)
```

常见示例：

```text
TTL:computer
ABST:(solar cell $W5 silicon)
PN:(CN123456789A)
APNO:(202310123456.7)
PBD:[20010101 TO 20101231]
IPC:[H01L31/0203 TO H01L31/042]
```

经验上建议：

- 单个词可直接写 `字段:词`
- 复合条件、位置算符、逻辑组合尽量写成 `字段:(...)`
- 日期、分类区间用 `[]` 包裹

## 2. 逻辑运算符

已确认支持：

- `AND`
- `OR`
- `NOT`
- `GAND`

示例：

```text
solar AND cell
battery OR cell
battery NOT lead-acid
TTL:computer AND AN:apple
TTL:computer OR IPC:H01L
AN:apple NOT ABST:screen
```

说明：

- `AND`：同时满足
- `OR`：满足其一
- `NOT`：包含前者且不包含后者
- `GAND`：页面有单独说明，但未在当前仓库内直接使用；通常可理解为更强的分组/全局组合约束，实际使用前建议到 Search Helper 页面再核对一次中文说明

## 3. 通配符

已确认支持：

- `*`：匹配 0 到多个字符
- `?`：匹配单个字符
- `#`：匹配可选单字符

示例：

```text
*otor
*oto*
electr*
EP200*B2
小*车

?otor
gra???ne
US7654???
小??车

#otor
m#tor
moto#
小#车
```

注意：

- `*` 最多可用 2 次
- 通配符不能放在引号短语内
- `*` 适合前缀/中缀扩展，通常应关闭 stemming 后使用
- Search Helper 明确要求关键词主体至少保留一定长度，实操上不要把词干写得过短

## 4. 位置算符

已确认支持：

- `$Wn`
- `$PREn`
- `$WS`
- `$SEN`
- `$PARA`

示例：

```text
ABST:(solar cell $W5 silicon)
ABST:(solar cell $PRE5 silicon)
ABST:(display $WS screen $WS HDMI)
CLMS:(data $SEN line)
DESC:(data $PARA line)
```

含义速记：

- `$Wn`：两个词在 n 个词距内接近出现
- `$PREn`：前词必须出现在后词之前，且间距不超过 n
- `$WS`：关键词按顺序紧邻/强相邻出现
- `$SEN`：位于同一句
- `$PARA`：位于同一段

注意：

- 位置算符优先级较高，建议总是配合括号使用
- `$SEN` 更适合权利要求或句级文本
- `$PARA` 更适合说明书全文或段落级文本

## 5. 频次算符

已确认支持：

- `$FREQn`

Search Helper 有单独说明，但当前提取到的源码未带出完整中文示例。按命名可理解为：

```text
关键词 $FREQn
```

用于约束某关键词在目标字段中出现的次数阈值。实际写法上线前建议到帮助页再核对一次。

## 6. 其他语法

### 分组

```text
vehicle AND (engine OR motor)
```

### 区间

```text
PBD:[20010101 TO 20101231]
IPC:[H01L31/0203 TO H01L31/042]
```

### 短语

```text
"electric vehicle"
```

双引号内词序固定，需相邻出现。

### 词干展开

Search Helper 提到 `Stemming` 开关：

- 开启时，会扩展到词根及可能词形
- 关闭时，更接近字面匹配
- 通配符会影响 stemming 效果

### 其他

页面还列出了以下特殊能力：

- `_`：可选分隔符
- `TREE@`
- 特殊字符转义/特殊词处理

这些能力存在于 Search Helper 页面，但本仓库当前未直接使用。

## 7. 常用字段速查

以下字段已在 Search Helper 源码 `fieldData.ts` 中确认存在。

### 号码与文本

- `PN`：公开号/文献号
- `APNO`：申请号
- `PRNO`：优先权号
- `KD`：文种码
- `PCT_PN`：PCT 公开号
- `PCT_APNO`：PCT 申请号
- `TTL`：标题
- `ABST`：摘要
- `CLMS`：权利要求
- `ICLMS`：独立权利要求
- `DESC`：说明书
- `DESC_F` / `DESC_B` / `DESC_S` / `DESC_D` / `DESC_E`：说明书子区域
- `TTL_CNTRANS` / `ABST_CNTRANS` / `CLMS_CNTRANS` / `DESC_CNTRANS`：中文翻译字段
- `TTL_ENTRANS` / `ABST_ENTRANS` / `CLMS_ENTRANS` / `DESC_ENTRANS`：英文翻译字段
- `TTL_ALL` / `ABST_ALL` / `CLMS_ALL` / `DESC_ALL`：多语言聚合字段
- `TA`：标题 + 摘要（推断）
- `TAC`：标题 + 摘要 + 权利要求（推断）
- `TACD`：标题 + 摘要 + 权利要求 + 说明书/描述（推断）
- `MAINF`：主附图/主图说明相关文本（推断）

### 申请人、发明人、权利人

- `AN`：申请人
- `ANC`：当前申请人
- `ANS` / `ANCS`：标准化申请人名称
- `IN`：发明人
- `AT`：受让人/转让相关主体
- `PE` / `AE` / `AUTHORITY`：专利局、审查机构、国家局相关字段

### 日期与分类

- `APD`：申请日
- `PBD`：公开/公告日
- `ISD`：授权日
- `EXPD`：到期日
- `PRIORITY_DATE`：优先权日
- `IPC`：IPC 分类
- `CPC`：CPC 分类
- `LOC`：LOC 外观分类
- `UPC`：UPC 分类
- `FI`：日本 FI
- `FTERM`：日本 F-Term
- `ADC` / `TTC` / `SEIC`：其他分类/标签字段

### 引证与同族

- `B_CITES`：后向引证
- `F_CITES`：前向引证
- `BF_CITES`：前后向引证合集
- `FAM`：简单同族
- `IFAM`：INPADOC 同族
- `EFAM`：扩展同族
- `FAM_ID` / `IFAM_ID` / `EFAM_ID`：对应同族 ID

### 法律状态与质量

- `LEGAL_STATUS`
- `LEGAL_EVENT`
- `SIMPLE_LEGAL_STATUS`
- `EFAM_STATUS`
- `PAGE_COUNT`
- `CLAIM_COUNT`
- `FCLMS_WORDCOUNT`
- `PATENT_TYPE`
- `SEP`

## 8. 本仓库已用到的查询模板

`[zhihuiya.py](/Users/yanhao/Documents/codes/patent/agents/common/search_clients/zhihuiya.py)` 里已直接使用或隐含使用了下面这些检索式：

### 基础检索

```text
q = 任意智慧芽检索式
```

对应实现：

- `[search()](/Users/yanhao/Documents/codes/patent/agents/common/search_clients/zhihuiya.py#L553)`

### 通过公开号查专利

```text
PN:(公开号)
```

对应实现：

- `get_patent_id_by_pn()`

### 通过申请号查专利

```text
APNO:(申请号)
```

对应实现：

- `get_patent_id_by_pn()`
- `get_patent_details_by_apno()`

### 查同族

```text
EFAM:(PN)
```

示例：

```text
EFAM:(CN123456789A)
```

对应实现：

- `[get_extended_family_by_pn()](/Users/yanhao/Documents/codes/patent/agents/common/search_clients/zhihuiya.py#L725)`

### 查引证

```text
BF_CITES:(PN)
```

示例：

```text
BF_CITES:(CN123456789A)
```

对应实现：

- `[get_citations_by_pn()](/Users/yanhao/Documents/codes/patent/agents/common/search_clients/zhihuiya.py#L744)`

## 9. 推荐写法

建议优先使用下面几类稳定写法：

```text
PN:(CN123456789A)
APNO:(202310123456.7)
TTL:(battery AND thermal)
ABST:(solar cell $W5 silicon)
CLMS:(electrode $SEN separator)
DESC:(electrolyte $PARA additive)
IPC:(H01M)
PBD:[20200101 TO 20241231]
EFAM:(CN123456789A)
BF_CITES:(CN123456789A)
```

如果要做查用检索，常见组合通常是：

```text
TACD:(核心技术词) AND IPC:(目标分类)
TAC:(方案词1 $W5 方案词2) AND PBD:[起始日 TO 结束日]
AN:(竞争对手) AND TACD:(产品词 OR 方法词)
```

其中 `TAC` / `TACD` 的组合能力很强，但字段精确定义建议以上线页面帮助说明为准。

## 10. 备注

- 智慧芽帮助页的字段说明是动态加载的，本文已经把页面源码里能稳定提取的字段和运算符整理出来。
- 如果后续要把这些语法做成程序内校验或 query builder，建议优先覆盖：
  - 逻辑运算符
  - 通配符
  - 位置算符
  - 区间表达式
  - 常用字段白名单
