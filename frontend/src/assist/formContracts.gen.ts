// 本文件由 `python3 -m workbench.assist_forms_gen` 生成 —— 禁止手改。
// 来源：contracts/forms/*.json（autofill/1 表单契约，接口文档 04 §6.1 冻结语法）。
// schemaDigest 为 canonical JSON（键排序、去 digest 字段）后的 SHA-256，
// 与后端 workbench/assist_forms.py 同口径；不一致即 CONTEXT_STALE（重新取上下文）。
// 契约演进：字段增删/类型/枚举/权限/依赖变化必须 schemaVersion+1；布局文案改动可不升版。


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




export const ASSIST_FORM_IDS = [
  "object",
  "property",
  "sharedProperty",
  "link",
  "rule",
  "action",
  "identity",
  "propertySource",
  "linkMapping",
  "actionBinding",
] as const


export type AssistFormId = (typeof ASSIST_FORM_IDS)[number]


export const FORM_OBJECT: AssistFormContract = {
  "codecs": [],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "label",
      "label": "对象名称",
      "maxLength": 120,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "comment",
      "label": "业务定义",
      "maxLength": 2000,
      "required": true,
      "type": "textarea",
    },
  ],
  "formId": "object",
  "lists": [],
  "refProviders": {},
  "schemaDigest": "575f537c972ff12a8317e8591742390619f23797aa93df0f7b7d1bc324aefa13",
  "schemaVersion": 1,
  "space": "ontology",
  "title": "对象定义",
}

export const FORM_PROPERTY: AssistFormContract = {
  "codecs": [
    "dataTypeTransform",
    "formattingCodec",
  ],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "label",
      "label": "属性名称",
      "maxLength": 120,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "comment",
      "label": "业务定义",
      "maxLength": 2000,
      "required": true,
      "type": "textarea",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "atomicGroup": "typeCore",
      "enum": [
        "string",
        "double",
        "boolean",
        "dateTime",
        "array",
        "struct",
        "timeSeries",
      ],
      "id": "dataType",
      "label": "数据类型",
      "required": true,
      "type": "enum",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "atomicGroup": "typeCore",
      "enum": [
        "string",
        "double",
        "boolean",
        "dateTime",
      ],
      "id": "obsType",
      "label": "观测值类型",
      "requires": "dataType",
      "type": "enum",
      "visibleWhen": {
        "field": "dataType",
        "op": "eq",
        "value": "timeSeries",
      },
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "codec": "formattingCodec",
      "id": "formatting",
      "label": "显示格式",
      "nullable": true,
      "requires": "dataType",
      "type": "group",
    },
  ],
  "formId": "property",
  "lists": [],
  "refProviders": {},
  "schemaDigest": "72b9f3678aed21369bd581ef314e2e673bc0802c02a884020fe25cb311991d22",
  "schemaVersion": 1,
  "space": "ontology",
  "title": "属性定义",
}

export const FORM_SHARED_PROPERTY: AssistFormContract = {
  "codecs": [
    "dataTypeTransform",
    "formattingCodec",
  ],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "label",
      "label": "属性名称",
      "maxLength": 120,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "comment",
      "label": "业务定义",
      "maxLength": 2000,
      "required": true,
      "type": "textarea",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "atomicGroup": "typeCore",
      "enum": [
        "string",
        "double",
        "boolean",
        "dateTime",
        "array",
        "struct",
        "timeSeries",
      ],
      "id": "dataType",
      "label": "数据类型",
      "required": true,
      "type": "enum",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "atomicGroup": "typeCore",
      "enum": [
        "string",
        "double",
        "boolean",
        "dateTime",
      ],
      "id": "obsType",
      "label": "观测值类型",
      "requires": "dataType",
      "type": "enum",
      "visibleWhen": {
        "field": "dataType",
        "op": "eq",
        "value": "timeSeries",
      },
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "codec": "formattingCodec",
      "id": "formatting",
      "label": "显示格式",
      "nullable": true,
      "requires": "dataType",
      "type": "group",
    },
  ],
  "formId": "sharedProperty",
  "lists": [],
  "refProviders": {},
  "schemaDigest": "f5bae9aa79f56a2c9f764efb1639cabd395aca41a01b2f0bd9f55cb35a3e2c4d",
  "schemaVersion": 1,
  "space": "ontology",
  "title": "共享属性定义",
}

