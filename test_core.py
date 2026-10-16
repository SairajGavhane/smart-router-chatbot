import copy
import unittest
from core import AppError, Config, conversation, parse_route, run_turn, token_cost


def decision(choice="low", confidence=0.95):
    return {"model": "jev-test", "answers": {"tier": {"choice": choice, "confidence": confidence}},
            "usage": {"input_tokens": 1000, "output_tokens": 40}}


ANSWER = {"model": "answer-test", "choices": [{"message": {"content": "A useful answer"}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


class FakeAPI:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def __call__(self, url, key, body):
        self.requests.append((url, body))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(jev_key="test", llm_key="test")
        self.ledger = []

    def test_simple_calls_cheap_and_accounts_for_both_calls(self):
        api = FakeAPI(decision(), ANSWER)
        result = run_turn("Define a list", [], self.config, self.ledger, transport=api)
        self.assertEqual(result["route"]["tier"], "low")
        self.assertEqual(api.requests[1][1]["model"], self.config.low_model)
        self.assertAlmostEqual(sum(e["cost_usd"] for e in self.ledger), 0.001242)
        self.assertAlmostEqual(result["strong_cost_proxy_usd"], 0.006)

    def test_uncertainty_escalates(self):
        for choice, confidence in [("low", 0.3), ("uncertain", 1), ("high", 0.9)]:
            self.assertEqual(parse_route(decision(choice, confidence), 0.75)["tier"], "high")

    def test_invalid_decisions_are_not_trusted(self):
        for choice, confidence in [("cheap", 1), ("low", float("nan")), ("low", -1), ("low", True), ("low", 2)]:
            with self.assertRaises(AppError):
                parse_route(decision(choice, confidence), 0.75)

    def test_failed_router_does_not_call_answer_model(self):
        api = FakeAPI(AppError("HTTP 401"))
        with self.assertRaises(AppError):
            run_turn("hello", [], self.config, self.ledger, transport=api)
        self.assertEqual(len(api.requests), 1)
        self.assertIsNone(self.ledger[0]["cost_usd"])

    def test_failed_answer_keeps_router_cost(self):
        api = FakeAPI(decision(), AppError("timeout"))
        with self.assertRaises(AppError):
            run_turn("hello", [], self.config, self.ledger, transport=api)
        self.assertAlmostEqual(self.ledger[0]["cost_usd"], 0.000042)
        self.assertIsNone(self.ledger[1]["cost_usd"])

    def test_missing_usage_is_unknown_not_free(self):
        data = copy.deepcopy(ANSWER)
        del data["usage"]
        run_turn("hello", [], self.config, self.ledger, transport=FakeAPI(decision(), data))
        self.assertIsNone(self.ledger[-1]["cost_usd"])
        self.assertIsNone(token_cost({}, "a", "b", 1, 2))

    def test_manual_mode_bypasses_jev(self):
        self.config.jev_key = ""
        api = FakeAPI(ANSWER)
        run_turn("hello", [], self.config, self.ledger, "Strong", api)
        self.assertEqual(len(api.requests), 1)
        self.assertEqual(api.requests[0][1]["model"], self.config.high_model)

    def test_router_and_generator_share_context(self):
        history = [{"role": "user", "content": "Prove a theorem"},
                   {"role": "assistant", "content": "Here is a proof."}]
        api = FakeAPI(decision("high"), ANSWER)
        run_turn("Explain step two", history, self.config, self.ledger, transport=api)
        self.assertEqual(api.requests[0][1]["state"]["conversation"], api.requests[1][1]["messages"][1:])

    def test_context_limits_keep_pairs(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 5
        messages, omitted = conversation(history, "new", 2)
        self.assertEqual(len(messages), 5)
        self.assertEqual(omitted, 6)
        self.assertEqual(conversation(history, "new", 0)[0], [{"role": "user", "content": "new"}])
        with self.assertRaises(AppError):
            conversation([], "a" * 12001, 2)

    def test_blank_credentials_do_not_make_calls(self):
        api = FakeAPI()
        with self.assertRaises(AppError):
            run_turn("hello", [], Config(), self.ledger, transport=api)
        self.assertEqual(api.requests, [])

    def test_empty_answer_retains_usage(self):
        data = copy.deepcopy(ANSWER)
        data["choices"][0]["message"]["content"] = ""
        with self.assertRaises(AppError):
            run_turn("hello", [], self.config, self.ledger, transport=FakeAPI(decision(), data))
        self.assertIsNotNone(self.ledger[-1]["cost_usd"])

    def test_token_limit_reported(self):
        data = copy.deepcopy(ANSWER)
        data["choices"][0]["finish_reason"] = "length"
        result = run_turn("hello", [], self.config, self.ledger, transport=FakeAPI(decision(), data))
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
