from pathlib import Path
stage=Path(__file__).parent
p=stage/'TenderPackageView.vue';t=p.read_text(encoding='utf-8-sig')
t=t.replace("import PackageTree, { type TreeNode } from '@/components/tender-package/PackageTree.vue'","import PackageTree, { type TreeNode } from '@/components/tender-package/PackageTree.vue'\nimport RecognitionPanel from '@/components/tender-package/RecognitionPanel.vue'")
t=t.replace("const material=ref({solidified_id:'',name:'',conductor:''})\n",'').replace("const roles=['invitation','notice','tech_spec','contract','quotation','integrity','other']\n",'')
start=t.index('<section v-if="task.classified"><h2>角色与物料核验')
end=t.index('<section v-if="task.classified"><h2>真实来源上传',start)
t=t[:start]+'''<RecognitionPanel v-if="task.classified&&task.recognition" :classified="task.classified" :recognition="task.recognition" :files="files" :busy="busy" :frozen="!!task.ssot" @role="args=>guard(()=>action('role',args))" @material="args=>guard(()=>action('material',args))" @export="guard(()=>action('material_export'))" />
<p v-else-if="task.classified">当前后端尚未提供识别结果展示契约，请在允许的部署窗口加载新后端代码。</p>
<section v-if="task.classified"><button :disabled="busy||!!task.ssot" @click="guard(()=>action('ssot'))">汇聚 SSOT</button><details v-if="task.mapping"><summary>供SSOT读取的最终映射（原模块二契约）</summary><pre>{{JSON.stringify(task.mapping,null,2)}}</pre></details></section>
'''+t[end:]
t=t.replace('本轮跳过全量回归，仅做主链路验证。','本轮跳过全量回归，仅做定向验证。')
t=t.replace('<h2>真实来源上传</h2>','<h2>真实来源上传</h2><input v-model="basis" placeholder="本次来源批准/补录依据">')
p.write_text(t,encoding='utf-8')
