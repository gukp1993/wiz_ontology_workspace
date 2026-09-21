"""『从物料自动构建本体』解析器层回归（验收缺陷 D18 / D15 / D12 解析侧）。

覆盖范围（只测 `workbench/ontology_build/parsers/`，不起 HTTP 服务、不碰真实 ontology/）：
* D18a Markdown 表格：表格与单元格事实的 locator.line 必须是原文真实行号（曾恒为 0）；
  起始行/结束行/分隔行如实记录；对齐分隔行不得当数据行；行首 `|` 不再产生幽灵空列。
* D18b DDL 命名外键：`CONSTRAINT x FOREIGN KEY (a) REFERENCES t(b)` 的 from.columns 必须是
  约束自己的列（曾按位置组取到约束名）；顺带锁定表级约束的真实行号（同一行多个约束曾因
  locator+snippet 相同被 FactSink 去重吞掉）。
* D18c Vue 模板绑定：`_directive_name` / `_expression_identifiers` 必须存在并可用（曾因未定义
  导致整份 .vue 解析失败）；引号配对不再被反向引用组号带偏；指令原文、修饰符、真实行号入证据。
* D15 文本型 PDF：合成最小可提取文本 PDF，逐页产出带真实页码的事实；近空白页按扫描页显式
  报告（不假装 OCR）；同时锁定「文件句柄必须在解析期间保持打开」。
* D12 解析侧解压守卫：`zipguard.CappedZipFile` 按**实际解压字节**封顶（声明值即超限零字节拒绝、
  累计超限流式中止），伪造声明头部的 docx/xlsx 被显式拒绝且不被当作完整文档解析。

隔离：全部材料写进 `tempfile.mkdtemp()` 新建的临时目录，运行结束删除；不写任何真实数据根。

运行：python3 tests/test_ontology_build_parsers.py
"""
import io
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

TMP = Path(tempfile.mkdtemp(prefix='wiz_parser_defs_'))

PASSED = []
FAILED = []


def check(condition, name, actual=None):
    if condition:
        PASSED.append(name)
        print('  ok   %s' % name)
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, '' if actual is None else '  actual=%r' % (actual,)))


def write(name, data):
    """把合成材料写入临时根，返回绝对路径（str）。"""
    path = TMP / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), 'wb') as handle:
        handle.write(data if isinstance(data, bytes) else str(data).encode('utf-8'))
    return str(path)


def facts_of(result, kind=None):
    return [fact for fact in result.facts if kind is None or fact.kind == kind]


def notes_of(result):
    return [str(note) for note in (result.coverage.get('notes') or [])]


def failures_of(result):
    return list(result.coverage.get('failedSegments') or [])


# ---------------------------------------------------------------------------
# D18a：Markdown 表格定位与内容
# ---------------------------------------------------------------------------
MD_SAMPLE = """# 设备台账

说明段落第一行。

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| battery_id | string | 电池簇标识 |
| soc | double | 荷电状态 |

## 另一节

结尾段落。
"""

MD_HEADER_LINE = 5
MD_SEPARATOR_LINE = 6
MD_FIRST_DATA_LINE = 7
MD_LAST_DATA_LINE = 8


def test_markdown_table_locator():
    from workbench.ontology_build.parsers import markdown_parser

    path = write('md/table_demo.md', MD_SAMPLE)
    result = markdown_parser.parse(path, 'bm-md')

    tables = facts_of(result, 'table')
    check(len(result.facts) > 0 and len(tables) == 1, 'D18a Markdown 表格事实被产出',
          actual=[fact.kind for fact in result.facts])
    table = tables[0] if tables else None
    check(table is not None and table.locator.get('line') == MD_HEADER_LINE,
          'D18a 表格事实 locator.line 为表头真实行号（修复前恒为 0）',
          actual=table.locator.get('line') if table else None)
    data = (table.data if table else {})
    check(data.get('startLine') == MD_HEADER_LINE and data.get('endLine') == MD_LAST_DATA_LINE,
          'D18a startLine/endLine 覆盖整张表格',
          actual=(data.get('startLine'), data.get('endLine')))
    check(data.get('separatorLine') == MD_SEPARATOR_LINE,
          'D18a 对齐分隔行按真实行号单独记录', actual=data.get('separatorLine'))
    check(data.get('header') == ['字段名', '类型', '说明'],
          'D18a 表头列名正确（行首 `|` 不再产生幽灵空列）', actual=data.get('header'))
    check(data.get('rows') == 2 and data.get('columns') == 3,
          'D18a 数据行数 2、列宽 3（分隔行未计入）',
          actual=(data.get('rows'), data.get('columns')))

    cells = facts_of(result, 'tableCell')
    by_value = {fact.data.get('value'): fact for fact in cells}
    check('battery_id' in by_value
          and by_value['battery_id'].locator.get('line') == MD_FIRST_DATA_LINE
          and by_value['battery_id'].data.get('row') == 2,
          'D18a 第一条数据行的单元格定位到真实行且 row=2',
          actual=(by_value['battery_id'].locator.get('line'), by_value['battery_id'].data.get('row'))
          if 'battery_id' in by_value else None)
    check('soc' in by_value and by_value['soc'].locator.get('line') == MD_LAST_DATA_LINE
          and by_value['soc'].data.get('row') == 3,
          'D18a 第二条数据行行号与行序号一致（line=8, row=3）',
          actual=(by_value['soc'].locator.get('line'), by_value['soc'].data.get('row'))
          if 'soc' in by_value else None)
    check(all(fact.locator.get('line', 0) > 0 for fact in cells),
          'D18a 所有单元格事实 locator.line 均大于 0',
          actual=sorted({fact.locator.get('line') for fact in cells}))
    check(all(fact.data.get('columnName') for fact in cells)
          and by_value.get('string') and by_value['string'].data.get('columnName') == '类型',
          'D18a 单元格列名与表头对应',
          actual=by_value['string'].data.get('columnName') if by_value.get('string') else None)
    check(all(fact.locator.get('section') == '设备台账' for fact in cells + tables),
          'D18a 章节路径随表格定位保留',
          actual=sorted({fact.locator.get('section') for fact in cells + tables}))
    check(not any(str(fact.data.get('value', '')).startswith('---') for fact in cells),
          'D18a 分隔行 `---` 未被当成单元格值输出',
          actual=[fact.data.get('value') for fact in cells][:8])


