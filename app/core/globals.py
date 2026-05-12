import os
from openai import AsyncOpenAI  # async client prevents OpenAI calls from blocking the event loop
from dotenv import load_dotenv

load_dotenv()

# to create one Centralized OpenAI client to avoid circular imports and state issues
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")) # the system is designed for concurrent, multi-user, I/O-heavy LLM workloads, and async prevents OpenAI calls from blocking entire backend.
