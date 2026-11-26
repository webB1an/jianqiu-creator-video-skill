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

## 素材、语义检索、AI B-roll 与数学动画

```sh
# 1) 个人素材库视觉+语义向量索引与按情绪/潜台词检索（离线免 Key）
"$PY" "$SK/scripts/library.py" index-visual
"$PY" "$SK/scripts/library.py" semantic-search "机房代码高速编译验证的精密感" --top 5

# 2) 三路混合素材路由（实拍库 / 风格锁定 AI 视频 B-roll / Remotion 透明数据叠加）
"$PY" "$SK/scripts/broll_router.py" providers
"$PY" "$SK/scripts/broll_router.py" route edit.json --out qa/broll-route.json
"$PY" "$SK/scripts/broll_router.py" generate "1700万行代码流过深青色机房" --seconds 4 --out assets/ai-code.mp4

# 3) 外部高清实拍/档案视频检索
"$PY" "$SK/scripts/media.py" search-video 'coding server technology' > broll.json
"$PY" "$SK/scripts/media.py" search pixabay-video 'mathematics chalkboard' > pixabay.json
"$PY" "$SK/scripts/media.py" search nasa-video 'supercomputer' > nasa-video.json
"$PY" "$SK/scripts/media.py" search openverse 'macro camera lens' > candidates.json
"$PY" "$SK/scripts/media.py" fetch assets.json

# 4) 3Blue1Brown 风格数学/几何/依赖图动画 & Johnny Harris 风格离线动态地图
"$PY" "$SK/scripts/math_motion.py" list
"$PY" "$SK/scripts/math_motion.py" render --preset complex-plane --seconds 6 --out visuals/zeta.mp4
"$PY" "$SK/scripts/geo_map.py" list
"$PY" "$SK/scripts/geo_map.py" render --preset semiconductor-chain --seconds 5 --out visuals/map.mp4

# 5) 视觉圣经、跨镜自动色彩统一、自动人脸/显著性避让、包装先行 A/B 封面与脚本情绪导演
"$PY" "$SK/scripts/style_bible.py" init --project . --preset jianqiu-dark-editorial --apply-edit
"$PY" "$SK/scripts/story_craft.py" audit .
"$PY" "$SK/scripts/story_craft.py" annotate-moods edit.json --write
"$PY" "$SK/scripts/packaging.py" draft --project . --out qa/packaging
"$PY" "$SK/scripts/color_match.py" --project . --hero s01 --write
"$PY" "$SK/scripts/auto_focus.py" --project . --write

# 6) 多轨分轨配乐、多模态观众审片包与平台留存曲线回流学习
"$PY" "$SK/scripts/music_stems.py" mix stems-plan.json --timeline timeline.json --out audio/music.wav
"$PY" "$SK/scripts/multimodal_critique.py" build --project . --media out/final.mp4
"$PY" "$SK/scripts/retention_feedback.py" ingest retention.csv --project . --platform bilibili --save-library
```

`search-video` 会自动聚合本机所有可用的高清视频源（优先已配 Key 的 Pixabay/Pexels，加上免 Key 的 `nasa-video`、`archive-video`、`commons-video`）。`broll_router.py` 自动根据每镜台词判断走**实拍/档案库（Route A）**、**风格锁定 AI 视频 B-roll（Route B，自动选用本机已配 Key 的 Veo/Runway/Kling/Seedance，无 Key 时自动离线渲染 1080p 程序化动态底片）**还是**Remotion 数据叠加动态底片（Route C）**。检查源页面、权利、使用限制和实际画面，再将选中的外部素材写为 `assets.json` 数组并执行 `fetch`。按许可在片中或随片说明中署名，注明裁切、剪辑或 AI 生成标识。

### 素材与生成 API Key 环境变量来源

