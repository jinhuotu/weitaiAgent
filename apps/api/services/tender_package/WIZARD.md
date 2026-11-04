# 压缩包标书向导与统一 Issue

本次只叠加只读投影与独立前端组件。现有 view、action、解包、分类、物料补齐、SSOT、货物适配器和工程类生成函数均保持原实现。

## 文件清单

后端新增：

- `E:/code/python/weitaiAgent/apps/api/services/tender_package/wizard.py`：Issue 模型、收集器注册表、只读状态投影。
- `E:/code/python/weitaiAgent/apps/api/services/tender_package/test_wizard.py`：6 项定向用例。
- `E:/code/python/weitaiAgent/apps/api/services/tender_package/WIZARD.md`：本说明。

后端修改：

- `E:/code/python/weitaiAgent/apps/api/routers/tender_package.py`：仅新增 GET `/{key}/wizard`。

前端新增：

- `E:/code/python/weitaiAgentui/src/views/TenderPackageWizard.vue`：自动阶段、人工待办、草稿产物与高级模式入口。
- `E:/code/python/weitaiAgentui/src/components/tender-package/IssueDynamicForm.vue`：表单状态、必填校验与提交事件。
- `E:/code/python/weitaiAgentui/src/components/tender-package/IssueSchemaField.vue`：递归对象、数组及六类输入控件。
- `E:/code/python/weitaiAgentui/src/lib/tender-package-wizard.ts`：共享类型、Schema 校验及声明式参数映射。

前端修改：

- `E:/code/python/weitaiAgentui/src/router/index.ts`：新增 `/tender-package-wizard` 映射。
- `E:/code/python/weitaiAgentui/src/config/nav.ts`：向导加入常显入口，原 `/tender-package` 标为高级模式，沿用 `/tenders` 权限。

原 TenderPackageView.vue、TendersView.vue、请求和下载封装均未编辑。

## 只读接口与契约

`GET /api/v1/tender-package/{key}/wizard` 沿用当前登录用户、菜单权限和 owner 范围。调用现有 `PackageStore.view`，再调用纯投影 `build_wizard`；不会保存快照或触发生成。

顶层字段：`schema_version`、`id`、`revision`、`stage`、`stage_label`、`progress:{done,total}`、`issues`、`can_auto_continue`、`next_action`、`next_hint`、`retry_required`、`retry_action`、`result`、`test_only`、`ready_for_submission:false`。

每项 Issue：

- `id`：问题身份与精确目标定位的稳定摘要；参数问题裁决后保留身份，状态变为 resolved。
- `type`：预定义 material_conflict / role_ambiguity / missing_field / price_missing / business_missing / file_problem / rule_uncovered；保留可扩展字符串及 `register(name)` 收集器注册表。
- `severity`：blocking / warning / info；`title`、`description` 为用户说明。
- `evidence`：label / locator / quote；定位采用逻辑文件路径及人读位置，清除服务器绝对路径。
- `action`：kind / options / schema / default。六类 kind 为 single_choice、multi_choice、text、number、file、confirm；不预选响应、材质或报价。
- `target_action`：现有 operation、arguments 模板；`{"$answer":"字段名"}` 表示取表单答案，不执行表达式。可选 upload 描述复用现有 sources 上传接口。
- `status`：open / resolved / deferred；`unavailable_reason` 指明暂不能提交的原因和高级模式入口。

表单支持本轮使用的 JSON Schema 子集：object/properties/required、array/items/minItems/maxItems/uniqueItems、string/minLength/maxLength/pattern/enum、number/integer/minimum/maximum、boolean/const、binary 文件输入。不是完整 Draft 2020-12 验证器；未来引入其它关键字须补齐通用校验及对应测试。

确认人由现有 action 接口使用当前登录账号覆盖；原接口负责再次校验、版本冲突保护、来源和历史记录。新接口不新增业务写入逻辑。

## 本轮接入的收集器

