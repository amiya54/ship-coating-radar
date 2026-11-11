<div align="center" id="top">

# 船舶涂装前沿情报双周报

**面向船厂涂装技术室的前沿动态监控** —— 自动抓取 · AI 研判 · 双周推送 · 网页归档

[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-全自动运行-2088FF?style=flat-square&logo=github-actions)](https://github.com/amiya54/ship-coating-radar/actions)
[![GitHub Pages](https://img.shields.io/badge/GitHub_Pages-网页版报告-4285F4?style=flat-square&logo=github)](https://amiya54.github.io/ship-coating-radar/)
[![RSS](https://img.shields.io/badge/RSS-20%2B订阅源-orange?style=flat-square&logo=rss)](#数据源)
[![AI](https://img.shields.io/badge/AI-双周研判与交叉引用-FF6B6B?style=flat-square&logo=openai)](#核心功能)

</div>

<br>

## 这是什么

一条为**船舶涂装赛道**定制的情报流水线：每期自动从 20+ 个公开 RSS 源抓取与涂装相关的新
闻 —— 涂层性能标准（压载舱 / 货油舱 PSPC）、防污底公约（AFS）与生物杀灭剂禁限、涂料供应
商新品与型式认可、表面处理与涂层检测工艺 —— 经 AI 分级研判生成中文双周报，推送到企业微信
并在 GitHub Pages 归档成网页。

- **双周节奏**：北京时间每周五 08:00 自动发布（隔周一期）
- **零成本运行**：全部跑在 GitHub Actions 免费额度内
- **纯公开信息**：只抓 RSS 公开源，不依赖任何内部数据
- **血统**：由已稳定运行的船舶低碳情报双周报（同作者）原班机制改制

## 核心功能

| 功能 | 说明 |
|---|---|
| 中文双周报 | AI 将英文新闻研判、翻译、归纳为按板块组织的中文报告 |
| 交叉引用 | AI 正文中的（#N）可点击跳转到对应原始新闻，被引条目可反向跳回 |
| 跨期跟踪 | 重要事件跨期追踪时间线，连续 2 期无后续自动结案 |
| 数据瘦身 | 同事件多源合并、已刊出的法规旧文自动降重、单源配额上限 |
| 搜索同义词 | 报告内搜索支持中英同义词簇（如「防污漆」↔ antifouling） |
| 法规直采 | 直接抓中国船级社 / 海事局官网公告列表页，经涂装相关性白名单过滤 |
| 网页归档 | 每期报告存档于 GitHub Pages，支持宽屏 / 深色模式 / 导出 |

## 数据源

- **航运综合媒体**：gCaptain / Seatrade / The Motorship / Maritime Executive /
  Splash 247 / Hellenic Shipping News / Offshore Energy 等（沿用低碳版实测可用名单）
- **Google News 定向查询**（6 条）：涂装总览 / PSPC 与涂层标准 / 防污底公约与生物杀灭剂 /
  船级社涂装规范 / IMO 决议与涂层生效 / 国内涂装与涂料动态
- **法规原文直采**：中国船级社（规范 / 指南 / 征求意见）+ 中国海事局（技术法规 / 通知公告），
  由 `scripts/fetch_regulations.py` 的涂装相关性白名单过滤

抓取词表见 `config/frequency_words.txt`（7 组：法规原文 / 法规规范 / 船级社规范 / 防污涂料 /
涂装工艺与材料 / 环保与安全 / 企业动态），源清单见 `config/config.yaml` 的 `rss` 段。

## 运行机制

```
crawler.yml（爬虫，双周五 00:00 UTC）
  ├─ RSS 抓取 → 词表过滤 → 数据瘦身（补丁L）
  ├─ AI 研判（中文双周报正文 + 交叉引用）
  └─ report/html.py 渲染 → GitHub Pages 发布 + 企业微信推送

clean-crawler.yml（签到，周三/周六 05:00 北京时间）
  └─ 清空运行历史，重置上游试用期计时（防 7 天熔断）
```

仓库根目录的 `apply_patches.py` 是**定制补丁集**（[A]–[M]，共 13 个）：上游升级后重新下载
源码，运行 `python apply_patches.py` 一键重打全部补丁（含语法自检与幂等跳过）。

## 目录结构

```
├── config/
│   ├── config.yaml              # RSS 源、推送通道、运行参数
│   ├── frequency_words.txt      # 抓取词表（涂装 7 组）
│   └── ai_analysis_prompt.txt   # AI 研判提示词（v3.3.1-coating，含引用规范）
├── apply_patches.py             # 定制补丁集 [A]–[M]
├── scripts/
│   ├── fetch_regulations.py     # 法规原文直采（含涂装相关性白名单）
│   └── archive_and_track.py     # 过刊归档 + 跨期跟踪 + 观察池结案
└── .github/workflows/           # crawler.yml（爬虫）/ clean-crawler.yml（签到）
```

## 部署（新建仓库后的一次性动作）

1. **Secrets**（Settings → Secrets and variables → Actions）：
   - `WEWORK_WEBHOOK_URL` 企业微信群机器人地址（推送与告警通道）
   - `AI_API_KEY` / `AI_MODEL` / `AI_API_BASE` AI 研判模型配置
2. **Pages**（Settings → Pages → Build and deployment → Source 选 **GitHub Actions**）
3. 首次运行到 Actions 页手动触发 **Get Hot News**（workflow_dispatch，可跳过隔周门控）

## 致谢与许可

本项目基于开源项目 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar)（GPL-3.0）
深度定制：替换了抓取源、词表、AI 提示词与报告渲染，并叠加了十余项针对性补丁。原框架的部
署与推送机制功不可没，相关改动同样以 GPL-3.0 开放。
