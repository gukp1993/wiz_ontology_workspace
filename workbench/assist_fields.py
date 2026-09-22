"""表单辅助填写（AI 建议）场景与字段白名单注册表 —— T0 协议冻结物（2026-09-21）。

本文件是 assist-context / assist-generate 两个接口的唯一字段契约来源：
* 场景（targetKind）→ 可编辑字段白名单（键、类型、必填、长度、枚举、引用类型）。
* assist_context（T1）用本表裁剪 draft、生成 editableFields 说明；
* assist_schema（T2）用本表校验模型输出：白名单外字段键、未知枚举、错误类型一律拒绝；
* assist_service（T4）用本表做引用核验（ref 类字段的值必须存在于权威候选集）。

冻结约定（接口文档 04 §5）：
* 字段键是受限的扁平键（复合组用组键整体替换，如 params / inputs / lookup.match）；
* 模型输出不允许出现白名单之外的任何键；建议的 proposedValues 值类型必须与字段类型一致；
* ref 类字段值必须是本次上下文发送给模型的候选 id 之一（后端核验存在性）；
* 本表只描述「辅助填写可改什么」，保存仍走各表单原有业务校验（CAS、共享影响确认等）。
"""
import hashlib
import json

# ---- 协议上限（T0 冻结，不得放宽 server 既有 2MB 请求上限） ----
MAX_INTENT = 4000          # 用户意图文字
MAX_ANSWER = 2000          # 单个补充问题回答
MAX_QUESTIONS = 3          # 一次生成的补充问题数
MAX_SUGGESTIONS = 12       # 一次生成的建议条数
MAX_DRAFT_JSON = 200_000   # 规范化编辑快照字节数
MAX_ISSUES = 30            # 检查模式问题条数
TOKEN_TTL = 600            # contextToken 有效期（秒）
MAX_MODEL_TOKENS = 4000    # 单次模型输出 max_tokens

# ---- 字段种类 ----
# text/textarea：字符串；select：options 枚举之一；url：http(s) 开头字符串；
# ref：值为候选 id（引用类型 ref 指明候选集，generate 时按权威候选核验存在性）；
# composite：整组替换的结构化对象（schema 由 assist_schema 校验）。
TEXT, TEXTAREA, SELECT, URL, REF, COMPOSITE = 'text', 'textarea', 'select', 'url', 'ref', 'composite'

# ref 候选集类型（context 阶段由 assist_context 从权威数据装载，generate 阶段核验）
REF_OBJECT = 'object'                    # 本体对象稳定 id（mg:object_*）
REF_CONN_MYSQL = 'connection:mysql'      # 项目 MySQL 连接 id
REF_CONN_REDIS = 'connection:redis'      # 项目 Redis 连接 id
REF_CONN_ANY = 'connection:any'          # 数据连接（含已登记 db/redis 来源，前缀形式）
REF_TABLE = 'table'                      # 目录表名
REF_FIELD = 'field'                      # 目录字段名
REF_FLOW = 'flow'                        # 编排 id
REF_FLOW_OUTPUT = 'flow_output'          # 编排输出 id
REF_FLOW_FIELD = 'flow_field'            # 编排输出元素字段 id
REF_SOURCE = 'source'                    # 实例来源/补充来源标识
REF_PROPERTY = 'property'                # 本体属性稳定 id

_DATA_TYPES = ['xsd:string', 'xsd:double', 'xsd:boolean', 'xsd:dateTime', 'xsd:array', 'xsd:struct']
_OBS_TYPES = ['xsd:string', 'xsd:double', 'xsd:boolean', 'xsd:dateTime']


def _f(key, label, kind, required=False, options=None, ref=None, max_len=2000, group=None, help=''):
    return {'key': key, 'label': label, 'kind': kind, 'required': required,
            'options': options, 'ref': ref, 'maxLen': max_len, 'group': group, 'help': help}


