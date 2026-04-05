import base64
import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.settings import Settings


TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class SourceRecord:
    title: str
    content: str
    url: str
    label: str


class KnowledgeSourceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search(self, source: str, query: str) -> tuple[str, list[str]]:
        normalized = (source or "").strip().lower()
        if normalized == "wiki":
            records = self._search_wiki(query)
        elif normalized == "confluence":
            records = self._search_confluence(query)
        elif normalized == "jira":
            records = self._search_jira(query)
        else:
            raise HTTPException(status_code=400, detail="Unsupported chatbot source.")

        if not records:
            raise HTTPException(status_code=404, detail=f"No {normalized} results matched the current question.")

        context = "\n\n---\n\n".join(
            f"Title: {record.title}\nReference: {record.url}\nContent:\n{record.content}"
            for record in records
        )
        return context, [record.label for record in records]

    def _search_wiki(self, query: str) -> list[SourceRecord]:
        if self._is_mediawiki_api_url(self.settings.wiki_api_url):
            return self._search_mediawiki(query)
        return self._fetch_wiki_page()

    def _search_mediawiki(self, query: str) -> list[SourceRecord]:
        search_url = self._build_url(
            self.settings.wiki_api_url,
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": "1",
                "srlimit": str(max(1, self.settings.wiki_search_limit)),
            },
        )
        payload = self._request_json(search_url, headers=self._wiki_headers())
        results = payload.get("query", {}).get("search", [])
        titles = [item.get("title", "").strip() for item in results if item.get("title")]
        if not titles:
            return []

        extracts_url = self._build_url(
            self.settings.wiki_api_url,
            {
                "action": "query",
                "prop": "extracts|info",
                "inprop": "url",
                "explaintext": "1",
                "redirects": "1",
                "format": "json",
                "titles": "|".join(titles),
            },
        )
        detail_payload = self._request_json(extracts_url, headers=self._wiki_headers())
        pages = detail_payload.get("query", {}).get("pages", {})

        records: list[SourceRecord] = []
        for page in pages.values():
            title = (page.get("title") or "Untitled wiki page").strip()
            content = self._normalize_text(page.get("extract") or "")
            if not content:
                continue
            url = page.get("fullurl") or self._default_wiki_page_url(title)
            records.append(
                SourceRecord(
                    title=title,
                    content=self._trim_content(content),
                    url=url,
                    label=f"Wiki: {title}",
                )
            )
        return records[: self.settings.wiki_search_limit]

    def _fetch_wiki_page(self) -> list[SourceRecord]:
        page_url = (self.settings.wiki_api_url or "").strip()
        if not page_url:
            return []

        if self._is_confluence_page_url(page_url):
            record = self._fetch_confluence_backed_wiki_page(page_url)
            return [record] if record else []

        html = self._request_text_with_fallback(page_url, headers_list=self._wiki_header_candidates(accept_json=False))
        content = self._extract_wiki_page_content(html)
        if not content:
            return []

        title = self._extract_wiki_page_title(page_url, html)
        return [
            SourceRecord(
                title=title,
                content=self._trim_content(content),
                url=page_url,
                label=f"Wiki: {title}",
            )
        ]

    def _fetch_confluence_backed_wiki_page(self, page_url: str) -> SourceRecord | None:
        parsed = urlparse(page_url)
        params = parse_qs(parsed.query)
        page_id = (params.get("pageId") or [""])[0].strip()
        title = (params.get("title") or [""])[0].strip()
        space_key = (params.get("spaceKey") or [""])[0].strip()
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        urls: list[str] = []
        if page_id:
            urls.extend(
                [
                    f"{base_url}/rest/api/content/{quote(page_id)}?expand=body.storage,version",
                    f"{base_url}/wiki/rest/api/content/{quote(page_id)}?expand=body.storage,version",
                ]
            )
        elif title:
            query_params = {"title": title, "expand": "body.storage,version"}
            if space_key:
                query_params["spaceKey"] = space_key
            urls.extend(
                [
                    self._build_url(f"{base_url}/rest/api/content", query_params),
                    self._build_url(f"{base_url}/wiki/rest/api/content", query_params),
                ]
            )
        else:
            return None

        payload = self._request_json_with_fallback(urls, headers_list=self._wiki_header_candidates(accept_json=True))
        content_record = self._extract_confluence_content_record(payload)
        if not content_record:
            return None

        record_title = (content_record.get("title") or title or "Wiki Page").strip()
        body = content_record.get("body", {}).get("storage", {}).get("value", "")
        normalized_body = self._normalize_html(body)
        if not normalized_body:
            return None

        return SourceRecord(
            title=record_title,
            content=self._trim_content(normalized_body),
            url=page_url,
            label=f"Wiki: {record_title}",
        )

    def _search_confluence(self, query: str) -> list[SourceRecord]:
        self._require_basic_settings(
            source="Confluence",
            base_url=self.settings.confluence_base_url,
            username=self.settings.confluence_email,
            token=self.settings.confluence_api_token,
        )
        base_url = self._normalize_base_url(self.settings.confluence_base_url)
        cql_parts = ['type = "page"', f'text ~ "{self._escape_cql(query)}"']
        if self.settings.confluence_space_key:
            cql_parts.append(f'space = "{self._escape_cql(self.settings.confluence_space_key)}"')
        search_url = self._build_url(
            f"{base_url}/wiki/rest/api/search",
            {
                "cql": " AND ".join(cql_parts),
                "limit": str(max(1, self.settings.confluence_search_limit)),
                "expand": "content.body.storage,content.version",
            },
        )
        payload = self._request_json(
            search_url,
            headers=self._basic_auth_headers(self.settings.confluence_email, self.settings.confluence_api_token),
        )

        records: list[SourceRecord] = []
        for item in payload.get("results", []):
            content = item.get("content", {})
            title = (content.get("title") or item.get("title") or "Untitled Confluence page").strip()
            body = content.get("body", {}).get("storage", {}).get("value", "")
            normalized_body = self._normalize_html(body)
            if not normalized_body:
                continue
            url = self._resolve_confluence_url(base_url, item, content)
            records.append(
                SourceRecord(
                    title=title,
                    content=self._trim_content(normalized_body),
                    url=url,
                    label=f"Confluence: {title}",
                )
            )
        return records[: self.settings.confluence_search_limit]

    def _search_jira(self, query: str) -> list[SourceRecord]:
        self._require_basic_settings(
            source="Jira",
            base_url=self.settings.jira_base_url,
            username=self.settings.jira_email,
            token=self.settings.jira_api_token,
        )
        base_url = self._normalize_base_url(self.settings.jira_base_url)
        jql_parts = [f'text ~ "{self._escape_cql(query)}"']
        if self.settings.jira_project_key:
            jql_parts.append(f'project = "{self._escape_cql(self.settings.jira_project_key)}"')

        payload = self._request_json(
            f"{base_url}/rest/api/3/search",
            headers={
                **self._basic_auth_headers(self.settings.jira_email, self.settings.jira_api_token),
                "Content-Type": "application/json",
            },
            method="POST",
            body={
                "jql": " AND ".join(jql_parts),
                "maxResults": max(1, self.settings.jira_search_limit),
                "fields": ["summary", "description", "status", "issuetype", "project"],
            },
        )

        records: list[SourceRecord] = []
        for issue in payload.get("issues", []):
            key = issue.get("key") or "UNKNOWN"
            fields = issue.get("fields", {})
            summary = (fields.get("summary") or "Untitled Jira issue").strip()
            status = fields.get("status", {}).get("name") or "Unknown"
            issue_type = fields.get("issuetype", {}).get("name") or "Issue"
            description = self._extract_jira_text(fields.get("description"))
            content = self._trim_content(
                self._normalize_text(
                    f"Key: {key}\nType: {issue_type}\nStatus: {status}\nSummary: {summary}\nDescription: {description or 'No description provided.'}"
                )
            )
            records.append(
                SourceRecord(
                    title=f"{key} - {summary}",
                    content=content,
                    url=f"{base_url}/browse/{quote(key)}",
                    label=f"Jira: {key}",
                )
            )
        return records[: self.settings.jira_search_limit]

    def _request_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> dict:
        payload = self._request_text(url, headers=headers, method=method, body=body)

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="External source returned invalid JSON.") from exc

    def _request_json_with_fallback(
        self,
        urls: list[str],
        headers_list: list[dict[str, str]],
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> dict:
        last_error: HTTPException | None = None
        for url in urls:
            for headers in headers_list:
                try:
                    return self._request_json(url, headers=headers, method=method, body=body)
                except HTTPException as exc:
                    last_error = exc
                    if exc.status_code not in {401, 403, 404, 502}:
                        raise
                    continue

        if last_error is not None:
            raise last_error
        raise HTTPException(status_code=502, detail="External source request failed.")

    def _request_text(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> str:
        encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=encoded_body, method=method.upper())
        for key, value in (headers or {}).items():
            request.add_header(key, value)

        try:
            with urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="ignore")[:400]
            raise HTTPException(
                status_code=502,
                detail=f"{method.upper()} request to external source failed with status {exc.code}: {response_body or exc.reason}",
            ) from exc
        except URLError as exc:
            raise HTTPException(status_code=502, detail=f"Could not connect to external source: {exc.reason}") from exc

    def _request_text_with_fallback(
        self,
        url: str,
        headers_list: list[dict[str, str]],
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> str:
        last_error: HTTPException | None = None
        for headers in headers_list:
            try:
                return self._request_text(url, headers=headers, method=method, body=body)
            except HTTPException as exc:
                last_error = exc
                if exc.status_code not in {401, 403, 404, 502}:
                    raise
                continue

        if last_error is not None:
            raise last_error
        raise HTTPException(status_code=502, detail="External source request failed.")

    def _build_url(self, base_url: str, params: dict[str, str]) -> str:
        query = urlencode(params, doseq=True)
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{query}"

    def _basic_auth_headers(self, username: str, token: str) -> dict[str, str]:
        raw = f"{username}:{token}".encode("utf-8")
        return {
            "Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}",
            "Accept": "application/json",
        }

    def _wiki_headers(self, accept_json: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json" if accept_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        authorization = self._wiki_authorization_header()
        if authorization:
            headers["Authorization"] = authorization
        return headers

    def _wiki_header_candidates(self, accept_json: bool = True) -> list[dict[str, str]]:
        accept_value = "application/json" if accept_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers_list: list[dict[str, str]] = [{"Accept": accept_value}]
        for authorization in self._wiki_authorization_candidates():
            headers_list.append({"Accept": accept_value, "Authorization": authorization})
        return headers_list

    def _wiki_authorization_header(self) -> str:
        candidates = self._wiki_authorization_candidates()
        return candidates[0] if candidates else ""

    def _wiki_authorization_candidates(self) -> list[str]:
        token = (self.settings.wiki_token or "").strip()
        if not token:
            return []

        if token.lower().startswith("bearer ") or token.lower().startswith("basic "):
            return [token]

        candidates: list[str] = []
        decoded = self._try_base64_decode(token)
        if decoded and ":" in decoded:
            candidates.append(f"Basic {token}")

        candidates.append(f"Bearer {token}")

        if ":" in token:
            basic_value = base64.b64encode(token.encode("utf-8")).decode("ascii")
            candidates.append(f"Basic {basic_value}")

        deduplicated: list[str] = []
        for candidate in candidates:
            if candidate not in deduplicated:
                deduplicated.append(candidate)
        return deduplicated

    def _require_basic_settings(self, source: str, base_url: str, username: str, token: str) -> None:
        if base_url and username and token:
            return
        raise HTTPException(
            status_code=500,
            detail=(
                f"{source} connection is not configured. Set the base URL, username/email, and API token in the .env file."
            ),
        )

    def _normalize_base_url(self, base_url: str) -> str:
        return (base_url or "").strip().rstrip("/")

    def _resolve_confluence_url(self, base_url: str, item: dict, content: dict) -> str:
        relative_url = item.get("url") or content.get("_links", {}).get("webui") or ""
        if relative_url.startswith("http://") or relative_url.startswith("https://"):
            return relative_url
        if relative_url.startswith("/"):
            return f"{base_url}{relative_url}"
        if relative_url:
            return f"{base_url}/wiki{relative_url if relative_url.startswith('/') else '/' + relative_url}"
        content_id = content.get("id")
        return f"{base_url}/wiki/pages/viewpage.action?pageId={quote(str(content_id or ''))}"

    def _default_wiki_page_url(self, title: str) -> str:
        api_url = self.settings.wiki_api_url
        if "w/api.php" in api_url:
            root = api_url.split("/w/api.php", 1)[0]
            return f"{root}/wiki/{quote(title.replace(' ', '_'))}"
        return api_url

    def _is_mediawiki_api_url(self, url: str) -> bool:
        normalized = (url or "").strip().lower()
        return normalized.endswith("/api.php") or "/api.php?" in normalized or "w/api.php" in normalized

    def _is_confluence_page_url(self, url: str) -> bool:
        normalized = (url or "").strip().lower()
        return "viewpage.action" in normalized or "/pages/" in normalized

    def _extract_confluence_content_record(self, payload: dict) -> dict[str, object] | None:
        if isinstance(payload.get("body"), dict):
            return payload

        results = payload.get("results")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                return first

        return None

    def _extract_wiki_page_content(self, html: str) -> str:
        cleaned = re.sub(r"<script.*?</script>", " ", html or "", flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<style.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<noscript.*?</noscript>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        return self._normalize_html(cleaned)

    def _extract_wiki_page_title(self, page_url: str, html: str) -> str:
        parsed = urlparse(page_url)
        params = parse_qs(parsed.query)
        title_param = params.get("title", [""])[0].strip()
        if title_param:
            return title_param

        match = re.search(r"<title>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = self._normalize_text(unescape(match.group(1)))
            if title:
                return title

        return "Wiki Page"

    def _try_base64_decode(self, value: str) -> str:
        try:
            return base64.b64decode(value, validate=True).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _normalize_html(self, value: str) -> str:
        text = TAG_RE.sub(" ", unescape(value or ""))
        return self._normalize_text(text)

    def _normalize_text(self, value: str) -> str:
        return WHITESPACE_RE.sub(" ", (value or "").strip())

    def _trim_content(self, value: str, limit: int = 2000) -> str:
        text = self._normalize_text(value)
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _extract_jira_text(self, value: object) -> str:
        parts: list[str] = []
        self._walk_jira_text(value, parts)
        return self._normalize_text(" ".join(parts))

    def _walk_jira_text(self, value: object, parts: list[str]) -> None:
        if isinstance(value, str):
            if value.strip():
                parts.append(value.strip())
            return

        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            content = value.get("content")
            if isinstance(content, Iterable):
                for item in content:
                    self._walk_jira_text(item, parts)
            return

        if isinstance(value, list):
            for item in value:
                self._walk_jira_text(item, parts)

    def _escape_cql(self, value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace('"', '\\"')