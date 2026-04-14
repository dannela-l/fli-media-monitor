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

DEFAULT_MAX_AGE = 48
FRESHNESS_OPTIONS = [1, 3, 6, 12, 24, 48]
USE_NEWSAPI = True
MAX_AI_ARTICLES = 4

# Default FLI demo configuration
DEFAULT_CLIENT_NAME = "Future of Life Institute"
DEFAULT_TOPICS_TEXT = (
    "AI safety, AGI, ASI, AI regulation, AI policy, AI governance, "
    "autonomous weapons, labor displacement, automation, existential risk"
)

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
    "safety",
    "governance",
    "policy",
    "regulation",
}

STRONG_AI_TERMS = {
    "ai",
    "artificial intelligence",
    "generative ai",
    "foundation model",
    "frontier model",
    "large language model",
    "llm",
    "openai",
    "anthropic",
    "google deepmind",
    "google",
    "meta ai",
    "chatgpt",
    "agi",
    "asi",
    "machine learning",
    "automation",
}

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
  <title>{{ client_name }} Media Monitor</title>
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
      max-width: 820px;
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
    label {
      font-weight: 600;
    }
    input[type="text"], select {
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid #cbd5e1;
      font-size: 15px;
      background: white;
      width: 100%;
      box-sizing: border-box;
    }
    textarea {
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid #cbd5e1;
      font-size: 15px;
      background: white;
      width: 100%;
      min-height: 84px;
      box-sizing: border-box;
      resize: vertical;
      font-family: inherit;
    }
    .field {
      margin-bottom: 16px;
    }
    .check {
      margin-top: 14px;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 15px;
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
    <h1>📰 {{ client_name }} Media Monitor</h1>
    <div class="sub">AI-powered media monitoring with configurable delivery and topics</div>

    <div class="status">
      <strong>Client:</strong> {{ client_name }}<br>
      <strong>Slack destination:</strong> {{ selected_channel }}<br>
      <strong>Today:</strong> {{ today }}<br>
      <strong>Freshness window:</strong> last {{ selected_hours }} hour{{ 's' if selected_hours > 1 else '' }}<br>
      <strong>Mode:</strong> {{ "Test mode (dedupe bypassed)" if test_mode else "Normal mode" }}<br>
      <strong>Sources:</strong> RSS{% if use_newsapi %} + NewsAPI{% endif %}<br>
      <strong>Last run:</strong> {{ last_run }}<br>
      <strong>Last result:</strong> {{ last_result }}
    </div>

    <form method="post" action="/run">
      <div class="field">
        <label for="client_name">Client name</label><br><br>
        <input type="text" id="client_name" name="client_name" value="{{ client_name }}">
      </div>

      <div class="field">
        <label for="channel">Slack channel</label><br><br>
        <input type="text" id="channel" name="channel" value="{{ selected_channel }}" placeholder="#client-channel">
      </div>

      <div class="field">
        <label for="topics">Topics (comma separated)</label><br><br>
        <textarea id="topics" name="topics" placeholder="AI safety, regulation, labor displacement">{{ topics_text }}</textarea>
      </div>

      <div class="field">
        <label for="hours">Freshness window</label><br><br>
        <select name="hours" id="hours">
          {% for h in options %}
            <option value="{{ h }}" {% if h == selected_hours %}selected{% endif %}>
              Past {{ h }} hour{{ 's' if h > 1 else '' }}
            </option>
          {% endfor %}
        </select>
      </div>

      <div class="check">
        <input type="checkbox" id="test_mode" name="test_mode" value="1" {% if test_mode %}checked{% endif %}>
        <label for="test_mode">Test mode: ignore same-day duplicate filter</label>
      </div>

      <br>
      <button class="btn" type="submit">Run clips now</button>
    </form>

    {% if message %}
      <div class="{{ 'success' if ok else 'error' }}">{{ message }}</div>
    {% endif %}

    <div class="note">
      This page triggers the clip workflow and posts the output into Slack. In normal mode, same-day duplicate clips are skipped. In test mode, clips may be reposted for preview purposes. To post to a different Slack channel, the bot must already be invited there.
    </div>
  </div>
</body>
</html>
"""


def load_json_file(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_seen_articles():
    return load_json_file(STATE_FILE)


def save_seen_articles(data):
    save_json_file(STATE_FILE, data)


def load_run_status():
    return load_json_file(STATUS_FILE)


def save_run_status(data):
    save_json_file(STATUS_FILE, data)


def get_today_key():
    return date.today().isoformat()


def get_now_pt_string():
    now = datetime.now(ZoneInfo(LOCAL_TIMEZONE))
    return now.strftime("%b %d, %Y at %I:%M %p PT")


def parse_article_datetime(date_string):
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


def is_fresh_enough(date_string, hours):
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


def normalize_channel(channel):
    channel = (channel or "").strip()
    return channel or SLACK_CHANNEL_NAME


def parse_topics(topics_text):
    if not topics_text:
        return []
    return [topic.strip().lower() for topic in topics_text.split(",") if topic.strip()]


def contains_exact_phrase(text, phrase):
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text.lower()) is not None


def has_any_relevance_signal(text, user_topics):
    lowered = text.lower()

    if any(term in lowered for term in RELEVANCE_TERMS):
        return True

    if any(term in lowered for term in STRONG_AI_TERMS):
        return True

    if any(topic in lowered for topic in user_topics):
        return True

    return False


def classify_article(headline, summary, content, user_topics):
    text = f"{headline} {summary} {content}".lower()

    for term in FLI_TERMS:
        if contains_exact_phrase(text, term):
            return "Future of Life Institute"

    for person in SPOKESPEOPLE:
        if contains_exact_phrase(text, person):
            return "Future of Life Institute"

    if has_any_relevance_signal(text, user_topics):
        return "Relevant Coverage"

    return None


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
    base_summary = trim_to_n_sentences(summary or "No summary available.", 3)

    if not ANTHROPIC_API_KEY:
        return base_summary

    try:
        prompt = f"""
You are a strategic communications analyst preparing a concise daily media clip.

Your job:
Write a strong, clean summary of this article in 2 to 3 sentences.

CRITICAL RULES:
- Do NOT paste or rewrite the full article
- Do NOT include long paragraphs
- Be concise, sharp, and skimmable
- Focus only on the most important takeaway
- Avoid repeating the headline
- No filler language
- Do NOT include a separate "why it matters" section

ARTICLE:
Headline: {headline}
Summary: {summary}
Category: {category}

Return ONLY the summary text.
"""

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=180,
            messages=[{"role": "user", "content": prompt}],
        )

        if not response.content:
            return base_summary

        text = response.content[0].text.strip()
        return trim_to_n_sentences(text, 3)

    except Exception as e:
        print(f"AI error for article '{headline}': {e}")
        return base_summary


def format_article(publication, headline, link, date_string, summary):
    pretty_date = clean_date(date_string)
    return (
        f"*{publication}* | <{link}|{headline}>\n"
        f"🕒 {pretty_date}\n\n"
        f"• *Summary:* {summary}"
    )


def fetch_rss_articles():
    rss_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                rss_articles.append({
                    "source": {"name": source_name},
                    "title": entry.get("title", "") or "",
                    "description": entry.get("summary", "") or entry.get("description", "") or "",
                    "url": entry.get("link", "") or "",
                    "publishedAt": entry.get("published", "") or entry.get("updated", "") or "",
                    "content": entry.get("summary", "") or entry.get("description", "") or "",
                })
        except Exception as e:
            print(f"RSS error for {source_name}: {e}")
    return rss_articles


def fetch_newsapi_articles():
    if not USE_NEWSAPI or not NEWS_API_KEY:
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": '("AI" OR "artificial intelligence" OR "AGI" OR "ASI" OR "AI safety" OR "AI policy" OR "AI regulation" OR "automation" OR "Max Tegmark" OR "Future of Life Institute")',
        "domains": "reuters.com,politico.com,nytimes.com,washingtonpost.com,bloomberg.com,ft.com,wired.com,technologyreview.com,axios.com,semafor.com,theatlantic.com,cnn.com,cnbc.com,bbc.com",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 30,
        "apiKey": NEWS_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        articles = []
        for article in data.get("articles", []):
            articles.append({
                "source": article.get("source", {}),
                "title": article.get("title", "") or "",
                "description": article.get("description", "") or "",
                "url": article.get("url", "") or "",
                "publishedAt": article.get("publishedAt", "") or "",
                "content": article.get("content", "") or "",
            })
        return articles
    except Exception as e:
        print(f"NewsAPI error: {e}")
        return []


def build_narrative_summary(formatted_fli, article_texts, client_name):
    text_blob = " ".join(article_texts).lower()
    lines = []

    if "regulation" in text_blob or "policy" in text_blob or "governance" in text_blob:
        lines.append("• Coverage is centering on regulation, governance, and whether public safeguards are keeping pace.")
    if "labor" in text_blob or "worker" in text_blob or "jobs" in text_blob or "automation" in text_blob:
        lines.append("• Labor displacement and the impact of automation on workers continue to surface as a meaningful media theme.")
    if "autonomous weapons" in text_blob or "military" in text_blob or "defense" in text_blob:
        lines.append("• National security and autonomous weapons risks remain part of the broader conversation.")
    if "agi" in text_blob or "asi" in text_blob or "superintelligence" in text_blob:
        lines.append("• Some coverage continues to reflect concern about increasingly powerful AI systems and the risks of accelerating capability development.")

    if formatted_fli:
        lines.append(f"• At least one story directly referenced {client_name} or a known spokesperson, giving the client a direct foothold in today’s coverage.")
    else:
        lines.append("• No direct client or spokesperson mentions appeared in this batch, but several stories still aligned with the selected issue areas.")

    if not lines:
        lines.append("• Today’s coverage broadly aligns with the selected issue areas and broader media environment.")

    return "🔑 *KEY NARRATIVES TODAY*\n\n" + "\n".join(lines[:4])


def build_section_message(header, articles, empty_text):
    if not articles:
        return f"{header}\n\n_{empty_text}_"
    return f"{header}\n\n" + "\n\n──────────\n\n".join(articles)


def post_threaded_clipbook(narrative_summary, formatted_fli, formatted_relevant, channel, client_name, test_mode=False):
    title = f"{client_name} Daily Clipbook"
    if test_mode:
        title += " (Test Mode)"

    test_note = ""
    if test_mode:
        test_note = "_Test mode is ON — same-day dedupe was bypassed for this run._\n\n"

    main_text = (
        f"📰 *{client_name.upper()} DAILY CLIPBOOK*\n"
        "_What’s driving coverage today:_\n\n"
        f"{narrative_summary}\n\n"
        f"{test_note}"
        "_See thread for today’s clips._"
    )

    parent = client.chat_postMessage(
        channel=channel,
        text=title,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": main_text[:2900]}}],
    )

    thread_ts = parent["ts"]

    fli_message = build_section_message(
        "🧠 *DIRECT / SPOKESPERSON MENTIONS*",
        formatted_fli,
        "No direct mentions today.",
    )

    relevant_message = build_section_message(
        "📌 *RELEVANT COVERAGE*",
        formatted_relevant,
        "No relevant coverage today.",
    )

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="Direct mentions",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": fli_message[:2900]}}],
    )

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="Relevant coverage",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": relevant_message[:2900]}}],
    )


def run_clipbook(max_hours, test_mode=False, channel=None, topics_text=None, client_name=None):
    channel = normalize_channel(channel)
    topics_text = topics_text if topics_text is not None else DEFAULT_TOPICS_TEXT
    client_name = client_name.strip() if client_name else DEFAULT_CLIENT_NAME
    user_topics = parse_topics(topics_text)

    seen_data = load_seen_articles()
    today_key = get_today_key()

    if today_key not in seen_data:
        seen_data = {today_key: []}

    seen_today = set(seen_data[today_key])

    rss_articles = fetch_rss_articles()
    newsapi_articles = fetch_newsapi_articles()
    articles = rss_articles + newsapi_articles

    formatted_direct = []
    formatted_relevant = []
    seen_urls = set()
    article_texts = []
    ai_count = 0

    for article in articles:
        publication = article.get("source", {}).get("name", "")
        if publication not in TOP_TIER_OUTLETS:
            continue

        headline = article.get("title", "") or ""
        raw_summary = article.get("description", "") or "No summary available."
        link = article.get("url", "") or ""
        date_string = article.get("publishedAt", "") or ""
        content = article.get("content", "") or ""

        if not is_fresh_enough(date_string, max_hours):
            continue

        clean_link = normalize_url(link)
        if not clean_link:
            continue

        if clean_link in seen_urls:
            continue

        if not test_mode and clean_link in seen_today:
            continue

        seen_urls.add(clean_link)

        category = classify_article(headline, raw_summary, content, user_topics)
        if not category:
            continue

        combined_text = " ".join([headline, raw_summary, content])

        if ai_count < MAX_AI_ARTICLES:
            summary = enhance_article_with_ai(
                headline=headline,
                summary=raw_summary,
                category=category,
            )
            ai_count += 1
        else:
            summary = trim_to_n_sentences(raw_summary, 3)

        article_texts.append(combined_text)

        formatted = format_article(
            publication=publication,
            headline=headline,
            link=link,
            date_string=date_string,
            summary=summary,
        )

        if category == "Future of Life Institute":
            formatted_direct.append(formatted)
        else:
            formatted_relevant.append(formatted)

        if not test_mode:
            seen_today.add(clean_link)

    formatted_direct = formatted_direct[:3]
    formatted_relevant = formatted_relevant[:3]

    if not formatted_direct and not formatted_relevant:
        result = {
            "ok": True,
            "message": (
                f"No matching clips found in the last {max_hours} hours."
                if not test_mode
                else f"No matching clips found in the last {max_hours} hours, even in test mode."
            ),
        }
        save_run_status({
            "last_run": get_now_pt_string(),
            "last_result": result["message"],
        })
        return result

    narrative_summary = build_narrative_summary(formatted_direct, article_texts, client_name)
    post_threaded_clipbook(
        narrative_summary=narrative_summary,
        formatted_fli=formatted_direct,
        formatted_relevant=formatted_relevant,
        channel=channel,
        client_name=client_name,
        test_mode=test_mode,
    )

    if not test_mode:
        seen_data[today_key] = list(seen_today)
        save_seen_articles(seen_data)

    total = len(formatted_direct) + len(formatted_relevant)
    result = {
        "ok": True,
        "message": (
            f"Posted {total} clip(s) to Slack "
            f"({len(formatted_direct)} direct / {len(formatted_relevant)} relevant) "
            f"— last {max_hours}h window"
            f"{' [TEST MODE]' if test_mode else ''}."
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
    selected_hours = int(request.args.get("hours", DEFAULT_MAX_AGE))
    test_mode = request.args.get("test_mode", "0") == "1"
    selected_channel = request.args.get("channel", SLACK_CHANNEL_NAME)
    topics_text = request.args.get("topics", DEFAULT_TOPICS_TEXT)
    client_name = request.args.get("client_name", DEFAULT_CLIENT_NAME)

    return render_template_string(
        PAGE_TEMPLATE,
        channel=SLACK_CHANNEL_NAME,
        today=get_today_key(),
        last_run=status.get("last_run", "Not run yet"),
        last_result=status.get("last_result", "No runs yet"),
        message=request.args.get("message"),
        ok=request.args.get("ok") == "1",
        options=FRESHNESS_OPTIONS,
        selected_hours=selected_hours,
        test_mode=test_mode,
        use_newsapi=USE_NEWSAPI,
        selected_channel=selected_channel,
        topics_text=topics_text,
        client_name=client_name,
    )


@app.route("/run", methods=["POST", "GET"])
def run_now():
    try:
        if request.method == "POST":
            hours = int(request.form.get("hours", DEFAULT_MAX_AGE))
            test_mode = request.form.get("test_mode") == "1"
            channel = request.form.get("channel", SLACK_CHANNEL_NAME)
            topics_text = request.form.get("topics", DEFAULT_TOPICS_TEXT)
            client_name = request.form.get("client_name", DEFAULT_CLIENT_NAME)
        else:
            hours = int(request.args.get("hours", DEFAULT_MAX_AGE))
            test_mode = request.args.get("test_mode", "0") == "1"
            channel = request.args.get("channel", SLACK_CHANNEL_NAME)
            topics_text = request.args.get("topics", DEFAULT_TOPICS_TEXT)
            client_name = request.args.get("client_name", DEFAULT_CLIENT_NAME)

        result = run_clipbook(
            max_hours=hours,
            test_mode=test_mode,
            channel=channel,
            topics_text=topics_text,
            client_name=client_name,
        )

        if request.method == "POST":
            return redirect(url_for(
                "home",
                message=result["message"],
                ok="1",
                hours=hours,
                test_mode="1" if test_mode else "0",
                channel=channel,
                topics=topics_text,
                client_name=client_name,
            ))

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