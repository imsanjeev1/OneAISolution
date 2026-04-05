# One AI Solution

Single-page FastAPI application with a left-side AI menu for:

- TextAI
- TextToimageAI
- WikiAI
- JiraAI
- ConfluenceAI
- Sentiment AnalysisAI
- ChatbotAI

## What it does

- Uses Databricks serving endpoints for text, sentiment, structured assistant, and vision tasks.
- Provides a TextToimageAI workflow with a text instruction and image upload, then returns image details in a textarea.
- Provides a ChatbotAI workflow that uploads PDF or Word documents, stores the extracted chunks in Chroma DB, and chats over that content using a RAG pattern.
- Keeps all workflows in a single-page UI with a left navigation menu.

## Project structure

```text
app/
  main.py
  schemas.py
  settings.py
  services/
    databricks_service.py
    rag_service.py
  static/
    app.js
    styles.css
  templates/
    index.html
data/
  chroma/
  uploads/
requirements.txt
```

## Setup

1. Create a `.env` file from `.env.example`.
2. Fill in the Databricks host, token, and endpoint names.
3. Install dependencies:

```powershell
pip install -r requirements.txt
```

4. Start the application:

```powershell
uvicorn app.main:app --reload
```

5. Open `http://127.0.0.1:8000`.

## Databricks requirements

You need three Databricks serving endpoints:

- A chat model endpoint for TextAI, WikiAI, JiraAI, ConfluenceAI, Sentiment AnalysisAI, and ChatbotAI answers
- A vision-capable model endpoint for TextToimageAI image analysis
- An embedding model endpoint for Chroma retrieval in ChatbotAI

## ChatbotAI notes

- Uploaded documents are saved under `data/uploads`.
- Extracted chunks are stored in Chroma under `data/chroma`.
- PDF and DOCX are supported directly.
- Legacy binary DOC files are accepted only when they contain readable text. For standard Word `.doc` files, convert to `.docx` if extraction fails.

## Live source connectors

- Wiki mode supports both MediaWiki API URLs such as `https://en.wikipedia.org/w/api.php` and direct corporate wiki page URLs. If `WIKI_TOKEN` is set, the app now sends it automatically as `Basic` or `Bearer` auth depending on the token format.
- Confluence mode requires `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`, and `CONFLUENCE_API_TOKEN`.
- Jira mode requires `JIRA_BASE_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN`.
- `CONFLUENCE_SPACE_KEY` and `JIRA_PROJECT_KEY` are optional filters.