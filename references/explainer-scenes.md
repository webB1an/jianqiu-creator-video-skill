# 财经、科技和产品解释镜头

在同一组镜头里调度机位、实拍、数字和关系节点时，可以直接使用这些场景。先写清每一步会让观众看懂什么，再按下表选画面。相邻两个场景可以共用一条底片，画框推满、原件落成桌面、关系节点打开证据窗口都能连续演完。见秋色板、宋体中文标题、黑体说明和 Avenir 数值字体用于这套画面。字幕、角标、调色、声音和最终署名走项目的导出流程。

| 画面要交代的事 | 可调用的场景 |
| --- | --- |
| 一个数、一笔算式、前后变化、一组指标 | `number`、`equation`、`change`、`stats` |
| 共同比例、面积反差、组成、排序、逐项比较 | `bars`、`versus`、`share`、`ranking`、`compare` |
| 有逐期实测数据的走势、逐日价格和标记 | `chart`、`line` |
| 关系全貌、实体并列、分支流程、资金循环、因果、时间或地理路线 | `board`、`discs`、`flow`、`network`、`chain`、`timeline`、`map` |
| 当事人与量级同框、人物身份、真实引语 | `person`、`profile`、`quote` |
| 实拍、固定前景的蒙太奇、多窗推满、立体卡片 | `footage`、`montage`、`frames`、`cards3d` |
| 原文、新闻桌面、节点弹窗、标题墙、产品界面 | `article`、`desk`、`popup`、`headlines`、`device` |
| 片名与章节、导航、判断、关键词快闪、自绘镜头 | `title`、`agenda`、`statement`、`flash`、`custom` |

## 回到一个已知数字时，让另一侧给出现场证据

开场已交代的发行价、路程或单价，可以在片尾原值回到同一视线位置。大数场景支持 `recall`，准确值先就位，随后在口播落点画出下划线。右侧可用 `clip` 播放同期实拍或有明确用途的示意视频。原件与使用依据登记在项目素材清单，在 `explainer_scene.clips` 填入源文件、入点和足够的覆盖秒数。例如，已登记的 `meter` 是电表发生读数变化的现场近景，下面这镜把先前的单价与那块电表接起来。

```json
{"scene":"number","value":"0.8 元","brow":"每千瓦时电价","recall":true,"clip":"meter","says":"用前文的单价解释现场测得的用电成本"}
```