# ---- 场景注册表（targetKind → 定义） ----
# space：ontology | project。fields：键 → 规格（dict 保序）。
SCENARIOS = {
    'object': {
        'space': 'ontology', 'label': '对象定义',
        'fields': [
            _f('label', '对象名称', TEXT, required=True, max_len=120, help='对象的业务名称，同一本体内唯一'),
            _f('comment', '业务定义', TEXTAREA, required=True, help='对象是什么、由什么构成、处于什么层级'),
        ],
    },
    'property': {
        'space': 'ontology', 'label': '属性定义',
        'fields': [
            _f('label', '属性名称', TEXT, required=True, max_len=120),
            _f('comment', '业务定义', TEXTAREA, required=True),
            _f('dataType', '数据类型', SELECT, required=True, options=_DATA_TYPES,
               help='业务数据类型；timeSeries 用 xsd:double 等观测类型表达时序由 mg:valueShape 承载'),
            _f('obsType', '观测值类型', SELECT, options=_OBS_TYPES, group='dataType',
               help='仅数据类型为时间序列时需要；与数据类型构成原子组'),
            _f('formatting', '显示格式', COMPOSITE, group='formatting',
               help='mg:formatting 配置（受限子集：常用样式与参数；历史样式与嵌套元素格式不在建议范围）'),
        ],
    },
    # sharedProperty 与 property 字段集一致，仅目标形态与影响确认流程不同
    'sharedProperty': {
        'space': 'ontology', 'label': '共享属性定义', 'alias_of': 'property',
        'fields': [],
    },
    'link': {
        'space': 'ontology', 'label': '链接定义',
        'fields': [
            _f('label', '正向名称', TEXT, required=True, max_len=120),
            _f('from', '起点对象', REF, required=True, ref=REF_OBJECT),
            _f('to', '终点对象', REF, required=True, ref=REF_OBJECT),
            _f('cardinality', '数量关系', SELECT, required=True,
               options=['one-to-one', 'one-to-many', 'many-to-one', 'many-to-many']),
            _f('reverseLabel', '反向名称', TEXT, max_len=120),
            _f('comment', '业务定义', TEXTAREA),
        ],
    },
    'rule': {
        'space': 'ontology', 'label': '业务规则',
        'fields': [
            _f('name', '规则名称', TEXT, required=True, max_len=120),
            _f('description', '业务定义', TEXTAREA, required=True),
            _f('content', '规则内容', TEXTAREA, max_len=4000, help='选填；不添加输出结果字段'),
        ],
    },
    'action': {
        'space': 'ontology', 'label': '动作定义',
        'fields': [
            _f('name', '动作名称', TEXT, required=True, max_len=120),
            _f('description', '业务定义', TEXTAREA, required=True),
            _f('effect', '预期效果', TEXTAREA, max_len=2000, help='选填；不新增动作参数或提交条件'),
        ],
    },
    'identity': {
        'space': 'project', 'label': '实例识别',
        'fields': [
            _f('mode', '实例来源方式', SELECT, required=True, options=['database', 'registered']),
            _f('connection', '数据连接', REF, ref=REF_CONN_MYSQL, group='mode',
               help='仅 database 模式需要'),
            _f('table', '来源表／视图', REF, ref=REF_TABLE, group='mode', help='仅 database 模式需要'),
            _f('primaryKey', '实例主键', REF, ref=REF_FIELD, group='mode',
               help='仅 database 模式需要；须为所选表中的字段'),
            _f('note', '说明', TEXTAREA, help='对象级项目说明（含登记实例的业务含义）'),
        ],
    },
    'propertySource': {
        'space': 'project', 'label': '属性取值来源',
        # 按 draft.kind 分派子白名单（kind 本身不可由建议修改）
        'fields': [], 'kinds': {
            'field': [
                _f('field', '取值字段', REF, required=True, ref=REF_FIELD),
                _f('note', '说明', TEXTAREA),
            ],
            'database': [
                _f('connection', '数据连接', REF, required=True, ref=REF_CONN_ANY),
                _f('table', '表／视图', REF, required=True, ref=REF_TABLE),
                _f('result.valueField', '取值字段', REF, required=True, ref=REF_FIELD),
                _f('result.timestampField', '时间字段', REF, ref=REF_FIELD,
                   help='时间序列属性需要'),
                _f('lookup.match', '匹配条件', COMPOSITE, group='lookup.match',
                   help='行结构 {field,operator:"eq",value:{kind:identityKey|identityField|property|constant|parameter,...}}；不做同名推断'),
                _f('note', '说明', TEXTAREA),
            ],
            'redis': [
                _f('connection', '数据连接', REF, required=True, ref=REF_CONN_ANY,
                   help='Redis 连接或已登记 Redis 来源'),
                _f('command', '读取方式', SELECT, required=True, options=['GET', 'HGET']),
                _f('key', 'Key 模板', TEXT, required=True, max_len=200,
                   help='占位符 {参数名}；不猜测 Key'),
                _f('hashField', 'Hash 字段', TEXT, max_len=100, help='仅 HGET 需要'),
                _f('params', 'Key 参数绑定', COMPOSITE, group='params',
                   help='结构 {占位符:{from:"primary"}|{from:"identityField",field}|{from:"property",property}}'),
                _f('conversion', '结果转换', SELECT, options=['number', 'integer', 'text']),
                _f('missing', '未找到策略', SELECT, options=['null', 'error']),
                _f('note', '说明', TEXTAREA),
            ],
            'flow': [
                _f('flow', '函数编排', REF, required=True, ref=REF_FLOW),
                _f('output', '取值输出', REF, required=True, ref=REF_FLOW_OUTPUT),
                _f('inputs', '输入参数绑定', COMPOSITE, group='inputs',
                   help='结构 {输入id:{from:"property",property}|{from:"constant",value}|{from:"instanceId"}}；运行时时间输入保持待配置'),
                _f('result.valueField', '取值字段', REF, ref=REF_FLOW_FIELD,
                   help='仅列表输出绑时间序列属性时需要'),
                _f('result.timestampField', '时间字段', REF, ref=REF_FLOW_FIELD),
                _f('note', '说明', TEXTAREA),
            ],
        },
    },
    'linkMapping': {
        'space': 'project', 'label': '链接映射',
        'fields': [
            _f('sourceId', '起点来源', REF, required=True, ref=REF_SOURCE),
            _f('field', '起点关联字段', REF, required=True, ref=REF_FIELD),
            _f('targetSourceId', '终点来源', REF, required=True, ref=REF_SOURCE),
            _f('targetField', '终点匹配字段', REF, required=True, ref=REF_FIELD),
            _f('note', '说明', TEXTAREA, help='两端字段的等价依据；同名不构成依据'),
        ],
    },
    'actionBinding': {
        'space': 'project', 'label': '动作接口映射',
        'fields': [
            _f('method', '请求方式', SELECT, required=True,
               options=['GET', 'POST', 'PUT', 'PATCH', 'DELETE']),
            _f('path', '接口地址', URL, required=True, max_len=500,
               help='完整 URL（协议+域名+路径）；不编造域名'),
            _f('bodyFormat', '请求体格式', SELECT, options=['json', 'form']),
            _f('description', '接口说明', TEXTAREA, max_len=2000),
            _f('parameters', '请求参数', COMPOSITE, group='parameters',
               help='行结构 {name,in,value:{from:"actionInput"|"instanceId"|"property"|"constant",...}}；认证与凭据不在建议范围'),
            _f('note', '说明', TEXTAREA),
        ],
    },
}

