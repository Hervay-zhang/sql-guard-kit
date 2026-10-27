"""执行前的 SQL 安全校验。

核心思路：不靠正则、不靠关键词黑名单，而是把 SQL 解析成 AST 之后按语法结构判断：

1. 必须是单语句（防止 ``SELECT 1; DROP TABLE t`` 这类多语句拼接）；
2. 根节点必须落在只读白名单里（SELECT / UNION / INTERSECT / EXCEPT）；
3. 递归遍历整棵 AST，任何位置出现写操作节点都拒绝（防止 CTE、子查询里藏写）；
4. 通过校验的语句由 AST 重新序列化并丢弃注释，再套一层行数上限。

为什么不用字符串匹配：注释（``--``、``/* */``）、大小写、空白、同义词都能绕过
关键词匹配；而 AST 是解析后的结构，绕不过去。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

# 写操作 / 管理类节点：整棵 AST 里出现任何一个都拒绝。
# Command 兜底那些被 sqlglot 解析成"原始命令"的语句（如 SHOW、SET、CALL）。
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Copy,
    exp.Command,
)

# 只读白名单：根节点必须是这几种之一。
READ_ONLY_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)


class GuardError(ValueError):
    """SQL 未通过安全校验。

    ``reason`` 是稳定的机器可读原因码，便于上层做埋点统计与提示文案分流。
    """

    EMPTY = "EMPTY_SQL"
    UNPARSABLE = "UNPARSABLE_SQL"
    NOT_SINGLE = "NOT_SINGLE_STATEMENT"
    NOT_READ_ONLY = "ROOT_NOT_READ_ONLY"
    WRITE_OPERATION = "WRITE_OPERATION"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class GuardedSQL:
    """校验通过后的语句。"""

    sql: str  # 已重写、带行数上限，可直接执行
    original: str  # 模型原始输出，仅用于日志与排障
    limited: bool  # 是否追加了行数上限


def check_sql(
    sql: str,
    *,
    max_rows: int = 500,
    dialect: str = "postgres",
) -> GuardedSQL:
    """校验并重写一条只读 SQL，不通过则抛 :class:`GuardError`。

    Args:
        sql: 模型生成的原始 SQL。
        max_rows: 外层行数上限，防止大表全表扫描把结果集打爆。
        dialect: sqlglot 方言名，需与目标库一致（解析和序列化都用它）。
    """
    if not sql or not sql.strip():
        raise GuardError(GuardError.EMPTY, "SQL 为空")

    # 只去掉首尾空白与结尾分号；中间的分号保留给解析器判断是否多语句。
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise GuardError(GuardError.EMPTY, "SQL 为空")

    try:
        statements = sqlglot.parse(cleaned, dialect=dialect)
    except SqlglotError as exc:  # 语法错误同样不放行
        raise GuardError(GuardError.UNPARSABLE, f"SQL 无法解析：{exc}") from exc

    parsed = [stmt for stmt in statements if stmt is not None]
    if len(parsed) != 1:
        raise GuardError(
            GuardError.NOT_SINGLE,
            f"只允许执行单条语句，实际解析出 {len(parsed)} 条",
        )

    statement = parsed[0]

    if not isinstance(statement, READ_ONLY_ROOTS):
        raise GuardError(
            GuardError.NOT_READ_ONLY,
            f"根节点为 {type(statement).__name__}，只允许 SELECT / UNION / INTERSECT / EXCEPT",
        )
    if statement.find(*FORBIDDEN_NODES) is not None:
        # 关键一步：递归检查全树，能拦住 WITH ... AS (DELETE ... RETURNING *) 这类
        # 根节点是 SELECT、但内部藏了写操作的语句。
        raise GuardError(GuardError.WRITE_OPERATION, "语句内部包含写操作，已拦截")

    # 由 AST 重新序列化：多余空白被规范化，注释显式丢弃（sqlglot 默认会保留注释）。
    # 注释虽不影响执行，但让它继续出现在数据库日志和审计里，会掩盖"语句曾被重写"这件事。
    normalized = statement.sql(dialect=dialect, comments=False)

    # 行数上限套在外层，不改写原语句语义，也不会因为子查询已有 LIMIT 而冲突。
    # 表别名固定为 _bi_result，避免与用户 SQL 里的别名撞名（下划线前缀在业务表命名中不用）。
    limited_sql = f"select * from ({normalized}) _bi_result limit {int(max_rows)}"
    return GuardedSQL(sql=limited_sql, original=sql, limited=True)
