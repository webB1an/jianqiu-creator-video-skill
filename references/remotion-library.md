# 本地 Remotion 动效库

[打开离线预览网页](../assets/remotion-library/index.html)。搜索动作或用途，点模板观看，暂停后拖动时间轴或用“上一帧”“下一帧”检查动作。编辑参数并点“应用预览”，随后复制或下载完整 JSON。画幅可切横屏与竖屏，有媒体输入的模板可选择自己的本地图片或视频预览。下载配置只记录文件名，把素材放在配置旁，或改成工程里的相对路径。网页和制片工具使用同一份模板组件。网页可直接在浏览器打开，所需脚本、示例图形均随 skill 保存。

## 按镜头用途选择

| 镜头要让观众看清什么 | 可选模板 | 剪辑时看哪一帧 |
| --- | --- | --- |
| 数量、差异、趋势、构成或步骤 | 数字滚动、同尺度横条、实测折线、占比环、步骤交接 | 让关键词落在变化或落定处，留出读数和单位的时间。 |
| 谁在说、原话哪些字有分量 | 人物身份条、原句分段划亮、引语与署名 | 身份条让开人物和主字幕，着重色跟随实际被读到的短语。 |
| 观众该看原件哪一处 | 定点推近、同位对比滑尺、全屏缩入画中画、前后景视差、局部聚光、双照片叠放 | 对比图先对齐同一部位。聚光按原图坐标移动，双照片各保留阅读时间，视差前景使用透明层。 |
| 上一镜如何交给下一镜 | 横推、擦除、叠化、圆窗、时钟扫入、翻面、推近切换、双源分屏取景 | 确认两侧素材各自的入点和重叠段，重点动作保留完整。 |
| 如何进入一章、如何收住结尾 | 章节标题、渐变、网格脉冲、宽银幕开幅、标志描边、结尾回扣、片尾署名 | 主标题出现时让背景减速，结尾完整停留到能读完。 |

