import json
import os
import streamlit as st
from core import AppError, Config, demo_turn, run_turn

st.set_page_config(page_title="Jev Smart Router", page_icon="↗", layout="wide")


def setting(name, default=""):
    if os.environ.get(name):
        return os.environ[name]
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default


for name, initial in (("history", []), ("results", []), ("ledger", [])):
    if name not in st.session_state:
        st.session_state[name] = initial

with st.sidebar:
    st.title("↗ Jev Router")
    environment = st.radio("Environment", ["Demo", "Live"], horizontal=True)
    st.caption("Demo is free and simulated. Live sends conversation context to Jev and OpenRouter.")
    if st.session_state.get("environment") != environment:
        st.session_state.history = []
        st.session_state.results = []
        st.session_state.environment = environment
    mode = st.selectbox("Routing", ["Auto", "Economy", "Strong"])
    with st.expander("API keys", expanded=environment == "Live"):
        jk = setting("TYPESAFE_API_KEY")
        lk = setting("OPENROUTER_API_KEY")
        st.caption("Keys stay on this app's server/session and are never included in exports.")
        st.markdown("[TypeSafe console](https://console.typesafe.ai) · [OpenRouter keys](https://openrouter.ai/settings/keys)")
    with st.expander("Models & routing"):
        low = st.text_input("Economy model ID", setting("LOW_MODEL", "openai/gpt-4.1-mini"))
        high = st.text_input("Strong model ID", setting("HIGH_MODEL", "openai/gpt-4.1"))
        jev = st.text_input("Jev model ID", "jev-latest")
        threshold = st.slider("Minimum confidence for economy", 0.0, 1.0, 0.75, 0.05)
        turns = st.slider("Previous conversation turns", 0, 10, 4)
        output = st.slider("Maximum output tokens", 256, 4096, 1024, 256)
    with st.expander("Estimated pricing · USD / 1M tokens"):
        st.caption("Defaults checked 15 Oct 2026. Update prices when changing models. Estimates exclude caching discounts, fees and tax.")
        ji = st.number_input("Jev input", min_value=0.0, value=0.042, format="%.4f")
        li = st.number_input("Economy input", min_value=0.0, value=0.4)
        lo = st.number_input("Economy output", min_value=0.0, value=1.6)
        hi = st.number_input("Strong input", min_value=0.0, value=2.0)
        ho = st.number_input("Strong output", min_value=0.0, value=8.0)
    if st.button("New conversation", use_container_width=True):
        st.session_state.history = []
        st.session_state.results = []
        st.rerun()
    st.caption("New conversation clears context, but keeps this session's live cost ledger.")

config = Config(jk, lk, jev, low, high, threshold, output, turns, ji, li, lo, hi, ho)
st.title("Sairaj's Smart Chatbot")
st.write("Ask a question. Jev selects a suitable AI model.")
if environment == "Demo":
    st.info("DEMO · Simulated routing and placeholder answers. No API requests or charges.")
elif mode == "Auto":
    st.caption("AUTO · Clear, simple tasks → Economy. Complex or uncertain tasks → Strong.")
else:
    st.caption(f"MANUAL · Every prompt goes directly to {mode}. Jev is bypassed.")

ledger = st.session_state.ledger
known = sum(e["cost_usd"] for e in ledger if e["cost_usd"] is not None)
unknown = sum(e["cost_usd"] is None for e in ledger)
a, b, c = st.columns(3)
a.metric("Live cost estimate · known subtotal", f"${known:.6f}")
b.metric("Live API attempts", len(ledger))
c.metric("Calls with unknown cost", unknown)
if unknown:
    st.warning("The subtotal is incomplete. Failed calls or missing usage may still be billed; check provider dashboards.")
if st.session_state.get("last_error"):
    st.error(st.session_state.pop("last_error"))
    st.caption("This attempt was not added to conversation context. Any API usage is retained in the ledger.")

chat, details = st.tabs(["Conversation", "Usage & how it works"])
with details:
    st.markdown("""**Routing policy**

1. Keep a bounded window of successful conversation turns.
2. Ask Jev to choose `low`, `high`, or `uncertain`.
3. Use economy only when its choice meets the confidence threshold.
4. Generate one answer with the selected model.

Confidence describes the routing decision, not the correctness of the final answer.
If Jev fails, the request stops. Choose a manual route to continue without Jev.
There are no automatic retries or second answer calls.

**Cost calculation:** reported input/output tokens × your configured prices.
Router charges are included even when the answer request fails. No global answer
cache is used, so new questions are evaluated in their own conversation context.
""")
    if ledger:
        st.dataframe([{k: v for k, v in e.items() if k != "usage"} for e in ledger], use_container_width=True)
        st.download_button("Download usage ledger", json.dumps(ledger, indent=2), "usage.json", "application/json")
    st.caption("Local prototype: text only, no web search or file tools. Before public deployment, add authentication, per-user quotas and provider spending limits.")


def render_result(result):
    with st.chat_message("assistant"):
        st.markdown(result["answer"])
        route = result["route"]
        confidence = "" if route["confidence"] is None else f" · routing confidence {route['confidence']:.0%}"
        st.caption(f"{route['tier'].upper()} · {result['model']}{confidence}")
        with st.expander("Why this model?"):
            st.write(route["reason"])
            if route["router_model"]:
                st.caption(f"Router: {route['router_model']} · Jev choice: {route['choice']}")
            if result["omitted_messages"]:
                st.caption(f"{result['omitted_messages']} older messages were omitted to limit context cost.")
            costs = result.get("calls", [])
            if costs:
                total = None if any(e["cost_usd"] is None for e in costs) else sum(e["cost_usd"] for e in costs)
                st.write("This turn's estimated cost: " + ("unknown" if total is None else f"${total:.6f}"))
                baseline = result["strong_cost_proxy_usd"]
                if baseline is not None and total is not None:
                    st.caption(f"Same-token strong-only cost proxy: ${baseline:.6f}. Difference after router cost: ${baseline-total:.6f}.")
                    st.caption("Illustration only: a real strong-model answer may use different tokens and have different quality. This is not measured savings.")
        if result["truncated"]:
            st.warning("The output token limit was reached. Increase it or ask the model to continue.")


with chat:
    if not st.session_state.results:
        st.markdown("Try **What is a Python list?** or **Design a fault-tolerant payment processing architecture.**")
    for result in st.session_state.results:
        with st.chat_message("user"):
            st.markdown(result["prompt"])
        render_result(result)

prompt = st.chat_input("Ask a question…")
if prompt:
    start = len(ledger)
    try:
        with st.spinner("Evaluating the task and preparing your answer…"):
            if environment == "Demo":
                result = demo_turn(prompt, st.session_state.history, config)
            else:
                result = run_turn(prompt, st.session_state.history, config, ledger, mode)
        result["prompt"] = prompt
        result["calls"] = ledger[start:]
        st.session_state.history.extend([{"role": "user", "content": prompt},
                                         {"role": "assistant", "content": result["answer"]}])
        st.session_state.results.append(result)
        st.rerun()
    except AppError as error:
        st.session_state.last_error = str(error)
        st.rerun()
