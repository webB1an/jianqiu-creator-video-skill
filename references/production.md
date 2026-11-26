# 生产与来源记录

优先依据语义挑镜头。一个电机转动的近景能支持“它开始转”，测得的转速或曲线才能支持“它变快了两倍”。对品牌和真实事件使用能核实身份的照片；选择不会冒充当事人或事件现场的素材。确需合成解释时用一个有意义的提示区分其性质；普通打字、翻纸等使用说明记在来源清单。筛选素材时看镜头起、中、末，检查是否有片中烧录时间、署名或水印，检查裁切后主体位置。

可优先搜索作者或机构原站的公开档案、NASA 公有领域高清视频（`nasa-video`）、Internet Archive（`archive-video`）、Wikimedia Commons 视频（`commons-video`）与 Openverse 图片。现代 1080p/4K 实拍 B-roll 推荐配置免费 `PIXABAY_API_KEY`（[Pixabay API 文档页](https://pixabay.com/api/docs/)登录后直接获取），已有历史 `PEXELS_API_KEY` 时也可继续接入（Pexels 目前暂停新 Key 签发）；全部 Key 来源、许可与配置方式见[素材 API Key 与环境变量来源](run.md#素材-api-key-与环境变量来源)。Openverse 搜索返回的许可是发现线索，在原作页面复核。Commons 返回文件元数据与作者，使用前检查具体条款。NASA 素材应查具体署名，带有第三方权利的内容可能有不同限制。[Pixabay 许可](https://pixabay.com/service/license-summary/) · [Openverse](https://api.openverse.org/v1/) · [MediaWiki 文件信息](https://www.mediawiki.org/wiki/API:Imageinfo) · [NASA 媒体使用说明](https://www.nasa.gov/nasa-brand-center/images-and-media/)

把选中的素材 URL、作者、许可或使用条件、署名和出典写入 assets.json。保存原始媒体、实际选用的时间区间和剪辑或裁切说明。来源说明随片交付，许可要求时也放进画面或发布简介。

每个项目保存 edit.json、timeline.json、可重建源码、素材、声音分轨和 out 目录。在 qa 记录技术结果、选题的事实出处与带时间码的审片反馈。已有工程使用其等价结构。背景音乐要有署名信息或保留原创工程。名义上的视频来源数量不代表画面匹配度；对关键画面核对素材身份和事件顺序。


多人活动用近景介绍当事人时，把可辨认的脸或现场点名、该时间的字幕或署名逐字稿至少一项与具体出镜画面核对，记录原片时码和可传播条件。到场名单不能代替近景识别。能核实静态肖像而动态片段身份或权利不清时，短用准确的照片，把更多时长留给有证据的动作。
