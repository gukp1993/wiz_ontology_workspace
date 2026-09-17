"""flow_sql.py 动态 SQL 子集回归：解析 / 参数扫描 / 渲染 / test 表达式 / 错误用例。

纯 python3 标准库直跑，无 IO、不碰真实数据。运行：python3 tests/test_flow_sql_dialect.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench import flow_sql  # noqa: E402

PASSED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
        return
    print(f'[失败] {message}')
    if actual is not None:
        print('  实际:', actual)
    sys.exit(1)


def expect_error(fn, needle, message):
    try:
        fn()
    except flow_sql.FlowSqlError as exc:
        check(needle in str(exc), message, str(exc))
        return
    check(False, message + '（未抛错）')


# 1) 解析与渲染基础 ------------------------------------------------------------
sql, args = flow_sql.render('SELECT * FROM t WHERE a = #{a} AND b = :b', {'a': 1, 'b': 'x'})
check(sql == 'SELECT * FROM t WHERE a = %s AND b = %s' and args == [1, 'x'], '#{} 与旧 :name 等价预编译', (sql, args))

sql, args = flow_sql.render(
    'SELECT id FROM t WHERE status = #{status}'
    '<if test="min_balance != null"> AND balance >= #{min_balance}</if>',
    {'status': 'ok', 'min_balance': None})
check('balance' not in sql and args == ['ok'], 'if test 为 null 时分支不进 SQL', sql)

sql, args = flow_sql.render(
    'SELECT * FROM t WHERE 1=1'
    '<if test="a != null and a > 10"> AND a > #{a}</if>'
    '<choose><when test="b == \'x\'"> AND b = #{b}</when><otherwise> AND b = 0</otherwise></choose>',
    {'a': 20, 'b': 'x'})
check(args == [20, 'x'], 'and/choose/when 组合', (sql, args))

sql, args = flow_sql.render(
    '<choose><when test="a == 1">one</when><when test="a == 2">two</when><otherwise>many</otherwise></choose>',
    {'a': 2})
check(sql == 'two', '多 when 命中第二个', sql)

sql, args = flow_sql.render('UPDATE t <set> a = #{a}, b = #{b},</set> WHERE id = #{id}',
                            {'a': 1, 'b': None, 'id': 5})
check(sql == 'UPDATE t SET a = %s, b = %s WHERE id = %s' and args == [1, None, 5], '<set> 剥尾部逗号', sql)

sql, args = flow_sql.render('SELECT * FROM t <where> a = #{a} <if test="b != null"> AND b = #{b}</if></where>',
                            {'a': 1, 'b': None})
check(sql == 'SELECT * FROM t WHERE a = %s', '<where> 无内容不输出', sql)

sql, args = flow_sql.render(
    'SELECT * FROM t WHERE id IN <foreach collection="ids" item="x" open="(" close=")" separator=", ">#{x}</foreach>',
    {'ids': [1, 2, 3]})
check(sql == 'SELECT * FROM t WHERE id IN (%s, %s, %s)' and args == [1, 2, 3], 'foreach 展开占位', (sql, args))

sql, args = flow_sql.render(
    'INSERT INTO t(a, i) VALUES <foreach collection="rows" item="r" index="i" separator=", ">(#{r}, #{i})</foreach>',
    {'rows': ['p', 'q']})
check(sql == 'INSERT INTO t(a, i) VALUES (%s, %s), (%s, %s)' and args == ['p', 0, 'q', 1],
      'foreach 的 item 与 index', (sql, args))

sql, args = flow_sql.render('<trim prefix="WHERE " prefixOverrides="AND"> a = #{a}</trim>', {'a': 1})
check(sql == 'WHERE a = %s', 'trim prefixOverrides', sql)

sql, args = flow_sql.render("SELECT '${col}' = #{v}", {'col': 'name', 'v': 1})
check("'name'" in sql and args == [1], '${} 原文拼接、#{} 预编译共存', sql)

# 2) 参数扫描 ----------------------------------------------------------------
scanned = flow_sql.scan_template(
    'SELECT #{a}, ${b} FROM t <if test="c != null and d == 1"> WHERE e = :e</if>'
    '<foreach collection="ids" item="x">#{x}</foreach>')
check(scanned['named'] == ['a', 'b', 'c', 'd', 'e'], '扫描含 #{} ${} test 与 :name', scanned)
check(scanned['collections'] == ['ids'] and 'x' not in scanned['named'], '循环变量不进 named', scanned)

# 3) 错误用例 ----------------------------------------------------------------
expect_error(lambda: flow_sql.render('SELECT #{missing}', {}), 'missing', '未知占位报错')
expect_error(lambda: flow_sql.parse_template('<if test="a != null"> 未闭合'), '未闭合', '标签未闭合报错')
expect_error(lambda: flow_sql.parse_template('</if>'), '开始标签', '孤立闭合报错')
expect_error(lambda: flow_sql.render('a <if> x</if>', {}), 'test', '缺 test 报错')
expect_error(lambda: flow_sql.render('<foreach collection="ids" item="x">#{x}</foreach>', {}),
            '未提供', 'foreach 缺集合报错')
expect_error(lambda: flow_sql.render('<foreach collection="a" item="x">#{x}</foreach>', {'a': 'not-list'}),
            '列表', 'foreach 集合非列表报错')
expect_error(lambda: flow_sql.parse_test('a == '), '操作数', 'test 表达式残缺报错')
expect_error(lambda: flow_sql.render('SELECT ${x}', {}), '未提供', '${} 缺参报错')

# test 求值语义
tpl = '<if test="a != null and a != \'\'">X</if>'
check(flow_sql.render(tpl, {'a': None})[0] == '', 'null 先行短路（a != null 为假）')
check(flow_sql.render(tpl, {'a': ''})[0] == '', '空字符串被 != \'\' 拦下')
check(flow_sql.render(tpl, {'a': 'v'})[0] == 'X', '非空字符串通过')
check(flow_sql.render('<if test="flag">Y</if>', {'flag': True})[0] == 'Y', '布尔参数直接真值判断')
check(flow_sql.render('<if test="!(a == 1)">N</if>', {'a': 2})[0] == 'N', 'not 与括号')

print(f'\n全部通过：{len(PASSED)} 项')