MD_RAGGED = """# 列数不齐

| a | b | c |
| --- | --- | --- |
| 1 | 2 |
| 4 | 5 | 6 |
"""


def test_markdown_ragged_rows_reported():
    from workbench.ontology_build.parsers import markdown_parser

    path = write('md/ragged.md', MD_RAGGED)
    result = markdown_parser.parse(path, 'bm-md-ragged')
    short_line = 5      # `| 1 | 2 |`
    full_line = 6       # `| 4 | 5 | 6 |`
    failed = failures_of(result)
    check(any(item.get('locator', {}).get('line') == short_line and '列数不齐' in item.get('reason', '')
              for item in failed),
          'D18a 行列数不齐按真实行号进入 failedSegments',
          actual=[(item.get('locator'), item.get('reason')[:24]) for item in failed])
    cells = facts_of(result, 'tableCell')
    lines = {fact.locator.get('line') for fact in cells}
    check(lines == {short_line, full_line},
          'D18a 单元格只落在真实存在的行上（缺列行不猜值、幽灵行不产事实）', actual=sorted(lines))
    expected_row = {short_line: 2, full_line: 3}
    check(all(fact.data.get('row') == expected_row.get(fact.locator.get('line'))
              for fact in cells),
          'D18a 行序号与定位行一致（分隔行不占行号）',
          actual=sorted({(fact.data.get('row'), fact.locator.get('line')) for fact in cells}))


# ---------------------------------------------------------------------------
# D18b：DDL 命名外键与表级约束定位
# ---------------------------------------------------------------------------
DDL_FK_SAMPLE = """CREATE TABLE `charge_order` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL COMMENT '下单用户',
  `station_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_order_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_order_station` FOREIGN KEY (`station_id`, `id`) REFERENCES `station` (`sid`, `tid`)
    ON UPDATE RESTRICT ON DELETE SET NULL,
  UNIQUE KEY `uk_order_no` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def test_ddl_named_foreign_key():
    from workbench.ontology_build.parsers import ddl

    path = write('ddl/fk_demo.sql', DDL_FK_SAMPLE)
    result = ddl.parse(path, 'bm-ddl')
    check(not result.error, 'D18b DDL 样本解析无 error', actual=result.error)
    fks = facts_of(result, 'fk')
    check(len(fks) == 2, 'D18b 两条命名外键均被解析',
          actual=[fact.data.get('constraint') for fact in fks])
    by_name = {fact.data.get('constraint'): fact for fact in fks}
    first = by_name.get('fk_order_user')
    check(first is not None and first.data['from']['columns'] == ['user_id'],
          'D18b 金样：CONSTRAINT x FOREIGN KEY (a) REFERENCES t(b) 的 from.columns 为约束列',
          actual=first.data.get('from') if first else None)
    check(first is not None and first.data['to']['table'] == 'user'
          and first.data['to']['columns'] == ['id'],
          'D18b 引用表与引用列正确', actual=first.data.get('to') if first else None)
    check(first is not None and first.data.get('actions') == {'DELETE': 'CASCADE'},
          'D18b ON DELETE 动作被采集', actual=first.data.get('actions') if first else None)
    check(first is not None and first.locator.get('column') == 'user_id'
          and first.locator.get('line') == 6,
          'D18b 外键定位到本表列与真实行号',
          actual=first.locator if first else None)
    second = by_name.get('fk_order_station')
    check(second is not None and second.data['from']['columns'] == ['station_id', 'id']
          and second.data['to']['columns'] == ['sid', 'tid']
          and second.data.get('actions') == {'UPDATE': 'RESTRICT', 'DELETE': 'SET NULL'},
          'D18b 复合列外键与多动作不被组号错位污染',
          actual=second.data if second else None)
    check(not any('fk_order' in str(column) for fact in fks for column in fact.data['from']['columns']),
          'D18b from.columns 不再出现约束名字符串',
          actual=[fact.data['from']['columns'] for fact in fks])

    primary = [fact for fact in facts_of(result, 'constraint')
               if fact.data.get('kind') == 'primaryKey']
    unique = [fact for fact in facts_of(result, 'constraint') if fact.data.get('kind') == 'unique']
    check(len(primary) == 1 and primary[0].data['columns'] == ['id']
          and primary[0].locator.get('line') == 5,
          'D18b 主键约束列与真实行号', actual=[(f.data.get('columns'), f.locator.get('line'))
                                               for f in primary])
    check(len(unique) == 1 and unique[0].data['columns'] == ['id']
          and unique[0].locator.get('line') == 9,
          'D18b 同行多约束不再被去重吞掉：UNIQUE KEY 事实存在且行号真实',
          actual=[(f.data.get('columns'), f.locator.get('line')) for f in unique])
    columns = facts_of(result, 'column')
    check({fact.locator.get('column') for fact in columns} == {'id', 'user_id', 'station_id'},
          'D18b 列事实定位保持正确', actual=[fact.locator.get('column') for fact in columns])
    check(all(fact.locator.get('line') > 0 for fact in result.facts),
          'D18b 所有 DDL 事实都带真实行号', 
          actual=sorted({fact.locator.get('line') for fact in result.facts}))


DDL_INLINE_SAMPLE = """CREATE TABLE t_edge (
  id BIGSERIAL PRIMARY KEY,
  code VARCHAR(32) UNIQUE NOT NULL,
  note TEXT
);
"""


def test_ddl_other_dialect_still_parsed():
    """外键修复不得影响其他方言（PostgreSQL SERIAL/inline REFERENCES 仍按原语义产出）。"""
    from workbench.ontology_build.parsers import ddl

    path = write('ddl/edge_pg.sql', DDL_INLINE_SAMPLE)
    result = ddl.parse(path, 'bm-ddl-pg')
    columns = facts_of(result, 'column')
    check(not result.error and len(columns) == 3, 'D18b 对照：PostgreSQL 风格 DDL 正常解析',
          actual=(result.error, len(columns)))
    check(any(fact.data.get('primaryKey') for fact in columns)
          and any(fact.data.get('unique') for fact in columns),
          'D18b 对照：行内 PRIMARY KEY/UNIQUE 标注保持', actual=[
              (fact.locator.get('column'), fact.data.get('primaryKey'), fact.data.get('unique'))
              for fact in columns])
    check(any('SERIAL' in str(hint) for hint in
              (facts_of(result, 'table')[0].data.get('dialectHints') or [])),
          'D18b 对照：方言提示仍被记录',
          actual=[fact.data.get('dialectHints') for fact in facts_of(result, 'table')])


# ---------------------------------------------------------------------------
# D18c：Vue 模板绑定
# ---------------------------------------------------------------------------
VUE_SAMPLE = """<template>
  <div class="soc-panel">
    <input v-model="form.soc" :disabled="readonly" placeholder="请输入 SOC" />
    <span v-if="device.status">{{ device.name }}</span>
    <button @click="submitForm" v-on:click.stop="resetForm">提交</button>
    <p v-for="(item, index) in batteryList" :key="item.guid">{{ index }}</p>
    <img v-bind:src="coverUrl" alt='封面' />
  </div>