export const FORM_LINK: AssistFormContract = {
  "codecs": [],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "label",
      "label": "正向名称",
      "maxLength": 120,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "from",
      "label": "起点对象",
      "refProvider": "object",
      "required": true,
      "type": "ref",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "to",
      "label": "终点对象",
      "refProvider": "object",
      "required": true,
      "type": "ref",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "enum": [
        "one-to-one",
        "one-to-many",
        "many-to-one",
        "many-to-many",
      ],
      "id": "cardinality",
      "label": "数量关系",
      "required": true,
      "type": "enum",
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "reverseLabel",
      "label": "反向名称",
      "maxLength": 120,
      "nullable": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "comment",
      "label": "业务定义",
      "maxLength": 2000,
      "type": "textarea",
    },
  ],
  "formId": "link",
  "lists": [],
  "refProviders": {
    "object": "ontologyObjects",
  },
  "schemaDigest": "cdfbaf008c8c34f32cc89eb17c68bfcf16f3983bba3337273a784496a9624263",
  "schemaVersion": 1,
  "space": "ontology",
  "title": "链接定义",
}

export const FORM_RULE: AssistFormContract = {
  "codecs": [],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "name",
      "label": "规则名称",
      "maxLength": 120,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "description",
      "label": "业务定义",
      "maxLength": 2000,
      "required": true,
      "type": "textarea",
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "content",
      "label": "规则内容",
      "maxLength": 4000,
      "nullable": true,
      "type": "textarea",
    },
  ],
  "formId": "rule",
  "lists": [],
  "refProviders": {},
  "schemaDigest": "37279adf5432ce25a48536f8a0af7a994271a38ea2401c772a6e7c891e974413",
  "schemaVersion": 1,
  "space": "ontology",
  "title": "业务规则",
}

export const FORM_ACTION: AssistFormContract = {
  "codecs": [],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "name",
      "label": "动作名称",
      "maxLength": 120,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "description",
      "label": "业务定义",
      "maxLength": 2000,
      "required": true,
      "type": "textarea",
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "effect",
      "label": "预期效果",
      "maxLength": 2000,
      "nullable": true,
      "type": "textarea",
    },
  ],
  "formId": "action",
  "lists": [],
  "refProviders": {},
  "schemaDigest": "06a1a23892ef88f212b4ae10aef46f28b08e20ac8dd046fbbeb6755f15e0e187",
  "schemaVersion": 1,
  "space": "ontology",
  "title": "动作定义",
}

export const FORM_IDENTITY: AssistFormContract = {
  "codecs": [],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "enum": [
        "database",
        "registered",
      ],
      "id": "mode",
      "label": "实例来源方式",
      "required": true,
      "type": "enum",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "connection",
      "label": "数据连接",
      "refProvider": "connection:mysql",
      "requires": "mode",
      "type": "ref",
      "visibleWhen": {
        "field": "mode",
        "op": "eq",
        "value": "database",
      },
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "table",
      "label": "来源表／视图",
      "refProvider": "table",
      "requires": "mode",
      "type": "ref",
      "visibleWhen": {
        "field": "mode",
        "op": "eq",
        "value": "database",
      },
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "primaryKey",
      "label": "实例主键",
      "refProvider": "identityField",
      "requires": "mode",
      "type": "ref",
      "visibleWhen": {
        "field": "mode",
        "op": "eq",
        "value": "database",
      },
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "note",
      "label": "说明",
      "maxLength": 2000,
      "nullable": true,
      "type": "textarea",
    },
  ],
  "formId": "identity",
  "lists": [],
  "refProviders": {
    "connection:mysql": "mysqlConnections",
    "identityField": "identityTableFields",
    "table": "catalogTables",
  },
  "schemaDigest": "4b1bade09ae9395bebd08cd8d4589c8d1460556fb265e41d3c65f916819f9509",
  "schemaVersion": 1,
  "space": "project",
  "title": "实例识别",
}

