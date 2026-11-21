# 怎么审网页运动与实际终混

一条能正常播放的影片里，指针仍可能每三帧才挪一下；清楚的人声也可能掩盖整条配乐。审片人在第一次看片前就拿到本片需要回答的问题。主编另查观众反馈，修了一处时仍须核对受影响的相邻画面和声部。签发绑定实际编码候选。

## 网页、推拉与鼠标

在手机宽度原速播放。分别取取景正在改变、指针正在拖行、观众读完后画面停住三个阶段。对平面网页可量一段连续帧中同一行文字的宽度，或量同一选区右边缘的横坐标。有可见的顿挫时，找最后一个静止帧和第一个大跳帧，分辨是相机缩放、光标、选区还是源视频本来重复。按字符吸附是浏览器文本选择的行为；指针轨迹和取景宽度应独立计算。把连续坐标保留到最终采样，分辨率和帧率在最后输出时固定。编辑式高亮在项目来源中注明；给真实点击配接触声，原文的后期高亮不借用鼠标点击的身份。

`scripts/screen_motion.py` 提供真实原件高分辨率图像上的浮点取景与逐行揭示。输入包含 `clean` 原像素、同一页面完成选择时的 `selected`、CSS 的 `clip` 与 `lines`、截图倍率 `dpr`、首末 `view_from/view_to`、帧数及移动区间。多行用浏览器 DOM Range 的行框。已停稳的镜头可以继续静止。运行 `python scripts/screen_motion.py --help` 查看当前入口。记录可复现的源、取景和选区范围，给最终压过字幕的编码片再做一次观测。

### 把网页原句做成证据镜头

金额、日期或条款需要看出处时，先展示原件上能够辨认的标题、段落或表头。保留决定含义的字，例如“至多”“预计”和计量单位。远离正文的标题可另选一镜。进入关键句后才沿原有换行刷出重点，留出完整读一遍的时间。这个镜头借鉴了原库将网页截图和文字实测位置一起带入剪辑的做法。[原库的网页取证方法](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/sourcing.md)

在制片工程里，把已核实的页面地址放入 `ARTICLE_URL`，把屏幕上要刷出的原文放入 `QUOTE`。本机的 Chromium 和 puppeteer-core 可直接截图。文章内出现重复原句时，在命令最后加能锁定该段的 CSS 选择器，例如 `'article p:nth-of-type(3)'`。

```sh
node "$SK/scripts/capture_passage.cjs" "$ARTICLE_URL" "$QUOTE" assets/report-passage
"$PY" "$SK/scripts/screen_motion.py" assets/report-passage/shot.json --output assets/report-shot.mp4
```

第一行在新目录保存原始截图和镜头参数。脚本等字体排好后测量原文字形的位置，跨粗体、链接和自动换行仍保留各自的框。打开截图，核对标题、日期、原句的上下文及遮挡情况。在 `shot.json` 中调整 `view_from/view_to`（视口 CSS 像素下的中心横纵坐标与取景宽度）、`move_frames`（开始推近、到位）、`reveal_frames`（开始标记、刷完）与 `frames`（整镜时长）。片长和这三个时刻从本镜分镜取值；缩小到目标播放宽度，读完关键句才接下镜。软件从同一截图按原文行框逐段着色，深色墨迹随原件保留。深底或竖排原文可用上文的原生选区图自行安排。制作时把第二行输出作为对应 shot 的 `visual`，在本片来源登记 `shot.json` 内保留的原网址、文章标题和引用原句。

## 配乐、动作音与说话的关系

从终混找一个听得出旋律的音头、一个具体动作、一个有语音的近邻窗口。在同一音量下对比，分别判断声音是否能被认出来、是否盖住字或太抢。音乐可改配器和节奏，在小扬声器能重放的区域安排标志性音符。按键、翻页和旋钮看短时瞬态及动作时机，逐个平衡；同一纸声落在语句间的效果可能比落在有声字上强很多。

