"""跑一下看效果：python examples/demo.py"""

import json

from sqlguard import GuardError, check_sql, recommend_chart

DANGEROUS = [
    "select id from t_order",
    "select 1; drop table t_order",
    "WITH d AS (DELETE FROM t_order RETURNING *) SELECT * FROM d",
    "update t_order set amount = 0",
    "show tables",
]

print("=" * 70)
print("一、SQL 安全校验")
print("=" * 70)
for sql in DANGEROUS:
    try:
        result = check_sql(sql)
        print(f"\n✅ 放行  {sql}")
        print(f"   → {result.sql}")
    except GuardError as exc:
        print(f"\n⛔ 拦截  {sql}")
        print(f"   → [{exc.reason}] {exc}")

print()
print("=" * 70)
print("二、图表选型")
print("=" * 70)

CASES = [
    ("上周共有多少笔订单？", ["count"], [[1286]]),
    ("各地区销售额", ["region", "amount"], [["华东", 128000], ["华南", 96000], ["华北", 74000]]),
    ("今年每月新增客户", ["month", "new_users"], [["01", 120], ["02", 168], ["03", 143]]),
    ("各商品销量排行", ["product", "qty"], [["手机", 320], ["电脑", 210], ["耳机", 460]]),
    ("列一下全部客户", ["name", "city"], [["张三", "上海"], ["李四", "深圳"]]),
]

for question, columns, rows in CASES:
    spec = recommend_chart(question, columns, rows)
    head = json.dumps(spec.option, ensure_ascii=False)
    print(f"\n{question}")
    print(f"   列: {columns}")
    print(f"   → 图表类型: {spec.type}")
    print(f"   → 配置: {head[:110]}{'...' if len(head) > 110 else ''}")
