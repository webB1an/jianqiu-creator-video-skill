# 把真实素材、运动和声音排成一段

## 从开头找到贯穿物

做财经、科技或产品解释，先把这一段的问题缩成观众能指给别人看的东西。例如「多耗的电都去了哪里」可以沿着电表读数、实际运转的部件、两次测试的差额三步走。在分镜上写明本镜引入的证据、观众要看清的动作、留给下一镜的对象和位置。先给能改变判断的那一帧，片尾再回到它，把答案补上。比较开头时换表达机制，例如直接给极端的读数和它的日常尺度；让两件反差很大的实物同框；从结果倒推；在几张真实报道中钉住同一个词。时间悬念、原话和可验证的具体比喻也可以担当开场。只在有多个可行选项时做内部对照，优先接得上下一条证据的方案。[原库的导演思路](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/thinking.md) · [原库的开头设计](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/opening.md)

若一组标题的作用是交代「多个来源都在讨论」，把关键词固定在同一位置，允许外围更新得快；首次出现的新证据需要给全貌、单位或原句留下完整阅读时间。开头和结尾可使用同一实物的两个状态。一段里让一项运动承担主要含义，画框、人物和文字的位置变化都围绕它安排。切回旧空间时复用物件和方位，减少观众找路的时间。[原库的画面规格](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/style.md) · [逐镜复盘](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/lessons.md)

## 找到动作发生的原段

