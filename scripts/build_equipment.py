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
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ★2026-11-23 重构：装备专栏不再复用 config.yaml 里的主报告查询。
#   原因（实测证据）：主报告查询统一加了 when:14d 抢新鲜度，装备要的是
#   一年窗口，两者口径相反；且 Google News 无算子查询只返回「最相关的 10 条」，
#   实测最旧一条是 2014 年 —— 这正是「一年池子只有 20 条」的真正原因：
#   每轮拿到 10 条、绝大多数是旧闻，过完 365 天窗口就只剩个位数。
#   对策：自建查询组 + after:/before: 按季度回溯（实测 Q1/Q2/Q3 各能取回 4-9 条）。
#   (label, query, lang)  lang: en / zh
EQUIP_QUERIES = [
    ("喷砂与抛丸",
     'shipyard blasting OR "hull blasting" OR "abrasive blasting" OR "shot blasting" ship', "en"),
    ("除锈与除漆",
     '"rust removal" OR "paint removal" OR "coating removal" OR derusting ship OR hull OR shipyard', "en"),
    ("激光清洗除锈",
     '"laser cleaning" OR "laser rust removal" OR "laser ablation" hull OR shipyard OR vessel', "en"),
    ("超高压水与干冰",
     '"ultra high pressure" OR UHP OR "water jetting" OR "dry ice blasting" shipyard OR hull OR ship', "en"),
    ("机器人除锈喷涂",
     '"painting robot" OR "spraying robot" OR "coating robot" OR "blasting robot" '
     'OR "crawling robot" ship OR shipyard OR hull', "en"),
    ("表面处理与喷涂设备",
     '"surface preparation" OR "shop primer" OR "paint booth" OR "airless spray" '
     'OR "spray painting" shipyard OR shipbuilding', "en"),
    ("中文除锈喷砂",
     '除锈 OR 喷砂 OR 除漆 OR 抛丸 船厂 OR 船舶 OR 船体', "zh"),
    ("中文机器人涂装",
     '喷涂机器人 OR 除锈机器人 OR 爬壁机器人 OR 涂装机器人 船舶 OR 船厂 OR 修船', "zh"),
    ("喷涂车间与作业设施",
     'shipyard "paint shop" OR "blasting facility" OR "coating facility" OR "paint facility"', "en"),
    ("涂装施工设备",
     '"coating application" OR "paint application" OR "coating equipment" ship OR hull OR shipyard', "en"),
    ("坞修涂装作业",
     'drydock OR "dry dock" painting OR blasting OR coating "ship repair" OR refit', "en"),
    ("中文修船涂装设备",
     '修船 OR 坞修 涂装 OR 喷砂 OR 除锈 设备 OR 机器人', "zh"),
]


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
# ★2026-11-23 修（用户反馈"一堆未见的"）：
#   · 水下船体清洗/螺旋桨抛光属【在航污损管理】，不是修造船除锈喷漆装备；
#     此前 6 家媒体转述同一桩机器人洗船体事件全进了"机器人"类。
#   · waterjet 推进器（Kongsberg 喷水推进订单）≠ 高压水除锈装备。
#   · 市场规模/前景展望类研究报告，不是"在用装备"动态。
EXCLUDE_RE = re.compile(
    r"automotive|car |vehicle|furniture|wood|-electric|EV |手机|家具|木材|"
    r"weapon|武器|导弹|"
    r"hull cle|in-water cleaning|propeller cleaning|hull grooming|hull management|hull care|"
    r"船体清洁|水下清洗|螺旋桨清洗|洗船体|飞行甲板|"
    r"waterjet (contract|propulsion|driven|system|order)|jet propulsion|喷水推进|"
    r"market (size|outlook|forecast|report|growth)|市场(规模|前景|展望|报告)|至20\d\d年",
    re.I)

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


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokens(t):
    """标题词袋：中文取二元组，英文/数字取 ≥2 长度单词。"""
    t = str(t or "").lower()
    out = set()
    for run in _CJK_RE.findall(t):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    for w in re.findall(r"[a-z0-9]{2,}", t):
        out.add(w)
    return out


