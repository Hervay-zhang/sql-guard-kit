"""sql-guard-kit：Text2SQL / ChatBI 的两个通用安全与出图组件。

从公司内部 ChatBI 项目里抽出的通用模块（已去掉全部业务表、字段与内部口径）：

- :mod:`sqlguard.guard` —— 执行前 SQL 安全校验（AST 白名单 + 单语句 + 行数上限）
- :mod:`sqlguard.chart` —— 查询结果到 ECharts 配置的规则选型
"""

from sqlguard.chart import ChartSpec, recommend_chart
from sqlguard.guard import GuardedSQL, GuardError, check_sql

__all__ = [
    "ChartSpec",
    "GuardError",
    "GuardedSQL",
    "check_sql",
    "recommend_chart",
]

__version__ = "0.1.0"
