import json
from app.core.globals import openai_client
from app.core.logging import logger

_GUARDRAIL_PROMPT = """You are a safety guardrail for a research intelligence platform used by academic researchers, scientists, and engineers.

Evaluate whether the following query is appropriate for a research assistant.

REJECT queries that explicitly request:
- Fabricating, inventing, or hallucinating research data, results, statistics, or citations
- Plagiarism assistance (e.g., "reword this so it won't be detected", "make it look original")
- Academic fraud (e.g., ghostwriting exams, forging peer reviews, manipulating data)
- Clearly off-topic personal tasks with zero research relevance (e.g., "write my dating profile", "order me food")
- Illegal activities or content that would harm people

ALLOW everything else, including:
- Any scientific, engineering, or academic topic — even sensitive ones (biosafety, dual-use research, gain-of-function — researchers have legitimate need)
- Summarising, analysing, comparing, or critiquing papers
- Searching for literature on any topic
- Writing research papers, abstracts, grant proposals, literature reviews
- Statistical analysis, data interpretation, methodology questions
- Code for data analysis or visualisation
- Questions that seem tangential but could be part of research workflows
- Ethical debates or controversial scientific topics

When in doubt, ALLOW. Over-restriction is harmful to legitimate research.

Return ONLY valid JSON — no markdown, no explanation outside the JSON:
{{"allowed": true}} or {{"allowed": false, "reason": "one short sentence"}}

Query: {query}"""


async def pre_retrieval_guardrail(query: str) -> dict:
    """
    Lightweight pre-retrieval check for the research platform.
    Returns {"allowed": True} or {"allowed": False, "reason": str}.
    Fails open — if the check itself errors, the query proceeds.
    """
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _GUARDRAIL_PROMPT.format(query=query)}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=60,
        )
        result = json.loads(resp.choices[0].message.content)
        if not result.get("allowed", True):
            logger.warning(f"GUARDRAIL: Rejected query — {result.get('reason', 'policy violation')}")
        return result
    except Exception as exc:
        logger.warning(f"GUARDRAIL: Check failed ({exc}), failing open.")
        return {"allowed": True}
