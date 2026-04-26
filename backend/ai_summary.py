from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

def generate_summary(signals):
    if not signals:
        return "No significant market movements today."

    text_data = "\n".join([
        f"{s['Ticker']}: {s['Signal']} ({s['Daily Return %']}%)"
        for s in signals
    ])

    prompt = f"""
    You are a financial analyst.

    Based on the following stock signals, write a short 2-3 sentence summary of market activity:

    {text_data}

    Be concise and professional.
    """

    response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

    return {
        "summary": response.choices[0].message.content,
        "source": "groq",
        "model": "llama-3.3-70b-versatile",
    }