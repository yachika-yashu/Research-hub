import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Centralized OpenAI client to avoid circular imports and state issues
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
