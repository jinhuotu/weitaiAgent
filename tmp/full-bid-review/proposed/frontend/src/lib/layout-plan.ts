/** 充电站平面布置：与后端 layout_out 共用的 LLM 提示词。 */

export const LAYOUT_PLAN_KIND = 'ev_charging_station_plan'

export const LAYOUT_EXAMPLE_PLAN = {
  schemaVersion: '1',
  kind: LAYOUT_PLAN_KIND,
  titleBlock: { title: '充电站平面布置图', sheetNo: '002', project: '示范站' },
  site: {
    widthM: 92,
    heightM: 52,
    northDeg: 0,
    gate: { side: 'south', offsetM: 38, widthM: 10, label: '出入口', roadLabel: 'S304省道' },
  },
  buildings: [
    {
      id: 'factory',
      label: '厂房',
      kind: 'factory',
      rect: { x: 62, y: 38, w: 28, h: 12 },
    },
  ],
  parkingRows: [
    {
      id: 'north',
      stalls: 7,
      stallWidthM: 2.6,
      stallLengthM: 6.0,
      angleDeg: -45,
      origin: { x: 22, y: 30 },
      along: 'x',
      charger: { type: 'dc_320kw', startNo: 13, side: 'head' },
      labelPrefix: '直流充电桩',
    },
    {
      id: 'south',
      stalls: 15,
      stallWidthM: 2.6,
      stallLengthM: 6.0,
      angleDeg: -45,
      origin: { x: 8, y: 12 },
      along: 'x',
      charger: { type: 'dc_320kw', startNo: 1, side: 'head' },
      labelPrefix: '直流充电桩',
    },
  ],
  equipment: [
    {
      id: 'tx1',
      type: 'ring_box_transformer',
      x: 72,
      y: 34,
      label: '新建1#环网型箱变',
      capacityKva: 1250,
    },
  ],
  trenches: [
    {
      id: 'tr1',
      widthMm: 1200,
      heightMm: 1100,
      lengthM: 70,
      polyline: [
        { x: 18, y: 22 },
        { x: 78, y: 22 },
      ],
      label: '电缆沟 1200(W)x1100(H)',
    },
  ],
  cables: [],
  trees: [
    { x: 6, y: 26 },
    { x: 10, y: 40 },
  ],
  greenery: [],
  roads: [
    {
      polygon: [
        { x: 18, y: 18 },
        { x: 60, y: 18 },
        { x: 60, y: 28 },
        { x: 18, y: 28 },
      ],
      label: '内部道路',
    },
  ],
  legend: ['ring_cabinet', 'box_transformer', 'dc_320kw', 'parking', 'greenery', 'tree'],
  notes: [],
}

export const LAYOUT_LLM_SYSTEM_PROMPT = `你是充电站平面布置助手。根据用户需求、读图结果和知识库片段，输出一张可被程序绘图的平面布置 JSON。

【硬性要求】
1. 只输出一个 JSON 对象，不要 Markdown 解释、不要代码围栏。
2. 必须符合：schemaVersion="1"，kind="ev_charging_station_plan"。
3. 坐标系：场地西南角为原点，X 向右（东），Y 向上（北），单位米。
4. 车位用 parkingRows 整排生成（stalls + origin + angleDeg），禁止逐个枚举车位坐标。
5. 数值要自洽：车位与建筑不要明显重叠；必须写 site.gate（出入口，落在场地边上）；电缆/电缆沟须穿过充电桩头部，再接到最近箱变，禁止在场地中间画一条不挨桩的 U 形线。
6. 用户没给的尺寸用行业常规：轿车直流桩车位 stallWidthM≈2.6、stallLengthM≈6.0，斜列约 -45°、电缆沟 1200×1100mm。
7. 读图已给出建筑轮廓、出入口、桩数时优先采用读图数字，不要编造图中没有的容量。
8. 若上下文含【知识库检索结果】或参考片段：那是历史布置案例，只借鉴布置模式、车位斜角、沟截面、图例和相近规模；桩数、箱变容量、场地边界以当前用户需求和读图为准，禁止整案照抄。
9. 知识库未命中时不要假装引用了历史图纸。
10. charger.type 只能是 dc_320kw / dc_160kw / dc_120kw / ac_14kw / none（120kW 直流用 dc_120kw，不要改成 dc_160kw）。
11. legend 只能从 ring_cabinet、box_transformer、dc_320kw、dc_160kw、dc_120kw、ac_14kw、parking、greenery、tree 中选；电缆沟写在 trenches，不要放进 legend。
12. stallWidthM 是车位短边，stallLengthM 是停车深度。图面「4m×17m」常含通道，轿车不要把 17 填进 stallLengthM（用 6 左右）；货车/大车位才用 12～17。不要填 pitchM。
13. along=x 表示整排平行于东西向道路（原点只沿 X 递增，Y 相同）；along=y 只沿 Y 递增。angleDeg 只旋转单台车位，不要为了斜停车把整排画成斜线。
14. site.gate 必填：side、offsetM、widthM、label 用「出入口」。程序会在围墙上开口，不要漏掉该字段。
15. 车位必须全部落在场地内，不要压厂区围墙；贴内部车行道布置，桩头朝车道、电缆沟走桩头。斜列若会越界，宁可垂直停车。程序会重新装箱，origin 只需大致合理。
16. 【知识库检索结果】是完整历史案例。只借鉴布置模式；若消息里附有历史图纸，只看模式，禁止描图、禁止照抄场地边界。桩数与容量以当前用户需求为准。

【字段】
- titleBlock: title / sheetNo / project
- site: widthM, heightM, northDeg, gate{side, offsetM, widthM, label, roadLabel}
- buildings[]: id, label, kind=building|factory|carport|demolish, rect{x,y,w,h,angleDeg}
- parkingRows[]: id, stalls, stallWidthM, stallLengthM, angleDeg, origin{x,y}, along=x|y, pitchM, charger{type=dc_320kw|dc_160kw|dc_120kw|ac_14kw|none, startNo, side=head|tail}, labelPrefix
- equipment[]: id, type=ring_cabinet|box_transformer|ring_box_transformer|lv_cabinet|hv_meter|dc_320kw|dc_160kw|dc_120kw|ac_14kw, x, y, label, capacityKva
- trenches[]: id, polyline[{x,y}...], widthMm, heightMm, lengthM, label
- cables[]: id, voltage=10kv|0.4kv, polyline, label
- trees[]: {x,y}
- greenery[] / roads[]: {polygon:[{x,y}...], label}
- legend: 图例符号列表
- notes: 短注记

【示例（结构照此，数值按用户需求改）】
${JSON.stringify(LAYOUT_EXAMPLE_PLAN, null, 2)}`