> **安全约定**：所有外部 Key 一律通过本机环境变量注入，**切勿写入工程文件、`edit.json`、skill 文档或版本库**。未配置任何收费/第三方 Key 时，免 Key 公域视频库 + 离线语义视觉检索 + 离线程序化动态底片 + 内置 3Blue1Brown 数学引擎仍可完整跑通。

| 环境变量 / 检索源 | 所属模块 | 是否需要 Key / 费用 | 官方获取地址与当前状态 | 用途与许可说明 |
| --- | --- | --- | --- | --- |
| `PIXABAY_API_KEY` | `media.py pixabay-video` / `broll_router.py` | **需要（免费，强烈推荐）** | [Pixabay API 文档页](https://pixabay.com/api/docs/)：注册免费账号并登录后，文档页顶部直接显示专属 Key（无需信用卡、无需审批，约 100 次/分钟） | 现代 1080p/4K 实拍与科技场景 B-roll；遵循 [Pixabay Content License](https://pixabay.com/service/license-summary/)，保留作者与来源署名 |
| `PEXELS_API_KEY` | `media.py pexels` / `broll_router.py` | 需要（仅老 Key 可用） | [Pexels API Key 页](https://www.pexels.com/api/key/)：**官方目前暂停新 Key 签发**（`New API key issuance is currently paused`），仅既有老 Key 可用；无 Key 直接用 `PIXABAY_API_KEY` | 现代 1080p/4K 实拍视频；遵循 [Pexels License](https://www.pexels.com/license/) |
| `DEEPSEEK_API_KEY` | `studio_pipeline.py postcheck --backend deepseek` | 按 API 用量计费 | [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) | 默认使用 `deepseek-flash` 审看故事板；可用 `DEEPSEEK_MODEL` 选择模型，用 `DEEPSEEK_BASE_URL` 选择兼容服务 |
| `LOCAL_VLM_URL` / `LOCAL_VLM_MODEL` | `--backend local-vlm` | 本地服务免云端 Key | 本机运行的 OpenAI 兼容视觉模型服务 | 例如 `http://127.0.0.1:11434/v1`；`--backend offline` 直接运行内置分析 |
| `GEMINI_API_KEY` | `broll_router.py --provider veo` / `semantic_broll.py` | 可选（按量付费，Veo 需开通计费） | [Google AI Studio API Keys](https://aistudio.google.com/apikey) | Route B 风格锁定 AI 视频 B-roll（Google Veo 2/3 文生/图生视频）及可选多模态语义向量 |
| `RUNWAYML_API_SECRET` | `broll_router.py --provider runway` | 可选（按点数计费） | [Runway Developer Portal](https://dev.runwayml.com/) | Route B 风格锁定 AI 视频 B-roll（Runway Gen-3/Gen-4 Turbo 图生视频） |
| `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | `broll_router.py --provider kling` | 可选（按资源包计费） | [可灵开放平台 Kling AI](https://platform.klingai.com/) | Route B 风格锁定 AI 视频 B-roll（可灵文生/图生视频，中文隐喻与写实镜头表现好） |
| `ARK_API_KEY`（+ 可选 `SEEDANCE_MODEL_ID`） | `broll_router.py --provider seedance` | 可选（按量计费） | [火山引擎方舟 Ark 控制台](https://console.volcengine.com/ark) | Route B 风格锁定 AI 视频 B-roll（豆包 Seedance 视频生成） |
| `OPENAI_API_KEY` | `semantic_broll.py` | 可选（按量计费） | [OpenAI API Keys](https://platform.openai.com/api-keys) | 可选稠密语义检索向量增强（未配时自动使用免 Key 双语概念+OpenCV 视觉向量） |
| `ELEVENLABS_API_KEY` / `SUNO_API_KEY` | `music_stems.py`（可选 AI 配乐扩展） | 可选（按量计费） | [ElevenLabs API Keys](https://elevenlabs.io/app/settings/api-keys) / [Suno](https://suno.com) | 可选外部 AI 音乐生成（未配时离线多轨分轨混音器 `music_stems.py` 与 `cue_score.py` 电影级合成声部均可免 Key 运行） |
| `nasa-video` / `nasa` | `media.py` | **免 Key** | [NASA Image & Video Library API](https://images-api.nasa.gov) | 航天、科研、超算高清 MP4 与任务图片；美国联邦公有领域 |
| `archive-video` | `media.py` | **免 Key** | [Internet Archive](https://archive.org) | 历史/纪录/科教档案 MP4（自动筛选 CC BY / CC0 / Public Domain） |
| `commons-video` | `media.py` | **免 Key** | [Wikimedia Commons API](https://commons.wikimedia.org/w/api.php) | 全球历史、地理、科学开源视频；按文件页实际条款署名 |
| `openverse` | `media.py` | **免 Key** | [Openverse API](https://api.openverse.org/v1/) | CC0 / PDM / CC BY 开源照片与插图 |
| `open-clip-torch` / `manim` | 本地可选 Python 包 | **免费开源（可选）** | `pip install open-clip-torch` / `pip install manim` | 可选本地 CLIP 图像编码器与原生 Manim CE 渲染引擎；不装时离线内置引擎均可直接运行 |
| `CREATOR_MEDIA_LIBRARY` / `CREATOR_REMOTION` / `CREATOR_CHROME` | 本地路径覆盖 | 可选本地路径 | 本机自定义目录/可执行文件 | 指定个人素材库根目录、Remotion 运行时目录与无头 Chrome 路径 |

**配置方式（macOS zsh）**：为确保交互终端和非交互脚本子进程都能读到环境变量，请同时写入 `~/.zshenv` 和 `~/.zshrc`：

```sh
export PIXABAY_API_KEY="你的_Pixabay_API_Key"
# 如需启用 AI 视频 B-roll 生成，按需追加其一即可：
# export GEMINI_API_KEY="你的_Google_AI_Studio_Key"
# export RUNWAYML_API_SECRET="你的_Runway_Secret"
# export KLING_ACCESS_KEY="..." ; export KLING_SECRET_KEY="..."
# export ARK_API_KEY="..."
```

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

原速从上一镜听过切点，比较观众第一眼能读出结果的帧和当时听到的词。已有实录或配音时，先用 `prepare.py` 建好节目时钟。把本段粗剪总帧数对到要替换的连续一组正式镜号，首尾对齐。可在这组镜号间插入新的纯画面镜号，一句原声可跨数镜。全片重排时也保留原有台词镜号，以便找到相应录音。有声与无声版本都从工程目录用同一命令采纳。

```sh
"$PY" "$SK/scripts/prepare.py" --adopt-roughcut qa/field-timing/receipt.json --plan rough-plan.json --version alternate
"$PY" "$SK/scripts/prepare.py"
```

第一条把选定镜序、每镜帧数、原件路径、源入点与裁切写回 `edit.json`。第二条按原来的说话顺序重建配音和字幕，并让 `timeline.json.shots` 使用新的画面切点。`narration_sections` 保留各段录音的原时钟。所选段内部采用预览里的直接切，随后可为需要的接点设计转场。在正式分镜同步调整观察窗、字幕避让、音效和按镜定位的提示。例如新切点 F56 将原先从 F48 开始的后镜缩短了八帧。用逐帧查看确认动作最早可辨的一帧仍落在原句旁。采纳会核对粗剪计划、选中代理片和原件的版本，正式导出继续按登记的实拍原件校验。仍在工程中编排的 Remotion 或场景组件从本镜第 0 帧起剪，按命令返回的 `regenerate_after_prepare` 镜号在新时间线上重建。用临时总混或中段 WAV 时，从相应节目起点截取，开声对准一个口播词或现场动作。

**改稿或重新留停顿。** 长句没给观众时间看清时，先改原句、`line.pause` 或连续录音。生成新的整片旁白导轨，再按它与画面重新比较。

```sh
"$PY" "$SK/scripts/prepare.py" --guide-dir qa/voice-02
"$PY" "$SK/scripts/screen_plan.py" rough-02.json --roughcut qa/full-02 --audio qa/voice-02/audio/narration.wav
"$PY" "$SK/scripts/prepare.py" --guide-dir qa/voice-02 --adopt-roughcut qa/full-02/receipt.json --plan rough-02.json --version first
"$PY" "$SK/scripts/prepare.py"
```

上例的计划在工程根目录，从 F0 覆盖新导轨的完整帧数，包含原有画面镜号。仅移动一刀且录音没改时，可继续对原来那一段滚剪。采用有声粗剪后，如果重建的配音发生变化，正式准备会要求重新对照该录音。


### 配音与正片

有整条真人或数字人口播素材时，先按[连续原声和词时钟](explainer-presenter.md)准备工程；采用参数化图形或主持镜头时，按[画面场景命令](explainer-scenes.md#按实际帧数制作)在 prepare 和 export 之间生成对应 shot 的底片。素材抠像、动作拟音、动作对照见[素材与运动](wallstreet-craft.md)。

新科技、财经和产品解释片在本期空目录运行 `"$PY" "$SK/scripts/prepare.py" --new-edit explainer`；支持四种片型骨架：`news`、`explainer`、`story` 和 `teardown`，自带 `adaptive-kinetic` 字幕、`broadcast` 人声润色与 2.4 kHz 音乐让位默认值。抓取素材后可跑 `"$PY" "$SK/scripts/media.py" qc assets/` 排查跳切、虚焦与黑边；配音/导出前可跑 `"$PY" "$SK/scripts/studio_pipeline.py" preflight --project . --write`（或 `prepare.py --preflight` / `--audit`）一站式检查钩子、多音字、焦点避让、肤色保护调色与节奏，导出后跑 `"$PY" "$SK/scripts/studio_pipeline.py" postcheck --project . --media <候选.mp4>`（或 `review.py --critique`）生成统一 `qa/director-report.json`。完整新工艺参数见[画质、转场、音画包装与节奏工艺](production-craft.md)。自编项目生成器可直接沿用下面的节奏字段。实际制作时用本期素材和台词替换示例。

```json
{"size":[1920,1080],"fps":30,"narration_pace":"quick","shots":[
  {"id":"reveal","visual":"assets/take.mp4","in":1.5,"fit":"cover","lead":0.3,
   "lines":[{"text":"开关按下后，灯才亮起来。","pause":0.25}],"tail":0.5}
]}
```

新制作的新闻或解释片先在 edit.json 声明 story_intent，如 `{"kind":"news","main_point":"末班车发车时刻提前十分钟","viewer_value":"晚下班乘客能从新时刻表找到备选换乘"}`。配音前运行 `"$PY" "$SK/scripts/prepare.py" --read-script` 得到纯口播稿；编辑定位句子可加 `--line-ids`。按[读稿与返修](video-writing.md#配音前通读一遍)改到观众能听明白本段发现，再运行 prepare.py 生成见秋配音。语速设定、逐句 A/B 试听与实测报告见[口播节奏](narration-pace.md)。参见[见秋旁白和独立试音](voicecasting.md)。用旧工程搭建新片时，移除指向旧片旁白的 line.audio、旧 voice 和仅适用于旧音色的 speed。主动沿用的本片录音可保留。每句 text 用于字幕和旁白，可加 spoken 解决缩写或数字读法；多音字须先跑 `"$PY" "$SK/scripts/pronunciation_review.py" --project .` 检查 ZipVoice 词表切分，命中跨词误切或单字回退时写 `line.pronunciation.phones` 走定向词表（不要用裸 `line.audio` 绕过定向词表），详见[见秋旁白·多音字](voicecasting.md#多音字短尾音与最终成片)；可加 audio 指向现有真人或已定向审听通过的单句录音。长句按自然呼吸分成多句。每镜唯一 id。lead、tail、pause 和视频入点 in 的单位为秒。没有旁白的镜头用空 lines 与正的 tail。有意整镜黑场时可设置 allow_black 为 true，保留设计理由。如果整片没有旁白，改用适合无声或纯音乐作品的混音流程。正片 size 必须满足 16:9。可另配置 music、effects 两条从片头起对齐的音轨及 music_gain、effects_gain（线性倍数）。音乐需要避开原话或一次操作反馈时，加按镜头定位的 [`music_cues`](screen-and-mix-review.md#把音乐起落对到动作或一句话)。混用相机、屏录和照片时按[镜头色彩说明](output-color.md)核对源解释。按主题写项目自己的生成/剪辑命令。音轨及自制动画的长度从 timeline.json 读取。字幕默认 Heiti SC，可通过 font 指定当地可用字体。

```sh
"$PY" "$SK/scripts/prepare.py"
# 此时生成原创镜头及配乐，或者选择长度充足的媒体。写好 sfx.json 后运行 soundmix.py。
"$PY" "$SK/scripts/export.py"
```

prepare 按句测量配音，生成 narration.wav、audio/captions.srt 和各镜头的帧区间。export 对齐画幅（支持 `cover`/`contain`/`backdrop`/`paper`/`ink`、原生 `camera`/`camera_drift` 运镜、`speed` 变速、全局/单镜 `grade` 与 `finish` 调色质感、17 套原生转场含 `match-cut` 与顶部 `chapter_bar`）、烧录字幕（`adaptive-kinetic`/`editorial`/`clean-shadow`/`kinetic-word`/`plate`/`outline`）、混音并完整解码，随后将 MP4、字幕、封面和时间轴放入独立的候选目录，打印媒体路径并打开本轮团队审片。配乐避让支持 `"music_ducking": "sidechain" | "gentle" | "firm" | "prebalanced"` 或自定义字典，并可开 `voice_polish`、`music_vocal_pocket`、`micro_silence` 刻意静音断拍与 `music_fade_in/out`。可用 `cue_score.py score.json --beats-out qa/beat-grid.json` 导出强拍网格，并用 `sfx_scaffold.py --project . --write` 快速起草带提前 2–3 帧预触发、Re-hook 低频蓄势与防疲劳密度上限的转场与揭示音效。旧工程默认保持兼容。用[同响度的编码样片](screen-and-mix-review.md)实查两种混音。已有正式片可继续播放。工程已有各画幅的专门设计时，传 `--prepared-covers 目录` 将这三张封面一起送审。普通镜头可直接叠加[分层 PNG 动画](kinetic-evidence.md)，由 export 依镜内帧合成并缓存。整镜取景、匹配剪辑或复杂三维动效也可由项目生成器先渲染成 visual。想比较段落快慢时，在 screen-plan.json 的段首 cut 写 `section`，导出后看 `pace.sections` 与 `pace.editorial_diagnostics`。

运行 `"$PY" "$SK/scripts/review.py"` 查看当前候选的接触表、黑场、静音和字幕信息。需要判断读数、表情或操作反馈能看多久时，在分镜登记[关键画面的观察区间](picture-edit.md#关键画面的可见时长)，从最终编码片复看。用 `--media 路径` 指定历史候选，审片工具读取随该版保存的时间轴和字幕。裸媒体可用 `--timeline`、`--sidecars` 指定配套工程。能使用本地 SenseVoice 时加 `--asr 模型目录`；逐句回译适合定位需复听的词。按[审片团队与定点修改](editorial-review.md)执行独立观片、会审、返修与签发。publish 将签发的字节、字幕和三种封面写入 out。固定时长的镜头、字幕和音频调整走局改；改时长时才重新计算受影响的时间区间和相应依赖。

准备投稿时可从本机签发的横屏文件继续生成[投稿稿件与来源附件](social-handoff.md)；需要竖屏副本时，按[逐镜重排](portrait-cut.md)用同一条时间轴合成，另行审看。

## 快审与返修

从工程目录运行 `studio_pipeline.py catalog` 可查配音、声音、视觉、渲染、质检五个域的脚本和命令。下面这些是 v3 在标准制片流程中的接入点。

| 制片阶段 | 如何使用和核对 |
| --- | --- |
| 写稿、配音 | 台词写 `[pause 0.35]` / `[停顿 0.35]` 或 `*重点词*`。运行 `prepare.py` 得到旁白、无标签字幕和时间线。科技词发音建议进入预检行动项。用 `voice_mood_pack.py status` 校验五组参考音，以 `import --mood … --audio … --text …` 导入对应实录。 |
| 镜头与声音设计 | 照片视差按下文绑定。B-roll 接力用 `broll_router.py generate "镜头描述" --chain-from assets/prev.mp4 --out assets/next.mp4 --max-retries 1`。默认先试一次，质检失败后可再生成一次。`--max-retries 0` 只试一次。通过后把结果写入目标镜头的 `visual`。 |
| 拟音与配乐 | `studio_pipeline.py preflight --write --apply-sfx` 按真实画面切点写入实录拟音。预检改稿后重跑 `prepare.py`。以最终时间线运行 `music_stems.py init-plan --pack warm-editorial` 和 `music_stems.py mix stems-plan.json --out audio/music.wav`。计划中的 `fills` 与 `outro_cadence` 控制过门和终止式，工程的 `music` 指向生成的 WAV，`voice_polish` 可选 `broadcast` 或 `studio-master`。 |
| 定剪和返修 | 预检报告的 `visual_diversity` 列出各媒介时长、香农熵、连续同类镜号与超八秒静停风险。候选的 `qa/director-report.json` 在 `pace.visual_diversity` 保留该剪辑版的统计，`measured_quiet_spans_over_8s` 给出实际成片上的静停帧段。两个入口都可加 `--require-visual-clear`，需要复核或尚未测量时写完报告后以状态码 2 退出。 |
| 竖版 | 用 `portrait_cut.py portrait-plan.json --out out/portrait.mp4` 读取选定母带和逐镜构图计划。横屏动态素材需要自动跟焦时设置 `auto_focus: true`，运动轨迹和硬切边界进入构图回执。 |


在进入镜上设置 `"j_cut": 0.30` 可把画面切点推后，`"l_cut": 0.30` 可把切点提前，两者择一。在 30 fps 下，0.30 秒对应 9 帧；相邻镜长会限制实际位移。运行 `prepare.py` 后，在 `timeline.json` 看 `cut_shift_frames` 和实际画面帧区间。口播、词时钟和 SRT 仍以录音时间定位。照片镜头需要前后景视差时，设置 `parallax_25d.source` 与镜头输出 `visual`，在定时后运行 `parallax_25d.py render --project . --shot s2`。渲染收据 `<visual>.parallax.json` 绑定照片、配置、帧数和输出。缺少收据的旧视差镜头也用同一命令补建。采用显式粗剪选段后，画面以已选帧区间为准。

从工程目录渲染快审版和供技术审片的完整候选。

```sh
"$PY" "$SK/scripts/export.py" --proxy
"$PY" "$SK/scripts/export.py" --stage-only
# CANDIDATE 取上一步输出的 candidate.mp4 路径
"$PY" "$SK/scripts/studio_pipeline.py" postcheck --project . --media "$CANDIDATE" --backend offline
```

代理片在 `out/proxy-preview.mp4`。`picture_reused` 表示本次是否复用已经校验的镜头画面和转场。字幕、角标、配乐与拟音会按当前工程重新合成。正式候选的审片结果在该候选目录的 `qa/director-report.json` 和 `qa/critique-packet/tickets.json`；单独传入媒体时，结果位于工程 `qa/`。导演报告的 `effective_backend` 记录实际使用的后端，`backend_attempts` 记录尝试及失败原因。使用 DeepSeek 时选 `--backend deepseek`，默认模型会收到故事板图像。文本模型可依据本地提取的画面、运动和字幕数据审片。新生成的返修单绑定审看的时间线，正式候选还会核对渲染素材。原路径换图或变更声音后要对新候选重新审片。自动缩短尾停前会核对静音是否确实落在口播尾部，添加转场前会核对两侧可用帧数。内置离线审片用 `--backend offline`，本机模型用 `local-vlm`，Gemini 用 `gemini`。故事板覆盖至最后一个不足一秒的片段。新导出的候选带实际字幕像素框，旧片只有预计字幕区时标记人工复核。[DeepSeek 模型能力](https://api-docs.deepseek.com/quick_start/pricing/)

```sh
"$PY" "$SK/scripts/multimodal_critique.py" patch --project . --tickets "$TICKETS"
# 采纳机械修改时加 --apply，然后重跑 prepare.py
"$PY" "$SK/scripts/export.py" --patch-shots s4 --patch-from "$CANDIDATE" --out out/patched.mp4
```

工单改变片长或音轨时，重跑 `prepare.py`，重建相关板片、配乐和拟音，再用 `export.py --stage-only` 生成完整候选并重复 `postcheck`。确定进入正式会审时，运行 `editorial_review.py begin "$CANDIDATE"`。已有正式轮次的工程在该轮裁决后加 `--from 旧候选SHA` 登记修订。直接运行 `export.py` 会在完整导出后打开正式审片轮次。

局部画面修补适用于时长、镜序与终混均相同的工程。修改 edit.json 后先重跑 prepare.py，让时间线绑定本次改动。沿用同一路径替换画面素材时可直接局部导出。修补会重新编码选中画面和受影响的相邻转场，并校验区间外画面和原音轨。改了配音、时长或全片包装时，重新完整导出。旧母带在所需边界缺少独立关键帧时，也使用完整导出。

## 文章与全文朗读

从同一份 article.json 生成可点击来源的文章及全文音频。文件含 title、byline、date、sources 和 sections。标题含数字或专名时可用 spoken_title 指定读音。sources 为键到 [来源名称, URL] 的映射。每段含 sentences（由 text、可选 spoken 和句后 pause 秒数组成），可选 heading 和 sources（引用键数组）。标题、段标题和每个正文句都会朗读；spoken 用于把数字、字母和人名写成读音。音频与该 HTML 放在同一目录时可直接点播放。

`"$PY" "$SK/scripts/article_read.py" article.json --out out --takes audio/article`

未改的句子按文本、读音、语速、声线和 WAV 哈希复用。逐句 WAV 存在 audio/article，语音清单与声线哈希见 voicecast.json。较短的视频口播另写 edit.json。逐句回听数字和专名；把编辑稿、播读稿与网页来源一起留在项目中。

## 制片中断与候选来源

同一工程的 prepare 与 export 串行。中断后重新运行正常入口，它先恢复成套的配音、字幕和时间轴，再检查实际用到的输入。制片时改了脚本，重新 prepare；只改画面或分轨，正常重新 export。已处理的本地镜头通过内容及解码校验后可复用。`qa/production-run.json` 记录阶段、耗时与复用情况。外部字幕可用 export 的 `--captions` 明确指定。旧时间轴能检查的来源范围会标记为 legacy。

新候选保存渲染与暂存时的 skill 版本、字幕封面和交付的来源文件。默认从工程搜 sources.md、visuals-sources.md、sound-sources.md、credits.md；自定义附件可在 edit.json 的 delivery_credits 列项目相对路径。审片后交付同一组文件。复制或导出正式片时，从同一签发版本取齐字幕、封面和署名。搬迁完整 out 目录时保留隐藏目录 `.release-versions`；脚本批量读取多份交付文件时先取 `review_bundle.released_snapshot(media).parent` 锁定同一代。
