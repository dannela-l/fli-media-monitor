import os
import re
import json
from datetime import datetime, date, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import anthropic
import feedparser
import requests
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, redirect, url_for
from slack_sdk import WebClient

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_NAME = os.getenv("SLACK_CHANNEL_NAME")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

client = WebClient(token=SLACK_BOT_TOKEN)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

LOCAL_TIMEZONE = "America/Los_Angeles"
STATE_FILE = "seen_articles.json"
STATUS_FILE = "run_status.json"

# RSS-first settings
USE_NEWSAPI = False
MAX_ARTICLE_AGE_HOURS = 24

app = Flask(__name__)

TOP_TIER_OUTLETS = {
    "Reuters",
    "Associated Press",
    "AP News",
    "The New York Times",
    "New York Times",
    "Washington Post",
    "The Washington Post",
    "The Wall Street Journal",
    "Wall Street Journal",
    "Financial Times",
    "Bloomberg",
    "POLITICO",
    "Politico",
    "Axios",
    "Semafor",
    "The Verge",
    "WIRED",
    "Wired",
    "MIT Technology Review",
    "BBC News",
    "BBC",
    "CNN",
    "CNBC",
    "The Atlantic",
}

SPOKESPEOPLE = {
    "Max Tegmark",
    "Anthony Aguirre",
    "Mark Brakel",
    "Michael Kleinman",
    "Emilia Javrosky",
    "Hamza Chaudhry",
    "Anna Hehir",
}

FLI_TERMS = {
    "Future of Life Institute",
    "FLI",
}

RELEVANCE_TERMS = {
    "AI safety",
    "AGI",
    "ASI",
    "artificial general intelligence",
    "artificial superintelligence",
    "AI regulation",
    "AI policy",
    "AI governance",
    "autonomous weapons",
    "frontier AI",
    "AI risk",
    "existential risk",
    "labor displacement",
    "worker displacement",
    "job replacement",
    "AI jobs",
    "AI replacing workers",
    "federal regulation",
    "labor replacement",
    "automation",
}

# Keep feeds that are already working in your setup.
# You can add more later if you find reliable public RSS endpoints.
RSS_FEEDS = {
    "Reuters": "https://www.reutersagency.com/feed/?best-topics=artificial-intelligence&post_type=best",
    "POLITICO": "https://www.politico.com/rss/politicopicks.xml",
    "WIRED": "https://www.wired.com/feed/tag/ai/latest/rss",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    "CNBC": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "CNN": "http://rss.cnn.com/rss/cnn_latest.rss",
    "BBC News": "http://feeds.bbci.co.uk/news/technology/rss.xml",
}

PAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>FLI Media Monitor</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f7fb;
      margin: 0;
      padding: 40px 20px;
      color: #1f2937;
    }
    .card {
      max-width: 760px;
      margin: 0 auto;
      background: white;
      border-radius: 16px;
      padding: 32px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.08);
    }
    h1 {
      margin-top: 0;
      font-size: 32px;
      color: #1e293b;
    }
    .sub {
      color: #6b7280;
      margin-bottom: 24px;
      font-size: 16px;
    }
    .status {
      background: #f3f4f6;
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 20px;
      line-height: 1.7;
      font-size: 16px;
    }
    .btn {
      display: inline-block;
      background: #0f172a;
      color: white;
      text-decoration: none;
      padding: 14px 20px;
      border-radius: 10px;
      font-weight: 600;
      border: none;
      cursor: pointer;
      font-size: 16px;
    }
    .btn:hover {
      background: #020617;
    }
    .note {
      margin-top: 20px;
      color: #6b7280;
      font-size: 14px;
    }
    .success {
      margin-top: 20px;
      background: #ecfdf5;
      color: #065f46;
      border-radius: 12px;
      padding: 14px 16px;
      font-size: 15px;
    }
    .error {
      margin-top: 20px;
      background: #fef2f2;
      color: #991b1b;
      border-radius: 12px;
      padding: 14px 16px;
      font-size: 15px;
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>📰 FLI Media Monitor</h1>
    <div class="sub">AI-powered media monitoring for Future of Life Institute</div>

    <div class="status">
      <strong>Client:</strong> Future of Life Institute<br>
      <strong>Topics:</strong> AI safety, AGI/ASI risk, regulation, governance, labor displacement, autonomous weapons<br>
      <strong>Slack destination:</strong> {{ channel }}<br>
      <strong>Today:</strong> {{ today }}<br>
      <strong>Freshness window:</strong> last {{ max_age }} hours<br>
      <strong>Last run:</strong> {{ last_run }}<br>
      <strong>Last result:</strong> {{ last_result }}
    </div>

    <form method="post" action="/run">
      <button class="btn" type="submit">Run clips now</button>
    </form>

    {% if message %}
      <div class="{{ 'success' if ok else 'error' }}">{{ message }}</div>
    {% endif %}

    <div class="note">
      This page triggers the clip workflow and posts the output into Slack. Same-day duplicate clips are automatically skipped.
    </div>
  </div>
</body>
</html>
"""


def load_seen_articles():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen_articles(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_run_status():
    if not os.path.exists(STATUS_FILE):
        return {}

    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_run_status(data):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_today_key():
    return date.today().isoformat()


def get_now_pt_string():
    now = datetime.now(ZoneInfo(LOCAL_TIMEZONE))
    return now.strftime("%b %d, %Y at %I:%M %p PT")


def parse_article_datetime(date_string):
    """
    Parse either ISO timestamps (NewsAPI) or RSS-style published dates.
    Return a timezone-aware datetime when possible.
    """
    if not date_string:
        return None

    try:
        return datetime.fromisoformat(date_string.replace("Z", "+00:00"))
    except Exception:
        pass

    try:
        dt = parsedate_to_datetime(date_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_fresh_enough(date_string, hours=24):
    article_dt = parse_article_datetime(date_string)
    if not article_dt:
        return False

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=hours)
    return article_dt >= cutoff


def clean_date(date_string):
    dt = parse_article_datetime(date_string)
    if not dt:
        return date_string

    try:
        local_dt = dt.astimezone(ZoneInfo(LOCAL_TIMEZONE))
        return local_dt.strftime("%b %d, %Y at %I:%M %p PT")
    except Exception:
        return date_string


def normalize_url(url):
    if not url:
        return ""
    return url.split("?")[0].rstrip("/")


def contains_exact_phrase(text, phrase):
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text.lower()) is not None


def classify_article(headline, summary):
    text = f"{headline} {summary}".lower()

    for term in FLI_TERMS:
        if contains_exact_phrase(text, term):
            return "Future of Life Institute"

    for person in SPOKESPEOPLE:
        if contains_exact_phrase(text, person):
            return "Future of Life Institute"

    for term in RELEVANCE_TERMS:
        if term.lower() in text:
            return "Relevant Coverage"

    return None


def generate_why_it_matters(category, text):
    lowered = text.lower()

    if category == "Future of Life Institute":
        return (
            "This article directly references FLI or one of its spokespeople, placing the organization "
            "in the broader conversation around AI safety, governance, and regulation."
        )

    if "labor" in lowered or "worker" in lowered or "job" in lowered or "automation" in lowered:
        return (
            "This story is relevant to FLI’s concerns about AI-driven labor displacement and the lack of "
            "safeguards around how advanced systems may reshape work."
        )

    if "autonomous weapons" in lowered or "military" in lowered or "defense" in lowered:
        return (
            "This article aligns with one of FLI’s core issue areas: the risks of advanced AI in military "
            "and autonomous weapons contexts."
        )

    if "regulation" in lowered or "policy" in lowered or "governance" in lowered:
        return (
            "This story is relevant to FLI’s push for stronger AI regulation and governance as advanced "
            "systems continue to develop faster than public safeguards."
        )

    if "agi" in lowered or "asi" in lowered or "superintelligence" in lowered:
        return (
            "This article touches on one of FLI’s core concerns: the continued acceleration toward AGI or "
            "more powerful systems despite unresolved safety and oversight risks."
        )

    return (
        "This article is relevant to FLI’s broader focus on AI safety, regulation, and the societal risks "
        "of increasingly powerful AI systems."
    )


def trim_to_n_sentences(text, max_sentences):
    text = text.strip()
    if not text:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    trimmed = " ".join(sentences[:max_sentences]).strip()

    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "."

    return trimmed


def enhance_article_with_ai(headline, summary, category):
    if not ANTHROPIC_API_KEY:
        return trim_to_n_sentences(summary, 3), None

    try:
        prompt = f"""
You are a strategic communications analyst preparing a concise daily media clip.

Your job:
1. Write a SHORT summary (MAX 3 sentences)
2. Write a sharp "Why it matters" (MAX 2 sentences)

CRITICAL RULES:
- Do NOT paste or rewrite the full article
- Do NOT include long paragraphs
- Be concise, tight, and skimmable
- Focus only on the most important takeaway
- Avoid repeating the headline
- No filler language

FLI context:
- AI safety
- AGI / ASI risk
- Regulation and governance
- Labor displacement
- Autonomous weapons

ARTICLE:
Headline: {headline}
Summary: {summary}
Category: {category}

Return EXACTLY in this format:

SUMMARY:
[2–3 sentences MAX]

WHY:
[1–2 sentences MAX]
"""

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )

        if not response.content:
            return trim_to_n_sentences(summary, 3), None

        text = response.content[0].text.strip()

        if "WHY:" in text and "SUMMARY:" in text:
            summary_part, why_part = text.split("WHY:", 1)
            summary_clean = summary_part.replace("SUMMARY:", "").strip()
            why_clean = why_part.strip()

            summary_clean = trim_to_n_sentences(summary_clean, 3)
            why_clean = trim_to_n_sentences(why_clean, 2)

            if summary_clean:
                return summary_clean, why_clean or None

        return trim_to_n_sentences(summary, 3), None

    except Exception as e:
        print(f"AI error for article '{headline}': {e}")
        return trim_to_n_sentences(summary, 3), None


def format_article(publication, headline, link, date, summary, why):
    pretty_date = clean_date(date)

    return (
        f"*{publication}* | <{link}|{headline}>\n"
        f"🕒 {pretty_date}\n\n"
        f"• *Summary:* {summary}\n\n"
        f"• *Why it matters:* {why}"
    )


def fetch_newsapi_articles():
    if not USE_NEWSAPI:
        return []

    if not NEWS_API_KEY:
        print("Missing NEWS_API_KEY in environment")
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": '("AI" AND ("regulation" OR "policy" OR "safety" OR "AGI" OR "ASI" OR "labor")) OR "Max Tegmark" OR "Future of Life Institute"',
        "domains": "reuters.com,politico.com,nytimes.com,washingtonpost.com,bloomberg.com,ft.com,wired.com,technologyreview.com,axios.com,semafor.com,theatlantic.com,cnn.com,cnbc.com,bbc.com",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": NEWS_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("articles", [])
    except Exception as e:
        print(f"NewsAPI error: {e}")
        return []


def fetch_rss_articles():
    rss_articles = []

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                published = entry.get("published", "") or entry.get("updated", "") or ""

                rss_articles.append({
                    "source": {"name": source_name},
                    "title": title,
                    "description": summary,
                    "url": link,
                    "publishedAt": published,
                    "content": summary,
                })
        except Exception as e:
            print(f"RSS error for {source_name}: {e}")

    return rss_articles


def build_narrative_summary(formatted_fli, article_texts):
    text_blob = " ".join(article_texts).lower()
    lines = []

    if "regulation" in text_blob or "policy" in text_blob or "governance" in text_blob:
        lines.append(
            "• Coverage is centering on AI regulation, governance, and whether public safeguards are keeping pace."
        )

    if "labor" in text_blob or "worker" in text_blob or "jobs" in text_blob or "automation" in text_blob:
        lines.append(
            "• Labor displacement and the impact of AI on workers continue to surface as a meaningful media theme."
        )

    if "autonomous weapons" in text_blob or "military" in text_blob or "defense" in text_blob:
        lines.append(
            "• National security and autonomous weapons risks remain part of the broader AI conversation."
        )

    if "agi" in text_blob or "asi" in text_blob or "superintelligence" in text_blob:
        lines.append(
            "• Some coverage continues to reflect concern about increasingly powerful AI systems and the risks of accelerating capability development."
        )

    if formatted_fli:
        lines.append(
            "• At least one story directly referenced FLI or one of its spokespeople, giving the organization a direct foothold in today’s coverage."
        )
    else:
        lines.append(
            "• No direct FLI or spokesperson mentions appeared in this batch, but several stories still aligned with FLI’s core issue areas."
        )

    if not lines:
        lines.append(
            "• Today’s coverage broadly aligns with FLI’s focus on AI safety, oversight, and the societal consequences of advanced AI systems."
        )

    return "🔑 *KEY NARRATIVES TODAY*\n\n" + "\n".join(lines[:4])


def build_section_message(header, articles, empty_text):
    if not articles:
        return f"{header}\n\n_{empty_text}_"
    return f"{header}\n\n" + "\n\n──────────\n\n".join(articles)


def post_threaded_clipbook(narrative_summary, formatted_fli, formatted_relevant):
    main_text = (
        "📰 *FLI DAILY CLIPBOOK*\n"
        "_What’s driving coverage today:_\n\n"
        f"{narrative_summary}\n\n"
        "_See thread for today’s clips._"
    )

    parent = client.chat_postMessage(
        channel=SLACK_CHANNEL_NAME,
        text="FLI Daily Clipbook",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": main_text[:2900]},
            }
        ],
    )

    thread_ts = parent["ts"]

    fli_message = build_section_message(
        "🧠 *FUTURE OF LIFE INSTITUTE*",
        formatted_fli,
        "No direct mentions today.",
    )

    relevant_message = build_section_message(
        "📌 *RELEVANT COVERAGE*",
        formatted_relevant,
        "No relevant coverage today.",
    )

    client.chat_postMessage(
        channel=SLACK_CHANNEL_NAME,
        thread_ts=thread_ts,
        text="Future of Life Institute clips",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": fli_message[:2900]},
            }
        ],
    )

    client.chat_postMessage(
        channel=SLACK_CHANNEL_NAME,
        thread_ts=thread_ts,
        text="Relevant Coverage clips",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": relevant_message[:2900]},
            }
        ],
    )


def run_clipbook():
    seen_data = load_seen_articles()
    today_key = get_today_key()

    if today_key not in seen_data:
        seen_data = {today_key: []}

    seen_today = set(seen_data[today_key])

    newsapi_articles = fetch_newsapi_articles()
    rss_articles = fetch_rss_articles()

    # RSS first / primary
    articles = rss_articles + newsapi_articles

    formatted_fli = []
    formatted_relevant = []
    seen_urls = set()
    article_texts = []

    for article in articles:
        publication = article.get("source", {}).get("name", "")
        if publication not in TOP_TIER_OUTLETS:
            continue

        headline = article.get("title", "") or ""
        raw_summary = article.get("description") or "No summary available."
        link = article.get("url", "") or ""
        date_string = article.get("publishedAt", "") or ""

        # Only keep fresh articles
        if not is_fresh_enough(date_string, MAX_ARTICLE_AGE_HOURS):
            continue

        clean_link = normalize_url(link)
        if not clean_link:
            continue

        if clean_link in seen_urls or clean_link in seen_today:
            continue

        seen_urls.add(clean_link)

        combined_text = " ".join([
            headline,
            raw_summary,
            article.get("content", "") or "",
        ])

        category = classify_article(headline, raw_summary)
        if not category:
            continue

        if category == "Relevant Coverage":
            lowered_combined = combined_text.lower()
            if "ai" not in lowered_combined and "artificial intelligence" not in lowered_combined:
                continue

        summary, ai_why = enhance_article_with_ai(
            headline=headline,
            summary=raw_summary,
            category=category,
        )

        article_texts.append(combined_text)
        why = ai_why if ai_why else generate_why_it_matters(category, combined_text)

        formatted = format_article(
            publication=publication,
            headline=headline,
            link=link,
            date=date_string,
            summary=summary,
            why=why,
        )

        if category == "Future of Life Institute":
            formatted_fli.append(formatted)
        else:
            formatted_relevant.append(formatted)

        seen_today.add(clean_link)

    formatted_fli = formatted_fli[:4]
    formatted_relevant = formatted_relevant[:4]

    if not formatted_fli and not formatted_relevant:
        result = {
            "ok": True,
            "message": f"No new matching clips found in the last {MAX_ARTICLE_AGE_HOURS} hours.",
        }
        save_run_status({
            "last_run": get_now_pt_string(),
            "last_result": result["message"],
        })
        return result

    narrative_summary = build_narrative_summary(formatted_fli, article_texts)
    post_threaded_clipbook(narrative_summary, formatted_fli, formatted_relevant)

    seen_data[today_key] = list(seen_today)
    save_seen_articles(seen_data)

    total = len(formatted_fli) + len(formatted_relevant)
    result = {
        "ok": True,
        "message": (
            f"Posted {total} new clips to Slack "
            f"({len(formatted_fli)} FLI / {len(formatted_relevant)} relevant coverage)."
        ),
    }

    save_run_status({
        "last_run": get_now_pt_string(),
        "last_result": result["message"],
    })

    return result


@app.route("/", methods=["GET"])
def home():
    status = load_run_status()

    return render_template_string(
        PAGE_TEMPLATE,
        channel=SLACK_CHANNEL_NAME,
        today=get_today_key(),
        max_age=MAX_ARTICLE_AGE_HOURS,
        last_run=status.get("last_run", "Not run yet"),
        last_result=status.get("last_result", "No runs yet"),
        message=request.args.get("message"),
        ok=request.args.get("ok") == "1",
    )


@app.route("/run", methods=["POST", "GET"])
def run_now():
    try:
        result = run_clipbook()

        if request.method == "POST":
            return redirect(url_for("home", message=result["message"], ok="1"))

        return result["message"], 200

    except Exception as e:
        error_message = f"Run failed: {e}"
        save_run_status({
            "last_run": get_now_pt_string(),
            "last_result": error_message,
        })

        if request.method == "POST":
            return redirect(url_for("home", message=error_message, ok="0"))

        return error_message, 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)