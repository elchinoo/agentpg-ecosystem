from apgkit.config import read_secret
from apgkit.llm import ChatConfig, LLMClient, LLMError
from apgkit.netguard import NetGuard, UnsafeURL

guard = NetGuard("DEMO_ALLOW_PRIVATE_URLS")                  # the SSRF guard for every URL the server will call
llm = LLMClient(guard)
cfg = ChatConfig("http://127.0.0.1:8631/v1", "echo-chat", "gateway", key_plain=read_secret("GATEWAY_KEY"))
try:
    print("answer:", llm.chat(cfg, [{"role": "user", "content": "Say hello"}])["text"][:60])
except LLMError as exc:
    print("refused:", exc)
try:
    guard.check("http://169.254.169.254/latest/meta-data")   # a cloud metadata address
except UnsafeURL as exc:
    print("metadata:", exc)