export const FORM_PROPERTY_SOURCE: AssistFormContract = {
  "codecs": [
    "lookupMatchRows",
    "redisKeyParams",
    "flowInputBindings",
  ],
  "formId": "propertySource",
  "refProviders": {
    "connection:any": "connectionsAndSources",
    "field": "catalogFields",
    "flow": "flows",
    "flow_field": "flowFields",
    "flow_output": "flowOutputs",
    "identityField": "identityTableFields",
    "parameter": "projectParameters",
    "property": "ontologyProperties",
    "table": "catalogTables",
  },
  "schemaDigest": "03ba2d6f027d69720155ddcc20da8e222d16a5a37dd8a5e77c929ac8498c9496",
  "schemaVersion": 1,
  "space": "project",
  "title": "属性取值来源",
  "variants": {
    "database": {
      "fields": [
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "connection",
          "label": "数据连接",
          "refProvider": "connection:any",
          "required": true,
          "type": "ref",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "table",
          "label": "表／视图",
          "refProvider": "table",
          "required": true,
          "requires": "connection",
          "type": "ref",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "result.valueField",
          "label": "取值字段",
          "refProvider": "field",
          "required": true,
          "requires": "table",
          "type": "ref",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "result.timestampField",
          "label": "时间字段",
          "nullable": true,
          "refProvider": "field",
          "requires": "result.valueField",
          "type": "ref",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "codec": "lookupMatchRows",
          "id": "lookup.match",
          "label": "匹配条件",
          "list": "lookupMatch",
          "requires": "table",
          "type": "list",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "note",
          "label": "说明",
          "maxLength": 2000,
          "nullable": true,
          "type": "textarea",
        },
      ],
      "lists": [
        {
          "id": "lookupMatch",
          "item": {
            "fields": [
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "field",
                "label": "匹配字段",
                "refProvider": "field",
                "required": true,
                "type": "ref",
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "enum": [
                  "eq",
                ],
                "id": "operator",
                "label": "操作符",
                "required": true,
                "type": "enum",
              },
              {
                "fields": [
                  {
                    "ai": {
                      "clearable": false,
                      "fillable": true,
                      "sensitive": false,
                    },
                    "enum": [
                      "identityKey",
                      "identityField",
                      "property",
                      "constant",
                      "parameter",
                    ],
                    "id": "kind",
                    "label": "取值方式",
                    "required": true,
                    "type": "enum",
                  },
                  {
                    "ai": {
                      "clearable": false,
                      "fillable": true,
                      "sensitive": false,
                    },
                    "id": "field",
                    "label": "身份表字段",
                    "refProvider": "identityField",
                    "requires": "value.kind",
                    "type": "ref",
                    "visibleWhen": {
                      "field": "value.kind",
                      "op": "eq",
                      "value": "identityField",
                    },
                  },
                  {
                    "ai": {
                      "clearable": false,
                      "fillable": true,
                      "sensitive": false,
                    },
                    "id": "property",
                    "label": "引用属性",
                    "refProvider": "property",
                    "requires": "value.kind",
                    "type": "ref",
                    "visibleWhen": {
                      "field": "value.kind",
                      "op": "eq",
                      "value": "property",
                    },
                  },
                  {
                    "ai": {
                      "clearable": false,
                      "fillable": true,
                      "sensitive": false,
                    },
                    "id": "value",
                    "label": "常量值",
                    "requires": "value.kind",
                    "type": "text",
                    "visibleWhen": {
                      "field": "value.kind",
                      "op": "eq",
                      "value": "constant",
                    },
                  },
                  {
                    "ai": {
                      "clearable": false,
                      "fillable": true,
                      "sensitive": false,
                    },
                    "id": "parameter",
                    "label": "项目参数",
                    "refProvider": "parameter",
                    "requires": "value.kind",
                    "type": "ref",
                    "visibleWhen": {
                      "field": "value.kind",
                      "op": "eq",
                      "value": "parameter",
                    },
                  },
                ],
                "id": "value",
                "label": "比较值",
                "required": true,
                "type": "group",
              },
            ],
            "id": "row",
            "type": "group",
          },
          "rowIdScope": "local",
        },
      ],
    },
    "field": {
      "fields": [
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "field",
          "label": "取值字段",
          "refProvider": "field",
          "required": true,
          "type": "ref",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "note",
          "label": "说明",
          "maxLength": 2000,
          "nullable": true,
          "type": "textarea",
        },
      ],
      "lists": [],
    },
    "flow": {
      "fields": [
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "flow",
          "label": "函数编排",
          "refProvider": "flow",
          "required": true,
          "type": "ref",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "output",
          "label": "取值输出",
          "refProvider": "flow_output",
          "required": true,
          "requires": "flow",
          "type": "ref",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "codec": "flowInputBindings",
          "id": "inputs",
          "label": "输入参数绑定",
          "list": "flowInputs",
          "requires": "flow",
          "type": "list",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "result.valueField",
          "label": "取值字段",
          "nullable": true,
          "refProvider": "flow_field",
          "requires": "output",
          "type": "ref",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "result.timestampField",
          "label": "时间字段",
          "nullable": true,
          "refProvider": "flow_field",
          "requires": "result.valueField",
          "type": "ref",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "note",
          "label": "说明",
          "maxLength": 2000,
          "nullable": true,
          "type": "textarea",
        },
      ],
      "lists": [
        {
          "id": "flowInputs",
          "item": {
            "fields": [
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "inputId",
                "label": "编排输入",
                "maxLength": 200,
                "required": true,
                "type": "text",
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "enum": [
                  "property",
                  "constant",
                  "instanceId",
                ],
                "id": "from",
                "label": "绑定来源",
                "required": true,
                "type": "enum",
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "property",
                "label": "引用属性",
                "refProvider": "property",
                "requires": "from",
                "type": "ref",
                "visibleWhen": {
                  "field": "from",
                  "op": "eq",
                  "value": "property",
                },
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "value",
                "label": "常量取值",
                "requires": "from",
                "type": "text",
                "visibleWhen": {
                  "field": "from",
                  "op": "eq",
                  "value": "constant",
                },
              },
            ],
            "id": "row",
            "type": "group",
          },
          "rowIdScope": "local",
        },
      ],
    },
    "redis": {
      "fields": [
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "connection",
          "label": "数据连接",
          "refProvider": "connection:any",
          "required": true,
          "type": "ref",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "enum": [
            "GET",
            "HGET",
          ],
          "id": "command",
          "label": "读取方式",
          "required": true,
          "type": "enum",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "id": "key",
          "label": "Key 模板",
          "maxLength": 200,
          "required": true,
          "type": "text",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "hashField",
          "label": "Hash 字段",
          "maxLength": 100,
          "nullable": true,
          "requires": "command",
          "type": "text",
          "visibleWhen": {
            "field": "command",
            "op": "eq",
            "value": "HGET",
          },
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "codec": "redisKeyParams",
          "id": "params",
          "label": "Key 参数绑定",
          "list": "keyParams",
          "requires": "key",
          "type": "list",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "enum": [
            "number",
            "integer",
            "text",
          ],
          "id": "conversion",
          "label": "结果转换",
          "type": "enum",
        },
        {
          "ai": {
            "clearable": false,
            "fillable": true,
            "sensitive": false,
          },
          "enum": [
            "null",
            "error",
          ],
          "id": "missing",
          "label": "未找到策略",
          "type": "enum",
        },
        {
          "ai": {
            "clearable": true,
            "fillable": true,
            "sensitive": false,
          },
          "id": "note",
          "label": "说明",
          "maxLength": 2000,
          "nullable": true,
          "type": "textarea",
        },
      ],
      "lists": [
        {
          "id": "keyParams",
          "item": {
            "fields": [
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "token",
                "label": "Key 占位符",
                "maxLength": 100,
                "required": true,
                "type": "text",
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "enum": [
                  "primary",
                  "property",
                  "identityField",
                ],
                "id": "from",
                "label": "绑定来源",
                "required": true,
                "type": "enum",
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "field",
                "label": "身份表字段",
                "refProvider": "identityField",
                "requires": "from",
                "type": "ref",
                "visibleWhen": {
                  "field": "from",
                  "op": "eq",
                  "value": "identityField",
                },
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "property",
                "label": "引用属性",
                "refProvider": "property",
                "requires": "from",
                "type": "ref",
                "visibleWhen": {
                  "field": "from",
                  "op": "eq",
                  "value": "property",
                },
              },
            ],
            "id": "row",
            "type": "group",
          },
          "rowIdScope": "local",
        },
      ],
    },
  },
}

