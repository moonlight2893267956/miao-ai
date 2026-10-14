# Agent 契约规范

> 规范版本 **v1**（对应声明文件里的 `api_version: 1`）
> 实现：`backend/app/contract.py` · 校验错误码：HTTP 400

## 1. 用来解决什么

平台的 Try Run 面板此前只能手写一段 JSON 去调 agent：调用方不知道字段叫什么、哪个必填、
该填什么值，只能翻 agent 源码或 README。**契约声明**让 agent 自己把这些信息说清楚：

- 平台按声明渲染**可视化表单**（中文标签、必填星号、字段说明、枚举下拉）；
- 提供**一键填充**的示例入参；
- 按出参声明**结构化展示结果**，而不是丢一坨 JSON 出来。

契约是**可选**的。不放声明文件的 agent 一切照旧，不受任何影响。

除 UI 之外，契约还承载一个**能力开关**：`conversation.enabled`（见 4.4）——声明它
平台才会在调用时注入多轮会话历史。其余字段仍然只影响 UI 与文档，不参与请求校验。

## 2. 放在哪里

放在 agent 代码包的**根目录**（与 `agent.py` 同级），文件名二选一：

| 文件名 | 说明 |
|---|---|
| `agent.yaml` | **推荐**。人写友好，可写注释 |
| `manifest.json` | 等价别名，适合脚本/工具生成 |

- 只认**根级精确文件名**，不递归查找（与 `agent.py` 的校验口径一致）。
- 两个文件**同时存在会被拒绝**，请只保留一个。
- 必须 **UTF-8** 编码，大小不超过 **64 KB**。
- 打包时会被自动带入：`zip -r v1.zip . -x '*.pyc' '__pycache__/*' 'agent.log'`。

## 3. 最小示例

```yaml
api_version: 1

input:
  schema:
    type: object
    required: [text]
    properties:
      text:
        type: string
        title: 内容
        description: 要处理的文本
  examples:
    - name: 默认
      value:
        text: "The quick brown fox jumps over the lazy dog."

output:
  schema:
    type: object
    properties:
      translated: {type: string, description: 译文}
  display:
    primary: translated
```

## 4. 字段参考

### 4.1 顶层

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `api_version` | int | 否 | 缺省 `1`；不认识的版本会被拒绝 |
| `name` | string | 否 | 习惯填平台上的 agent 名，便于人工核对（平台不据此改名） |
| `description` | string | 否 | 一句话说明这个 agent 做什么 |
| `input` | mapping | 否 | 入参声明，见 4.2 |
| `output` | mapping | 否 | 出参声明，见 4.2 |
| `conversation` | mapping | 否 | 多轮会话能力声明，见 4.4；不声明 = 不注入历史 |
| `x-*` | 任意 | 否 | 自定义扩展，平台原样保留、不做解释 |

**未列出的顶层字段会直接报错**（防止把 `imput` 写成 `input` 却毫无提示）。

### 4.2 `input` / `output`

`input`：

| 字段 | 说明 |
|---|---|
| `schema` | 入参结构，**根类型必须是 `object`** |
| `examples` | 示例入参数组，每项形如 `{name: 名称, value: {...}}`；`value` 必须是对象 |

`output`：

| 字段 | 说明 |
|---|---|
| `schema` | 出参结构，**根类型必须是 `object`** |
| `display.primary` | 结果里的**主字段**名（如 `answer`、`translated`、`explanation`），Try Run 会大字号突出它 |
| `display.secondary` | 其余想单独列出的字段名数组 |

### 4.3 schema 子集

平台只支持以下词汇（**不做**完整 JSON Schema 求值），其余键（如 `pattern`、`minimum`）会被原样保留但不解释：

| 键 | 说明 |
|---|---|
| `type` | `object` / `array` / `string` / `number` / `integer` / `boolean` |
| `title` | 表单里的中文标签 |
| `description` | 字段说明（表单里的辅助文字） |
| `default` | 默认值 |
| `enum` | 可选值数组（表单渲染为下拉） |
| `format` | 约定 `textarea` 时渲染成多行文本框 |
| `properties` | `type: object` 的子字段 |
| `items` | `type: array` 的元素结构 |
| `required` | 必填字段名数组，**必须 ⊆ 同级的 `properties`** |

