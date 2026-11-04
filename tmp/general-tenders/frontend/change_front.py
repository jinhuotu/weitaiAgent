from pathlib import Path
import re
root=Path('tmp/general-tenders/frontend')
p=root/'tenders-api.ts';s=p.read_text(encoding='utf-8')
s=re.sub(r"const CHARGER_SCOPE = .*\n",'',s)
a=s.index('const SCOPE_WORDS = [');b=s.index('\n]',a)+2;s=s[:a]+s[b:]
s=s.replace("  chargerRelated?: boolean","  bidderName?: string\n  chargerRelated?: boolean")
s=s.replace("  const pickHits = () => SCOPE_WORDS.filter((w) => wordInBlob(s, w)).slice(0, 3).join('、')\n",'').replace("if (SCOPE_JUNK.some((j) => s.includes(j))) return pickHits()","if (SCOPE_JUNK.some((j) => s.includes(j))) return ''").replace("  if (s.length > 16) return pickHits()\n",'')
s=re.sub(r'^\s*if \(CHARGER_SCOPE.has\(word\).*return true\n','\n',s,flags=re.M)
a=s.index('export function volumeDocx(');b=s.index('\nexport type ApprovalStepDef',a)
s=s[:a]+'''export function volumeDocx(
  row: { docxFile?: string; downloadName?: string; techDocxFile?: string | null;
         techDownloadName?: string | null; projectName?: string; generateVolume?: string },
  volume: BidVolume | 'full',
): { file: string; name: string } {
  if (volume === 'full' || row.generateVolume === 'full') {
    if (volume === 'technical' || (row.generateVolume && row.generateVolume !== 'full')) return { file: '', name: '' }
    return { file: row.docxFile || '', name: row.downloadName || `${row.projectName || 'bid'}-综合标.docx` }
  }
  if (volume === 'technical') {
    return { file: row.techDocxFile || '', name: row.techDownloadName || `${row.projectName || 'bid'}-技术标.docx` }
  }
  return { file: row.docxFile || '', name: row.downloadName || `${row.projectName || 'bid'}-商务标.docx` }
}
'''+s[b:]
s=s.replace('options: { replace?: boolean; invitationId?: string } = {}','options: { replace?: boolean; invitationId?: string; bidderName?: string } = {}',1)
s=s.replace("  if (options.invitationId) form.append('invitationId', options.invitationId)","  if (options.invitationId) form.append('invitationId', options.invitationId)\n  if (options.bidderName) form.append('bidderName', options.bidderName)",1)
p.write_text(s,encoding='utf-8')
p=root/'TendersView.vue';s=p.read_text(encoding='utf-8')
for key in ['bidderName','bidderNature','bidderAddress','bidderPhone','bidderEmail','foundedDate','businessTerm','legalPersonName','legalPersonGender','legalPersonAge','legalPersonTitle','legalPersonIdNo']:
 s=re.sub(rf"({key}:)\s*'[^']*'",rf"\1 ''",s,count=1)
s=s.replace('如：充电桩供货、安装调试及技术服务','填写本次招标文件明确的采购或服务范围').replace('报价单由工程量清单识别；大纲组卷时表头跟招标书，充电桩第五章仍用设备/技术参数列','报价项目和表头以本次招标文件或经核对的清单为准').replace('；充电桩类标仍用设备/技术参数列。','；按本次报价清单的实际表头填写。').replace('直流功率/群充/箱变','产品型号或服务范围').replace('未使用充电桩固定承诺书','未使用固定承诺书')
p.write_text(s,encoding='utf-8')
p=root/'TenderLibraryView.vue';s=p.read_text(encoding='utf-8')
s=s.replace("const DEFAULT_CREDIT_NAME = '河南伟泰光电科技有限公司'","const DEFAULT_CREDIT_NAME = ''\nconst materialCompany = ref('')")
s=s.replace("  const name = creditName.value.trim() || DEFAULT_CREDIT_NAME", "  const name = creditName.value.trim()\n  if (!name) { error.value = '请填写需要查询的公司名称'; return }")
s=s.replace('招标优先采用已竣工充电桩项目。','按本次招标条款核对行业范围、金额、数量与履约状态。')
s=s.replace('uploadTenderSlot(key, files[i], { replace: replace && i === 0 })','uploadTenderSlot(key, files[i], { replace: replace && i === 0, bidderName: materialCompany.value.trim() })')
s=s.replace('恢复默认：{{ DEFAULT_CREDIT_NAME }}','清空查询公司')
marker='    <Panel title="公司常备扫描件"'
s=s.replace(marker,'''    <label class="block text-sm">本次上传资料所属投标人（请核对）
      <input v-model="materialCompany" class="kb-input mt-2 w-full" placeholder="填写扫描件实际所属公司；不填写时需靠OCR或人工事实核验，不能自动用于组卷" />
    </label>
'''+marker,1)
p.write_text(s,encoding='utf-8')
print('Generic frontend defaults, dynamic upload ownership, and explicit full download prepared')