1. 物料名称/型号/导体差异：读取 recognition 的来源与合成历史，映射 material action；参数字段仅在现有 composer 已提供此类结构时出现，通用物料只要求名称。SSOT 冻结的物料不覆盖事实，转高级模式核验。
2. 文件用途不确定：排除纯压缩容器，人工纠正映射 role action；已纠正记录显示 resolved。
3. 标包分配：多标包重复表单、分标选择和物料多选；由现有 packages 操作校验全量覆盖与重复归属。
4. 参数核验：复用 review.catalog，保留原行、既有值、冲突双方依据；映射 review_rows，无自动响应结论。
5. 报价：复用完整 document_id/table/row/column，映射 price 操作；金额有限且非负，标包未确认时说明原因。实际被合并单元格保护拒绝的位置转 deferred，不把“有报价记录”冒充“已写入”。
6. 商务资料：复用 library、business_catalog 和 business 操作；真实候选供多选，未核实或无原文出处时禁用并说明原因。原表核验需要真实来源及定位。

尚未覆盖：所有 SSOT 字段的专用补录表单、加密/损坏文件的专门 Issue 表单及更多行业规则。本轮以 rule_uncovered/deferred 总结其它待确认事实，提供高级模式；file_problem 类型预留，现有失败通过明确错误、显式重试及高级入口处理。deferred 不代表人工通过，不会据此生成保证值或价格。

## 自动推进与产物状态

进入已选任务后顺序复用 unpack → classify → ssot；有 open Issue 时暂停，提交后重新读取状态。没有 open Issue 且草稿缺失或输入刚修改时，再调用现有 generate。失败、处理中、版本冲突均停止自动重试。商务原表/资料核验依赖已有草稿时，会在首次草稿后成为新待办。

生成与渲染实现未改变。产物按包与文件显示，技术 JSON 及原始历史默认折叠。结构产物、渲染完成、视觉人工验收严格区分；不自动标记可投标。

## 本轮定向验证

- 后端 6 个新增向导用例通过；现有 test_web_api.py 的 4 项接口用例通过，包含上传—解包—纠正—核验—生成—下载、owner 权限、旧版本拒绝及旧路由隔离。合计 10 个不同定向用例；未跑全量回归。
- 通用前端表单 13 个检查通过：必填、枚举、金额范围/有限值、整数、确认、文件、多选重复、嵌套必填、无默认决策及参数映射。
- 新增组件的严格定向 vue-tsc 检查通过。整项目严格检查仍有原 TendersView.vue 的 10 处未使用声明错误，本轮不修改该文件；关闭 noUnused 两项的整项目类型检查可通过，不冒充完整严格构建通过。
- 浏览器隔离样本使用真实路由/action 和本次组件，自动完成解包、识别、汇聚；空值和负数校验拒绝；确认 1 个测试标包、4 项合成参数、2 个测试报价后自动生成 3 份草稿，技术文件下载成功。无来源商务项保持待核验；高级模式与原 iframe 能正常加载。
- 浏览器检查中 storage_path / sha256 / table_index 均未在默认可见正文出现，高级详情默认折叠，无横向溢出。截图：`E:/code/python/weitaiAgent/tmp/wizard-browser-check/wizard-proof.png`。
- 临时实例只监听 loopback、使用 tmp 内的合成样本及测试用户；外部渲染器在测试进程内隔离，本轮没有把样本结构检查宣称为视觉验收。测试实例检查后关闭，不替换现有服务。
- 当前真实任务只读检查仍为 state-00000043.json，保证值历史 769 条、报价历史 193 条，无新增真实任务快照。Issue 包含 1 个待读取商务候选、2 个受保护价格位置及 1 组其它 SSOT 待核验概览；测试占位数据保持标记。
- 119 个既有后端服务/旧路由/前端页面与请求封装文件的 SHA-256 与本轮开始基线一致。新代码之外只修改压缩包路由新增段、前端导航和路由注册。

## 当前运行版本的限制

现有 8100 API 进程的 OpenAPI 尚无新 wizard 路由。本轮未重启前后端服务；新增端点已在隔离 FastAPI 实例验证，正式使用需在正常部署窗口加载新后端代码。前端遇到未加载接口会提供人读提示和带任务深链的高级模式入口。

本轮跳过全量回归，仅做定向验证。人工事实、商务资料、视觉逐页验收与整包总验收仍按原流程完成。
