"""Project-only multi-step lookup specifications. Validation only; no SQL execution."""
import re

IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
REFERENCE = re.compile(r'^\{\{(inputs\.(instanceId|model_id|model_name|attr_name|startTime|endTime)|steps\.([\w]+)\.([\w]+))\}\}$')
POLICIES = {'lookupMissing': 'error', 'lookupMultiple': 'error', 'emptySeries': 'empty',
            'nullValue': 'preserve', 'identifiers': 'connectionCatalog', 'timezone': 'project',
            'range': '[startTime,endTime)'}


def is_query_rule(value):
    return isinstance(value, dict) and value.get('kind') == 'queryRule'


def query_rule_errors(rule, ctx):
    if rule.get('schemaVersion') == 4:
        return sql_steps_errors(rule, ctx)
    if rule.get('schemaVersion') == 3 and rule.get('mode') == 'sqlTemplate':
        from workbench.sql_templates import sql_template_errors
        return sql_template_errors(rule, ctx)
    if rule.get('schemaVersion') == 3:
        return structured_rule_errors(rule, ctx)
    errors, known, inputs = [], {}, set()
    if rule.get('schemaVersion') not in (1, 2):
        errors.append('取值规则版本不受支持')
    if not str(rule.get('name', '')).strip():
        errors.append('请填写规则名称')
    if rule.get('schemaVersion') == 1:
        object_type = rule.get('objectType')
        if object_type not in ctx['bare_classes']:
            errors.append('适用对象不存在')
        binding = ctx['mapped_types'].get(object_type, {})
        if not binding.get('primary_key'):
            errors.append('请先配置适用对象的实例主键')
    connection = ctx['conn_by_id'].get(rule.get('connection'), {})
    if connection.get('engine') != 'mysql':
        errors.append('请选择当前项目已有的数据库连接')
    if rule.get('policies') != POLICIES:
        errors.append('异常、时间范围或动态标识校验约定无效')

    def check(value, label, identifier=False):
        if not isinstance(value, str) or not value.strip():
            errors.append(label + '未填写')
            return
        match = REFERENCE.fullmatch(value)
        if match:
            if match[2]:
                if identifier:
                    errors.append(label + '不能使用调用参数作为表名或字段名')
                else:
                    inputs.add(match[2])
            elif match[4] not in known.get(match[3], set()):
                errors.append(label + '引用了不存在或尚未执行的步骤输出')
        elif '{{' in value or '}}' in value:
            errors.append(label + '引用格式无效')
        elif identifier and not IDENTIFIER.fullmatch(value):
            errors.append(label + '请填写合法表名或字段名')

    steps = rule.get('steps')
    if not isinstance(steps, list) or not steps:
        return errors + ['至少需要一个查询步骤']
    for index, step in enumerate(steps):
        title = f'步骤 {index + 1}：'
        if not isinstance(step, dict):
            errors.append(title + '结构无效')
            continue
        sid = step.get('id')
        if not isinstance(sid, str) or not IDENTIFIER.fullmatch(sid) or sid in known:
            errors.append(title + '步骤标识无效或重复')
            sid = str(index)
        if not str(step.get('name', '')).strip():
            errors.append(title + '请填写步骤名称')
        check(step.get('table'), title + '来源表', True)
        if step.get('cardinality') != ('many' if index == len(steps) - 1 else 'one'):
            errors.append(title + '中间步骤须唯一记录，末步须多条采样记录')
        where = step.get('where')
        if not isinstance(where, list) or not where:
            errors.append(title + '至少填写一个查询条件')
            where = []
        for cond in where:
            if not isinstance(cond, dict):
                errors.append(title + '查询条件结构无效')
                continue
            check(cond.get('field'), title + '条件字段', True)
            check(cond.get('value'), title + '条件值')
            if cond.get('op') not in ('eq', 'gte', 'lt'):
                errors.append(title + '条件运算符无效')
        selected = step.get('select')
        if not isinstance(selected, list) or not selected:
            errors.append(title + '至少提取一个字段')
            selected = []
        aliases = set()
        for field in selected:
            if not isinstance(field, dict):
                errors.append(title + '提取字段结构无效')
                continue
            check(field.get('field'), title + '提取字段', True)
            alias = field.get('as')
            if not isinstance(alias, str) or not IDENTIFIER.fullmatch(alias) or alias in aliases:
                errors.append(title + '输出名称无效或重复')
            else:
                aliases.add(alias)
        known[sid] = aliases
    if rule.get('schemaVersion') == 2 and not {'model_name', 'attr_name', 'model_id'} <= inputs:
        errors.append('通用规则须引用 model_name、attr_name、model_id 三个入参')
    if not {'instanceId', 'model_id'} & inputs:
        errors.append('查询条件须绑定当前实例主键')
    if not {'startTime', 'endTime'} <= inputs:
        errors.append('查询条件须使用开始时间和结束时间')
    result = rule.get('result') if isinstance(rule.get('result'), dict) else {}
    if result.get('type') != 'timeSeries' or result.get('valueType') != 'double':
        errors.append('当前取值规则输出须为数值时间序列')
    if result.get('order') != 'ascending':
        errors.append('结果须按时间升序返回')
    last = steps[-1] if isinstance(steps[-1], dict) else {}
    if (result.get('step') != last.get('id') or
            result.get('timestamp') not in known.get(str(result.get('step')), set()) or
            result.get('value') not in known.get(str(result.get('step')), set()) or
            result.get('timestamp') == result.get('value')):
        errors.append('结果须选择最后一步中不同的时间、数值输出字段')
    else:
        time_field = next((f.get('field') for f in last.get('select', []) if isinstance(f, dict) and f.get('as') == result['timestamp']), '')
        where = last.get('where') if isinstance(last.get('where'), list) else []
        for param, op in [('startTime', 'gte'), ('endTime', 'lt')]:
            if not any(isinstance(w, dict) and w.get('field') == time_field and w.get('op') == op and w.get('value') == '{{inputs.' + param + '}}' for w in where):
                errors.append('最后一步须按输出时间字段配置开始时间包含、结束时间不包含的过滤条件')
                break
    return list(dict.fromkeys(errors))


