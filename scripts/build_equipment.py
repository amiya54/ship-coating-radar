#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修造船在用先进装备专栏（独立一年窗口）

与主报告的 14 天窗口解耦：
· 数据池 archive/equipment_pool.json 随归档一起提交回仓库，跨期累积；
· 每次运行：拉取装备类 GN 查询（近一年）+ 扫描台账历史事件 → 合并去重
  → 按装备类型归类 → 每类精选若干条 → 生成「六、修造船在用先进装备」
  区域注入 index.html（插在跨期跟踪块之前）。
· 纯程序生成、不经过 AI，防止编造设备参数；讲解文字为固定知识注解。

用法：
    python scripts/build_equipment.py --report index.html --archive archive --days 365
"""
import argparse
import datetime as dt
import email.utils
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

EQUIP_FEED_IDS = ["gnews-rustblast", "gnews-laserrob", "gnews-blast-cn", "gnews-spraytech", "gnews-yardpaint"]

# 装备语境词（标题须命中其一）
EQUIP_RE = re.compile(
    r"除锈|除漆|喷砂|抛丸|磨料|abrasive|blasting|shot.?blast|grit|"
    r"rust removal|paint removal|coating removal|derusting|descaling|"
    r"surface preparation|ultra.?high.?pressure|UHP|waterjet|water.?jetting|高压水|超高压水|"
    r"干冰|dry.?ice|sponge.?blast|激光|laser (?:clean|rust|ablat)|"
    r"机器人|robot|automated (?:blast|paint|spray|grind)|无人|"
    r"无气喷涂|airless|静电喷涂|electrostatic|喷涂机|喷漆房|spray booth|"
    r"curing|UV|近红外|infrared",
    re.I,
)
# 船舶语境词（标题须同时命中其一）——与主闸门同口径
MARINE_RE = re.compile(
    r"ship|vessel|hull|shipyard|marine|maritime|boat|barge|tanker|bulk|"
    r"drydock|dry dock|船|船舶|修船|造船|船厂|船体|坞|舱",
    re.I,
)
# 明确排除（汽车/家具等陆上行业即使带了 equipment 词也不收）
EXCLUDE_RE = re.compile(r"automotive|car |vehicle|furniture|wood|-electric|EV |手机|家具|木材", re.I)

CATEGORIES = [
    ("laser", "激光除锈与清洗",
     "用脉冲激光烧蚀表面锈层与旧漆膜，无需磨料和化学介质，粉尘少、基材损伤小，"
     "适合小面积高价值区域和修船局部修复；受功率与成本限制，目前多用于点状修复而非整船除锈。"),
    ("uhp", "超高压水 · 干冰 · 海绵除漆",
     "超高压水（2000–3000 bar）可整块剥落锈层与旧涂层并同步降尘，是修船坞内大面积除漆的主流先进路线；"
     "干冰与海绵喷砂是低尘替代工艺，适合舱室等通风受限空间的作业。"),
    ("blast", "喷砂 · 抛丸与磨料",
     "喷砂/抛丸仍是船厂表面处理的主力工艺，先进方向集中在磨料回收循环、环保磨料"
     "（如 sponge/garnet）以及封闭式自动喷砂房。"),
    ("robot", "机器人与自动化作业",
     "爬壁/履带机器人承担船体除锈、打磨与喷涂作业，替代高空脚手架人工；"
     "判断成熟度看三个数：单机效率（㎡/h）、减员幅度、在实船（而不是实验室）的累计作业面积。"),
    ("spray", "喷涂 · 固化设备",
     "无气喷涂仍是船厂涂装标配；先进方向是机器人喷涂、静电辅助上漆率提升，"
     "以及 UV/近红外快速固化（多用于小部件与车间底漆后的快速周转）。"),
    ("other", "其他装备与材料",
     "难以单独归类的设备与工装动态，一并保留供参考。"),
]
CAT_RE = {
    "laser": re.compile(r"激光|laser", re.I),
    "uhp": re.compile(r"超高压|UHP|waterjet|water.?jetting|高压水|干冰|dry.?ice|sponge", re.I),
    "blast": re.compile(r"喷砂|抛丸|磨料|abrasive|blast|grit|shot", re.I),
    "robot": re.compile(r"机器人|robot|automated|无人", re.I),
    "spray": re.compile(r"喷涂|喷漆|spray|painting|涂装|curing|UV|近红外|infrared|airless|静电", re.I),
}

SECTION_STYLE = """
<style>
.eq-sec{max-width:1080px;margin:28px auto;padding:0 16px;font-family:inherit}
.eq-head{background:linear-gradient(135deg,#1e3a5f,#2d5f8a);color:#fff;border-radius:12px 12px 0 0;padding:18px 22px}
.eq-head h2{margin:0;font-size:20px}
.eq-head p{margin:6px 0 0;font-size:12.5px;opacity:.85}
.eq-body{border:1px solid #d8e0ea;border-top:none;border-radius:0 0 12px 12px;padding:6px 22px 16px;background:#fff}
.eq-cat{margin:16px 0 6px;font-size:15px;color:#1e3a5f}
.eq-blurb{margin:2px 0 8px;font-size:12.5px;color:#556;line-height:1.7;background:#f4f7fb;border-left:3px solid #2d5f8a;padding:7px 10px;border-radius:0 6px 6px 0}
.eq-list{list-style:none;margin:0;padding:0}
.eq-list li{padding:5px 0;border-bottom:1px dashed #e3e9f0;font-size:13.5px;line-height:1.6}
.eq-list li:last-child{border-bottom:none}
.eq-date{display:inline-block;min-width:66px;color:#8896a8;font-size:12px}
.eq-src{color:#8896a8;font-size:12px;margin-left:6px}
.eq-list a{color:#1d4e89;text-decoration:none}
.eq-list a:hover{text-decoration:underline}
.eq-empty{color:#99a;font-size:13px;padding:4px 0 10px}
</style>
"""


def norm_title(t):
    s = re.sub(r"\s+", " ", t).strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)[:64]


def classify(title):
    for key, rx in CAT_RE.items():
        if rx.search(title):
            return key
    return "other"


def keep(title):
    if EXCLUDE_RE.search(title):
        return False
    return bool(EQUIP_RE.search(title) and MARINE_RE.search(title))


def fetch_gn(feeds, days):
    """直采装备类 GN 查询，返回 [(date, title, url, src)]。
    先试直连（Actions 环境），再退回系统代理（本机沙箱场景）。"""
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    default = urllib.request.build_opener()
    out = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    for f in feeds:
        xml = None
        for op in (direct, default):
            try:
                req = urllib.request.Request(f["url"], headers={"User-Agent": "Mozilla/5.0"})
                xml = op.open(req, timeout=30).read()
                break
            except Exception as e:
                last = e
        if xml is None:
            print(f"  ⚠️ 拉取 {f.get('name')} 失败：{type(last).__name__} {last}")
            continue
        try:
            root = ET.fromstring(xml)
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                u = (it.findtext("link") or "").strip()
                pd = it.findtext("pubDate") or ""
                try:
                    d = email.utils.parsedate_to_datetime(pd)
                except Exception:
                    continue
                if d.tzinfo is None:
                    d = d.replace(tzinfo=dt.timezone.utc)
                if d < cutoff or not keep(t):
                    continue
                out.append((d.date().isoformat(), t, u, f.get("name", "GN")))
        except Exception as e:
            print(f"  ⚠️ 解析 {f.get('name')} 失败：{type(e).__name__} {e}")
    return out


def scan_ledger(ledger_path, days):
    """扫台账历史事件（issue 日期为基准）"""
    out = []
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        data = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    except Exception:
        return out
    for iss in data.get("issues", []):
        d = iss.get("date", "")
        if not d or d < cutoff:
            continue
        for ev in iss.get("events", []):
            t = ev.get("title", "")
            if keep(t):
                out.append((d, t, ev.get("url", ""), ev.get("src", "过刊")))
    return out


def build_section(pool, per_cat=6):
    """按类目精选生成 HTML（每类取最近 per_cat 条，总量上限 24）"""
    by_cat = {k: [] for k, _, _ in CATEGORIES}
    for it in pool:  # pool 已按日期倒序，元素为 {d,t,u,s}
        by_cat[classify(it["t"])].append((it["d"], it["t"], it["u"], it["s"]))
    rows = []
    total = 0
    for key, name, blurb in CATEGORIES:
        items = by_cat[key][:per_cat]
        if not items:
            continue
        lis = []
        for d, t, u, s in items:
            src = s.split("·")[-1].strip() if s else ""
            if u:
                a = f'<a href="{html_escape(u)}" target="_blank" rel="noopener">{html_escape(t)}</a>'
            else:
                a = html_escape(t)
            lis.append(f'<li><span class="eq-date">{d}</span>{a}'
                       f'<span class="eq-src">{html_escape(src)}</span></li>')
        rows.append(f'<h4 class="eq-cat">▍{name}（近一年 {len(by_cat[key])} 条，精选 {len(items)} 条）</h4>'
                    f'<p class="eq-blurb">{blurb}</p>'
                    f'<ul class="eq-list">{"".join(lis)}</ul>')
        total += len(items)
    note = "区域数据池独立于主报告 14 天窗口（365 天），随每期自动累积去重；只精选代表性条目，不罗列全部来源。"
    return (SECTION_STYLE
            + f'<section class="eq-sec" id="equipment"><div class="eq-head">'
            f'<h2>🔧 六、修造船在用先进装备（近一年）</h2><p>{note}</p></div>'
            f'<div class="eq-body">{"".join(rows) or "<p class=\'eq-empty\'>装备池积累中，下期自动扩充。</p>"}</div></section>')


def html_escape(s):
    import html as _h
    return _h.escape(s or "", quote=True)


def load_equip_feeds(config_path):
    """零依赖解析 config.yaml，抽取装备类 GN 查询的 name/url。
    按 `- id: "xxx"` 切块，块内抓 name 与 url 行。"""
    txt = Path(config_path).read_text(encoding="utf-8")
    blocks = re.split(r'\n\s*-\s+id:\s*"', txt)
    feeds = []
    for b in blocks[1:]:
        m_id = re.match(r'([^"]+)"', b)
        if not m_id or m_id.group(1) not in EQUIP_FEED_IDS:
            continue
        m_name = re.search(r'name:\s*"([^"]+)"', b)
        m_url = re.search(r'url:\s*"([^"]+)"', b)
        # enabled: false 的跳过
        m_en = re.search(r'enabled:\s*false', b)
        if m_name and m_url and not m_en:
            feeds.append({"id": m_id.group(1), "name": m_name.group(1), "url": m_url.group(1)})
    return feeds


def inject(report, section_html):
    p = Path(report)
    doc = p.read_text(encoding="utf-8")
    if 'id="equipment"' in doc:
        # 已有旧区域则整体替换
        doc = re.sub(r'<section class="eq-sec" id="equipment">.*?</section>', section_html, doc, flags=re.S)
    else:
        anchor = "<!--TRACK:END-->"
        if anchor in doc:
            doc = doc.replace(anchor, anchor + section_html, 1)
        else:
            doc = doc.replace("</body>", section_html + "</body>", 1)
    p.write_text(doc, encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="index.html")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    arch = Path(args.archive)
    arch.mkdir(exist_ok=True)
    pool_path = arch / "equipment_pool.json"
    pool = {}
    if pool_path.exists():
        try:
            data = json.loads(pool_path.read_text(encoding="utf-8"))
            # 兼容两种存储：扁平 dict{k: item}（运行态）和 {"items": [...]}（落盘格式）
            src = data.get("items", []) if isinstance(data, dict) and "items" in data else data
            if isinstance(src, dict):
                pool = src
            elif isinstance(src, list):
                pool = {norm_title(v.get("t", "")): v for v in src if isinstance(v, dict)}
        except Exception:
            pool = {}

    fresh = []
    # 1) 直采装备类 GN（近一年）。零依赖：正则抽 config.yaml 里的 feed 定义，
    #    不 import yaml（Actions 的裸 python 环境没有 pyyaml）。
    try:
        feeds = load_equip_feeds(args.config)
        print(f"  装备类查询：{[f['name'] for f in feeds]}")
    except Exception as e:
        print(f"  ⚠️ 读配置失败（{type(e).__name__}），跳过直采只用台账")
        feeds = []
    if feeds:
        got = fetch_gn(feeds, args.days)
        print(f"  直采装备 GN：{len(got)} 条")
        fresh.extend(got)
    # 2) 台账历史
    lg = scan_ledger(arch / "ledger.json", args.days)
    print(f"  台账历史命中：{len(lg)} 条")
    fresh.extend(lg)

    cutoff = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    for d, t, u, s in fresh:
        k = norm_title(t)
        old = pool.get(k)
        if old is None or (d and d > old.get("d", "")) or not u:
            pool[k] = {"d": d, "t": t, "u": u, "s": s}
    # 过期清理
    pool = {k: v for k, v in pool.items() if v.get("d", "0000") >= cutoff}
    items = sorted(pool.values(), key=lambda x: x.get("d", ""), reverse=True)
    pool_path.write_text(json.dumps(
        {"updated": dt.date.today().isoformat(), "items": items},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  装备池合计：{len(items)} 条（≤{args.days} 天）→ {pool_path}")

    section = build_section(items)
    inject(args.report, section)
    print(f"  已注入专栏区域 → {args.report}")


if __name__ == "__main__":
    main()
