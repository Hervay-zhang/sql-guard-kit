"""查询结果到图表配置的规则选型。

为什么用规则而不是再调一次大模型：
    图表选型是确定性映射——"时间维度 → 折线、占比维度 → 饼图"是领域共识，
    交给规则准确率更高、零延迟、零成本，且结果可预测、可被单元测试覆盖。
    大模型适合做"读不懂的意图"，不适合做"有标准答案的分类"。

规则优先级（自上而下，命中即返回）：
    1. 结果为空                     → table
    2. 单行 + 单数值                 → kpi（指标卡）
    3. 占比型维度 + 类别数 ≤ 8       → pie（环形图）
    4. 时间型维度                    → line
    5. 其余多行多列                 → bar
    6. 兜底                        → table
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from numbers import Number
from typing import Any, Literal

ChartType = Literal["kpi", "pie", "line", "bar", "table"]

# 占比型维度：这些列做维度时，读者关心的是"各部分占整体多少"。
SHARE_KEYWORDS: tuple[str, ...] = (
    # 英文列名
    "region", "area", "province", "city", "status", "state", "category",
    "type", "channel", "source", "level", "grade", "segment",
    # 中文列名
    "地区", "区域", "省份", "城市", "状态", "品类", "类目", "分类",
    "类型", "渠道", "来源", "等级", "级别",
)

# 时间型维度：读者关心的是"随时间怎么变"。
TIME_KEYWORDS: tuple[str, ...] = (
    "date", "day", "month", "year", "week", "quarter", "time",
    "日期", "时间", "天", "周", "月", "年", "季度",
)

# 饼图最多展示的扇区数，超过这个数量饼图会糊成一团，退回柱状图。
MAX_PIE_SLICES = 8


@dataclass
class ChartSpec:
    """图表配置。``option`` 直接可交给 ECharts 渲染。"""

    type: ChartType
    title: str
    option: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_number(value: Any) -> bool:
    """数字判定。

    排除 bool：Python 里 True/False 也是 Number，但布尔列不是度量。
    注意 MySQL 驱动常把 SUM() 结果返回成字符串，那种情况下会被判为非数值，
    最终落到 table —— 这是有意的保守策略，宁可不画图也不画错图。
    """
    return isinstance(value, Number) and not isinstance(value, bool)


def _matches(dimension: str, keywords: tuple[str, ...]) -> bool:
    lowered = dimension.lower()
    return any(keyword in lowered for keyword in keywords)


def recommend_chart(
    title: str,
    columns: list[str],
    rows: list[list[Any]],
) -> ChartSpec:
    """根据结果集形状与维度语义推荐图表类型。

    Args:
        title: 图表标题，通常直接用用户的问题。
        columns: 列名列表。
        rows: 结果行，每行与 ``columns`` 等长。
    """
    clean_title = (title or "").strip().rstrip("？?")

    if not rows or not columns:
        return ChartSpec("table", clean_title)

    # 以首行推断哪些列是数值列（结果集同一列类型一致，看首行足够）。
    numeric_indexes = [i for i, value in enumerate(rows[0]) if _is_number(value)]

    # 规则 2：单行单值 —— 问"总共多少"这类问题，画图反而不如直接给数字。
    if len(rows) == 1 and len(numeric_indexes) == 1:
        return ChartSpec(
            "kpi",
            clean_title,
            {"value": rows[0][numeric_indexes[0]]},
        )

    if len(columns) < 2 or not numeric_indexes:
        return ChartSpec("table", clean_title)

    # 度量取最后一个数值列（多度量时通常最后一个才是用户要看的聚合结果），
    # 维度优先取第一列；若度量就在第 0 列，退而取第 1 列做维度。
    value_index = numeric_indexes[-1]
    dimension_index = 0 if value_index != 0 else 1

    dimension_name = str(columns[dimension_index])
    labels = [str(row[dimension_index]) for row in rows]
    values = [row[value_index] for row in rows]

    # 规则 3：占比型维度且扇区不过多 → 环形图
    if len(rows) <= MAX_PIE_SLICES and _matches(dimension_name, SHARE_KEYWORDS):
        return ChartSpec(
            "pie",
            clean_title,
            {
                "tooltip": {"trigger": "item"},
                "legend": {"bottom": 0},
                "series": [
                    {
                        "type": "pie",
                        "radius": ["45%", "70%"],
                        "data": [
                            {"name": label, "value": value}
                            for label, value in zip(labels, values, strict=True)
                        ],
                    }
                ],
            },
        )

    # 规则 4：时间维度 → 折线；其余 → 柱状
    chart_type: ChartType = (
        "line" if _matches(dimension_name, TIME_KEYWORDS) else "bar"
    )

    return ChartSpec(
        chart_type,
        clean_title,
        {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 48, "right": 24, "top": 24, "bottom": 48},
            "xAxis": {
                "type": "category",
                "data": labels,
                "axisLabel": {"rotate": 20},
            },
            "yAxis": {
                "type": "value",
                "splitLine": {"lineStyle": {"color": "#edf0f4"}},
            },
            "series": [
                {
                    "name": columns[value_index],
                    "type": chart_type,
                    "smooth": True,
                    "data": values,
                }
            ],
        },
    )
