"""D015 受控造错：仅在本进程临时替换 deepcopy，退出上下文立即恢复。"""

import io
import unittest
from copy import copy
from unittest.mock import patch

from app import world
from app.actions import ActionProposal


class SnapshotContract(unittest.TestCase):
    def test_old_nested_location_is_unchanged(self):
        original = world.create_world("copy-exercise")
        moved, _, _ = world.adjudicate(
            original, ActionProposal("move", destination_id="storage_room"),
            actor_id="lin_yan", turn_id="T001")
        self.assertEqual(original.actor_locations["lin_yan"], "duty_room")
        self.assertEqual(moved.actor_locations["lin_yan"], "storage_room")


def run_contract():
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SnapshotContract)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return result, stream.getvalue()


def main():
    with patch.object(world, "deepcopy", side_effect=copy):
        broken, broken_output = run_contract()
    print("=== 注入浅复制：预期旧快照断言失败 ===")
    print(broken_output)
    restored, restored_output = run_contract()
    print("=== 恢复深复制：预期通过 ===")
    print(restored_output)
    return 0 if len(broken.failures) == 1 and not broken.errors and restored.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