在个人库、官方素材页、发布会原播、公开档案或本期许可覆盖的来源中，按具体设备、人物、地点、事件和可见动作找画面。用媒体和许可页面登记作者、来源、使用条件、源时码。实物很难辨认时，依次看较近机位、较高分辨率的原件和主体出画前后的原始帧；已有暖场片头、字幕或台标遮到关键动作时另挑一段或另找出处。下载到了节选，须记录它在原片中的时间偏移。使用本机 `media.py search / fetch` 和[个人素材库](media-library.md)；有可下载授权的平台原视频，也可用已安装的 yt-dlp 取一个指定时间段。留存网页引用可用[真实截图与逐行定位](screen-and-mix-review.md#把网页原句做成证据镜头)。[原库的素材流程](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/sourcing.md)

### 检索公开视频并取一段授权原片

在项目目录使用[本机制片 Python](run.md) 的 `"$PY" -m yt_dlp --version` 确认工具位置。新环境可用 `"$PY" -m pip install yt-dlp` 安装到同一 venv。把动作、现场和对象组成检索词。先看候选所属频道、真实题名、日期、时长和首尾能见的动作。以下第一条只列候选，第二条读取已选页面的来源信息。平台的数据字段有时为空，实际权利状态以原始发布页或书面授权为准。

```sh
QUERY='设备型号 校准动作 发布会'
"$PY" -m yt_dlp --ignore-config --flat-playlist --skip-download \
  --print '%(id)s | %(channel)s | %(title)s | %(webpage_url)s' "ytsearch8:$QUERY"
"$PY" -m yt_dlp --ignore-config --no-playlist --skip-download \
  --print '%(channel)s | %(upload_date)s | %(duration_string)s | %(license)s | %(webpage_url)s' -- "$SOURCE_URL"
```

将已核实可下载使用的页面填入 `SOURCE_URL`。原始发布方另供直链时，优先按本片 `assets.json` 和 `media.py fetch` 保存授权原件。以下示例取平台源片的 00:01:12.000 至 00:01:18.500，FFmpeg 在截点重编码以减少关键帧拖尾。文件名含原始 ID 和申请的源区间，旁边保存信息 JSON。根据页面与本期许可，把作者、来源、实际用到的时间范围和使用条件写进工程来源清单。[yt-dlp 下载区间与输出字段](https://github.com/yt-dlp/yt-dlp#download-options)

```sh
"$PY" -m yt_dlp --ignore-config --no-playlist --no-overwrites --write-info-json \
  --download-sections '*00:01:12.000-00:01:18.500' --force-keyframes-at-cuts \
  -f 'bv*[height<=1080]+ba/b[height<=1080]' \
  -o 'assets/sources/%(extractor_key)s-%(id)s-%(section_start).3f-%(section_end).3f.%(ext)s' \
  --print after_move:filepath -- "$SOURCE_URL"
```

用末行打印的实际路径读取文件。先用 `ffprobe -v error -show_streams -show_format "$CLIP"` 核对解码流、起始 PTS 与时长，再用 `reference_scan.py "$CLIP" --out qa/source-window --every-frame` 查首尾及动作帧，在原发布画面对照第一个能辨认的接触或状态变化。将该帧的**节选显示 PTS** 和**原片对应时间**同时登记。其他本镜帧按「原片确认点 + 当前帧 PTS − 节选确认点 PTS」换算。末帧可见动作和原声波形要与另一端吻合。平台关键帧、节选重新编码和缓冲可能影响头尾；拿得到全长授权源时，优先从原文件按原生显示帧切取，准确保留实际源 PTS。

### 把确定的源段交给画面场景

先用 `reference_scan.py --every-frame` 逐帧确认真实镜头的边界；多机位用 `action_takes.py` 在同一动作点比较。实拍、主持人房间或部件需要交给浏览器场景时，可在制片工程中运行下面两行。第一行取 60 至 119 的源显示帧，转成项目的 30 fps。第二行在同一套帧上分出前景，便于把标签插在实物与环境之间。

```sh
"$PY" "$SK/scripts/clip_frames.py" assets/bench.mp4 assets/clips/bench --first 60 --stop 120 --fps 30
"$PY" "$SK/scripts/subject_matte.py" assets/clips/bench assets/cut/bench --vision --fps 30
```

帧序列默认输出 1920×1080，按源显示方向、像素宽高比和目标比例居中取景。可用 `--crop X Y WIDTH HEIGHT` 传入[选材阶段确定的裁切框](live-action-editing.md)，或用 `--size WIDTH HEIGHT` 指定画幅。输入与输出帧率不同时按真实源时间戳取当时正在显示的帧，末尾可能补齐不到一个项目帧的时长。工具打印场景可用的 `dir/frames/fps/ext`，目录中 `frames.json` 记录每张图对应的源帧号、PTS 和源色彩解释。图像经见秋的色彩归一化后转换为浏览器使用的 sRGB，导回正片时再走项目的 Rec.709 输出。如果场景里也需要同步实录原声，按相同源入点、范围和项目时钟取音频。

## 从实拍分出前景

`subject_matte.py` 和下方运动诊断使用 OpenCV。在跟随本机安装说明配置的制片 venv 中可直接运行；在其他机器首次需要时，使用该环境的 `"$PY" -m pip install opencv-python-headless`。帧序列和遮罩都按显示顺序编号，同一镜头的机位、画幅和帧数要对应。

| 原件条件 | 取前景的方法 |
| --- | --- |
| 当事人、设备或产品在有纹理的真实环境中 | macOS 14 及以后可用 `--vision` 调用系统的前景实例分割，随工具编译的小程序需要本机 Command Line Tools。若自动选中多个东西，用 `--box 0.2 0.1 0.8 0.9` 框住贯穿这整段的对象，或用逐帧蒙版。矩形四值依次是归一化的左、上、右、下。 |
| 干净的绿幕或蓝幕 | 用 `--key green` 或 `--key blue`，通过 `--key-range 12 90` 调整「前景保留」和「背景完全透明」的颜色差门限。主体自身含有幕布色时可换外部蒙版。 |
| 原画已经带 alpha、需要指定一件物体，或精细处已经手工描过 | RGBA 序列用 `--alpha`；用 `--masks assets/masks/bench` 读与源画同名、同尺寸的灰度 PNG，白色表示保留。原片的薄线、发丝、眼镜腿可在这里逐帧修。 |

输出保留房间底片的完整像素坐标，每张 PNG 可与原画无缝叠放。逐帧 `bounds`（右、下不含）、上部轮廓横向中位数 `cx`、顶边 `top` 以及 `possible_cut_frames` 都写入 `box.json`。固定机位的背景与主体共用取景；换底用同一主体叠到新空间上；做跟随小窗时根据 `cx` 平滑地从原位置移到新位置。空帧的 `bounds` 为 null，两侧位置用于插值跟踪。先检查疑似换镜的第一格和最后一格，确认没有另一个人、空窗或被截断的手势。

把同一帧依次放到浅底、深底和饱和对比色上。只有软边闪动时才平滑，主体实心像素保留。离散小块用 `--min-component 80` 按源图像素面积筛除；镜框、耳麦等细而独立的部分需要保留时减小面积。色边可用 `--despill blue` 或 `green`，对有污染的软边借近处实心颜色补色，保住主体本色。宽轮廓仍裹着原底时可用 `--inset 1` 收一像素再在两种底色上核对。细物轮廓以实际像素和原片形状为准。[原库的抠像与去溢色实现](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/scripts/matte_despill.py)

## 动作声落在动作上

镜头平移可以给较轻的掠过声，数值持续累加可以有渐起的气流，落定时给较低的短击。档案纸页或机械动作优先听现场原声，需要补声音时保留材质与发力方向。相邻画面连续变动时挑一个真正推进理解的动作给重音。其余可以留给原声或音乐底，让停顿和安静也能表达结果。合成器可直接生成四类可复建的声音，不依赖下载。

```sh
"$PY" "$SK/scripts/design_sound.py" rise --key counter-rise --seconds 0.42
"$PY" "$SK/scripts/design_sound.py" land --key counter-stop
```

同样支持 `pass` 与 `tick`。工具在当前工程 `assets/sfx/<key>/` 写工作 WAV、合成参数、波形校验值，打印 `key/trim/anchor`。在 `sfx.json` 里沿用这三个值，加上 `shot`、`offset`（镜内动作帧除以 fps 的秒数）、`event`、`peak_db` 和 `pan`。想让渐起声先结束时，按其尾音和动作落点反推 `offset`。运行 `"$PY" "$SK/scripts/soundmix.py"`，将生成的效果轨设为本期 `effects`；真人原声、旁白、配乐、效果保留各自可调电平。人声让位及必要时的音乐抬落走现有 `music_cues`，使用实际成片核对峰值、手机回放和词尾。[声音与画面对位](screen-and-mix-review.md) · [原库的拟音布局](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/audio.md#L30-L48)

## 判断画面究竟怎样动

开剪先看各镜承担的事实、是否沿用上一镜的位置与对象。粗调之后对照同一段节目范围分析。下面的 `--first / --stop` 是每份输入自己的原始显示帧号，若两版帧率或起止不同，请分别给各自范围。整体平移和缩放、主体自己移动、随镜头移动的局部细节可分别读到。默认取画面纵向的 5%–81%，可用 `--roi 0.12 0.73` 避开黑边、片头角标和独立字幕。

```sh
"$PY" "$SK/scripts/motion_field.py" edit-A.mp4 edit-B.mp4 --first 30 --stop 160
"$PY" "$SK/scripts/motion_field.py" edit-B.mp4 --details --output qa/motion-field.json
"$PY" "$SK/scripts/motionstudy.py" edit-B.mp4 --start-frame 100 --frames 18 --out qa/seam.jpg
```

工具在连续帧上追踪纹理点并反向检查配对，拟合平移、转动与缩放，记录剩余运动。时间比例按真实 PTS 差加权。较长的 `hold` 对回观察窗检查是不是在读结果；`camera_and_local` 是局部轨迹和整体机位有明显差异，可能来自主体动作、遮挡或视差。`unresolved` 多见于低纹理、交叠或快速转场，需要回原帧看。切换候选给出需检查的原帧号。单一镜头或作者没有必须达到的运动配额。[原库的运动诊断](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/scripts/motion_check.py)

逐帧看动前、速度最快处、落稳和第一帧可读结果。留意抠像首格露出其他人、推移交接处闪黑、两侧方向反转、虚化拖过停读窗口和字被前景挡住。按 `footage_audit.py` 回看实际素材覆盖，用正片常规审片检查首尾画面、署名、混音和显示尺寸。对照上一部时找重复出现的手法次序，换掉失去叙事作用的一镜。在实际工程里按镜号记录改动和复核结果。[原库的审片方法](https://github.com/HeyChengC/wallstreet-explainer/blob/f39845abd45dab3c9ce62587aa5d02f6afe82612/references/review.md)