模板分类参考 React Video Editor 的公开目录。各条目的 `reference` 指向具体示例，`implementation` 记录本机实际调用。八种双景切换调用已安装的官方 `@remotion/transitions`，双源分屏复用本 skill 的 `EvidenceCut`。图表、文字和包装保留可编辑 JSX。示例数字、表盘和署名用于演示，上片时换成本期内容。[React Video Editor 参考目录](https://www.reactvideoeditor.com/remotion-templates) · [官方转场](https://www.remotion.dev/docs/transitions/)

双图档案与转场预览使用阿波罗 11 号的两张原始档案照片，一张是 Neil Armstrong 拍摄的 Buzz Aldrin，另一张是 Aldrin 拍摄的月壤靴印。每个采用它们的示例都标明摄影来源。换成本期原件时，按本期来源登记。[NASA 人物照片](https://science.nasa.gov/resource/apollo-11-buzz-aldrin/) · [NASA 靴印照片](https://science.nasa.gov/resource/apollo-11-bootprint/)

## 查找与单镜渲染

在本期工程目录运行。也可以把网页下载的 JSON 放在这里，直接从渲染命令开始。自己的素材路径相对配置文件，`@library/` 开头的路径指向库内示例。

```sh
SK="$HOME/.codex/skills/creator-video"
PY="$HOME/.cache/creator-video-env/bin/python"
"$PY" "$SK/scripts/remotion_library.py" list 转场
"$PY" "$SK/scripts/remotion_library.py" show line-chart
"$PY" "$SK/scripts/remotion_library.py" example wipe > wipe.json
"$PY" "$SK/scripts/remotion_library.py" render wipe.json --out wipe-mid.png --frame 44
"$PY" "$SK/scripts/remotion_library.py" render wipe.json --out wipe.mp4
```

独立配置的 `width`、`height`、`fps`、`frames` 是画幅和本镜时基。镜内从第 0 帧计时，最后一帧是 `frames - 1`。视频素材的 `sourceStart`、`outStart` 等入点也用本镜帧率表达。人物身份条可在配置顶层填 `"background":"assets/interview.mp4"`。网格、描边或署名叠在实拍上时，同时设 `params.transparent` 为 `true`。MP4 输出已合成底片，声音、主字幕在全片导出时统一制作。

## 接入本期分镜

在现有 `edit.json` 的对应镜头增加 `remotion_effect`，给 `visual` 指定该镜独有的 MP4 路径。下面是一条镜头的完整示例。引用录音时把 `audio` 换成本期已有文件。

```json
{
  "id": "receipt",
  "visual": "visuals/receipt.mp4",
  "lines": [{"id": "total", "text": "三笔报销合计四百二十元。", "audio": "assets/total.wav"}],
  "remotion_effect": {
    "effect": "stat-counter",
    "params": {"title": "三笔报销合计", "from": 0, "to": 420, "unit": "元"},
    "cues": {"settleFrame": "@total:四百二十"}
  }
}
```

镜头继承工程画幅和实际配音排出的帧数。网页 JSON 中的 `effect`、`params` 放入 `remotion_effect` 即可。正式素材路径相对本期工程。需要卡词时，在 `cues` 指定帧参数和台词锚点。例如 `@total:合计#2` 取第二次出现的“合计”，`@total` 取这句起音。准备时间轴时会对带锚点的镜头做字音对齐，也可沿用本期审定的 `word_times`。

```sh
"$PY" "$SK/scripts/prepare.py"
"$PY" "$SK/scripts/remotion_library.py" render --project . --shot receipt
"$PY" "$SK/scripts/export.py"
```

同一镜有多个信息层时，可以把词锚直接给到某一层。例如原句分段划亮的 `params.segments` 已有两段，可在 `remotion_effect.cues` 写 `{"segments[0].at":"@quote:原价","segments[0].until":"@quote:实付","segments[1].at":"@quote:实付"}`。观众听到“实付”时视线才从原价交给实付，第二处停稳供阅读。局部聚光可以给多处证据设置独立的抵达词和停读词，完整的 `stops` 配置见[静态原件的逐词取景示例](picture-edit.md#让静态素材跟得上旁白)。人物身份条可把 `startFrame` 和 `endFrame` 绑定到介绍与让出画面的词。结束帧为首个隐藏帧。换录音后重新 prepare 并渲染该镜，按实读落点核对交接。

### 把身份条排在证据读完后的空档

当人物介绍可以稍晚出现时，先把原件上要保护的读数或出处按出现时段登记在镜头的 `caption_avoid`。在同一镜的 `remotion_effect` 增加 `placement`。用预览确认身份条从滑入到停稳占用的总范围，将这个框填入 `box`，四个数依次是画面宽高归一化的左、上、右、下边界。姓名、职务、字号或取景变动后重新核对这个框。实拍底片填在 `remotion_effect.background`，保持 `params.background` 透明，读数在身份条让开时仍能看到。

```json
{
  "id":"engineer","visual":"visuals/engineer.mp4",
  "caption_avoid":[{"label":"压力与单位","frames":[12,42],"box":[0.10,0.61,0.30,0.72]}],
  "remotion_effect":{
    "effect":"lower-third","background":"assets/interview.mp4",
    "params":{"name":"陈川","role":"值班工程师","startFrame":8,"endFrame":96},
    "placement":{"duration_frames":36,"box":[0.035,0.57,0.36,0.77]}
  }
}
```

启用自动避让时，`startFrame`、`endFrame` 给出允许出场的镜内窗口，也可用上文的实读词锚来定。省略两端时取整镜。`duration_frames` 是完整连续显示时长，包含入场动画。上例要求在 [8,96) 中找到 36 帧，F12–41 同处要读仪表，所以身份条排在 [42,78)。画面其他位置的保护区可以同时展示。窗口不足会报出涉及时段和对象，由剪辑调整可用窗口、显示时长或构图。

实际占位写入镜头收据的 `placement.frames` 和 `placement.box`。横版主字幕依选定时段与此框逐句避让。竖版另按重构后的实际位置在竖版计划登记，带定位样式的外部字幕和手动指定的字幕位置延续各自排版。独立网页预览可把收据中的两端填回 `params.startFrame`、`params.endFrame` 逐帧看交接。

正式镜头旁生成 `<visual>.remotion.json`，记录使用的原件、时间区间、模板版本信息和画面摘要。原件变动后重渲对应镜头；分镜或实读时间轴变动后，重新准备并重渲受收据绑定影响的模板镜头。全片导出会检查当前画面与收据，接入原有调色、配音、字幕与署名流程。

## 在本期工程调整组件

用 `stage` 将组件、配置和素材复制到一个空的镜头工程。里面的 `package.json` 固定本地使用的依赖版本。有独立依赖时运行 `npm install`、`npm run studio`；在本机渲染环境下创建子目录，也可直接调用父工程的 Remotion CLI。

```sh
"$PY" "$SK/scripts/remotion_library.py" stage wipe.json --project ./wipe-studio
```

本机渲染环境默认位于 `~/.cache/creator-video-motion/remotion`，可用 `CREATOR_REMOTION` 选择其他安装目录，`CREATOR_CHROME` 可指定本地 Chrome。新环境的依赖版本见[运行清单](../assets/remotion-library/runtime-package.json)。

## 把可复用动效加回本地库

换掉本期素材、文案、数值和卡点后，仍能解决另一镜同类问题的动效，在本期镜头最终渲染前回库。这个判断同样适用于引入的 Remotion 组件、多个现成组件组成的镜头和新写的动作。仅换配色、文案或入场帧的镜头，继续沿用原模板 ID 和本期参数。同组目录和组件源码都绑定在镜头收据里，后续改库时按导出提示重渲受影响的镜头。

1. **整理组件和示例。** 把通用动作以命名导出接入 `assets/remotion-library/<组>/effects.jsx`。把会随选题变化的输入做成参数，示例使用可随库分发的素材和相对路径。组合保留原组件的调用，让网页和制片共用同一份 JSX。
2. **登记到可检索目录。** 在同组 `catalog.json` 写入唯一 ID、中文名称与用途、检索词、命名导出、参数说明和可运行的示例，注明参考来源和实际复用的组件。媒体参数列入 `media`。跨文件组合时，把该组导入所需的本地 JSX（包括间接依赖）列入 `scripts/remotion_library.py` 的 `engine_files`，让镜头工程和渲染收据都带上依赖。新增 npm 包时同步本机运行环境和运行清单。素材若只在部分帧露出，在 `scripts/remotion_source_usage.py` 登记入点与可见区间。
3. **重建并实际预览。** 用新 ID 运行 `example` 取得示例配置，再按上文的 `render --frame` 命令渲染一个能看出动作的帧。运行 `node "$SK/scripts/build_remotion_gallery.cjs"`，直接打开[本地预览网页](../assets/remotion-library/index.html)，用中文用途搜到新卡，播放并逐帧检查动作交接和落稳。切换横竖屏，改一项参数并应用，确认预览随之变化。将重建的网页资源随模板一起保存在 skill 中，本期分镜即可用同一 ID 渲染。
