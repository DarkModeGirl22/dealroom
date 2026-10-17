import os
import tempfile

# every test run gets its own throwaway data folder, with no real keys
os.environ["DEALROOM_DATA_DIR"] = tempfile.mkdtemp(prefix="dealroom-test-")
for k in ("NEBIUS_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "PAYPAL_WEBHOOK_ID"):
    os.environ.pop(k, None)
