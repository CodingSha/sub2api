# 2026年11月26日生产基线

本次封版保存11月16—20日全员日评、周评替换后的实际生产源码，并纳入随后部署的容器收尾修复。封版不代表评审质量争议已解决，也不重新运行评分或更新生产应用镜像。

## 已发布状态

- 本轮99名用户，495份日评记录、99份周评已发布。
- 日页面展示维度分析；周页面展示综合评分、公司排名和能力图。
- 发布验算核对99份精确周分及495份日来源关联；技术未知请求与个人评分保持区分。
- 本轮采用已获用户接受的GPT链路，不作为公司本地DeepSeek已验证的证明。

## 镜像与源码核对

- 应用：`sub2api-local:conversation-20261126-product`，镜像ID `sha256:f8ddb7d0ea4c8a6faeed7968e9a1f18ba87fa42f89f55e93346a8230f5b821fa`。
- 评分Worker：`zhiyongli-worker-local:conversation-20261126-repair-envelope`，镜像ID `sha256:ec2519f78cad130345e7f474e2cd3f7ac1037deee9299b3d59bf33cf2a27e9d1`。
- 最终评审实现签名：`f04ecfbd8e0f52a2417732acd829332edfdc0b65b4580e067fccd036d195838e`。
- 生产构建目录：`/data/zhiyongli/releases/cutover-34a9e6635/source`。按相同筛选条件对文件路径和内容摘要排序计算指纹，排除测试文件、依赖目录和构建缓存。

| 源码范围 | 文件数 | SHA256 |
| --- | ---: | --- |
| backend | 1717 | `98c34237d72fc326888526c6b9f693bd1bac89aa7df185caa7b7fdd55b8111fb` |
| frontend | 579 | `722eb4b91fb92692b83c2aae39238e922735e4fceec9ec38149352946072504a` |
| tools/zhiyongli | 114 | `d76656d69b65c1e45640c8a04bcbdfb703f511dfa5f2ad181adf5b32f503e32e` |
| research/ai-usage-assessment/scoring_pipeline | 64 | `5387776c9d397f9c32008120562d8fed451ac79d189cb0bbffd81d81fc2cab7f` |

本地独有的周评草稿扩展未上线，已单独私有备份，本基线采用生产实际的日评草稿限制。两份生成的Vite配置缓存已移除，不计入源码。容器收尾属于运维修复，安装于`/data/zhiyongli/ops/`及systemd，不改变以上产品代码指纹。

## 验证与留档

- 连续对话测试84项通过，日评相关136项检查通过（5项跳过），页面测试17项通过。
- 智用力Go包及对应合成数据库集成检查通过；容器边界测试10项通过，并完成生产真实容器验证。
- 七个固定真实案例的结果和原回执证明重建已在发布阶段核对；不将工程回归或同模型自检表述为独立人工评审。
- 生产发布回执保留在`/data/zhiyongli/releases/conversation-20261125-v14/`，冻结来源和原始请求回执保留在`/data/zhiyongli/state/conversation-campaign-20261125/`；这些私有资料不提交Git。
- 主目录此前78个已跟踪改动及134个未跟踪文件，保存在本地`refs/archive/zhiyongli-main-before-production-20261126`，未混入本基线。可在原分支用`git stash apply`该引用恢复。
- 4712个旧试评文件已压缩、逐文件校验并移除展开副本，净释放约1.99GB。归档和恢复索引保留于本机私有maintenance目录；当前质量验证、共享来源和预算账簿继续保留。

后续生产发布须使用已提交并推送的确定提交，记录应用与Worker镜像ID；不得再把未提交工作树直接当成已封版分支。运行容器的数量、历史容器的清理和评分质量须分别验收。
