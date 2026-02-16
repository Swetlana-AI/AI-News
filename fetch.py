import feedparser
from datetime import date

URL = 'https://news.google.com/rss/search?q="Sam+Altman"+OR+"Dario+Amodei"+OR+"OpenAI"+OR+"Anthropic"+OR+"Andrej Karpathy"+OR+"Grok"'

feed = feedparser.parse(URL)

today = date.today()

lines = []
lines.append(f"## {today}\n")

seen = set()

for e in feed.entries:
    title = e.title.strip()
    link = e.link.strip()

    # avoid duplicates
    if title in seen:
        continue
    seen.add(title)

    lines.append(f"- {title}\n  {link}\n")

lines.append("\n")

with open("news.md", "a", encoding="utf-8") as f:
    f.write("\n".join(lines))
