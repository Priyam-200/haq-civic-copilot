import time
from llm.client import ask_llm, MODEL, FALLBACKS

print("Main model:", MODEL, "| Fallbacks:", FALLBACKS)
t = time.time()
print(ask_llm("Say hello in Hindi in one short sentence."))
print(f"Took {time.time() - t:.1f} seconds")