嵌套深度上限 **8 层**。

> 表单对复杂字段（对象、对象数组）没有输入控件，会退化成等宽 JSON 小框，
> 所以**嵌套结构务必配 `examples`**，让用户一键填充后改几个字即可。

### 4.4 `conversation`（多轮会话能力）

声明这个 agent 需要「多轮会话上下文」，平台才会在调用时把历史注入
`config.conversation`。**不声明或 `enabled: false` 时平台不做任何注入**，
历史零开销（也是默认行为）。

```yaml
conversation:
  enabled: true
  max_history_turns: 10   # 请求读取的最大历史轮次，1..50，缺省 10
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `enabled` | bool | 否 | 缺省 `false`。只有 `true` 时平台才注入 `config.conversation` |
| `max_history_turns` | int | 否 | 注入的最大轮次（1..50，缺省 10）；**必须与 `enabled: true` 一起声明** |
| `x-*` | 任意 | 否 | 自定义扩展 |

两条硬规则（写错会在上传时就报错，不会静默失效）：

- `max_history_turns` 只有在 `enabled: true` 时才生效，单独写它会直接报错；
- 声明了 `enabled: true` 的 agent **应该**在 `invoke` 里读取 `config.get("conversation")`，
  否则等于多付了带宽却没用到上下文。

## 5. 平台行为

| 时机 | 行为 |
|---|---|
| 上传版本（`POST /agents/{name}/versions`） | 有声明且合法 → 与版本一起入库；**有声明但非法 → 400 拒绝**；没有声明 → 正常入库，契约为空 |
| 激活版本（`POST /agents/{name}/versions/activate`） | 解包后**重新解析并覆盖**契约（保证"生效契约 == 实际部署的代码"）；解析异常只记 warning，**绝不阻断激活** |
| 切换版本 | 契约随版本走：新版本没带声明文件，旧契约自动消失，不会残留误导 UI |
| 调用（`/invoke`、`/invoke/stream`、`/invoke/async`） | `input` / `output` 类型不符只在表单里给黄色提示，**不拦截请求**；`conversation` 段是例外——它是能力开关，平台据此决定是否注入历史 |
| 带 `conversation_id` 的调用 | 仅当生效版本的契约声明了 `conversation.enabled: true` 时，才注入按 §4.4 规则裁剪后的 `config.conversation`；不声明则即使传了 `conversation_id` 平台也只记账、不注入 |

## 6. 校验规则与报错

报错信息统一是 `<文件名>: <字段路径>: <原因>`，例如：

```
agent.yaml: input.schema.properties.positions.items: 不支持的 type 'objct'
agent.yaml: input.schema.required 里的 tags 不在 properties 中
agent.yaml: input.examples[0].value 必须是对象
manifest.json: 同时存在 agent.yaml 与 manifest.json，请只保留一个声明文件
```

常见触发条件：

- 顶层/字段名拼错（含 `properties` 的键名）；
- `type` 用了不支持的取值（`objct`、`str`、`list`…）；
- `required` 里有 `properties` 中不存在的字段；
- `examples[].value` 不是对象；
- schema 根类型不是 `object`；
- 文件不是 UTF-8、超过 64 KB、YAML 语法错误。

## 7. 完整示例：`wubi-explainer`

`demos/wubi-explainer/agent.yaml`：

```yaml
api_version: 1
name: wubi-explainer
description: 五笔拆字解释：在给定候选字根内挑选并讲清理由

