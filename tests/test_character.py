"""D002：验证目标之外的内容保持一致，构建过程没有副作用。"""

import unittest
from unittest.mock import patch

from app.character import BASE_CARD, GOALS, build_prompt
from app.model import RealModelAdapter


class CharacterTests(unittest.TestCase):
    def test_only_goal_changes_and_cards_are_not_modified(self):
        original = BASE_CARD.copy()
        cards = [{**BASE_CARD, "goal": goal} for goal in GOALS.values()]
        before = [card.copy() for card in cards]
        prompts = [build_prompt(card) for card in cards]
        self.assertEqual(cards, before)
        self.assertEqual(BASE_CARD, original)
        self.assertIsNot(cards[0], cards[1])
        normalized = []
        for card, prompt in zip(cards, prompts):
            for value in card.values():
                self.assertIn(value, prompt)
            normalized.append(prompt.replace(card["goal"], "<目标>"))
        self.assertEqual(normalized[0], normalized[1])

    def test_new_goal_needs_no_adapter_change(self):
        card = {**BASE_CARD, "goal": "希望玩家自愿帮忙。"}
        self.assertIn(card["goal"], build_prompt(card))

    def test_adapter_disables_retries_and_rejects_empty_text(self):
        with patch("app.model.OpenAI") as client:
            adapter = RealModelAdapter("test-only", "test-model", "https://example.invalid/v1")
            self.assertEqual(client.call_args.kwargs["max_retries"], 0)
            message = client.return_value.chat.completions.create.return_value.choices[0].message
            for text in (None, "", "   "):
                with self.subTest(text=text):
                    message.content = text
                    with self.assertRaises(ValueError):
                        adapter.generate([{"role": "user", "content": "你好"}])


if __name__ == "__main__":
    unittest.main()
