# 复用 Remotion 动效与镜头组件

## 先找现成组件

确定这镜让观众看懂什么，再用动作去找组件，例如“两个真实数同轴比较”“说到这个词时刷亮”“右边画面推满屏”。先在[本地预览网页](../assets/remotion-library/index.html)查看模板，按[命令与分镜示例](remotion-library.md)换入真实数据和媒体。目录未覆盖的动作再查官方 Elements。计数、柱图、折线、原文标记、字幕高亮和画中画都可以在官方目录预览。核对效果是否保留真实的数值关系、素材取景和停读时间。合用就直接复用，替换本期素材、字体、颜色和时钟。跨镜的推移、擦除等用下方官方转场。两侧取景能用现有 `EvidenceCut` 的，直接传入两份原件。[Remotion Elements](https://www.remotion.dev/elements) · [数字计数器](https://www.remotion.dev/elements/data/number-counter/) · [水平柱图](https://www.remotion.dev/elements/data/horizontal-bar-chart/) · [原文高亮](https://www.remotion.dev/elements/text/news-article-highlight/)

在本期可编辑的 Remotion 项目根目录运行 Elements 命令。项目可从官方的 Blank 模板起步，包含自己的 `package.json`、入口和已注册的合成。下例假设目标合成 ID 为 `Counter`，实际运行时填写本期 ID。第一条命令预览将增加的依赖和改动的文件，第二条引入同一份官方组件。[Remotion 起始模板](https://www.remotion.dev/templates)

```sh
R="$HOME/.cache/creator-video-motion/remotion"
ELEMENT=https://www.remotion.dev/elements/data/number-counter/
"$R/node_modules/.bin/remotion" elements use "$ELEMENT" --composition Counter --dry-run
"$R/node_modules/.bin/remotion" elements use "$ELEMENT" --composition Counter --yes
```

Elements 会把可编辑的源码带进本期工程。用本期数据替换示例值，让关键变化落在 `timeline.json` 的实读帧，保留用户需要看的落稳时长。字体和第三方输入按项目实际准备。品牌字幕走后期时，底片给字幕留位置。用 `remotion still` 检查动作开始、中间和结束处。渲染干净镜头后把文件填入对应 `shot.visual`，由原制片流程合成声音和字幕。重建命令、选用的组件地址和项目依赖跟该镜保存。

数据镜头要在停读区核一次准确的值。本机核验的官方 Number Counter 示例目标为 24,813，原动画停在了 24,789。遇到这种情况，沿用组件里的滚动动画，从本片设定的落稳帧起直接显示准确终值，再逐帧复核数字和单位。

单个现成组件只覆盖部分动作时，把分镜拆成先后发生或同时可见的几层，组合已有的本地组件、官方组件或专用场景。例如合同镜头先沿原页推近到金额，再在口播读出金额时划亮原文。让取景和标记共用原页坐标，用同一条实读时间轴安排推近、划亮和停读。先后发生的动作可用 `Sequence` 安排入点，需同时可见的图层在同一合成内叠放。需要实物遮挡时可复用[主持人房间场景](explainer-presenter.md)的遮罩方法。组合后若还缺少从旧选框移到新选框的动作，就只为选框边缘补插值。分镜备注写下实际复用的组件和补写的部分。[Remotion 局部时间轴](https://www.remotion.dev/docs/sequence) · [Remotion 插值](https://www.remotion.dev/docs/interpolate) · [SVG 路径揭示](https://www.remotion.dev/docs/paths/evolve-path)

## 两段素材之间的官方转场

本机 `motion_project.py remotion` 可以把两份原件接入 `@remotion/transitions`。下面的例子以 30 fps 做左推，前一段占 54 帧，后一段占 48 帧，中间共用 12 帧，合成长度是 90 帧。两段素材都要覆盖用于过渡的区间。把这 90 帧对到同一个节目时间窗；底片中已有这次转场，主剪在底片外沿按实际分镜衔接。[Remotion 官方转场](https://www.remotion.dev/docs/transitions/)

```json
{
  "width":1920, "height":1080, "fps":30, "frames":90,
  "sources":[
    {"file":"assets/before.mp4","in":1,"frames":54},
    {"file":"assets/after.mp4","in":0,"frames":48}
  ],
  "transition":{"type":"slide","direction":"from-right","frames":12}
}
```

源路径相对这份 JSON。可选 `slide`、`wipe`、`fade`、`iris`；后两种直接填类型和帧数。以下命令在本期工程运行，使用此镜独有的工作目录。第 48 帧落在过渡内部。

```sh
R="$HOME/.cache/creator-video-motion/remotion"
SHOW="$PWD"
SHOT_DIR=$(mktemp -d "$R/shot-XXXXXXXX")
"$PY" "$SK/scripts/motion_project.py" remotion "$SHOW/transition.json" --project "$SHOT_DIR"
cd "$R"
node_modules/.bin/remotion still "$SHOT_DIR/index.jsx" ShotTransition "$SHOT_DIR/F48.png" --frame=48 --public-dir="$SHOT_DIR/public"
node_modules/.bin/remotion render "$SHOT_DIR/index.jsx" ShotTransition "$SHOW/visual.mp4" --public-dir="$SHOT_DIR/public" --muted
```

如果本机默认 Chrome 启动超时，可从已有渲染环境查找 Chrome Headless Shell，给上面的 `still` 和 `render` 加上 `--browser-executable="$SHOT_BROWSER"`。本机可用 `find "$HOME/.cache/hyperframes/chrome" -type f -name chrome-headless-shell -print -quit` 找到它，把返回的可执行文件路径赋给 `SHOT_BROWSER`。

## 选镜头

当旁白同时引出两处可见证据，然后把问题落到其中一处，用双源取景。先按左右两格各自取景，到关键词位置再让分隔边缘移动，整个被选镜头连续播放到全屏。可把观众刚看过的人物、物件或手势留到下一镜。两个画面来自不同场合时，在来源清单分别登记时间地点；存在现场误认风险时，另选镜头或在首次出现时交代关键时空。不同时间拍摄的人物同框容易被误读成同一场事件。

当第二句话补上第一句话的证据范围，用逐层揭示。把两句话分别写成可核实的陈述，标签标明是谁说的，第二层的出场时点与实际口播的关键词对齐。原生模板在 960×540 的底部约 120 px 留给后期字幕。证据是一份可读原件时，可先给观众看原件，再决定是否需要这一条说明镜头。

## 双源取景

输入 JSON 用全片的帧率。左右源可分别为视频或照片。路径相对 JSON，或写绝对路径。源时间以秒为单位；取景对齐值在 0 至 1 之间，居中为 0.5。取景对齐会影响超出窗口的裁切部分。片段采样持续到各源最后一次可见的帧。画面尺寸支持横屏和竖屏，H.264 需要偶数边长。

```json
{
  "width": 1920, "height": 1080, "fps": 30, "frames": 90,
  "focusStart": 18, "focusEnd": 48, "focusSide": "left",
  "sources": [
    {"file":"footage/interview.mp4","in":4.2,"anchor":[0.62,0.45]},
    {"file":"stills/document.jpg","anchor":[0.45,0.32]}
  ]
}
```

本地 FFmpeg、ffprobe 和带 Pillow 的 Python 能直接输出静音 H.264 或指定帧的无损 PNG。全局字幕、配乐和连续环境声由主剪辑合成，避免转场时声音被截断。下列命令从 skill 目录执行。

```sh
python scripts/evidence_cut.py /path/to/split.json --out /path/to/visual.mp4
python scripts/evidence_cut.py /path/to/split.json --frame 38 --out /path/to/check.png
```

原生 Remotion 入口允许在 React 时间轴上调整几何、镜头和图层。每一帧的位置只依赖合成帧号，移动曲线使用端点速度与加速度均为零的五次平滑曲线。视频使用 OffthreadVideo 按源时基提帧。开始点会落在输出合成的帧格上，两个渲染后端的解码与压缩结果有细微像素差异。把需要用于下镜的最后取景与下镜第一帧并排核对。[Remotion 帧号](https://www.remotion.dev/docs/use-current-frame) · [精确取视频帧](https://www.remotion.dev/docs/offthreadvideo) · [局部时间轴](https://www.remotion.dev/docs/sequence)

首次安装各框架时使用独立的本地 npm 项目。此例的直接依赖固定版本，npm 产生的 package-lock.json 可随制作工程保存，重建时用 npm ci。项目目录放在对应 node_modules 的子目录。生成脚本在该目录放入自己的入口、已解析的参数和所用媒体。包内只需保留脚本和模板。

```sh
R="$HOME/.cache/creator-video-motion/remotion"
npm install --prefix "$R" --save-exact --no-audit --no-fund @remotion/cli@4.0.543 remotion@4.0.543 @remotion/transitions@4.0.543 react@19.3.1 react-dom@19.3.1
python scripts/motion_project.py remotion /path/to/split.json --project "$R/shot"
cd "$R"
node_modules/.bin/remotion render shot/index.jsx EvidenceCut /path/to/visual.mp4 --public-dir=./shot/public --concurrency=2
```

可向 remotion render 传 --browser-executable 选择本机 Chrome。Remotion 对个人及最多三人的公司提供免费许可，其他组织需依其条款核对使用资格。[Remotion 官方许可](https://www.remotion.dev/license)

## HyperFrames 双层说明

输入行分别给出标签和短句。短句适合一至两行，若换行使第二行靠近字幕，先缩短文案或调资产模板内的字号。duration 是时长，reveal 是第二行开始揭示的秒数。示例的文字只说明结构。

```json
{
  "duration": 2.4, "reveal": 0.95,
  "rows": [
    {"label":"媒体报道","copy":"报道里的说法"},
    {"label":"公开文件","copy":"文件实际确认的内容"}
  ]
}
```

模板将真实时长写入 composition 与各 clip，文字用暂停的 GSAP 时间轴定位，窗口注册在 window.__timelines。正式片中从旁白时间轴取揭示时点。HTML 不访问远程 CDN。GSAP 从本机 npm 缓存复制，中文采用渲染机器的系统字体。换机器重建时，检查汉字和换行。需要同字形的项目可自带有授权的字体，在模板里使用本地 @font-face。[HyperFrames 组成与时间轴](https://hyperframes.heygen.com/concepts/compositions) · [HTML 属性](https://hyperframes.heygen.com/reference/html-schema)

```sh
H="$HOME/.cache/creator-video-motion/hyperframes"
npm install --prefix "$H" --no-audit --no-fund hyperframes@0.9.55 gsap@3.15.1
python scripts/motion_project.py hyperframes /path/to/labels.json --project "$H/shot" --gsap "$H/node_modules/gsap/dist/gsap.min.js"
"$H/node_modules/.bin/hyperframes" lint "$H/shot" --json
"$H/node_modules/.bin/hyperframes" render "$H/shot" --fps 30 --workers 1 --output /path/to/labels.mp4
```

HyperFrames 0.9.55 声明 Node 22 或更高，本地 CLI 使用浏览器和 FFmpeg。框架采用 Apache 2.0，GSAP 使用其 Standard License，实拍、图片、字体的权利另按来源记录。[HyperFrames 官方仓库与许可](https://github.com/heygen-com/hyperframes) · [本地渲染说明](https://hyperframes.heygen.com/quickstart) · [GSAP 授权](https://gsap.com/standard-license/)

## 新样式怎样沉淀

每次落地一个可变参数和一个可以逐帧复查的小样。先以实拍检查主体在裁切过程中是否出窗，再查看起动、中途、落定的连续画面；只看首尾可能漏过中途挤压、二次裁切或文字提前。对照音轨验证揭示时点后，在本期工程记录素材归属、输入参数、渲染版本、抽查帧和实际入片时间。下期从新的问题、素材宽高比和旁白重算各时间点。

换一份素材或数据仍能解决同类镜头问题的 Remotion 动效，在本期[登记为本地模板并更新离线预览网页](remotion-library.md#把可复用动效加回本地库)。
