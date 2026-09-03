"""原型：解析 github.com/trending 页面，验证字段提取是否可靠。"""
import re
import html as ihtml
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _txt(s):
    s = re.sub(r"<[^>]+>", "", s)
    return ihtml.unescape(s).strip()


def _num(s):
    s = (s or "").strip().replace(",", "")
    m = re.search(r"([\d.]+)\s*[kK]?", s)
    if not m:
        return 0
    v = float(m.group(1))
    if "k" in s.lower():
        v *= 1000
    return int(v)


def parse(html):
    out = []
    chunks = re.split(r'<article class="Box-row">', html)[1:]
    for i, c in enumerate(chunks, 1):
        c = c.split("</article>")[0]
        m = re.search(r'<h2 class="h3 lh-condensed">.*?href="/([^/"]+/[^/"]+)"', c, re.S)
        if not m:
            continue
        full = m.group(1)
        d = re.search(r'<p class="col-9 color-fg-muted my-1 pr-4">(.*?)</p>', c, re.S)
        desc = _txt(d.group(1)) if d else ""
        lang = ""
        lm = re.search(r'itemprop="programmingLanguage">([^<]+)<', c)
        if lm:
            lang = ihtml.unescape(lm.group(1)).strip()
        stars = 0
        sm = re.search(r'href="/[^/]+/[^/]+/stargazers"[^>]*>(.*?)</a>', c, re.S)
        if sm:
            stars = _num(_txt(sm.group(1)))
        forks = 0
        fm = re.search(r'href="/[^/]+/[^/]+/forks"[^>]*>(.*?)</a>', c, re.S)
        if fm:
            forks = _num(_txt(fm.group(1)))
        today = 0
        tm = re.search(r'float-sm-right[^>]*>(.*?)</span>', c, re.S)
        if tm:
            today = _num(_txt(tm.group(1)))
        topics = [ihtml.unescape(t) for t in re.findall(
            r'data-view-component="true" class="topic-tag topic-tag-link">(.*?)</a>', c, re.S)]
        out.append({"rank": i, "fullName": full, "desc": desc, "lang": lang,
                    "stars": stars, "forks": forks, "today": today, "topics": topics})
    return out


if __name__ == "__main__":
    req = urllib.request.Request("https://github.com/trending?since=daily", headers={"User-Agent": UA})
    h = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "ignore")
    items = parse(h)
    print("解析到", len(items), "条\n")
    for it in items[:8]:
        print(f"{it['rank']:>2}. {it['fullName']}")
        print(f"    lang={it['lang'] or '-'}  ★{it['stars']}  today+{it['today']}  forks={it['forks']}")
        print(f"    {(it['desc'] or '(无描述)')[:90]}")
        if it["topics"]:
            print(f"    topics: {', '.join(it['topics'])[:80]}")
        print()
