import os
import json
import threading
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime, time as dtime
from typing import List, Dict, Any, Optional, Tuple

import streamlit as st
from dotenv import load_dotenv
from google import genai


# ---------------- ENV & GLOBAL CONFIG ----------------

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()


# ---------------- LLM (GEMINI) CLIENT ----------------

def call_llm(messages: List[Dict[str, str]]) -> Tuple[Optional[str], Optional[str]]:
    """
    Call Gemini as a generic chat LLM.
    messages = [{"role": "system"|"user"|"assistant", "content": "..."}]
    Returns (text, error_message). Never raises.
    """
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY is missing. Put it in a .env file."

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Flatten chat into a single prompt string
        full_prompt_parts: List[str] = []
        for m in messages:
            role = m.get("role", "user").upper()
            content = m.get("content", "")
            full_prompt_parts.append(f"{role}:\n{content}\n")
        full_prompt = "\n".join(full_prompt_parts).strip()

        resp = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=full_prompt,
        )
        text = (resp.text or "").strip()
        if not text:
            return None, "Gemini returned an empty response."
        return text, None
    except Exception as e:
        return None, f"LLM request failed: {e}"


def build_email_from_topic(
    user_prompt: str,
    tone: str,
    language: str,
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """
    Asks the LLM to turn the user's description into:
    { "subject": "...", "body": "..." }
    Returns (email_dict, error_message).
    """
    system_msg = {
        "role": "system",
        "content": f"""
You are an email-writing assistant for business workflows.

The user will describe:
- who they are emailing,
- why,
- key points and context.

You MUST respond ONLY with a valid JSON object, no extra text:

{{
  "subject": "...",
  "body": "..."
}}

Rules:
- Language: {language}
- Tone: {tone}
  Tone options: formal, friendly, business, apologetic, angry, promotional.
- If tone is "angry", keep it professional and respectful (no insults, no threats).
- The email must be clear, concise, and ready to send.
""",
    }

    user_msg = {
        "role": "user",
        "content": user_prompt,
    }

    text, err = call_llm([system_msg, user_msg])
    if err:
        return None, err

    # Parse JSON from the model output
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON substring if the model adds extra text
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, f"LLM output was not valid JSON:\n{text}"
        try:
            data = json.loads(match.group(0))
        except Exception as e:
            return None, f"JSON parse error: {e}\nRaw output:\n{text}"

    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()
    if not subject or not body:
        return None, "AI returned an empty subject or body."

    return {"subject": subject, "body": body}, None


# ---------------- EMAIL SENDING & SCHEDULING ----------------

def send_email_now(
    to_email: str,
    subject: str,
    body: str,
) -> Tuple[bool, str]:
    """
    Sends an email immediately using SMTP_* config.
    Returns (success, message). Never raises.
    """
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return False, "SMTP credentials missing. Set SMTP_EMAIL and SMTP_PASSWORD in .env."

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())

        return True, f"Sent successfully to {to_email}."
    except Exception as e:
        return False, f"Send failed: {e}"


def schedule_email(
    send_at: datetime,
    to_email: str,
    subject: str,
    body: str,
) -> Tuple[bool, str]:
    """
    Schedules an email using a background timer.
    Returns (success, message) for scheduling itself.
    """
    def task():
        ok, msg = send_email_now(to_email, subject, body)
        print("[SCHEDULED EMAIL]", msg)

    try:
        now = datetime.now()
        delay = (send_at - now).total_seconds()
        if delay <= 0:
            ok, msg = send_email_now(to_email, subject, body)
            return ok, msg

        t = threading.Timer(delay, task)
        t.daemon = True
        t.start()
        return True, f"Email scheduled for {send_at} to {to_email}."
    except Exception as e:
        return False, f"Scheduling failed: {e}"


# ---------------- STREAMLIT SESSION HELPERS ----------------

def init_state() -> None:
    if "chat" not in st.session_state:
        st.session_state.chat: List[Dict[str, str]] = []
    if "draft" not in st.session_state:
        st.session_state.draft: Optional[Dict[str, str]] = None


def add_chat(role: str, content: str) -> None:
    st.session_state.chat.append({"role": role, "content": content})


