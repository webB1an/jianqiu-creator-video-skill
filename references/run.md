# 从工作目录制作与重建

Codex 是导演和素材选择器。每个主题写本期讲稿、检索素材、编排画面和配乐。通用动效[先查现有 Remotion 组件](motion-frameworks.md#先找现成组件)，用本期素材和实读卡点配置选中的组件。下面的工具处理可重复的下载、真实语音时间轴、混音和编码。在镜头目录运行命令，SK 指向本 skill 的绝对路径。脚本的位置与项目位置可以不同。

## 一次性环境

需要 ffmpeg、ffprobe（带 libx264、libass、loudnorm），能显示中文的字体，以及 Python 3.12。新环境用 Python 3.12 创建 venv，然后运行 python -m pip install -r "$SK/scripts/requirements.txt"。已有环境优先使用。推荐用独立虚拟环境，安装 numpy、requests 和 sherpa-onnx==1.12.39。审片工具和附带样片还用 Pillow。在这台电脑已有可用环境：

```sh
SK="$HOME/.codex/skills/creator-video"
PY="$HOME/.cache/creator-video-env/bin/python"
"$PY" "$SK/scripts/setup_zipvoice.py"
```

ZipVoice 第一次合成时会自动下载并校验模型，也可用上面的命令提前安装。本机已有缓存，后续配音可离线完成。全部使用现成录音时，prepare.py 会直接读取音频。重建使用旧 Melo 音色的工程时，可用 setup_model.py 安装旧模型，并在 edit.json 顶层指定 `"voice": "melo"`。

## 素材

```sh
"$PY" "$SK/scripts/media.py" search openverse 'macro camera lens' > candidates.json
"$PY" "$SK/scripts/media.py" search nasa 'Apollo 8' > archive.json
"$PY" "$SK/scripts/media.py" fetch assets.json
```

archive-video 在 Internet Archive 搜索标有开放许可的实拍或档案视频，返回多个可用版本及体积；查看来源页面和样帧后选择适合画幅的版本。commons-video 在 Wikimedia Commons 无密钥搜索视频，返回时长、分辨率、字节数、作者和许可；选片时检查原作要求。Openverse 搜索照片，NASA 搜索任务档案，已有 PEXELS_API_KEY 时 search pexels 搜实拍视频。也可以用网页检索专业档案馆并提取官方提供的影片下载链接。检查源页面、权利、使用限制和实际画面，再将选中的结果写为 assets.json 数组。每项 file 是本地文件名，url 是媒体地址，source 是原作页面，author 和 rights 来自原站。抓取器验证媒体、保存哈希并复用缓存。按许可在片中或随片说明中署名，注明裁切、剪辑等修改，提供适用的许可链接。

## 编排

### 实素材粗剪

配音前可用分镜的一段试排原件。保存下例为 `rough-plan.json`，用实际文件名、入点和观察替换。路径相对该 JSON。起止是预览时间线的帧号，含开始帧，排除结束帧，从第 0 帧连续排列。视频 `in` 是源秒数，按原速截取该镜帧数。JPEG、PNG 等静帧按镜长停留。每个镜号只出现一次，同一原件的两段可以用不同镜号。

```json
{"fps":30,"cuts":[
  {"id":"prompt","start":0,"end":48,"visual":"assets/operation.mp4","in":1.2,"subject":"错误提示","action":"文件名变红","discovery":"当前文件名缺少扩展名"},
  {"id":"field","start":48,"end":78,"visual":"assets/name.png","subject":"文件名输入框","action":"停在修正后的扩展名","eye_trace":"从上镜红色文件名移到输入框末尾"}
]}
```

```sh
"$PY" "$SK/scripts/screen_plan.py" rough-plan.json --roughcut qa/opening-01 --alternate field prompt
```

打开 `qa/opening-01/first.mp4` 和 `alternate.mp4` 正常速度比较。单版试剪可省略 `--alternate`。默认输出为等比完整取景的 960×540 无声预览。镜头有明确的 `crop` 时沿用指定取景，保存到对应收据行。目录里的 `receipt.json` 留下源文件路径和校验值、各版镜序、源入出秒数、预览帧区间及文件校验值。改稿使用新目录。比较烧录字幕或调色效果时，用正片相同的处理链出候选。已有工程用 `screen_plan.py screen-plan.json --timeline timeline.json` 校验正式分镜。

若本次要把镜序的观察写回素材卡，先为实际镜号登记选卡决定，再给上述双版命令加 `--project "$PROJECT"`。分镜原顺序 first.mp4 是本次要检验的剪法，alternate.mp4 是对照。两个 MP4 会绑定渲染时的选卡决定和同一组源片段。[匿名对照与素材卡回写](continuous-learning.md#让下一期搜到使用效果)

### 对齐原声与旁白，比一处切点的早晚

有一处动作、结果或词尾需要调节奏时，把与这段粗剪第 0 帧对齐的旁白、现场声或临时总混先备成 WAV。预览只播放这条 WAV，现场录音与音乐按需要预先混入。路径相对分镜 JSON，也可给绝对路径。保留音轨开头的有意留白；若只试全片中段，先从该段真正的节目入点取声音。WAV 应覆盖整段，过长时取至计划末帧。

```sh
"$PY" "$SK/scripts/screen_plan.py" rough-plan.json --roughcut qa/field-timing --audio audio/guide.wav --shift-cut field 8
```

上例第一版在全片 F48 进入 field 镜，第二版在 F56 进入，总长仍为 78 帧。前镜多播八帧，后镜少播八帧。后镜用视频时，它的源入点也顺延 8/30 秒，后面的动作依然对齐同一句话；用静帧时直接调整停留。负数表示提前，入镜视频需有真实前余量，两镜均至少保留一帧。两版共享同一次编码的音轨，收据记录源 WAV、AAC 内容校验值和切点偏移。只给 `--audio` 可直接预览单版。

原速从上一镜听过切点，比较第一眼能读出结果的时刻和当时的声音。选定后打开 `receipt.json` 的 `versions.first.cuts` 或 `versions.alternate.cuts`，按镜号取 `start/end`（粗剪帧）和视频 `in/out`（源秒）。将这些窗口写进正片实际剪画面用的轨道或镜头生成器，声音沿用同一节目时基；取了中段的粗剪要加回它的节目起帧。程序按旁白长度切镜的项目，可以把这两个画面连剪为原有台词上的连续底片，再按现有各镜时长从它取画。同步更新正式分镜的切点和观察窗，导出后对一遍结果首帧与原句。

### 配音与正片

有整条真人或数字人口播素材时，先按[连续原声和词时钟](explainer-presenter.md)准备工程；采用参数化图形或主持镜头时，按[画面场景命令](explainer-scenes.md#按实际帧数制作)在 prepare 和 export 之间生成对应 shot 的底片。素材抠像、动作拟音、动作对照见[素材与运动](wallstreet-craft.md)。

新科技、财经和产品解释片在本期空目录运行 `"$PY" "$SK/scripts/prepare.py" --new-edit explainer`，新闻把最后一个参数改为 `news`。在生成的 edit.json 里补上主旨和镜头。自编项目生成器可直接沿用下面的节奏字段。实际制作时用本期素材和台词替换示例。

```json
{"size":[1920,1080],"fps":30,"narration_pace":"quick","shots":[
  {"id":"reveal","visual":"assets/take.mp4","in":1.5,"fit":"cover","lead":0.3,
   "lines":[{"text":"开关按下后，灯才亮起来。","pause":0.25}],"tail":0.5}
]}
```

新制作的新闻或解释片先在 edit.json 声明 story_intent，如 `{"kind":"news","main_point":"末班车发车时刻提前十分钟","viewer_value":"晚下班乘客能从新时刻表找到备选换乘"}`。配音前运行 `"$PY" "$SK/scripts/prepare.py" --read-script` 得到纯口播稿；编辑定位句子可加 `--line-ids`。按[读稿与返修](video-writing.md#配音前通读一遍)改到观众能听明白本段发现，再运行 prepare.py 生成见秋配音。语速设定、逐句 A/B 试听与实测报告见[口播节奏](narration-pace.md)。参见[见秋旁白和独立试音](voicecasting.md)。用旧工程搭建新片时，移除指向旧片旁白的 line.audio、旧 voice 和仅适用于旧音色的 speed。主动沿用的本片录音可保留。每句 text 用于字幕和旁白，可加 spoken 解决缩写、多音字或数字读法，可加 audio 指向现有单句录音。长句按自然呼吸分成多句。每镜唯一 id。lead、tail、pause 和视频入点 in 的单位为秒。没有旁白的镜头用空 lines 与正的 tail。有意整镜黑场时可设置 allow_black 为 true，保留设计理由。如果整片没有旁白，改用适合无声或纯音乐作品的混音流程。正片 size 必须满足 16:9。可另配置 music、effects 两条从片头起对齐的音轨及 music_gain、effects_gain（线性倍数）。音乐需要避开原话或一次操作反馈时，加按镜头定位的 [`music_cues`](screen-and-mix-review.md#把音乐起落对到动作或一句话)。混用相机、屏录和照片时按[镜头色彩说明](output-color.md)核对源解释。按主题写项目自己的生成/剪辑命令。音轨及自制动画的长度从 timeline.json 读取。字幕默认 Heiti SC，可通过 font 指定当地可用字体。

```sh
"$PY" "$SK/scripts/prepare.py"
# 此时生成原创镜头及配乐，或者选择长度充足的媒体。写好 sfx.json 后运行 soundmix.py。
"$PY" "$SK/scripts/export.py"
```

prepare 按句测量配音，生成 narration.wav、audio/captions.srt 和各镜头的帧区间。export 对齐画幅、烧录字幕、混音并完整解码，随后将 MP4、字幕、封面和时间轴放入独立的候选目录，打印媒体路径并打开本轮团队审片。口播轻版或已依据对白调过音量的配乐，可在 edit.json 写 `"music_ducking": "prebalanced"`。旧工程默认 sidechain。用[同响度的编码样片](screen-and-mix-review.md)实查两种混音。已有正式片可继续播放。工程已有各画幅的专门设计时，传 `--prepared-covers 目录` 将这三张封面一起送审。普通镜头可直接叠加[分层 PNG 动画](kinetic-evidence.md)，由 export 依镜内帧合成并缓存。整镜取景、匹配剪辑或复杂三维动效也可由项目生成器先渲染成 visual。想比较段落快慢时，在 screen-plan.json 的段首 cut 写 `section`，导出后看 `pace.sections`。

运行 `"$PY" "$SK/scripts/review.py"` 查看当前候选的接触表、黑场、静音和字幕信息。需要判断读数、表情或操作反馈能看多久时，在分镜登记[关键画面的观察区间](picture-edit.md#关键画面的可见时长)，从最终编码片复看。用 `--media 路径` 指定历史候选，审片工具读取随该版保存的时间轴和字幕。裸媒体可用 `--timeline`、`--sidecars` 指定配套工程。能使用本地 SenseVoice 时加 `--asr 模型目录`；逐句回译适合定位需复听的词。按[审片团队与定点修改](editorial-review.md)执行独立观片、会审、返修与签发。publish 将签发的字节、字幕和三种封面写入 out。固定时长的镜头、字幕和音频调整走局改；改时长时才重新计算受影响的时间区间和相应依赖。

准备投稿时可从本机签发的横屏文件继续生成[投稿稿件与来源附件](social-handoff.md)；需要竖屏副本时，按[逐镜重排](portrait-cut.md)用同一条时间轴合成，另行审看。

## 文章与全文朗读

从同一份 article.json 生成可点击来源的文章及全文音频。文件含 title、byline、date、sources 和 sections。标题含数字或专名时可用 spoken_title 指定读音。sources 为键到 [来源名称, URL] 的映射。每段含 sentences（由 text、可选 spoken 和句后 pause 秒数组成），可选 heading 和 sources（引用键数组）。标题、段标题和每个正文句都会朗读；spoken 用于把数字、字母和人名写成读音。音频与该 HTML 放在同一目录时可直接点播放。

`"$PY" "$SK/scripts/article_read.py" article.json --out out --takes audio/article`

未改的句子按文本、读音、语速、声线和 WAV 哈希复用。逐句 WAV 存在 audio/article，语音清单与声线哈希见 voicecast.json。较短的视频口播另写 edit.json。逐句回听数字和专名；把编辑稿、播读稿与网页来源一起留在项目中。

## 制片中断与候选来源

同一工程的 prepare 与 export 串行。中断后重新运行正常入口，它先恢复成套的配音、字幕和时间轴，再检查实际用到的输入。制片时改了脚本，重新 prepare；只改画面或分轨，正常重新 export。已处理的本地镜头通过内容及解码校验后可复用。`qa/production-run.json` 记录阶段、耗时与复用情况。外部字幕可用 export 的 `--captions` 明确指定。旧时间轴能检查的来源范围会标记为 legacy。

新候选保存渲染与暂存时的 skill 版本、字幕封面和交付的来源文件。默认从工程搜 sources.md、visuals-sources.md、sound-sources.md、credits.md；自定义附件可在 edit.json 的 delivery_credits 列项目相对路径。审片后交付同一组文件。复制或导出正式片时，从同一签发版本取齐字幕、封面和署名。搬迁完整 out 目录时保留隐藏目录 `.release-versions`；脚本批量读取多份交付文件时先取 `review_bundle.released_snapshot(media).parent` 锁定同一代。