def rule_input_errors(inputs, binding, rule=None):
    if rule and rule.get("schemaVersion") in (3, 4):
        return ["请填写输入参数 " + p["name"] for p in rule.get("inputs", []) if p.get("source") != "runtime" and not str((inputs or {}).get(p["name"], "")).strip()]
    if not isinstance(inputs, dict):
        return ['请填写取值规则的输入参数']
    errors = []
    for key, label in [('model_name', '对象表名'), ('attr_name', '属性名')]:
        if not isinstance(inputs.get(key), str) or not inputs[key].strip():
            errors.append(f'请填写 {key}（{label}）')
    if inputs.get('model_id') != '{id}':
        errors.append('model_id 请填写 {id}，使用当前实例主键')
    if not binding.get('primary_key'):
        errors.append('请先配置当前对象的实例主键')
    return errors


def structured_rule_errors(rule, ctx):
    """V3 generic query specification; legacy validation remains unchanged."""
    errors, known, names = [], {}, set()
    def ident(v):
        return isinstance(v, str) and bool(IDENTIFIER.fullmatch(v))
    if not str(rule.get('name', '')).strip(): errors.append('请填写规则名称')
    if ctx['conn_by_id'].get(rule.get('connection'), {}).get('engine') != 'mysql': errors.append('请选择当前项目已有的数据库连接')
    for p in rule.get('inputs', []):
        name = p.get('name')
        if not ident(name) or name in names: errors.append('输入参数名称无效或重复')
        names.add(str(name))
        if p.get('type') not in ('string', 'double', 'boolean', 'dateTime'): errors.append('输入参数数据类型无效')
        if p.get('source') not in ('binding', 'runtime'): errors.append('输入参数传入方式无效')
    def check(v, label, identifier=False):
        if not isinstance(v, str) or not v.strip():
            errors.append(label+'未填写'); return
        m = re.fullmatch(r'\{\{(inputs\.(\w+)|steps\.(\w+)\.(\w+))\}\}', v)
        if m:
            if m[2]:
                if identifier or m[2] not in names: errors.append(label+'引用了无效输入参数')
            elif m[4] not in known.get(m[3], set()): errors.append(label+'引用了不存在、返回多条或尚未执行的步骤输出')
        elif '{{' in v or '}}' in v or (identifier and not ident(v)): errors.append(label+'格式无效')
    steps = rule.get('steps') or []
    if not steps: errors.append('至少需要一个查询步骤')
    for i, s in enumerate(steps):
        t = f'步骤 {i+1}：'
        if not ident(s.get('id')) or s['id'] in known: errors.append(t+'步骤标识无效或重复')
        if not str(s.get('name', '')).strip(): errors.append(t+'请填写步骤名称')
        check(s.get('table'), t+'来源表', True)
        if s.get('cardinality') not in ('one', 'many'): errors.append(t+'预期记录数无效')
        if not s.get('where'): errors.append(t+'至少填写一个查询条件')
        for w in s.get('where', []):
            check(w.get('field'), t+'条件字段', True); check(w.get('value'), t+'条件值')
            if w.get('op') not in ('eq','ne','gt','gte','lt','lte'): errors.append(t+'条件运算符无效')
        aliases = set()
        if not s.get('select'): errors.append(t+'至少提取一个字段')
        for f in s.get('select', []):
            check(f.get('field'), t+'提取字段', True)
            if not ident(f.get('as')) or f['as'] in aliases: errors.append(t+'输出名称无效或重复')
            aliases.add(str(f.get('as')))
        for sort in s.get('orderBy', []):
            check(sort.get('field'), t+'排序字段', True)
            if sort.get('direction') not in ('ascending','descending'): errors.append(t+'排序方向无效')
        known[str(s.get('id'))] = aliases if s.get('cardinality') == 'one' else set()
    r, last = rule.get('result') or {}, steps[-1] if steps else {}
    aliases = [f.get('as') for f in last.get('select', [])]
    if r.get('type') not in ('scalar','timeSeries') or r.get('valueType') not in ('string','double','boolean','dateTime'): errors.append('返回类型无效')
    if not last or r.get('step') != last.get('id') or r.get('value') not in aliases: errors.append('返回值须选择最后一步的输出字段')
    if r.get('type') == 'scalar' and last.get('cardinality') != 'one': errors.append('单值输出的最后一步须返回一条记录')
    if r.get('type') == 'timeSeries' and (r.get('timestamp') not in aliases or r.get('timestamp') == r.get('value') or last.get('cardinality') != 'many' or r.get('order') != 'ascending'): errors.append('时间序列须返回多条记录，选择不同的时间和值字段，并按时间升序')
    if rule.get('policies') != POLICIES: errors.append('异常、时间范围或动态标识校验约定无效')
    return list(dict.fromkeys(errors))


