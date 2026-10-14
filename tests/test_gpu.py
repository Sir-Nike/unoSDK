"""Allocator ordering and fail-fast CUDA contract without importing Torch."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

path = Path(__file__).resolve().parents[1] / "src/uno_agent/gpu.py"
spec = importlib.util.spec_from_file_location("sdk_gpu", path)
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


class GpuContractTest(unittest.TestCase):
    def test_missing_cuda_fails(self):
        torch = Mock()
        torch.cuda.is_available.return_value = False
        with self.assertRaisesRegex(RuntimeError, "CPU fallback"):
            gpu.configure_torch(torch, True)

    def test_declared_budget_is_applied(self):
        torch = Mock()
        torch.cuda.is_available.return_value = True
        torch.cuda.get_device_properties.return_value = SimpleNamespace(total_memory=1000)
        with patch.dict("os.environ", {"UNO_GPU_MEMORY_BYTES":"200"}):
            self.assertEqual(gpu.configure_torch(torch, True), "cuda:0")
        torch.cuda.set_per_process_memory_fraction.assert_called_once_with(.2, device=0)

    def test_cpu_does_not_touch_cuda(self):
        torch = Mock()
        self.assertEqual(gpu.configure_torch(torch), "cpu")
        torch.cuda.is_available.assert_not_called()