# ---------------- STREAMLIT APP ----------------

def main() -> None:
    init_state()

    st.set_page_config(
        page_title="Enterprise Email Automation Agent",
        page_icon="📧",
        layout="centered",
    )
    st.title("📧 Enterprise Email Automation Agent")

    st.caption("Track: C – Enterprise Agents (LLM-powered email automation).")

    # Sidebar: show config status
    st.sidebar.header("Configuration status")
    st.sidebar.markdown(f"- Gemini API key set: **{bool(GEMINI_API_KEY)}**")
    st.sidebar.markdown(f"- Model name: `{GEMINI_MODEL_NAME or 'not set'}`")
    st.sidebar.markdown(f"- SMTP email set: **{bool(SMTP_EMAIL)}**")
    st.sidebar.markdown(f"- SMTP host: `{SMTP_HOST}`")

    st.subheader("Email style")
    col1, col2 = st.columns(2)
    with col1:
        language = st.selectbox(
            "Language",
            ["English", "Spanish", "French", "German", "Hindi", "Tamil"],
            index=0,
        )
    with col2:
        tone = st.selectbox(
            "Tone",
            ["formal", "friendly", "business", "apologetic", "angry", "promotional"],
            index=2,
        )

    st.markdown("---")

    # Show chat history
    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Show current draft email if exists
    draft = st.session_state.draft
    if draft:
        with st.expander("Current email draft", expanded=True):
            st.markdown(f"**Subject:** {draft['subject']}")
            st.text_area(
                "Body",
                draft["body"],
                height=220,
                key="draft_body_display",
                disabled=True,
            )

        st.markdown("### Send / Schedule")

        to_email = st.text_input("Recipient email")

        col3, col4 = st.columns(2)
        with col3:
            send_now_clicked = st.button("📤 Send now")
        with col4:
            schedule_clicked = st.button("⏰ Schedule send")

        send_at: Optional[datetime] = None
        if schedule_clicked:
            st.markdown("#### Choose schedule time")
            date_val = st.date_input("Date", datetime.now().date(), key="sched_date")
            time_default = dtime(
                hour=datetime.now().hour,
                minute=(datetime.now().minute + 2) % 60,
            )
            time_val = st.time_input("Time", time_default, key="sched_time")
            send_at = datetime.combine(date_val, time_val)

        if send_now_clicked:
            if not to_email:
                add_chat("assistant", "I need the recipient email before I can send.")
            else:
                ok, msg = send_email_now(to_email, draft["subject"], draft["body"])
                add_chat("assistant", msg)

        if schedule_clicked and send_at:
            if not to_email:
                add_chat("assistant", "I need the recipient email before scheduling.")
            else:
                ok, msg = schedule_email(send_at, to_email, draft["subject"], draft["body"])
                add_chat("assistant", msg)

    # Chat input for new instructions
    user_text = st.chat_input(
        "Describe the email you want (who, why, details of the message)..."
    )

    if user_text:
        add_chat("user", user_text)

        with st.chat_message("assistant"):
            st.markdown("Let me write a good email for that…")

        email_dict, err = build_email_from_topic(
            user_prompt=user_text,
            tone=tone,
            language=language,
        )
        if err:
            add_chat(
                "assistant",
                f"I tried to build the email but hit a problem:\n\n`{err}`\n\n"
                "Check your Gemini API key and try again.",
            )
        else:
            st.session_state.draft = email_dict
            add_chat(
                "assistant",
                f"Here’s your draft:\n\n"
                f"**Subject:** {email_dict['subject']}\n\n"
                f"**Body:**\n\n{email_dict['body']}\n\n"
                "Now type the recipient email and choose **Send now** or **Schedule send**.",
            )

    # Initial welcome message (only once)
    if not st.session_state.chat:
        add_chat(
            "assistant",
            "Hi, I’m your email automation bot. 👋\n\n"
            "1. Tell me what email you want to send (who, why, key points).\n"
            "2. I’ll generate the subject and body in the style you chose.\n"
            "3. Then you can send it immediately or schedule it for later.",
        )
        st.rerun()


if __name__ == "__main__":
    main()
