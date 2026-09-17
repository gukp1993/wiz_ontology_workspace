"""显示格式优化（任务 C）：常用格式 builtin 语义测试，python 直调 workbench.formatting.format_value。

依据《显示格式优化需求_v1_20260915.md》第 3 节规则与第 8 节验收 A4—A9 写期望；
冻结 schema：dataType string|number|boolean|date|time|array|struct|timeSeries，
config.mode builtin|natural|code（历史 code 兼容）。

纯 python3 脚本（无 pytest），builtin 路径经 Node 子进程执行（与生产预览同一路径）。
自然语言用例通过临时 HOME 隔离本机 ~/.config 大模型配置，绝不发起真实请求。
运行：WIZ_WORKBENCH_ROOT=$(mktemp -d) python3 tests/test_formatting_builtin.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES = []

# --- 环境：HOME 指向空临时目录 + 清除 FORMAT_LLM_*，保证自然语言分支拿不到任何模型配置 -----
_HOME_BACKUP = os.environ.get('HOME')
_ISOLATED_HOME = Path(tempfile.mkdtemp(prefix='wiz_fmt_builtin_home_'))
os.environ['HOME'] = str(_ISOLATED_HOME)
for _key in ('FORMAT_LLM_URL', 'FORMAT_LLM_MODEL', 'FORMAT_LLM_API_KEY'):
    os.environ.pop(_key, None)


def fmt(value, data_type, config):
    from workbench.formatting import format_value
    return format_value(value, data_type, config)


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        print(f'失败  {name}: {type(exc).__name__}: {exc}')


def expect(value, data_type, config, want):
    out = fmt(value, data_type, config)
    assert out == want, f'实际 {out!r}，期望 {want!r}（dataType={data_type}，config={json.dumps(config, ensure_ascii=False)}）'


def expect_error(value, data_type, config, *fragments):
    try:
        out = fmt(value, data_type, config)
    except ValueError as exc:
        message = str(exc)
        if fragments:
            assert any(f in message for f in fragments), f'报错 {message!r} 未包含任一期望片段 {fragments}'
        assert message, '报错文案为空，无法向用户解释'
        return message
    raise AssertionError(f'未按预期报错，实际输出 {out!r}（dataType={data_type}，config={json.dumps(config, ensure_ascii=False)}）')


def expect_match(value, data_type, config, pattern, what):
    import re
    out = fmt(value, data_type, config)
    assert re.search(pattern, out), f'实际 {out!r} 不匹配 {what}（{pattern}）'


# --- 数值（A4 / A5 / 需求 3.1）-------------------------------------------------------

def test_percent_two_scales():
    # A4：70.56 按 0—100 口径一位小数 → 70.6%
    expect(70.56, 'number', {'mode': 'builtin', 'style': 'percent', 'percentInput': 'hundred', 'decimals': 1}, '70.6%')
    # A4：0.7056 按 0—1 口径一位小数 → 同样 70.6%
    expect(0.7056, 'number', {'mode': 'builtin', 'style': 'percent', 'percentInput': 'ratio', 'decimals': 1}, '70.6%')


def test_percent_zero_and_legacy_scale_missing():
    expect(0, 'number', {'mode': 'builtin', 'style': 'percent', 'percentInput': 'ratio', 'decimals': 1}, '0.0%')
    # 历史配置未写 percentInput：沿用现状 ratio 口径（0.7056 → 70.6%），不静默改口径
    expect(0.7056, 'number', {'mode': 'builtin', 'style': 'percent', 'decimals': 1}, '70.6%')


def test_standard_negative_grouping():
    # 负数 + 千位分隔默认开启 + decimals 固定两位
    expect(-1234.5, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 2}, '-1,234.50')


def test_decimal_padding():
    # 补零：decimals:2 的 5 → 5.00
    expect(5, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 2}, '5.00')


def test_decimals_overrides_legacy_min_max():
    # decimals 固定精度优先于旧 min/max（需求 3.1“填写后最少和最多一致”）
    expect(1.5, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 2, 'minDecimals': 0, 'maxDecimals': 4}, '1.50')


def test_scientific():
    import re
    out = fmt(12345, 'number', {'mode': 'builtin', 'style': 'scientific', 'decimals': 2})
    assert re.fullmatch(r'1\.23E\+?4', out), f'科学计数实际 {out!r}，期望 1.23E4 形态'


def test_invalid_decimals():
    for bad in (21, -1, 1.5):
        expect_error(70.56, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': bad}, '小数位')


def test_legacy_min_max_preserved():
    # 旧 min≠max 未填写 decimals：保留原有精度行为（1.5 → '1.5'，不强制补零）
    expect(1.5, 'number', {'mode': 'builtin', 'style': 'standard', 'minDecimals': 1, 'maxDecimals': 3}, '1.5')
    expect(1.234, 'number', {'mode': 'builtin', 'style': 'standard', 'minDecimals': 1, 'maxDecimals': 3}, '1.234')


def test_a5_suffix_grouping():
    # A5：1234.5 固定两位、千位分隔、后缀 ' kW' → 1,234.50 kW
    expect(1234.5, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 2, 'suffix': ' kW'}, '1,234.50 kW')
    expect(1234.5, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 2, 'grouping': False, 'suffix': ' kW'}, '1234.50 kW')


def test_currency_default_cny():
    # 未配置币种默认 CNY；未填 decimals 沿用原有默认精度行为（min0/max2 → 1,234.5）
    out = fmt(1234.5, 'number', {'mode': 'builtin', 'style': 'currency'})
    assert '1,234.5' in out, f'货币默认精度（未填 decimals 沿用原行为）：实际 {out!r}'
    assert ('¥' in out) or ('CNY' in out) or ('CN¥' in out), f'未配置币种时默认 CNY：实际 {out!r}'
    # 填写 decimals 后为固定小数位
    out2 = fmt(1234.5, 'number', {'mode': 'builtin', 'style': 'currency', 'decimals': 2})
    assert '1,234.50' in out2, f'货币 decimals:2 应显示两位小数：实际 {out2!r}'


def test_prefix():
    expect(70.56, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 1, 'prefix': '≈'}, '≈70.6')


# --- 文本 / 布尔 / 空值（A6 / 需求 3.2）----------------------------------------------

def test_string_template():
    expect('001', 'string', {'mode': 'builtin', 'style': 'template', 'template': 'ESS-{value}'}, 'ESS-001')


def test_string_mapping():
    config = {'mode': 'builtin', 'style': 'mapping', 'mappings': [{'from': 'running', 'to': '运行中'}]}
    expect('running', 'string', config, '运行中')
    # 未匹配保留原值，不静默清空
    expect('stopped', 'string', config, 'stopped')


def test_falsy_not_null():
    # 0 / false / '' 不是空值：走正常格式化而非 emptyText（A6）
    expect(0, 'number', {'mode': 'builtin', 'style': 'standard', 'decimals': 1}, '0.0')
    expect(False, 'boolean', {'mode': 'builtin'}, '否')
    expect('', 'string', {'mode': 'builtin', 'style': 'template', 'template': 'ESS-{value}'}, 'ESS-')


def test_null_empty_text():
    expect(None, 'string', {'mode': 'builtin'}, '—')
    expect(None, 'number', {'mode': 'builtin', 'style': 'percent', 'percentInput': 'hundred', 'decimals': 1}, '—')
    expect(None, 'string', {'mode': 'builtin', 'emptyText': '无数据'}, '无数据')


def test_boolean_custom():
    expect(True, 'boolean', {'mode': 'builtin', 'style': 'custom', 'trueText': '启用', 'falseText': '停用'}, '启用')
    expect(False, 'boolean', {'mode': 'builtin', 'style': 'custom', 'trueText': '启用', 'falseText': '停用'}, '停用')


def test_boolean_default():
    expect(True, 'boolean', {'mode': 'builtin', 'style': 'default'}, '是')
    expect(False, 'boolean', {'mode': 'builtin', 'style': 'default'}, '否')


# --- 日期 / 时间（A7 / 需求 3.3）------------------------------------------------------

def test_date_precision():
    expect('2026-09-15', 'date', {'mode': 'builtin', 'style': 'date', 'precision': 'year'}, '2026')
    expect('2026-09-15', 'date', {'mode': 'builtin', 'style': 'date', 'precision': 'month'}, '2026-09')
    expect('2026-09-15', 'date', {'mode': 'builtin', 'style': 'date', 'precision': 'day'}, '2026-09-15')


def test_date_custom_pattern():
    expect('2026-09-15', 'date', {'mode': 'builtin', 'style': 'custom', 'pattern': 'YYYY/MM/DD'}, '2026/09/15')
    # 纯日期禁补时分秒（需求 3.3）
    expect_error('2026-09-15', 'date', {'mode': 'builtin', 'style': 'custom', 'pattern': 'YYYY-MM-DD HH:mm'}, '时分秒', 'HH')


def test_datetime_timezone_conversion():
    # +08:00 转 UTC：14:30(+08) → 06:30(UTC)
    out = fmt('2026-09-15T14:30:00+08:00', 'time',
              {'mode': 'builtin', 'style': 'datetime', 'precision': 'minute', 'timezone': 'UTC'})
    assert out == '2026-09-15 06:30', f'UTC 换算实际 {out!r}，期望 2026-09-15 06:30'


def test_datetime_default_timezone():
    # 默认 Asia/Shanghai：原墙钟时间不变
    expect('2026-09-15T14:30:25+08:00', 'time', {'mode': 'builtin', 'style': 'datetime', 'precision': 'second'},
           '2026-09-15 14:30:25')


def test_time_style_minute():
    expect('2026-09-15T14:30:25+08:00', 'time', {'mode': 'builtin', 'style': 'time', 'precision': 'minute'}, '14:30')


def test_invalid_timezone():
    expect_error('2026-09-15T14:30:00+08:00', 'time',
                 {'mode': 'builtin', 'style': 'datetime', 'precision': 'minute', 'timezone': 'Mars/Olympus'}, '时区')


def test_invalid_datetime():
    expect_error('2026-13-01T00:00:00+08:00', 'time', {'mode': 'builtin', 'style': 'datetime', 'precision': 'minute'},
                 '无效', '格式', '日期', '时间')


def test_relative_time():
    # 相对时间不锁定漂移文本，只断言方向词（A7：沿用当前运行时规则）
    expect_match('2020-01-01T00:00:00+08:00', 'time', {'mode': 'builtin', 'style': 'relative'}, '前', '过去方向「前」')
    expect_match('2035-06-01T00:00:00+08:00', 'time', {'mode': 'builtin', 'style': 'relative'}, '后', '未来方向「后」')


# --- 数组（A8 / 需求 3.4）-------------------------------------------------------------

def test_array_list_and_join():
    expect(['告警A', '告警B', '告警C'], 'array', {'mode': 'builtin', 'style': 'list'}, '告警A\n告警B\n告警C')
    expect(['A', 'B', 'C'], 'array', {'mode': 'builtin', 'style': 'join'}, 'A、B、C')
    expect(['A', 'B'], 'array', {'mode': 'builtin', 'style': 'join', 'separator': '; '}, 'A; B')


def test_array_max_items_default_10():
    items = [f'i{n:02d}' for n in range(1, 13)]  # i01..i12 共 12 项
    out = fmt(items, 'array', {'mode': 'builtin', 'style': 'list'})
    assert out.endswith('…共12项，已显示前10项'), f'省略提示实际结尾 {out[-30:]!r}'
    for kept in items[:10]:
        assert kept in out, f'前 10 项应保留：缺 {kept}（实际 {out!r}）'
    for dropped in items[10:]:
        assert dropped not in out, f'第 11 项起不显示：出现 {dropped}（实际 {out!r}）'


def test_array_max_items_explicit():
    items = ['x1', 'x2', 'x3', 'x4', 'x5']
    out = fmt(items, 'array', {'mode': 'builtin', 'style': 'join', 'maxItems': 3})
    assert out.endswith('…共5项，已显示前3项'), f'自定义 maxItems 提示实际结尾 {out[-30:]!r}'
    assert 'x3' in out and 'x4' not in out and 'x5' not in out, f'截断结果实际 {out!r}'


def test_array_max_items_bounds():
    for bad in (0, 101):
        expect_error(['a', 'b'], 'array', {'mode': 'builtin', 'style': 'join', 'maxItems': bad}, '项数', '1', '100')


def test_array_element_format():
    config = {'mode': 'builtin', 'style': 'join', 'elementType': 'number',
              'elementFormat': {'style': 'standard', 'decimals': 1}}
    expect([70.56, 8], 'array', config, '70.6、8.0')
    # 未配置元素格式：默认原样（需求 3.4）
    expect([70.56, 8], 'array', {'mode': 'builtin', 'style': 'join'}, '70.56、8')


# --- 结构体（A8 / 需求 3.4）------------------------------------------------------------

def test_struct_pairs_fields_order():
    expect({'b': 1, 'a': 2}, 'struct', {'mode': 'builtin', 'style': 'pairs', 'fields': ['a', 'b']}, 'a: 2；b: 1')
    # 未配置 fields：按输入字段顺序
    expect({'b': 1, 'a': 2}, 'struct', {'mode': 'builtin', 'style': 'pairs'}, 'b: 1；a: 2')
    # 自定义分隔符
    expect({'a': 2, 'b': 1}, 'struct', {'mode': 'builtin', 'style': 'pairs', 'fields': ['a', 'b'], 'separator': ' | '},
           'a: 2 | b: 1')


def test_struct_template():
    config = {'mode': 'builtin', 'style': 'template', 'template': '{manufacturer} / {model}'}
    expect({'manufacturer': 'BYD', 'model': 'BYD-3000'}, 'struct', config, 'BYD / BYD-3000')
    # 缺失字段用空值占位（默认 —）
    expect({'manufacturer': 'BYD'}, 'struct', config, 'BYD / —')
    expect({'manufacturer': 'BYD'}, 'struct', {**config, 'emptyText': '未填'}, 'BYD / 未填')


# --- 时间序列（A9 / 需求 3.4）----------------------------------------------------------

def test_time_series_formatted():
    config = {'mode': 'builtin', 'style': 'series',
              'timeFormat': {'style': 'datetime', 'precision': 'minute'},
              'valueFormat': {'style': 'percent', 'percentInput': 'hundred', 'decimals': 1}}
    value = [{'timestamp': '2026-09-15T14:30:00+08:00', 'value': 70.56}]
    expect(value, 'timeSeries', config, '2026-09-15 14:30  70.6%')
    # 多条记录逐条换行，时间与值分别格式化
    two = value + [{'timestamp': '2026-09-15T15:00:00+08:00', 'value': 0}]
    out = fmt(two, 'timeSeries', config)
    assert out == '2026-09-15 14:30  70.6%\n2026-09-15 15:00  0.0%', f'多记录实际 {out!r}'


def test_time_series_timezone():
    config = {'mode': 'builtin', 'style': 'series',
              'timeFormat': {'style': 'datetime', 'precision': 'minute', 'timezone': 'UTC'},
              'valueFormat': {'style': 'standard', 'decimals': 1}}
    out = fmt([{'timestamp': '2026-09-15T14:30:00+08:00', 'value': 70.5}], 'timeSeries', config)
    assert '06:30' in out, f'时间部分应按 UTC 显示 06:30，实际 {out!r}'


def test_time_series_empty():
    expect([], 'timeSeries', {'mode': 'builtin', 'style': 'series'}, '暂无记录')


def test_time_series_missing_fields():
    base = {'mode': 'builtin', 'style': 'series',
            'timeFormat': {'style': 'datetime', 'precision': 'minute'},
            'valueFormat': {'style': 'standard', 'decimals': 1}}
    expect_error([{'timestamp': '2026-09-15T14:30:00+08:00'}], 'timeSeries', base, 'value', '值', '字段')
    expect_error([{'value': 70.5}], 'timeSeries', base, 'timestamp', '时间', '字段')


def test_time_series_null():
    expect(None, 'timeSeries', {'mode': 'builtin', 'style': 'series', 'emptyText': '无采样'}, '无采样')


# --- 历史 code 兼容（需求第 7 节 / A12）------------------------------------------------

def test_legacy_code_mode():
    expect('abc', 'string', {'mode': 'code', 'code': 'return String(value).toUpperCase()'}, 'ABC')
    expect('ESS-001', 'string', {'mode': 'code', 'code': 'return value.replace("ESS-", "")'}, '001')


# --- 自然语言（A10 / 需求 3.2、第 4 节）-------------------------------------------------

def test_natural_null_local_placeholder():
    # 空值本地返回占位，不依赖模型配置、不发请求（隔离 HOME 下无任何配置）
    expect(None, 'string', {'mode': 'natural', 'instruction': '把值转成中文描述', 'emptyText': '无数据'}, '无数据')
    expect(None, 'number', {'mode': 'natural', 'instruction': '保留一位小数'}, '—')


def test_natural_no_model_error():
    # 无模型配置且已填写规则：先报配置错误（formatting.py 现状：配置检查在前）
    message = expect_error('70', 'string', {'mode': 'natural', 'instruction': '保留一位小数并加百分号'},
                           '尚未配置大模型')
    assert '规则可保存' in message or '保存' in message, f'应提示规则可保存，实际 {message!r}'


def test_natural_empty_instruction_error():
    # instruction 为空：配置检查在前（现状），报任一可读文案即可
    expect_error('70', 'string', {'mode': 'natural'}, '尚未配置大模型', '自然语言格式规则')


# --------------------------------------------------------------------------------------

if __name__ == '__main__':
    if shutil.which('node') is None:
        print('失败  环境：未找到 node，builtin 格式化无法执行')
        FAILURES.append('node 环境')
    run('数值-1 A4 百分比两种口径一致（70.6%）', test_percent_two_scales)
    run('数值-2 百分比 0 显示 0.0%；未写口径按历史 ratio', test_percent_zero_and_legacy_scale_missing)
    run('数值-3 负数与千位分隔 -1,234.50', test_standard_negative_grouping)
    run('数值-4 补零 decimals:2 的 5 → 5.00', test_decimal_padding)
    run('数值-5 decimals 优先于旧 min/max', test_decimals_overrides_legacy_min_max)
    run('数值-6 科学计数 12345 → 1.23E4', test_scientific)
    run('数值-7 非法 decimals（21/-1/1.5）报错', test_invalid_decimals)
    run('数值-8 旧 min≠max 保留原精度行为', test_legacy_min_max_preserved)
    run('数值-9 A5 1,234.50 kW / 无千位分隔变体', test_a5_suffix_grouping)
    run('数值-10 货币默认 CNY 两位小数', test_currency_default_cny)
    run('数值-11 前缀 ≈70.6', test_prefix)
    run('文本-1 模板 ESS-{value}', test_string_template)
    run('文本-2 映射命中与未匹配保留原值', test_string_mapping)
    run('空值-1 0/false/空串不是 null', test_falsy_not_null)
    run('空值-2 null → emptyText（默认 —）', test_null_empty_text)
    run('布尔-1 自定义 是/否 文字', test_boolean_custom)
    run('布尔-2 默认 是/否', test_boolean_default)
    run('日期-1 三档精度 year/month/day', test_date_precision)
    run('日期-2 自定义模板 + 禁补时分秒', test_date_custom_pattern)
    run('时间-1 +08:00 → UTC 显示 06:30', test_datetime_timezone_conversion)
    run('时间-2 默认时区墙钟不变（second 精度）', test_datetime_default_timezone)
    run('时间-3 time 样式 minute 精度', test_time_style_minute)
    run('时间-4 无效时区报错', test_invalid_timezone)
    run('时间-5 无效日期报错', test_invalid_datetime)
    run('时间-6 相对时间仅断言 前/后', test_relative_time)
    run('数组-1 list 换行与 join 分隔符', test_array_list_and_join)
    run('数组-2 默认 maxItems=10 省略提示', test_array_max_items_default_10)
    run('数组-3 自定义 maxItems=3', test_array_max_items_explicit)
    run('数组-4 maxItems 越界（0/101）报错', test_array_max_items_bounds)
    run('数组-5 元素格式（数值一位小数）与默认原样', test_array_element_format)
    run('结构-1 fields 顺序 / 未配置按输入顺序 / 分隔符', test_struct_pairs_fields_order)
    run('结构-2 模板与缺失字段占位', test_struct_template)
    run('序列-1 时间/值分别格式化，逐条换行', test_time_series_formatted)
    run('序列-2 时间部分按时区显示', test_time_series_timezone)
    run('序列-3 空数组 → 暂无记录', test_time_series_empty)
    run('序列-4 缺 timestamp/value 报错', test_time_series_missing_fields)
    run('序列-5 null → emptyText', test_time_series_null)
    run('兼容-1 历史 code 模式仍可执行', test_legacy_code_mode)
    run('自然语言-1 null 本地占位不依赖模型', test_natural_null_local_placeholder)
    run('自然语言-2 无模型配置可读报错', test_natural_no_model_error)
    run('自然语言-3 空 instruction 可读报错', test_natural_empty_instruction_error)
    # 还原 HOME，避免影响同进程后续测试
    if _HOME_BACKUP is not None:
        os.environ['HOME'] = _HOME_BACKUP
    else:
        os.environ.pop('HOME', None)
    shutil.rmtree(_ISOLATED_HOME, ignore_errors=True)
    if FAILURES:
        print(f'\n{len(FAILURES)} 项失败：{", ".join(FAILURES)}')
        sys.exit(1)
    print('\n全部通过')
