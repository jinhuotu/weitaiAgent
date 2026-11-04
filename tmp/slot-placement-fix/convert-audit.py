from pathlib import Path
import subprocess
root=Path('tmp/slot-placement-fix/audit').resolve()
remote='/tmp/material-consolidation-audit'
for path in sorted(root.glob('*.docx')):
 xml=root/(path.stem+'.xml')
 xml.write_text('<TaskQueueDataConvert><m_sFileFrom>'+remote+'/'+path.name+'</m_sFileFrom><m_sFileTo>'+remote+'/'+path.stem+'.pdf</m_sFileTo><m_nFormatTo>513</m_nFormatTo></TaskQueueDataConvert>',encoding='utf-8')
 for command in [['docker','cp',str(path),'weitai-onlyoffice:'+remote+'/'+path.name],['docker','cp',str(xml),'weitai-onlyoffice:'+remote+'/'+xml.name],['docker','exec','weitai-onlyoffice','/var/www/onlyoffice/documentserver/server/FileConverter/bin/x2t',remote+'/'+xml.name],['docker','cp','weitai-onlyoffice:'+remote+'/'+path.stem+'.pdf',str(root/(path.stem+'.pdf'))]]:
  subprocess.run(command,check=True,capture_output=True)
 print('converted '+path.name,flush=True)
