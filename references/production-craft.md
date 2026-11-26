# 画面、转场、调色、声音、字幕与节奏协同参考

本页集中说明已接入默认制片管线（`prepare.py`、`export.py`、`render_window.py`、`portrait_cut.py`、`review.py`）的进阶能力与选用原则。一切修饰服务于观众在正常速度和手机宽度下的理解与观感，避免为了炫技而干扰证据。

## 1. 画面剪辑：原生镜头运镜、非 16:9 衬底与变速

### 原生底片运镜（`camera` / `camera_drift`）

普通实拍或静图镜头可在 `edit.json` 的 shot 上直接声明 `camera` 关键帧或 `camera_drift` 简写，由 `export.py` 在 Rec.709 YUV444 空间内做浮点亚像素重采样（高分辨率原件自动保留最高 2 倍输出分辨率细节），并与 `motion_layers`、转场余量和 `picture_holds.py` 协同。

```json
{
  "id": "detail",
  "visual": "assets/photo-4k.png",
  "fit": "cover",
  "camera": [
    {"frame": 0, "zoom": 1.0, "at": [0.5, 0.5]},
    {"frame": "@line1:重点", "zoom": 1.45, "at": [0.64, 0.42]}
  ],
  "camera_curve": "ease_out"
}
```

- `camera_curve` 可选 `ease`（默认）、`ease_out`（干脆到位）、`ease_in`、`flow`（穿过多键保持速度）、`spring`（轻弹性落稳）、`linear`。
- 只需轻微呼吸感推进时，可直接写 `"camera_drift": "push-in"`（另支持 `pull-out`、`pan-left`、`pan-right`、`pan-up`、`pan-down`，或自定义 `{"from_zoom":1,"to_zoom":1.08,"at":[0.5,0.5]}`）。
- `frame` 支持镜内帧号或实读词锚 `@line:词`。裁剪框自动贴紧画幅边界，不露黑边。

### 非 16:9 素材构图（`fit`）

- `cover` / `contain`：沿用原行为。
- `backdrop`：适合竖屏录屏或手机实拍。自动铺满柔焦压暗的同源背景，前景按 92% 安全高宽等比居中并带细边，消除生硬黑边。
- `paper` / `ink`：适合档案照片、票据或截图。将原件留白摆放在见秋米纸 `#F4EDDD` 或深墨 `#131A17` 画布中。

### 镜头变速（`speed`）

对移动视频镜头可设置 `speed`（`0.25`–`8.0`）。例如用 `"speed": 1.8` 压缩重复操作铺垫，或 `"speed": 0.5` 放慢微动作观察。导出器与 `transitions.py` 会按倍速自动校核源片余量与时长。

## 2. 转场：带缓动与方向模糊的原生空间桥

`edit.json` 镜头的 `transition` 新增以下预设（全部遵循原帧时钟和 `handles: "auto" | "source" | "hold"`）：

| 预设 | 默认时长 | 视觉机制与适用场景 |
| --- | --- | --- |
| `whip-left` / `whip-right` / `whip-up` / `whip-down` | 0.30–0.33s | 五次缓动 + 峰值方向运动模糊，起止两帧保持清晰；同级证据快切换场。 |
| `blur-dissolve` | 0.43s | 上镜离焦虚化、下镜由虚入实，时间或空间跨度较大时自然过渡。 |
| `dip-ink` / `dip-paper` | 0.47s | 经见秋深墨 `#131A17` 或米纸 `#F4EDDD` 短停换章，给大段落留呼吸。 |
| `pull-back` | 0.40s | 从细节锚点 `at` 缩回更宽语境（配 `zoom` 与 `settle`），与 `push-through` 互为呼应。 |
| `iris-open` | 0.43s | 从锚点 `at` 平滑张开柔边圆窗露出下一镜主体。 |
| `match-cut` | 0.40s | **实拍↔图形同坐标几何匹配剪辑**：前后两镜围绕共同锚点 `at` 缩放对接，同时以琥珀亮环光圈完成硬核接力。 |

