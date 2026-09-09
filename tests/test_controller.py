from concurrent.futures import Future
import unittest
from unittest.mock import Mock

from tailtray.controller import OperationCoordinator
from tailtray.core import Snapshot


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.executor = Mock()
        self.dispatch = lambda callback: callback()
        self.coordinator = OperationCoordinator(self.executor, self.dispatch)

    def test_mutations_are_serialized_and_disconnect_is_queued(self):
        up = Future()
        down = Future()
        self.executor.submit.side_effect = [up, down]
        self.assertTrue(self.coordinator.submit("Connecting", Mock()))
        self.assertFalse(self.coordinator.request_disconnect(Mock()))
        self.assertTrue(self.coordinator.busy)
        up.set_result(None)
        self.assertEqual(self.executor.submit.call_count, 2)
        self.assertTrue(self.coordinator.busy)
        down.set_result(None)
        self.assertFalse(self.coordinator.busy)

    def test_refresh_started_before_mutation_cannot_repaint_afterward(self):
        refresh = Future()
        self.executor.submit.return_value = refresh
        token = self.coordinator.start_refresh()
        self.coordinator.submit("Connecting", Mock())
        refresh.set_result(Snapshot(backend_state="Stopped"))
        self.assertFalse(self.coordinator.accept_refresh(token))

    def test_executor_failure_does_not_leave_busy_state(self):
        self.executor.submit.side_effect = RuntimeError("executor closed")
        completed = Mock()
        self.coordinator.set_done_handler(completed)
        self.coordinator.submit("Connecting", Mock())
        self.assertFalse(self.coordinator.busy)
        self.assertFalse(completed.call_args.args[1])
        self.assertIsInstance(completed.call_args.args[3], RuntimeError)

    def test_shutdown_ignores_late_completion(self):
        pending = Future()
        self.executor.submit.return_value = pending
        self.coordinator.submit("Connecting", Mock())
        self.coordinator.shutdown()
        pending.set_result(None)
        self.assertFalse(self.coordinator.busy)


if __name__ == "__main__":
    unittest.main()
