"""autofill/1 表单契约 → 前端元数据生成器（T1）。

运行：`python3 -m workbench.assist_forms_gen`
读取 `contracts/forms/*.json`（经 workbench.assist_forms 严格校验），产出
`frontend/src/assist/formContracts.gen.ts`：每个 formId 的字段元数据常量、契约类型、
FORM_CONTRACTS 记录与每文件 schemaVersion/schemaDigest 导出。

生成物入库、禁止手改（接口文档 04 §6.1）；确定性输出——同输入同字节，
由 tests/test_autofill_contracts.py 做二次运行字节比对守护。
digest 口径与后端 loader 一致：canonical JSON（键排序、去 schemaDigest）后 SHA-256。
"""
import json
import re

from workbench import assist_forms
from workbench.paths import CODE_ROOT

OUTPUT_PATH = CODE_ROOT / 'frontend' / 'src' / 'assist' / 'formContracts.gen.ts'

_HEADER = """\
// 本文件由 `python3 -m workbench.assist_forms_gen` 生成 —— 禁止手改。
// 来源：contracts/forms/*.json（autofill/1 表单契约，接口文档 04 §6.1 冻结语法）。
// schemaDigest 为 canonical JSON（键排序、去 digest 字段）后的 SHA-256，
// 与后端 workbench/assist_forms.py 同口径；不一致即 CONTEXT_STALE（重新取上下文）。
// 契约演进：字段增删/类型/枚举/权限/依赖变化必须 schemaVersion+1；布局文案改动可不升版。
"""

_TYPES = """\
export type AssistFormFieldType = 'text' | 'textarea' | 'enum' | 'boolean' | 'ref' | 'group' | 'list'

export type AssistFormWhenOp = 'eq' | 'ne' | 'in' | 'notEmpty'

/** 受限条件表达（{field,op,value}，不是 eval）；field 为同作用域点路径，行内相对行根 */
export interface AssistFormWhen {
  field: string
  op: AssistFormWhenOp
  value?: unknown
}

/** ai 权限：fillable 默认 true（false=系统派生/只读）；clearable 仅 nullable 字段合法；sensitive 不进 prompt 且不可写 */
export interface AssistFormAi {
  fillable: boolean
  clearable: boolean
  sensitive: boolean
}

/** 契约字段定义（04 §6.1 冻结键集）；list.item 行包装节点仅 id/type/fields */
export interface AssistFormField {
  id: string
  label?: string
  type: AssistFormFieldType
  required?: boolean
  maxLength?: number
  enum?: string[]
  nullable?: boolean
  visibleWhen?: AssistFormWhen
  editableWhen?: AssistFormWhen
  /** 同名原子组必须整组生效（如 typeCore=dataType+obsType） */
  atomicGroup?: string
  /** 依赖字段（点路径，同作用域） */
  requires?: string
  /** 省略时按 fillable=true（04 §6.1 默认值）；list.item 行包装节点可无 ai */
  ai?: AssistFormAi
  /** 已注册业务 setter/转换标识（不携带可执行代码） */
  codec?: string
  /** ref 候选角色（键，具体提供方见 refProviders） */
  refProvider?: string
  /** group 嵌套固定结构；codec 托管的组（如 formatting）无内嵌 fields */
  fields?: AssistFormField[]
  /** list 字段指向的 list 声明 id */
  list?: string
}

/** list 行结构声明：rowIdScope 冻结 local（客户端本地稳定 rowId） */
export interface AssistFormListDef {
  id: string
  rowIdScope: 'local'
  item: AssistFormField
}

/** propertySource 按 draft.kind 分派的子契约变体 */
export interface AssistFormVariant {
  fields: AssistFormField[]
  lists: AssistFormListDef[]
}

/** 表单契约（propertySource 用 variants，其余用顶层 fields/lists） */
export interface AssistFormContract {
  formId: string
  schemaVersion: number
  schemaDigest: string
  title: string
  space: 'ontology' | 'project'
  fields?: AssistFormField[]
  lists?: AssistFormListDef[]
  variants?: Record<string, AssistFormVariant>
  codecs: string[]
  refProviders: Record<string, string>
}
"""


