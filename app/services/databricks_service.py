import base64
import mimetypes
import re
from typing import Iterable

from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError, OpenAI

from app.settings import Settings


FEATURE_PROMPTS = {
    "TextAI": "You are TextAI. Answer clearly and directly based on the user's text request.",
    "WikiAI": "You are WikiAI. Organize the response like a wiki note with overview, key points, and references to missing information.",
    "JiraAI": "You are JiraAI. Convert the input into a concise Jira-style output with summary, description, acceptance criteria, and risks.",
    "ConfluenceAI": "You are ConfluenceAI. Rewrite the input as a Confluence-ready page section with title, summary, details, and action items.",
    "Sentiment AnalysisAI": "You are Sentiment AnalysisAI. Return sentiment, confidence, and a short explanation from the provided text.",
}


class DatabricksService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.databricks_token,
            base_url=self._build_base_url(settings.databricks_host),
        )

    def _normalized_image_prompt(self) -> str:
        prompt = (self.settings.databricks_image_description_prompt or "").strip()
        if len(prompt) >= 2 and prompt[0] == prompt[-1] and prompt[0] in {'"', "'"}:
            prompt = prompt[1:-1].strip()
        base_prompt = prompt or "Describe the image accurately and answer the user's prompt."
        return (
            f"{base_prompt} Return exactly one plain sentence. "
            "Do not use headings, markdown, bullet points, labels, or section titles."
        )

    def _normalize_image_response(self, content: str) -> str:
        text = (content or "").strip()
        if not text:
            return "No response returned."

        text = re.sub(r"^#{1,6}\s+.*$", " ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[-*+]\s+", " ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[A-Za-z][A-Za-z\s&/()-]{1,40}:\s*", " ", text, flags=re.MULTILINE)
        text = re.sub(r"\s+", " ", text).strip(" .,:;\n\t")

        if not text:
            return "No response returned."

        if text[-1] not in ".!?":
            text = f"{text}."
        return text

    def _build_base_url(self, host: str) -> str:
        normalized = (host or "").strip().rstrip("/")
        if normalized.endswith("/serving-endpoints"):
            normalized = normalized[: -len("/serving-endpoints")]
        return f"{normalized}/serving-endpoints" if normalized else ""

    def _raise_api_error(self, exc: Exception) -> None:
        if isinstance(exc, APIConnectionError):
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not connect to Databricks serving endpoints. Verify DATABRICKS_HOST uses only the workspace URL, "
                    "for example https://<workspace>.azuredatabricks.net, and confirm the workspace is reachable from this machine."
                ),
            ) from exc

        if isinstance(exc, APIStatusError):
            status_code = exc.status_code if isinstance(exc.status_code, int) else 502
            raise HTTPException(
                status_code=status_code,
                detail=(
                    f"Databricks request failed with status {status_code}. Check the token and confirm the configured model names "
                    "are Databricks serving endpoint names, not raw foundation model IDs."
                ),
            ) from exc

        raise exc
    def validate_configuration(self) -> None:
        if not self.settings.databricks_host or not self.settings.databricks_token:
            raise HTTPException(
                status_code=500,
                detail="Databricks configuration is missing. Set DATABRICKS_HOST and DATABRICKS_TOKEN in the .env file.",
            )

    def run_feature(self, feature: str, text: str) -> str:
        self.validate_configuration()
        if feature not in FEATURE_PROMPTS:
            raise HTTPException(status_code=404, detail="Unsupported AI feature.")

        try:
            completion = self.client.chat.completions.create(
                model=self.settings.databricks_chat_model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": FEATURE_PROMPTS[feature]},
                    {"role": "user", "content": text},
                ],
            )
        except Exception as exc:
            self._raise_api_error(exc)
        return self._normalize_image_response(completion.choices[0].message.content or "")

    def describe_image(self, prompt: str, image_bytes: bytes, filename: str) -> str:
        self.validate_configuration()
        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{encoded}"

        try:
            completion = self.client.chat.completions.create(
                model=self.settings.databricks_vision_model,
                temperature=0.2,
                messages=[
                    {
                        "role": "system",
                        "content": self._normalized_image_prompt(),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            )
        except Exception as exc:
            self._raise_api_error(exc)
        return completion.choices[0].message.content or "No response returned."

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        self.validate_configuration()
        prepared_texts = list(texts)
        if not prepared_texts:
            return []

        try:
            response = self.client.embeddings.create(
                model=self.settings.databricks_embedding_model,
                input=prepared_texts,
            )
        except Exception as exc:
            self._raise_api_error(exc)
        return [item.embedding for item in response.data]

    def chat_with_context(self, question: str, context: str, history: list[dict[str, str]]) -> str:
        self.validate_configuration()
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are ChatbotAI. Answer only from the provided context. "
                    "Do not use outside knowledge. If the answer is not explicitly supported by the context, say exactly: "
                    "The document does not contain that information. Keep the answer concise and factual."
                ),
            }
        ]
        messages.extend(history[-6:])
        messages.append(
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion:\n{question}",
            }
        )

        try:
            completion = self.client.chat.completions.create(
                model=self.settings.databricks_chat_model,
                temperature=0,
                messages=messages,
            )
        except Exception as exc:
            self._raise_api_error(exc)
        return completion.choices[0].message.content or "No response returned."

    def chat_with_source_context(
        self,
        source: str,
        question: str,
        context: str,
        history: list[dict[str, str]],
    ) -> str:
        self.validate_configuration()
        source_label = source.upper()
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"You are ChatbotAI connected to {source_label}. Answer only from the retrieved {source_label} context. "
                    "Do not use outside knowledge. If the answer is not supported by the retrieved context, say exactly: "
                    f"The selected {source_label} source does not contain that information. Keep the answer concise and factual."
                ),
            }
        ]
        messages.extend(history[-6:])
        messages.append(
            {
                "role": "user",
                "content": f"Retrieved {source_label} context:\n{context}\n\nQuestion:\n{question}",
            }
        )

        try:
            completion = self.client.chat.completions.create(
                model=self.settings.databricks_chat_model,
                temperature=0,
                messages=messages,
            )
        except Exception as exc:
            self._raise_api_error(exc)
        return completion.choices[0].message.content or "No response returned."