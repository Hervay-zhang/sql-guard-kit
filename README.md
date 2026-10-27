# sql-guard-kit

执行前 SQL 安全校验 + 查询结果图表选型 —— Text2SQL / ChatBI 场景里的两个通用组件。

从我在公司做的 ChatBI 项目里抽出来的独立模块，去掉了全部业务表、字段、统计口径与内部配置，只保留与业务无关的通用逻辑。

```
pip install sqlglot
```

## 为什么需要它

把"自然语言 → SQL"接进业务系统，模型只是链路中的一环，真正决定能不能上线的有两件事：

1. **模型生成的语句不能直接执行。** 它可能写错表、可能拼出 `; DROP TABLE`，也可能把写操作藏进 CTE。提示词约束不是安全边界——模型可以被绕过，而代码不能。
2. **只读查询也要防慢查询。** 一句 `select * from 大表` 就能把线上库的 IO 打满，而模型天生不会主动加 LIMIT。

这两个问题与具体业务无关，所以做成了可复用的独立模块。

## 两个模块

### 1. `sqlguard.guard` —— 执行前安全校验

```python
from sqlguard import check_sql, GuardError

try:
    result = check_sql("select id, name from t_user")
    execute(result.sql)        # → select * from (SELECT id, name FROM t_user) _bi_result limit 500
except GuardError as exc:
    print(exc.reason)          # NOT_SINGLE_STATEMENT / WRITE_OPERATION / ...
```

四层判断，逐层收紧：

| 层 | 判断 | 拦住的典型攻击 |
|---|---|---|
| 1 | 解析成 AST，必须恰好 1 条语句 | `select 1; drop table t` |
| 2 | 根节点必须属于 `SELECT / UNION / INTERSECT / EXCEPT` | `update`、`show tables`、`grant` |
| 3 | **递归遍历全树**，任何位置出现写节点即拒绝 | `WITH x AS (DELETE ... RETURNING *) SELECT * FROM x` |
| 4 | 重写：丢弃注释 + 外层套 `limit N` | 注释注入、全表扫描 |

第 3 层是这套方案的核心：根节点是 `SELECT`、写操作藏在 CTE 或子查询里的语句，只有递归遍历整棵 AST 才能拦住。用 `statement.find(*FORBIDDEN_NODES)` 一句话完成。

被拦下的语句会带上稳定的原因码（`GuardError.reason`），便于上层做埋点统计与提示文案分流。

### 2. `sqlguard.chart` —— 图表选型

```python
from sqlguard import recommend_chart

spec = recommend_chart("各地区销售额", ["region", "amount"],
                       [["华东", 128000], ["华南", 96000]])
spec.type      # "pie"
spec.option    # 可直接交给 ECharts 渲染
```

规则优先级（自上而下，命中即返回）：

| 条件 | 输出 |
|---|---|
| 结果为空 | `table` |
| 单行 + 单数值列 | `kpi` 指标卡 |
| 占比型维度（地区/品类/渠道/状态…）且 ≤ 8 行 | `pie` 环形图 |
| 时间型维度（日期/月份/季度/周…） | `line` 折线图 |
| 其余多行多列 | `bar` 柱状图 |
| 无可用数值列 | `table` |

维度名同时支持中英文关键词匹配。

## 设计取舍

**为什么用 AST 而不是正则/关键词黑名单？**
`-- drop`、`/* */`、大小写、同义词、奇偶空白都能绕过字符串匹配，而 AST 是解析后的结构，绕不过去。多语句在 AST 层直接表现为语句数量 ≠ 1，这是结构判断，不是模式猜测。

**为什么重写后要丢弃注释？**
sqlglot 默认保留注释。注释不影响执行，但让它继续出现在慢日志和审计记录里，会让"这条语句曾被安全层重写过"这件事变得不可见。

**为什么图表选型用规则而不是再调一次大模型？**
"时间维度 → 折线、占比维度 → 饼图"是有标准答案的分类任务。规则做这件事准确率更高、零延迟、零 token，而且结果可预测、可被单元测试覆盖。大模型适合处理"读不懂的意图"，不适合处理"有确定答案的分类"。

**为什么 LIMIT 套在外层而不是改原语句？**
`select * from (原语句) _bi_result limit N` 不改写原语句语义，不会与子查询里已有的 LIMIT 冲突，也不用解析用户的聚合逻辑。表别名固定为 `_bi_result`，下划线前缀在日常表命名里几乎不会撞名。

## 已知边界

这些是这套方案的局限，不是待办清单里的"以后再做"——写在这里是为了让使用者清楚它拦得住什么、拦不住什么：

- **只做语句结构校验，不做权限控制。** 它保证"进来的只能是查询"，但不保证"这个人有权查这张表"。表级/行级权限必须由数据库账号授权或查询代理实现。
- **不做脱敏、不做审计落库。** 这两件事与具体合规要求绑定，属于接入方的职责。
- **`limit N` 只限制返回行数，不限制扫描量。** 一句没走索引的聚合查询照样能跑满 CPU —— 真实的兜底还需要 `statement_timeout` 与只读事务，那属于执行层，不在本模块内。
- **图表选型依赖列名语义。** 列名是 `col_1`、`v1` 这类无语义命名时，规则只能退回柱状图或表格；也不需要为此引入模型，把列名起好更划算。
- **数值列只按首行类型推断。** MySQL 驱动常把 `SUM()` 返回成字符串，此时会被判为非数值并退回表格——这是有意的保守策略：宁可不画图，也不画错图。
- **不支持多度量、多系列同图。** 当前只取最后一个数值列作为度量，多指标对比需要扩展 `ChartSpec`。

## 测试

```bash
pip install -e ".[dev]"
pytest
```

42 个用例，覆盖：合法语句放行与 LIMIT 注入、9 类写操作拦截、多语句、CTE 藏写、注释绕过、语法错误、空输入，以及 KPI / 饼图 / 折线 / 柱状 / 表格五条选型分支与各自的退化路径。

## 目录

```
src/sqlguard/
  guard.py      # AST 白名单校验 + 单语句 + LIMIT 重写
  chart.py      # 规则选型 → ECharts option
tests/
  test_guard.py
  test_chart.py
examples/demo.py
```