# 认证/凭据相关键（任何场景都不可被建议修改）
FORBIDDEN_KEYS = {'auth', 'auth.type', 'auth.credentialId', 'auth.name', 'auth.in',
                  'apiKey', 'api_key', 'credentialId', 'secret', 'password'}


def resolve(target_kind):
    """场景定义（含 sharedProperty → property 的别名展开）；未知场景抛 ValueError。"""
    spec = SCENARIOS.get(str(target_kind or ''))
    if spec is None:
        raise ValueError('未知的辅助填写目标类型：' + str(target_kind))
    if spec.get('alias_of'):
        base = SCENARIOS[spec['alias_of']]
        return {'space': base['space'], 'label': spec['label'], 'fields': base['fields'],
                'kinds': base.get('kinds')}
    return spec


def fields_for(target_kind, draft_kind=None):
    """按场景（及 propertySource 的 draft.kind）返回字段规格列表。"""
    spec = resolve(target_kind)
    if spec.get('kinds'):
        rows = spec['kinds'].get(str(draft_kind or ''))
        if rows is None:
            raise ValueError('未知的属性取值来源类型：' + str(draft_kind))
        return rows
    return spec['fields']


def field_map(target_kind, draft_kind=None):
    return {f['key']: f for f in fields_for(target_kind, draft_kind)}


def normalize_draft(target_kind, draft):
    """按白名单裁剪编辑快照；白名单外的键一律拒绝（400 语义，由路由层转换）。

    返回（裁剪后的 dict, draft_kind）。draft 必须是 dict；propertySource 依据
    draft['kind'] 选择子白名单，kind 缺失或未知报错。
    """
    if draft is None:
        draft = {}
    if not isinstance(draft, dict):
        raise ValueError('编辑快照必须是 JSON 对象')
    spec = resolve(target_kind)
    draft_kind = None
    if spec.get('kinds'):
        draft_kind = str(draft.get('kind') or '')
        if draft_kind not in spec['kinds']:
            raise ValueError('编辑快照缺少有效的 kind 字段')
    fmap = field_map(target_kind, draft_kind)
    cleaned = {}
    if spec.get('kinds'):
        cleaned['kind'] = draft_kind
    for key, value in draft.items():
        if key in FORBIDDEN_KEYS or key.split('.')[0] in ('auth',):
            raise ValueError('编辑快照包含不可辅助修改的字段：' + str(key))
        if key == 'kind' and spec.get('kinds'):
            continue
        if key not in fmap:
            raise ValueError('编辑快照包含白名单之外的字段：' + str(key))
        cleaned[key] = value
    return cleaned, draft_kind


def editable_fields_payload(target_kind, draft_kind=None):
    """context 响应用的字段说明列表（不含内部 help 之外的内容裁剪）。"""
    return [{'key': f['key'], 'label': f['label'], 'kind': f['kind'], 'required': f['required'],
             'options': f['options'], 'group': f['group'], 'help': f['help']}
            for f in fields_for(target_kind, draft_kind)]


def canonical_hash(obj):
    """规范化 JSON 的 sha256（draft 摘要 / 指纹计算共用）。"""
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