</template>

<script setup>
const form = reactive({ soc: 0 })
const batteryList = ref([])
</script>
"""


def test_vue_template_bindings():
    from workbench.ontology_build.parsers import code

    check(callable(getattr(code, '_directive_name', None)),
          'D18c _directive_name 已定义（此前被调用却未定义）')
    check(callable(getattr(code, '_expression_identifiers', None)),
          'D18c _expression_identifiers 已定义（此前 NameError 死路径）')

    path = write('vue/SocPanel.vue', VUE_SAMPLE)
    result = code.parse(path, 'bm-vue')
    check(not result.error, 'D18c .vue 解析不再因未定义辅助函数整体失败', actual=result.error)
    bindings = facts_of(result, 'templateBinding')
    names = {fact.data.get('name') for fact in bindings}
    check({'form', 'readonly', 'device'} <= names,
          'D18c v-model/:disabled/v-if 的根标识符登记为字段使用证据', actual=sorted(names))
    check({'submitForm', 'resetForm', 'batteryList', 'coverUrl'} <= names,
          'D18c 事件绑定与 v-bind 表达式同样产出证据', actual=sorted(names))
    check(not any(str(name).startswith('$') for name in names),
          'D18c $ 前缀的 Vue 内置成员不作为字段', actual=sorted(names))
    check(not ({'item', 'index'} & names), 'D18c v-for 局部别名不作为字段证据',
          actual=sorted(names & {'item', 'index'}))

    by_name = {fact.data.get('name'): fact for fact in bindings}
    check(by_name.get('form') and by_name['form'].data.get('directive') == 'v-model'
          and by_name['form'].locator.get('line') == 3,
          'D18c 指令原文与真实行号一起入证据',
          actual=(by_name['form'].data.get('directive'), by_name['form'].locator.get('line'))
          if by_name.get('form') else None)
    check(by_name.get('readonly') and by_name['readonly'].data.get('directive') == ':disabled',
          'D18c :disabled 简写指令被识别',
          actual=by_name['readonly'].data.get('directive') if by_name.get('readonly') else None)
    check(by_name.get('resetForm')
          and by_name['resetForm'].data.get('directive') == 'v-on:click.stop',
          'D18c 事件修饰符保留在指令名里',
          actual=by_name['resetForm'].data.get('directive') if by_name.get('resetForm') else None)
    check(by_name.get('coverUrl')
          and by_name['coverUrl'].data.get('expression') == 'coverUrl'
          and by_name['coverUrl'].locator.get('line') == 7,
          'D18c v-bind:src 表达式不吞掉同行后续属性',
          actual=by_name['coverUrl'].data.get('expression') if by_name.get('coverUrl') else None)
    expressions = facts_of(result, 'templateExpression')
    check(any('device.name' in (fact.data.get('expression') or '') for fact in expressions),
          'D18c 插值表达式 {{ device.name }} 保留原文',
          actual=[fact.data.get('expression') for fact in expressions])


VUE_QUOTE_SAMPLE = """<template>
  <input v-model='form.soc' alt="a\\"b" />
  <input v-model="unterminated placeholder='x' />