- `push-through` 另支持可选 `"settle": 1.08`，让入镜从轻微放大平稳落稳到 `1.0`，衔接冲量。
- `evidence_window` 另支持 `"blur": 6`（0–24），弹窗时同步柔焦压暗底片，突出前景证据。
- 本地 Remotion 转场组件默认改为 `timing: "cubic"`（支持 `spring` / `linear`），并新增 `whip-pan`、`blur-dissolve` 与 `match-cut-ring`（双色旋转几何环匹配剪辑）。

## 3. 动效、3D 运镜、数学引擎与混合素材路由

- **语义向量级 B-roll 检索（`semantic_broll.py` / `library.py index-visual` & `semantic-search`）**：对个人素材库自动抽关键帧测量色温、明暗、运动能量、景别（远/中/微距）与 48 维视觉描述符，结合双语潜台词/情绪语义向量（并支持自动挂载本地 `open_clip` 或云端向量），按台词潜台词推荐远/中/微距交替的 B-roll。
- **三路混合素材路由器（`broll_router.py`）**：① 现实事件与物证走实拍/档案库（`library.py` + `media.py search-video`，入库前用 `media.py qc` 检测原片内部跳切、模糊、黑边与帧率顿挫）；② 抽象概念/代码流/历史重演走风格锁定 AI 视频 B-roll（`broll_router.py generate` 支持 Veo / Runway / Kling / Seedance 异步轮询下载、内容缓存与 `--fallback-procedural` 纹理化离线回退；图生视频首帧用独立的 `--first-frame`，严禁将带文字的 `style-bible.png` 直接作为首帧）；③ 精密数据与文字一律由 Remotion 透明叠加在动态视频上，并由 `remotion_library.py` 自动测量背景亮度 `backgroundLuma` 在 `composition.jsx` 中自适应调节压暗遮罩。
- **3Blue1Brown 风格数学/结构动画引擎（`math_motion.py`）**：支持 `--engine manim`（装有 Manim 时直连原生 Manim 场景）与零依赖离线 `--engine builtin`（内置 `complex-plane`、`function-morph`、`vector-field`、`network-graph` 及通用参数多曲线 **`formula-plot`**），支持 `--project` + `--shot` 锁定精准帧数。
- **`motion_layers`**：`curve` 新增 `ease_out`、`ease_in`、`spring`；关键帧新增 `blur`（0–40 px，可做前后景移焦入场）；图层新增 `shadow`（`true` 或 `{"dx":0,"dy":10,"blur":16,"opacity":0.36}`）给悬浮卡增加层次感。
- **`value_comparison.py`**：`reveal` 与 `gap` 支持 `@line:词` 词锚，新增 `subtitle`、`source`、底轨导引与圆角柱端。
- **Remotion 本地库（43 项）与高精度离线地理态势地图（`geo_map.py`）**：涵盖 `infinite-canvas`、`spatial-3d-card`、`exploded-diagram`、`sticker-callout`、`broll-evidence-pop`、`match-cut-ring`。`geo_map.py` 内置公有领域 **Natural Earth 110m 全球 176 国高精度海岸线与国界矢量**，支持 `highlight_countries`（按 ISO3/中英文名高亮国家与国界）与**真实三维球面大圆航线（Great-Circle Arc）**。全部数据图与文字卡均接入亮度自适应动态视频背景引擎。
- **画面统一与智能构图三件套**：① **`style_bible.py`** 同步产出供人看的 `assets/style-bible.png` 色卡板与供光影参考的**无字氛围底图 `assets/style-mood-plate.png`**；② **`color_match.py`** 在 CIE Lab 空间按**亮度分桶（跨明暗场景自动柔化曝光偏移）+ YCrCb 肤色掩码保护**对齐镜头，并实测输出调色前后真实 `delta_e_before` → `delta_e_after`（且 `color_finish.py` 自动豁免 `remotion_effect`/`explainer_scene`/`value_comparison`/`math_motion`/`geo_map`）；③ **`auto_focus.py`** 在 macOS 上优先调用**原生 Apple Vision 神经引擎（`focus_vision.m` 人脸 + 注意力显著性）**，支持 `--apply-covers` 写回封面焦点，并打通 `covers.py`（`"focus": "auto"`）与 `portrait_cut.py`（`crop_focus` / `auto_focus` 自动跟焦裁切 9:16）。
- **`screen_motion.py`（网页/PDF 证据镜头）**：默认启用 `drift_zoom: 0.025`（高亮落稳后保持轻微连续推进不死停，可设 `0` 关闭），并新增多段运镜节点 `stops: [{frame, view:[cx,cy,w]}, ...]`。
- **视频 B-roll 优先检索（`media.py`）**：聚合 `pixabay-video`、`pexels`、`nasa-video`、`archive-video`、`commons-video`（详见 [API Key 来源表](run.md#素材与生成-api-key-环境变量来源)）。

## 4. 调色与光学精修（`grade` 与 `finish`）

所有调色均在 10 位 Rec.709 空间内完成，早于字幕、角标与图层叠加，品牌色和字幕不受污染。

- **`grade` 扩展字段**：
  - `look`：内置克制影调预设：`editorial-warm`、`tech-cool`、`archive-sepia`、`cinema-neutral`、`high-contrast-doc`、`night-slate`（可与单字段叠加微调）。
  - `exposure`（`-3..3` EV 线性光曝光）、`temperature` / `tint`（`-1..1` 保亮度色温与色调）、`shadows` / `highlights`（`-1..1` 单调暗部与高光控制）、`vibrance`（`-0.6..0.8` 保护肤色与高饱和指示灯的自然饱和度）。
  - `lut` + `lut_strength`（`0..1`）：加载项目内 `.cube` 3D LUT（四面体插值）。
- **`finish` 光学质感字段**（在构图/运镜后、图层/字幕前生效）：
  - `vignette`（`0..1` 柔边暗角聚焦中心）、`grain`（`0..1` 仅亮度通道有机胶片颗粒，消除深色渐变断层）、`sharpen`（`0..1` 仅亮度通道微反差锐化）、`bloom`（`0..1` 克制高光辉光）、`blur`（`0..40` 整体柔焦）。
- **整片继承**：在 `edit.json` 顶层写 `grade` / `finish` 自动应用于全片实拍和照片镜头；数据图 / Remotion / explainer 生成镜默认跳过全局调色以保品牌色准，单镜写 `"grade": false` 或 `"finish": false` 也可单独豁免。

## 5. 音频：广播级人声、心理声学预触发、刻意静音断拍与节拍驱动

- **人声多情绪真声参考与广播级处理（`line.mood` + `voice_polish`）**：`assets/voices/jianqiu/voice.json` 现内置源自见秋真声 `original.mp3` 的 5 套独立情绪参考音（`hook` / `curious` / `suspense` / `reveal` / `warm`），搭配 `story_craft.py annotate-moods --write` 真正驱动 ZipVoice 情绪韵律与临场感变化。
- **真实原声切片、自动 J/L-Cut 与环境底噪床**：`location_sound` 支持声明式 `j_cut`（下镜声音提前入画）、`l_cut`（上镜声音延后退场）及独立外部原声 `file`（支持在 B-roll/图形镜中直接穿插采访原声切片）；`edit.ambience` 支持真实底噪文件或 4 套内置声学底噪床（`studio` / `server-room` / `office` / `paper-desk`），消除 TTS 句间真空死寂感。
- **真乐器 CC0 采样与开箱即用多轨分轨乐包**：`assets/instruments/` 现预置来自 VCSL（CC0 1.0）的真实原声立式钢琴（`piano`）、静谧管风琴铺底（`pad`）、弓奏颤音琴（`bell`）、低音踏板（`bass`）、弓奏弦乐（`strings`）与音乐会竖琴（`harp`），并升级 `cue_score.py` 立体声 Schroeder 厅堂混响。`assets/music-stems/` 随库预置 3 套 4 轨同步（`pad`/`piano`/`bass`/`percussion`）CC0 分轨包（`tech-investigation`、`warm-editorial`、`kinetic-teardown`），运行 `music_stems.py init-plan --pack tech-investigation` → `music_stems.py mix stems-plan.json --beats-out qa/beat-grid.json` 即可零配置获得四幕动态起伏配乐与强拍网格。
- **强拍吸附剪辑（`beat_snap.py`）**：在不动任何口播语音、保持最低 `tail >= 0.10s` 前提下，将 ±5 帧内的镜头切点自动吸附到 `qa/beat-grid.json` 最近强拍/拍点（`beat_snap.py --project . --write`，写入后重跑 `prepare.py` 生效）。
- **音乐总线协同与 0.3s 刻意静音断拍（Micro-Silence）**：
  - `music_vocal_pocket: true`（或衰减 dB 数）：在音乐轨 2.4 kHz 轻挖约 -2.8 dB 人声频段，手机外放也能听清字尾与数字。
  - `music_ducking`：支持 `sidechain`、`gentle`、`firm`、`prebalanced` 或详细阈值对象；`music_cues` 默认采用感知平滑曲线 `curve: "smooth"`（等响余弦过渡），并支持声明式 **`micro_silence` 简写**。
- **拟音、预触发声画桥与防疲劳机制**：`sfx_scaffold.py --project . --apply-edit` 除自动提前 2–3 帧预触发、控制每分钟 ≤9 次防疲劳上限外，还可一步登记 `sfx_plan` 并将非重叠的 Re-hook `micro_silence` 合并写入 `edit.json`。

## 6. 字幕、自适应动态词时钟、章节进度条与封面

- **智能中文断句（`captioning.py`）**：数字+单位（如 `120 元`、`42.5 个百分点`、`2.56 MB`、`第 3 行`）和常见科技财经复合词自动保持整体不断开，避免行首落单虚词。
- **六种字幕样式（`caption_style`）**：
  - `adaptive-kinetic`（新工程默认）：开场 0–6 秒钩子段、标有 `hook: true` / `emphasis: true` / `rehook: true` 的镜头、带 `line.highlights` 的金句镜及 `kinetic-hook`/`quote-card` 自动切换 **`kinetic-word` 词时钟逐词琥珀点亮**，其余说明段保持克制 **`editorial` 磨砂胶囊**，单镜也可用 `shots[].caption_style` 覆写，全程自动结合 `caption_avoid` 避开主体焦点。
  - `editorial`：见秋磨砂墨底胶囊 + 暖色细边 + 柔影 + 3 帧微浮入场。
  - `clean-shadow`：无底框多层柔影字幕，适合电影感实拍画面。
  - `kinetic-word`：全片联动 `timeline.json` 词时钟（`beats`），随实读进度以见秋琥珀 `#EDA961` 逐词点亮。
  - `plate` / `outline`：兼容原有样式。
- **关键词高亮**：在 `edit.json` 顶层写 `caption_highlights` 或单句写 `line.highlights`，纯文本 SRT 保持干净，成片烧录字幕自动以琥珀色强调关键数字与概念。
- **极简章节进度条（`chapter_bar`）**：在 `edit.json` 设 `"chapter_bar": true`（自动读取 `chapters` 或 `screen-plan.json` 的 `section`），在画面极顶端绘制细进度线与左上角静谧章节牌，字幕上移避让时自动隐藏左上章节牌防止冲突。
- **封面与包装先行（`covers.py` + `packaging.py`）**：`covers.py` 支持 `style: "high-contrast"` 与 `"focus": "auto"`（自动调用 Apple Vision 对焦主体）；开片前用 `packaging.py draft --apply` 一次生成 A/B/C 三版封面，以纯客观 **160px 手机缩略图对比度/字数/主体反差硬门禁**筛选并将最佳配置写回 `cover.json`，同时校验封面承诺与前 5 秒台词兑现度。

## 7. 统一预检、多模态联合审片与平台留存自进化

- `prepare.py --new-edit` 支持 `news`、`explainer`、`story`、`teardown` 四类题材模板。
- **一键制片预检（`studio_pipeline.py preflight --project . --write` 或 `prepare.py --preflight`）**：单命令串联 `story_craft`、`pronunciation_review`、`auto_focus`、`color_match`、`packaging`、`sfx_scaffold`（加 `--apply-sfx`）与 `narrative_audit`，合并输出统一 `qa/preflight-report.json`。
- **一键导出后联合质检（`studio_pipeline.py postcheck --project . --media <候选.mp4>` 或 `review.py --critique`）**：将 `review.py` 技术验收与 `multimodal_critique.py`（1fps 故事板、运动量/响度/ΔE 色差跳变）合流为单一 `qa/director-report.json`；机械类低运动/高 ΔE 修复可直接用 `multimodal_critique.py patch --tickets ... --apply` 安全写入 `edit.json`。发布后用 `retention_feedback.py ingest --save-library` 反查掉点镜头并沉淀回个人素材库。
