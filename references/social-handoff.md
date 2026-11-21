# 交付社交平台投稿稿件

先定本期要投的平台、首发画幅和观众。用同一集的标题、封面和前两镜做一次冷读。让首次观看者用自己的话说明作品提出的问题、第一份证据在哪里，以及看完能用上什么。手机宽度下难以指出左右两图的差异时，可将两档原图在同一位置、相同倍率切换。给实机或目标 App 草稿检查保留证据范围，分别记录界面遮挡、转码后的细字、静音时动作的含义和低音量外放能否听清数值。

## 制备本地投稿包

给 agent 说“把这版整理成 B 站投稿稿件”即可。Agent 用观众能看懂的语言写标题与简介，核对限定条件和平台当次创作声明。带有 AI 配音时在简介标明真实声源。B 站可在投稿界面的「创作声明」勾选相应的人工智能合成标识，具体以当期界面和官方说明为准。[B 站治理公告](https://www.bilibili.com/opus/1106496554576904197)

把本期配置写进项目。下面的路径指向制片流程签发的一组附件，按实际文件名取用。试剪可使用它自己的 MP4、SRT、封面、实际用料署名及同版 timeline.json。使用简洁的纯文本署名作为简介结尾。没有合成内容时省略 ai_usage、declaration 和 declaration_source。

```json
{
  "platform": "B站",
  "video": "out/final.mp4",
  "captions": "out/captions.srt",
  "cover": "out/covers/16x9.jpg",
  "credits": "out/credits/library-auto/publish.txt",
  "timeline": "timeline.json",
  "title": "同样六秒的电路板实拍，压小以后哪里先糊？",
  "description": "用同一帧、同一块电路板上的细字比较两种编码设置。片中的体积变化来自这段六秒的样本。",
  "tags": ["视频压缩", "画质对比"],
  "ai_usage": "旁白使用见秋个人声线在本机合成。",
  "declaration": "本片使用合成旁白，投稿时在创作声明中选择相应的人工智能合成标识。",
  "declaration_source": "https://www.bilibili.com/opus/1106496554576904197"
}
```

```sh
python scripts/social_handoff.py prepare --project 本期 --spec 本期/投稿配置.json --out 本期/投稿稿件
python scripts/social_handoff.py status --bundle 本期/投稿稿件
```

打开包里的「投稿稿件.md」即可复制标题、简介与署名，同时选用一处保存的视频、字幕和封面。已有本地签发回执时会逐项核对附件和时间线。试剪包标明候选状态。每个包冻结一版实片；重跑同一配置可以复核原包，更换了剪辑或文案就给新包起另一个名字。账号投稿按用户选定的平台与授权办理。

## 把实际观看与平台数据对回镜头

Agent 可以根据用户提供的真机记录、平台回看、评论、留存截图或后台导出填入观察记录。一次观察写一个 JSON。kind 选 first_view（首次观看）、real_phone（实体手机）、platform_preview（经过平台处理的回看）、comment 或 retention。写入 status 给出的 media_sha256、observed_at、note、evidence（已有的本地文件或来源网址）。本地文件相对观察 JSON 解析，并随记录存档。涉及评论或后台数据时附上对应稿件的 video_url，先核实线上采用的就是这版剪辑，若平台剪过则先取得对应的镜头时间线。

可定位的观察用 start 或 start、end，单位为秒，区间含起点至终点前。全片完播率、没有明确时码的评论可省略时间，两者保留全片范围。note 写观察到的原话、数值、统计窗口、样本量与设备条件中能取得的部分。next_test 单写下期想验证的改动。技术回放、真实人耳、平台转码和后台数据各用实际来源；有点击率下降时还要结合曝光与标题封面是否变动，单条曲线先作为复测线索。[B 站创作服务与稿件分析](https://www.bilibili.com/opus/959811803783626806)

```sh
python scripts/social_handoff.py feedback --bundle 本期/投稿稿件 --input 本期/观察.json
python scripts/social_handoff.py status --bundle 本期/投稿稿件
```

输出的 record.json 会保留来源、对应版本及镜号。下次检索同类素材时，把这份记录的镜号、实看片段和可复测的观察一起带回已有素材卡。尚未发生的真机试播、发布或平台表现保留待验证状态。
