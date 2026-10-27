"""SQL 安全校验的行为契约。"""

import pytest

from sqlguard.guard import GuardError, check_sql


class TestAllow:
    """合法只读语句必须放行，且被追加行数上限。"""

    def test_plain_select_is_wrapped_with_limit(self):
        result = check_sql("select id, name from t_user")
        assert result.limited is True
        assert result.sql.lower().startswith("select * from (")
        assert "limit 500" in result.sql.lower()
        assert "_bi_result" in result.sql

    def test_custom_limit(self):
        result = check_sql("select 1", max_rows=10)
        assert result.sql.lower().endswith("limit 10")

    def test_trailing_semicolons_are_stripped(self):
        result = check_sql("select 1 ;;;  ")
        assert result.limited is True

    def test_union_is_read_only(self):
        result = check_sql("select 1 as a union select 2 as a")
        assert "union" in result.sql.lower()

    def test_cte_containing_only_select(self):
        result = check_sql("with c as (select 1 as x) select * from c")
        assert result.limited is True

    def test_original_sql_is_kept_for_logging(self):
        raw = "select 1  ;"
        assert check_sql(raw).original == raw


class TestReject:
    """危险语句必须拦下，并给出稳定的原因码。"""

    @pytest.mark.parametrize(
        "sql",
        [
            "delete from t_order",
            "update t_order set amount = 0",
            "drop table t_order",
            "create table t_new (id int)",
            "alter table t_order add column x int",
            "truncate table t_order",
            "grant select on t_order to public",
            "insert into t_order select * from t_order",
            "merge into t_order t using t_src s on t.id = s.id when matched then delete",
            "copy t_order from '/tmp/x.csv'",
        ],
    )
    def test_write_statements_are_rejected(self, sql):
        with pytest.raises(GuardError):
            check_sql(sql)

    def test_multi_statement_is_rejected(self):
        """多语句是注入最常用的手法，必须整条拒绝而不是只取第一条。"""
        with pytest.raises(GuardError) as exc:
            check_sql("select 1; drop table t_order")
        assert exc.value.reason == GuardError.NOT_SINGLE

    def test_write_hidden_in_cte_is_caught_recursively(self):
        """根节点是 SELECT，但 CTE 里藏了 DELETE —— 只能靠递归遍历全树拦住。"""
        sql = "WITH d AS (DELETE FROM t_order RETURNING *) SELECT * FROM d"
        with pytest.raises(GuardError) as exc:
            check_sql(sql)
        assert exc.value.reason == GuardError.WRITE_OPERATION

    def test_non_read_only_root_is_rejected(self):
        with pytest.raises(GuardError) as exc:
            check_sql("show tables")
        assert exc.value.reason == GuardError.NOT_READ_ONLY

    def test_empty_sql(self):
        with pytest.raises(GuardError) as exc:
            check_sql("   ;  ")
        assert exc.value.reason == GuardError.EMPTY

    def test_syntax_error_is_rejected(self):
        with pytest.raises(GuardError) as exc:
            check_sql("select from where")
        assert exc.value.reason == GuardError.UNPARSABLE


class TestRewrite:
    """通过校验的语句会被 AST 重新序列化，这本身就是一层防护。"""

    def test_comment_is_dropped_after_reserialization(self):
        """注释里的分号不会变成第二条语句，且注释在重写后被丢弃。"""
        result = check_sql("select 1 /* ; drop table t_order */")
        assert "drop" not in result.sql.lower()
        assert "/*" not in result.sql

    def test_line_comment_cannot_smuggle_statement(self):
        result = check_sql("select 1 -- ; drop table t_order")
        assert "drop" not in result.sql.lower()

    def test_output_is_normalized(self):
        result = check_sql("SELECT   id\nFROM   t_order")
        assert "  " not in result.sql
