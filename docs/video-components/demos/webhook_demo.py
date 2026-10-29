import time

import webhooks                                   # shopfront/src/shopfront/webhooks.py, copied as is: standard library only

SECRET, raw, now = "demo-secret", b'{"event":"payment.captured","id":"pay_1"}', int(time.time())
sig = webhooks.sign(SECRET, str(now), raw)        # the sender: HMAC-SHA256 over "timestamp.body"
webhooks.verify(raw, str(now), sig, SECRET)
print("untouched body : accepted")
cases = [("altered body", raw.replace(b"pay_1", b"pay_2"), now, sig),
         ("replayed later", raw, now - 900, webhooks.sign(SECRET, str(now - 900), raw))]
for label, body, ts, signature in cases:
    try:
        webhooks.verify(body, str(ts), signature, SECRET)
    except webhooks.WebhookError as exc:
        print(f"{label:15}: refused ({exc.code})")
