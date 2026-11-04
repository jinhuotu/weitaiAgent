# 角色与物料识别展示

本轮跳过全量回归，仅做定向验证。不修改 unpack、SSOT 实现、工程类入口或其他正式页面。

## 调用与口径

正式接口仍为 POST /api/v1/tender-package/{id}/actions/classify，调用 recognition.recognize，
该函数仅编排已有 classify → composed → completion，保留人工来源账本与合成历史。
manifest 为不可变原始解包清单，原 pending 在 classified.files 中更新为识别角色，前端优先显示后者。
对未知/冲突角色保留 other，所有原文件、压缩容器和签名均保留。

共享 classify 增加通用 commitment 角色：诚信/廉洁/合规等承诺或声明。
它与 integrity（签名/完整性校验）分开；CLI、独立页面和正式API共用同一分类器，不另建文件名清单。

GET /api/v1/tender-package/{id} 额外提供 recognition，包含 role_labels、role_counts、
id_count、entries、pending_ids、ssot_contract、ssot_ready。
每个 entry 包含固化ID、全部关联文件与哈希、当前名称/source_type、needs_confirmation、
selected_sources、全部 sources、composition_history 和历史差异标志。
这只是既有解析账本的展示投影，不推断新名称或将历史差异静默消除。

## 人工纠正与导出

- role：原参数 storage_path/role/basis。只接受本任务文件，记录当前登录人及时间/依据。
- material：solidified_id/name/conductor/basis，可选 model。填写型号时复用 confirm_material 的
  型号/材质一致性校验，记录 confirmed_parameters；所有原始参数和历史均保留。
- material_retire：record_id/basis。显式停用错误来源，复用 retire_source，保留原值及 corrections。
  不会自动删除前一条人工记录。多条有效人工名称不一致时仍待确认，须由人明确停用错误来源。
- material_export：调用现有 build_final_mapping，验证文件哈希、名称唯一性及来源依据，
  输出原模块二 tender-package-material-map/1 完整契约，供原 build_ssot 读取。
  缺少可靠名称时拒绝最终导出，仍可查看/纠正其他已识别项。

本步未实现或运行标书生成，也未修改 SSOT 汇聚算法。

## 页面

新增 RecognitionPanel.vue，仅用于 TenderPackageView.vue。显示中文角色、文件/ID/物料映射、
mapping/manual/composed/extracted 来源、原始参数行号、人工记录及全部证据。
自动参数与已有人工/映射依据并列显示，裁决字段全部留空，不预选型号/导体/名称。
角色纠正下拉也从“选择纠正角色”开始，不将识别结果当成人工提交。

## 定向核验结果

真实样本29文件/容器，10个固化ID全部提取，与直接CLI调用结果一致：
10技术规范、3合同、1正文/须知、1邀请书、1完整性签名、13其他/容器。
承诺角色使用独立通用样本验证；真实样本没有被强行标为该角色的文件。
9个名称为composed，9985-500108211-00001仍因原YJLV22与铜导体字段矛盾保持待确认，不伪造名称。
完整逐ID名称与来源输出位于 tmp/recognition-real-results.json。

20项定向测试覆盖共享分类器、正式API、型号/导体人工裁决、旧来源停用/历史、SSOT输入导出、
文件哈希和权限。新增组件独立类型检查通过。
浏览器以隔离合成样本验证解包→识别→角色纠正→人工裁决→导出→刷新持久化；未提交真实业务裁决，
未调用生成器，页面无JS异常。原AI标书页面/API文件哈希保持不变。
未重启现有前后端；页面与API的新代码需要由运行进程正常加载后生效，隔离验证不能替代正式部署检查。