def _const_name(form_id):
    return 'FORM_' + re.sub(r'(^|[a-z0-9])([A-Z])', r'\1_\2', form_id).upper()


def _ts_literal(value, indent=0):
    """确定性 TS 字面量：对象键排序、数组保持契约顺序、字符串 json 转义。"""
    pad = '  ' * indent
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not value:
            return '[]'
        rows = ',\n'.join('  ' * (indent + 1) + _ts_literal(v, indent + 1) for v in value)
        return '[\n' + rows + ',\n' + pad + ']'
    if isinstance(value, dict):
        if not value:
            return '{}'
        rows = ',\n'.join(
            '  ' * (indent + 1) + json.dumps(str(k), ensure_ascii=False) + ': '
            + _ts_literal(value[k], indent + 1)
            for k in sorted(value, key=str))
        return '{\n' + rows + ',\n' + pad + '}'
    raise TypeError('契约含不可生成值：%r' % (value,))


def build_ts():
    """装载全部契约并产出 TS 文本；同一契约集永远产出同一字节。"""
    constants = []
    contract_names = []
    versions_rows = []
    digests_rows = []
    for form_id in assist_forms.FORM_IDS:
        doc, version, digest = assist_forms.load_raw(form_id)
        payload = {
            'formId': form_id,
            'schemaVersion': version,
            'schemaDigest': digest,
            'title': doc['title'],
            'space': doc['space'],
            'codecs': doc.get('codecs') or [],
            'refProviders': doc.get('refProviders') or {},
        }
        if form_id == assist_forms.PROPERTY_SOURCE_FORM:
            payload['variants'] = doc['variants']
        else:
            payload['fields'] = doc['fields']
            payload['lists'] = doc.get('lists') or []
        name = _const_name(form_id)
        constants.append('export const %s: AssistFormContract = %s'
                         % (name, _ts_literal(payload, 0)))
        contract_names.append('  %s: %s' % (json.dumps(form_id), name))
        versions_rows.append('  %s: %d' % (json.dumps(form_id), version))
        digests_rows.append('  %s: %s' % (json.dumps(form_id), json.dumps(digest)))

    parts = [_HEADER, _TYPES, '']
    parts.append('export const ASSIST_FORM_IDS = [\n'
                 + ',\n'.join('  ' + json.dumps(fid) for fid in assist_forms.FORM_IDS)
                 + ',\n] as const\n')
    parts.append('export type AssistFormId = (typeof ASSIST_FORM_IDS)[number]\n')
    parts.append('\n\n'.join(constants))
    parts.append(
        'export const FORM_CONTRACTS: Record<AssistFormId, AssistFormContract> = {\n'
        + ',\n'.join(contract_names) + ',\n}\n')
    parts.append(
        '/** 每契约文件 schemaVersion（字段增删/类型/枚举/权限/依赖变化时 +1） */\n'
        'export const FORM_SCHEMA_VERSIONS: Record<AssistFormId, number> = {\n'
        + ',\n'.join(versions_rows) + ',\n}\n')
    parts.append(
        '/** 每契约文件 schemaDigest（canonical JSON 后 SHA-256；与后端 loader 同口径） */\n'
        'export const FORM_SCHEMA_DIGESTS: Record<AssistFormId, string> = {\n'
        + ',\n'.join(digests_rows) + ',\n}\n')
    return '\n\n'.join(parts) + '\n'


def main(argv=None):
    text = build_ts()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print('已生成 %s（%d 个契约，%d 字节）' % (OUTPUT_PATH, len(assist_forms.FORM_IDS), len(text.encode('utf-8'))))
    for form_id in assist_forms.FORM_IDS:
        print('  %-16s v%d %s' % (form_id, assist_forms.schema_version(form_id),
                                  assist_forms.schema_digest(form_id)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