def _jac(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


_STOP = set("""and the for with from that this its are was were has have not but can will
ship ships vessel vessels hull shipyard yard marine maritime boat barge barges tanker
robot robots robotic robotics automated automation system systems technology tech
new launch launches launched unveil unveils unveiling introduce introduces
test tests testing trial trials partner partners partnership programme program project
facility facilities first largest next major new
cleaning clean blast blasting blasted paint painting coating coated corrosion rust
removal surface preparation equipment solution solutions provider company companies group
news report magazine international global offshore industry industrial
set to of in on at by as an a is be it or
""".split())


def _core(t):
    """标题的「实体词」：去掉行业泛词与虚词后的英文/数字词。
    同一桩事被多家改写的标题，海务泛词几乎全重叠，真正能区分事件的
    是船东/厂商/地名这类专名（louis、dreyfus、neptune…）。"""
    return {w for w in re.findall(r"[a-z0-9&]{2,}", str(t or "").lower())
            if w not in _STOP}


def _clean_title(t):
    """去掉 Google News 拼在标题尾部/开头的噪音，再算相似度：
    尾部「 - 媒体名」、开头「【栏目】」都会稀释中文标题的相似度
    （实测同一事件两条标题 J 从 0.39 提到 0.46）。"""
    s = str(t or "")
    s = re.sub(r"\s*[-–|]\s*[^-–|]{2,20}\s*$", "", s)
    s = re.sub(r"^[【\[（(][^】\]）)]{1,12}[】\]）)]", "", s)
    return s.strip()


def _lcs(a, b):
    """最长公共子串长度（仅用于中文标题的同事件判断）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def dedupe_items(items):
    """★2026-11-23 跨源同事件合并：一桩事件被多家媒体转述只留最新一条，
    其余家数记在 n 字段、页面上显示「另 N 家转述」。
    判据任一条成立即视为同一事件：
      ① 标题 Jaccard：英文 ≥0.52 / 中文 ≥0.72（沿用主报告口径）；
      ② 实体词重合 ≥2 个（英文新闻改写后 J 只有 0.2-0.3，实测同一桩
         「路易达孚 × Neptune 机器人洗船体」六家标题 J 仅 0.16-0.36，
         纯相似度抓不住，只能靠 louis/dreyfus/neptune 这类专名）；
      ③ 中文标题最长公共子串 ≥8 字（中文改写常整句换说法，
         但事件主体名与型号名会原样保留，如「新一代双高脉冲船舶涂装激光清洗」）。
    入参须按日期倒序。"""
    kept = []
    for it in items:
        t = it.get("t", "")
        ct = _clean_title(t)
        cjk = bool(_CJK_RE.search(ct))
        core = _core(ct)
        hit = None
        for k in kept:
            kt = _clean_title(k.get("t", ""))
            thr = 0.72 if (cjk and _CJK_RE.search(kt)) else 0.52
            if (_jac(ct, kt) >= thr
                    or len(core & _core(kt)) >= 2
                    or (cjk and _CJK_RE.search(kt) and _lcs(ct, kt) >= 8)):
                hit = k
                break
        if hit is not None:
            hit["n"] = hit.get("n", 1) + 1
            continue
        it["n"] = it.get("n", 1)
        kept.append(it)
    return kept


def classify(title):
    for key, rx in CAT_RE.items():
        if rx.search(title):
            return key
    return "other"


# ★2026-11-23：机器人是最大的误判来源 —— 焊接、装配、探伤、船体清洁机器人
#   都会命中「robot + ship」，但它们不是涂装装备。仅靠机器人命中的标题，
#   必须再出现一条涂装/表面处理作业词才算数。
ROBOT_RE = re.compile(r"机器人|robot|automated|无人", re.I)
COAT_OP_RE = re.compile(
    r"除锈|除漆|喷砂|抛丸|喷涂|喷漆|涂装|涂层|涂料|漆|打磨|探厚|测厚|"
    r"rust|paint|coat|blast|surface prep|grind|laser|UHP|waterjet|高压水|干冰", re.I)


def keep(title):
    if EXCLUDE_RE.search(title):
        return False
    if not (EQUIP_RE.search(title) and MARINE_RE.search(title)):
        return False
    if ROBOT_RE.search(title) and not COAT_OP_RE.search(title):
        return False
    return True


def _gn_url(query, start, end, lang):
    q = f"{query} after:{start} before:{end}"
    base = "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q)
    if lang == "zh":
        return base + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans&num=100"
    return base + "&hl=en-US&gl=US&ceid=US:en&num=100"


def fetch_gn(feeds, days, windows=6):
    """按时间段分窗直采装备类查询，返回 [(date, title, url, src)]。
    ★2026-11-23：改为 after:/before: 分窗回溯。无算子查询只回「最相关 10 条」
    （实测最旧 2014 年），一年窗口靠它等于白抓；分窗后每窗口能取回 4-9 条，
    四个窗口拼起来才真正覆盖一年。先试直连（Actions 环境），再退回系统代理。"""
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    default = urllib.request.build_opener()
    out = []
    today = dt.date.today()
    span = max(1, days // windows)
    for label, query, lang in feeds:
        got = 0
        for w in range(windows):
            end = today - dt.timedelta(days=w * span)
            start = end - dt.timedelta(days=span)
            url = _gn_url(query, start.isoformat(), end.isoformat(), lang)
            xml = None
            last = None
            for op in (direct, default):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    xml = op.open(req, timeout=30).read()
                    break
                except Exception as e:      # noqa: BLE001
                    last = e
            if xml is None:
                print(f"  ⚠️ {label} [{start}~{end}] 失败：{type(last).__name__} {last}")
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
                    if d.date() < today - dt.timedelta(days=days) or not keep(t):
                        continue
                    out.append((d.date().isoformat(), t, u, "装备检索·" + label))
                    got += 1
            except Exception as e:          # noqa: BLE001
                print(f"  ⚠️ 解析 {label} 失败：{type(e).__name__} {e}")
            time.sleep(2.5)
        print(f"  · {label}：{got} 条")
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


def build_section(pool, ai_text="", ai_date="", per_cat=6):
    """按类目精选生成 HTML（每类取最近 per_cat 条，总量上限 24）"""
    by_cat = {k: [] for k, _, _ in CATEGORIES}
    for it in pool:  # pool 已按日期倒序、已同事件合并，元素为 {d,t,u,s,n}
        by_cat[classify(it["t"])].append(
            (it["d"], it["t"], it["u"], it["s"], it.get("n", 1)))
    rows = []
    total = 0
    for key, name, blurb in CATEGORIES:
        items = by_cat[key][:per_cat]
        if not items:
            continue
        lis = []
        for d, t, u, s, n in items:
            src = s.split("·")[-1].strip() if s else ""
            if n and n > 1:
                src = f"{src}（另{n - 1}家转述）" if src else f"另{n - 1}家转述"
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
    ai_html = ""
    if ai_text:
        ai_html = (f'<div class="eq-ai"><h4 class="eq-cat">▍AI 研判 · 在用装备现状（{ai_date}）</h4>'
                   f'<p class="eq-ai-body">{html_escape(ai_text)}</p></div>')
    ai_style = ""
    if ai_text:
        ai_style = ("<style>.eq-ai{background:#f0f5fa;border:1px solid #cfdcec;border-radius:8px;"
                    "padding:10px 14px;margin:14px 0 4px}.eq-ai-body{margin:4px 0 2px;font-size:13.5px;"
                    "line-height:1.9;color:#233;white-space:pre-wrap}</style>")
    return (SECTION_STYLE + ai_style
            + f'<section class="eq-sec" id="equipment"><div class="eq-head">'
            f'<h2>🔧 六、修造船在用先进装备（近一年）</h2><p>{note}</p></div>'
            f'<div class="eq-body">{ai_html}{"".join(rows) or "<p class=\'eq-empty\'>装备池积累中，下期自动扩充。</p>"}</div></section>')


def html_escape(s):
    import html as _h
    return _h.escape(s or "", quote=True)


def load_ai_conf(config_path):
    """零依赖读 AI 配置：model（去掉 litellm 前缀）与 api_base。"""
    txt = Path(config_path).read_text(encoding="utf-8")
    m = re.search(r'^ai:\s*$(.*?)^(?=\S|\Z)', txt, re.M | re.S)
    block = m.group(1) if m else txt
    model = re.search(r'model:\s*"([^"]+)"', block)
    base = re.search(r'api_base:\s*"([^"]*)"', block)
    name = model.group(1) if model else "deepseek/deepseek-v4-flash"
    short = name.split("/", 1)[-1]
    base_url = (base.group(1) if base and base.group(1) else "https://api.deepseek.com").rstrip("/")
    return short, base_url + "/v1/chat/completions"


AI_PROMPT = """你是一名修船/造船涂装工艺工程师，为「修造船在用先进装备」专栏写一段研判（500-750字，纯文本段落，不要表格、不要标题序号）。

下面是近一年抓到的装备类新闻清单（日期 | 类别 | 标题）。请按五条装备线组织：①激光除锈与清洗 ②超高压水/干冰/海绵除漆 ③喷砂抛丸与磨料 ④机器人与自动化作业 ⑤喷涂与固化设备。每条线写清楚：
- 目前行业在用什么（从清单里的事件归纳，说明是试验、批量应用还是刚发布）；
- 成熟度判断（报道密度高+实船应用多=较成熟；只有单一试验报道=早期）；
- 对造船线与修船坞分别的适用性。

铁律：
1. 只能基于清单归纳，不得编造清单外的设备、厂商或数字；
2. 清单里出现的数字（效率、除锈等级等）可以引用，但要注明出自哪条报道；
3. 某条线近一年完全没有报道，就明确写"该线近一年未见报道"并给一句基于行业常识的补充说明（标注"常识推断"）；
4. 语言务实，不写营销腔。

清单：
{list}"""


def ai_analyze(items, config_path):
    """调 AI 对装备池做研判；返回文本或 None。"""
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        print("  ⚠️ 无 AI_API_KEY，跳过 AI 研判")
        return None
    try:
        model, url = load_ai_conf(config_path)
    except Exception as e:
        print(f"  ⚠️ 读 AI 配置失败：{type(e).__name__}")
        return None
    lines = [f"{it['d']} | {classify(it['t'])} | {it['t'][:120]}" for it in items[:45]]
    if not lines:
        return None
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": AI_PROMPT.replace("{list}", "\n".join(lines))}],
        "temperature": 1.0,
        "max_tokens": 8000,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data["choices"][0]["message"]["content"] or "").strip()
        print(f"  AI 研判完成：{len(text)} 字（{model}）")
        return text
    except Exception as e:
        print(f"  ⚠️ AI 研判失败：{type(e).__name__} {str(e)[:120]}")
        return None




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
    # 1) 装备类查询：自建查询组 + 按季度回溯一年（不依赖 config.yaml 的
    #    主报告查询，后者带 when:14d，口径与一年窗口相反）。
    got = fetch_gn(EQUIP_QUERIES, args.days)
    print(f"  直采装备 GN：{len(got)} 条")
    fresh.extend(got)
    # 2) 台账历史
    lg = scan_ledger(arch / "ledger.json", args.days)
    print(f"  台账历史命中：{len(lg)} 条")
    fresh.extend(lg)

    cutoff = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    # ★2026-11-23：池内历史条目也要过一遍现行规则 —— 排除词是后来才加的，
    #   不清洗的话，早期混进来的水下清洗机器人、市场报告会永久留在页面上。
    _before = len(pool)
    pool = {k: v for k, v in pool.items() if keep(v.get("t", ""))}
    if _before != len(pool):
        print(f"  池内清洗：{_before} → {len(pool)} 条"
              f"（按现行排除规则剔除 {_before - len(pool)} 条）")
    for d, t, u, s in fresh:
        k = norm_title(t)
        old = pool.get(k)
        if old is None or (d and d > old.get("d", "")) or not u:
            pool[k] = {"d": d, "t": t, "u": u, "s": s}
    # 过期清理
    pool = {k: v for k, v in pool.items() if v.get("d", "0000") >= cutoff}
    items = sorted(pool.values(), key=lambda x: x.get("d", ""), reverse=True)
    # ★跨源同事件合并（6 家媒体转述同一桩事 → 收敛成 1 条）
    raw_n = len(items)
    items = dedupe_items(items)
    if raw_n != len(items):
        print(f"  同事件合并：{raw_n} → {len(items)} 条（{raw_n - len(items)} 条为多源转述）")

    # 3) AI 研判（对装备池整体，一年窗口）：成功则缓存，失败沿用上次结果
    old_ai = {}
    try:
        _raw = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else {}
        old_ai = {"t": _raw.get("ai_summary", ""), "d": _raw.get("ai_date", "")}
    except Exception:
        pass
    ai_text = ai_analyze(items, args.config)
    if ai_text:
        ai_cache = {"t": ai_text, "d": dt.date.today().isoformat()}
    else:
        ai_cache = old_ai
        if ai_cache.get("t"):
            print(f"  沿用上次 AI 研判（{ai_cache['d']}）")
    pool_path.write_text(json.dumps(
        {"updated": dt.date.today().isoformat(),
         "ai_summary": ai_cache.get("t", ""), "ai_date": ai_cache.get("d", ""),
         "items": items},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  装备池合计：{len(items)} 条（≤{args.days} 天）→ {pool_path}")

    section = build_section(items, ai_cache.get("t", ""), ai_cache.get("d", ""))
    inject(args.report, section)
    print(f"  已注入专栏区域 → {args.report}")


if __name__ == "__main__":
    main()