SQL_REF = re.compile(r':([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?')
SQL_TOKEN = re.compile(r'\{\{([^{}]*)\}\}')
SQL_IDENT_DOT_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*')


def strip_sql_noise(code):
    """词法剥离：字符串字面量（'...'／"..."，含 '' "" 转义）、-- 行注释、块注释替换为空白。

    后续 SELECT／分号／引用检查一律在剥离后的文本上做：字符串与注释内的伪引用、
    伪分号不得误判（A14）。保长度可不强求，仅保证分隔符语义不变。
    """
    out, i, n = [], 0, len(code)
    while i < n:
        ch = code[i]
        if ch in ("'", '"'):
            i += 1
            while i < n:
                if code[i] == ch:
                    if code[i + 1:i + 2] == ch:  # '' / "" 转义
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(' ')
        elif code[i:i + 2] == '--':
            while i < n and code[i] != '\n':
                i += 1
            out.append(' ')
        elif code[i:i + 2] == '/*':
            i += 2
            while i < n and code[i:i + 2] != '*/':
                i += 1
            i += 2
            out.append(' ')
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def strip_sql_noise_mysql(code):
    """MySQL 语义的词法剥离（属性内联 SQL 用）：字符串（''/"" 加倍 + 反斜杠转义）、
    反引号标识符（`` 加倍）、--（须后随空白/行尾，MySQL 规则）／#／块注释 → 空白。

    与 strip_sql_noise 的差异：补反斜杠转义、反引号与 # 注释；-- 按 MySQL 规则仅在后随
    空白时视为注释。保分隔符语义，保长度可不强求。
    """
    out, i, n = [], 0, len(code)
    while i < n:
        ch = code[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if code[i] == '\\':
                    i += 2
                    continue
                if code[i] == quote:
                    if code[i + 1:i + 2] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(' ')
        elif ch == '`':
            i += 1
            while i < n:
                if code[i] == '`':
                    if code[i + 1:i + 2] == '`':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(' ')
        elif ch == '-' and code[i:i + 2] == '--':
            nxt = code[i + 2:i + 3]
            if nxt == '' or nxt in ' \t\r\n':
                while i < n and code[i] != '\n':
                    i += 1
                out.append(' ')
            else:
                out.append(ch)
                i += 1
        elif ch == '#':
            while i < n and code[i] != '\n':
                i += 1
            out.append(' ')
        elif code[i:i + 2] == '/*':
            i += 2
            while i < n and code[i:i + 2] != '*/':
                i += 1
            i += 2
            out.append(' ')
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


SQL_PARAM = re.compile(r'(?<![A-Za-z0-9_])(?<!:):([A-Za-z_][A-Za-z0-9_]*)')


def scan_sql_params(sql):
    """识别 SQL 模板中的 :参数（字符串、反引号、注释之外；字母/下划线起始），去重保序。"""
    code = strip_sql_noise_mysql(str(sql or ''))
    seen, out = set(), []
    for m in SQL_PARAM.finditer(code):
        if m[1] not in seen:
            seen.add(m[1])
            out.append(m[1])
    return out


def sql_steps_errors(rule, ctx):
    """V4 卡片式 SQL 步骤（mode='sqlSteps'）：每步一条 SELECT 模板。

    SQL 内引用：``:参数名``=输入值；``:key.列``=前置 one 步骤的值；``{{key.列}}``=动态
    表名/字段名。policies/extensions 等未知字段原样保留、不校验内容；仅配置校验，不执行 SQL。
    """
    errors, names, known = [], set(), set()
    # 三个集合分开维护：ids=全部步骤 ID（非空且唯一）；keys=全部技术名（合法且唯一）；
    # known=可被后续引用的前置 one 步骤 key。ID 是系统生成的 UUID/旧标识，不按 SQL 标识符规则校验。
    ids, keys = set(), set()
    def ident(v):
        return isinstance(v, str) and bool(IDENTIFIER.fullmatch(v))
    if not str(rule.get('name', '')).strip(): errors.append('请填写规则名称')
    if ctx['conn_by_id'].get(rule.get('connection'), {}).get('engine') != 'mysql': errors.append('请选择当前项目已有的数据库连接')
    inputs = rule.get('inputs') if isinstance(rule.get('inputs'), list) else []
    for p in inputs:
        if not isinstance(p, dict): p = {}
        name = p.get('name')
        if not ident(name) or name in names: errors.append('输入参数名称无效或重复')
        names.add(str(name))
        if p.get('type') not in ('string', 'double', 'boolean', 'dateTime'): errors.append('输入参数数据类型无效')
        if p.get('source') not in ('binding', 'runtime'): errors.append('输入参数传入方式无效')
    if rule.get('mode') != 'sqlSteps': errors.append('取值规则版本不受支持')
    steps = rule.get('steps') if isinstance(rule.get('steps'), list) else []
    step_names = {s.get('key'): (str(s.get('name', '')).strip() or str(s.get('key'))) for s in steps if isinstance(s, dict)}
    if not steps: errors.append('至少需要一个查询步骤')
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f'步骤 {index + 1}：结构无效')
            continue
        key = step.get('key')
        title = '步骤「' + (str(step.get('name', '')).strip() or str(key)) + '」：'
        if not str(step.get('name', '')).strip(): errors.append(title + '请填写步骤名称')
        sid = step.get('id')
        if not isinstance(sid, str) or not sid.strip():
            errors.append(title + '步骤标识缺失')
        elif sid in ids:
            errors.append(title + '步骤标识重复')
        else:
            ids.add(sid)
        if not ident(key) or key in keys: errors.append(title + '步骤技术名无效或重复')
        if ident(key): keys.add(key)
        if step.get('cardinality') not in ('one', 'many'): errors.append(title + '预期记录数无效')
        sql = step.get('sql')
        if not isinstance(sql, str) or not sql.strip():
            errors.append(title + 'SQL 未填写')
        else:
            code = strip_sql_noise(sql)
            if not re.match(r'^\s*SELECT\b', code, re.I):
                errors.append(title + ('本期不支持 WITH 开头的语句' if re.match(r'^\s*WITH\b', code, re.I) else '须以 SELECT 开始'))
            if len([s for s in code.split(';') if s.strip()]) != 1:
                errors.append(title + '每张卡片只填写一条 SELECT')
            for m in SQL_REF.finditer(code):
                if m[2]:
                    if m[1] not in known:
                        errors.append(title + f'引用 {m[1]}.{m[2]}，但「{step_names.get(m[1], m[1])}」不存在、返回多条或尚未执行')
                elif m[1] not in names:
                    errors.append(title + '引用了未声明的输入参数 :' + m[1])
            for m in SQL_TOKEN.finditer(code):
                inner = m[1]
                if SQL_IDENT_DOT_IDENT.fullmatch(inner):
                    if inner.split('.')[0] not in known:
                        errors.append(title + f'动态标识 {inner} 须引用前置唯一记录步骤的输出')
                else:
                    errors.append(title + '引用格式无效')
        if ident(key) and step.get('cardinality') == 'one':
            known.add(key)
    result = rule.get('result') if isinstance(rule.get('result'), dict) else {}
    by_id = {}
    for s in steps:
        if isinstance(s, dict) and s.get('id') is not None and s.get('id') not in by_id:
            by_id[s['id']] = s
    out_step = by_id.get(result.get('step'))
    if out_step is None:
        errors.append('返回步骤不存在')
    if result.get('type') not in ('scalar', 'timeSeries') or result.get('valueType') not in ('string', 'double', 'boolean', 'dateTime'):
        errors.append('返回类型无效')
    timestamp = str(result.get('timestamp', '') or '').strip()
    value = str(result.get('value', '') or '').strip()
    if result.get('type') == 'scalar':
        if out_step is None or out_step.get('cardinality') != 'one' or not value:
            errors.append('单值返回须选择一条记录的步骤并填写值列')
    elif result.get('type') == 'timeSeries':
        if out_step is None or out_step.get('cardinality') != 'many' or not timestamp or not value or timestamp == value:
            errors.append('时间序列返回须选择多条记录的步骤，填写不同的时间列与值列')
    # 输出步骤非最后一步仅为界面提示（建议删除无用步骤），不阻断保存与发布。
    return list(dict.fromkeys(errors))