默认 `music_ducking: "sidechain"` 在人说话时自动压低伴奏。已经按旁白混好或使用“口播轻版”的片子，先试 `"music_ducking": "prebalanced"`，再对实际 AAC 验证。保留旁白、音乐和动作分轨，测相对于同窗语音的强弱时使用与最终导出一致的压缩与增益。整曲 RMS 和音乐 WAV 本身的电平不能代替这一步。静默留白可作为叙事设计，审片单要写清听的是哪一段。

```sh
python scripts/scene_audition.py old.mp4 new.mp4 --start 2.75 --end 6.65 --output compare.wav
python scripts/scene_audition.py old.mp4 new.mp4 --start 2.75 --end 6.65 --phone --output phone-proxy.wav
```

两份输出都按**整片**响度匹配，顺序为旧段、0.38 秒间隔、新段。第二份为模拟单声道频带，实机的外放、耳机或用户自己的播放器还会影响听感。

### 把音乐起落对到动作或一句话

粗剪时先挑出真正需要音乐改变的地方。产品演示可以从手接触开关到指示灯亮起留出空间；资料片可以在对应原句第一次清楚可读时减少节拍，等镜头进入下一份材料再恢复。音乐如何变化取决于当时要听什么、看什么。需要换配器、删一拍或重写乐句时在原曲分轨处理。需要改变现有音乐轨的强弱时，可在已有 `edit.json` 写：

```json
"music_cues": [
  {"shot":"operation","offset":0.20,"fade":0.35,"gain":0.25,"event":"手落到控件，让出反馈声"},
  {"shot":"result","offset":0.45,"fade":0.60,"gain":1,"event":"观众已看清结果，恢复音乐"}
]
```

`shot` 使用本期固定镜号，`offset` 是距该镜起点的秒数。`fade` 是从当时音量渐变到目标的秒数，省略时立即切换。增益相对本片已有的 `music_gain`，0 为静音，0.25 约低 12 dB，1 恢复原音量。目标到达后保持，直到下一个提示，过渡可以跨镜。只有一个相对音量作用于这一时刻。例里的时间和音量按实际停顿、音头及正常速度试听决定。若音乐分轨已包含本次起落，可直接混用原分轨。导出保留对白侧链或 `prebalanced` 的原有选择。重新准备与导出后，候选的 `technical.json` 保存提示、解析后的起止采样和实际终混校准。

只改局部配乐时，更新本期提示并运行 `prepare.py`。在原工程用 `mix_window.py` 从完整分轨重算侧链和渐变，再取票上帧窗。把讲话、拟音、渐变尾音一起纳入窗口，两端选在未动的声段。原候选的 `technical.json` 若含 `mix_normalization`，可用 `--levels-from` 沿用其响度校准；省略时按新版全片测量。随后按[局部修音](editorial-review.md)交给 `audio_window.py`，复听接缝和窗内台词。

```sh
"$PY" "$SK/scripts/mix_window.py" --start-frame "$A" --end-frame "$B" --levels-from "$OLD_TECH" --output edit/mixed-window.wav
```

### 用重读词和动作接触帧定位声音

先原速听一句。想让观众听清「锁住」时，乐床可以在重音之前开始下沉，真实扣上的动作声应对齐金属件接触的那帧。这两个提示可以落在不同位置。

```json
"music_cues": [
  {"shot":"latch","at":"@lock:锁住","shift_frames":-4,"fade":0.12,"gain":0.3,"event":"重音前给锁扣留出空间"}
],
"sfx_plan":"sfx.json"
```

在 `sfx.json` 写动作音。例中的 83 是动作发生在原素材中的显示帧号，从零开始计数。用本镜 `visual` 原件逐帧找接触，再试听所选录音，填入录音里选定接触峰的秒数 `anchor`。

