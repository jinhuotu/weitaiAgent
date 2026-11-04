# 模块五：独立正式接入

本轮跳过全量回归，仅做主链路验证。接入代码已落地；不标记整包可投标，不推进整包总验收。

## 正式入口与权限

前端路由 `/tender-package`，侧栏“压缩包标书”。沿用现有 `/tenders` 菜单权限、登录态、
apiRequest/apiDownload 与统一错误提示。原 TendersView、原 tenders-api 和工程类生成路由未改。
后端只在应用装配处新增 include_router；既有49个工程类路径的 OpenAPI 请求/响应定义对比一致。

所有任务位于现有 storage_root/tender-packages/登录用户ID/任务UUID。
任务、来源上传、版本、产物、人工历史均按用户隔离；管理员也不能用其他用户的任务ID读取资料。
每次确认以服务端当前登录账号为 actor，客户端不能冒用其他确认人。旧交付记录的原确认人完整保留。
资料库读取沿用 require_tender_lib_read，后续生成/商务选择再次检查资料库读取权限。

## API（相对于现有 api_prefix，默认 /api/v1）

- GET /tender-package：当前用户任务列表。
- POST /tender-package/upload：multipart file，接收 ZIP/RAR/7z，记录哈希；普通文档仍用原入口。
- GET /tender-package/{id}：阶段、版本、清单、角色、SSOT、核验信息、warnings、产物。
  files 的 logical_path/source_chain/depth 为递归目录树依据，保留重复标注与哈希。
- POST /tender-package/{id}/sources：multipart revision/file，登记产品、映射或报价的真实文件。
- POST /tender-package/{id}/actions/{operation}：JSON revision/arguments，统一乐观并发校验。
  operation 为 unpack、classify、role、material、mapping、ssot、field、source、library、review、generate。
  密码仅用于当次解包，不进入历史。role/material/mapping 在SSOT汇聚后冻结；字段补录生成新SSOT版本。
  review 仅转交现有 review_rows/packages/price/source_price/business 接口，不自动填写任何业务结论。
- GET /tender-package/{id}/review-page：现有核验组件模板；正式 Vue 页面通过受控消息桥和统一登录请求
  访问上述 API，不启动 localhost 临时 HTTP 服务。核验区为无同源权限的沙箱，不接触访问令牌。
- GET /tender-package/{id}/artifacts/{index}：校验任务所有权、受控目录与 SHA-256 后下载。
- POST /tender-package/import-prepared/{token}：仅管理员可导入服务器准备的既有交付成果。
  必须填写 arguments.basis，采用当前登录用户作为任务所有者；不接受客户端提供服务器路径。

普通API参数不接受绝对来源路径，source 必须使用本任务上传产生的 upload_id。
ZIP解包、格式适配、分类、物料名称提取/补齐/合成、SSOT、货物生成、真实贴图、到期 warnings
均调用原模块。RAR仍依赖本地可选解码，7z仍提示转换ZIP，不宣称安全扩展验收通过。
失败保留上次有效结果并显示重试提示；并发/旧版本提交拒绝覆盖。服务在运行中中断且遗留 busy.lock
时，提示管理员核验任务是否仍在处理后恢复，不自行抢锁并发写入。

## 前端文件

- src/views/TenderPackageView.vue：独立上传、任务、目录、角色/物料、SSOT、核验和产物页面。
- src/components/tender-package/PackageTree.vue：递归展示逻辑路径、嵌套容器、深度、重复项与哈希。
- src/lib/tender-package-api.ts：复用登录令牌、请求/下载封装。
- src/router/index.ts、src/config/nav.ts：只新增独立路由与导航，继承标书菜单权限。

## 模块四旧成果迁移

管理员在服务器执行 handoff.prepare 或以下命令，得到只含随机UUID的预备包ID：

```powershell
$env:PYTHONPATH = 'apps;packages'
.venv/Scripts/python -m api.services.tender_package.handoff --storage storage --manifest 清单.json --root 解包目录 --ssot SSOT.json --inputs 最新正式接口会话.json --result 草稿结果.json
```

命令校验全部来源及产物哈希，复制真实文件和供应商/报价依据，保留原始会话作为不可变审计依据，
不修改原始SSOT、模块二映射或文档内容。管理员在正式页面填入ID及依据后，导入到自己的账号。
未确认项保持未确认，旧视觉核验记录保留为历史，正式任务显示视觉未验收；随后读取资料库真实候选。
本次已准备的包ID：85134f2a006a4bf78eec3c538ea3624e。
未擅自选择生产账号，尚未将此包导入任何真实用户。

## 定向验证与启用限制

- 4项隔离API检查：上传/嵌套树/角色纠正/SSOT/人工回填/生成/下载；跨用户拒绝；恶意路径；
  旧版本拒绝覆盖；原工程类路由隔离；迁移所有权与哈希校验。
- 浏览器运行实际Vue页面，通过真实本机代理连接隔离API；只有登录信息采用测试身份。
  已检查跳转、上传、嵌套树、纠正、核验消息桥、回填、生成、下载及刷新持久化，页面无JS异常。
  样本为合成测试数据，未代用户确认真实532项；外部渲染器在测试中明确禁用，验证视觉未验收状态。
- 新增组件独立vue-tsc检查通过。整体vue-tsc仍报告原TendersView的10项未使用声明；本轮未修改这些代码。
- 真实旧数据迁移至隔离账号验证：29文件、237自动通过、532待核验、50批准报价，标包仍未确认。
- 未重启现有后端/前端。Windows后端无热加载，因此现运行8100进程尚未加载新路由；
  当前验证来自隔离应用，不能据此宣称生产服务已上线。需后续按用户允许的部署窗口加载新代码，
  再使用真实登录态做正式服务联通复核。本轮不执行该重启。

仍待人工：型号冲突、532项响应、实际标包/报价位置、商务原表及真实资料选择。
全部人工项完成后，仍须另行取得用户同意补跑全量回归，并验收新生成整包的视觉、容量及格式。
