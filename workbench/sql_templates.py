"""Template checks only. No SQL execution or claim of complete SQL validation."""
import re


def sql_template_errors(rule, ctx):
    errors, names, known, seen = [], set(), set(), set()
    if not str(rule.get('name', '')).strip(): errors.append('请填写规则名称')
    if ctx['conn_by_id'].get(rule.get('connection'), {}).get('engine') != 'mysql': errors.append('请选择当前项目已有的数据库连接')
    for p in rule.get('inputs', []):
        name = p.get('name', '')
        if not re.fullmatch(r'[A-Za-z_]\w*', name) or name in names: errors.append('输入参数名称无效或重复')
        names.add(name)
        if p.get('type') not in ('string','double','boolean','dateTime') or p.get('source') not in ('binding','runtime'): errors.append('输入参数类型或传入方式无效')
    parts = re.split(r'^\s*--\s*@step\s+(\w+)\s+(one|many)\s*$', str(rule.get('sqlTemplate', '')), flags=re.M)
    if len(parts) < 4: errors.append('请用 -- @step 步骤名 one 或 many 声明查询段')
    if re.sub(r'--[^\n]*', '', parts[0]).strip(): errors.append('SQL 必须写在 @step 查询段内')
    last, cardinality = '', ''
    for i in range(1, len(parts)-2, 3):
        sid, card, body = parts[i:i+3]
        label = f'查询段 {sid}：'
        if sid in seen: errors.append(label+'步骤名重复')
        seen.add(sid)
        code = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/", ' ', body)
        if not re.match(r'^\s*SELECT\b', code, re.I): errors.append(label+'请填写 SELECT 查询')
        if len([s for s in code.split(';') if s.strip()]) != 1: errors.append(label+'每段只填写一条查询')
        for m in re.finditer(r':(\w+)(?:\.(\w+))?', code):
            if (m[1] not in known if m[2] else m[1] not in names): errors.append(label+'参数或前置步骤不存在：'+m[0])
        for m in re.finditer(r'\{\{([^}]+)\}\}', code):
            if not re.fullmatch(r'\w+\.\w+', m[1]) or m[1].split('.')[0] not in known: errors.append(label+'动态标识须引用前面 one 查询段的输出：'+m[0])
        if card == 'one': known.add(sid)
        last, cardinality = sid, card
    result = rule.get('result') or {}
    if result.get('type') not in ('scalar','timeSeries') or result.get('valueType') not in ('string','double','boolean','dateTime'): errors.append('请选择返回类型和值类型')
    if result.get('type') == 'scalar' and cardinality != 'one': errors.append('单值输出的最后一段须为 one')
    if result.get('type') == 'timeSeries' and cardinality != 'many': errors.append('时间序列输出的最后一段须为 many')
    if not result.get('value') or result.get('type') == 'timeSeries' and (not result.get('timestamp') or result.get('timestamp') == result.get('value')): errors.append('请填写返回字段；时间与值字段不能相同')
    if not last: errors.append('至少填写一段 SQL 查询')
    return list(dict.fromkeys(errors))
