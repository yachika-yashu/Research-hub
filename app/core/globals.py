import os
from openai import AsyncOpenAI  # async client prevents OpenAI calls from blocking the event loop
from dotenv import load_dotenv

load_dotenv()

# Fall back to a sentinel when OPENAI_API_KEY is unset so the SDK constructor
# accepts the value and the app can boot (e.g. in CI smoke tests that don't
# call OpenAI). Real API calls then fail with a clear 401 instead of crashing
# the worker at import time.
# The env var is also rewritten so downstream libraries that read it directly
# (langchain_openai.ChatOpenAI, langsmith, ragas) see the same sentinel.
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "sk-no-openai-key-configured"
openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
