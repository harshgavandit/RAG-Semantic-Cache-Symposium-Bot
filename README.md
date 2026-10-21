# Symposium RAG Bot

A chatbot that answers questions from a symposium PDF. You can ask about the event date, venue, schedule, eligibility, competitions and registration fees.

[Open the app](https://symposium-rag-bot.vercel.app)

The app finds the relevant parts of the document and uses them to answer your question. Each answer includes page references so you can check where the information came from.

- Repeated or similar questions can reuse a saved answer instead of generating it again.
- Cached answers keep their original source references.
- If the document does not contain the answer, the bot says so.
- Request limits control how many questions can be submitted in a short period.

The live demo uses a fictional Innovate 2026 event guide. It answers from that guide rather than searching the web. A Streamlit interface is also available for running the app locally with your own symposium PDF.
