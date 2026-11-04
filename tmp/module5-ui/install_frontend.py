from pathlib import Path
import hashlib,json
root=Path(r'E:\code\python\weitaiAgentui');stage=Path(__file__).parent
files=['src/views/TendersView.vue','src/lib/tenders-api.ts']
hashes={f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in files}
for name,dest in [('tender-package-api.ts','src/lib/tender-package-api.ts'),('PackageTree.vue','src/components/tender-package/PackageTree.vue'),('TenderPackageView.vue','src/views/TenderPackageView.vue')]:
    target=root/dest;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((stage/name).read_bytes())
router=root/'src/router/index.ts';text=router.read_text(encoding='utf-8')
if "'/tender-package':" not in text:text=text.replace("  '/tenders': () => import('@/views/TendersView.vue'),","  '/tenders': () => import('@/views/TendersView.vue'),\n  '/tender-package': () => import('@/views/TenderPackageView.vue'),")
router.write_text(text,encoding='utf-8')
nav=root/'src/config/nav.ts';text=nav.read_text(encoding='utf-8')
if "if (it.href === '/tender-package'" not in text:text=text.replace('  if (menus.includes(it.href)) return true',"  if (it.href === '/tender-package' && menus.includes('/tenders')) return true\n  if (menus.includes(it.href)) return true")
if "href: '/tender-package'" not in text:text=text.replace("      { href: '/tenders', label: 'AI标书生成', icon: FileText },","      { href: '/tenders', label: 'AI标书生成', icon: FileText },\n      { href: '/tender-package', label: '压缩包标书', icon: FolderOpen },")
nav.write_text(text,encoding='utf-8')
assert all(hashlib.sha256((root/f).read_bytes()).hexdigest()==h for f,h in hashes.items())
(stage/'legacy-hashes.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
print('Installed isolated frontend page/API/tree and added route/navigation. Legacy view/API unchanged.')