export const FORM_LINK_MAPPING: AssistFormContract = {
  "codecs": [],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "sourceId",
      "label": "起点来源",
      "refProvider": "source",
      "required": true,
      "type": "ref",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "field",
      "label": "起点关联字段",
      "refProvider": "field",
      "required": true,
      "requires": "sourceId",
      "type": "ref",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "targetSourceId",
      "label": "终点来源",
      "refProvider": "source",
      "required": true,
      "type": "ref",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "targetField",
      "label": "终点匹配字段",
      "refProvider": "field",
      "required": true,
      "requires": "targetSourceId",
      "type": "ref",
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "note",
      "label": "说明",
      "maxLength": 2000,
      "nullable": true,
      "type": "textarea",
    },
  ],
  "formId": "linkMapping",
  "lists": [],
  "refProviders": {
    "field": "catalogFields",
    "source": "instanceSources",
  },
  "schemaDigest": "4a9fab280d494bdf20ce209f5c2652e7d6ee5ff6ad83a6d3d0036d7744fd4e3b",
  "schemaVersion": 1,
  "space": "project",
  "title": "链接映射",
}

export const FORM_ACTION_BINDING: AssistFormContract = {
  "codecs": [
    "actionParamRows",
  ],
  "fields": [
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "enum": [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
      ],
      "id": "method",
      "label": "请求方式",
      "required": true,
      "type": "enum",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "id": "path",
      "label": "接口地址",
      "maxLength": 500,
      "required": true,
      "type": "text",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "enum": [
        "json",
        "form",
      ],
      "id": "bodyFormat",
      "label": "请求体格式",
      "type": "enum",
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "description",
      "label": "接口说明",
      "maxLength": 2000,
      "nullable": true,
      "type": "textarea",
    },
    {
      "ai": {
        "clearable": false,
        "fillable": true,
        "sensitive": false,
      },
      "codec": "actionParamRows",
      "id": "parameters",
      "label": "请求参数",
      "list": "actionParams",
      "type": "list",
    },
    {
      "ai": {
        "clearable": true,
        "fillable": true,
        "sensitive": false,
      },
      "id": "note",
      "label": "说明",
      "maxLength": 2000,
      "nullable": true,
      "type": "textarea",
    },
  ],
  "formId": "actionBinding",
  "lists": [
    {
      "id": "actionParams",
      "item": {
        "fields": [
          {
            "ai": {
              "clearable": false,
              "fillable": true,
              "sensitive": false,
            },
            "id": "name",
            "label": "参数名",
            "maxLength": 200,
            "required": true,
            "type": "text",
          },
          {
            "ai": {
              "clearable": false,
              "fillable": true,
              "sensitive": false,
            },
            "enum": [
              "path",
              "query",
              "header",
              "body",
            ],
            "id": "in",
            "label": "参数位置",
            "required": true,
            "type": "enum",
          },
          {
            "fields": [
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "enum": [
                  "actionInput",
                  "instanceId",
                  "property",
                  "constant",
                ],
                "id": "from",
                "label": "取值来源",
                "required": true,
                "type": "enum",
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "inputId",
                "label": "动作输入",
                "refProvider": "actionInput",
                "requires": "value.from",
                "type": "ref",
                "visibleWhen": {
                  "field": "value.from",
                  "op": "eq",
                  "value": "actionInput",
                },
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "property",
                "label": "引用属性",
                "refProvider": "property",
                "requires": "value.from",
                "type": "ref",
                "visibleWhen": {
                  "field": "value.from",
                  "op": "eq",
                  "value": "property",
                },
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "enum": [
                  "string",
                  "number",
                  "boolean",
                ],
                "id": "valueType",
                "label": "固定值类型",
                "requires": "value.from",
                "type": "enum",
                "visibleWhen": {
                  "field": "value.from",
                  "op": "eq",
                  "value": "constant",
                },
              },
              {
                "ai": {
                  "clearable": false,
                  "fillable": true,
                  "sensitive": false,
                },
                "id": "value",
                "label": "固定值",
                "requires": "value.from",
                "type": "text",
                "visibleWhen": {
                  "field": "value.from",
                  "op": "eq",
                  "value": "constant",
                },
              },
            ],
            "id": "value",
            "label": "取值",
            "required": true,
            "type": "group",
          },
        ],
        "id": "row",
        "type": "group",
      },
      "rowIdScope": "local",
    },
  ],
  "refProviders": {
    "actionInput": "actionInputs",
    "property": "ontologyProperties",
  },
  "schemaDigest": "d7756db50ff2ba8b5add0f58345b1bfe253e403bba7788ca1f0e02297a06b4c2",
  "schemaVersion": 1,
  "space": "project",
  "title": "动作接口映射",
}

