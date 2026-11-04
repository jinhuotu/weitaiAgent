import fs from 'node:fs/promises';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const run=(await fs.readFile('tmp/current-offer-test-run.txt','utf8')).trim();
const plan=JSON.parse(await fs.readFile(run+'/excel-test-plan.json','utf8'));
const workbook=await SpreadsheetFile.importXlsx(await FileBlob.load(plan.source));
const before=await workbook.render({sheetName:plan.sheets[0].name,range:'A1:F10',scale:1.3});
await fs.writeFile(run+'/excel-before.png',new Uint8Array(await before.arrayBuffer()));
for(const entry of plan.sheets)workbook.worksheets.getItem(entry.name).getRange(entry.range).values=entry.values;
workbook.recalculate();
for(const entry of plan.sheets){
  const range=workbook.worksheets.getItem(entry.name).getRange(entry.range).values;
  if(range.length!==entry.values.length||range.some((r,i)=>r[0]!==entry.values[i][0]||r[1]!==entry.values[i][1]))throw Error('Excel written values mismatch '+entry.name);
  const png=await workbook.render({sheetName:entry.name,range:'A1:F10',scale:1.3});
  await fs.writeFile(run+'/excel-'+entry.name+'.png',new Uint8Array(await png.arrayBuffer()));
}
await (await SpreadsheetFile.exportXlsx(workbook)).save(plan.output);
console.log(JSON.stringify({output:plan.output,filled:plan.matches.length,sheets:plan.sheets.length}));