`number.clip` 逐帧读取已登记的原片，按 16:9 画布右侧的竖窗裁切。开剪前用该取景实看动作。`from` 以秒计，相对于已登记片段的起点。`roll` 适合有真实起止数值的累计或变化，`recall` 适合回扣已知数字。只有终点总里程的地图可以沿可靠轨迹先行进，再在终点一次给出准确里程。分屏两个区域的证据按[局部动作与原片核对](graphic-voice-revision.md#在编码片中复查)登记。

## 按实际帧数制作

横版工程的一镜填写 `explainer_scene`，其 `visual` 指向本镜专用的生成 MP4。下例先展示一份实测报告，再把报告里的读数带进测量现场。正式使用时换成本片核实的值、原件和真实单位。示例需要 66 帧以上，预计采用 90 帧。将同组的多个视觉场景放在这一镜内，就能在内部切点保留前一场的构图供转场采样。

```json
{
  "id": "energy",
  "visual": "generated/energy.mp4",
  "lines": [],
  "tail": 3,
  "explainer_scene": {
    "scenes": [
      {"scene":"article","says":"从实测报告看清出处","img":"assets/report.png","cssW":1440,"hl":[[170,260,390,48]],"zoom":"F6","hlAt":"F12","until":"F36"},
      {"scene":"number","says":"把报告里的数带进分析","enter":"flat","value":"18.4","brow":"完成同一任务所用电量（Wh）","until":"F66"},
      {"scene":"footage","says":"沿该读数切入测量环境","enter":"through","throughAt":[960,515],"clip":"bench"}
    ],
    "clips":{"bench":{"file":"assets/bench.mov","in":1.2,"seconds":1.3}}
  }
}
```

工程运行 `prepare.py` 后，执行以下命令。默认生成可直接交给 `export.py` 的静音镜头。检查模式会建立场景，读取本片素材并逐帧求值、解码可见图像，输出场景表和提醒。`--moves` 逐帧求出实际调用的转场和运动函数，按场记录首次作用的帧、用意与场景顺序。可按需要指定个别帧精看。生成时在底片旁保存同名 `.scene.json`，记录用到的原件、屏上帧区间、生成器和底片校验值；原件、准备时间线或渲染器改变后重新运行本镜命令，再导出主片。预拆帧和人物前景可按[素材与动作研习](wallstreet-craft.md)准备。Chrome 路径可用 `CREATOR_CHROME` 指定，脚本使用本机 puppeteer-core。
 
```sh
node "$SK/scripts/explainer_scene.cjs" --list
node "$SK/scripts/explainer_scene.cjs" --describe network
node "$SK/scripts/explainer_scene.cjs" --project . --shot energy --check
node "$SK/scripts/explainer_scene.cjs" --project . --shot energy --moves
node "$SK/scripts/explainer_scene.cjs" --project . --shot energy --frame 49 --png energy-f49.png
node "$SK/scripts/explainer_scene.cjs" --project . --shot energy
```

- `F36` 表示本镜准备时间线的第 36 帧。数值时刻按本镜秒数填写，`until` 是下一视觉场景开始的时刻，最后一场默认抵达镜尾。图、坐标、蒙太奇片段使用工程相对路径。图像素材支持 PNG、JPEG、WebP、GIF、SVG；需要确定逐帧运动的媒体传视频或帧序列。源视频的 `in` 与 `seconds` 为源秒，素材窗口应覆盖该 clip 所用的全部动作。分镜板和正式场景读取同一组直立、方像素 sRGB 帧，由各自画框决定铺满或完整显示。可变帧率原片按真实显示时间戳取帧，入点落在两帧之间时沿用当时仍在屏上的那一帧。色彩标签缺失时，可在该 clip 里按已经核实的输入写 `input_color`，字段见[源色彩解释](output-color.md)。`--check` 和底片回执的 `decoded_clips` 列出从零计数的原始显示帧号、整数 PTS、时基、画幅和实际色彩解释。已有序列可登记 `{"dir":"assets/clips/bench","frames":75,"fps":25,"ext":"jpg"}`，从 `0001.jpg` 起。静帧或视频在资产清单登记实际出处。用实拍做来源证据时，以原始视频核对色彩与细节。
- 复用实读时钟时，当前镜 `timeline.beats` 的节目时码会换算成本镜秒数。写 `@编号`、`@编号:词`、`@编号:词#2` 或 `@编号:END`；也可在 `explainer_scene.beats` 显式提供本镜时间 `{id,text,start,end,chars:[[字,本镜秒],…]}`。一个切点的数字和声音取本片时钟。给原文留下阅读时间后才收成下一镜的背景。
- `enter` 支持 `cut`、`flat`、`through`、`blur`、`push`、`push-right`、`whip`、`whip-left` 和 `fade`。已有舞台时，`flat` 能让上一场的画面倾倒成桌面，再接 `number`、`chart`、`change` 等；`throughAt:[x,y]` 用 1920×1080 基准像素指定推穿对象。需要从一个节点拉出原件再回到原处，可在 `network.steps` 写 `pop`，或在下一场 `popup` 写 `from`、`to`、`back`。选哪一扇实拍窗接满屏，就在 `frames.fill` 写 `i` 与 `at`。
- `network` 用 `nodes` 放带 id、名字、颜色的实体，用 `links` 写 `from/to`。用 `steps` 在关键帧依次 `focus` 节点、`flow` 连线或 `loop` 一圈，从而讲明方向。`map` 也用 `nodes`、`links`、`steps`，节点给实际经纬度，地图使用 Natural Earth 等距圆柱陆地示意底图。单个场景所接受的字段、必填项和可用子结构可用上方 `--describe 场景名` 查询。
- 凡画长度、面积、占比和差值，先核对分母、单位及币种。`chart` 和 `line` 写真实逐期观测，`line` 横向间距表示逐条观测次序。只有累计值时用 `number`。引用原句优先用真实截图；使用核实过的文字重排时，`article.mock` 会在页上标作原文摘录，保留发布者、日期和原始链接。标题墙每项给真实来源。
- 需要自绘时，把项目浏览器脚本放进 `explainer_scene.drawing`，例如 `"drawing":"art/process.js"`。脚本用 `window.sceneDrawings={part(t,c){…}}` 注册帧绘制函数，场景写 `{"scene":"custom","render":"part","says":"本镜的发现"}`。可直接使用 `KIT` 的布局和实测值、`S25` 的前后景/景深、`MV` 的移动/弹窗函数。对同一 t 应返回相同的 HTML。凡从文献、网页或采访带进来的文字都作为文字绘制，制作方自己的代码单独保存在工程。

## 从一条关系打开证据，再回到关系图

下面是一条可放入 `scenes` 的示例。先让读者在原有位置认出订单与排产，再沿连线打开该订单的记录。弹窗收回后才拉远。换成真实案例时，节点名称、连线含义和原件要来自同一次业务过程；读完一张原件的时间按正文长度调整。

```json
{
  "scene":"network","says":"订单通过同一个工单号进入排产",
  "nodes":[{"id":"order","name":"客户订单"},{"id":"plan","name":"排产记录"}],
  "links":[{"from":"order","to":"plan","text":"工单号"}],
  "steps":[
    {"at":"F0","focus":"order"},
    {"at":"F14","flow":"order>plan","focus":["order","plan"]},
    {"at":"F38","pop":{"from":"order>plan","img":"assets/order-record.png","to":[320,105,1280,720],"back":"F77"}},
    {"at":"F88","focus":"all"}
  ]
}
```

`focus` 同时取景、压低其余节点，`light` 只换明暗。`link` 画线，`flow` 让流向沿线持续可见，`loop` 接受按真实顺序排列的连线 id。`mark:{"plan":"ok"}` 可标记已核实的状态；需要解释收购、包含时，用 `nest:["子对象","外对象"]` 让两个实体闭合。同组关系多次回看时保留节点 id、位置、名称与色义。地图的 `steps` 可同样沿真实方向跨点飞行。`--check` 会分别统计主持人出现、主持房间占据主画面、有证据素材的场景数，可据此复看论点最需要原件的部分。

场景与视觉运动库基于 MIT 许可的 wallstreet-explainer 对应版本，按见秋的实际镜头时间线、品牌和数据用法改编。版权说明随渲染库保存。[上游场景说明](https://github.com/HeyChengC/wallstreet-explainer/tree/f39845abd45dab3c9ce62587aa5d02f6afe82612/kit) · [本地许可](../assets/explainer-kit/LICENSE) · [地图与资源出处](../assets/explainer-kit/NOTICE)