export const FORM_CONTRACTS: Record<AssistFormId, AssistFormContract> = {
  "object": FORM_OBJECT,
  "property": FORM_PROPERTY,
  "sharedProperty": FORM_SHARED_PROPERTY,
  "link": FORM_LINK,
  "rule": FORM_RULE,
  "action": FORM_ACTION,
  "identity": FORM_IDENTITY,
  "propertySource": FORM_PROPERTY_SOURCE,
  "linkMapping": FORM_LINK_MAPPING,
  "actionBinding": FORM_ACTION_BINDING,
}


/** 每契约文件 schemaVersion（字段增删/类型/枚举/权限/依赖变化时 +1） */
export const FORM_SCHEMA_VERSIONS: Record<AssistFormId, number> = {
  "object": 1,
  "property": 1,
  "sharedProperty": 1,
  "link": 1,
  "rule": 1,
  "action": 1,
  "identity": 1,
  "propertySource": 1,
  "linkMapping": 1,
  "actionBinding": 1,
}


/** 每契约文件 schemaDigest（canonical JSON 后 SHA-256；与后端 loader 同口径） */
export const FORM_SCHEMA_DIGESTS: Record<AssistFormId, string> = {
  "object": "575f537c972ff12a8317e8591742390619f23797aa93df0f7b7d1bc324aefa13",
  "property": "72b9f3678aed21369bd581ef314e2e673bc0802c02a884020fe25cb311991d22",
  "sharedProperty": "f5bae9aa79f56a2c9f764efb1639cabd395aca41a01b2f0bd9f55cb35a3e2c4d",
  "link": "cdfbaf008c8c34f32cc89eb17c68bfcf16f3983bba3337273a784496a9624263",
  "rule": "37279adf5432ce25a48536f8a0af7a994271a38ea2401c772a6e7c891e974413",
  "action": "06a1a23892ef88f212b4ae10aef46f28b08e20ac8dd046fbbeb6755f15e0e187",
  "identity": "4b1bade09ae9395bebd08cd8d4589c8d1460556fb265e41d3c65f916819f9509",
  "propertySource": "03ba2d6f027d69720155ddcc20da8e222d16a5a37dd8a5e77c929ac8498c9496",
  "linkMapping": "4a9fab280d494bdf20ce209f5c2652e7d6ee5ff6ad83a6d3d0036d7744fd4e3b",
  "actionBinding": "d7756db50ff2ba8b5add0f58345b1bfe253e403bba7788ca1f0e02297a06b4c2",
}

