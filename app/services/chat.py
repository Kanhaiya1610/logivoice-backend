"""Optional Gemini document-grounded chat integration."""

from __future__ import annotations


class ChatProviderUnavailable(Exception):
    """Gemini has not been configured or its SDK is unavailable."""


def answer_question(document_text: str, question: str, config: dict) -> str:
    api_key = config.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ChatProviderUnavailable("Gemini is not configured")
    try:
        import google.generativeai as genai
    except ImportError as error:  # pragma: no cover - dependency is optional at runtime
        raise ChatProviderUnavailable("The Gemini SDK is unavailable") from error

    prompt = (
        "Answer the user's question using only the document context below. "
        "If the answer is not present, say that the information is not found in the document. "
        "Do not invent values. Keep the answer concise.\n\n"
        f"Document context:\n{document_text[:12000]}\n\n"
        f"Question: {question[:2000]}"
    )
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(config.get("GEMINI_MODEL", "gemini-1.5-flash"))
        response = model.generate_content(prompt)
        answer = getattr(response, "text", "")
        if not answer:
            raise RuntimeError("Gemini returned an empty response")
        return answer.strip()
    except ChatProviderUnavailable:
        raise
    except Exception as error:
        raise RuntimeError("Gemini request failed") from error
