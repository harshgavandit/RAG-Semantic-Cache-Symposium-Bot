"""
Phase 2: Streamlit Frontend
This is the chat interface where users interact with the RAG bot.
"""

import streamlit as st
import requests
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# ============================================================================
# 1. PAGE CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="Symposium RAG Bot",
    page_icon="🤖",
    layout="centered"
)


# ============================================================================
# 2. BACKEND API CONFIGURATION
# ============================================================================

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/") + "/chat"
REQUEST_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "30")) + 30


# ============================================================================
# 3. INITIALIZE CHAT HISTORY
# ============================================================================

if "messages" not in st.session_state:
    st.session_state.messages = []


# ============================================================================
# 4. UI HEADER
# ============================================================================

st.title("🤖 Symposium RAG Bot")
st.markdown("Ask me anything about the symposium document!")
st.divider()


# ============================================================================
# 5. DISPLAY CHAT HISTORY
# ============================================================================

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        # Display cache badge + sources for assistant messages
        if message["role"] == "assistant":
            if message.get("cache_hit"):
                sim = message.get("similarity")
                st.caption(f"⚡ Cached  ·  similarity {sim:.4f}" if sim else "⚡ Cached")
            if message.get("sources"):
                with st.expander("📚 Sources"):
                    for source in message["sources"]:
                        st.caption(source)


# ============================================================================
# 6. USER INPUT HANDLING
# ============================================================================

if user_question := st.chat_input("Type your question here..."):

    # Display user message
    with st.chat_message("user"):
        st.markdown(user_question)

    # Add user message to chat history
    st.session_state.messages.append({
        "role": "user",
        "content": user_question
    })


    # ========================================================================
    # 7. CALL BACKEND API
    # ========================================================================

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):

            try:
                response = requests.post(BACKEND_URL, json={"question": user_question}, timeout=REQUEST_TIMEOUT)
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After", "60")
                    st.warning(f"Rate limit reached. Try again in {retry} seconds.")
                    st.stop()
                if not response.ok:
                    detail = response.json().get("detail", "The backend could not answer this question.")
                    st.error(detail if isinstance(detail, str) else "Please enter a valid question (up to 4,000 characters).")
                    st.stop()
                result = response.json()
                answer = result["answer"]
            except requests.Timeout:
                st.error("The request timed out. Please try again.")
                st.stop()
            except (requests.RequestException, ValueError, KeyError):
                st.error("Cannot reach the chat backend. Check that it is running and try again.")
                st.stop()
            sources = result.get("sources", [])
            cache_hit = result.get("cache_hit", False)
            similarity = result.get("similarity")


            # ====================================================================
            # 8. DISPLAY ASSISTANT RESPONSE
            # ====================================================================

            if cache_hit:
                st.caption(f"⚡ Cached  ·  similarity {similarity:.4f}" if similarity else "⚡ Cached")

            st.markdown(answer)

            # Cached and freshly generated answers retain their source pages.
            if sources:
                with st.expander("📚 Sources"):
                    for source in sources:
                        st.caption(source)


    # Add assistant message to chat history
    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "sources": sources,
        "cache_hit": cache_hit,
        "similarity": similarity,
    })
