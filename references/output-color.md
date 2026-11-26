# 色彩匹配与实际交付

科技产品、人物和网页放在一起剪时，先分别选一张能看清主体的基准帧。用相邻镜头里的同一材质或中性物检查白平衡、黑位、高光层次，再决定整场的冷暖。设备的指示灯、图表的组别色、屏幕上的警告色有具体含义。比较两种成像处理时，两边用相同的缩放、显示处理和色彩转换，留存未调原帧。

常规横版与竖版都交付视频范围的 SDR Rec.709。导出器逐镜读取色彩矩阵、原色、传递函数与范围，在叠加角标或字幕前归一化，完成后再检查实际 MP4 的四项标记。带内嵌 ICC 的静态照片按其配置转为 sRGB。已标明 PQ 或 HLG 的输入通过带 zscale 的 FFmpeg 在线性光中映射为 SDR；涉及 HDR、高光层次或显示器性能的结论另核原始显示条件。作者已有指定的相机 Log 变换、杜比视界处理或成片风格时，可直接提供按相应流程处理好的 SDR 镜头。

旧片缺少部分标记时，保留已知字段，补全项记入横版候选的 `technical.json → color.shots`，竖版命令在输出 JSON 中列出相同信息。常规无标签图形按 sRGB 解读，普通无标签 YUV 视频按标准清晰度或高清来源估计矩阵，常规视频默认使用视频范围。已知来源的解释优先于推定。横版 `edit.json` 或竖版计划的相应镜头可用 `"input_color":{"color_space":"smpte170m","color_primaries":"smpte170m","color_transfer":"bt709","color_range":"tv"}` 覆盖缺项或纠正错标。各字段采用 ffprobe 命名，范围为 `tv` 或 `pc`。局部返修 `render_window.py` 接受同样字段的 `--input-color` JSON。

同个产品拍完之后，把导出文件里实拍的白色丝印、暗部纹理和品牌图形各回看一次。对比横竖两版时选择同一时刻和同一个小区域；相同的标签只能证明文件声明一致，实际图像的颜色与明暗仍要看像素与屏幕。[FFmpeg 色彩变换及色调映射文档](https://ffmpeg.org/ffmpeg-filters.html#colorspace)

## 把镜头间的明暗与饱和度接起来

在同一场景选一个基准镜，先把两镜的共有物放在同一显示尺寸看。若下镜的中灰偏暗，在横版 `edit.json` 或竖版计划的对应 shot 上写 `"grade":{"gamma":1.06}`。若暗部和高光同时拉得太开，用小于 1 的 `contrast` 收回层次。若同一件物品在下镜显得过艳，可单调 `saturation`。按同一材质微调后连播切点，再看肤色、白纸和暗处纹理。比较原件本身的颜色或亮度时，两边使用相同处理。

`gamma`、`contrast`、`saturation` 的中性值均为 1。`gamma` 大于 1 提亮中间调，`contrast` 围绕信号中点调整对比，`saturation` 为 0 输出灰度。另支持 `look`（`editorial-warm`、`tech-cool`、`archive-sepia`、`cinema-neutral`、`high-contrast-doc`、`night-slate`）、`exposure`（`-3..3` EV 线性曝光）、`temperature` / `tint`（`-1..1` 保亮度色温色调）、`shadows` / `highlights`（`-1..1` 暗部与高光层次）、`vibrance`（`-0.6..0.8` 保护高饱和色与肤色的自然饱和度），以及 `.cube` 3D LUT（`lut` 与 `lut_strength`）。可在 `edit.json` 顶层写 `grade` 统一全片实拍基调，数据/动效生成镜默认跳过，单镜可写 `"grade": false` 豁免。处理在源色彩归一化后的 10 位视频范围内进行，早于画面裁切、PNG 图层、字幕与角标。光学质感 `finish`（`vignette`、`grain`、`sharpen`、`bloom`、`blur`）在构图与运镜之后、图层与字幕之前生效。横版候选在 `technical.json → color.shots` 记录采用值。用未经调色的同场底片局部返修时，给 `render_window.py` 传相同的 `--grade` 和 `--finish` JSON；已有调色的画面直接沿用原色。

## 用共同中性物校正偏色

同一场景两机位拍出的白纸、灰卡或无色外壳一青一黄时，选一块有纹理且远离反光和阴影边缘的共同区域。选两个机位中材质照明可比的帧。用下例分别测待校正角度和基准角度。帧从每份原件的第一幅解码图像起算，区域用按旋转方向摆正后的像素 `x y width height`。

```sh
"$PY" "$SK/scripts/color_reference.py" angle-b.mp4 angle-a.mp4 \
  --source-frame 38 --source-box 240 128 52 36 \
  --reference-frame 29 --reference-box 211 149 48 34 \
  --evidence-box 362 150 22 18
```

输出 `grade.white_balance.sample` 和 `reference` 为源色彩归一后、按 Rec.709 传递函数编码的全范围 RGB 中位数。把该 `white_balance` 对象合入镜头的 `grade`，保留本镜原有的对比度、伽马或饱和度设定。来源需要手工解释时，采样分别传 `--source-color`、`--reference-color`，与镜头的 `input_color` 保持一致。实现在线性光 RGB 中将样本色度对到基准，保持这块中性物的亮度，再作本镜明暗修正。横版、竖版及接受 `--grade` 的局部返修共用这套处理。

`corrected_patch` 可核对校正后的中性物。基准较亮或较暗时两者亮度仍会有差别。用 `--evidence-box` 指定指示灯、样品或肤色，查看颜色在校正前后的值和新增通道触顶、触底比例，再连播同场切点核对。保护品牌字色的做法是先校正干净底片再合成图层。已把图卡烧进底片的镜头应在底片生成时校正实拍层。颜色或亮度本身是论据的对比镜头，两侧沿用测量条件和同一校正规则。
