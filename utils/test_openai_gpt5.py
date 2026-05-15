import os

from openai import OpenAI


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in your local environment or .env file.")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model="gpt-5",
        input="Say hello in one short sentence.",
    )
    print(response.output_text)


if __name__ == "__main__":
    main()
