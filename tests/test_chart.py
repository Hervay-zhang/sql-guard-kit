"""图表选型规则的行为契约。"""

from decimal import Decimal

import pytest

from sqlguard.chart import recommend_chart


class TestKpi:
    def test_single_row_single_number(self):
        spec = recommend_chart("一共多少客户？", ["count"], [[42]])
        assert spec.type == "kpi"
        assert spec.option["value"] == 42
        assert spec.title == "一共多少客户"  # 问号被去掉

    def test_single_row_with_label_column_is_still_kpi(self):
        """单行单值时，多出来的标签列不影响判定 —— 数值本身就是要看的那个数。"""
        spec = recommend_chart("客户数", ["name", "count"], [["全部", 42]])
        assert spec.type == "kpi"
        assert spec.option["value"] == 42

    def test_decimal_is_numeric(self):
        """数据库 numeric / sum 常返回 Decimal，必须认作数值。"""
        spec = recommend_chart("总金额", ["total"], [[Decimal("1234.50")]])
        assert spec.type == "kpi"


class TestDimVsMeasure:
    def test_share_dimension_becomes_pie(self):
        spec = recommend_chart(
            "各地区销售额",
            ["region", "amount"],
            [["华东", 100], ["华南", 80], ["华北", 60]],
        )
        assert spec.type == "pie"
        assert spec.option["series"][0]["type"] == "pie"
        assert [d["name"] for d in spec.option["series"][0]["data"]] == ["华东", "华南", "华北"]

    def test_chinese_dimension_name_also_matches(self):
        spec = recommend_chart(
            "各品类库存",
            ["品类", "qty"],
            [["手机", 10], ["电脑", 20]],
        )
        assert spec.type == "pie"

    def test_too_many_slices_falls_back_to_bar(self):
        rows = [[f"类目{i}", i] for i in range(20)]
        spec = recommend_chart("各类目销量", ["category", "qty"], rows)
        assert spec.type == "bar"

    @pytest.mark.parametrize("column", ["month", "order_date", "月份", "统计日期"])
    def test_time_dimension_becomes_line(self, column):
        spec = recommend_chart(
            "月度销售额",
            [column, "amount"],
            [["2026-01", 10], ["2026-02", 20]],
        )
        assert spec.type == "line"
        assert spec.option["series"][0]["smooth"] is True

    def test_other_dimension_becomes_bar(self):
        spec = recommend_chart(
            "各商品销量",
            ["product", "qty"],
            [["手机", 10], ["电脑", 20]],
        )
        assert spec.type == "bar"

    def test_measure_in_first_column_uses_second_as_dimension(self):
        spec = recommend_chart(
            "销量排行",
            ["qty", "product"],
            [[10, "手机"], [20, "电脑"]],
        )
        assert spec.type == "bar"
        assert spec.option["xAxis"]["data"] == ["手机", "电脑"]


class TestFallback:
    def test_empty_result_returns_table(self):
        spec = recommend_chart("随便问问", ["a", "b"], [])
        assert spec.type == "table"
        assert spec.option == {}

    def test_no_numeric_column_returns_table(self):
        spec = recommend_chart("列一下客户", ["name", "city"], [["张三", "上海"]])
        assert spec.type == "table"

    def test_single_column_returns_table(self):
        spec = recommend_chart("所有编号", ["id"], [[1], [2]])
        assert spec.type == "table"

    def test_numeric_as_string_returns_table(self):
        """MySQL 驱动把 SUM 返回成字符串时，宁可退回表格也不画错图。"""
        spec = recommend_chart("总金额", ["total"], [["1234.5"]])
        assert spec.type == "table"

    def test_bool_is_not_measure(self):
        spec = recommend_chart("是否有效", ["name", "ok"], [["a", True], ["b", False]])
        assert spec.type == "table"

    def test_spec_is_json_serializable(self):
        import json

        spec = recommend_chart("各地区销售额", ["region", "amount"], [["华东", 1]])
        json.dumps(spec.to_dict())