input:
  schema:
    type: object
    required: [char, code, positions]
    properties:
      char:
        type: string
        title: 汉字
        description: 要讲解拆分的单个汉字，例如「照」
      code:
        type: string
        title: 五笔编码
        description: 纯小写字母，每一位对应 positions 的一项，例如「jvko」
      positions:
        type: array
        title: 逐位候选字根
        description: 位数必须与 code 一致；第 i 项的 key 必须等于 code 的第 i 个字符
        items:
          type: object
          required: [key, candidates]
          properties:
            index: {type: integer, description: 0 起的位序}
            key: {type: string, description: 该位对应的字母键}
            role: {type: string, description: 该位的组字角色，可空}
            candidates:
              type: array
              description: 该键位允许的字根集合，模型只能从这里挑
              items:
                type: object
                required: [glyph]
                properties:
                  glyph: {type: string, description: 字根字形}
                  name: {type: string, description: 字根名称}
      notes:
        type: array
        title: 已知标注
        description: 可选，逐条 message 作为额外提示
        items:
          type: object
          properties:
            message: {type: string}
      error:
        type: object
        title: 本次易错点
        description: 可选；传入后讲解会点名这两个键上的易混字根
        properties:
          expected: {type: string}
          actual: {type: string}
  examples:
    - name: 照（jvko）
      value:
        char: "照"
        code: "jvko"
        positions:
          - {key: "j", candidates: [{glyph: "日", name: "日字"}, {glyph: "曰", name: "曰字"}]}
          - {key: "v", candidates: [{glyph: "刀", name: "刀字"}]}
          - {key: "k", candidates: [{glyph: "口", name: "口字"}]}
          - {key: "o", candidates: [{glyph: "灬", name: "四点底"}]}
        notes: []

output:
  schema:
    type: object
    properties:
      explanation: {type: string, description: 拆字讲解正文}
      positions: {type: array, description: 逐位键与字根断言}
      suggestions: {type: array}
      error: {type: string, description: 失败原因，成功时不存在}
      self_check: {type: object, description: 自校验结果}
      model: {type: string}
  display:
    primary: explanation
    secondary: [positions, model]
```

## 8. 常见错误（YAML 的坑）

YAML 1.1 会把一批**看起来像字符串的值解析成别的类型**，这是契约报错最常见的来源：

| 写法 | 实际解析成 | 正确写法 |
|---|---|---|
| `key: no` / `yes` / `on` / `off` | 布尔 `False` / `True` | `key: "no"` |
| `code: 0123` | **八进制整数 `83`**（不是字符串！） | `code: "0123"` |
| `code: 123` | 整数 `123` | `code: "123"` |
| `char: 2026-01-01` | 日期对象（不是 JSON 类型，会被直接拒绝） | `char: "2026-01-01"` |
| `properties: {no: {...}}` | 键名变成布尔 `False` | 键名加引号：`"no": {...}` |

**经验法则：凡是不确定的值，一律加引号。**

平台已实测的两点边界（以 PyYAML 6.0.3 为准）：

- 单字母 `y` / `n` **不会**被解析成布尔（仍是字符串），但为了换解析器时不踩坑，建议一并加引号；
- `0123` 这类前导零数字会按**八进制**处理（变成 `83`），五笔编码、手机号、编号务必写成字符串。

平台会对非 JSON 基本类型的值（日期、二进制等）直接报错并指出位置，但 `123` 与 `"123"`
都是合法 JSON，只能靠 `schema.type` 约定 + 书写规范来避免，**不会**被自动拦截。

其余高频问题：

- 忘了 `value` 那一层：`examples` 的每一项必须是 `{name, value}`，真正的入参在 `value` 里；
- `required` 写了 `properties` 里不存在的字段；
- 声明了 `schema` 却没给 `examples`：嵌套结构在表单里只能手写 JSON，强烈建议补上示例。

## 9. 兼容性与演进

- **没有声明文件的 agent**：上传、激活、调用、Try Run 界面全部与不带此能力时一致。
- **契约基本只影响 UI**：不参与任何请求校验，不影响 `invoke` 的性能与行为。
  唯一例外是 4.4 的 `conversation` 能力开关——它以 opt-in 的方式决定是否注入会话历史；
  不声明（或 `enabled: false`）时，带 `conversation_id` 的调用也不会产生注入开销。
- 平台升级契规范时通过 `api_version` 区分；旧版本声明在新平台若不再支持会被明确拒绝，
  而不是被静默误读。
