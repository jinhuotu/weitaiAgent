from pathlib import Path
root=Path(r'E:\code\python\weitaiAgentui');stage=Path(__file__).parent
for source,target in [('TenderPackageView.vue','src/views/TenderPackageView.vue'),('RecognitionPanel.vue','src/components/tender-package/RecognitionPanel.vue')]:
    (root/target).write_bytes((stage/source).read_bytes())
print('Updated only package view and new recognition panel.')
