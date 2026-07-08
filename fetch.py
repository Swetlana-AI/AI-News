import json
import os
import re
import time
from datetime import date, datetime

import feedparser
import requests

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

RSS_URL = (
    'https://news.google.com/rss/search?q='
    '"Sam+Altman"+OR+"Dario+Amodei"+OR+"OpenAI"+OR+"Anthropic"+OR+"Andrej+Karpathy"+OR+"Grok"'
    '&hl=en-US&gl=US&ceid=US:en'
)
MAX_STORIES = 8
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

NEWS_DIR = "news"
README_PATH = "README.md"
STYLE_PATH = "prompts/style.md"
DATA_PATH = "docs/data.json"

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "stories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {
                        "type": "INTEGER",
                        "description": "index of the source headline this pick is based on",
                    },
                    "commentary": {"type": "STRING"},
                },
                "required": ["index", "commentary"],
            },
        }
    },
    "required": ["stories"],
}


def load_style_guide():
    with open(STYLE_PATH, encoding="utf-8") as f:
        return f.read()


def fetch_headlines():
    feed = feedparser.parse(RSS_URL)
    seen = set()
    headlines = []
    for entry in feed.entries:
        title = entry.title.strip()
        link = entry.link.strip()
        if title in seen:
            continue
        seen.add(title)
        headlines.append({"title": title, "link": link})
    return headlines


def select_top_stories(headlines, day):
    if not headlines:
        return []
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    numbered = "\n".join(f"{i}. {h['title']}" for i, h in enumerate(headlines))
    prompt = (
        f"You are curating a daily AI news digest for {day.isoformat()}. Below is a numbered "
        "list of headlines scraped from Google News; many are near-duplicate stories covering "
        "the same event from different outlets.\n\n"
        f"Pick at most {MAX_STORIES} of the most significant, distinct AI-related stories. "
        "Merge duplicate/near-duplicate coverage of the same event into a single pick. Skip "
        "opinion pieces, listicles, and minor/low-impact items.\n\n"
        "For each pick, don't just summarize it neutrally. Write 2-4 sentences of actual "
        "commentary reacting to it in your voice: what's the real story underneath the "
        "headline, what's the surprising or overlooked angle, what does it reveal, why should "
        "the reader care. Follow the style guide below exactly.\n\n"
        f"Headlines:\n{numbered}\n"
    )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": load_style_guide()}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }

    resp = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = requests.post(url, json=body, timeout=60)
        if resp.status_code not in RETRYABLE_STATUS_CODES or attempt == MAX_ATTEMPTS:
            break
        time.sleep(2 ** attempt)  # 2s, 4s, 8s
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)

    stories = []
    used_indices = set()
    for item in parsed.get("stories", [])[:MAX_STORIES]:
        idx = item.get("index")
        if not isinstance(idx, int) or idx in used_indices or not (0 <= idx < len(headlines)):
            continue
        used_indices.add(idx)
        stories.append(
            {
                "title": headlines[idx]["title"],
                "link": headlines[idx]["link"],
                "commentary": item.get("commentary", "").strip(),
            }
        )
    return stories


def month_file_path(day):
    return os.path.join(NEWS_DIR, f"{day.strftime('%Y-%m')}.md")


def format_day_section(day, stories):
    lines = [f"## {day.isoformat()}\n\n"]
    if stories:
        for s in stories:
            lines.append(f"- **{s['title']}**\n  {s['commentary']}\n  [Read more]({s['link']})\n\n")
    else:
        lines.append("_No notable AI news found today._\n\n")
    return "".join(lines)


def append_to_month_file(day, stories):
    os.makedirs(NEWS_DIR, exist_ok=True)
    path = month_file_path(day)
    day_header = f"## {day.isoformat()}"

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if day_header in f.read():
                return path  # already logged today, avoid duplicate section

    lines = []
    if not os.path.exists(path):
        lines.append(f"# {day.strftime('%B %Y')}\n\n")
    lines.append(format_day_section(day, stories))

    with open(path, "a", encoding="utf-8") as f:
        f.writelines(lines)

    return path


def list_month_files():
    if not os.path.isdir(NEWS_DIR):
        return []
    files = [f for f in os.listdir(NEWS_DIR) if re.fullmatch(r"\d{4}-\d{2}\.md", f)]
    return sorted(files, reverse=True)


def format_archive_section():
    lines = ["## Archive\n\n"]
    for fname in list_month_files():
        ym = fname[:-3]
        d = datetime.strptime(ym, "%Y-%m")
        lines.append(f"- [{d.strftime('%B %Y')}]({NEWS_DIR}/{fname})\n")
    return "".join(lines) + "\n"


def update_readme(day, stories):
    header = (
        "# AI News\n\n"
        "A daily digest of AI news, automatically fetched and curated down to the "
        f"top {MAX_STORIES} stories each day.\n\n"
    )
    content = header + f"## Latest — {day.isoformat()}\n\n"
    if stories:
        for s in stories:
            content += f"- **{s['title']}**\n  {s['commentary']}\n  [Read more]({s['link']})\n\n"
    else:
        content += "_No notable AI news found today._\n\n"
    content += format_archive_section()

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def update_data_json(day, stories):
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"months": {}}

    month = data["months"].setdefault(day.strftime("%Y-%m"), {})
    month[day.isoformat()] = [
        {"title": s["title"], "link": s["link"], "commentary": s["commentary"]} for s in stories
    ]

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main():
    today = date.today()
    headlines = fetch_headlines()
    stories = select_top_stories(headlines, today)
    append_to_month_file(today, stories)
    update_readme(today, stories)
    update_data_json(today, stories)


if __name__ == "__main__":
    main()