```json
[{"shot":"latch","at":{"source_frame":83},"key":"dial-629301","trim":[0.3,0.8],"anchor":0.378,"peak_db":-18,"pan":0,"event":"扣件接触"}]
```

配乐和拟音都支持 `at` 或原有的 `offset`。词锚与画面动效共用准备后的词钟，同一句第二次说到同一词时用 `@lock:锁住#2`。`prepare.py` 会为配乐和拟音计划引用的台词生成词钟，逐字发声时间以本次录音或人工校准的 `word_times` 为准。用 `shift_frames` 以成片帧为单位前移或后移。先让锚点落在标注镜号内，再调整预告或余响。源帧按媒体显示时间映射，可处理可变帧率。重录、调镜长或改变入点后运行 `prepare.py`，正式导出与局部修音都会按当下的计划重算。已经混好的效果分轨沿用 `effects`；同一工程在 `effects` 与 `sfx_plan` 中选一项。候选片的 `technical.json` 在 `sfx_cues` 记下实际命中采样、取用的声音片段及素材来源。

### 从上一个镜头听到下一个现场

普通实拍镜头可直接保留原文件的第一条音轨。在需要的 shot 中设置 `location_sound`。例如本镜画面取自原片 6.0 至 8.0 秒，手伸进抽屉前先听见滑轨声，切到物件特写后再听完余振。

```json
{"id":"drawer","visual":"assets/drawer.mov","in":6.0,
 "location_sound":{"source_range":[5.8,8.3],"gain_db":-6,"fade_in":0.04,"fade_out":0.16}}
```

`source_range` 的起止值和画面 `in` 都以原媒体开头为零点。这里声音先于切入 0.2 秒，晚于切出 0.3 秒。用相邻画面真实可用的余量安排 J-cut / L-cut；片头、片尾以及原轨末尾要容得下完整范围。淡入淡出的单位是秒。遇到交叠的环境声，在同一音量下听前后两个空间的距离和底噪，再调增益与渐变长度。导出保留原件校验值、取用的原声采样区间及落在成片的区间，可据此复听接缝。

## 签发时写什么

`editorial_review.py status` 列出候选片的 `missing_perception`。常规导出从实际 music/effects 和 screen-plan.json 推导应检项；自定义项目可以在 edit.json 用 `perception_checks` 明确要求 music、effects 或 motion。每类用实看片段填写，不套用其他片的数值。例：

- 声音意见新增 `observations[].perception`，如 `{"kind":"music","subject":"0.8 秒的琴音","method":"mix_proxy","reference":"同窗旁白，300–5000 Hz 单声道 RMS","contrast_db":-11.29}`。写实际片段的起止帧，effects 项可用击键的实际窗口。真正用设备监听时填 `method:"device"` 和 device、reference，在 evidence 中写听到的声部、时机和判断。测量结果需明确为近似，并保留比较短样。
- 页面运动行可写 `{"kind":"motion","subject":"选区右边缘","method":"frame_trace","first_frame":939,"positions":[177,184,191,199],"unit":"384宽画面的横坐标像素"}`，行的起止帧须覆盖这段正在移动的镜头。多种问题并存时，分别量取景和指针。同一镜头里有先后两段动作，或需要复查观众点名的某段时，在该 cut 用 `"motion_windows": [[100, 112], [130, 146]]` 明确登记全片帧号，终点不含在内。已列窗口作为该镜需验的动作集合。主编逐项查看已登记的运动窗。status 中的 motion@起帧..止帧 表示该段还缺轨迹。局改后，已审且整窗经验证保持相同的段可沿用上次观测；有待改票、被改动或缺少这一证明的段重新测量。
- 若意见是需要修改，仍开带窗口与验收条件的票。如果会后发现缺一项，可以用 `editorial_review.py supplement MEDIA spot.json` 追加当前候选的实看片段并重开会议。测得差值和位移只能支持可读、可辨与同步的具体判断。主编负责解释判断和局限。