</template>
"""


def test_vue_quote_pairing():
    """引号配对按同类闭合：单引号属性正常，缺同类闭引号时不跨属性瞎配（D18 组号错位）。"""
    from workbench.ontology_build.parsers import code

    path = write('vue/QuotePair.vue', VUE_QUOTE_SAMPLE)
    result = code.parse(path, 'bm-vue-quote')
    bindings = facts_of(result, 'templateBinding')
    expressions = [fact.data.get('expression') for fact in bindings]
    check("'form.soc'" in str(expressions) or 'form.soc' in expressions,
          'D18c 单引号属性的 v-model 表达式被正确提取', actual=expressions)
    check(all('placeholder' not in str(value) and '"' not in str(value)
              and "'" not in str(value) for value in expressions),
          'D18c 表达式不会跨越属性边界（旧 \\4 反向引用组号错位）', actual=expressions)
    check(not any('unterminated' in str(value) and 'placeholder' in str(value)
                  for value in expressions),
          'D18c 未闭合引号的绑定不产出错误表达式', actual=expressions)


def test_vue_expression_identifier_rules():
    from workbench.ontology_build.parsers import code

    ident = code._expression_identifiers
    check(ident("form.name + '-' + row.soc") == ['form'],
          'D18c 属性访问只留宿主标识符', actual=ident("form.name + '-' + row.soc"))
    check(ident('{ active: flag, disabled: true }') == ['flag'],
          'D18c 对象字面量的键被排除', actual=ident('{ active: flag, disabled: true }'))
    check(ident('save() && isValid(x)') == ['x'],
          'D18c 紧跟括号的函数/方法调用名不作为字段', actual=ident('save() && isValid(x)'))
    check(ident('$emit("changed", payload)') == ['payload'],
          'D18c $ 前缀与字符串字面量被排除', actual=ident('$emit("changed", payload)'))
    check(ident('Math.max(a, b)') == ['a', 'b'],
          'D18c 宿主全局被排除、实参保留', actual=ident('Math.max(a, b)'))
    check(ident('(item, index) in batteryList') == ['batteryList'],
          'D18c v-for 表达式只留集合本身', actual=ident('(item, index) in batteryList'))
    check(ident('') == [] and ident(None) == [], 'D18c 空表达式安全返回空列表')
    check(len(ident('a1 || ' + ' || '.join('b%d' % i for i in range(40))))
          == code.MAX_EXPRESSION_IDENTIFIERS,
          'D18c 单条表达式的标识符数量有上限（防噪声）',
          actual=len(ident('a1 || ' + ' || '.join('b%d' % i for i in range(40)))))


# ---------------------------------------------------------------------------
# D15：文本型 PDF（程序合成，含可提取文本）
# ---------------------------------------------------------------------------
def _pdf_escape(text):
    return text.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')


def build_text_pdf(page_texts):
    """生成最小可提取文本 PDF：每页一个未压缩文本对象 + 标准 Helvetica 字体。

    对象编号：1 catalog、2 pages、每页 (page, content) 依次编号、最后一个为字体。
    """
    bodies = []
    for text in page_texts:
        lines = ['BT', '/F1 12 Tf', '16 TL', '72 720 Td']
        for line in text:
            lines.append('(%s) Tj' % _pdf_escape(line))
            lines.append('T*')
        lines.append('ET')
        bodies.append('\n'.join(lines))

    page_count = len(page_texts)
    font_number = 2 * page_count + 3
    kids = ' '.join('%d 0 R' % (3 + 2 * i) for i in range(page_count))
    objects = ['<< /Type /Catalog /Pages 2 0 R >>',
               '<< /Type /Pages /Kids [%s] /Count %d >>' % (kids, page_count)]
    for index, body in enumerate(bodies):
        page_number = 3 + 2 * index
        content_number = page_number + 1
        objects.append('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                       '/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>'
                       % (font_number, content_number))
        objects.append('<< /Length %d >>\nstream\n%s\nendstream' % (len(body), body))
    objects.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding '
                   '/WinAnsiEncoding >>')

    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += ('%d 0 obj\n%s\nendobj\n' % (number, body)).encode('latin-1')
    xref_start = len(out)
    out += ('xref\n0 %d\n' % (len(objects) + 1)).encode('latin-1')
    out += b'0000000000 65535 f \n'
    for offset in offsets:
        out += ('%010d 00000 n \n' % offset).encode('latin-1')
    out += ('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'
            % (len(objects) + 1, xref_start)).encode('latin-1')
    return bytes(out)


PDF_PAGE_ONE = [
    'Battery cluster ledger (synthetic acceptance sample)',
    'battery_cluster: field soc is the state of charge in percent.',
    'charging_station: field station_id is the primary key, name is the label.',
]
PDF_PAGE_TWO = ['scan']          # < MIN_PAGE_CHARS → 判定为扫描页/图片页


def test_text_pdf_with_pypdf():
    from workbench.ontology_build.parsers import pdf_parser

    library, library_name = pdf_parser._load_library()
    check(library is not None,
          'D15 PDF 解析库在交付环境可用（requirements.txt 已登记 pypdf）',
          actual='未安装 pypdf/PyPDF2：执行 .venv/bin/pip install -r requirements.txt 后重试')
    if library is None:
        return
    requirements = (REPO / 'requirements.txt').read_text(encoding='utf-8')
    check('pypdf' in requirements, 'D15 requirements.txt 已声明 pypdf 依赖',
          actual=requirements.splitlines()[-4:])

    path = write('pdf/ledger_demo.pdf', build_text_pdf([PDF_PAGE_ONE, PDF_PAGE_TWO]))
    result = pdf_parser.parse(path, 'bm-pdf')
    check(not result.error, 'D15 文本型 PDF 解析成功且无 error（句柄保持打开）',
          actual=result.error)
    pages = facts_of(result, 'pageSummary')
    check(len(pages) == 1 and pages[0].locator.get('page') == 1,
          'D15 文本页产出带真实页码的定位事实',
          actual=[(fact.kind, fact.locator.get('page')) for fact in result.facts])
    check(pages and pages[0].snippet.startswith('Battery cluster ledger')
          and pages[0].data.get('extractor') in ('pypdf', 'PyPDF2'),
          'D15 页首行摘要与提取器名可回读',
          actual=(pages[0].snippet[:40], pages[0].data.get('extractor')) if pages else None)
    check(all('\ufffd' not in str(fact.snippet) for fact in result.facts),
          'D15 提取文本无替换字符（字体编码正确）',
          actual=[fact.snippet[:30] for fact in result.facts])
    check(any(fact.kind == 'pageLine' and 'battery_cluster' in str(fact.snippet)
              for fact in result.facts),
          'D15 行线索事实保留字段名原文',
          actual=[fact.snippet[:30] for fact in result.facts])
    failed = failures_of(result)
    check(any(item.get('locator', {}).get('page') == 2 and 'OCR' in item.get('reason', '')
              for item in failed),
          'D15 近空白页按扫描页逐页显式报告（需要 OCR，不假装解析成功）', actual=failed)
    check(result.partial and result.coverage.get('scannedPages') == [2]
          and result.coverage.get('pageCount') == 2,
          'D15 存在扫描页时 partial=True 且页数/扫描页清单如实记录',
          actual=(result.partial, result.coverage.get('pageCount'),
                  result.coverage.get('scannedPages')))
    check(any('OCR' in note for note in notes_of(result)),
          'D15 coverage.notes 写明 OCR 缺口', actual=notes_of(result)[-4:])

    broken = write('pdf/broken.pdf', b'%PDF-1.4\nthis is not a real body\n')
    broken_result = pdf_parser.parse(broken, 'bm-pdf-broken')
    check(bool(broken_result.error) and not broken_result.facts,
          'D15 损坏 PDF 显式失败且不产出事实',
          actual=(broken_result.error[:80], len(broken_result.facts)))


# ---------------------------------------------------------------------------
# D12：OOXML 部件按实际解压字节封顶
# ---------------------------------------------------------------------------
BIG_PADDING = 3000000     # ≈3 MB 解压后噪声：单部件上限 64 KiB、累计上限 1 MiB 都能拦住


def make_docx(document_xml, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml',
                         '<?xml version="1.0"?><Types xmlns="p"><Default Extension="xml" '
                         'ContentType="application/xml"/></Types>')
        archive.writestr('word/document.xml', document_xml)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def docx_body(padding_chars):
    """合法 DOCX 正文 + 尾部噪声段落（噪声同样计入实际解压字节）。"""
    inner = ''.join('<w:p><w:r><w:t>段落 %d：电池簇字段 soc</w:t></w:r></w:p>' % index
                    for index in range(20))
    filler = ''
    if padding_chars > 0:
        filler = ('<w:p><w:r><w:t>' + 'x' * padding_chars + '</w:t></w:r></w:p>')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>%s%s</w:body></w:document>' % (inner, filler))


def make_xlsx(sheet_xml, shared=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml',
                         '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
                         'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                         'officeDocument/2006/relationships"><sheets><sheet name="台账" sheetId="1" '
                         'r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels',
                         '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
                         '.org/package/2006/relationships"><Relationship Id="rId1" Type="t" '
                         'Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/sharedStrings.xml',
                         shared or '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats'
                                   '.org/spreadsheetml/2006/main"></sst>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)
    return buffer.getvalue()


def xlsx_sheet(padding_chars):
    cells = ''.join('<c r="A%d" t="inlineStr"><is><t>值%d</t></is></c>' % (row, row)
                    for row in range(1, 6))
    filler = '<!--' + ('y' * padding_chars) + '-->' if padding_chars > 0 else ''
    return ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData><row r="1">%s</row></sheetData>%s</worksheet>'
            % (cells, filler))


def make_xlsx_two_sheets(sheet_xml_a, sheet_xml_b):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml',
                         '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
                         'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                         'officeDocument/2006/relationships"><sheets>'
                         '<sheet name="A表" sheetId="1" r:id="rId1"/>'
                         '<sheet name="B表" sheetId="2" r:id="rId2"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels',
                         '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
                         '.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="t" Target="worksheets/sheet1.xml"/>'
                         '<Relationship Id="rId2" Type="t" Target="worksheets/sheet2.xml"/>'
                         '</Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet_xml_a)
        archive.writestr('xl/worksheets/sheet2.xml', sheet_xml_b)
    return buffer.getvalue()


def _zip_entry_offsets(data, member):
    """按成员名找到它的本地头与中央目录头偏移（不是首个条目，必须逐个匹配名字）。"""
    name = str(member).encode('utf-8')
    local = central = -1
    position = 0
    while True:
        position = data.find(b'PK\x03\x04', position)
        if position < 0:
            break
        name_length = struct.unpack_from('<H', data, position + 26)[0]
        if data[position + 30:position + 30 + name_length] == name:
            local = position
            break
        position += 4
    position = 0
    while True:
        position = data.find(b'PK\x01\x02', position)
        if position < 0:
            break
        name_length = struct.unpack_from('<H', data, position + 28)[0]
        if data[position + 46:position + 46 + name_length] == name:
            central = position
            break
        position += 4
    return local, central


def forge_declared_sizes(payload, member, declared_file_size, declared_compress_size):
    """把 ZIP 中指定成员的本地头与中央目录声明值改成小值（模拟伪造头部绕过声明检查）。"""
    data = bytearray(payload)
    local, central = _zip_entry_offsets(data, member)
    assert local >= 0 and central >= 0, '未找到该成员的 ZIP 头（样本生成失败）'
    struct.pack_into('<I', data, local + 18, declared_compress_size)
    struct.pack_into('<I', data, local + 22, declared_file_size)
    struct.pack_into('<I', data, central + 20, declared_compress_size)
    struct.pack_into('<I', data, central + 24, declared_file_size)
    return bytes(data)


def _open_capped(path, member_limit=None, total_limit=None):
    from workbench.ontology_build.parsers import zipguard
    return zipguard.CappedZipFile(zipfile.ZipFile(str(path)), member_limit=member_limit,
                                  total_limit=total_limit)


def test_zipguard_declared_limit_rejects_without_decompressing():
    from workbench.ontology_build.parsers import zipguard

    path = write('zip/capped_source.docx', make_docx(docx_body(BIG_PADDING)))
    handle = _open_capped(path, member_limit=64 * 1024, total_limit=4 * 1024 * 1024)
    try:
        raised = None
        try:
            handle.read('word/document.xml')
        except zipguard.ZipBombDetected as exc:
            raised = exc
        check(raised is not None and 'word/document.xml' in str(raised) and '声明' in str(raised),
              'D12 声明解压体积即超限时直接拒绝（不信任 header，也无需解压）',
              actual=str(raised) if raised else '未抛异常（封顶失效）')
        check(raised is not None and raised.read_bytes == 0 and handle.read_bytes == 0,
              'D12 声明超限路径零字节解压', actual=(getattr(raised, 'read_bytes', None),
                                                   handle.read_bytes))
    finally:
        handle.close()


def test_zipguard_actual_bytes_are_counted_while_streaming():
    from workbench.ontology_build.parsers import zipguard

    path = write('zip/capped_source.docx', make_docx(docx_body(BIG_PADDING)))
    # 单部件上限放到 8 MiB（声明值合规），但整包累计上限只有 1 MiB：
    # 必须在流式解压过程中按实际产出字节中止，而不是信声明值放行。
    handle = _open_capped(path, member_limit=8 * 1024 * 1024, total_limit=1024 * 1024)
    try:
        raised = None
        try:
            handle.read('word/document.xml')
        except zipguard.ZipBombDetected as exc:
            raised = exc
        check(raised is not None and '累计' in str(raised),
              'D12 按实际解压字节累计封顶（声明值合规也拦得住）',
              actual=str(raised) if raised else '未抛异常')
        check(raised is None or raised.read_bytes <= 1024 * 1024 + zipguard.READ_CHUNK_BYTES,
              'D12 中止前实际读取字节数受上限约束（内存有界）',
              actual=getattr(raised, 'read_bytes', None))
        check(handle.read_bytes == 0, 'D12 未通过的部件不计入已读总量', actual=handle.read_bytes)
    finally:
        handle.close()


def test_zipguard_within_limits_counts_and_reuses():
    from workbench.ontology_build.parsers import zipguard

    payload = make_docx(docx_body(2000))
    path = write('zip/capped_source_small.docx', payload)
    handle = _open_capped(path, member_limit=8 * 1024 * 1024, total_limit=8 * 1024 * 1024)
    try:
        data = handle.read('word/document.xml')
        check(b'<w:document' in data and handle.read_bytes == len(data),
              'D12 限额内正常读取且累计计数等于实际字节',
              actual=(len(data), handle.read_bytes))
        handle.read('word/document.xml')
        check(handle.read_bytes == 2 * len(data), 'D12 多次读取按实际字节累计',
              actual=handle.read_bytes)
    finally:
        handle.close()

    two = write('zip/two_sheets.xlsx', make_xlsx_two_sheets(xlsx_sheet(2000), xlsx_sheet(2000)))
    handle = _open_capped(two, member_limit=8 * 1024 * 1024, total_limit=4000)
    try:
        first = handle.read('xl/worksheets/sheet1.xml')
        check(2000 < len(first) < 4000, 'D12 对照：两部件样本单部件在累计上限之内',
              actual=len(first))
        raised = None
        try:
            handle.read('xl/worksheets/sheet2.xml')
        except zipguard.ZipBombDetected as exc:
            raised = exc
        check(raised is not None and '累计' in str(raised),
              'D12 多部件分摊也不放行：整包累计上限生效',
              actual=(len(first), str(raised) if raised else '未抛异常'))
    finally:
        handle.close()


class _MemberLimit:
    """临时下调 zipguard 默认单部件上限（退出恢复，测试不改动交付默认值）。"""

    def __init__(self, value):
        self.value = value
        self.saved = None

    def __enter__(self):
        from workbench.ontology_build.parsers import zipguard
        self.saved = zipguard.DEFAULT_MEMBER_LIMIT
        zipguard.DEFAULT_MEMBER_LIMIT = self.value
        return zipguard

    def __exit__(self, exc_type, exc_value, traceback):
        from workbench.ontology_build.parsers import zipguard
        zipguard.DEFAULT_MEMBER_LIMIT = self.saved
        return False


def test_docx_beyond_limit_rejected():
    from workbench.ontology_build.parsers import docx_parser, zipguard

    path = write('docx/big_document.docx', make_docx(docx_body(BIG_PADDING)))
    with _MemberLimit(64 * 1024):
        result = docx_parser.parse(path, 'bm-docx-big')
    check(bool(result.error) and '上限' in result.error,
          'D12 超出单部件上限的 DOCX 显式失败（不静默截断）', actual=result.error)
    check(not result.facts, 'D12 被拒 DOCX 不产出任何事实', actual=len(result.facts))
    failed = failures_of(result)
    check(bool(failed) and failed[0].get('locator', {}).get('kind') == 'docx',
          'D12 拒绝原因进入 failedSegments 且带定位', actual=failed)
    check(any('实际解压' in note for note in notes_of(result)) or
          any('实际解压' in (failed[0].get('reason') if failed else '')),
          'D12 拒绝说明写明按实际解压字节封顶', actual=notes_of(result))
    check(zipguard.DEFAULT_MEMBER_LIMIT == 64 * 1024 * 1024,
          'D12 测试后默认上限恢复为交付值', actual=zipguard.DEFAULT_MEMBER_LIMIT)


def test_xlsx_beyond_limit_rejected():
    from workbench.ontology_build.parsers import xlsx_parser, zipguard

    path = write('xlsx/big_sheet.xlsx', make_xlsx(xlsx_sheet(BIG_PADDING)))
    with _MemberLimit(64 * 1024):
        result = xlsx_parser.parse(path, 'bm-xlsx-big')
    text = '%s %s' % (result.error, ' '.join(item.get('reason', '') for item in failures_of(result)))
    check('上限' in text and (bool(result.error) or bool(failures_of(result))),
          'D12 超出单部件上限的 XLSX 显式失败/失败片段', actual=(result.error[:80], text[:160]))
    check(not any(fact.kind == 'cell' for fact in result.facts),
          'D12 被拒工作簿不产出单元格事实', actual=[fact.kind for fact in result.facts][:10])
    check(zipguard.DEFAULT_MEMBER_LIMIT == 64 * 1024 * 1024,
          'D12 XLSX 测试后默认上限恢复', actual=zipguard.DEFAULT_MEMBER_LIMIT)


def test_forged_header_materials_rejected():
    from workbench.ontology_build.parsers import docx_parser, xlsx_parser

    # 声明值伪造成 10 字节：只看声明值的外层守卫会放行，解析侧必须有界
    forged_docx = forge_declared_sizes(make_docx(docx_body(BIG_PADDING)),
                                       'word/document.xml', 10, 10)
    docx_path = write('docx/forged_header.docx', forged_docx)
    docx_result = docx_parser.parse(docx_path, 'bm-docx-forged')
    check(bool(docx_result.error) or bool(failures_of(docx_result)) or docx_result.partial,
          'D12 伪造声明值的 DOCX 被显式拒绝/报告（声明超限或 CRC 不符，绝不静默）',
          actual=(docx_result.error[:80], docx_result.partial))
    check(not any('段落 19' in str(fact.snippet or '') for fact in docx_result.facts),
          'D12 伪造头部的 DOCX 不会被当作完整文档解析到底',
          actual=[fact.snippet[:30] for fact in docx_result.facts][:5])

    forged_xlsx = forge_declared_sizes(make_xlsx(xlsx_sheet(BIG_PADDING)),
                                       'xl/worksheets/sheet1.xml', 10, 10)
    xlsx_path = write('xlsx/forged_header.xlsx', forged_xlsx)
    xlsx_result = xlsx_parser.parse(xlsx_path, 'bm-xlsx-forged')
    check(bool(xlsx_result.error) or bool(failures_of(xlsx_result)) or xlsx_result.partial,
          'D12 伪造声明值的 XLSX 显式失败或 partial（不当空表继续）',
          actual=(xlsx_result.error[:80], xlsx_result.partial,
                  [item.get('reason', '')[:40] for item in failures_of(xlsx_result)]))
    check(not any(fact.kind == 'cell' and str(fact.data.get('value')) == '值5'
                  for fact in xlsx_result.facts),
          'D12 伪造头部的 XLSX 不会产出被截断之后的单元格值',
          actual=[fact.data.get('value') for fact in xlsx_result.facts][:10])


def test_guarded_parsers_still_work_within_limits():
    """限额未触发时 DOCX/XLSX 常规能力不受守卫改动影响（正向对照）。"""
    from workbench.ontology_build.parsers import docx_parser, xlsx_parser

    docx_path = write('docx/normal.docx', make_docx(docx_body(200)))
    docx_result = docx_parser.parse(docx_path, 'bm-docx-ok')
    paragraphs = facts_of(docx_result, 'paragraph')
    check(not docx_result.error and len(paragraphs) >= 20,
          'D12 对照：正常 DOCX 段落事实照旧产出', actual=(docx_result.error, len(paragraphs)))
    check(any('实际解压' in note for note in notes_of(docx_result)),
          'D12 对照：DOCX coverage 说明解压封顶语义', actual=notes_of(docx_result)[-3:])

    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main"><dimension ref="A1:B2"/><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>字段名</t></is></c>'
             '<c r="B1" t="inlineStr"><is><t>说明</t></is></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>soc</t></is></c>'
             '<c r="B2" t="inlineStr"><is><t>荷电状态</t></is></c></row>'
             '</sheetData></worksheet>')
    xlsx_path = write('xlsx/normal.xlsx', make_xlsx(sheet))
    xlsx_result = xlsx_parser.parse(xlsx_path, 'bm-xlsx-ok')
    cells = [fact for fact in xlsx_result.facts if fact.kind in ('cell', 'header')]
    values = {fact.data.get('value') for fact in cells}
    check(not xlsx_result.error and {'字段名', 'soc', '荷电状态'} <= values,
          'D12 对照：正常 XLSX 单元格事实照旧产出', actual=(xlsx_result.error, sorted(values)))
    check(all(fact.locator.get('cell') for fact in cells),
          'D12 对照：单元格 A1 坐标定位保持',
          actual=[fact.locator.get('cell') for fact in cells])


def test_parse_material_dispatch_after_changes():
    """统一入口仍能分派全部已登记类型，且解析器异常不会上抛（回归保护）。"""
    from workbench.ontology_build import parsers

    cases = {
        'md': ('entry/dispatch.md', '# 标题\n\n正文段落。\n'),
        'ddl': ('entry/dispatch.sql', 'CREATE TABLE t (id bigint PRIMARY KEY);\n'),
        'code': ('entry/Dispatch.vue',
                 '<template><i :value="form.a"/></template>\n'
                 '<script setup>const form = { a: 1 }</script>\n'),
    }
    for kind, (name, data) in cases.items():
        path = write(name, data)
        result = parsers.parse_material(path, 'bm-entry', kind, rel_path=name)
        check(not result.error and result.facts, 'D18 统一入口解析 %s 材料成功' % kind,
              actual=(result.error, len(result.facts)))

    docx_path = write('entry/normal.docx', make_docx(docx_body(200)))
    result = parsers.parse_material(docx_path, 'bm-entry-docx', 'docx', rel_path='entry/normal.docx')
    check(not result.error and result.facts,
          'D12 统一入口解析受守卫保护的 DOCX 成功', actual=(result.error, len(result.facts)))
    huge_docx = write('entry/big.docx', make_docx(docx_body(BIG_PADDING)))
    with _MemberLimit(64 * 1024):
        guarded = parsers.parse_material(huge_docx, 'bm-entry-big', 'docx',
                                        rel_path='entry/big.docx')
    check(bool(guarded.error) and not guarded.facts,
          'D12 统一入口下超限 DOCX 仍显式失败（异常不外抛）',
          actual=(guarded.error[:80], len(guarded.facts)))
    pdf_path = write('entry/ledger.pdf', build_text_pdf([PDF_PAGE_ONE]))
    pdf_result = parsers.parse_material(pdf_path, 'bm-entry-pdf', 'pdf', rel_path='entry/ledger.pdf')
    check(not pdf_result.error and pdf_result.facts,
          'D15 统一入口解析文本型 PDF 成功', actual=(pdf_result.error, len(pdf_result.facts)))


def main():
    print('\n== D18a Markdown 表格定位与内容 ==')
    test_markdown_table_locator()
    test_markdown_ragged_rows_reported()
    print('\n== D18b DDL 命名外键与约束定位 ==')
    test_ddl_named_foreign_key()
    test_ddl_other_dialect_still_parsed()
    print('\n== D18c Vue 模板绑定 ==')
    test_vue_template_bindings()
    test_vue_quote_pairing()
    test_vue_expression_identifier_rules()
    print('\n== D15 文本型 PDF ==')
    test_text_pdf_with_pypdf()
    print('\n== D12 解压字节守卫 ==')
    test_zipguard_declared_limit_rejects_without_decompressing()
    test_zipguard_actual_bytes_are_counted_while_streaming()
    test_zipguard_within_limits_counts_and_reuses()
    test_docx_beyond_limit_rejected()
    test_xlsx_beyond_limit_rejected()
    test_forged_header_materials_rejected()
    test_guarded_parsers_still_work_within_limits()
    test_parse_material_dispatch_after_changes()
    return 0


if __name__ == '__main__':
    exit_code = 1
    try:
        exit_code = main()
    except Exception as exc:  # 未预期异常不吞
        import traceback
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(str(TMP), ignore_errors=True)
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if exit_code == 0 and not FAILED else 1)
